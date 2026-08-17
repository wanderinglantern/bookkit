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
