"""Market seats are correctable where they are read — the TUI backfill.

Phase 1 gave the web seat correction and removal; the TUI could bind a
market (via a submission) and never fix or remove one — a wrong share typed
in the terminal could only be corrected in the browser. `e` on a
carriers-table row now edits that seat, `D` removes it (confirm first), and
`D` on a placeholder row removes the unplaced layer (D2).
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Input, TabbedContent

from bookkit import db, sync
from bookkit.repo import placements as placements_repo
from bookkit.tui.app import BookkitApp
from bookkit.tui.screens.account import ConfirmProgramRemoval
from bookkit.tui.widgets.forms import FormModal
from bookkit.tui.widgets.tables import ListTable


async def _on_carriers_row(app, pilot, linked, want_carrier: bool):
    """Open the account, land on the placements tab, put the cursor on a
    carriers row — a seated one, or a placeholder ('to be placed') row."""
    app.open_account(linked.org_id)
    await pilot.pause()
    app.screen.query_one(TabbedContent).active = "tab-placements"
    await pilot.pause()
    table = app.screen.query_one("#placements-table", ListTable)
    table.move_cursor(row=table.get_row_index(linked.id))
    app.screen.show_placement(linked.id)
    await pilot.pause()
    carriers = app.screen.query_one("#carriers-table", ListTable)
    carriers.focus()
    for row in range(carriers.row_count):
        from textual.coordinate import Coordinate

        key = carriers.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value
        layer_id, carrier = app.screen._carrier_seats[str(key)]
        if want_carrier == (carrier is not None):
            carriers.move_cursor(row=row)
            return layer_id, carrier
    raise AssertionError(
        f"no carriers row with carrier={'set' if want_carrier else 'placeholder'}"
    )


async def test_e_on_a_seat_opens_the_correction_prefilled_right(snapshot_db: Path):
    """The share pre-fill must be the PERCENT — the web's version of this
    form fed percent into a bps formatter and pre-filled 0.4 for a 40% seat,
    so an unedited save cut the share 100x. Same trap, other surface."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        linked = placements_repo.all_linked(app.conn)[0]
        layer_id, carrier = await _on_carriers_row(app, pilot, linked, want_carrier=True)
        layer = next(
            ly for ly in sync.layer_details(app.conn, linked.id)
            if str(ly["id"]) == layer_id
        )
        seat = next(pt for pt in layer["participants"] if pt["carrier"] == carrier)

        await pilot.press("e")
        await pilot.pause()

        assert isinstance(app.screen, FormModal)
        assert app.screen.spec.title.startswith(f"correct {carrier}")
        share_input = next(
            w for w in app.screen.query(Input).results()
            if w.name == "share" or "share" in (w.id or "")
        )
        assert share_input.value == f"{seat['share_pct']:g}", (
            f"share pre-filled {share_input.value!r} for a {seat['share_pct']:g}% seat"
        )


async def test_d_on_a_seat_confirms_then_takes_the_market_off(snapshot_db: Path):
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        linked = placements_repo.all_linked(app.conn)[0]
        layer_id, carrier = await _on_carriers_row(app, pilot, linked, want_carrier=True)
        path = Path(str(linked.program_path))
        before = path.read_bytes()

        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmProgramRemoval)
        assert path.read_bytes() == before, "the confirm itself wrote"

        await pilot.press("y")
        await pilot.pause()

        layer = next(
            ly for ly in sync.layer_details(app.conn, linked.id)
            if str(ly["id"]) == layer_id
        )
        assert carrier not in [pt["carrier"] for pt in layer["participants"]]
        assert layer["name"], "the layer went with the seat"


async def test_d_on_a_placeholder_row_removes_the_unplaced_layer(snapshot_db: Path):
    conn = db.connect(snapshot_db)
    linked = placements_repo.all_linked(conn)[0]
    # an unplaced layer at the top of a line, so removing it strands nothing
    lines = sync.program_lines(conn, linked.id)
    line_id = lines[0][0]
    top = max(
        (ly["attach_cents"] + ly["limit_cents"])
        for ly in sync.layer_details(conn, linked.id)
        if line_id in ly["applies_to"]
    )
    assert sync.add_layer(
        conn, linked.id, "Doomed Excess", [line_id],
        attach_cents=top, limit_cents=5_000_000_00,
    ).ok
    conn.close()

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        linked = placements_repo.all_linked(app.conn)[0]
        await _on_carriers_row(app, pilot, linked, want_carrier=False)

        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmProgramRemoval)
        await pilot.press("y")
        await pilot.pause()

        names = [ly["name"] for ly in sync.layer_details(app.conn, linked.id)]
        assert "Doomed Excess" not in names
