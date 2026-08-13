from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from textual.coordinate import Coordinate
from textual.widgets import Input, Label, ListView

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


async def _fill(pilot, app, key: str, text: str) -> None:
    widget = app.screen.query_one(f"#form-{key}", Input)
    widget.focus()
    await pilot.pause()
    widget.value = text
    await pilot.pause()


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    path = tmp_path / "empty.db"
    db.connect(path).close()
    return path


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
        assert "expires" in labels and "due in" in labels
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


async def test_navigator_row_keys_require_table_focus(seeded_db: Path) -> None:
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.forms import FormModal

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav._current = ("att", "tasks")
        nav._render_pane()
        await pilot.pause()
        open_before = len(tasks_repo.open_tasks(app.conn))
        assert open_before > 0
        nav.query_one("#nav-tree").focus()
        await pilot.press("d")  # tree focused: must NOT touch the table's row
        await pilot.press("e")
        await pilot.pause()
        assert len(tasks_repo.open_tasks(app.conn)) == open_before
        assert not isinstance(app.screen, FormModal)
        table = nav.query_one("#nav-table", ListTable)
        table.focus()
        await pilot.press("d")  # table focused: acts
        await pilot.pause()
        assert len(tasks_repo.open_tasks(app.conn)) == open_before - 1


async def test_navigator_export_row(
    seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bookkit.repo import placements as placements_repo

    monkeypatch.chdir(tmp_path)
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        org_id = placements_repo.all_linked(app.conn)[0].org_id
        org = orgs.get(app.conn, org_id)
        nav._current = ("account", org_id)
        nav._render_pane()
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        expected = tmp_path / f"{org.ref}-open-items-{date.today().isoformat()}.xlsx"
        assert expected.exists()


async def test_nest_market_under_new_master(seeded_db: Path) -> None:
    from textual.widgets import Input

    from bookkit.tui.screens.markets import MarketsScreen
    from bookkit.tui.widgets.forms import FormModal
    from bookkit.tui.widgets.picker import Picker

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        indian = orgs.create(app.conn, kind="market", name="Indian Harbor Ins Co")
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, MarketsScreen)
        table = app.screen.query_one("#markets-table", ListTable)
        table.move_cursor(row=table.get_row_index(indian.id))
        await pilot.press("N")
        await pilot.pause()
        assert isinstance(app.screen, Picker)
        # option 2 is "create new master…"
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        app.screen.query_one("#form-name", Input).value = "AXA XL"
        await pilot.press("ctrl+s")
        await pilot.pause()
        refreshed = orgs.get(app.conn, indian.id)
        assert refreshed.parent_org_id is not None
        assert orgs.get(app.conn, refreshed.parent_org_id).name == "AXA XL"
        # the outline shows the child indented under its master
        table = app.screen.query_one("#markets-table", ListTable)
        rows = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
        child_row = next(i for i, r in enumerate(rows) if "Indian Harbor" in r)
        assert "└" in rows[child_row]          # indented as a family member
        assert "AXA XL" in rows[child_row - 1]  # directly under its master


async def test_assign_member_to_account_from_team_screen(seeded_db: Path) -> None:
    from bookkit.repo import team as team_repo
    from bookkit.tui.screens.team import TeamScreen
    from bookkit.tui.widgets.forms import FormModal
    from bookkit.tui.widgets.picker import Picker

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        member = team_repo.create_member(app.conn, "Rosa Silva", specialty="property")
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, TeamScreen)
        app.screen.refresh_data()
        await pilot.pause()
        table = app.screen.query_one("#team-table", ListTable)
        table.move_cursor(row=table.get_row_index(member.id))
        table.focus()
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, Picker)
        await pilot.press("enter")  # first client account
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await pilot.press("ctrl+s")  # role/lines optional — assign as-is
        await pilot.pause()
        rows = team_repo.for_member(app.conn, member.id)
        assert len(rows) == 1 and rows[0]["org_name"]


async def test_paste_underwriter_on_market_detail(seeded_db: Path) -> None:
    from bookkit.repo import contacts as contacts_repo
    from bookkit.tui.screens.markets import MarketDetailScreen
    from bookkit.tui.widgets.paste_import import PasteImportModal

    sig = (
        "Ken Ito\nSenior Underwriter\nken.ito@sompo.example.com | (646) 555-0100\n"
    )
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        market = orgs.list_orgs(app.conn, kind="market")[0]
        app.push_screen(MarketDetailScreen(market.id))
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, PasteImportModal)
        app.screen.query_one("#paste-text").text = sig
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert app.screen._staged is not None and app.screen._staged.ok
        await pilot.press("ctrl+s")
        await pilot.pause()
        ken = next(
            c for c in contacts_repo.for_org(app.conn, market.id)
            if c.last_name == "Ito"
        )
        assert ken.role == "underwriter"
        assert ken.email == "ken.ito@sompo.example.com"


async def test_navigator_inline_cell_edit(seeded_db: Path) -> None:
    """i opens a cell editor on the contact row; enter commits through the
    normalizers as one undoable field write; esc abandons without writing."""
    from bookkit.repo import contacts as contacts_repo
    from bookkit.tui.widgets.inline_edit import CellEditor, InlineTable

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        conn = app.conn
        org = orgs.list_orgs(conn, kind="client")[0]
        contact = contacts_repo.for_org(conn, org.id)[0]
        nav._current = ("group", ("contacts", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"contact:{contact.id}"))
        await pilot.pause()

        await pilot.press("i")  # editor opens over the first editable column
        await pilot.pause()
        editor = nav.query_one(CellEditor)
        assert editor.value == (contact.role or "")
        editor.value = "cfo"
        await pilot.press("enter")
        await pilot.pause()
        assert contacts_repo.get(conn, contact.id).role == "cfo"
        assert not nav.query(CellEditor), "editor closes on commit"

        # esc must abandon, never write
        table.focus()
        await pilot.press("i")
        await pilot.pause()
        nav.query_one(CellEditor).value = "never-saved"
        await pilot.press("escape")
        await pilot.pause()
        assert contacts_repo.get(conn, contact.id).role == "cfo"

        # the commit is one event-log entry: u reverts it
        await pilot.press("u")
        await pilot.pause()
        assert contacts_repo.get(conn, contact.id).role == (contact.role or None)


async def _open_account_group(pilot, nav, org_id: str, group: str):
    """Drive the tree the way a user does: expand the account, land on one of
    its group leaves. Returns the (tree, account node) pair."""
    from textual.widgets import Tree

    tree = nav.query_one("#nav-tree", Tree)
    tree.focus()
    await pilot.pause()
    node = next(
        n for n in tree.root.children[1].children if n.data == ("account", org_id)
    )
    tree.cursor_line = node.line
    await pilot.pause()
    node.expand()
    await pilot.pause()
    leaf = next(n for n in node.children if n.data[1][0] == group)
    tree.cursor_line = leaf.line
    await pilot.pause()
    assert nav._current == ("group", (group, org_id))
    return tree, node


async def test_navigator_refresh_keeps_your_place_in_the_tree(seeded_db: Path) -> None:
    """A refresh rebuilds the tree without moving the user. tree.clear() drops
    expansion, which shortens the tree, which makes Textual clamp cursor_line
    onto an unrelated node — the pane then swapped out from under whatever the
    user had open (this is what crashed inline editing)."""
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        org = orgs.list_orgs(app.conn, kind="client")[-1]
        tree, _node = await _open_account_group(pilot, nav, org.id, "tasks")

        nav.refresh_data()
        await pilot.pause()

        assert nav._current == ("group", ("tasks", org.id)), "pane moved on refresh"
        rebuilt = next(
            n for n in tree.root.children[1].children if n.data == ("account", org.id)
        )
        assert rebuilt.is_expanded, "the account collapsed under the user"
        assert tree.cursor_node is not None
        assert tree.cursor_node.data == ("group", ("tasks", org.id))
        assert nav.query_one("#nav-table", InlineTable).inline_fields


async def test_inline_edit_defers_refresh_until_the_editor_closes(
    seeded_db: Path,
) -> None:
    """tab hops across the row without a refresh landing mid-edit — the editor
    is mounted on the SCREEN, so its own focus fires on_descendant_focus and
    used to flush the deferred refresh straight into the open editor."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.inline_edit import CellEditor, InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[-1]
    task = tasks_repo.create(
        app.conn, "call broker", due_on=date.today().isoformat(), org_id=org.id
    )
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        await _open_account_group(pilot, nav, org.id, "tasks")
        table = nav.query_one("#nav-table", InlineTable)

        refreshes: list[bool] = []
        real_refresh = nav.refresh_data
        nav.refresh_data = lambda: (refreshes.append(table.editing), real_refresh())

        table.focus()
        table.move_cursor(row=table.get_row_index(f"task:{task.id}"))
        await pilot.pause()
        await pilot.press("i")  # opens on due (column 0)
        await pilot.pause()
        nav.query_one(CellEditor).value = date.today().isoformat()

        await pilot.press("tab")  # commit due, hop to title (column 1)
        await pilot.pause()
        assert refreshes == [], "a refresh landed while the editor was open"
        assert nav.query_one(CellEditor)._coordinate.column == 1

        nav.query_one(CellEditor).value = "call broker back"
        await pilot.press("enter")  # commit + close — now the refresh may land
        await pilot.pause()
        assert not nav.query(CellEditor)
        assert refreshes and not any(refreshes), "refreshed with an editor open"
        assert tasks_repo.get(app.conn, task.id).title == "call broker back"


async def test_inline_edit_hop_survives_a_table_rebuild(seeded_db: Path) -> None:
    """Belt and braces: if the table is rebuilt under a live editor anyway,
    the hop closes the editor instead of raising ValueError."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.inline_edit import CellEditor, InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[-1]
    task = tasks_repo.create(
        app.conn, "call broker", due_on=date.today().isoformat(), org_id=org.id
    )
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav._current = ("group", ("tasks", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"task:{task.id}"))
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        assert nav.query(CellEditor)

        # the pane swaps to a view with no editable columns at all
        nav._current = ("att", "overdue")
        nav._render_pane()
        await pilot.pause()
        assert not table.inline_fields
        assert not nav.query(CellEditor), "rebuilding the table abandons the editor"

        # and the card panes, which hide the table without ever clearing it
        nav._current = ("group", ("tasks", org.id))
        nav._render_pane()
        await pilot.pause()
        table.focus()
        table.move_cursor(row=table.get_row_index(f"task:{task.id}"))
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        assert nav.query(CellEditor)
        nav._current = ("account", org.id)
        nav._render_pane()
        await pilot.pause()
        assert not table.display
        assert not nav.query(CellEditor), "editor left floating over a hidden table"

        # a hop from a stale coordinate is a no-op, not a crash
        table.inline_fields = {}
        table._hop(Coordinate(0, 1), +1)
        assert not nav.query(CellEditor)


async def test_export_row_reports_a_stale_towerkit(seeded_db: Path) -> None:
    """x must not take the app down when the installed towerkit predates the
    workbook renderer — say so and stay up."""
    from bookkit.services import export_open_items

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        org = orgs.list_orgs(app.conn, kind="client")[0]
        nav._current = ("account", org.id)
        nav._render_pane()
        await pilot.pause()

        def boom(*a, **k):
            raise ModuleNotFoundError("No module named 'towerkit.render.table_xlsx'")

        original, export_open_items.write = export_open_items.write, boom
        try:
            await pilot.press("x")
            await pilot.pause()
        finally:
            export_open_items.write = original
        assert app.is_running
        assert any("towerkit" in str(n.message) for n in app._notifications)


async def test_export_open_items_flow_writes_workbook_and_guards_stale_org(
    seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """entity_actions.export_open_items_flow is the shared home: it writes
    the workbook for a live org, and a soft-deleted org notifies instead of
    raising (Task 9's client tab will call this same flow)."""
    from bookkit.tui.widgets import entity_actions

    monkeypatch.chdir(tmp_path)
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        org = orgs.list_orgs(app.conn, kind="client")[0]

        entity_actions.export_open_items_flow(nav, org.id)
        await pilot.pause()
        expected = tmp_path / f"{org.ref}-open-items-{date.today().isoformat()}.xlsx"
        assert expected.exists()

        orgs.delete(app.conn, org.id)
        entity_actions.export_open_items_flow(nav, org.id)
        await pilot.pause()
        assert app.is_running
        assert any(
            "no longer exists" in str(n.message) for n in app._notifications
        )


async def test_task_tables_show_description_and_detail(seeded_db: Path) -> None:
    """description + detail surface on every task table: attention "tasks
    due", an account's task group, and the account overview tab."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.screens.navigator import NavigatorScreen
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    task = tasks_repo.create(
        app.conn, "call broker", description="brief line", detail="**long** notes",
        due_on=date.today().isoformat(), org_id=org.id,
    )
    async with app.run_test(size=(160, 48)) as pilot:
        assert isinstance(app.screen, NavigatorScreen)
        nav = app.screen

        # attention: "tasks due"
        nav._current = ("att", "tasks")
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        headers = [str(c.label) for c in table.columns.values()]
        assert "description" in headers and "detail" in headers

        # per-account tasks group
        nav._current = ("group", ("tasks", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        headers = [str(c.label) for c in table.columns.values()]
        assert "description" in headers and "detail" in headers

        # account overview tab
        app.open_account(org.id)
        await pilot.pause()
        overview = app.screen.query_one("#ov-tasks", ListTable)
        headers = [str(c.label) for c in overview.columns.values()]
        assert "description" in headers and "detail" in headers
        row = overview.get_row(task.id)
        assert "brief line" in [str(c) for c in row]


async def test_task_tables_group_by_category(seeded_db: Path) -> None:
    """category surfaces on every task table and rows arrive grouped by it
    (display-level: repo ordering stays authoritative for briefs)."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.screens.navigator import NavigatorScreen
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    today = date.today().isoformat()
    task_b = tasks_repo.create(
        app.conn, "call broker", category="B", due_on=today, org_id=org.id,
    )
    task_a1 = tasks_repo.create(
        app.conn, "send COI", category="A", due_on=today, org_id=org.id,
    )
    task_a2 = tasks_repo.create(
        app.conn, "chase quote", category="A", due_on=today, org_id=org.id,
    )
    async with app.run_test(size=(160, 48)) as pilot:
        assert isinstance(app.screen, NavigatorScreen)
        nav = app.screen

        # attention: "tasks due" — the two A-category tasks land adjacent,
        # ahead of the B-category task
        nav._current = ("att", "tasks")
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        headers = [str(c.label) for c in table.columns.values()]
        assert "category" in headers
        idx_a1 = table.get_row_index(f"task:{task_a1.id}")
        idx_a2 = table.get_row_index(f"task:{task_a2.id}")
        idx_b = table.get_row_index(f"task:{task_b.id}")
        assert abs(idx_a1 - idx_a2) == 1
        assert idx_b > max(idx_a1, idx_a2)

        # per-account tasks group
        nav._current = ("group", ("tasks", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        headers = [str(c.label) for c in table.columns.values()]
        assert "category" in headers
        idx_a1 = table.get_row_index(f"task:{task_a1.id}")
        idx_a2 = table.get_row_index(f"task:{task_a2.id}")
        idx_b = table.get_row_index(f"task:{task_b.id}")
        assert abs(idx_a1 - idx_a2) == 1
        assert idx_b > max(idx_a1, idx_a2)

        # account overview tab
        app.open_account(org.id)
        await pilot.pause()
        overview = app.screen.query_one("#ov-tasks", ListTable)
        headers = [str(c.label) for c in overview.columns.values()]
        assert "category" in headers
        idx_a1 = overview.get_row_index(task_a1.id)
        idx_a2 = overview.get_row_index(task_a2.id)
        idx_b = overview.get_row_index(task_b.id)
        assert abs(idx_a1 - idx_a2) == 1
        assert idx_b > max(idx_a1, idx_a2)


async def test_onboarding_screen_lists_steps_with_state(empty_db: Path) -> None:
    from bookkit.tui.screens.onboarding import OnboardingScreen

    app = BookkitApp(empty_db)
    org = orgs.create(app.conn, name="Newco", kind="client")
    async with app.run_test(size=(130, 42)) as pilot:
        app.push_screen(OnboardingScreen(org.id))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        labels = [str(item.query_one(Label).render()) for item in
                  screen.query_one("#onboard-steps", ListView).children]
        assert len(labels) == 5
        assert "account basics" in labels[0]
        # highlight starts on the first incomplete step
        assert screen.query_one("#onboard-steps", ListView).index == 0


async def test_onboarding_enter_opens_step_form_and_save_advances(empty_db: Path) -> None:
    from bookkit.tui.screens.onboarding import OnboardingScreen
    from bookkit.tui.widgets.forms import FormModal

    app = BookkitApp(empty_db)
    org = orgs.create(app.conn, name="Newco", kind="client")
    async with app.run_test(size=(130, 42)) as pilot:
        app.push_screen(OnboardingScreen(org.id))
        await pilot.pause()
        await pilot.press("enter")          # org basics step
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await _fill(pilot, app, "owner", "grant")
        await _fill(pilot, app, "industry", "construction")
        await pilot.press("ctrl+s")
        await pilot.pause()
        # back on the wizard, org step now complete, highlight advanced
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        assert orgs.get(app.conn, org.id).owner == "grant"
        assert screen.query_one("#onboard-steps", ListView).index == 1  # contacts


async def test_onboarding_projects_step_survives_need_form_cancel(
    empty_db: Path,
) -> None:
    """Fill in the project, esc out of the chained need form: the project
    must stay saved, the wizard must reflect that (not sit stuck on
    'untouched'), and re-entering the step must route to adding a need on
    the project that already exists rather than spawning a duplicate."""
    from bookkit.repo import projects as projects_repo
    from bookkit.services import onboarding
    from bookkit.tui.screens.onboarding import OnboardingScreen
    from bookkit.tui.widgets.forms import FormModal

    app = BookkitApp(empty_db)
    org = orgs.create(app.conn, name="Newco", kind="client")
    async with app.run_test(size=(130, 42)) as pilot:
        app.push_screen(OnboardingScreen(org.id))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        steps = screen.query_one("#onboard-steps", ListView)
        steps.index = 3  # "projects & needs"
        await pilot.pause()

        await pilot.press("enter")           # opens the project form
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await _fill(pilot, app, "name", "HQ Tower Build")
        await pilot.press("ctrl+s")
        await pilot.pause()

        # save chains straight into the need form
        assert isinstance(app.screen, FormModal)
        assert app.screen.query_one("#form-line", Input)

        # user escapes the need form without filling it in
        await pilot.press("escape")
        await pilot.pause()

        projs = projects_repo.projects_for_org(app.conn, org.id)
        assert len(projs) == 1  # the project itself was never lost

        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        status = screen._statuses[3]
        assert status.state == onboarding.PARTIAL  # refreshed, not stale

        # re-entering the step routes to adding a need on the SAME
        # project, not a brand-new blank project form
        steps = screen.query_one("#onboard-steps", ListView)
        steps.index = 3
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        assert app.screen.query_one("#form-line", Input)  # need form again
        await pilot.press("escape")
        await pilot.pause()

        projs_after = projects_repo.projects_for_org(app.conn, org.id)
        assert len(projs_after) == 1  # still exactly one project


async def test_navigator_onboarding_attention_and_resume(seeded_db: Path) -> None:
    """A fresh client with no contacts shows up in the onboarding attention
    leaf, and o on the account (from anywhere in the Navigator) resumes the
    wizard for it."""
    from bookkit.tui.screens.navigator import NavigatorScreen
    from bookkit.tui.screens.onboarding import OnboardingScreen

    def _find_leaf(node, data):
        if node.data == data:
            return node
        for child in node.children:
            found = _find_leaf(child, data)
            if found is not None:
                return found
        return None

    app = BookkitApp(seeded_db)
    org = orgs.create(app.conn, name="Fresh Prospect Co", kind="client")
    async with app.run_test(size=(160, 48)) as pilot:
        assert isinstance(app.screen, NavigatorScreen)
        nav = app.screen
        nav.refresh_data()
        await pilot.pause()

        assert len(nav._attention["onboarding"]) == 1
        assert nav._attention["onboarding"][0][0].id == org.id

        tree = nav.query_one("#nav-tree")
        leaf = _find_leaf(tree.root, ("att", "onboarding"))
        assert leaf is not None
        label = str(leaf.label)
        assert "onboarding incomplete" in label
        assert "1" in label

        # o on the selected account starts/resumes onboarding
        nav._current = ("account", org.id)
        await pilot.press("o")
        await pilot.pause()
        assert isinstance(app.screen, OnboardingScreen)
        assert app.screen.org_id == org.id


async def test_navigator_onboarding_row_enter_resumes_wizard(seeded_db: Path) -> None:
    """enter on a row in the onboarding-attention table pushes OnboardingScreen
    for that client, not AccountScreen — the row-resume branch in
    on_data_table_row_selected must special-case the onboarding attention
    list, since every other attention/group table's rows still open the
    account (see test_navigator_home_attention_and_group_tables)."""
    from bookkit.tui.screens.navigator import NavigatorScreen
    from bookkit.tui.screens.onboarding import OnboardingScreen

    app = BookkitApp(seeded_db)
    org = orgs.create(app.conn, name="Row Resume Co", kind="client")
    async with app.run_test(size=(160, 48)) as pilot:
        assert isinstance(app.screen, NavigatorScreen)
        nav = app.screen
        nav.refresh_data()
        await pilot.pause()

        nav._current = ("att", "onboarding")
        nav._render_pane()
        await pilot.pause()

        table = nav.query_one("#nav-table", ListTable)
        assert table.row_count > 0
        table.focus()
        table.move_cursor(row=table.get_row_index(f"org:{org.id}"))
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, OnboardingScreen)
        assert app.screen.org_id == org.id


async def test_open_items_tab_datasheet(seeded_db: Path, tmp_path, monkeypatch) -> None:
    """AccountScreen's Open items tab (8): a task datasheet keyed by client
    (direct or via a placement), and x exports from that tab like anywhere
    else on the screen."""
    from bookkit.repo import placements
    from bookkit.repo import tasks as tasks_repo

    monkeypatch.chdir(tmp_path)
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    p = placements.for_org(app.conn, org.id)[0]
    tasks_repo.create(app.conn, "placement task", placement_id=p.id, category="Renewal")
    async with app.run_test(size=(150, 44)) as pilot:
        app.push_screen(AccountScreen(org.id))
        await pilot.pause()
        await pilot.press("8")
        await pilot.pause()
        table = app.screen.query_one("#open-items-table")
        assert table.has_focus                      # focus lands IN the datasheet
        titles = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]
        assert "placement task" in titles           # placement-owned included
        await pilot.press("x")
        await pilot.pause()
        assert list(tmp_path.glob("*-open-items-*.xlsx"))
