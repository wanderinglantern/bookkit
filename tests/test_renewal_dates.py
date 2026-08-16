"""Every screen counts down to the same date.

Attention is measured to the earliest LINE end (`RenewalItem.renewal_on`),
because an Inland Marine layer runs out months before its program period
does. The Navigator and the MCP server print that date. Today, Book, the
account header and the calendar printed `placement.period_to` beside a
countdown measured to `renewal_on` — so on seeded data Today read

    expiry 2026-09-03   ◆ 70d over   Atomic Industries, Inc.

a date twenty days in the FUTURE, in red, labelled seventy days overdue. Four
independent reviewers found this; it is the number a broker uses to decide
when to start a renewal.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from textual.coordinate import Coordinate

from bookkit.repo import orgs, placements
from bookkit.services import renewals
from bookkit.tui.app import BookkitApp
from bookkit.tui.widgets.tables import ListTable

TODAY = date(2026, 8, 14)


def _cell(table: ListTable, row: int, col: int) -> str:
    return str(table.get_cell_at(Coordinate(row, col)))


def _drifting(conn) -> renewals.RenewalItem:
    """A renewal whose line end differs from its program period end — the
    whole point of the bug."""
    for item in renewals.upcoming(conn, TODAY, days=100000):
        if item.renewal_on and item.renewal_on != item.placement.period_to:
            return item
    raise AssertionError("seed has no placement whose line end drifts")


def _drifting_next_for_org(conn) -> renewals.RenewalItem:
    """Same, but reached through next_for_org — which is what Book and the
    account header render, and which can pick a DIFFERENT placement of the
    same account than upcoming() lists first."""
    for org in orgs.list_orgs(conn, kind="client"):
        item = renewals.next_for_org(conn, org.id, TODAY)
        if item and item.renewal_on and item.renewal_on != item.placement.period_to:
            return item
    raise AssertionError("no account whose NEXT renewal drifts from its period end")


# --- the four screens -------------------------------------------------------


async def test_today_counts_down_to_the_date_it_prints(snapshot_db: Path) -> None:
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        item = _drifting(app.conn)
        await pilot.press("t")
        await pilot.pause()
        table = app.screen.query_one("#renewals-table", ListTable)
        rows = [_cell(table, r, 0) for r in range(table.row_count)]
        assert item.renewal_on in rows
        assert item.placement.period_to not in rows


async def test_book_counts_down_to_the_date_it_prints(snapshot_db: Path) -> None:
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        item = _drifting_next_for_org(app.conn)
        await pilot.press("b")
        await pilot.pause()
        table = app.screen.query_one("#book-table", ListTable)
        names = [_cell(table, r, 1) for r in range(table.row_count)]
        row = names.index(item.org.name)
        assert _cell(table, row, 4) == item.renewal_on


async def test_the_account_header_counts_down_to_the_date_it_prints(
    snapshot_db: Path,
) -> None:
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        item = _drifting_next_for_org(app.conn)
        app.open_account(item.org.id)
        await pilot.pause()
        header = str(app.screen.query_one("#account-header").render())
        assert item.renewal_on in header
        assert item.placement.period_to not in header


async def test_the_calendar_shows_overdue_renewals(snapshot_db: Path) -> None:
    """The grid started at this month and silently dropped anything earlier,
    so all seven seeded overdue renewals were invisible on the one screen
    meant to make the year legible."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        overdue = [
            i for i in renewals.upcoming(app.conn, TODAY, days=120)
            if i.days_remaining < 0
        ]
        assert overdue, "seed has no overdue renewals to check"

        await pilot.press("c")
        await pilot.pause()
        table = app.screen.query_one("#calendar-table", ListTable)
        # A name alone proves nothing (the row was always created), and a
        # filled cell proves nothing either: the grid plotted period_to, so an
        # overdue renewal rendered as a comfortable FUTURE month with no
        # marker at all. The legend promises "◆ overdue" — assert it appears.
        flagged = {
            _cell(table, r, 0)
            for r in range(table.row_count)
            if any(
                "\u25c6" in _cell(table, r, c)
                for c in range(1, len(table.columns))
            )
        }
        missing = {i.org.name for i in overdue} - flagged
        assert not missing, f"overdue renewals not flagged in the calendar: {missing}"


# --- the book's premium column ---------------------------------------------


async def test_book_premium_is_the_accounts_bound_premium(
    snapshot_db: Path,
) -> None:
    """It printed whichever single placement renews next, whatever its status
    — so an account with $15.6M bound across two placements read $8M, and one
    with nothing bound read $900K. Neither could be reconciled with the
    Navigator's bound-only headline."""
    from bookkit.money import format_cents_compact

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        conn = app.conn
        target = None
        for org in orgs.list_orgs(conn, kind="client"):
            bound = [
                p for p in placements.for_org(conn, org.id) if p.status == "bound"
            ]
            if len(bound) > 1:
                target = (org, sum(p.total_premium or 0 for p in bound))
                break
        assert target, "seed has no account with two bound placements"
        org, expected = target

        await pilot.press("b")
        await pilot.pause()
        table = app.screen.query_one("#book-table", ListTable)
        names = [_cell(table, r, 1) for r in range(table.row_count)]
        row = names.index(org.name)
        assert _cell(table, row, 6) == format_cents_compact(expected)
