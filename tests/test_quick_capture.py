"""Quick capture (n) — the fastest way into the book, and the one that never
recorded who was in the room.

`interaction_contact` has a repo writer, a reader, an alive-filter, a
soft-delete story and a web timeline column. Quick capture, the only path a
user reaches from every screen, never passed `contact_ids`, so the attendee
column was built and could never fill.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input, Static

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


# --- the near tie, which is the case that actually happens -------------------


def _account_with_roster(conn, name: str, roster: list[tuple[str, str]]):
    """An account whose contact list is exactly what the test says it is —
    the seeded accounts carry their own people, and a margin test measured
    against a roster it did not choose measures nothing."""
    org = orgs_repo.create(conn, kind="client", name=name, status="active")
    for first, last in roster:
        contacts_repo.create(conn, org.id, first_name=first, last_name=last)
    return org


async def _refused(pilot, app: BookkitApp, org, *, who: str, subject: str) -> bool:
    before = len(interactions_repo.for_org(app.conn, org.id))
    await _open(pilot, app, org, who=who, subject=subject)
    await pilot.press("ctrl+s")
    await pilot.pause()
    still_open = isinstance(app.screen, QuickCapture)
    unwritten = len(interactions_repo.for_org(app.conn, org.id)) == before
    return still_open and unwritten


async def test_a_name_that_two_people_NEARLY_answer_to_is_refused_too(
    snapshot_db: Path,
) -> None:
    """The exact tie is the rare case. The common one is two similar names on
    one account, where the winner leads by two or three points — and an
    equality test (`best[0][0] == best[1][0]`) does not fire on it at all, so
    the wrong person went onto the client's file in writing, silently.

    Every pair below was measured picking the WRONG person before the margin:
    Jon 87.5 / Jonathan 85.5, Michael 96.6 / Michelle 93.3, Rosa Delgado 90.0
    / Robert Delgado-Vance 84.2.
    """
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn

        smiths = _account_with_roster(
            conn, "Zephyrine Cold Storage", [("Jon", "Smith"), ("Jonathan", "Smith")]
        )
        assert await _refused(pilot, app, smiths, who="J Smith", subject="Jay"), (
            "'J Smith' resolved to one of Jon/Jonathan Smith on a 2-point lead"
        )
        await pilot.press("escape")
        await pilot.pause()

        brennans = _account_with_roster(
            conn, "Quillfeather Aggregates",
            [("Michael", "Brennan"), ("Michelle", "Brennan")],
        )
        assert await _refused(
            pilot, app, brennans, who="Michel Brennan", subject="Mich"
        ), "'Michel Brennan' resolved to Michael or Michelle on a 3-point lead"
        await pilot.press("escape")
        await pilot.pause()

        delgados = _account_with_roster(
            conn, "Umbral Freight Systems",
            [("Rosa", "Delgado"), ("Robert", "Delgado-Vance")],
        )
        assert await _refused(
            pilot, app, delgados, who="Rosa Delgado-Vance", subject="Del"
        ), "'Rosa Delgado-Vance' resolved to Rosa Delgado on a 6-point lead"


async def test_the_margin_still_lets_a_name_typed_in_full_through(
    snapshot_db: Path,
) -> None:
    """The other half of the margin, and the reason it is 8 and not 15: a
    refusal the user cannot clear is as useless as a wrong guess. Michael and
    Michelle Brennan are the closest pair of genuinely different names
    measured, and typing either IN FULL leads by 9.7 — so the full name has to
    resolve, on the same roster the test above refuses a fragment of."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_roster(
            conn, "Quillfeather Aggregates",
            [("Michael", "Brennan"), ("Michelle", "Brennan")],
        )
        michelle = next(
            c for c in contacts_repo.for_org(conn, org.id) if c.first_name == "Michelle"
        )

        await _open(pilot, app, org, who="Michelle Brennan", subject="Named in full")
        await pilot.press("ctrl+s")
        await pilot.pause()

        entry = _logged(conn, org, "Named in full")
        assert [c.id for c in interactions_repo.attendees(conn, entry.id)] == [
            michelle.id
        ]


async def test_a_contact_who_has_left_cannot_be_put_in_the_room(
    snapshot_db: Path,
) -> None:
    """`for_org` filters twice and the two filters answer different questions.
    base.alive() drops the REMOVED contact and it runs unconditionally, so the
    removed-contact test above passes with or without `active_only`. What
    active_only=True genuinely gates is the person who left the client and was
    never removed — active = 0, still on the file because their history is,
    and not somebody who can have been in a meeting this morning."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        departed = contacts_repo.create(
            conn, org.id, first_name="Halvard", last_name="Ochterlony"
        )
        contacts_repo.update(conn, departed.id, active=0)
        # still on the account's file — this is not a removal
        assert any(
            c.id == departed.id
            for c in contacts_repo.for_org(conn, org.id, active_only=False)
        )
        before = len(interactions_repo.for_org(conn, org.id))

        await _open(pilot, app, org, who="Halvard Ochterlony", subject="Departed")
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert isinstance(app.screen, QuickCapture), "a departed contact was matched"
        assert len(interactions_repo.for_org(conn, org.id)) == before
        # and the roster does not offer them either
        assert "Halvard" not in str(app.screen.query_one("#qc-who-known", Static).render())


async def test_a_date_that_will_not_parse_refuses_the_save_and_says_so(
    snapshot_db: Path,
) -> None:
    """Ambiguous entry is refused, never guessed. The old fallback stamped
    today over whatever was typed — a follow-up entered as "the 5th" once
    saved as 2027-05-01 and fell off every attention window silently. The
    modal stays open with the typed date still in it."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        before = len(interactions_repo.for_org(conn, org.id))

        await _open(pilot, app, org, who="", subject="Call")
        app.screen.query_one("#qc-date", Input).value = "5"
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert isinstance(app.screen, QuickCapture), "the save was accepted"
        assert app.screen.query_one("#qc-date", Input).value == "5"
        assert len(interactions_repo.for_org(conn, org.id)) == before


async def test_the_accepted_follow_up_task_is_one_revertible_batch(
    snapshot_db: Path,
) -> None:
    """ConfirmTask used to write the task UNBATCHED — the one capture write
    `u` could not reach. Same batch shape as the web now: tool="task_add"."""
    from textual.widgets import TextArea

    from bookkit.repo import tasks as tasks_repo
    from bookkit.services import batches as batches_svc
    from bookkit.tui.widgets.quick_capture import ConfirmTask

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        org = _account_with_contacts(conn)
        before = {t.id for t in tasks_repo.open_tasks_for_client(conn, org.id)}

        await _open(pilot, app, org, who="", subject="Renewal kickoff")
        app.screen.query_one("#qc-note", TextArea).text = (
            "Follow up Tuesday about loss runs."
        )
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmTask), "no follow-up was offered"
        await pilot.press("y")
        await pilot.pause()

        created = [
            t for t in tasks_repo.open_tasks_for_client(conn, org.id) if t.id not in before
        ]
        assert len(created) == 1
        batch = batches_repo.most_recent(conn)
        assert batch is not None
        assert batch.source == "tui" and batch.tool == "task_add"
        assert batch.org_id == org.id
        # and `u` can now actually take it back
        from bookkit import db

        batches_svc.revert(conn, batch.ref, now=db.utc_now())
        assert created[0].id not in {
            t.id for t in tasks_repo.open_tasks_for_client(conn, org.id)
        }
