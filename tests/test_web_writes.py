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

import re
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
    with TestClient(app, base_url="http://127.0.0.1") as client:
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
    contact_inline (and its web mirror, CONTACT_FIELDS) never declares it,
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


def test_a_refused_save_keeps_every_value_and_writes_nothing(app_and_org):
    """Commit-in-place: the form comes back with the input intact and the
    error, and the transaction rolled back — a refused save leaves nothing
    behind and costs nothing retyped.

    BOTH counts are asserted, and the batch one is not redundant. `_save`
    opens its batch BEFORE calling `write`, so a route that swallowed the
    refusal would strand an empty EventBatch even where no contact row was
    ever inserted — a row in RECENT CHANGES for a save that did not happen,
    with nothing for `Revert` to put back. Both mutations were run: making
    `_save` swallow the FieldError and write the record anyway fails the row
    count AND the batch count, while opening the batch BEFORE parsing and
    returning the refusal from inside it (a normal `with` exit, so the empty
    batch commits) writes no row at all and fails ONLY the batch count.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import contacts as contacts_repo

    before_count = len(contacts_repo.for_org(conn, org.id))
    before_batches = len(batches_repo.recent(conn, since="", limit=50))

    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "", "last_name": "Okafor", "email": "dana@example.com",
              "phone": "", "mobile": "", "title": "Head of Risk", "role": "",
              "linkedin": "", "notes": "call back Tuesday"},
    )

    assert response.status_code == 200, "htmx drops 4xx — a refusal must be a 200"
    # the PARSER's own sentence, in the form's error slot — not a bare
    # `"required" in response.text`, which every rendered form satisfies
    # through the HTML `required` attribute on its own inputs and so passes
    # over a route that swallowed the refusal and was rejected by a NOT NULL
    # constraint instead (verified: that mutation passes the loose check).
    assert '<p class="form-error" role="alert">first name is required</p>' in response.text
    # every other value survives the refusal
    assert "Okafor" in response.text
    assert "dana@example.com" in response.text
    assert "Head of Risk" in response.text
    assert "call back Tuesday" in response.text
    # and nothing was written
    assert len(contacts_repo.for_org(conn, org.id)) == before_count
    assert len(batches_repo.recent(conn, since="", limit=50)) == before_batches


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


def test_a_program_batch_with_no_snapshot_says_it_was_not_put_back(app_and_org):
    """A program_* batch wrote a towerkit FILE, and services/batches still
    refuses those outright — file contents are not event_log rows.

    What CHANGED on 2026-08-19: the rail no longer stops at that refusal. It
    calls the file-side revert (services.program_files.revert_file), which is
    what the MCP server has used since program writes shipped. This batch
    carries no snapshot — nothing was actually written through it — so the
    file revert refuses too, and the toast says the change was not put back
    and why. The old assertion expected the rail's own "cannot undo a file"
    message, which is no longer the answer it gives.

    services.batches.revert's refusal is unchanged and still tested where it
    belongs, in tests/test_batches_service.py."""
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
    assert _redirect_params(response)["outcome"] == "filerefused"

    page = client.get(response.headers["HX-Redirect"]).text
    assert "was not put back" in page
    assert "towerkit" in page


def test_an_unknown_batch_ref_is_gone_not_a_500(app_and_org):
    """The `gone` token had no test at all until review round 1 (F9). A ref
    that resolves to nothing is a stale page — someone reverted from the TUI
    while this tab sat open — so it answers like every other outcome instead
    of raising."""
    client, org = app_and_org

    response = _revert(client, org.ref, "MCP-9999")
    assert response.status_code == 204
    assert _redirect_params(response)["outcome"] == "gone"

    page = client.get(response.headers["HX-Redirect"])
    assert "that change no longer exists" in page.text


def test_a_crafted_tab_is_404_and_never_reaches_the_redirect(app_and_org):
    """`tab` is the only part of the redirect target a caller supplies, so it
    is the open-redirect surface — and nothing pinned it before review round
    1 (F9). It is refused outright, not sanitised: the batch must not be
    reverted on the way to a 404 either."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)

    # ("program" is deliberately NOT in this list — it is a real tab id.)
    for crafted in ("//evil.example.com/", "../../evil", "overview", ""):
        response = client.post(
            f"/accounts/{org.ref}/changes/{batch.ref}/revert",
            params={"tab": crafted},
        )
        assert response.status_code == 404, crafted
        assert "HX-Redirect" not in response.headers, crafted
        assert batches_repo.get(conn, batch.id).reverted_at is None, crafted


def test_two_records_conflicting_on_one_field_say_it_once(app_and_org):
    """Review round 1, F5, and round 2, A. One clause per Conflict printed
    "contact title changed since, contact title changed since" for TWO
    contacts conflicting on ONE field — a sentence that reads as a rendering
    fault and still names neither record.

    The fixture is the whole point of this test and the reason it was
    rewritten: round 1 shipped a version that conflicted five FIELDS on one
    contact, which is five distinct (entity_type, field) pairs — the exact
    case the dedupe cannot touch. Deleting the dedupe left it green. Two
    records, one field, is what makes the assertion below depend on it."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    contacts = contacts_repo.for_org(conn, org.id)
    assert len(contacts) >= 2, "need two contacts to conflict on the same field"
    first, second = contacts[0], contacts[1]

    with batches_svc.open_batch(
        conn, source="web", tool="edit_contact",
        summary="retitled two contacts", org_id=org.id,
    ):
        contacts_repo.update(conn, first.id, title="Head of Risk")
        contacts_repo.update(conn, second.id, title="Head of Claims")

    batch = _latest_batch(conn)
    # both retitled again, outside that batch: two conflicts, one pair
    with batches_svc.open_batch(
        conn, source="web", tool="edit_contact", summary="and again", org_id=org.id,
    ):
        contacts_repo.update(conn, first.id, title="Head of Risk x")
        contacts_repo.update(conn, second.id, title="Head of Claims x")

    response = _revert(client, org.ref, batch.ref)
    assert _redirect_params(response)["outcome"] == "refused"
    # two changes really are in conflict — otherwise the dedupe has nothing
    # to dedupe and the assertion below is vacuous
    assert _redirect_params(response)["n"] == "2"

    page = client.get(response.headers["HX-Redirect"])
    text = re.search(r'<span class="toast-text">([^<]*)</span>', page.text)
    assert text, "no toast rendered"
    said = text.group(1)
    assert said == f"{batch.ref} refused — contact title changed since", said


def test_the_refusal_names_three_conflicts_then_counts_the_rest(app_and_org):
    """`+N more` had no test (review round 1, F9). Five distinct fields
    conflict here — five distinct (entity_type, field) pairs, so nothing is
    deduped: three are named and two are counted. The dedupe itself is
    covered by the test above, which is the one that can see it."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    contact = contacts_repo.for_org(conn, org.id)[0]
    fields = {
        "title": "Head of Risk", "role": "syndicate lead",
        "email": "a@example.com", "phone": "555-0101", "mobile": "555-0102",
    }
    with batches_svc.open_batch(
        conn, source="web", tool="edit_contact",
        summary="five cells on the first contact", org_id=org.id,
    ):
        contacts_repo.update(conn, contact.id, **fields)

    batch = _latest_batch(conn)
    # every one of the five changed again, outside that batch
    with batches_svc.open_batch(
        conn, source="web", tool="edit_contact", summary="and again", org_id=org.id,
    ):
        contacts_repo.update(conn, contact.id, **{k: v + " x" for k, v in fields.items()})

    response = _revert(client, org.ref, batch.ref)
    assert _redirect_params(response)["outcome"] == "refused"

    page = client.get(response.headers["HX-Redirect"])
    text = re.search(r'<span class="toast-text">([^<]*)</span>', page.text)
    assert text, "no toast rendered"
    said = text.group(1)
    assert said.startswith(f"{batch.ref} refused — ")
    assert said.count("changed since") == 3, said
    assert said.endswith(", +2 more"), said


def test_the_remedy_is_a_second_line_not_a_second_column(app_and_org):
    """`.toast` is a flex row, so the remedy rendered BESIDE the message until
    review round 1 (F7). Both now live in one `.toast-lines` column — the
    markup is what makes "second line" true, so the markup is what is
    asserted."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    contact = contacts_repo.for_org(conn, org.id)[0]
    with batches_svc.open_batch(
        conn, source="web", tool="edit_contact", summary="a title", org_id=org.id,
    ):
        contacts_repo.update(conn, contact.id, title="Head of Risk")
    batch = _latest_batch(conn)
    _set_title(client, org, contact.id, "Head of Claims")

    page = client.get(_revert(client, org.ref, batch.ref).headers["HX-Redirect"])
    block = re.search(r'<div class="toast-lines">(.*?)</div>', page.text, re.S)
    assert block, "the toast has no message column"
    assert 'class="toast-text"' in block.group(1)
    assert 'class="toast-remedy"' in block.group(1)


def test_a_crafted_outcome_url_cannot_put_words_in_the_toast(app_and_org):
    """Review round 1, F1. `?outcome=reverted&undo=<prose>&n=9999` used to
    render that prose plus a fabricated success count inside BookKit's own
    toast — attacker-chosen text in the app's chrome, which is phishing even
    though Jinja escapes it. Every batch-naming token is now checked against
    the book first, and the toast prints `batch.ref`, never the param."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    crafted = "SECURITY NOTICE call 555-0100"
    page = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "reverted", "undo": crafted, "n": "9999"},
    )
    assert page.status_code == 200
    assert crafted not in page.text
    assert 'class="toast"' not in page.text

    # ...and a REAL ref that has not been reverted cannot be dressed up as one
    # either: the count is only a report about a revert that happened.
    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)
    page = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "reverted", "undo": batch.ref, "n": "9999"},
    )
    assert 'class="toast"' not in page.text


def test_a_plain_value_error_is_not_dressed_up_as_a_program_file(app_and_org):
    """Review round 1, F2. `except ValueError` around revert assumed the
    program-file refusal was the only ValueError it could raise; pydantic's
    ValidationError subclasses ValueError and repo/base.py raises bare ones,
    so a malformed event_log row under an `edit_contact` batch made the page
    state that a contact edit "wrote a towerkit program FILE". The outcome is
    decided from `batch.tool` now, and an unexpected exception propagates as
    the bug it is rather than wearing a wrong explanation."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    contact = contacts_repo.for_org(conn, org.id)[0]
    with batches_svc.open_batch(
        conn, source="web", tool="edit_contact", summary="a title", org_id=org.id,
    ):
        contacts_repo.update(conn, contact.id, title="Head of Risk")
    batch = _latest_batch(conn)
    assert not batch.tool.startswith("program_")

    from bookkit.web.routes import changes as changes_route

    def exploding_plan(_conn, _batch):
        raise ValueError("event_log row names no column of 'contact'")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(batches_svc, "plan_revert", exploding_plan)
        with pytest.raises(ValueError):
            _revert(client, org.ref, batch.ref)

    # and the token itself is off the tool, so no crafted `outcome=program`
    # can claim a towerkit file was written by a contact edit either
    page = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "program", "undo": batch.ref},
    )
    assert "towerkit program FILE" not in page.text
    assert changes_route.toast_for(conn, org, {"outcome": "program", "undo": batch.ref}) is None


def test_a_key_error_from_inside_revert_is_not_dressed_up_as_gone(app_and_org):
    """Review round 2, B — F2's failure mode wearing a different exception.
    `except KeyError` around revert was written for one race (the batch being
    hard-deleted between the two reads on this autocommit connection), but it
    caught every KeyError revert can raise: base.update, base.undelete and
    ENTITY_TABLES[entity_type] all raise KeyError for a missing ENTITY, and
    answering those with "that change no longer exists" says it about a batch
    that is still sitting in the rail. The route re-reads the ref now and only
    claims `gone` when the ref is actually gone."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    contact = contacts_repo.for_org(conn, org.id)[0]
    with batches_svc.open_batch(
        conn, source="web", tool="edit_contact", summary="a title", org_id=org.id,
    ):
        contacts_repo.update(conn, contact.id, title="Head of Risk")
    batch = _latest_batch(conn)

    def missing_entity(_conn, _batch):
        raise KeyError("no contact CON-9999")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(batches_svc, "plan_revert", missing_entity)
        with pytest.raises(KeyError):
            _revert(client, org.ref, batch.ref)

    # ...and the race the clause exists for still answers `gone` rather than
    # raising: with the ref itself unresolvable, KeyError IS the batch being
    # gone. Both halves, or the fix is just a different wrong answer.
    assert _redirect_params(_revert(client, org.ref, "MCP-9999"))["outcome"] == "gone"


def test_gone_is_not_rendered_about_a_batch_that_is_still_here(app_and_org):
    """Review round 2, D. `?outcome=gone&undo=<a live ref>` rendered "that
    change no longer exists" while the Recent changes rail on the SAME screen
    listed that batch — two contradictory statements one screenful apart.
    `gone` is a claim about the ref, so it is checked like every other."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)

    page = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "gone", "undo": batch.ref},
    )
    assert page.status_code == 200
    assert "that change no longer exists" not in page.text
    # the batch really is on the page it would have contradicted
    assert batch.ref in page.text

    # a ref that resolves to nothing still says it — the token is not dead
    page = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "gone", "undo": "MCP-9999"},
    )
    assert "that change no longer exists" in page.text


def test_a_negative_count_renders_no_toast(app_and_org):
    """Review round 2, E. `n` was unvalidated beyond int(), so
    `?outcome=reverted&undo=<ref>&n=-9999` rendered "reverted — -9999
    change(s)". Inflation is accepted (the revert did happen); a negative
    count describes no batch state that can exist."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)
    assert _revert(client, org.ref, batch.ref).status_code == 204

    page = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "reverted", "undo": batch.ref, "n": "-9999"},
    )
    assert "-9999" not in page.text
    assert 'class="toast"' not in page.text


def test_toast_for_refuses_a_batch_from_another_account(app_and_org):
    """Review round 2, F. The org check on the WRITE path is pinned; the one
    in `toast_for` was not, and removing it left the suite green. Without it,
    a crafted link on account A prints account B's activity in A's chrome —
    a cross-account leak on a read the account page performs for anyone."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.repo import orgs
    from bookkit.web.routes import changes as changes_route

    other = next(o for o in orgs.list_orgs(conn, kind="client") if o.id != org.id)
    contact = contacts_repo.for_org(conn, other.id)[0]
    _set_title(client, other, contact.id, "Head of Risk")
    batch = _latest_batch(conn)
    assert _revert(client, other.ref, batch.ref).status_code == 204

    # the same ref, rendered on the OTHER account's page: no toast at all
    for outcome in ("reverted", "already", "refused", "program"):
        params = {"outcome": outcome, "undo": batch.ref, "n": "1"}
        assert changes_route.toast_for(conn, org, params) is None, outcome
    page = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "reverted", "undo": batch.ref, "n": "1"},
    )
    assert 'class="toast"' not in page.text
    assert batch.ref not in page.text

    # ...and it DOES render on its own account — otherwise this passes just
    # as well against a toast_for that renders nothing for anybody
    assert changes_route.toast_for(
        conn, other, {"outcome": "reverted", "undo": batch.ref, "n": "1"}
    ) is not None


def test_a_token_naming_a_state_the_batch_is_not_in_renders_nothing(app_and_org):
    """Review round 2, F. `already` and `refused` each assert a batch state,
    and neither gate was tested: removing either left the suite green. A
    crafted `already` on a live batch says a revert happened that did not,
    and a crafted `refused` on a reverted one makes _refusal_text re-plan a
    batch whose every change now conflicts — a sentence about nothing."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.web.routes import changes as changes_route

    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)

    # unreverted: `already` is a lie, `refused` is the honest state
    assert changes_route.toast_for(
        conn, org, {"outcome": "already", "undo": batch.ref}
    ) is None
    assert changes_route.toast_for(
        conn, org, {"outcome": "refused", "undo": batch.ref}
    ) is not None
    page = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "already", "undo": batch.ref},
    )
    assert "already reverted" not in page.text

    assert _revert(client, org.ref, batch.ref).status_code == 204

    # reverted: the two swap over
    assert changes_route.toast_for(
        conn, org, {"outcome": "already", "undo": batch.ref}
    ) is not None
    assert changes_route.toast_for(
        conn, org, {"outcome": "refused", "undo": batch.ref}
    ) is None


# --- interactions: the timeline on the Relationship tab (Task 10) ------------
#
# READ, EDIT, DELETE. Creation is deliberately absent: forms.entities has
# `interaction_form(existing)` — an EDIT builder that requires an Interaction —
# and no create builder, because logging one is quick capture's job (it does
# account matching and the follow-up-task offer). The header's "+ Log
# interaction" pill stays inert, and no test here asks for a create route.
#
# Editing is a WHOLE FORM, not an inline subject cell, even though the design
# prototype draws a dashed underline there: forms/inline.py owns which fields
# are inline-editable for BOTH surfaces and has no INTERACTION_FIELDS, so a
# web-only set would fork the two surfaces on the one axis that file exists to
# keep unified (R49).


@pytest.fixture
def timeline(app_and_org):
    """One interaction with a NOTE BODY and a named attendee, on the account
    the url names.

    Built rather than drawn from the seed: seed.py scatters its 200
    interactions across randomly chosen orgs, so "the first client org happens
    to own one with a body and an attendee" is a coin toss — and a coin-toss
    fixture is how a green test comes to assert nothing.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.repo import interactions as interactions_repo

    attendee = contacts_repo.for_org(conn, org.id)[0]
    entry = interactions_repo.log(
        conn, org.id, type="call", subject="Zephyr renewal strategy call",
        occurred_on="2026-08-13",
        body="They want the umbrella limit taken to 25M before renewal.",
        contact_ids=[attendee.id],
    )
    return client, org, entry, attendee


def _edit_url(org, interaction_id: str) -> str:
    return f"/accounts/{org.ref}/interactions/{interaction_id}/edit"


def _edit_payload(entry, **overrides) -> dict[str, str]:
    """Every field interaction_form declares — a browser posts them all."""
    payload = {
        "occurred_on": entry.occurred_on,
        "type": str(entry.type),
        "subject": entry.subject,
        "body": entry.body or "",
    }
    payload.update(overrides)
    return payload


def test_the_timeline_shows_the_note_body(timeline):
    """The body was stored and never shown anywhere before review F33. The
    timeline is the surface that fixes that on the web."""
    client, org, entry, _attendee = timeline

    response = client.get(f"/accounts/{org.ref}/relationship")

    assert response.status_code == 200
    assert entry.subject in response.text
    assert "They want the umbrella limit taken to 25M before renewal." in response.text, (
        "the timeline renders the subject but not the note body"
    )


def test_the_timeline_shows_who_attended(timeline):
    """interactions.attendees — 'a call' with nobody on it is half a record.

    Asserted INSIDE the timeline's own who-line, not against the whole page:
    the PEOPLE panel beside it already prints every contact's name, so a bare
    `name in response.text` passed with no timeline rendered at all."""
    client, org, entry, attendee = timeline

    response = client.get(f"/accounts/{org.ref}/relationship")

    who = re.findall(r'class="timeline-who">([^<]*)<', response.text)
    assert who, "the timeline renders no attendee line"
    assert any(f"{attendee.first_name} {attendee.last_name}" in line for line in who)


def test_a_bare_number_is_refused_as_a_date_on_the_web_too(timeline):
    """dateparser reads "5" as a MONTH and future-biases it: a follow-up typed
    as "the 5th" saved as 2027-05-01 and fell off every attention window
    silently. The refusal is forms.spec.date_refusal's own sentence, on the web
    exactly as in the TUI — asserted against what that function returns, not
    against a copy of its wording."""
    from markupsafe import escape

    from bookkit.forms.spec import date_refusal
    from bookkit.repo import interactions as interactions_repo

    client, org, entry, _attendee = timeline
    conn = client.app.state.conn

    response = client.post(
        _edit_url(org, entry.id), data=_edit_payload(entry, occurred_on="5")
    )

    assert response.status_code == 200, "htmx drops 4xx — a refusal must be a 200"
    assert str(escape(date_refusal("5"))) in response.text
    assert interactions_repo.get(conn, entry.id).occurred_on == "2026-08-13", (
        "the refused date was written anyway"
    )


def test_a_refused_interaction_edit_keeps_every_value_and_writes_nothing(timeline):
    """The same contract on the form that shipped after Task 8. It routes
    through the shared `_save`, so this is a seam check, not a second
    implementation: the assertion is that the interaction edit form actually
    goes through it — a green suite proves nothing broke, not that the new
    path is taken."""
    client, org, entry, _attendee = timeline
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import interactions as interactions_repo

    before_batches = len(batches_repo.recent(conn, since="", limit=50))

    response = client.post(
        _edit_url(org, entry.id),
        data=_edit_payload(
            entry, subject="", body="they want the umbrella at 25M"
        ),
    )

    assert response.status_code == 200, "htmx drops 4xx — a refusal must be a 200"
    assert '<p class="form-error" role="alert">subject is required</p>' in response.text
    # the typed body survives, and so does the date and the type
    assert "they want the umbrella at 25M" in response.text
    assert entry.occurred_on in response.text
    # nothing written: neither the record nor a stranded batch
    assert interactions_repo.get(conn, entry.id).subject == entry.subject
    assert interactions_repo.get(conn, entry.id).body == entry.body
    assert len(batches_repo.recent(conn, since="", limit=50)) == before_batches


def test_editing_an_interaction_writes_one_batch(timeline):
    """The assertion is the BATCH, not the field: a write that lands outside
    one is unreachable from `R` and from the rail's Revert."""
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import interactions as interactions_repo
    from bookkit.services import batches as batches_svc

    client, org, entry, _attendee = timeline
    conn = client.app.state.conn

    response = client.post(
        _edit_url(org, entry.id),
        data=_edit_payload(entry, subject="Zephyr renewal strategy call (rescheduled)"),
    )
    assert response.status_code == 200
    assert interactions_repo.get(conn, entry.id).subject.endswith("(rescheduled)")

    batch = _latest_batch(conn)
    assert batch is not None, "the edit wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"
    assert batches_repo.events_for(conn, batch.id), "the batch carries no events to revert"

    batches_svc.revert(conn, batch.ref, now=db.utc_now())
    assert interactions_repo.get(conn, entry.id).subject == "Zephyr renewal strategy call"


def test_blanking_the_note_clears_it(timeline):
    """apply_interaction has a deliberate branch for this — a blank textarea
    parses to None and `dropped()` would swallow it, leaving the old note in
    place while the form showed it gone. Untested on the web until now."""
    from bookkit.repo import interactions as interactions_repo

    client, org, entry, _attendee = timeline
    conn = client.app.state.conn

    response = client.post(_edit_url(org, entry.id), data=_edit_payload(entry, body=""))

    assert response.status_code == 200
    assert interactions_repo.get(conn, entry.id).body is None, (
        "the note survived the edit that blanked it"
    )


def test_deleting_asks_first_then_soft_deletes(timeline):
    """GET is the confirm and writes NOTHING; POST acts. The delete is SOFT —
    the confirm says so, because 'delete' that is really 'take it off the list'
    must not read as destruction."""
    from bookkit.repo import base as base_repo
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import interactions as interactions_repo

    client, org, entry, _attendee = timeline
    conn = client.app.state.conn
    url = f"/accounts/{org.ref}/interactions/{entry.id}/delete"
    before = _latest_batch(conn)
    before_batches = batches_repo.recent(conn, since="", limit=50)

    confirm = client.get(url)

    assert confirm.status_code == 200
    assert "data-form-cancel" in confirm.text and "Cancel" in confirm.text, (
        "the confirm has no way out — Cancel is required of every form here"
    )
    assert entry.id in {i.id for i in interactions_repo.for_org(conn, org.id)}
    assert _latest_batch(conn) == before, "the confirm GET wrote something"

    removed = client.post(url)

    assert removed.status_code == 200
    assert entry.id not in {i.id for i in interactions_repo.for_org(conn, org.id)}
    row = base_repo.raw_row(conn, "interaction", entry.id)
    assert row is not None and row["deleted_at"] is not None, (
        "a soft delete keeps the row and stamps deleted_at"
    )
    after = batches_repo.recent(conn, since="", limit=50)
    assert len(after) == len(before_batches) + 1, (
        "one writer action is one undo unit — this delete wrote "
        f"{len(after) - len(before_batches)} batches"
    )
    batch = after[0]
    assert batch.source == "web"
    # the CONTENT, not just the existence: services.interactions._summary is
    # shared with the TUI so the two surfaces cannot describe one write
    # differently, and a summary that says only "deleted an interaction"
    # leaves `R` and the changes rail naming nothing the user can recognise.
    assert entry.subject in batch.summary and org.name in batch.summary, (
        f"the batch summary says neither what was deleted nor from where: {batch.summary!r}"
    )


def test_the_confirm_names_the_consequences_and_a_way_out(timeline):
    """services.interactions.consequences is the confirm's whole content, for
    the reason services.contacts.consequences is the contact confirm's: two
    surfaces that compose their own sentences promise different things about
    one write. The TUI half of this claim is asserted below.

    WHAT the sentences say is asserted here too, not just that the render
    matches whatever they happen to be (fix round 1). `assert notes` passes on
    one generic line: the entire last-touch branch could be deleted, and the
    revertibility sentence could be replaced with "gone for good" — a
    materially false claim about a soft delete — with the suite green.
    """
    from markupsafe import escape

    from bookkit.repo import interactions as interactions_repo
    from bookkit.services import interactions as interactions_svc

    client, org, entry, _attendee = timeline
    conn = client.app.state.conn
    # strictly newer than anything seeded (the seed's newest is 2026-08-13), so
    # this IS the account's last touch and the fixture's entry is what the rail
    # falls back to — both halves of the sentence have a real read behind them.
    newest = interactions_repo.log(
        conn, org.id, type="call", subject="Zephyr excess layer call",
        occurred_on="2026-08-17", body="the newest touch on the account",
    )
    notes = interactions_svc.consequences(conn, newest.id)

    assert any("last touch" in n and entry.occurred_on in n for n in notes), (
        "no consequence names the date the account's last touch falls back to "
        f"({entry.occurred_on}): {notes}"
    )
    assert all(newest.occurred_on not in n for n in notes), (
        "the fallback names the date being deleted, not the one it falls back to"
    )
    assert any("attendee" in n and ("undo" in n or "revertible" in n) for n in notes), (
        f"no consequence says the delete is revertible, attendees and all: {notes}"
    )

    html = client.get(f"/accounts/{org.ref}/interactions/{newest.id}/delete").text

    for note in notes:
        # escaped, not raw: the sentences are prose and carry apostrophes,
        # which Jinja autoescapes — comparing raw would fail on a confirm that
        # renders the list perfectly.
        assert str(escape(note)) in html, f"the web confirm does not show: {note}"


async def test_the_tui_delete_confirm_shows_the_same_consequences(snapshot_db):
    """The cross-surface half of the test above. It lives in this file rather
    than beside the other TUI tests because the contract being asserted is that
    the two renders are the SAME — split apart, that is invisible, which is how
    the contact confirm's note loop stayed deletable with the suite green."""
    from textual.screen import ModalScreen
    from textual.widgets import Static

    from bookkit.repo import contacts as contacts_repo
    from bookkit.repo import interactions as interactions_repo
    from bookkit.repo import orgs as orgs_repo
    from bookkit.services import interactions as interactions_svc
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.tables import ListTable

    app = BookkitApp(snapshot_db)
    org = orgs_repo.list_orgs(app.conn, kind="client")[0]
    attendee = contacts_repo.for_org(app.conn, org.id)[0]
    entry = interactions_repo.log(
        app.conn, org.id, type="call", subject="Zephyr renewal strategy call",
        occurred_on="2026-08-13", body="note", contact_ids=[attendee.id],
    )
    notes = interactions_svc.consequences(app.conn, entry.id)
    assert notes

    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()
        table = app.screen.query_one("#interactions-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(entry.id))
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()

        assert isinstance(app.screen, ModalScreen), "D deleted an interaction with no confirm"
        shown = "\n".join(str(w.render()) for w in app.screen.query(Static))
        for note in notes:
            assert note in shown, f"the TUI confirm does not show: {note}"


def test_an_interaction_from_another_account_is_not_editable(timeline):
    """{ref} plus an id is TWO claims and both are checked — the hole closed
    across eighteen routes on 2026-08-18, asserted here for the four new ones
    so they cannot reopen it. tests/test_web_scoping.py drives them in its own
    loop as well."""
    from bookkit.repo import base as base_repo
    from bookkit.repo import interactions as interactions_repo
    from bookkit.repo import orgs as orgs_repo

    client, org, _entry, _attendee = timeline
    conn = client.app.state.conn
    other = [o for o in orgs_repo.list_orgs(conn, kind="client") if o.id != org.id][0]
    theirs = interactions_repo.log(
        conn, other.id, type="meeting", subject="Borealis broker of record",
        occurred_on="2026-08-11", body="theirs",
    )
    calls = [
        ("GET", f"/accounts/{org.ref}/interactions/{theirs.id}/edit", None),
        ("POST", f"/accounts/{org.ref}/interactions/{theirs.id}/edit",
         _edit_payload(theirs, subject="hijacked")),
        ("GET", f"/accounts/{org.ref}/interactions/{theirs.id}/delete", None),
        ("POST", f"/accounts/{org.ref}/interactions/{theirs.id}/delete", None),
    ]

    open_routes = []
    for method, url, form in calls:
        response = client.request(method, url, data=form)
        if response.status_code != 404:
            open_routes.append(f"{method} {url} answered {response.status_code}")
        if other.name in response.text:
            open_routes.append(f"{method} {url} named the other account")

    assert not open_routes, "; ".join(open_routes)
    assert interactions_repo.get(conn, theirs.id).subject == "Borealis broker of record"
    row = base_repo.raw_row(conn, "interaction", theirs.id)
    assert row is not None and row["deleted_at"] is None


def test_filtering_the_timeline_by_type(timeline):
    """Server-side, on the tab url — no JS. The count follows the filter, or
    the header contradicts the list under it."""
    from bookkit.repo import interactions as interactions_repo

    client, org, entry, _attendee = timeline
    conn = client.app.state.conn
    email = interactions_repo.log(
        conn, org.id, type="email", subject="Zephyr umbrella quote follow-up",
        occurred_on="2026-08-12", body="sent the quote",
    )
    calls = [i for i in interactions_repo.for_org(conn, org.id) if i.type == "call"]

    response = client.get(f"/accounts/{org.ref}/relationship", params={"type": "call"})

    assert response.status_code == 200
    assert entry.subject in response.text
    assert email.subject not in response.text, "a filtered-out row is still rendered"
    count = re.search(r'class="timeline-count">(\d+)<', response.text)
    assert count is not None, "the timeline header renders no count"
    assert count.group(1) == str(len(calls))


def test_the_type_filters_offer_the_account_s_own_types_and_no_others(app_and_org):
    """The pills are the timeline's ONLY filter affordance, and nothing held
    them (fix round 1): `present = []` renders the "All" pill alone — no way to
    filter by type at all — and every other test here survives it, because
    they hit ?type= directly or are satisfied by "All".

    The account is chosen for leaving part of the vocabulary unused, so "no
    pill for a type that is not here" is a real assertion rather than a vacuous
    one — the seeded first client happens to use all six."""
    from bookkit.models import InteractionType
    from bookkit.repo import interactions as interactions_repo
    from bookkit.repo import orgs as orgs_repo

    client, _org = app_and_org
    conn = client.app.state.conn
    for candidate in orgs_repo.list_orgs(conn, kind="client"):
        entries = interactions_repo.for_org(conn, candidate.id, limit=200)
        present = [t.value for t in InteractionType
                   if any(str(e.type) == t.value for e in entries)]
        absent = [t.value for t in InteractionType if t.value not in present]
        if present and absent:
            break
    else:  # pragma: no cover - the seed has never produced this
        raise AssertionError("no seeded account leaves an interaction type unused")

    response = client.get(f"/accounts/{candidate.ref}/relationship")

    block = re.search(r'class="timeline-filters".*?</div>', response.text, re.S)
    assert block is not None, "the timeline renders no type filters"
    labels = re.findall(r">([^<>]+)</a>", block.group(0))
    assert labels == ["All"] + [t.replace("_", " ") for t in present], (
        f"the pills are {labels}; this account logs {present} and never {absent}"
    )


def test_a_crafted_type_is_not_reflected_into_the_page(timeline):
    """_filter_type validates against models.InteractionType rather than
    passing the raw string through, and its own docstring is the only thing
    that said so (fix round 1). Without the check a crafted ?type= is echoed
    into the filtered-empty sentence — Jinja escapes it and the app is loopback
    single-user, so this is a defence in depth, not a live hole; but an
    untested defence is one the next edit deletes."""
    from bookkit.repo import interactions as interactions_repo

    client, org, _entry, _attendee = timeline
    conn = client.app.state.conn
    total = len(interactions_repo.for_org(conn, org.id, limit=200))

    response = client.get(
        f"/accounts/{org.ref}/relationship", params={"type": "zzcrafted"}
    )

    assert response.status_code == 200
    assert "zzcrafted" not in response.text, (
        "an unrecognised type is echoed back into the page"
    )
    count = re.search(r'class="timeline-count">(\d+)<', response.text)
    assert count is not None, "the timeline header renders no count"
    assert count.group(1) == str(total), (
        "an unrecognised type filtered the timeline instead of being ignored"
    )


def test_a_filter_link_does_not_resurrect_the_revert_toast(timeline):
    """Task 15b puts ?undo=&outcome=&n= on this same tab url. The filter links
    are built with `type` ALONE, so following one clears the toast params
    rather than re-showing a message about a revert that already happened —
    which is what carrying the current query string through would do."""
    client, org, _entry, _attendee = timeline
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    _set_title(client, org, contact.id, "Head of Risk")
    batch = _latest_batch(conn)
    assert batch is not None
    assert _revert(client, org.ref, batch.ref).status_code == 204

    response = client.get(
        f"/accounts/{org.ref}/relationship",
        params={"outcome": "reverted", "undo": batch.ref, "n": "1"},
    )

    assert "reverted" in response.text, "the toast this test is about did not render"
    filters = re.search(
        r'class="timeline-filters".*?</div>', response.text, re.S
    )
    assert filters is not None, "the timeline renders no type filters"
    links = re.findall(r'href="([^"]*)"', filters.group(0))
    assert links, "the type filters are not links"
    for link in links:
        assert "undo=" not in link and "outcome=" not in link and "n=" not in link, link


def test_the_parity_ledger_records_the_interaction_routes():
    """web/parity.py is the ledger that stops "narrowed slice" from becoming
    "silently missing". Interactions have both a web edit and a web delete, and
    the ledger must still name each of them wherever its action now sits.

    `edit_here` stays PENDING: it covers rows this task did not build, and
    partial coverage belongs in the reason, never in a false IMPLEMENTED.
    `delete_row` MOVED to IMPLEMENTED on 2026-08-21, when tasks — the last of
    AccountScreen.DELETABLE's four row kinds with no web route — got their
    Drop. Whichever dict it sits in, it must still name the interaction
    route, because the promotion is what makes a dropped sentence invisible.
    """
    from bookkit.web.parity import IMPLEMENTED, PENDING

    assert "edit_here" in PENDING
    assert "interaction" in PENDING["edit_here"], (
        "the ledger does not record the interaction edit form"
    )
    assert "edit_here" not in IMPLEMENTED

    assert "delete_row" in IMPLEMENTED and "delete_row" not in PENDING
    assert "/interactions/" in IMPLEMENTED["delete_row"], (
        "the ledger still says interactions have no web removal"
    )
    assert "/drop" in IMPLEMENTED["delete_row"], (
        "the ledger does not record the task drop that closed this action"
    )
