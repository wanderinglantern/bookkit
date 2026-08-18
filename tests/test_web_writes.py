"""Web writes are batched writes.

The assertion is deliberately NOT 'the field changed'. A plain outcome check
passes even when the route writes outside a batch — which is exactly how 33
FormModal call sites bypassed the batched push_form seam while the suite
stayed green. What is asserted is that the batch exists, that it carries
source='web', and that reverting it puts the record back.

Contacts are edited in place, cell by cell (Grant's 2026-08-17 amendment) —
not through a whole-form .../contacts/{id}/edit route. Each test below POSTs
a single cell, per the settled contract (Task 6): GET .../cell/{key} is the
display cell, GET .../cell/{key}/edit is the editor, POST .../cell/{key}
saves."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import db
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs

    org = orgs.list_orgs(app.state.conn, kind="client")[0]
    with TestClient(app) as client:
        yield client, org


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, since="", limit=1)
    return found[0] if found else None


def test_editing_a_contact_cell_writes_one_web_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]

    response = client.post(
        f"/accounts/{org.ref}/contacts/{contact.id}/cell/title",
        data={"title": "Head of Risk"},
    )
    assert response.status_code == 200

    assert contacts_repo.get(conn, contact.id).title == "Head of Risk"

    batch = _latest_batch(conn)
    assert batch is not None, "the edit wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"
    assert batch.tool == "edit_contact"
    from bookkit.repo import batches as batches_repo

    # open_batch() creates its EventBatch row unconditionally, even around
    # an empty body — so a batch existing with the right source/tool is not
    # by itself proof the WRITE happened inside it. events_for being
    # non-empty is: an empty batch has nothing for revert to undo, which is
    # exactly as broken as writing outside a batch entirely (confirmed by
    # temporarily moving the write outside open_batch() during review — see
    # the task report's batch mutation proof).
    assert batches_repo.events_for(conn, batch.id), "the batch carries no events to revert"


def test_the_web_batch_reverts(app_and_org):
    """One writer action, one undo unit — on every surface."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    contact = contacts_repo.for_org(conn, org.id)[0]
    before = contact.title

    client.post(
        f"/accounts/{org.ref}/contacts/{contact.id}/cell/title",
        data={"title": "Interim CFO"},
    )
    batch = _latest_batch(conn)
    assert batch is not None
    batches_svc.revert(conn, batch.ref, now=db.utc_now())
    assert contacts_repo.get(conn, contact.id).title == before


def test_adding_a_contact_writes_one_web_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    before = len(contacts_repo.for_org(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "Dana", "last_name": "Okafor", "email": "DANA@EXAMPLE.COM",
              "phone": "", "mobile": "", "title": "", "role": "", "linkedin": "",
              "notes": ""},
    )
    assert response.status_code == 200
    after = contacts_repo.for_org(conn, org.id)
    assert len(after) == before + 1

    created = [c for c in after if c.last_name == "Okafor"][0]
    # clean_email lowercases the DOMAIN only — RFC 5321 makes the local part
    # case-sensitive (normalize.py's docstring) — so this pins that the
    # shared cleaner ran without asserting the wrong invariant.
    assert created.email == "DANA@example.com", "the shared cleaner did not run"

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"


def test_adding_a_contact_replaces_the_panel_exactly_once(app_and_org):
    """Fix round 2: contact_create used to return the WHOLE panel as the
    primary swap for a form whose own hx-target is "closest .form-host" —
    and .form-host lives INSIDE the panel, so the response nested a second
    copy of the panel one level down inside itself. A presence check
    ('people-head' in response.text) passes on that broken version just as
    well as the fixed one; only a count tells them apart."""
    client, org = app_and_org

    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "Priya", "last_name": "Nair", "email": "priya@example.com",
              "phone": "", "mobile": "", "title": "", "role": "", "linkedin": "",
              "notes": ""},
    )
    assert response.status_code == 200
    # class="people-head" exactly — a bare substring check also matches
    # "people-head-spacer" and would pass at count==2 even on the fixed
    # response, which is no better than the presence check this replaces.
    assert response.text.count('class="people-head"') == 1
    assert response.text.count('id="contacts-panel"') == 1
    assert response.text.count("Priya Nair") == 1
    # The count checks above are necessary but NOT sufficient: TestClient
    # never executes htmx, so a raw response body looks identical whether
    # or not it will nest once a real browser applies the swap — the panel
    # markup itself is the same either way. What differs is whether the
    # panel carries hx-swap-oob: without it, the response becomes the
    # innerHTML of .form-host (which is INSIDE this very panel already on
    # the page) — nesting a second copy one level down. With it, htmx
    # swaps #contacts-panel out-of-band instead, as a separate operation.
    assert 'id="contacts-panel" hx-swap-oob="true"' in response.text


def test_a_non_editable_key_is_404_not_a_write(app_and_org):
    """first_name is display-only server-side, not just in the template —
    CONTACT_INLINE (and its web mirror, CONTACT_FIELDS) never declares it,
    so the cell route must refuse it rather than silently accepting a POST
    the table never offers a control for."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    before = contact.first_name

    response = client.post(
        f"/accounts/{org.ref}/contacts/{contact.id}/cell/first_name",
        data={"first_name": "Nope"},
    )
    assert response.status_code == 404
    assert contacts_repo.get(conn, contact.id).first_name == before


# --- reverting a change from the account page --------------------------------
# The right rail's per-row `Revert` and the top bar's `Undo <last change>`
# pill both POST this one route. The assertions below are about the whole
# round trip, not just the service call: services/batches.revert already
# works and has its own suite (tests/test_batches_service.py) — what is new
# here is authorization, the outcome token that survives the redirect, and
# the toast the redirect target renders from it.


def _revert(client, org_ref: str, batch_ref: str, tab: str = "relationship"):
    return client.post(f"/accounts/{org_ref}/changes/{batch_ref}/revert?tab={tab}")


def _redirect_params(response) -> dict[str, str]:
    from urllib.parse import parse_qsl, urlsplit

    target = response.headers["HX-Redirect"]
    return dict(parse_qsl(urlsplit(target).query))


def _set_title(client, org, contact_id: str, value: str):
    return client.post(
        f"/accounts/{org.ref}/contacts/{contact_id}/cell/title", data={"title": value}
    )


def test_the_revert_link_reverts_the_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    before = contact.title

    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)
    assert contacts_repo.get(conn, contact.id).title == "Head of Risk"

    response = _revert(client, org.ref, batch.ref)
    assert response.status_code == 204
    assert contacts_repo.get(conn, contact.id).title == before
    # reverted_at is the half a value check alone would miss: a route that
    # wrote the old value back without marking the batch would let the same
    # change be "reverted" forever.
    assert batches_repo.get(conn, batch.id).reverted_at is not None


def test_reverting_redirects_with_the_outcome_and_the_count(app_and_org):
    """HX-Redirect, not a fragment swap: a revert can move any panel, the
    header badge, the tab counts and the rail at once, and every other web
    write swaps exactly one panel by id."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)

    response = _revert(client, org.ref, batch.ref)
    assert response.status_code == 204
    target = response.headers["HX-Redirect"]
    assert target.startswith(f"/accounts/{org.ref}/relationship?")
    params = _redirect_params(response)
    assert params["outcome"] == "reverted"
    assert params["undo"] == batch.ref
    assert params["n"] == "1"

    page = client.get(target)
    assert f"{batch.ref} reverted — 1 change(s)" in page.text


def test_a_batch_from_another_account_is_not_revertible(app_and_org):
    """Authorization, not decoration: without the org check a crafted URL on
    account A reverts a write that happened on account B."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import contacts as contacts_repo
    from bookkit.repo import orgs

    other = next(o for o in orgs.list_orgs(conn, kind="client") if o.id != org.id)
    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)

    response = client.post(
        f"/accounts/{other.ref}/changes/{batch.ref}/revert?tab=relationship"
    )
    assert response.status_code == 404
    assert contacts_repo.get(conn, contact.id).title == "Head of Risk"
    assert batches_repo.get(conn, batch.id).reverted_at is None

    # ...and the 404 above is about the ORG, not a missing route: the very
    # same batch reverts under its own account. Without this the test passes
    # just as well against a route that does not exist at all.
    assert _revert(client, org.ref, batch.ref).status_code == 204
    assert batches_repo.get(conn, batch.id).reverted_at is not None


def test_a_conflicted_batch_is_refused_and_says_which_field(app_and_org):
    """A REFUSAL SAYS SOMETHING — the toast names what conflicts, not just a
    count, and says where force lives.

    The batch deliberately carries TWO fields, only one of which is changed
    afterwards. A one-field batch cannot tell refuse-only from force: forcing
    a batch whose every change is conflicted also reverts nothing and also
    reports `refused` (services/batches.revert's "applied means the book
    moved" branch), so the same assertions would pass with force=True wired
    in by mistake — which is exactly what the force mutation proof showed
    before this test was widened."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    contact = contacts_repo.for_org(conn, org.id)[0]
    with batches_svc.open_batch(
        conn, source="web", tool="edit_contact",
        summary="set title and role on the first contact", org_id=org.id,
    ):
        contacts_repo.update(conn, contact.id, title="Head of Risk", role="syndicate lead")
    batch = _latest_batch(conn)

    # ...and then someone changes ONE of the two, outside that batch
    _set_title(client, org, contact.id, "Head of Claims")

    response = _revert(client, org.ref, batch.ref)
    assert response.status_code == 204
    # nothing moved — not the conflicted field, and not the clean one either:
    # the revert is refused whole, never half-applied
    assert contacts_repo.get(conn, contact.id).title == "Head of Claims"
    assert contacts_repo.get(conn, contact.id).role == "syndicate lead"
    # and the batch stays revertible (from the TUI, with force)
    assert batches_repo.get(conn, batch.id).reverted_at is None

    params = _redirect_params(response)
    assert params["outcome"] == "refused"
    assert params["n"] == "1"

    page = client.get(response.headers["HX-Redirect"])
    assert f"{batch.ref} refused — contact title changed since" in page.text
    assert "revert it from the TUI with R to force past the conflict" in page.text


def test_reverting_twice_says_already_reverted(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)

    assert _revert(client, org.ref, batch.ref).status_code == 204
    again = _revert(client, org.ref, batch.ref)
    assert again.status_code == 204
    assert _redirect_params(again)["outcome"] == "already"
    assert "already reverted" in client.get(again.headers["HX-Redirect"]).text


def test_a_program_batch_refuses_and_names_program_revert_file(app_and_org):
    """A program_* batch wrote a towerkit FILE; services/batches refuses with
    a message naming the tool that can undo it. The toast must carry THAT
    sentence, not a re-typed copy — so the assertion compares against the
    exception the service itself raises."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.services import batches as batches_svc

    with batches_svc.open_batch(
        conn, source="web", tool="program_renew", summary="renewed the program",
        org_id=org.id,
    ):
        pass
    batch = _latest_batch(conn)

    response = _revert(client, org.ref, batch.ref)
    assert response.status_code == 204
    assert _redirect_params(response)["outcome"] == "program"

    with pytest.raises(ValueError) as raised:
        batches_svc.revert(conn, batch.ref, now=db.utc_now())

    page = client.get(response.headers["HX-Redirect"])
    assert str(raised.value) in page.text
