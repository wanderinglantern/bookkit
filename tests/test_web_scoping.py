"""A web url names an ACCOUNT and a ROW, and both claims are checked.

Fix round 1, 2026-08-18. Thirteen routes under /accounts/{ref}/ also take an
entity id, and exactly one of them — routes/changes.py's revert — proved the
entity was this account's. The rest resolved the id alone, so a stale tab or a
pasted url wrote to whichever account owned the id while the page showed the
one in the url. The contact remove-confirm rendered the contradiction in a
single fragment: a header naming one account beside a consequence naming the
other, in the one artifact whose whole justification is that it shows a plan.

The guard is one function (routes/account.py `_owned`), so this file asserts it
per route FAMILY rather than per handler, and asserts the two things the guard
must not break: a task owned only through its placement (org_id NULL) stays
reachable, and an item cannot be worked under a request it does not belong to.

Exposure was never an attacker — the app is loopback-only and single-user.
It is a write landing on an account the page never showed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import db
from bookkit.repo import base
from bookkit.repo import batches as batches_repo
from bookkit.repo import contacts as contacts_repo
from bookkit.repo import orgs as orgs_repo
from bookkit.repo import placements as placements_repo
from bookkit.repo import rfi as rfi_repo
from bookkit.repo import tasks as tasks_repo
from bookkit.web.app import create_app


@pytest.fixture
def two_accounts(tmp_path: Path):
    """Two client accounts, each with one of everything the web can edit.

    Hand-built, never Grant's book. `mine` is the account the url will name;
    `theirs` owns every id the tests then try to reach through it.
    """
    path = tmp_path / "scoping.db"
    conn = db.connect(path)

    def furnish(name: str) -> dict[str, object]:
        org = orgs_repo.create(conn, name=name, kind="client")
        contact = contacts_repo.create(
            conn, org.id, first_name="Rosa", last_name=name.split()[0]
        )
        task = tasks_repo.create(
            conn, f"chase {name}", org_id=org.id, due_on="2026-09-01"
        )
        request = rfi_repo.create_request(
            conn, org.id, title=f"{name} renewal data", requested_on="2026-08-01"
        )
        item = rfi_repo.add_item(conn, request.id, prompt=f"{name} loss runs")
        placement = placements_repo.create(
            conn, org.id, program_name=f"{name} casualty",
            period_from="2026-01-01", period_to="2026-12-31",
        )
        # org_id NULL, owned only through the placement — legal, and the shape
        # repo.tasks.open_tasks_for_client exists to catch. The ownership rule
        # has to see it or the guard 404s a row the panel just rendered.
        via_placement = tasks_repo.create(
            conn, f"bind {name}", placement_id=placement.id, due_on="2026-09-02"
        )
        return {
            "org": org, "contact": contact, "task": task, "request": request,
            "item": item, "placement": placement, "via_placement": via_placement,
        }

    mine = furnish("Atomic Test Industries")
    theirs = furnish("Borealis Test Foods")
    conn.close()

    app = create_app(path)
    with TestClient(app) as client:
        yield client, mine, theirs


def _cross_account_calls(mine, theirs) -> list[tuple[str, str, str, dict[str, str]]]:
    """(name, method, url, form) for every route that takes {ref} plus an id.

    The url names `mine`; every id belongs to `theirs`. Route families, not
    handlers: display cell / editor cell / save is one contract in
    forms_render, and all three resolve the same id the same way.
    """
    ref = mine["org"].ref
    contact, task = theirs["contact"], theirs["task"]
    request_row, item = theirs["request"], theirs["item"]
    return [
        # relationship.py — contacts
        ("contact cell", "GET", f"/accounts/{ref}/contacts/{contact.id}/cell/email", {}),
        ("contact cell edit", "GET",
         f"/accounts/{ref}/contacts/{contact.id}/cell/email/edit", {}),
        ("contact cell save", "POST", f"/accounts/{ref}/contacts/{contact.id}/cell/email",
         {"email": "hijack@example.com"}),
        ("contact remove confirm", "GET",
         f"/accounts/{ref}/contacts/{contact.id}/remove", {}),
        ("contact remove", "POST", f"/accounts/{ref}/contacts/{contact.id}/remove", {}),
        # work.py — tasks
        ("task cell", "GET", f"/accounts/{ref}/tasks/{task.id}/cell/title", {}),
        ("task cell edit", "GET", f"/accounts/{ref}/tasks/{task.id}/cell/title/edit", {}),
        ("task cell save", "POST", f"/accounts/{ref}/tasks/{task.id}/cell/title",
         {"title": "hijacked"}),
        ("task done", "POST", f"/accounts/{ref}/tasks/{task.id}/done", {}),
        # work.py — requests
        ("request edit form", "GET", f"/accounts/{ref}/requests/{request_row.id}/edit", {}),
        ("request update", "POST", f"/accounts/{ref}/requests/{request_row.id}/edit",
         {"title": "hijacked", "requested_on": "2026-08-02"}),
        ("request detail", "GET", f"/accounts/{ref}/requests/{request_row.id}", {}),
        # work.py — request items
        ("item new form", "GET",
         f"/accounts/{ref}/requests/{request_row.id}/items/new", {}),
        ("item create", "POST", f"/accounts/{ref}/requests/{request_row.id}/items/new",
         {"prompt": "hijacked"}),
        ("item cell", "GET",
         f"/accounts/{ref}/requests/{request_row.id}/items/{item.id}/cell/prompt", {}),
        ("item cell edit", "GET",
         f"/accounts/{ref}/requests/{request_row.id}/items/{item.id}/cell/prompt/edit", {}),
        ("item cell save", "POST",
         f"/accounts/{ref}/requests/{request_row.id}/items/{item.id}/cell/prompt",
         {"prompt": "hijacked"}),
        ("item received", "POST",
         f"/accounts/{ref}/requests/{request_row.id}/items/{item.id}/received", {}),
    ]


def _fingerprint(conn, theirs) -> dict[str, object]:
    """Every column of every row the other account owns, plus its batch count.

    Raw rows, so a soft delete shows up as a change like any other, and the
    batch count so an empty batch left behind by a half-guarded write is a
    failure too."""
    ids = {
        "contact": theirs["contact"].id,
        "task": theirs["task"].id,
        "rfi_request": theirs["request"].id,
        "rfi_item": theirs["item"].id,
    }
    snapshot: dict[str, object] = {
        kind: dict(base.raw_row(conn, kind, entity_id) or {})
        for kind, entity_id in ids.items()
    }
    snapshot["items"] = [i.id for i in rfi_repo.items_for_request(conn, ids["rfi_request"])]
    snapshot["batches"] = len(batches_repo.recent(conn, since="", limit=500))
    return snapshot


def test_no_route_reaches_another_accounts_row(two_accounts):
    """One assertion for eighteen handlers: the url's account does not own the
    id, so every one of them 404s and nothing changes.

    Looped rather than parametrized, and every route reported rather than the
    first: the point is the PATTERN, so a run has to say how many of the
    eighteen are open, not stop at whichever is alphabetically unlucky. The
    fingerprint is re-taken before each call, so a route that does write is
    named by the call that made it and does not smear onto the rest."""
    client, mine, theirs = two_accounts
    conn = client.app.state.conn

    unguarded: list[str] = []
    for name, method, url, form in _cross_account_calls(mine, theirs):
        before = _fingerprint(conn, theirs)
        response = client.request(method, url, data=form or None)
        if response.status_code != 404:
            unguarded.append(f"{name} ({method}) answered {response.status_code}")
        if _fingerprint(conn, theirs) != before:
            unguarded.append(f"{name} ({method}) WROTE to the other account")

    assert not unguarded, (
        f"{len(unguarded)} route(s) reached an account the url did not name: "
        + "; ".join(unguarded)
    )


def test_the_refusal_never_names_the_other_account(two_accounts):
    """404, and a sentence about THIS account only. Distinguishing "no such id"
    from "not yours" is how a guessable id becomes a membership oracle."""
    client, mine, theirs = two_accounts
    ref, contact = mine["org"].ref, theirs["contact"]

    response = client.get(f"/accounts/{ref}/contacts/{contact.id}/remove")

    assert response.status_code == 404
    assert theirs["org"].name not in response.text
    assert mine["org"].name in response.text


def test_an_unknown_id_and_someone_elses_id_answer_the_same(two_accounts):
    client, mine, theirs = two_accounts
    ref = mine["org"].ref

    unknown = client.get(f"/accounts/{ref}/tasks/no-such-task/cell/title")
    foreign = client.get(f"/accounts/{ref}/tasks/{theirs['task'].id}/cell/title")

    assert unknown.status_code == foreign.status_code == 404


def test_the_accounts_own_rows_still_answer(two_accounts):
    """The guard must not 404 the page's own rows — including the task owned
    only through a placement (org_id NULL), which `task.org_id == org.id`
    alone would refuse."""
    client, mine, _theirs = two_accounts
    ref = mine["org"].ref

    assert client.get(
        f"/accounts/{ref}/contacts/{mine['contact'].id}/cell/email"
    ).status_code == 200
    assert client.get(
        f"/accounts/{ref}/tasks/{mine['task'].id}/cell/title"
    ).status_code == 200
    assert client.get(
        f"/accounts/{ref}/tasks/{mine['via_placement'].id}/cell/title"
    ).status_code == 200, "a placement-owned task the panel renders was refused"
    assert client.get(
        f"/accounts/{ref}/requests/{mine['request'].id}"
    ).status_code == 200
    assert client.get(
        f"/accounts/{ref}/requests/{mine['request'].id}/items/{mine['item'].id}"
        "/cell/prompt"
    ).status_code == 200


def test_an_item_cannot_be_worked_under_the_wrong_request(two_accounts):
    """Both ids are checked, not just the outer one: this request and this item
    are the same account's, but the item is not that request's. The panel that
    came back would list rows the write never touched."""
    client, mine, _theirs = two_accounts
    ref = mine["org"].ref
    conn = client.app.state.conn
    other_request = rfi_repo.create_request(
        conn, mine["org"].id, title="second ask", requested_on="2026-08-05"
    )

    response = client.post(
        f"/accounts/{ref}/requests/{other_request.id}/items/{mine['item'].id}/received"
    )

    assert response.status_code == 404
    assert rfi_repo.get_item(conn, mine["item"].id).received_on is None


def test_a_removed_contact_of_this_account_still_gets_the_services_answer(two_accounts):
    """The removal POST checks ownership against the RAW row, so a contact this
    account already removed reaches services.contacts.remove and gets "already
    removed" — the alive-filtered guard would have buried it under "no
    contact", which is a different and less true thing to tell someone who
    double-clicked."""
    client, mine, _theirs = two_accounts
    ref, contact = mine["org"].ref, mine["contact"]

    assert client.post(f"/accounts/{ref}/contacts/{contact.id}/remove").status_code == 200
    second = client.post(f"/accounts/{ref}/contacts/{contact.id}/remove")

    assert second.status_code == 200
    assert "already removed" in second.text
