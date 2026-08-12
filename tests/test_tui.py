from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bookkit import db, seed
from bookkit.repo import interactions, orgs
from bookkit.tui.app import BookkitApp
from bookkit.tui.screens.account import AccountScreen
from bookkit.tui.screens.book import BookScreen
from bookkit.tui.screens.calendar import CalendarScreen
from bookkit.tui.screens.markets import MarketDetailScreen, MarketsScreen
from bookkit.tui.screens.pipeline import PipelineScreen
from bookkit.tui.screens.today import TodayScreen
from bookkit.tui.widgets.tables import ListTable

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    path = tmp_path / "tui.db"
    conn = db.connect(path)
    seed.seed(conn, today=date.today(), programs_dir=tmp_path / "programs")
    from bookkit import sync

    sync.project_all(conn, [tmp_path / "programs"])
    conn.close()
    return path


def snapshot(app: BookkitApp, name: str) -> None:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    (SNAPSHOT_DIR / f"{name}.svg").write_text(app.export_screenshot())


async def test_today_screen_populates(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("t")  # navigator is home; Today lives behind t
        await pilot.pause()
        assert isinstance(app.screen, TodayScreen)
        assert app.screen.query_one("#renewals-table", ListTable).row_count > 0
        assert app.screen.query_one("#tasks-table", ListTable).row_count > 0
        assert app.screen.query_one("#stale-table", ListTable).row_count > 0
        assert app.screen.query_one("#sla-table", ListTable).row_count > 0
        snapshot(app, "today")
        await pilot.press("escape")


async def test_book_and_account(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("b")
        assert isinstance(app.screen, BookScreen)
        table = app.screen.query_one("#book-table", ListTable)
        assert table.row_count == 20
        snapshot(app, "book")
        await pilot.press("enter")
        assert isinstance(app.screen, AccountScreen)
        header = str(app.screen.query_one("#account-header").render())
        assert "ACC-" in header
        snapshot(app, "account")
        await pilot.press("escape")
        assert isinstance(app.screen, BookScreen)


async def test_account_placements_tower(seeded_db: Path) -> None:
    conn = db.connect(seeded_db)
    org = orgs.find_by_name(conn, "Atomic Industries, Inc.")
    conn.close()
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AccountScreen)
        state = str(screen.query_one("#sync-state").render())
        assert "in sync" in state
        carriers = screen.query_one("#carriers-table", ListTable)
        assert carriers.row_count > 0
        snapshot(app, "account_placements")


async def test_quick_capture_end_to_end(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        before = None
        conn = app.conn
        org = orgs.find_by_name(conn, "Everline Software")
        before = len(interactions.for_org(conn, org.id))
        await pilot.press("n")
        await pilot.pause()
        # pick the account by typing, then jump to subject and type the note
        await pilot.click("#qc-org")
        for ch in "Everline":
            await pilot.press(ch)
        await pilot.pause()
        await pilot.click("#qc-subject")
        for ch in "Quick call":
            await pilot.press(ch if ch != " " else "space")
        snapshot(app, "quick_capture")
        await pilot.press("ctrl+s")
        await pilot.pause()
        after = interactions.for_org(conn, org.id)
        assert len(after) == before + 1
        assert after[0].subject == "Quick call"


async def test_quick_capture_draft_survives_escape(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await pilot.pause()
        from textual.widgets import Input as _Input

        app.screen.query_one("#qc-subject", _Input).focus()
        await pilot.pause()
        for ch in "Draft":
            await pilot.press(ch)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("n")  # reopen: the draft must come back
        await pilot.pause()
        from textual.widgets import Input

        assert app.screen.query_one("#qc-subject", Input).value == "Draft"


async def test_search_modal(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        for ch in "atomic":
            await pilot.press(ch)
        await pilot.pause()
        from textual.widgets import OptionList

        results = app.screen.query_one("#search-results", OptionList)
        assert results.option_count > 0
        snapshot(app, "search")


async def test_calendar_pipeline_markets(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("c")
        assert isinstance(app.screen, CalendarScreen)
        assert app.screen.query_one("#calendar-table", ListTable).row_count > 0
        snapshot(app, "calendar")
        await pilot.press("escape")

        await pilot.press("p")
        assert isinstance(app.screen, PipelineScreen)
        snapshot(app, "pipeline")
        await pilot.press("escape")

        await pilot.press("m")
        assert isinstance(app.screen, MarketsScreen)
        table = app.screen.query_one("#markets-table", ListTable)
        assert table.row_count == 15
        snapshot(app, "markets")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, MarketDetailScreen)
        snapshot(app, "market_detail")


async def test_help_screen(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        from bookkit.tui.screens.help import HelpScreen

        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        from bookkit.tui.screens.navigator import NavigatorScreen

        assert isinstance(app.screen, NavigatorScreen)


async def test_l_edits_layer_under_cursor_and_single_layer_skips_picker(
    seeded_db: Path,
) -> None:
    from textual.widgets import TabbedContent

    from bookkit.repo import placements as placements_repo
    from bookkit.tui.widgets.forms import FormModal

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        linked = placements_repo.all_linked(app.conn)[0]
        app.open_account(linked.org_id)
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "tab-placements"
        await pilot.pause()
        table = app.screen.query_one("#placements-table", ListTable)
        table.move_cursor(row=table.get_row_index(linked.id))
        app.screen.show_placement(linked.id)
        await pilot.pause()
        carriers = app.screen.query_one("#carriers-table", ListTable)
        assert carriers.row_count > 0
        carriers.focus()
        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)  # no picker in between
        assert app.screen.spec.title.startswith("edit layer")


async def test_placements_table_shows_expiry_sorted_soonest_first(
    seeded_db: Path,
) -> None:
    from textual.widgets import TabbedContent

    from bookkit.repo import placements as placements_repo

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        linked = placements_repo.all_linked(app.conn)[0]
        app.open_account(linked.org_id)
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "tab-placements"
        await pilot.pause()
        table = app.screen.query_one("#placements-table", ListTable)
        labels = [str(col.label) for col in table.columns.values()]
        assert "expires" in labels and "d" in labels
        from bookkit.dates import days_until

        day_counts = [
            days_until(placements_repo.get(app.conn, str(key.value)).period_to)
            for key in table.rows
        ]
        live = [d for d in day_counts if d >= 0]
        assert live == sorted(live)  # soonest upcoming expiry on top
        if any(d < 0 for d in day_counts):  # expired sink below the live block
            first_expired = next(i for i, d in enumerate(day_counts) if d < 0)
            assert all(d < 0 for d in day_counts[first_expired:])


async def test_deal_team_shows_under_placement(seeded_db: Path) -> None:
    from textual.widgets import Static, TabbedContent

    from bookkit.repo import placements as placements_repo
    from bookkit.repo import team as team_repo

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        linked = placements_repo.all_linked(app.conn)[0]
        member = team_repo.create_member(app.conn, "Rosa Silva", specialty="property")
        team_repo.assign(
            app.conn, member.id, placement_id=linked.id, role="placement_specialist"
        )
        app.open_account(linked.org_id)
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "tab-placements"
        await pilot.pause()
        app.screen.show_placement(linked.id)
        await pilot.pause()
        state = str(app.screen.query_one("#sync-state", Static).render())
        assert "Rosa Silva" in state and "placement_specialist" in state


async def test_ctrl_t_task_from_anywhere_attaches_current_client(
    seeded_db: Path,
) -> None:
    from textual.widgets import Input, Select

    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.forms import FormModal

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        org = orgs.list_orgs(app.conn, kind="client")[0]
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        assert app.screen.query_one("#form-org_id", Select).value == org.id
        app.screen.query_one("#form-title", Input).value = "chase COI"
        await pilot.press("ctrl+s")
        await pilot.pause()
        task = next(
            t for t in tasks_repo.open_tasks(app.conn) if t.title == "chase COI"
        )
        assert task.org_id == org.id


async def test_projects_tab_add_edit_and_need_to_opportunity(seeded_db: Path) -> None:
    from textual.widgets import TabbedContent

    from bookkit.repo import opportunities as opps_repo
    from bookkit.repo import projects as projects_repo
    from bookkit.tui.screens.today import TodayScreen

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        org = orgs.list_orgs(app.conn, kind="client")[0]
        project = projects_repo.create_project(
            app.conn, org.id, "HQ Tower Build", status="active"
        )
        need = projects_repo.add_need(
            app.conn, project.id, "Builder's Risk", "2026-09-01",
            premium_indication_cents=25_000_000,
        )
        app.open_account(org.id)
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "tab-projects"
        await pilot.pause()
        app.screen.refresh_data()
        await pilot.pause()
        projects_table = app.screen.query_one("#projects-table", ListTable)
        assert projects_table.row_count == 1
        needs_table = app.screen.query_one("#needs-table", ListTable)
        assert needs_table.row_count == 1

        # o on the need creates the linked, pre-filled opportunity
        needs_table.focus()
        await pilot.press("o")
        await pilot.pause()
        refreshed = projects_repo.get_need(app.conn, need.id)
        assert refreshed.opportunity_id is not None
        opp = opps_repo.get(app.conn, refreshed.opportunity_id)
        assert opp.title == "HQ Tower Build — Builder's Risk"
        assert opp.target_effective == "2026-09-01"
        assert opp.lines == "Builder's Risk"
        assert opp.target_premium == 25_000_000

        # the need shows on Today as attention
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("t")  # navigator is home now
        await pilot.pause()
        assert isinstance(app.screen, TodayScreen)
        app.screen.refresh_data()
        await pilot.pause()
        renewals_table = app.screen.query_one("#renewals-table", ListTable)
        keys = [str(key.value) for key in renewals_table.rows]
        assert any(k.startswith(f"need:{need.id}") for k in keys)


async def test_navigator_home_attention_and_group_tables(seeded_db: Path) -> None:
    from bookkit.repo import placements as placements_repo
    from bookkit.tui.screens.navigator import NavigatorScreen
    from bookkit.tui.widgets.forms import FormModal

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        assert isinstance(app.screen, NavigatorScreen)
        nav = app.screen
        table = nav.query_one("#nav-table", ListTable)

        # attention: the renewals group renders a working table
        nav._current = ("att", "renewals")
        nav._render_pane()
        await pilot.pause()
        assert table.display and table.row_count > 0

        # an account's placements group: scoped table with expiry columns
        linked = placements_repo.all_linked(app.conn)[0]
        nav._current = ("group", ("placements", linked.org_id))
        nav._render_pane()
        await pilot.pause()
        labels = [str(col.label) for col in table.columns.values()]
        assert "expires" in labels and "d" in labels
        assert table.row_count > 0

        # e on a placement row opens the commit-in-place form
        table.focus()
        table.move_cursor(row=table.get_row_index(f"placement:{linked.id}"))
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await pilot.press("escape")
        await pilot.pause()

        # enter on the row opens the full account screen
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, AccountScreen)
        snapshot(app, "navigator")
