"""Keystrokes that used to kill the whole session.

Three screens built OptionList options keyed on the owning `org_id`, which is
not unique — two rows belonging to one account collide and Textual raises
DuplicateID. All three fire from MESSAGE HANDLERS, which App.run_action's
crash net explicitly does not cover (its own docstring says so), so they
reached Textual's fatal path: screen stack, open forms and the quick-capture
draft all gone.
"""

from __future__ import annotations

from pathlib import Path

from bookkit.repo import interactions, orgs, placements
from bookkit.repo import team as team_repo
from bookkit.tui.app import BookkitApp


def _alive(app: BookkitApp) -> bool:
    return app.is_running and app.screen_stack != []


def _clean(app: BookkitApp) -> bool:
    """No handler raised at all.

    `_alive` alone stopped being sufficient once the dispatch guard landed:
    the guard catches DuplicateID and keeps the app up, so a test asserting
    only "still running" passes even with the duplicate-key bug restored.
    Verified by mutation — putting org_id back in the search option id left
    the _alive assertions green."""
    log = app.crash_log_path()
    return not log.exists() or "DuplicateID" not in log.read_text()


# --- search: two hits at one account ---------------------------------------


async def test_typing_a_two_letter_prefix_does_not_kill_the_app(
    snapshot_db: Path,
) -> None:
    """`/` then "ca" — on the seeded book six of seven two-letter prefixes
    used to raise DuplicateID, including the first two letters of the app's
    own demo client."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "ca":
            await pilot.press(ch)
            await pilot.pause()
        assert _alive(app), "the search keystroke killed the session"
        assert _clean(app), "the handler still raised DuplicateID"


async def test_every_two_letter_prefix_survives(snapshot_db: Path) -> None:
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        for prefix in ("ca", "re", "st", "at", "co", "me", "in"):
            await pilot.press("slash")
            await pilot.pause()
            for ch in prefix:
                await pilot.press(ch)
                await pilot.pause()
            assert _alive(app), f"{prefix!r} killed the session"
            assert _clean(app), f"{prefix!r} still raised DuplicateID"
            await pilot.press("escape")
            await pilot.pause()


async def test_search_still_opens_the_right_account(snapshot_db: Path) -> None:
    """The id carries the entity now, so the org has to be resolved another
    way — this is the assertion that keeps that wiring honest."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "atom":
            await pilot.press(ch)
            await pilot.pause()
        await pilot.press("enter")     # focus results
        await pilot.pause()
        await pilot.press("enter")     # open
        await pilot.pause()
        await pilot.pause()
        assert _alive(app)
        assert type(app.screen).__name__ == "AccountScreen"
        org = orgs.get(app.conn, app.screen.current_org_id)
        assert "Atomic" in org.name


# --- team: one colleague, two assignments at one account -------------------


async def test_enter_on_a_member_with_two_assignments_at_one_account(
    snapshot_db: Path,
) -> None:
    """Exactly the shape repo.team.for_org is written to support: an
    account-level assignment AND a placement-level one on the same client."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        member = team_repo.list_members(conn)[0]
        org = orgs.list_orgs(conn, kind="client")[0]
        plc = placements.for_org(conn, org.id)[0]
        # exactly one scope each — the placement-level one RESOLVES to the
        # same org, which is precisely the collision
        team_repo.assign(conn, member.id, org_id=org.id, role="account_lead")
        team_repo.assign(
            conn, member.id, placement_id=plc.id, role="placement_specialist",
        )

        await pilot.press("w")                 # team screen
        await pilot.pause()
        await pilot.press("enter")             # assignments picker
        await pilot.pause()
        await pilot.pause()
        assert _alive(app), "enter on the member killed the session"
        assert _clean(app), "enter on the member still raised DuplicateID"


# --- the net itself ---------------------------------------------------------


async def test_a_raising_message_handler_no_longer_kills_the_app(
    snapshot_db: Path, monkeypatch
) -> None:
    """The net under the ones nobody thought of. run_action covers actions;
    this covers the handlers, which is where all three crashes lived.

    Raised from search's on_input_changed — a real handler on a real path,
    not a stubbed one, because a stub that never gets dispatched makes this
    test pass for the wrong reason."""
    from bookkit.repo import search as search_repo

    def boom(*args: object, **kwargs: object) -> list:
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(search_repo, "search", boom)

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45), notifications=True) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "ca":
            await pilot.press(ch)
            await pilot.pause()

        assert _alive(app), "a raising message handler killed the session"
        assert app.crash_log_path().exists(), "the failure was swallowed silently"
        assert "handler exploded" in app.crash_log_path().read_text()


async def test_the_search_handler_really_is_the_one_that_raises(
    snapshot_db: Path, monkeypatch
) -> None:
    """Guards the test above: if search_repo.search stopped being called from
    a message handler, the net test would pass without exercising the net."""
    from bookkit.repo import search as search_repo

    calls: list[str] = []

    def spy(conn: object, text: str, limit: int = 20) -> list:
        calls.append(text)
        return []

    monkeypatch.setattr(search_repo, "search", spy)

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "ca":
            await pilot.press(ch)
            await pilot.pause()
    assert calls, "search was never reached from the input handler"
    assert interactions is not None


# --- acting on a row you cannot see ----------------------------------------


async def test_r_does_not_renew_from_the_tab_bar(snapshot_db: Path) -> None:
    """`b enter 4 tab tab r` fired ConfirmRenew with focus on the ContentTabs
    bar. r clones a placement AND its towerkit file, so it acted on a cursor
    the user could not see."""
    from textual.widgets import TabbedContent
    from textual.widgets._tabbed_content import ContentTabs

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45), notifications=True) as pilot:
        await pilot.pause()
        org = orgs.list_orgs(app.conn, kind="client")[0]
        app.open_account(org.id)
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "tab-placements"
        await pilot.pause()
        before = len(placements.for_org(app.conn, org.id))

        # one tab press from the tab's first table lands here — the chrome,
        # where there is no visible row cursor at all
        app.screen.query_one(ContentTabs).focus()
        await pilot.pause()
        assert type(app.focused).__name__ == "ContentTabs"
        await pilot.press("r")
        await pilot.pause()

        assert type(app.screen).__name__ == "AccountScreen", (
            "a confirm opened from the tab bar"
        )
        assert len(placements.for_org(app.conn, org.id)) == before


async def test_reverting_a_program_batch_reports_instead_of_crashing(
    snapshot_db: Path,
) -> None:
    """services/batches refuses a program_* revert with a good message naming
    program_revert_file. It escaped through a dismiss callback and killed the
    app instead of reaching the user."""
    from bookkit.repo import batches as batches_repo
    from bookkit.services import batches as batches_svc

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45), notifications=True) as pilot:
        await pilot.pause()
        batch_id = batches_repo.new_batch_id()
        batches_repo.create(
            app.conn, batch_id=batch_id, source="mcp", tool="program_bind",
            summary="bound a layer", org_id=None,
        )
        ref = batches_repo.get(app.conn, batch_id).ref

        nav = app.screen
        nav._apply_batch_revert(ref, "revert")     # the dismiss callback
        await pilot.pause()

        assert _alive(app), "the refusal killed the session"
        assert batches_svc is not None
