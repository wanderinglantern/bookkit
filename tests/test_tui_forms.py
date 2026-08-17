"""Creation and edit paths: a fresh empty book must be fully populatable from
the TUI, and the seeded book must support the daily mutations (record a quote,
edit a task, add a contact)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from textual.coordinate import Coordinate
from textual.widgets import Input, Select, Static, TabbedContent

from bookkit import db, seed
from bookkit.repo import contacts, orgs, placements, submissions
from bookkit.repo import tasks as tasks_repo
from bookkit.tui.app import BookkitApp
from bookkit.tui.screens.account import AccountScreen
from bookkit.tui.widgets.forms import FormModal
from bookkit.tui.widgets.tables import ListTable


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    path = tmp_path / "empty.db"
    db.connect(path).close()
    return path


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    path = tmp_path / "seeded.db"
    conn = db.connect(path)
    seed.seed(conn, today=date.today())
    conn.close()
    return path


async def _fill(pilot, app, key: str, text: str) -> None:
    widget = app.screen.query_one(f"#form-{key}", Input)
    widget.focus()
    await pilot.pause()
    widget.value = text
    await pilot.pause()


async def _pick(pilot, app, key: str, value: str) -> None:
    app.screen.query_one(f"#form-{key}", Select).value = value
    await pilot.pause()


async def test_setup_path_from_empty_book(empty_db: Path) -> None:
    """The full from-scratch flow: account → contact → placement, no seed."""
    app = BookkitApp(empty_db)
    async with app.run_test(size=(130, 42)) as pilot:
        # Today hints at the path; b → book, a → new account form
        await pilot.press("b")
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await _fill(pilot, app, "name", "Acme Corp")
        await _pick(pilot, app, "kind", "client")
        await _pick(pilot, app, "status", "active")
        await _fill(pilot, app, "owner", "grant")
        await pilot.press("ctrl+s")
        await pilot.pause()
        org = orgs.find_by_name(app.conn, "Acme Corp")
        assert org is not None and org.ref == "ACC-0001" and org.status == "active"

        # open it and add a contact from the contacts tab
        table = app.screen.query_one("#book-table", ListTable)
        assert table.row_count == 1
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, AccountScreen)
        app.screen.query_one(TabbedContent).active = "tab-contacts"
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await _fill(pilot, app, "first_name", "  Rosa ")
        await _fill(pilot, app, "last_name", "Silva")
        await _pick(pilot, app, "role", "risk_manager")
        await _fill(pilot, app, "email", " Rosa.Silva@ACME.example ")
        await _fill(pilot, app, "phone", "312.555.0142")
        await _fill(pilot, app, "linkedin", "in/rosa-silva")
        await pilot.press("ctrl+s")
        await pilot.pause()
        roster = contacts.for_org(app.conn, org.id)
        assert [c.name for c in roster] == ["Rosa Silva"]
        assert roster[0].email == "Rosa.Silva@acme.example"
        assert roster[0].phone == "(312) 555-0142"
        assert roster[0].linkedin == "https://www.linkedin.com/in/rosa-silva"

        # and a placement with human dates + money shorthand
        app.screen.query_one(TabbedContent).active = "tab-placements"
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await _fill(pilot, app, "program_name", "2026 Casualty Program")
        await _fill(pilot, app, "period_from", "2026-01-01")
        await _fill(pilot, app, "period_to", "2027-01-01")
        await _fill(pilot, app, "total_premium", "1.5m")
        await _fill(pilot, app, "commission_bps", "1250")
        await pilot.press("ctrl+s")
        await pilot.pause()
        rows = placements.for_org(app.conn, org.id)
        assert len(rows) == 1
        assert rows[0].total_premium == 150_000_000  # 1.5m in cents
        assert rows[0].period_to == "2027-01-01"


async def test_form_rejects_bad_money_and_missing_required(empty_db: Path) -> None:
    app = BookkitApp(empty_db)
    async with app.run_test(size=(130, 42)) as pilot:
        await pilot.press("b")
        await pilot.press("a")
        await pilot.pause()
        # missing required name → still on the form
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await pilot.press("escape")
        await pilot.pause()
        assert orgs.list_orgs(app.conn) == []


async def test_new_market_and_appetite(empty_db: Path) -> None:
    app = BookkitApp(empty_db)
    async with app.run_test(size=(130, 42)) as pilot:
        await pilot.press("m")
        await pilot.press("a")
        await pilot.pause()
        await _fill(pilot, app, "name", "Chubb")
        await _pick(pilot, app, "kind", "market")
        await _pick(pilot, app, "market_type", "carrier")
        await _fill(pilot, app, "am_best_rating", "A++")
        await pilot.press("ctrl+s")
        await pilot.pause()
        market = orgs.find_by_name(app.conn, "Chubb")
        assert market is not None and market.kind == "market"
        profile = orgs.get_market_profile(app.conn, market.id)
        assert profile is not None and profile.market_type == "carrier"


async def test_record_market_response(seeded_db: Path) -> None:
    """e on a submission records the quote — the core daily mutation."""
    conn = db.connect(seeded_db)
    out = [s for s in submissions.outstanding(conn)][0]
    placement = placements.get(conn, out.placement_id)
    conn.close()

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(130, 42)) as pilot:
        app.open_account(placement.org_id)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AccountScreen)
        screen.query_one(TabbedContent).active = "tab-pipeline"
        await pilot.pause()
        subs_table = screen.query_one("#pipeline-subs", ListTable)
        subs_table.focus()
        # move the cursor onto our known-'out' submission
        for row_index in range(subs_table.row_count):
            from textual.coordinate import Coordinate

            key = subs_table.coordinate_to_cell_key(Coordinate(row_index, 0)).row_key.value
            if key == out.id:
                subs_table.move_cursor(row=row_index)
                break
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await _pick(pilot, app, "status", "quoted")
        await _fill(pilot, app, "quoted_premium", "800k")
        await pilot.press("ctrl+s")
        await pilot.pause()
        updated = submissions.get(app.conn, out.id)
        assert updated.status == "quoted"
        assert updated.quoted_premium == 80_000_000


async def test_today_new_task(empty_db: Path) -> None:
    app = BookkitApp(empty_db)
    async with app.run_test(size=(130, 42)) as pilot:
        await pilot.press("t")  # navigator is home; Today has the a=task key
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await _fill(pilot, app, "title", "Call the accountant")
        await _fill(pilot, app, "due_on", "+2w")
        await pilot.press("ctrl+s")
        await pilot.pause()
        tasks = tasks_repo.open_tasks(app.conn)
        assert [t.title for t in tasks] == ["Call the accountant"]
        assert tasks[0].due_on is not None


async def test_form_commit_refusal_keeps_form_open(empty_db: Path) -> None:
    from bookkit.forms.spec import Field, FormSpec

    app = BookkitApp(empty_db)
    outcomes: list[str | None] = ["refused: gap under layer", None]
    committed: list[dict] = []

    def commit(values: dict) -> str | None:
        committed.append(values)
        return outcomes.pop(0)

    spec = FormSpec("edit layer", [Field("name", "name", required=True)])
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(FormModal(spec, commit=commit))
        await pilot.pause()
        app.screen.query_one("#form-name", Input).value = "Primary"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)  # refused → still open
        assert app.screen.query_one("#form-name", Input).value == "Primary"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, FormModal)  # success → dismissed
        assert len(committed) == 2


async def test_form_commit_exception_is_an_error_not_a_crash(empty_db: Path) -> None:
    from bookkit.forms.spec import Field, FormSpec

    app = BookkitApp(empty_db)

    def commit(values: dict) -> str | None:
        raise RuntimeError("db locked")

    spec = FormSpec("x", [Field("name", "name")])
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(FormModal(spec, commit=commit))
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)  # still alive


async def test_form_fields_carry_dropdown_and_ghost_suggestions(seeded_db: Path) -> None:
    from textual_autocomplete import AutoComplete

    from bookkit.tui.widgets.entity_forms import placement_form
    from bookkit.tui.widgets.forms import FormModal

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(130, 42)) as pilot:
        spec = placement_form(conn=app.conn)
        program_field = next(f for f in spec.fields if f.key == "program_name")
        assert program_field.suggestions  # existing program names flow in
        app.push_screen(FormModal(spec))
        await pilot.pause()
        assert app.screen.query(AutoComplete)  # dropdown mounted
        assert app.screen.query_one("#form-program_name", Input).suggester is not None


async def test_form_draft_survives_esc_and_clears_on_save(empty_db: Path) -> None:
    from bookkit.forms.spec import Field, FormSpec
    from bookkit.repo import drafts
    from bookkit.tui.widgets.forms import FormModal

    def spec() -> FormSpec:
        return FormSpec(
            "t",
            [Field("title", "title", required=True), Field("notes", "notes", "textarea")],
        )

    app = BookkitApp(empty_db)
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(FormModal(spec(), draft_key="test:draft"))
        await pilot.pause()
        await _fill(pilot, app, "title", "half-typed thought")
        await pilot.press("escape")
        await pilot.pause()
        assert drafts.load(app.conn, "test:draft") is not None

        # reopen: the half-typed value is back
        app.push_screen(FormModal(spec(), draft_key="test:draft"))
        await pilot.pause()
        assert app.screen.query_one("#form-title", Input).value == "half-typed thought"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert drafts.load(app.conn, "test:draft") is None


async def test_assign_unknown_member_creates_inline(seeded_db: Path) -> None:
    """Choosing '+ new team member…' in the who-select chains into the member
    form; saving it creates the member AND completes the assignment."""
    from bookkit.repo import team as team_repo

    conn = db.connect(seeded_db)
    org = orgs.find_by_name(conn, "Atomic Industries, Inc.")
    conn.close()

    app = BookkitApp(seeded_db)
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        assert isinstance(app.screen, AccountScreen)
        await pilot.press("w")  # action_assign_team
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await _pick(pilot, app, "team_member_id", "__new__")
        await pilot.press("ctrl+s")
        await pilot.pause()
        # chained into the member form
        assert isinstance(app.screen, FormModal)
        await _fill(pilot, app, "name", "Priya Nair")
        await pilot.press("ctrl+s")
        await pilot.pause()

        members = team_repo.list_members(app.conn)
        assert any(m.name == "Priya Nair" for m in members)
        new_member = next(m for m in members if m.name == "Priya Nair")
        assignments = team_repo.for_org(app.conn, org.id)
        assert any(a["team_member_id"] == new_member.id for a in assignments)


# --- team assignments on the account overview ---------------------------------
#
# One colleague can hold several assignments on the same account — one row per
# line of cover or per placement (the MCP side asserts the same in
# test_mcpserver.py). So every one of these flows has to act on the ASSIGNMENT
# under the cursor, never on "the member", or Grant's two-row case (a blank
# `lines` row plus a "casualty" row) becomes unfixable from the TUI.


async def _team_rows(app) -> dict[str, int]:
    """assignment_id → row index, so a test never depends on sort order between
    two assignments that share a member and a scope."""
    table = app.screen.query_one("#ov-team", ListTable)
    return {
        table.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value: row
        for row in range(table.row_count)
    }


async def _focus_team_row(pilot, app, assignment_id: str) -> None:
    table = app.screen.query_one("#ov-team", ListTable)
    table.focus()
    await pilot.pause()
    table.cursor_coordinate = Coordinate((await _team_rows(app))[assignment_id], 0)
    await pilot.pause()


@pytest.fixture
def two_assignments(seeded_db: Path):
    """One member, two account-level assignments: a blank-lines row and a
    casualty row — the shape Grant hit."""
    from bookkit.repo import team as team_repo

    conn = db.connect(seeded_db)
    org = orgs.find_by_name(conn, "Atomic Industries, Inc.")
    member = team_repo.create_member(conn, "Rosa Delgado")
    blank = team_repo.assign(conn, member.id, org_id=org.id)
    casualty = team_repo.assign(conn, member.id, org_id=org.id, lines="casualty")
    conn.close()
    return seeded_db, org, member, blank, casualty


async def test_removing_one_assignment_leaves_the_member_s_others(two_assignments) -> None:
    """D on the team table removes the row under the cursor and nothing else:
    the member keeps their other assignment and stays on the roster."""
    from bookkit.repo import team as team_repo

    path, org, member, blank, casualty = two_assignments
    app = BookkitApp(path)
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await _focus_team_row(pilot, app, casualty.id)

        await pilot.press("D")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        surviving = {r["id"] for r in team_repo.for_org(app.conn, org.id)}
        assert casualty.id not in surviving
        assert blank.id in surviving
        # removing an assignment is not retiring a colleague
        assert any(m.id == member.id for m in team_repo.list_members(app.conn))


async def test_remove_confirm_names_the_line_so_two_rows_are_telling_apart(
    two_assignments,
) -> None:
    """With two rows for one person, 'remove Rosa Delgado?' is unanswerable —
    the prompt has to carry the line and the scope."""
    path, org, _member, _blank, casualty = two_assignments
    app = BookkitApp(path)
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await _focus_team_row(pilot, app, casualty.id)
        await pilot.press("D")
        await pilot.pause()

        prompt = " ".join(
            str(s.render()) for s in app.screen.query(Static)
        )
        assert "Rosa Delgado" in prompt
        assert "casualty" in prompt


async def test_edit_assignment_fills_in_the_blank_lines_row(two_assignments) -> None:
    """e on a team row edits THAT assignment's role/lines/notes — the direct
    fix for a row assigned with no lines."""
    from bookkit.repo import team as team_repo

    path, org, _member, blank, casualty = two_assignments
    app = BookkitApp(path)
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await _focus_team_row(pilot, app, blank.id)

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        # the member is already known — the form must not offer to re-pick them
        assert not app.screen.query("#form-team_member_id")
        await _fill(pilot, app, "lines", "property")
        await pilot.press("ctrl+s")
        await pilot.pause()

        rows = {r["id"]: r for r in team_repo.for_org(app.conn, org.id)}
        assert rows[blank.id]["lines"] == "property"
        assert rows[casualty.id]["lines"] == "casualty"  # sibling untouched


async def test_edit_assignment_never_re_scopes_it(two_assignments) -> None:
    """Correcting an assignment must not move it between clients — re-scoping
    is unassign+assign, deliberately not an edit (CLAUDE.md)."""
    from bookkit.repo import team as team_repo

    path, org, _member, blank, _casualty = two_assignments
    app = BookkitApp(path)
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await _focus_team_row(pilot, app, blank.id)
        await pilot.press("e")
        await pilot.pause()

        for forbidden in ("#form-client", "#form-placement_ref", "#form-org_id"):
            assert not app.screen.query(forbidden)

        await pilot.press("escape")
        await pilot.pause()
        row = {r["id"]: r for r in team_repo.for_org(app.conn, org.id)}[blank.id]
        assert row["org_id"] == org.id
        assert row["placement_id"] is None


async def test_removed_assignment_comes_back_with_u(two_assignments) -> None:
    """Soft delete + event log, so the screen's undo restores it."""
    from bookkit.repo import team as team_repo

    path, org, _member, _blank, casualty = two_assignments
    app = BookkitApp(path)
    async with app.run_test(size=(140, 45)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await _focus_team_row(pilot, app, casualty.id)
        await pilot.press("D")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert casualty.id not in {r["id"] for r in team_repo.for_org(app.conn, org.id)}

        await pilot.press("u")
        await pilot.pause()
        assert casualty.id in {r["id"] for r in team_repo.for_org(app.conn, org.id)}
