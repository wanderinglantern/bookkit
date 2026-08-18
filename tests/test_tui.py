from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListView, TextArea

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
        await pilot.press("escape")


async def test_book_and_account(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("b")
        assert isinstance(app.screen, BookScreen)
        table = app.screen.query_one("#book-table", ListTable)
        assert table.row_count == 20
        await pilot.press("enter")
        assert isinstance(app.screen, AccountScreen)
        header = str(app.screen.query_one("#account-header").render())
        assert "ACC-" in header
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


async def test_calendar_pipeline_markets(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("c")
        assert isinstance(app.screen, CalendarScreen)
        assert app.screen.query_one("#calendar-table", ListTable).row_count > 0
        await pilot.press("escape")

        await pilot.press("p")
        assert isinstance(app.screen, PipelineScreen)
        await pilot.press("escape")

        await pilot.press("m")
        assert isinstance(app.screen, MarketsScreen)
        table = app.screen.query_one("#markets-table", ListTable)
        assert table.row_count == 15
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, MarketDetailScreen)


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


async def test_overview_tasks_wrap_long_detail(seeded_db: Path) -> None:
    """The overview task table wraps the long notes down the row instead of
    clipping them: the detail column is fixed-width, the row auto-heights, and
    the whole note is in the cell. The one-line task tables keep clipping."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.screens.navigator import NavigatorScreen
    from bookkit.tui.widgets.inline_edit import InlineTable

    long_detail = (
        "Carrier wants the signed TRIA rejection plus 2021-2025 loss runs "
        "with open reserves before they will release terms."
    )
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    task = tasks_repo.create(
        app.conn, "chase terms", detail=long_detail,
        due_on=date.today().isoformat(), org_id=org.id,
    )
    async with app.run_test(size=(160, 48)) as pilot:
        assert isinstance(app.screen, NavigatorScreen)
        app.open_account(org.id)
        await pilot.pause()
        overview = app.screen.query_one("#ov-tasks", ListTable)

        # the whole note reaches the cell — nothing clipped with an ellipsis
        detail = str(overview.get_row(task.id)[5])
        assert detail == long_detail

        # and the row grew to fit it rather than cutting it off at one line
        row_key = next(
            key for key in overview.rows if key.value == task.id
        )
        assert overview.rows[row_key].height > 1

        # Y still yanks one flat line — a wrapped cell must not paste as
        # several spreadsheet rows
        overview.focus()
        overview.move_cursor(row=overview.get_row_index(task.id))
        await pilot.pause()
        await pilot.press("Y")
        assert "\n" not in app._clipboard
        assert "open reserves before they will release terms." in app._clipboard

        # the editable task tables are unchanged: still one line, still clipped
        app.pop_screen()
        await pilot.pause()
        nav = app.screen
        assert isinstance(nav, NavigatorScreen)
        nav._current = ("group", ("tasks", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        assert all(row.height == 1 for row in table.rows.values())
        assert str(table.get_row(f"task:{task.id}")[4]).endswith("…")


async def test_overview_tasks_stand_down_when_narrow(seeded_db: Path) -> None:
    """At 80 columns there is no room to wrap into: a detail column a few
    characters wide would burn three rows per task to say nothing, so the
    table falls back to one clipped line — the call the placements tab makes
    at PREVIEW_MIN_WIDTH."""
    from bookkit.repo import tasks as tasks_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    task = tasks_repo.create(
        app.conn, "chase terms",
        detail="Carrier wants the signed TRIA rejection plus 2021-2025 loss "
               "runs with open reserves before they will release terms.",
        due_on=date.today().isoformat(), org_id=org.id,
    )
    async with app.run_test(size=(80, 24)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        overview = app.screen.query_one("#ov-tasks", ListTable)
        row_key = next(key for key in overview.rows if key.value == task.id)
        assert overview.rows[row_key].height == 1
        assert str(overview.get_row(task.id)[5]).endswith("…")


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


async def test_open_items_tab_inline_cell_edit_regroups(seeded_db: Path) -> None:
    """i on the open-items datasheet edits a task in place, same as the
    navigator table: enter commits through the field parsers, and the next
    refresh regroups the row under its new category."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.inline_edit import CellEditor, InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    # two categorized tasks, ordered so the edit below swaps which one leads
    anchor = tasks_repo.create(app.conn, "anchor task", org_id=org.id, category="Mango")
    task = tasks_repo.create(app.conn, "edited task", org_id=org.id, category="Zulu")
    async with app.run_test(size=(150, 44)) as pilot:
        app.push_screen(AccountScreen(org.id))
        await pilot.pause()
        await pilot.press("8")
        await pilot.pause()
        table = app.screen.query_one("#open-items-table", InlineTable)
        assert table.has_focus
        # before the edit, Mango sorts ahead of Zulu
        assert table.get_row_index(anchor.id) < table.get_row_index(task.id)
        table.move_cursor(row=table.get_row_index(task.id))
        await pilot.pause()

        await pilot.press("i")  # editor opens over the first editable column (due)
        await pilot.pause()
        await pilot.press("tab")  # due -> title, unchanged
        await pilot.pause()
        await pilot.press("tab")  # title -> category, unchanged
        await pilot.pause()
        editor = app.screen.query_one(CellEditor)
        assert editor.value == "Zulu"
        editor.value = "Apple"
        await pilot.press("enter")
        await pilot.pause()

        assert tasks_repo.get(app.conn, task.id).category == "Apple"
        assert not app.screen.query(CellEditor), "editor closes on commit"

        # a refresh regroups the row under its new category — Apple now
        # leads Mango, reversing the pair's on-screen order
        table = app.screen.query_one("#open-items-table", InlineTable)
        assert table.get_row_index(task.id) < table.get_row_index(anchor.id)
        assert str(table.get_row_at(table.get_row_index(task.id))[2]) == "Apple"


async def test_inline_category_cell_completes_from_the_vocabulary(seeded_db: Path) -> None:
    """`i` on the Open Items tab is the PRIMARY edit path — the add/edit modal
    is the secondary one — so the vocabulary that makes "Internal"
    discoverable has to be in the CELL. Both halves CLAUDE.md names: the
    textual-autocomplete dropdown and the SuggestFromList ghost text.

    Enter and tab still commit and hop (prevent_default_* are off on this
    dropdown), which the regrouping test above exercises end to end on this
    very cell."""
    from textual_autocomplete import AutoComplete

    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.inline_edit import CellEditor, InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    task = tasks_repo.create(app.conn, "audit support", org_id=org.id, category="Renewal")
    async with app.run_test(size=(150, 44)) as pilot:
        app.push_screen(AccountScreen(org.id))
        await pilot.pause()
        await pilot.press("8")
        await pilot.pause()
        table = app.screen.query_one("#open-items-table", InlineTable)
        table.move_cursor(row=table.get_row_index(task.id))
        await pilot.pause()

        # title has no vocabulary: no ghost text, no dropdown
        await pilot.press("i")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen.query_one(CellEditor).suggester is None
        assert not app.screen.query(AutoComplete)

        await pilot.press("tab")  # title -> category
        await pilot.pause()
        editor = app.screen.query_one(CellEditor)
        assert editor.value == "Renewal"
        assert editor.suggester is not None, "no ghost text on a vocabulary cell"
        dropdowns = app.screen.query(AutoComplete)
        assert len(dropdowns) == 1
        # type toward it and the dropdown offers the category nobody in this
        # book has typed yet — repo.vocab.task_categories always carries it
        editor.value = "Inter"
        await pilot.pause()
        options = dropdowns.first().option_list
        shown = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
        assert shown == ["Internal"], shown

        await pilot.press("escape")
        await pilot.pause()
        assert not app.screen.query(AutoComplete), (
            "the dropdown outlived the cell it completes")


def test_category_text_marks_internal_and_leaves_everything_else_alone() -> None:
    """The glyph, the words and the colour are all load-bearing and all were
    unpinned: dropping the ⊘, or rendering the marked row AMBER like an
    ordinary one, changed nothing any test could see. The last assertion is
    the other half — a refactor that adds a marker must not restyle the rows
    that do not carry it."""
    from bookkit.tui import theme

    internal = theme.category_text("Internal")
    assert internal.plain == "Internal ⊘ not exported"
    assert internal.style == theme.DIM

    # the value the user typed survives verbatim — the marker is a suffix
    assert theme.category_text("internal").plain == "internal ⊘ not exported"

    plain = theme.category_text("Renewal")
    assert plain.plain == "Renewal"
    assert plain.style == theme.AMBER
    # a near miss is an ORDINARY category (models.is_internal_category) and
    # must read as one — this is the shape withheld_note exists to announce
    assert theme.category_text("Internal Review").plain == "Internal Review"
    assert theme.category_text("Internal Review").style == theme.AMBER
    assert theme.category_text(None).plain == "—"


@pytest.mark.parametrize(
    "total, expected",
    [
        # lead = 10 (due) + 6 (due in) + 1 (title "t") + 23 (the RENDERED
        # category, "Internal ⊘ not exported") + 1 (description "—")
        # + 12 (padding) + 2 (margin) = 55. spare = total - 55, floored at
        # OV_DETAIL_MIN=40 and capped at OV_DETAIL_MAX=60.
        (94, None),   # spare 39 — below the floor, stand down
        (95, 40),     # spare 40 — exactly the floor
        (100, 45),
        (110, 55),
        (120, 60),    # spare 65, capped. The ONLY case the cap hides the
        # measurement in — the four above are what make this test load-bearing
    ],
)
def test_overview_detail_width_measures_the_rendered_category(
    seeded_db: Path, total: int, expected: int | None
) -> None:
    """The overview's five auto-width columns are measured off the data and
    taken off the pane width first; the detail column wraps into what is left.
    An Internal task's category cell carries a badge, so measuring the RAW
    category under-counts the row by the badge's width (23 - 8 = 15 columns)
    and the detail column overflows. Nothing else in the suite notices — the
    tables still render, a few characters too wide.

    Absolute, not relative: comparing an Internal row against a "Renewal" row
    only catches the mutation where BOTH land under the cap, which is one
    narrow band of widths and not the band that matters."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.screens.account import _ov_detail_width

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    internal = tasks_repo.create(
        app.conn, "t", org_id=org.id, category="Internal", detail="note")
    assert _ov_detail_width([internal], total) == expected


async def test_open_items_marks_internal_tasks(seeded_db: Path) -> None:
    """An Internal task reads as export-excluded ON THE ROW. The category is
    what decides whether a task leaves the building, so it says so where the
    category is rendered — not only in a legend somewhere else."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    internal = tasks_repo.create(
        app.conn, "our own file note", org_id=org.id, category="Internal")
    normal = tasks_repo.create(app.conn, "renew GL", org_id=org.id, category="Renewal")
    async with app.run_test(size=(150, 44)) as pilot:
        app.push_screen(AccountScreen(org.id))
        await pilot.pause()
        await pilot.press("8")
        await pilot.pause()
        table = app.screen.query_one("#open-items-table", InlineTable)

        def cell(t) -> str:
            return str(table.get_row_at(table.get_row_index(t.id))[2])

        assert "not exported" in cell(internal)
        assert "Internal" in cell(internal)
        assert cell(normal) == "Renewal"


async def test_account_overview_marks_internal_tasks(seeded_db: Path) -> None:
    """The account's DEFAULT tab, whose task table is a different call site
    from the Open Items tab's (account.py's #ov-tasks fill). Reverting it to
    the old inline expression passed every other test in this file."""
    from bookkit.repo import tasks as tasks_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    internal = tasks_repo.create(
        app.conn, "our own file note", org_id=org.id, category="Internal")
    normal = tasks_repo.create(app.conn, "renew GL", org_id=org.id, category="Renewal")
    async with app.run_test(size=(150, 44)) as pilot:
        app.push_screen(AccountScreen(org.id))
        await pilot.pause()
        table = app.screen.query_one("#ov-tasks", ListTable)

        def cell(t) -> str:
            return str(table.get_row_at(table.get_row_index(t.id))[3])

        assert cell(internal) == "Internal ⊘ not exported"
        assert cell(normal) == "Renewal"


async def test_navigator_attention_feed_marks_internal_tasks(seeded_db: Path) -> None:
    """The attention feed — the screen CLAUDE.md calls home — is a SEPARATE
    fill from the per-account group below it (_fill_attention_table vs
    _fill_group_table). Both key their rows "task:<id>", so a test that sets
    _current to ("group", …) looks like it covers this and does not."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    internal = tasks_repo.create(
        app.conn, "our own file note", org_id=org.id, category="Internal",
        due_on=date.today().isoformat(),
    )
    normal = tasks_repo.create(
        app.conn, "renew GL", org_id=org.id, category="Renewal",
        due_on=date.today().isoformat(),
    )
    async with app.run_test(size=(150, 44)) as pilot:
        nav = app.screen
        nav.refresh_data()
        nav._current = ("att", "tasks")
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)

        def cell(t) -> str:
            return str(table.get_row_at(table.get_row_index(f"task:{t.id}"))[2])

        assert cell(internal) == "Internal ⊘ not exported"
        assert cell(normal) == "Renewal"


async def test_navigator_account_group_marks_internal_tasks(seeded_db: Path) -> None:
    """The per-account tasks group under an account node — the fourth call
    site, and the one the old test actually exercised."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    internal = tasks_repo.create(
        app.conn, "our own file note", org_id=org.id, category="Internal",
        due_on=date.today().isoformat(),
    )
    async with app.run_test(size=(150, 44)) as pilot:
        nav = app.screen
        nav._current = ("group", ("tasks", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        row = table.get_row_at(table.get_row_index(f"task:{internal.id}"))
        assert str(row[2]) == "Internal ⊘ not exported"


async def test_export_says_how_many_internal_tasks_were_withheld(
    seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The signal that catches a mistyped category at the moment it matters:
    you filed it as "Internal Review", you export, and the toast tells you
    nothing was withheld."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets import entity_actions

    monkeypatch.chdir(tmp_path)
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    tasks_repo.create(app.conn, "our own file note", org_id=org.id, category="Internal")
    tasks_repo.create(app.conn, "our own reserve note", org_id=org.id, category="internal")
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        entity_actions.export_open_items_flow(nav, org.id)
        await pilot.pause()
        messages = [str(n.message) for n in app._notifications]
        assert any("2 internal tasks withheld" in m for m in messages), messages


async def test_export_toast_names_the_near_miss_that_shipped(
    seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both surfaces read the same sentence because both call withheld_note —
    this is the TUI half of it. The near miss is the case with no signal
    anywhere else: the row renders exactly like "Renewal"."""
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets import entity_actions

    monkeypatch.chdir(tmp_path)
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    tasks_repo.create(app.conn, "audit support", org_id=org.id, category="Internal Review")
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        entity_actions.export_open_items_flow(nav, org.id)
        await pilot.pause()
        messages = [str(n.message) for n in app._notifications]
        assert any(
            '1 task categorised "Internal Review" WAS exported '
            '(only the exact category "Internal" is withheld)' in m
            for m in messages
        ), messages
        assert not any("internal task withheld" in m for m in messages)


async def test_export_toast_says_nothing_when_there_is_nothing_to_say(
    seeded_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bookkit.repo import tasks as tasks_repo
    from bookkit.tui.widgets import entity_actions

    monkeypatch.chdir(tmp_path)
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    tasks_repo.create(app.conn, "renew GL", org_id=org.id, category="Renewal")
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        entity_actions.export_open_items_flow(nav, org.id)
        await pilot.pause()
        wrote = [str(n.message) for n in app._notifications if "wrote " in str(n.message)]
        assert wrote and all(m.endswith(".xlsx") for m in wrote), wrote


async def test_navigator_rfi_chase_bucket_and_group(seeded_db: Path) -> None:
    """A request with an outstanding item shows in the attention feed as ONE
    row carrying its open count, and under its account as a group."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(
        app.conn, org.id, "Sompo — property questions", "2026-08-05",
        due_on=date.today().isoformat(),
    )
    rfi.add_item(app.conn, req.id, "how many vehicles?")
    rfi.add_item(app.conn, req.id, "loss runs", kind="document")

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        await pilot.pause()

        nav._current = ("att", "rfi")
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        assert table.row_count == 1, "one row per request, not per item"
        row = [str(c) for c in table.get_row(f"rfi:{req.id}")]
        assert any("Sompo — property questions" in c for c in row)
        assert any("2 of 2" in c for c in row)

        nav._current = ("group", ("requests", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        assert table.get_row_index(f"rfi:{req.id}") == 0


async def test_request_scope_shows_on_both_surfaces(seeded_db: Path) -> None:
    """The spec's scope link is only real if you can see it: the placement's
    ref lands in the scope column on tab 9 and in the chase feed."""
    from bookkit.repo import placements, rfi
    from bookkit.tui.widgets.inline_edit import InlineTable
    from bookkit.tui.widgets.tables import ListTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    placement = placements.for_org(app.conn, org.id)[0]
    market = orgs.create(app.conn, name="Sompo", kind="market")
    req = rfi.create_request(
        app.conn, org.id, "Sompo — property questions", "2026-08-05",
        due_on=date.today().isoformat(), market_org_id=market.id,
        placement_id=placement.id,
    )
    rfi.add_item(app.conn, req.id, "how many vehicles?")

    async with app.run_test(size=(170, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        await pilot.pause()
        nav._current = ("att", "rfi")
        nav._render_pane()
        await pilot.pause()
        row = [str(c) for c in nav.query_one("#nav-table", InlineTable).get_row(
            f"rfi:{req.id}"
        )]
        assert any(placement.ref in c for c in row), "chase feed shows the scope"

        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        table = app.screen.query_one("#rfi-requests", ListTable)
        row = [str(c) for c in table.get_row(f"rfi:{req.id}")]
        assert any(placement.ref in c for c in row), "tab 9 shows the scope"
        assert any("Sompo" == c for c in row), "tab 9 shows who asked"


async def test_request_survives_a_merged_away_market(seeded_db: Path) -> None:
    """A market merge soft-deletes the loser. A request still pointing at a
    dead market must not take the app down: the navigator's requests group
    renders it, and its edit form builds instead of handing Select a dead id."""
    from bookkit.forms import entities as ef
    from bookkit.repo import base as repo_base
    from bookkit.repo import rfi
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    dupe = orgs.create(app.conn, name="Axa XL", kind="market")
    req = rfi.create_request(
        app.conn, org.id, "Sompo — property questions", "2026-08-05",
        market_org_id=dupe.id,
    )
    repo_base.soft_delete(app.conn, "org", dupe.id)

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        await pilot.pause()

        nav._current = ("group", ("requests", org.id))
        nav._render_pane()  # KeyError here used to kill the app
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        row = [str(c) for c in table.get_row(f"rfi:{req.id}")]
        assert any("merged market" in c for c in row)

        spec = ef.request_form(
            rfi.get_request(app.conn, req.id), conn=app.conn, org_id=org.id
        )
        assert spec.initial["market_org_id"] is None, (
            "a dead market id would raise InvalidSelectValueError"
        )


async def test_account_requests_tab(seeded_db: Path) -> None:
    """Tab 9 is master/detail: the request list fills the items datasheet
    below it, and d on an item marks it received and dates it today."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.inline_edit import InlineTable
    from bookkit.tui.widgets.tables import ListTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")
    item = rfi.add_item(app.conn, req.id, "how many vehicles?")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()

        requests = app.screen.query_one("#rfi-requests", ListTable)
        assert requests.get_row_index(f"rfi:{req.id}") == 0

        items = app.screen.query_one("#rfi-items", InlineTable)
        assert items.get_row_index(item.id) == 0

        items.focus()
        items.move_cursor(row=0)
        await pilot.press("d")
        await pilot.pause()
        got = rfi.get_item(app.conn, item.id)
        assert got.status == "received"
        assert got.received_on == date.today().isoformat()


async def test_account_requests_tab_empty_paste_stays_open(seeded_db: Path) -> None:
    """P over the items datasheet opens the paste form; saving an empty paste
    is refused in place — the form stays up (input intact) and no item is
    created. Commit-in-place: a refusal is corrected, never retyped."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.forms import FormModal
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()

        app.screen.query_one("#rfi-items", InlineTable).focus()
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)

        form = app.screen
        form.query_one("#form-pasted", TextArea).text = "   \n\n  "
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.screen is form, "an empty paste keeps the form open"
        assert rfi.items_for_request(app.conn, req.id) == []

        # and the refusal is a RAISE, not a returned string: push_form's
        # wrapper discards whatever on_save returns, so a returned message
        # would close the form and silently add nothing. The form's own
        # required check fires first from the keyboard, so this is the only
        # way to reach the guard behind it.
        assert form._commit is not None
        with pytest.raises(ValueError):
            form._commit({"pasted": ""})
        assert rfi.items_for_request(app.conn, req.id) == []

        # the happy path: a real paste creates one item per line, in order,
        # on the request the datasheet is pointed at
        form.query_one("#form-pasted", TextArea).text = (
            "1. how many vehicles?\n2. loss runs"
        )
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.screen is not form, "an accepted paste closes the form"
        assert [i.prompt for i in rfi.items_for_request(app.conn, req.id)] == [
            "how many vehicles?", "loss runs",
        ]


async def test_paste_is_all_or_nothing(seeded_db: Path, monkeypatch) -> None:
    """The only bulk write in the feature runs inside db.transaction, so a
    failure partway leaves NO partial batch committed on the autocommit
    connection."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.forms import FormModal
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")

    real_add_item = rfi.add_item
    calls: list[str] = []

    def flaky(conn, request_id, prompt, **fields):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("disk fell over")
        return real_add_item(conn, request_id, prompt, **fields)

    monkeypatch.setattr(rfi, "add_item", flaky)

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        app.screen.query_one("#rfi-items", InlineTable).focus()
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, FormModal)
        form.query_one("#form-pasted", TextArea).text = "first\nsecond\nthird"
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.screen is form, "a failed paste keeps the form open"
        assert rfi.items_for_request(app.conn, req.id) == [], (
            "the first row must roll back with the batch"
        )


async def test_requests_tab_items_edit_in_cell(seeded_db: Path) -> None:
    """i on the items datasheet writes through rfi_repo — including the
    response column, which is where the answer actually lands."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.inline_edit import CellEditor, InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")
    item = rfi.add_item(app.conn, req.id, "how many vehicles?")

    async with app.run_test(size=(170, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        table = app.screen.query_one("#rfi-items", InlineTable)
        table.focus()
        table.move_cursor(row=0)
        await pilot.pause()

        await pilot.press("i")  # opens on the first editable column, the prompt
        await pilot.pause()
        editor = app.screen.query_one(CellEditor)
        assert editor.value == "how many vehicles?"
        editor.value = "how many trucks?"
        await pilot.press("enter")
        await pilot.pause()
        assert rfi.get_item(app.conn, item.id).prompt == "how many trucks?"

        # hop across the editable columns to the response — prompt, group,
        # needed by, response (status and received-on are `d`'s job, not i's)
        table = app.screen.query_one("#rfi-items", InlineTable)
        table.focus()
        table.move_cursor(row=0)
        await pilot.press("i")
        await pilot.pause()
        for _ in range(3):
            await pilot.press("tab")
            await pilot.pause()
        editor = app.screen.query_one(CellEditor)
        editor.value = "42, all owned"
        await pilot.press("enter")
        await pilot.pause()
        assert rfi.get_item(app.conn, item.id).response == "42, all owned"


async def test_add_on_the_requests_tab_never_does_nothing(seeded_db: Path) -> None:
    """`a` with the items table focused but no request picked says so, the way
    P already does; with neither table focused it falls through to the
    account-level default (a new task), like every other tab."""
    from bookkit.tui.widgets.forms import FormModal
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        screen = app.screen

        # no requests exist on the seeded account, so nothing to add an item to
        app.screen.query_one("#rfi-items", InlineTable).focus()
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app.screen is screen, "no form opens without a request"
        assert any(
            "pick a request first" in str(n.message) for n in app._notifications
        ), "a must say why it did nothing"

        # neither table focused → the account-level default
        screen.set_focus(None)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        assert "task" in app.screen.spec.title.lower()


async def test_rfi_items_show_an_inherited_due_date_dimmed(seeded_db: Path) -> None:
    """The effective-due rule reaches the tab, not just the queue and the
    sheet. An item with no due of its own shows its request's date DIM, so the
    user can tell an inherited date from one that is really on the item; an
    item with neither still reads as an em dash."""
    from bookkit.repo import rfi
    from bookkit.tui import theme
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(
        app.conn, org.id, "Sompo questions", "2026-08-05", due_on="2026-08-19"
    )
    own = rfi.add_item(app.conn, req.id, "loss runs", due_on="2026-08-15")
    inherited = rfi.add_item(app.conn, req.id, "how many vehicles?")

    undated_req = rfi.create_request(app.conn, org.id, "no dates", "2026-08-05")
    undated = rfi.add_item(app.conn, undated_req.id, "anything")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()

        items = app.screen.query_one("#rfi-items", InlineTable)
        # column 4 — item, detail, type, group, NEEDED BY, ...
        own_cell = items.get_row(own.id)[4]
        inherited_cell = items.get_row(inherited.id)[4]

        assert str(own_cell) == "2026-08-15"
        assert str(inherited_cell) == "2026-08-19"
        # the whole point: the inherited one is visibly not the item's own
        assert theme.DIM in str(getattr(inherited_cell, "style", ""))
        assert theme.DIM not in str(getattr(own_cell, "style", ""))

        # a request with no date either: em dash, nothing inherited
        requests = app.screen.query_one("#rfi-requests", ListTable)
        requests.move_cursor(row=requests.get_row_index(f"rfi:{undated_req.id}"))
        await pilot.pause()
        assert str(items.get_row(undated.id)[4]) == "—"


async def test_rfi_inline_edit_of_an_inherited_due_starts_blank(seeded_db: Path) -> None:
    """Displaying the inherited date must not turn it into a stored override
    by accident: the edit buffer is seeded from the ITEM, so opening the cell
    on an inherited date offers an empty field, and saving is a deliberate act."""
    from bookkit.repo import rfi

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(
        app.conn, org.id, "Sompo questions", "2026-08-05", due_on="2026-08-19"
    )
    item = rfi.add_item(app.conn, req.id, "how many vehicles?")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()

        screen = app.screen
        assert screen._rfi_item_inline_initial(item.id, "due_on") == ""
        assert rfi.get_item(app.conn, item.id).due_on is None


async def test_i_on_the_requests_tab_is_only_edit_in_cell(seeded_db: Path) -> None:
    """The tab lands the cursor on the REQUEST list, where nothing is inline
    editable — and a screen-level `i` used to win there and open the account's
    paste-import chooser (contact signature / program schedule / renewal
    terms), while the tab's own hint promised "i edit in cell". One key, two
    unrelated actions, decided by which of two adjacent tables had focus.
    Import moved to I (Grant's call 2026-08-14)."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.paste_import import ImportChooser
    from bookkit.tui.widgets.tables import ListTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")
    rfi.add_item(app.conn, req.id, "how many vehicles?")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        assert app.screen.focused is app.screen.query_one("#rfi-requests", ListTable)

        await pilot.press("i")
        await pilot.pause()
        assert not isinstance(app.screen, ImportChooser), (
            "i on the requests tab opened the paste-import chooser"
        )

        await pilot.press("I")
        await pilot.pause()
        assert isinstance(app.screen, ImportChooser)


async def test_paste_items_answers_from_the_request_list(seeded_db: Path) -> None:
    """P is the intake gesture this tab exists for, and the cursor lands on
    the request list — so requiring items-table focus left it dead, silently,
    exactly where a user first presses it. The highlighted request is the one
    pasted into."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.forms import FormModal
    from bookkit.tui.widgets.tables import ListTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    first = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")
    second = rfi.create_request(app.conn, org.id, "Chubb questions", "2026-08-06")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        table = app.screen.query_one("#rfi-requests", ListTable)
        table.move_cursor(row=table.get_row_index(f"rfi:{second.id}"))
        await pilot.pause()

        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        from textual.widgets import TextArea

        app.screen.query_one("#form-pasted", TextArea).text = (
            "1. loss runs\n2. vehicle count"
        )
        await pilot.press("ctrl+s")
        await pilot.pause()

        # pasted into the HIGHLIGHTED request, not the first one
        assert [i.prompt for i in rfi.items_for_request(app.conn, second.id)] == [
            "loss runs", "vehicle count",
        ]
        assert rfi.items_for_request(app.conn, first.id) == []


async def test_d_from_the_request_list_names_the_table(seeded_db: Path) -> None:
    """`d` is per-ITEM by design; from the master table it used to do nothing
    at all and say nothing — the same silent no-op `a` was already fixed for."""
    from bookkit.repo import rfi

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")
    item = rfi.add_item(app.conn, req.id, "how many vehicles?")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()

        assert any("item" in str(n.message) for n in app._notifications), (
            "d on the request list said nothing"
        )
        assert rfi.get_item(app.conn, item.id).status == "outstanding"


async def test_a_withdrawn_request_says_it_is_withdrawn(seeded_db: Path) -> None:
    """Cancelling a request drops it out of the chase queue, the export and
    open_items. Both surfaces that list requests rendered it identically to a
    live one — same amber count — so nothing on screen said why it vanished."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.tables import ListTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    live = rfi.create_request(app.conn, org.id, "Live ask", "2026-08-05")
    rfi.add_item(app.conn, live.id, "q1")
    dead = rfi.create_request(app.conn, org.id, "Withdrawn ask", "2026-08-05")
    rfi.add_item(app.conn, dead.id, "q2")
    rfi.update_request(app.conn, dead.id, cancelled_at="2026-08-10")
    empty = rfi.create_request(app.conn, org.id, "Nothing typed up", "2026-08-05")
    closed = rfi.create_request(app.conn, org.id, "All back", "2026-08-05")
    back = rfi.add_item(app.conn, closed.id, "q3")
    rfi.update_item(app.conn, back.id, status="received", received_on="2026-08-09")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        table = app.screen.query_one("#rfi-requests", ListTable)
        # column 2 — ref, request, STATE, due, ...
        state = {
            key.value: str(table.get_row(key.value)[2]) for key in table.rows
        }
        assert state[f"rfi:{live.id}"] == "1 open"
        assert state[f"rfi:{dead.id}"] == "withdrawn"
        assert state[f"rfi:{empty.id}"] == "no items yet"
        assert state[f"rfi:{closed.id}"] == "closed"

        # column order is load-bearing, not cosmetic: a DataTable crops from
        # the right, and with state last it rendered as "wi" at 80 columns,
        # the narrowest width the app supports
        assert [str(c.label) for c in table.columns.values()][:4] == [
            "ref", "request", "state", "due",
        ]



async def test_item_detail_is_visible_on_the_datasheet(seeded_db: Path) -> None:
    """The item form has a detail textarea and no table showed it: typed in,
    then invisible everywhere in the TUI, reachable only via the export. The
    same shape as F33's interaction body. First line, dimmed and clipped."""
    from bookkit.repo import rfi
    from bookkit.tui import theme
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")
    item = rfi.add_item(
        app.conn, req.id, "loss runs 2021-2025",
        detail="all lines, valued within 90 days\nand a second line",
    )
    bare = rfi.add_item(app.conn, req.id, "how many vehicles?")

    async with app.run_test(size=(170, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        items = app.screen.query_one("#rfi-items", InlineTable)
        cell = items.get_row(item.id)[1]
        assert str(cell) == "all lines, valued within 90 days"
        assert theme.DIM in str(getattr(cell, "style", ""))
        assert str(items.get_row(bare.id)[1]) == "—"


async def test_delete_interaction_confirms_then_removes_it_undoably(seeded_db: Path) -> None:
    """D on a focused interactions table removes a logged interaction — the
    correction path for an activity logged in error (typically by MCP). It
    confirms first, soft-deletes, and u puts it back."""
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    bad = interactions.log(
        app.conn, org.id, type="note", subject="wrong account",
        occurred_on="2026-08-12", body="logged against the wrong client",
    )

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()

        table = app.screen.query_one("#interactions-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(bad.id))
        await pilot.pause()

        await pilot.press("D")
        await pilot.pause()
        # destructive, so it asks before it acts
        assert isinstance(app.screen, ModalScreen)

        await pilot.press("y")
        await pilot.pause()
        assert bad.id not in {i.id for i in interactions.for_org(app.conn, org.id)}

        await pilot.press("u")
        await pilot.pause()
        assert bad.id in {i.id for i in interactions.for_org(app.conn, org.id)}


async def test_delete_interaction_can_be_declined(seeded_db: Path) -> None:
    """esc at the confirm leaves the interaction alone."""
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    kept = interactions.log(
        app.conn, org.id, type="note", subject="keep me",
        occurred_on="2026-08-12", body="a good note",
    )

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()

        table = app.screen.query_one("#interactions-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(kept.id))
        await pilot.pause()

        await pilot.press("D")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert kept.id in {i.id for i in interactions.for_org(app.conn, org.id)}


async def test_delete_interaction_needs_the_table_focused(seeded_db: Path) -> None:
    """House rule: row actions require table focus. Without it D must be
    inert — not delete whatever happens to sit under an unfocused cursor."""
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    safe = interactions.log(
        app.conn, org.id, type="note", subject="untouched",
        occurred_on="2026-08-12", body="x",
    )

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()

        app.screen.set_focus(None)
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)
        assert safe.id in {i.id for i in interactions.for_org(app.conn, org.id)}


async def test_navigator_lists_recent_mcp_batches(seeded_db: Path) -> None:
    """MCP CHANGES is its own tree section, NOT an attention leaf: attention
    means 'act on this' and carries the 120-day window; this is an audit list
    where most rows need no action. It renders into the shared #nav-table."""
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website on Acme", org_id=org.id,
    )

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()

        table = app.screen.query_one("#nav-table", ListTable)
        row = [str(c) for c in table.get_row(f"batch:{made.id}")]
        assert made.ref in row
        assert "enrich_field" in row
        assert org.name in row


async def test_reverted_batches_render_as_reverted(seeded_db: Path) -> None:
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="task_create", summary="made a task", org_id=None,
    )
    batches_repo.mark_reverted(app.conn, made.id, "2026-08-13T18:00:00Z")

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()

        table = app.screen.query_one("#nav-table", ListTable)
        row = [str(c) for c in table.get_row(f"batch:{made.id}")]
        assert any("reverted" in cell for cell in row)


async def test_mcp_changes_section_absent_when_no_batches(seeded_db: Path) -> None:
    """No MCP activity, no section — the tree stays clean."""
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        from bookkit.tui.screens.navigator import _walk
        tree = app.screen.query_one("#nav-tree")
        datas = {n.data for n in _walk(tree.root) if n.data}
        assert ("batches", None) not in datas


async def test_R_reverts_the_highlighted_batch(seeded_db: Path) -> None:
    """R is dual-role, the house x pattern: revert on a focused MCP CHANGES
    table, refresh everywhere else. Confirm modal first; y applies."""
    from bookkit import db as db_mod
    from bookkit.repo import base
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website", org_id=org.id,
    )
    with db_mod.transaction(app.conn, batch=db_mod.BatchState(batch_id=made.id)):
        base.update(app.conn, "org", org.id, {"website": "https://mcp.example"})

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()

        table = app.screen.query_one("#nav-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"batch:{made.id}"))
        await pilot.pause()

        await pilot.press("R")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        await pilot.press("y")
        await pilot.pause()

        assert orgs.get(app.conn, org.id).website is None
        assert batches_repo.get(app.conn, made.id).reverted_at is not None


async def test_R_on_a_conflicted_batch_shows_the_refusal(seeded_db: Path) -> None:
    """The modal becomes the refusal list; y is inert, esc leaves everything."""
    from bookkit import db as db_mod
    from bookkit.repo import base
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website", org_id=org.id,
    )
    with db_mod.transaction(app.conn, batch=db_mod.BatchState(batch_id=made.id)):
        base.update(app.conn, "org", org.id, {"website": "https://mcp.example"})
    base.update(app.conn, "org", org.id, {"website": "https://grant.example"})

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()
        table = app.screen.query_one("#nav-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"batch:{made.id}"))
        await pilot.press("R")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        await pilot.press("y")          # inert on a conflicted batch
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert orgs.get(app.conn, org.id).website == "https://grant.example"
        assert batches_repo.get(app.conn, made.id).reverted_at is None


async def test_R_off_the_batches_pane_still_refreshes(seeded_db: Path) -> None:
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="task_create", summary="s", org_id=None,
    )
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        await pilot.press("R")          # tree focused, not the batches table
        await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)
        assert batches_repo.get(app.conn, made.id).reverted_at is None


async def test_enter_on_a_batch_shows_field_level_before_and_after(seeded_db: Path) -> None:
    from bookkit import db as db_mod
    from bookkit.repo import base
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website", org_id=org.id,
    )
    with db_mod.transaction(app.conn, batch=db_mod.BatchState(batch_id=made.id)):
        base.update(app.conn, "org", org.id, {"website": "https://mcp.example"})

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()
        table = app.screen.query_one("#nav-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"batch:{made.id}"))
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ModalScreen)
        from textual.widgets import Static as StaticW
        rendered = " ".join(str(w.render()) for w in app.screen.query(StaticW))
        assert "website" in rendered
        assert "https://mcp.example" in rendered


async def test_help_documents_the_revert_key(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        from textual.widgets import Static as StaticW
        rendered = " ".join(str(w.render()) for w in app.screen.query(StaticW))
        assert "R reverts the highlighted change" in rendered


async def test_R_survives_the_batch_being_reverted_under_the_open_modal(
    seeded_db: Path,
) -> None:
    """TOCTOU with the MCP server: the modal is open, the server reverts the
    same batch on its own connection, y lands on an already-reverted batch.
    The dismiss callback must notify, never crash the app."""
    from bookkit import db as db_mod
    from bookkit.repo import base
    from bookkit.repo import batches as batches_repo
    from bookkit.services import batches as batches_svc

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website", org_id=org.id,
    )
    with db_mod.transaction(app.conn, batch=db_mod.BatchState(batch_id=made.id)):
        base.update(app.conn, "org", org.id, {"website": "https://mcp.example"})

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()
        table = app.screen.query_one("#nav-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"batch:{made.id}"))
        await pilot.press("R")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)

        # the MCP server gets there first
        batches_svc.revert(app.conn, made.ref, now="2026-08-13T18:00:00Z")

        await pilot.press("y")
        await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)  # app is alive, modal gone
        assert orgs.get(app.conn, org.id).website is None  # the first revert held


async def test_detail_of_a_reverted_batch_shows_no_false_conflicts(
    seeded_db: Path,
) -> None:
    """After a revert, current values legitimately differ from the batch's
    new_values. enter must render the history plainly, not paint every field
    amber as external tampering."""
    from bookkit import db as db_mod
    from bookkit.repo import base
    from bookkit.repo import batches as batches_repo
    from bookkit.services import batches as batches_svc

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website", org_id=org.id,
    )
    with db_mod.transaction(app.conn, batch=db_mod.BatchState(batch_id=made.id)):
        base.update(app.conn, "org", org.id, {"website": "https://mcp.example"})
    batches_svc.revert(app.conn, made.ref, now="2026-08-13T18:00:00Z")

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()
        table = app.screen.query_one("#nav-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"batch:{made.id}"))
        await pilot.press("enter")
        await pilot.pause()

        from textual.widgets import Static as StaticW
        rendered = " ".join(str(w.render()) for w in app.screen.query(StaticW))
        assert "website" in rendered
        assert "since changed to" not in rendered
        assert "reverted" in rendered


# --- Batch A regressions (review findings F1, F7, F11, F22, F23, F28) --------


def _contrast(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    """WCAG relative-contrast between two rendered colours.

    Colour is signal in this app (CLAUDE.md), so a signal the cursor paints
    over is a bug, not a cosmetic issue. F32 generalises this into a sweep.
    """

    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    def luminance(c: tuple[int, int, int]) -> float:
        return 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2])

    a, b = luminance(fg), luminance(bg)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def _worst_contrast(widget, line: int) -> tuple[float, str]:
    """Lowest contrast of any non-blank text segment on one rendered line."""
    worst, culprit = 21.0, ""
    for segment in widget.render_line(line):
        style = segment.style
        if not style or not style.color or not style.bgcolor or not segment.text.strip():
            continue
        fg = style.color.get_truecolor()
        bg = style.bgcolor.get_truecolor()
        ratio = _contrast((fg.red, fg.green, fg.blue), (bg.red, bg.green, bg.blue))
        if ratio < worst:
            worst, culprit = ratio, segment.text.strip()
    return worst, culprit


async def test_today_edit_ignores_a_table_that_does_not_have_focus(seeded_db: Path) -> None:
    """F1: `e` must act on the tasks table only when the tasks table is
    focused. It used to open the edit form for whatever row the invisible
    tasks cursor sat on — editing a record the user never looked at."""
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, TodayScreen)
        app.screen.query_one("#renewals-table", ListTable).focus()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, TodayScreen), (
            "e opened a form while the renewals table had focus"
        )


async def test_today_edit_still_works_when_the_tasks_table_has_focus(seeded_db: Path) -> None:
    """F1's other half: the gate must not disable the feature it guards."""
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        app.screen.query_one("#tasks-table", ListTable).focus()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        assert app.screen.spec.title == "edit task"


async def test_search_jump_replaces_the_account_screen_instead_of_stacking(
    seeded_db: Path,
) -> None:
    """F7: jumping between clients with / used to push a fresh AccountScreen
    each time, so esc walked back through every account visited."""
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("b")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        baseline = len(app.screen_stack)
        assert isinstance(app.screen, AccountScreen)
        for _ in range(3):
            await pilot.press("slash")
            await pilot.pause()
            for character in "atom":
                await pilot.press(character)
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, AccountScreen)
        assert len(app.screen_stack) == baseline, (
            f"stack grew to {[type(s).__name__ for s in app.screen_stack]}"
        )


async def test_the_theme_command_is_not_offered(seeded_db: Path) -> None:
    """F11: bookkit is deliberately one warm dark palette, and its colours are
    baked into Rich markup — switching theme from the palette repaints the
    chrome and leaves every status word, glyph and separator behind."""
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        titles = [c.title for c in app.get_system_commands(app.screen)]
        assert "Theme" not in titles
        assert "Quit" in titles, "the other system commands must survive"


async def test_opening_a_missing_document_says_so_instead_of_pretending(
    seeded_db: Path, monkeypatch
) -> None:
    """F22: enter on a document row notified 'opening …' and fired `open` at a
    path it never checked, so a moved or deleted file failed in silence."""
    from bookkit.repo import documents

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    documents.add(app.conn, org.id, "Gone Policy", "/nope/not/here.pdf", kind="policy")

    launched: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.Popen", lambda args, *a, **k: launched.append(list(args))
    )

    async with app.run_test(size=(120, 40)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("7")
        await pilot.pause()
        table = app.screen.query_one("#documents-table", ListTable)
        table.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert launched == [], "launched a viewer on a path that does not exist"
        messages = " ".join(n.message for n in app._notifications)
        assert "not found" in messages.lower()


async def test_a_stale_request_edit_names_what_is_missing(seeded_db: Path) -> None:
    """F23: the refusal read 'no longer exists' with no subject."""
    from bookkit.repo import rfi as rfi_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    request = rfi_repo.create_request(app.conn, org.id, "Sompo questions", "2026-08-01")

    async with app.run_test(size=(120, 40)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        table = app.screen.query_one("#rfi-requests", ListTable)
        table.focus()
        await pilot.pause()
        rfi_repo.delete_request(app.conn, request.id)  # vanishes under the cursor
        await pilot.press("e")
        await pilot.pause()
        messages = " ".join(n.message for n in app._notifications)
        assert "request" in messages.lower(), f"unsubjected refusal: {messages!r}"


async def test_the_highlighted_pipeline_card_stays_readable(seeded_db: Path) -> None:
    """F28, reported from use: OptionList paints its highlight BEHIND the
    prompt and does not override an explicit foreground the way DataTable
    does, so every span styled theme.DIM rendered at 1.83:1 on the gold
    cursor — the ref, the probability and the whole lines/effective row
    simply vanished from the selected card."""
    from textual.widgets import OptionList

    from bookkit.services import pipeline as pipeline_svc

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        first_stage = pipeline_svc.OPEN_STAGES[0]
        option_list = app.screen.query_one(f"#list-{first_stage}", OptionList)
        option_list.focus()
        option_list.highlighted = 0
        await pilot.pause()
        assert option_list.option_count, "no cards to highlight"
        for line in range(4):
            worst, culprit = _worst_contrast(option_list, line)
            assert worst >= 3.0, (
                f"line {line} of the highlighted card renders {culprit!r} "
                f"at {worst:.2f}:1"
            )


# --- Batch B regressions (F4, F5, F6, F12, F25) ------------------------------


async def test_today_renewal_countdown_is_not_a_bare_minus(seeded_db: Path) -> None:
    """F4: the countdown was `str(days_remaining)`, so in a 36-cell pane an
    overdue -345 truncated to "-" — indistinguishable from an empty cell.
    theme.days_text renders "◆ 345d over", which carries a glyph and a word."""
    from textual.coordinate import Coordinate

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        table = app.screen.query_one("#renewals-table", ListTable)
        assert table.row_count
        countdowns = [
            str(table.get_cell_at(Coordinate(row, 1))) for row in range(table.row_count)
        ]
        overdue = [c for c in countdowns if "over" in c]
        assert overdue, f"no overdue countdown rendered a word: {countdowns[:5]}"
        assert all("◆" in c for c in overdue), overdue


async def test_today_collapses_to_one_column_on_a_narrow_terminal(
    seeded_db: Path,
) -> None:
    """F4: four panes across 80 columns leaves 36 cells for seven columns."""
    from textual.containers import Grid

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        grid = app.screen.query_one("#today-grid", Grid)
        assert grid.styles.grid_size_columns == 1
        tasks = app.screen.query_one("#tasks-table", ListTable)
        assert tasks.size.width > 60, "a single column should give the pane real width"


async def test_today_stays_two_columns_when_there_is_room(seeded_db: Path) -> None:
    """F4's other half: don't punish the daily-driver terminal."""
    from textual.containers import Grid

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        assert app.screen.query_one("#today-grid", Grid).styles.grid_size_columns == 2


@pytest.mark.parametrize("size", [(100, 30), (120, 40), (140, 45), (170, 50)])
async def test_placements_table_shows_the_premium_column(seeded_db: Path, size) -> None:
    """F5: the tower preview took enough width that `status` truncated to one
    letter and `premium` fell off the table entirely — on the tab that exists
    to show what a programme costs.

    Parameterised because the first fix was a percentage, which fit at 140 and
    was one cell short at 120: the property is 'the numbers are never cropped',
    at every width, not 'it looks right on my terminal'."""
    from textual.coordinate import Coordinate

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    async with app.run_test(size=size) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()
        table = app.screen.query_one("#placements-table", ListTable)
        assert table.row_count
        premium_column = len(table.columns) - 1
        needed = sum(c.get_render_width(table) for c in table.columns.values())
        assert needed <= table.size.width, (
            f"at {size[0]} columns the table needs {needed} cells "
            f"but has {table.size.width}"
        )
        assert str(table.get_cell_at(Coordinate(0, premium_column))).strip()


async def test_the_glance_card_never_wraps_a_renewal_row(seeded_db: Path) -> None:
    """F6: each renewal was one long markup string in a Static, so the landing
    view — the first thing seen on every launch — wrapped every row onto a
    second line even at 140 columns."""
    from rich.cells import cell_len
    from rich.markup import render as render_markup
    from textual.widgets import Static

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        card = app.screen.query_one("#nav-card", Static)
        rendered = card.render()
        plain = getattr(rendered, "plain", None)
        if plain is None:  # a markup string — measure what it actually paints
            plain = render_markup(str(rendered)).plain
        too_long = [
            line for line in plain.splitlines() if cell_len(line) > card.size.width
        ]
        assert not too_long, (
            f"{len(too_long)} row(s) exceed the {card.size.width}-cell pane: "
            f"{too_long[0][:90]!r}"
        )


async def test_an_empty_account_tab_says_so(seeded_db: Path) -> None:
    """F12: an empty tab rendered a header row over blank space, so 'nothing
    here' and 'it failed to load' looked identical. The Navigator solved this
    with its hint line; the account screen never got it."""
    from textual.widgets import Static

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("7")  # documents — empty in the seed
        await pilot.pause()
        assert app.screen.query_one("#documents-table", ListTable).row_count == 0
        hint = str(app.screen.query_one("#tab-hint", Static).render())
        assert "empty" in hint.lower() or "nothing" in hint.lower(), hint


async def test_a_populated_account_tab_keeps_its_key_hints(seeded_db: Path) -> None:
    """F12's other half: the empty state must not eat the key hints."""
    from textual.widgets import Static

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("2")  # contacts — seeded
        await pilot.pause()
        assert app.screen.query_one("#contacts-table", ListTable).row_count
        hint = str(app.screen.query_one("#tab-hint", Static).render())
        assert "add" in hint.lower()


async def test_kanban_columns_stay_wide_enough_to_read(seeded_db: Path) -> None:
    """F25: five fixed columns across 80 cells gave each 13 characters, so
    every card wrapped into unreadable stubs. Columns now hold a floor and the
    row scrolls instead."""
    from textual.widgets import OptionList

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        for option_list in app.screen.query(OptionList):
            assert option_list.size.width >= 24, option_list.size.width


async def test_help_sections_are_collapsible_and_fit_the_width(seeded_db: Path) -> None:
    """F13: help was one hand-aligned two-column Static, 160 rows tall in a
    19-row viewport at 80 columns, whose columns only lined up above ~70 cells.
    Sections now collapse, and no line depends on a width it may not get."""
    from rich.cells import cell_len
    from textual.widgets import Collapsible, Static

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        sections = list(app.screen.query(Collapsible))
        assert len(sections) >= 6, f"only {len(sections)} help sections"
        assert sum(1 for s in sections if not s.collapsed) == 1, (
            "exactly one section should start open"
        )
        # measure the width the open section's body ACTUALLY gets, rather than
        # guessing at the box's padding and the Collapsible's indent
        body_width = next(
            s.query_one(".help-body", Static).size.width
            for s in sections
            if not s.collapsed
        )
        assert body_width >= 40, f"help body is only {body_width} cells wide"
        from bookkit.tui.screens.help import HELP_SECTIONS

        for title, body in HELP_SECTIONS:
            for line in body.splitlines():
                assert cell_len(line) <= body_width, (
                    f"{title!r} line needs {cell_len(line)} of {body_width}: {line!r}"
                )


async def test_help_is_current_about_the_account_tabs(seeded_db: Path) -> None:
    """F13's content errors: help said '1–8 jump straight to a tab' when there
    are nine, and carried two separate 'markets screen' sections."""
    from bookkit.tui.screens.help import HELP_SECTIONS

    titles = [title for title, _ in HELP_SECTIONS]
    assert len(titles) == len(set(titles)), f"duplicate help sections: {titles}"
    joined = "\n".join(body for _, body in HELP_SECTIONS)
    assert "1–8" not in joined and "1-8" not in joined
    assert "1–9" in joined


# --- Batch C: the CRUD gaps Grant hit in use (F33, F34) ----------------------


async def _open_tab(pilot, app, org_id: str, tab: str, table_id: str):
    app.open_account(org_id)
    await pilot.pause()
    await pilot.press(tab)
    await pilot.pause()
    table = app.screen.query_one(f"#{table_id}", ListTable)
    table.focus()
    await pilot.pause()
    return table


async def test_D_drops_the_selected_task(seeded_db: Path) -> None:
    """F34: a task could only be completed. One logged against the wrong
    account had to be marked 'done' — a lie in the event log."""
    from bookkit.repo import tasks as tasks_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    task = tasks_repo.create(app.conn, "Wrong account, delete me", org_id=org.id)
    async with app.run_test(size=(140, 45)) as pilot:
        table = await _open_tab(pilot, app, org.id, "8", "open-items-table")
        table.move_cursor(row=table.get_row_index(task.id))
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        assert tasks_repo.get(app.conn, task.id).status == "dropped"
        assert task.id not in {
            t.id for t in tasks_repo.open_tasks(app.conn, org_id=org.id)
        }


async def test_dropping_a_task_is_undoable(seeded_db: Path) -> None:
    """F34: drop is ONE field write, so `u` genuinely restores it — which is
    why drop is the right verb here and hard delete is not."""
    from bookkit.repo import tasks as tasks_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    task = tasks_repo.create(app.conn, "Dropped by mistake", org_id=org.id)
    async with app.run_test(size=(140, 45)) as pilot:
        table = await _open_tab(pilot, app, org.id, "8", "open-items-table")
        table.move_cursor(row=table.get_row_index(task.id))
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        assert tasks_repo.get(app.conn, task.id).status == "dropped"
        await pilot.press("u")
        await pilot.pause()
        assert tasks_repo.get(app.conn, task.id).status == "open"


async def test_D_still_deletes_an_interaction(seeded_db: Path) -> None:
    """F34's regression half: generalising D must not break what it did."""
    from bookkit.repo import interactions

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    logged = interactions.for_org(app.conn, org.id)[0]
    async with app.run_test(size=(140, 45)) as pilot:
        table = await _open_tab(pilot, app, org.id, "3", "interactions-table")
        table.move_cursor(row=table.get_row_index(logged.id))
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)  # confirm first, as before
        await pilot.press("y")
        await pilot.pause()
        assert logged.id not in {i.id for i in interactions.for_org(app.conn, org.id)}


async def test_the_interaction_body_is_readable(seeded_db: Path) -> None:
    """F33: interactions.log() stores a body and nothing displayed it, so
    every note written was unreadable from the app that wrote it."""
    from textual.widgets import Static

    from bookkit.repo import interactions

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    note = interactions.log(
        app.conn, org.id, "call", "Renewal strategy",
        "2026-08-12", body="They want to move the AL limit to $10M [confirmed].",
    )
    async with app.run_test(size=(140, 45)) as pilot:
        table = await _open_tab(pilot, app, org.id, "3", "interactions-table")
        table.move_cursor(row=table.get_row_index(note.id))
        await pilot.pause()
        shown = str(app.screen.query_one("#interaction-detail", Static).render())
        assert "move the AL limit" in shown
        # brackets survive: pasted email bodies are not Rich markup
        assert "[confirmed]" in shown


async def test_editing_an_interaction_saves_the_body(seeded_db: Path) -> None:
    """F33: interactions.update() existed in the repo and was called from
    nowhere in tui/ — on tab 3, `e` edited the ACCOUNT."""
    from bookkit.repo import interactions

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    note = interactions.log(
        app.conn, org.id, "call", "Wrong subject", "2026-08-12", body="first draft"
    )
    async with app.run_test(size=(140, 45)) as pilot:
        table = await _open_tab(pilot, app, org.id, "3", "interactions-table")
        table.move_cursor(row=table.get_row_index(note.id))
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen), "e did not open a form"
        assert "interaction" in app.screen.spec.title
        await _fill(pilot, app, "subject", "Corrected subject")
        app.screen.query_one("#form-body", TextArea).text = "the note, rewritten"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        saved = interactions.get(app.conn, note.id)
        assert saved.subject == "Corrected subject"
        assert saved.body == "the note, rewritten"


async def test_completing_a_task_then_undoing_actually_reopens_it(
    seeded_db: Path,
) -> None:
    """The toast says "task done — u to undo". complete() writes TWO fields
    (status and completed_at) and base.update logs one event per field, while
    undo takes only the most recent — so this pins whether the promise is
    real."""
    from bookkit.repo import tasks as tasks_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    task = tasks_repo.create(app.conn, "Finish me", org_id=org.id)
    async with app.run_test(size=(140, 45)) as pilot:
        table = await _open_tab(pilot, app, org.id, "8", "open-items-table")
        table.move_cursor(row=table.get_row_index(task.id))
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert tasks_repo.get(app.conn, task.id).status == "done"
        await pilot.press("u")
        await pilot.pause()
        assert tasks_repo.get(app.conn, task.id).status == "open", (
            "u after d left the task done — the toast promises an undo that "
            "does not happen"
        )


def _walk_tree(node):
    """Every node under `node`, depth-first — so a test can address a tree
    node by what it IS rather than by how many downs it takes to reach."""
    for child in node.children:
        yield child
        yield from _walk_tree(child)


async def test_navigator_D_drops_the_task_under_the_cursor(seeded_db: Path) -> None:
    """F34's other half: the Navigator is home, `d` completes a task there,
    and nothing dropped one."""
    from textual.widgets import Tree

    from bookkit.repo import tasks as tasks_repo

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        # find the node rather than counting keypresses: a tree whose shape
        # changes should not silently retarget this test at another list
        tree = app.screen.query_one("#nav-tree", Tree)
        target = next(
            node
            for node in _walk_tree(tree.root)
            if node.data == ("att", "tasks")
        )
        tree.cursor_line = target.line
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        table = app.screen.query_one("#nav-table", ListTable)
        assert table.has_focus and table.row_count
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        task_id = str(key).partition(":")[2]
        await pilot.press("D")
        await pilot.pause()
        assert tasks_repo.get(app.conn, task_id).status == "dropped"
        await pilot.press("u")
        await pilot.pause()
        assert tasks_repo.get(app.conn, task_id).status == "open"


# --- Batch D: faster than navigating (F10, F19, F20, F21) --------------------


async def test_the_palette_finds_a_record_by_name(seeded_db: Path) -> None:
    """F10: the palette was already bound and advertised in the Footer, and
    offered only theme-change and quit. For a database utility, jumping to any
    record from anywhere is the highest-leverage thing it can do."""
    from bookkit.tui.commands import RecordCommands

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        provider = RecordCommands(app.screen)
        await provider.startup()
        hits = [hit async for hit in provider.search("atom")]
        assert hits, "no hits for 'atom'"
        assert any("Atomic" in (hit.text or "") for hit in hits)


async def test_the_palette_offers_the_screens(seeded_db: Path) -> None:
    """F10's other half: the palette should also answer 'what can I do'."""
    from bookkit.tui.commands import ScreenCommands

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        provider = ScreenCommands(app.screen)
        await provider.startup()
        offered = {hit.text async for hit in provider.discover()}
        assert any("Today" in t for t in offered if t)
        assert any("Calendar" in t for t in offered if t)


async def test_the_palette_jumps_to_the_account(seeded_db: Path) -> None:
    """A hit that does not open the record is a search box, not a jump."""
    from bookkit.tui.commands import RecordCommands
    from bookkit.tui.screens.account import AccountScreen

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        provider = RecordCommands(app.screen)
        await provider.startup()
        hit = next(
            h for h in [x async for x in provider.search("atom")]
            if "Atomic" in (h.text or "")
        )
        hit.command()
        await pilot.pause()
        assert isinstance(app.screen, AccountScreen)


async def test_Y_copies_the_selected_row(seeded_db: Path) -> None:
    """F19: nothing in the app ever called copy_to_clipboard, so an email or a
    ref could not be lifted out of a row without retyping it."""
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    copied: list[str] = []
    async with app.run_test(size=(140, 45)) as pilot:
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        table = app.screen.query_one("#contacts-table", ListTable)
        table.focus()
        await pilot.pause()
        await pilot.press("Y")
        await pilot.pause()
        assert copied, "Y copied nothing"
        # a contact row is worth copying for its email above all
        assert "@" in copied[0]


async def test_the_book_filter_survives_a_restart(seeded_db: Path) -> None:
    """F21: filters reset on every entry, so the same one was retyped daily."""
    from textual.widgets import Input

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("b")
        await pilot.pause()
        app.screen.query_one("#book-filter", Input).value = "borealis"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    again = BookkitApp(seeded_db)
    async with again.run_test(size=(140, 45)) as pilot:
        await pilot.press("b")
        await pilot.pause()
        assert again.screen.query_one("#book-filter", Input).value == "borealis"
        assert again.screen.query_one("#book-table", ListTable).row_count == 1


def test_bookctl_open_resolves_a_ref_or_a_name(seeded_db: Path) -> None:
    """F20: bookctl always landed on the Navigator, so a shell alias could not
    put you in front of a client."""
    from bookkit import db as db_mod
    from bookkit.cli import resolve_org

    conn = db_mod.connect(seeded_db)
    try:
        by_ref = resolve_org(conn, "ACC-0001")
        by_name = resolve_org(conn, "atomic industries")
        assert by_ref is not None and by_ref.id == by_name.id
        assert resolve_org(conn, "no such client") is None
    finally:
        conn.close()


# --- Batch E: keep it fixed (F3, F16, F32) -----------------------------------


async def test_a_failing_row_action_does_not_take_the_app_down(
    seeded_db: Path, tmp_path: Path, monkeypatch
) -> None:
    """F3: there was no app-level handler, so a stale row key or a locked
    database on any un-guarded row action dropped the session to a traceback,
    losing the screen stack and whatever form was open."""
    from bookkit.repo import placements

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("4")
        await pilot.pause()
        app.screen.query_one("#placements-table", ListTable).focus()
        await pilot.pause()
        monkeypatch.setattr(placements, "get", boom)
        await pilot.press("r")  # renew — reaches placements.get with no guard
        await pilot.pause()
        assert app.is_running, "the app exited on a failing action"
        assert isinstance(app.screen, AccountScreen), "lost the screen stack"
        messages = " ".join(n.message for n in app._notifications).lower()
        assert "database is locked" in messages
        assert app.crash_log_path().exists(), "no traceback was written"
        assert "OperationalError" in app.crash_log_path().read_text()


def test_the_private_datatable_attributes_still_exist() -> None:
    """F16: _settle_tables() drives four private DataTable attributes. They
    exist in textual 8.2.8 and nothing promises they will survive an upgrade,
    so this fails loudly instead of column widths going quietly wrong."""
    from textual.widgets import DataTable

    table = DataTable()
    for attribute in ("_require_update_dimensions", "_new_rows"):
        assert hasattr(table, attribute), attribute
    for method in ("_update_dimensions", "_clear_caches"):
        assert callable(getattr(table, method, None)), method


def test_textual_is_pinned_to_a_major_version() -> None:
    """F16's other half: pyproject declared textual>=0.58 while running 8.2.8,
    three majors above the floor — so `uv lock --upgrade` elsewhere could pull
    a version where the privates above are gone."""
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        pins = tomllib.load(handle)["project"]["dependencies"]
    textual_pin = next(p for p in pins if p.startswith("textual"))
    assert "<" in textual_pin, f"textual is unbounded above: {textual_pin!r}"


async def test_appetite_can_be_edited_from_the_market_screen(seeded_db: Path) -> None:
    """F18: MarketDetailScreen bound a/w/i and neither e nor d, and
    appetite_form(existing=…) existed and was never called with one — so a
    typo'd appetite row was permanent from the TUI."""
    from bookkit.repo import orgs
    from bookkit.tui.screens.markets import MarketDetailScreen

    app = BookkitApp(seeded_db)
    market = next(
        m for m in orgs.list_orgs(app.conn, kind="market")
        if orgs.appetite_for_market(app.conn, m.id)
    )
    row = orgs.appetite_for_market(app.conn, market.id)[0]

    async with app.run_test(size=(140, 45)) as pilot:
        app.push_screen(MarketDetailScreen(market.id))
        await pilot.pause()
        table = app.screen.query_one("#md-appetite", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(row.id))
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen), "e opened nothing"
        assert "appetite" in app.screen.spec.title
        await _fill(pilot, app, "territories", "US and Canada")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert orgs.get_appetite(app.conn, row.id).territories == "US and Canada"


async def test_appetite_can_be_deleted_from_the_market_screen(seeded_db: Path) -> None:
    """F18's other half, and it must be undoable like every other delete."""
    from bookkit.repo import orgs
    from bookkit.tui.screens.markets import MarketDetailScreen

    app = BookkitApp(seeded_db)
    market = next(
        m for m in orgs.list_orgs(app.conn, kind="market")
        if orgs.appetite_for_market(app.conn, m.id)
    )
    row = orgs.appetite_for_market(app.conn, market.id)[0]

    async with app.run_test(size=(140, 45)) as pilot:
        app.push_screen(MarketDetailScreen(market.id))
        await pilot.pause()
        table = app.screen.query_one("#md-appetite", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(row.id))
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        assert row.id not in {a.id for a in orgs.appetite_for_market(app.conn, market.id)}
        await pilot.press("u")
        await pilot.pause()
        assert row.id in {a.id for a in orgs.appetite_for_market(app.conn, market.id)}


async def test_an_underwriter_can_be_edited_from_the_market_screen(
    seeded_db: Path,
) -> None:
    """The same gap on the contacts table: `w` added underwriters and nothing
    could correct one."""
    from bookkit.repo import contacts, orgs
    from bookkit.tui.screens.markets import MarketDetailScreen

    app = BookkitApp(seeded_db)
    market = next(
        m for m in orgs.list_orgs(app.conn, kind="market")
        if contacts.for_org(app.conn, m.id)
    )
    who = contacts.for_org(app.conn, market.id)[0]

    async with app.run_test(size=(140, 45)) as pilot:
        app.push_screen(MarketDetailScreen(market.id))
        await pilot.pause()
        table = app.screen.query_one("#md-contacts", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(who.id))
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        assert "contact" in app.screen.spec.title
