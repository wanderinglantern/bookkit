"""Removing a contact from an account, on all three surfaces.

Grant, 2026-08-18: the MCP server filed a wholesaler as a CLIENT contact and
nothing could take it off — `repo/contacts.delete` existed as a soft delete and
no surface called it. A write with no inverse.

The whole slice lives in one test file on purpose: the rule is one service
(`services/contacts.remove`) that all three surfaces call, and the thing most
worth asserting is that they behave identically. Split across
test_mcpserver/test_tui/test_web_writes that shared contract is invisible.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from textual.screen import ModalScreen
from textual.widgets import Static

from bookkit import db, mcpserver
from bookkit.repo import base
from bookkit.repo import batches as batches_repo
from bookkit.repo import contacts as contacts_repo
from bookkit.repo import interactions as interactions_repo
from bookkit.repo import orgs as orgs_repo
from bookkit.services import batches as batches_svc
from bookkit.tui.app import BookkitApp
from bookkit.tui.widgets.tables import ListTable
from bookkit.web.app import create_app


@pytest.fixture
def book(tmp_path: Path):
    """A tiny hand-built account: one client, two contacts (one primary), and
    an interaction both attended. Seeded sample data only — never Grant's."""
    path = tmp_path / "contacts.db"
    conn = db.connect(path)
    org = orgs_repo.create(conn, name="Acme Freight", kind="client")
    keeper = contacts_repo.create(
        conn, org.id, first_name="Rosa", last_name="Delgado", title="Risk Manager"
    )
    wrong = contacts_repo.create(
        conn, org.id, first_name="Pat", last_name="Wholesale", title="Wholesaler"
    )
    meeting = interactions_repo.log(
        conn, org.id, type="meeting", subject="Renewal strategy",
        occurred_on="2026-08-12", contact_ids=[keeper.id, wrong.id],
    )
    return conn, org, keeper, wrong, meeting


# --- the service: the rules all three surfaces inherit ----------------------


def test_removing_a_contact_takes_them_off_the_account_and_off_attendees(book):
    from bookkit.services import contacts as contacts_svc

    conn, org, keeper, wrong, meeting = book

    contacts_svc.remove(conn, wrong.id, source="mcp")

    assert [c.id for c in contacts_repo.for_org(conn, org.id)] == [keeper.id]
    assert [c.id for c in interactions_repo.attendees(conn, meeting.id)] == [keeper.id]


def test_the_interactions_survive_and_undelete_puts_the_contact_back(book):
    """Reversibility is proved, not asserted: the interaction is untouched and
    base.undelete restores the contact on BOTH lists, attendee link intact."""
    from bookkit.services import contacts as contacts_svc

    conn, org, keeper, wrong, meeting = book

    contacts_svc.remove(conn, wrong.id, source="mcp")

    assert interactions_repo.get(conn, meeting.id).subject == "Renewal strategy"
    assert [i.id for i in interactions_repo.for_org(conn, org.id)] == [meeting.id]

    base.undelete(conn, "contact", wrong.id)

    assert wrong.id in {c.id for c in contacts_repo.for_org(conn, org.id)}
    assert wrong.id in {c.id for c in interactions_repo.attendees(conn, meeting.id)}


def test_removing_the_primary_clears_is_primary_and_promotes_nobody(book):
    """`delete()` alone leaves is_primary = 1 on a dead row: the account
    silently has no primary while set_primary's invariant is quietly false.
    Promoting someone else is a judgment the user makes, never a side effect."""
    from bookkit.services import contacts as contacts_svc

    conn, org, keeper, wrong, _meeting = book
    contacts_repo.set_primary(conn, wrong.id)
    assert contacts_repo.get(conn, wrong.id).is_primary

    result = contacts_svc.remove(conn, wrong.id, source="mcp")

    dead = base.raw_row(conn, "contact", wrong.id)
    assert dead is not None
    assert not dead["is_primary"], "is_primary survived on a removed contact"
    # nobody was promoted in their place
    assert [c.id for c in contacts_repo.for_org(conn, org.id) if c.is_primary] == []
    assert result.was_primary is True
    assert "no primary" in result.message.lower()


def test_removal_is_one_batch_and_revert_restores_the_contact(book):
    """One writer action, one undo unit — including the is_primary clear. A
    revert has to put BOTH writes back or the contact returns unprimaried."""
    from bookkit.services import contacts as contacts_svc

    conn, org, _keeper, wrong, meeting = book
    contacts_repo.set_primary(conn, wrong.id)

    result = contacts_svc.remove(conn, wrong.id, source="mcp")

    batch = batches_repo.get_by_ref(conn, result.batch)
    assert batch.source == "mcp"
    assert batch.tool == "contact_remove"
    assert batch.org_id == org.id
    events = batches_repo.events_for(conn, batch.id)
    assert events, "the batch carries no events to revert"
    assert {e.entity_id for e in events} == {wrong.id}, (
        "a write landed outside the batch — `R` cannot reach it"
    )

    outcome = batches_svc.revert(conn, result.batch, now="2026-08-18T09:00:00+00:00")
    assert outcome.applied, outcome.refused

    back = contacts_repo.get(conn, wrong.id)
    assert back.is_primary, "revert put the contact back without their primary flag"
    assert wrong.id in {c.id for c in interactions_repo.attendees(conn, meeting.id)}


def test_the_service_counts_the_interactions_it_did_not_touch(book):
    from bookkit.services import contacts as contacts_svc

    conn, _org, _keeper, wrong, _meeting = book

    result = contacts_svc.remove(conn, wrong.id, source="mcp")

    assert result.interactions == 1
    assert "Pat Wholesale" in result.message
    assert "Acme Freight" in result.message


def test_the_service_refuses_an_unknown_contact_with_a_sentence(book):
    from bookkit.services import contacts as contacts_svc

    conn, *_ = book
    with pytest.raises(ValueError) as err:
        contacts_svc.remove(conn, "no-such-contact", source="mcp")
    assert "no contact" in str(err.value)


def test_the_service_refuses_a_contact_already_removed(book):
    from bookkit.services import contacts as contacts_svc

    conn, _org, _keeper, wrong, _meeting = book
    contacts_svc.remove(conn, wrong.id, source="mcp")
    with pytest.raises(ValueError) as err:
        contacts_svc.remove(conn, wrong.id, source="mcp")
    assert "already removed" in str(err.value)


# --- MCP: the surface that created the bad row ------------------------------


def test_mcp_registers_contact_remove(tmp_path: Path):
    path = tmp_path / "mcp.db"
    db.connect(path).close()
    server = mcpserver.build_server(path)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "contact_remove" in names


def test_mcp_contact_remove_removes_and_returns_the_batch(book):
    conn, org, keeper, wrong, _meeting = book

    out = mcpserver._contact_remove(conn, "Acme Freight", "Pat Wholesale")

    assert out["removed"] is True
    assert out["batch"].startswith("MCP-")
    assert [c.id for c in contacts_repo.for_org(conn, org.id)] == [keeper.id]


def test_mcp_contact_remove_refuses_an_unknown_contact_with_a_sentence(book):
    conn, _org, _keeper, _wrong, _meeting = book
    with pytest.raises(ValueError) as err:
        mcpserver._contact_remove(conn, "Acme Freight", "Nobody Here")
    message = str(err.value)
    assert "Nobody Here" in message
    assert "Rosa Delgado" in message, "a refusal should name what IS on the account"


# --- TUI: D on the contacts tab ---------------------------------------------


async def test_D_on_the_contacts_tab_confirms_then_removes(snapshot_db: Path) -> None:
    app = BookkitApp(snapshot_db)
    org = orgs_repo.list_orgs(app.conn, kind="client")[0]
    doomed = contacts_repo.create(
        app.conn, org.id, first_name="Pat", last_name="Wholesale"
    )
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        table = app.screen.query_one("#contacts-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(doomed.id))
        await pilot.pause()

        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen), "D removed a contact with no confirm"

        await pilot.press("y")
        await pilot.pause()
        assert doomed.id not in {c.id for c in contacts_repo.for_org(app.conn, org.id)}

        await pilot.press("u")
        await pilot.pause()
        assert doomed.id in {c.id for c in contacts_repo.for_org(app.conn, org.id)}


async def test_D_on_the_contacts_tab_can_be_declined(snapshot_db: Path) -> None:
    app = BookkitApp(snapshot_db)
    org = orgs_repo.list_orgs(app.conn, kind="client")[0]
    kept = contacts_repo.create(app.conn, org.id, first_name="Keep", last_name="Me")
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        table = app.screen.query_one("#contacts-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(kept.id))
        await pilot.pause()

        await pilot.press("D")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert kept.id in {c.id for c in contacts_repo.for_org(app.conn, org.id)}


# --- web: the Relationship tab's contact card -------------------------------


@pytest.fixture
def web(snapshot_db: Path):
    app = create_app(snapshot_db)
    org = orgs_repo.list_orgs(app.state.conn, kind="client")[0]
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def test_the_web_confirm_step_writes_nothing(web):
    client, org = web
    conn = client.app.state.conn
    contact = contacts_repo.for_org(conn, org.id)[0]

    response = client.get(f"/accounts/{org.ref}/contacts/{contact.id}/remove")

    assert response.status_code == 200
    assert contact.name in response.text
    assert contact.id in {c.id for c in contacts_repo.for_org(conn, org.id)}


def test_the_confirmed_web_post_removes_in_one_web_batch(web):
    client, org = web
    conn = client.app.state.conn
    contact = contacts_repo.for_org(conn, org.id)[0]

    response = client.post(f"/accounts/{org.ref}/contacts/{contact.id}/remove")

    assert response.status_code == 200
    assert contact.id not in {c.id for c in contacts_repo.for_org(conn, org.id)}

    batch = batches_repo.recent(conn, since="", limit=1)[0]
    assert batch.source == "web"
    assert batch.tool == "contact_remove"
    assert batches_repo.events_for(conn, batch.id), "the batch carries no events"


def test_the_web_refuses_an_unknown_contact_with_a_sentence(web):
    client, org = web
    response = client.post(f"/accounts/{org.ref}/contacts/no-such-id/remove")
    assert response.status_code == 404
    assert "no contact" in response.text


def test_the_contact_card_offers_the_remove_control(web):
    client, org = web
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "/remove" in response.text, "the Relationship tab has no way to remove"


def test_the_undo_promise_is_qualified_for_a_primary(book):
    """The consequence list said "undo puts them back" without qualification,
    and for a primary that over-promises: revert replays only the entities the
    batch touched, so promoting someone else in between leaves the account with
    TWO primaries. Proved here, not assumed — and the sentence now says so."""
    from bookkit.services import contacts as contacts_svc

    conn, org, keeper, wrong, _meeting = book
    contacts_repo.set_primary(conn, wrong.id)

    notes = contacts_svc.consequences(conn, wrong.id)
    assert any("TWO primaries" in note for note in notes), (
        "the undo promise is unqualified on the one path where it over-promises"
    )

    result = contacts_svc.remove(conn, wrong.id, source="tui")
    contacts_repo.set_primary(conn, keeper.id)
    outcome = batches_svc.revert(conn, result.batch, now="2026-08-18T09:00:00+00:00")
    assert outcome.applied, outcome.refused

    primaries = [c.id for c in contacts_repo.for_org(conn, org.id) if c.is_primary]
    assert sorted(primaries) == sorted([keeper.id, wrong.id]), (
        "the sentence warns about two primaries; the code no longer produces them "
        "— re-read the note before deleting this test"
    )


# --- the confirm contract: one list of consequences, both surfaces ----------


def test_the_web_confirm_shows_every_consequence_and_a_way_out(web):
    """services.contacts.consequences is the confirm's whole content. The
    template's note loop and its Cancel button were both deletable with the
    suite still green (fix round 1) — a confirm that shows no plan is an
    hx-confirm with extra steps, and one with no way out is worse."""
    from bookkit.services import contacts as contacts_svc

    client, org = web
    conn = client.app.state.conn
    contact = contacts_repo.for_org(conn, org.id)[0]
    contacts_repo.set_primary(conn, contact.id)
    notes = contacts_svc.consequences(conn, contact.id)
    assert len(notes) >= 2, "the fixture no longer produces a consequence to show"

    html = client.get(f"/accounts/{org.ref}/contacts/{contact.id}/remove").text

    for note in notes:
        assert note in html, f"the web confirm does not show: {note}"
    assert "data-form-cancel" in html and "Cancel" in html, (
        "the confirm has no way out — Cancel is required of every form here"
    )


async def test_the_tui_confirm_shows_the_same_consequences(snapshot_db: Path) -> None:
    """The claim three comments make about each other, asserted once. Both
    surfaces render the SAME strings from the same function, so neither can
    drift into promising something the other does not."""
    from bookkit.services import contacts as contacts_svc

    app = BookkitApp(snapshot_db)
    org = orgs_repo.list_orgs(app.conn, kind="client")[0]
    contact = contacts_repo.for_org(app.conn, org.id)[0]
    contacts_repo.set_primary(app.conn, contact.id)
    notes = contacts_svc.consequences(app.conn, contact.id)
    assert len(notes) >= 2, "the seeded contact no longer produces a consequence"

    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        table = app.screen.query_one("#contacts-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(contact.id))
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()

        assert isinstance(app.screen, ModalScreen), "D removed a contact with no confirm"
        shown = "\n".join(str(w.render()) for w in app.screen.query(Static))
        for note in notes:
            assert note in shown, f"the TUI confirm does not show: {note}"


async def test_D_removes_a_contact_from_the_overview_tab_too(snapshot_db: Path) -> None:
    """ov-contacts is in DELETABLE and had no test: deleting its entry left the
    suite green. A row that answers `D` on one tab and not another is exactly
    the drift the hint lines keep getting caught by."""
    app = BookkitApp(snapshot_db)
    org = orgs_repo.list_orgs(app.conn, kind="client")[0]
    doomed = contacts_repo.create(
        app.conn, org.id, first_name="Overview", last_name="Wholesale"
    )
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        table = app.screen.query_one("#ov-contacts", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(doomed.id))
        await pilot.pause()

        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen), "D removed a contact with no confirm"

        await pilot.press("y")
        await pilot.pause()
        assert doomed.id not in {c.id for c in contacts_repo.for_org(app.conn, org.id)}


def test_a_second_confirm_says_so_where_the_browser_can_see_it(web):
    """A REFUSAL SAYS SOMETHING — and on the web that means in the DOM. htmx
    does not swap 4xx by default and nothing listens for htmx:responseError, so
    the 404 this used to raise for an already-removed contact produced no swap,
    no message and no change at all: a destructive control that looks broken.
    The refusal comes back as the panel htmx is already swapping, carrying the
    service's own sentence (fix round 1)."""
    client, org = web
    conn = client.app.state.conn
    contact = contacts_repo.for_org(conn, org.id)[0]
    action = f"/accounts/{org.ref}/contacts/{contact.id}/remove"
    assert client.post(action).status_code == 200

    second = client.post(action)

    assert second.status_code == 200, "a 4xx here renders as nothing at all"
    assert "already removed" in second.text
    assert 'id="contacts-panel"' in second.text, (
        "the refusal is not in the fragment htmx swaps, so nothing shows it"
    )


def test_a_stale_remove_button_says_so_instead_of_swapping_nothing(web):
    """The SAME defect, on the click that actually comes first.

    The scenario this control is built for — two tabs, or a TUI/MCP removal
    while a card is on screen — hits the confirm GET, not the POST: the user
    clicks Remove on a card whose contact is already gone. That GET answered
    404, htmx does not swap 4xx and nothing listens for htmx:responseError, so
    the page did not move and they never reached the POST fix round 1 fixed
    (fix round 2).

    The response is the refreshed panel and NOTHING else, out of band, with the
    sentence inside it. Both halves are load-bearing and both are asserted
    below: not out of band and the panel nests inside the .form-host it is a
    parent of (contact_create's trap), and anything OUTSIDE the OOB element
    lands in that same .form-host AFTER htmx has already replaced the panel it
    hangs from — detached, invisible, the same nothing again."""
    client, org = web
    conn = client.app.state.conn
    contact = contacts_repo.for_org(conn, org.id)[0]
    action = f"/accounts/{org.ref}/contacts/{contact.id}/remove"
    assert client.post(action).status_code == 200

    stale = client.get(action)

    assert stale.status_code == 200, "a 4xx here renders as nothing at all"
    assert "already removed" in stale.text, "the refusal says nothing"
    body = stale.text.strip()
    assert body.startswith('<div id="contacts-panel"'), (
        "content outside the OOB panel lands in a .form-host the OOB swap has "
        "already detached — htmx swaps out-of-band content first"
    )
    assert "hx-swap-oob" in body, (
        "as a primary swap this nests a second #contacts-panel inside the first"
    )
    assert body.index('class="form-error"') > body.index('id="contacts-panel"')
    assert action not in body, "the refreshed panel still offers the stale card"
