"""Quick capture (n) — the fastest way into the book, and the one that never
recorded who was in the room.

`interaction_contact` has a repo writer, a reader, an alive-filter, a
soft-delete story and a web timeline column. Quick capture, the only path a
user reaches from every screen, never passed `contact_ids`, so the attendee
column was built and could never fill.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from bookkit.repo import batches as batches_repo
from bookkit.repo import contacts as contacts_repo
from bookkit.repo import interactions as interactions_repo
from bookkit.repo import orgs as orgs_repo
from bookkit.tui.app import BookkitApp
from bookkit.tui.widgets.quick_capture import QuickCapture


def _account_with_contacts(conn):
    return next(
        org
        for org in orgs_repo.list_orgs(conn, kind="client")
        if len(contacts_repo.for_org(conn, org.id)) >= 2
    )


def _logged(conn, org, subject: str):
    """The interaction this capture was supposed to write — asserted rather
    than StopIteration'd, so a refused save reads as a refused save."""
    found = [i for i in interactions_repo.for_org(conn, org.id) if i.subject == subject]
    assert found, f"nothing was logged for {subject!r} — the save was refused"
    return found[0]


async def _open(pilot, app: BookkitApp, org, *, who: str, subject: str) -> None:
    await pilot.press("n")
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, QuickCapture)
    screen.query_one("#qc-org", Input).value = org.name
    await pilot.pause()
    screen.query_one("#qc-subject", Input).value = subject
    screen.query_one("#qc-who", Input).value = who
    await pilot.pause()


async def test_quick_capture_records_who_was_in_the_room(snapshot_db: Path) -> None:
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        person = contacts_repo.for_org(conn, org.id)[0]

        await _open(pilot, app, org, who=person.name, subject="Renewal call")
        await pilot.press("ctrl+s")
        await pilot.pause()

        entry = _logged(conn, org, "Renewal call")
        assert [c.id for c in interactions_repo.attendees(conn, entry.id)] == [person.id]


async def test_the_interaction_and_its_attendees_are_one_undo_unit(
    snapshot_db: Path,
) -> None:
    """Two tables in one save. CLAUDE.md: one writer action is one undo unit."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        person = contacts_repo.for_org(conn, org.id)[0]

        await _open(pilot, app, org, who=person.name, subject="Broker meeting")
        await pilot.press("ctrl+s")
        await pilot.pause()

        entry = _logged(conn, org, "Broker meeting")
        batch = batches_repo.most_recent(conn)
        assert batch is not None
        assert batch.source == "tui"
        assert batch.org_id == org.id
        assert entry.id in {e.entity_id for e in batches_repo.events_for(conn, batch.id)}


async def test_a_name_typed_in_the_wrong_case_still_finds_the_person(
    snapshot_db: Path,
) -> None:
    """rapidfuzz WRatio is case SENSITIVE without a processor: a name pasted
    out of an email header in capitals scores 16 against the stored one, and
    a lower-case first name only 73 — both refused for their capitalisation
    alone, on a field whose whole job is to stop a name being dropped."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        person = contacts_repo.create(
            conn, org.id, first_name="Marguerite", last_name="Ashworth"
        )

        await _open(pilot, app, org, who="MARGUERITE ASHWORTH", subject="Lower case")
        await pilot.press("ctrl+s")
        await pilot.pause()

        entry = _logged(conn, org, "Lower case")
        assert [c.id for c in interactions_repo.attendees(conn, entry.id)] == [person.id]


async def test_a_name_that_matches_nobody_refuses_the_save_and_says_so(
    snapshot_db: Path,
) -> None:
    """Silently dropping the name is the bug being fixed; silently dropping
    the WHOLE note would be worse. The modal stays open with the text in it."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        before = len(interactions_repo.for_org(conn, org.id))

        await _open(pilot, app, org, who="Someone Not On This Account", subject="Call")
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert isinstance(app.screen, QuickCapture), "the save was accepted"
        assert app.screen.query_one("#qc-subject", Input).value == "Call"
        assert len(interactions_repo.for_org(conn, org.id)) == before


async def test_a_name_two_people_answer_to_is_refused_rather_than_guessed(
    snapshot_db: Path,
) -> None:
    """Two people sharing a surname is exactly the case that made the search
    list unusable; picking the first match here would put the wrong person in
    the room, in writing, on the client's file."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        contacts_repo.create(conn, org.id, first_name="Sarah", last_name="Chen")
        contacts_repo.create(conn, org.id, first_name="David", last_name="Chen")
        before = len(interactions_repo.for_org(conn, org.id))

        await _open(pilot, app, org, who="Chen", subject="Ambiguous")
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert isinstance(app.screen, QuickCapture), "it guessed a Chen"
        assert len(interactions_repo.for_org(conn, org.id)) == before


async def test_a_removed_contact_cannot_be_put_back_in_the_room(
    snapshot_db: Path,
) -> None:
    """Removal is deliberately non-cascading: the interaction_contact rows
    survive so an undelete restores the attendee list, and attendees() is
    alive-filtered so the person stops appearing on the OLD ones. Naming them
    on a NEW one would write a link that renders nowhere — a row nobody can
    see and nobody asked for."""
    from bookkit.services import contacts as contacts_svc

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        gone = contacts_repo.create(
            conn, org.id, first_name="Marta", last_name="Vellacourt"
        )
        contacts_svc.remove(conn, gone.id, source="tui")
        before = len(interactions_repo.for_org(conn, org.id))

        await _open(pilot, app, org, who="Marta Vellacourt", subject="Ghost")
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert isinstance(app.screen, QuickCapture), "a removed contact was matched"
        assert len(interactions_repo.for_org(conn, org.id)) == before


async def test_capture_with_nobody_named_still_saves(snapshot_db: Path) -> None:
    """The field is optional — a note to self has no attendees."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)

        await _open(pilot, app, org, who="", subject="Note to self")
        await pilot.press("ctrl+s")
        await pilot.pause()

        entry = _logged(conn, org, "Note to self")
        assert interactions_repo.attendees(conn, entry.id) == []
