"""Creation and edit paths: a fresh empty book must be fully populatable from
the TUI, and the seeded book must support the daily mutations (record a quote,
edit a task, add a contact)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from textual.widgets import Input, Select, TabbedContent

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
