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


# --- the overdue branch, which the seed cannot reach -------------------------
#
# Every test above walks the SEED. `_drifting_next_for_org` returns the first
# account whose next renewal drifts from its period end — and on the seed no
# such account is also OVERDUE, so the account header's `days_remaining < 0`
# branch was never entered by any of them. It kept printing period_to for four
# reviews after the `else` branch beside it was fixed, and the overdue branch
# is the one that renders in red.
#
# So the case is built, not found: a program period ending months from now,
# with a LINE whose policy already ran out.


def _overdue_and_drifting(tmp_path: Path):
    """A book with one account whose next renewal is OVERDUE by 7 days while
    its program period still has 131 days to run.

    Pre-fix this renders `renewal ◆ 2026-12-23 · 7d over` — a date four months
    in the FUTURE, in red, labelled overdue. That is the whole bug.
    """
    from towerkit.model import (
        Layer,
        Line,
        Participant,
        Period,
        Program,
        Retention,
        RetentionType,
        dump_program,
    )
    from towerkit.model import Placement as TkPlacement

    from bookkit import db as db_mod
    from bookkit import sync

    period_to = date(2026, 12, 23)      # 131 days AFTER TODAY
    line_end = date(2026, 8, 7)         # 7 days BEFORE TODAY
    assert line_end < TODAY < period_to

    program = Program(
        insured="Delta Marine Logistics, LLC",
        program="Marine Program",
        placement=TkPlacement.BOUND,
        period=Period(start=date(2026, 1, 1), end=period_to),
        lines=[Line(id="im", name="Inland Marine", abbr="IM")],
        layers=[
            Layer(
                id="primary-im", name="Primary IM", applies_to=["im"],
                attach=0, limit=5_000_000, premium=250_000,
                # the policy on this line expired while the PROGRAM runs on
                period=Period(start=date(2026, 1, 1), end=line_end),
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            )
        ],
        retentions=[
            Retention(applies_to=["im"], type=RetentionType.DEDUCTIBLE, amount=50_000)
        ],
    )
    programs = tmp_path / "programs"
    programs.mkdir(parents=True, exist_ok=True)
    dump_program(program, programs / "delta.json")

    path = tmp_path / "overdue.db"
    conn = db_mod.connect(path)
    org = orgs.create(
        conn, kind="client", name="Delta Marine Logistics, LLC", status="active"
    )
    diags = sync.confirm_link(conn, programs / "delta.json", org.id)
    assert diags.ok, [(d.code, d.message) for d in diags.errors]

    item = renewals.next_for_org(conn, org.id, TODAY)
    assert item is not None
    assert item.days_remaining < 0, "fixture must reach the OVERDUE branch"
    assert item.renewal_on == line_end.isoformat()
    assert item.placement.period_to == period_to.isoformat()
    assert item.renewal_on != item.placement.period_to, "fixture must also drift"
    conn.close()
    return path, org.id, item.renewal_on, item.placement.period_to


async def test_the_account_header_overdue_branch_prints_the_date_it_counts_to(
    tmp_path: Path, frozen_clock: date,
) -> None:
    """The survivor. tui/screens/account.py printed
    `placement.period_to · {-days_remaining}d over`, so a date 131 days in the
    future rendered red as '7d over'."""
    path, org_id, renewal_on, period_to = _overdue_and_drifting(tmp_path)
    app = BookkitApp(path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        app.open_account(org_id)
        await pilot.pause()
        header = str(app.screen.query_one("#account-header").render())
        assert "d over" in header, "fixture did not reach the overdue branch"
        assert renewal_on in header
        assert period_to not in header, (
            f"the overdue header printed the program period end {period_to} "
            f"beside a countdown measured to {renewal_on}"
        )


def test_bookctl_renewals_prints_the_date_it_counts_to(
    tmp_path: Path, frozen_clock: date, monkeypatch, capsys,
) -> None:
    """`bookctl renewals` — the fifth surface, and it was never swept."""
    from bookkit.cli import main

    path, _org_id, renewal_on, period_to = _overdue_and_drifting(tmp_path)
    monkeypatch.setenv("BOOKKIT_DB", str(path))
    assert main(["renewals", "--days", "120"]) == 0
    out = capsys.readouterr().out
    assert "Delta Marine" in out
    assert renewal_on in out
    assert period_to not in out


def test_bookctl_today_prints_the_date_it_counts_to(
    tmp_path: Path, frozen_clock: date, monkeypatch, capsys,
) -> None:
    """`bookctl today` — the sixth."""
    from bookkit.cli import main

    path, _org_id, renewal_on, period_to = _overdue_and_drifting(tmp_path)
    monkeypatch.setenv("BOOKKIT_DB", str(path))
    assert main(["today"]) == 0
    out = capsys.readouterr().out
    renewals_block = out.split("RENEWALS NEXT 120 DAYS", 1)[1].split("\n\n", 1)[0]
    assert "Delta Marine" in renewals_block
    assert renewal_on in renewals_block
    assert period_to not in renewals_block
