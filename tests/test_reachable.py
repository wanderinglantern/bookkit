"""Fields that exist, are correct, and never reach the screen.

Not "missing" — a different bug with a different fix. Every value asserted
here is computed, stored and handed to a widget already; what fails is the
FRAME. A column loses a width squeeze and is painted past the right-hand
edge; a label is derived from a table other than the one it sits over. Both
are invisible to a test that asserts on the data, which is exactly why they
shipped and why these tests render at a real terminal size and read the
painted rows back.
"""

from __future__ import annotations

from pathlib import Path

from test_layout import WIDE, _screen_rows

from bookkit.tui.app import BookkitApp
from bookkit.tui.widgets.tables import ListTable


def _painted(app: BookkitApp) -> str:
    return "\n".join(_screen_rows(app, WIDE))


# --- Today: the lines of cover are the mandatory context --------------------


async def test_todays_renewals_paint_the_lines_of_cover(snapshot_db: Path) -> None:
    """CLAUDE.md: attention tables show lines of cover, because "program name
    alone is not enough context". The pane declared seven columns into 66
    cells and painted four, so `lines` — the one field the rule names as
    mandatory — was the field the squeeze discarded.

    The row data has always been right; only the frame was wrong."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()

        table = app.screen.query_one("#renewals-table", ListTable)
        # the data is there — this is a width bug, not a data bug
        cover = {
            str(cell)
            for row in table.rows
            for cell in table.get_row(row)
            if "," in str(cell) and str(cell).replace(",", "").replace(" ", "").isalpha()
        }
        assert cover, "no seeded renewal carries lines of cover — fixture drifted"

        painted = _painted(app)
        for lines in cover:
            assert lines in painted, (
                f"{lines!r} is in the table and not on the screen: the renewals "
                "pane is 66 cells wide and its columns add up to more"
            )
        # the general form of the same bug: a column declared past the pane's
        # right edge is not "narrow", it is absent. Seven columns needed 105
        # cells of 66. When this fails, drop a column — do not widen the pane.
        assert table.virtual_size.width <= table.container_size.width, (
            f"the renewals pane needs {table.virtual_size.width} columns and has "
            f"{table.container_size.width} — whatever is on the right is being "
            "painted off the screen"
        )


async def test_todays_renewals_still_say_what_renews_without_lines(
    snapshot_db: Path,
) -> None:
    """The program name is not deleted, it is DEMOTED: it stands in, dimmed,
    for the rows with no projected line ends (most rows, until the towerkit
    files are linked). Dropping it outright would leave those rows saying
    only "something at this account renews"."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        painted = _painted(app)
        assert "Casualty" in painted, "unlinked renewals name nothing at all"


def test_a_renewal_with_neither_lines_nor_a_program_says_so() -> None:
    """The last column of the pane, and the one already at risk of clipping —
    so when it has nothing to print it must print the em dash, not nothing.
    The code this replaced said `item.lines or dash()` and got that half
    right; the rewrite kept the fallback chain and lost the dash, so a
    placement with an empty program_name rendered a blank cell, which reads as
    a rendering fault rather than as an absence. The dash is free."""
    from bookkit.models import Org, Placement
    from bookkit.services.renewals import RenewalItem
    from bookkit.tui.screens.today import _cover

    stamps = {"created_at": "2026-08-14T09:00:00+00:00",
              "updated_at": "2026-08-14T09:00:00+00:00"}
    org = Org(
        id="o1", ref="ACC-0001", kind="client", name="Nameless Co",
        status="active", **stamps,
    )
    placement = Placement(
        id="p1", ref="PLC-0001", org_id="o1", program_name="", status="bound",
        period_from="2026-01-01", period_to="2026-12-31", **stamps,
    )
    blank = RenewalItem(placement=placement, org=org, days_remaining=30, bucket="0-30")
    assert str(_cover(blank)) == "\u2014", repr(str(_cover(blank)))
    # the stand-in still wins when there IS one
    named = RenewalItem(
        placement=Placement(
            id="p2", ref="PLC-0002", org_id="o1", program_name="2025 Casualty Program",
            status="bound", period_from="2026-01-01", period_to="2026-12-31",
            **stamps,
        ),
        org=org, days_remaining=30, bucket="0-30",
    )
    assert str(_cover(named)) == "Casualty"


# --- the account Overview tab: the label describes what is under it ---------


async def test_the_overview_hint_is_not_empty_over_five_populated_tables(
    snapshot_db: Path,
) -> None:
    """The hint derived emptiness from TAB_TABLES — the table the CURSOR
    lands on — and the Overview tab stacks five. An account with team,
    contacts, interactions and opportunities but no open task got
    "empty — a adds the first row" printed over all of them."""
    from bookkit.repo import contacts as contacts_repo
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import tasks as tasks_repo

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        conn = app.conn
        populated = next(
            org
            for org in orgs_repo.list_orgs(conn, kind="client")
            if contacts_repo.for_org(conn, org.id)
            and not [t for t in tasks_repo.open_tasks(conn) if t.org_id == org.id]
        )
        app.open_account(populated.id)
        await pilot.pause()

        painted = _painted(app)
        assert "adds the first row" not in painted, (
            f"{populated.name} has contacts and interactions on the Overview "
            "tab and the hint line calls the tab empty"
        )
        # and the real hint — the keys that tab actually offers — is there
        assert "edit task/team/account" in painted


async def test_the_overview_hint_still_says_empty_when_it_really_is(
    snapshot_db: Path,
) -> None:
    """The empty state is not being deleted, it is being derived correctly:
    a brand-new account with nothing on any of the five tables must still be
    told how to put the first row in."""
    from bookkit.repo import orgs as orgs_repo

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        fresh = orgs_repo.create(app.conn, kind="client", name="Zeta Holdings")
        app.open_account(fresh.id)
        await pilot.pause()
        assert "adds the first row" in _painted(app)
