"""The Program tab: placements, their layers, and the markets on them.

Writes arrive in phase 2 (docs/superpowers/plans/2026-08-19-programs-on-the-web.md).
What this file asserts first is that the panel stops contradicting the badge
above it: the stub printed the addable-list empty state unconditionally while
the tab counted the placements it claimed did not exist, and an account with
two programs read as an account with none.

The `snapshot_db` fixture seeds a book AND projects real towerkit files, so
the layers asserted here are read off disk rather than invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import sync
from bookkit.money import format_cents
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    """An account that has at least one placement. base_url is loopback
    because web/origin.py refuses TestClient's default Host of "testserver" —
    exactly the forged name the guard exists to catch."""
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o for o in orgs.list_orgs(conn, kind="client") if placements.for_org(conn, o.id)
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _linked(conn, org):
    """This account's placements that actually have a program file."""
    from bookkit.repo import placements

    return [p for p in placements.for_org(conn, org.id) if p.program_path]


# --- the tab tells the truth --------------------------------------------------


def test_the_program_tab_lists_the_placements(app_and_org):
    client, org = app_and_org
    from bookkit.repo import placements

    page = client.get(f"/accounts/{org.ref}/program")

    assert page.status_code == 200
    for placement in placements.for_org(client.app.state.conn, org.id):
        assert placement.program_name in page.text
        assert placement.ref in page.text


def test_the_panel_never_contradicts_the_tab_badge(app_and_org):
    """The one assertion this whole task exists for."""
    client, org = app_and_org

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "empty — add the first row" not in page


def test_an_account_with_no_placements_says_so_honestly(snapshot_db: Path):
    from bookkit.repo import orgs

    app = create_app(snapshot_db)
    bare = orgs.create(app.state.conn, kind="client", name="No Programs Co")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        page = client.get(f"/accounts/{bare.ref}/program")

    assert page.status_code == 200
    assert "no programs on this account" in page.text


# --- the layers, which are the surface phase 2 edits ---------------------------


def test_every_layer_of_a_linked_program_is_listed(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    linked = _linked(conn, org)
    assert linked, "the seeded book has no placement with a program file"

    page = client.get(f"/accounts/{org.ref}/program").text

    for placement in linked:
        for layer in sync.layer_details(conn, placement.id):
            assert layer["name"] in page, f"{placement.ref} is missing layer {layer['name']}"


def test_the_markets_on_a_layer_are_named(app_and_org):
    """A tower without its carriers is a picture of capacity nobody is on.
    Phase 2 edits these; phase 1 has to show them."""
    client, org = app_and_org
    conn = client.app.state.conn

    page = client.get(f"/accounts/{org.ref}/program").text

    seen = 0
    for placement in _linked(conn, org):
        for layer in sync.layer_details(conn, placement.id):
            for participant in layer["participants"]:
                assert participant["carrier"] in page
                seen += 1
    assert seen, "no participants anywhere in the seeded book — test proves nothing"


def test_an_unplaced_layer_says_to_be_placed(app_and_org):
    """towerkit's own word for it. An empty participant list and a missing one
    are different facts: "nobody is on this layer" is the fact a reader most
    needs, and a blank cell asserts nothing."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    # ABOVE the existing tower. towerkit refuses an overlapping layer, which
    # is the validator doing its job — the first draft of this test attached
    # at $10M, inside the existing Umbrella, and was refused with
    # "OVERLAP Umbrella→Excess Test Layer at $27,000,000 vs $10,000,000".
    top = max(
        layer["attach_cents"] + layer["limit_cents"]
        for layer in sync.layer_details(conn, placement.id)
    )
    added = sync.add_layer(
        conn, placement.id, "Excess Test Layer", [], top, 5_000_000_00
    )
    assert added.ok, [d.message for d in added.errors]

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "To be placed" in page


def test_a_placement_with_no_file_says_so_rather_than_looking_empty(app_and_org):
    """Three different facts — no file linked, a file with no layers, layers
    present — and collapsing the first two is how the stub came to lie."""
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    placements.create(
        conn, org.id, "Unlinked Program", "2026-03-01", "2027-03-01",
        status="prospective",
    )

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "no program file linked" in page


# --- money reads as money -----------------------------------------------------


def test_layer_money_is_formatted_not_raw_cents(app_and_org):
    """A layer with a $5M limit must not render as 500000000.

    The first version of this asserted `str(cents) not in page or cents == 0`
    against the FIRST layer — which attaches at 0 in the seeded book, so the
    right-hand side was always true and the test passed with the formatter
    replaced by str(). Review caught it by mutation. It now checks a non-zero
    figure, in a named cell, and asserts the formatted form is present as well
    as the raw one absent.

    DISPLAY IS COMPACT AS OF D5 (2026-08-19): the cell shows "$5M", matching
    the tower drawing above it, and the editor pre-fills the exact figure —
    see test_money_editor_prefill_stays_exact for the other half."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    priced = next(
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["limit_cents"]
    )

    cell = _cell_text(
        client.get(f"/accounts/{org.ref}/program").text,
        placement, priced["id"], "limit_cents",
    )

    assert cell != str(priced["limit_cents"]), "raw cents rendered"
    from bookkit.money import format_cents_compact

    assert cell == format_cents_compact(priced["limit_cents"])


def test_money_editor_prefill_stays_exact(app_and_org):
    """The half of D5 that keeps the old invariant alive: the editor's
    pre-fill is the exact figure, because a compact string ("$50M") parses
    back lossily and an unedited save would destroy the odd dollars."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    priced = next(
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["limit_cents"]
    )

    editor = client.get(_cell(org, placement, priced, "limit_cents") + "/edit").text

    exact = format_cents(priced["limit_cents"]).lstrip("$")
    assert f'value="{exact}"' in editor


def test_a_primary_layer_attaching_at_zero_says_zero(app_and_org):
    """An em dash means UNRECORDED. A primary layer attaches at $0 and that is
    a fact about the tower — rendering it as a dash tells the reader the
    attachment is unknown when it is known and is zero.

    Compact display (D5) renders it "$0" — a figure, not a dash."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    primaries = [
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["attach_cents"] == 0
    ]
    assert primaries, "the seeded book has no ground-up layer — test proves nothing"

    page = client.get(f"/accounts/{org.ref}/program").text
    cell = _cell_text(page, placement, primaries[0]["id"], "attach_cents")

    assert cell == "$0", f"a $0 attachment rendered as {cell!r}"
    assert "—" not in cell, "the attachment cell reads as unrecorded"


# --- phase 2: the write seam --------------------------------------------------


def test_the_write_wrapper_refuses_with_the_diagnostics_intact():
    """A flat string is enough for an MCP client and not enough for the web.

    The route has to tell an ordinary validation refusal — re-render the cell
    with the message, keep the typed value — from a CONFLICT, which offers
    Reload / Overwrite / Keep editing. Only the diagnostics carry the code
    that distinguishes them, and `sync._mutate` is what puts it there."""
    from towerkit.validate import Diagnostics

    from bookkit.services.program_files import ProgramWriteRefused, raise_on_errors

    diags = Diagnostics()
    diags.error("conflict", "the file moved under this write")

    with pytest.raises(ProgramWriteRefused) as refused:
        raise_on_errors(diags)

    assert refused.value.diags is diags
    assert any(d.code == "conflict" for d in refused.value.diags.errors)
    assert "moved under" in str(refused.value)


def test_a_clean_write_returns_its_warnings_rather_than_raising():
    """Warnings ride along: an unplaced layer is a warning, not a refusal, and
    a write that refused on one would make a half-built tower unsaveable."""
    from towerkit.validate import Diagnostics

    from bookkit.services.program_files import raise_on_errors

    diags = Diagnostics()
    diags.warn("layer-unplaced", "Excess: 0% placed")

    assert raise_on_errors(diags) == ["Excess: 0% placed"]


def test_a_refusal_is_still_a_value_error():
    """The ordinary case has to keep working with no new code: ValueError is
    what open_batch already rolls back on, and what every web form already
    renders."""
    from bookkit.services.program_files import ProgramWriteRefused

    assert issubclass(ProgramWriteRefused, ValueError)


# --- phase 2: editing a layer where it is read --------------------------------


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, since="", limit=1)
    return found[0] if found else None


def _first_layer(conn, org):
    placement = _linked(conn, org)[0]
    return placement, sync.layer_details(conn, placement.id)[0]


def _cell_text(page: str, placement, layer_id: str, key: str) -> str:
    """The rendered text of one layer cell, found by its own action URL rather
    than by slicing near a name — a fixed-width slice missed the value and
    failed for the wrong reason once already."""
    import re

    action = f"/accounts/[^/]+/program/{placement.id}/layers/{layer_id}/cell/{key}"
    match = re.search(rf'data-cell-action="{action}".*?>(.*?)</td>', page, re.S)
    assert match, f"no {key} cell rendered for layer {layer_id}"
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def _cell(org, placement, layer, key):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer['id']}/cell/{key}"


def test_a_layer_cell_offers_an_editor(app_and_org):
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    editor = client.get(_cell(org, placement, layer, "name") + "/edit")

    assert editor.status_code == 200
    assert layer["name"] in editor.text
    assert "<input" in editor.text


def test_editing_a_layer_writes_the_file_and_leaves_a_revertible_batch(app_and_org):
    """THE SEAM, not the outcome. A response that merely looks right passes
    even when the route wrote outside a batch and left no pre-image — which is
    exactly what makes a program write unrevertible. So: the batch exists, it
    is this surface's, it carries the MCP server's own tool name, the snapshot
    is on disk, and the bytes changed."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    saved = client.post(_cell(org, placement, layer, "name"), data={"name": "Primary Casualty"})

    assert saved.status_code == 200
    assert path.read_bytes() != before, "the file on disk did not change"
    batch = _latest_batch(conn)
    assert batch is not None
    assert batch.source == "web"
    assert batch.tool == "program_layer_edit"
    snapshot = path.parent / ".mcp-snapshots" / f"{batch.ref}.json"
    assert snapshot.exists(), "no pre-image captured — this write cannot be reverted"
    assert snapshot.read_bytes() == before


def test_the_saved_value_comes_back_in_the_cell(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    saved = client.post(_cell(org, placement, layer, "name"), data={"name": "Renamed Layer"})

    assert "Renamed Layer" in saved.text
    assert sync.layer_details(conn, placement.id)[0]["name"] == "Renamed Layer"


def test_a_sub_dollar_premium_is_refused_not_rounded(app_and_org):
    """towerkit files carry whole dollars. Rounding here silently changes a
    client's premium; refusing says so, and keeps what was typed."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "1,234.56"}
    )

    assert refused.status_code == 200
    assert "1,234.56" in refused.text, "the typed value was not kept for correction"
    assert path.read_bytes() == before, "the file was written anyway"


def test_a_refused_layer_edit_leaves_no_batch_and_no_snapshot(app_and_org):
    """A refusal that logged an undo unit would offer to revert a write that
    never happened — and a refusal that left a snapshot would offer to restore
    a pre-image of nothing.

    The name promised the snapshot half and the body never checked it: moving
    `capture` above `raise_on_errors` in services/program_files.py left this
    green while every refused edit wrote a .mcp-snapshots file. Review found
    it by exactly that mutation."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    snapdir = Path(placement.program_path).parent / ".mcp-snapshots"
    before_batch = _latest_batch(conn)
    before_snaps = set(snapdir.glob("*.json")) if snapdir.exists() else set()

    client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "1,234.56"}
    )

    after = _latest_batch(conn)
    assert (after.ref if after else None) == (before_batch.ref if before_batch else None)
    after_snaps = set(snapdir.glob("*.json")) if snapdir.exists() else set()
    assert after_snaps == before_snaps, "a refused write left snapshot debris"


def test_a_layer_under_another_account_is_not_reachable(app_and_org):
    """Both ids are checked: the placement is this account's AND the layer is
    that placement's. Without the second, a layer could be edited under a
    placement it does not belong to."""
    client, org = app_and_org
    from bookkit.repo import orgs, placements

    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    stranger = orgs.create(conn, kind="client", name="Someone Else Ltd")
    # POINTED AT THE SAME FILE, so the layer id really does exist under their
    # placement too. Without that the route 404s because the layer is missing
    # rather than because it is not ours, and the test passes with the
    # ownership check deleted — which it did, until a mutation showed it
    # (2026-08-19). A guard test that cannot fail is not a guard.
    theirs = placements.create(
        conn, stranger.id, "Their Program", "2026-01-01", "2027-01-01",
        program_path=placement.program_path,
    )

    poked = client.post(
        f"/accounts/{org.ref}/program/{theirs.id}/layers/{layer['id']}/cell/name",
        data={"name": "not mine"},
    )

    assert poked.status_code == 404
    # and nothing was written to the file they share
    assert sync.layer_details(conn, placement.id)[0]["name"] != "not mine"


def test_a_derived_column_offers_no_editor(app_and_org):
    """signed_pct is the sum of the participants' shares. A cell that offered
    to edit it would write nothing and read as broken."""
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    assert client.get(_cell(org, placement, layer, "signed_pct") + "/edit").status_code == 404


# --- phase 2: adding a layer, and working the markets on one ------------------


def _markets(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/markets"


def test_adding_a_layer_appends_it_pending(app_and_org):
    """A new layer is created unplaced — markets join as they bind. towerkit's
    word for that state is 'To be placed'."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    line = _first_line(conn, placement)
    top = max(
        layer["attach_cents"] + layer["limit_cents"]
        for layer in sync.layer_details(conn, placement.id)
        if line in layer["applies_to"]
    )

    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "3rd Excess",
            "line": line,
            "attach_cents": str(top // 100),
            "limit_cents": "5,000,000",
            "premium_cents": "",
        },
    )

    assert added.status_code == 200
    names = [layer["name"] for layer in sync.layer_details(conn, placement.id)]
    assert "3rd Excess" in names
    fresh = next(
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["name"] == "3rd Excess"
    )
    assert fresh["participants"] == []
    batch = _latest_batch(conn)
    assert batch.source == "web" and batch.tool == "program_layer_add"


def test_a_layer_that_would_leave_a_gap_is_refused_and_says_so(app_and_org):
    """towerkit refuses a gap as firmly as an overlap, and the refusal names
    the layers it is between — that message is the whole value of not writing
    it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "Floating Excess",
            "line": _first_line(conn, placement),
            "attach_cents": "900,000,000",
            "limit_cents": "5,000,000",
            "premium_cents": "",
        },
    )

    assert refused.status_code == 200
    assert "GAP" in refused.text or "gap" in refused.text
    assert path.read_bytes() == before


def test_binding_a_market_onto_a_layer(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    # A layer of our own with nothing on it, so the share is ours to give. The
    # first seeded layer may be fully signed, and skipping on that would make
    # this test protect nothing on the day it mattered.
    top = max(
        ly["attach_cents"] + ly["limit_cents"]
        for ly in sync.layer_details(conn, placement.id)
    )
    assert sync.add_layer(
        conn, placement.id, "Bind Target", [], top, 5_000_000_00
    ).ok
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["name"] == "Bind Target"
    )

    bound = client.post(
        _markets(org, placement, layer["id"]),
        data={"carrier": "Berkshire", "share_pct": "40"},
    )

    assert bound.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert "Berkshire" in [p["carrier"] for p in fresh["participants"]]
    assert _latest_batch(conn).tool == "program_bind"


def test_over_signing_a_layer_is_refused_and_the_file_is_untouched(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        _markets(org, placement, layer["id"]),
        data={"carrier": "Overshare Re", "share_pct": "100"},
    )

    assert refused.status_code == 200
    assert path.read_bytes() == before
    assert "Overshare Re" not in path.read_text()


def test_a_markets_share_is_corrected_in_place(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    assert layer["participants"], "the first layer has no market to correct"
    was = layer["participants"][0]["share_pct"]

    saved = client.post(
        f"{_markets(org, placement, layer['id'])}/0/cell/share_pct",
        data={"share_pct": str(was / 2)},
    )

    assert saved.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert fresh["participants"][0]["share_pct"] == was / 2
    # a share moves the layer's signed % and can re-seat other rows, so the
    # cell comes back WITH the panel out of band, like a layer cell save
    assert 'hx-swap-oob="true"' in saved.text


def test_a_market_is_taken_off_a_layer_and_the_layer_survives(app_and_org):
    """Losing a layer because its last market fell away would destroy the
    tower's shape."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    assert layer["participants"], "the first layer has no market to remove"
    carrier = layer["participants"][0]["carrier"]

    removed = client.post(f"{_markets(org, placement, layer['id'])}/0/remove")

    assert removed.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert carrier not in [p["carrier"] for p in fresh["participants"]]
    assert fresh["name"] == layer["name"], "the layer went with the market"


def _first_seat(conn, org):
    """A (placement, layer, index, seat) with at least one market bound."""
    for placement in _linked(conn, org):
        for layer in sync.layer_details(conn, placement.id):
            if layer["participants"]:
                return placement, layer, 0, layer["participants"][0]
    raise AssertionError("the seeded book has no bound market anywhere")


def _market_cell(org, placement, layer_id, index, key):
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/layers/{layer_id}/markets/{index}/cell/{key}"
    )


def test_share_editor_prefills_the_actual_percent(app_and_org):
    """40% must pre-fill '40', not '0.4'. The old mini-form fed the seat's
    PERCENT into initial_text, whose share kind formats BPS — so a 40% seat
    pre-filled 0.4 and an unedited save would have cut the share 100x. The
    same silent-destruction class as the cents rule in CLAUDE.md."""
    client, org = app_and_org
    placement, layer, index, seat = _first_seat(client.app.state.conn, org)

    editor = client.get(
        _market_cell(org, placement, layer["id"], index, "share_pct") + "/edit"
    ).text

    assert f'value="{seat["share_pct"]:g}"' in editor


def test_carrier_editor_offers_existing_market_names(app_and_org):
    """Vocabulary completes from existing records (CLAUDE.md): freehand
    carrier spelling is how 'Zurich Insurance Group' vs 'Zurich' drift
    starts, and the TUI already wires Field.suggestions for this."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    from bookkit.repo import vocab

    editor = client.get(
        _market_cell(org, placement, layer["id"], index, "carrier") + "/edit"
    ).text

    names = vocab.market_names(conn)
    assert names, "the seeded book names no markets — test proves nothing"
    assert "<datalist" in editor
    assert names[0] in editor


def test_market_remove_asks_first_and_the_get_writes_nothing(app_and_org):
    """The remove control fetches an IN-PLACE confirm; only the confirm's
    POST writes. Contacts and interactions already confirm — a market seat
    is the same severity of removal."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    page = client.get(f"/accounts/{org.ref}/program").text
    base = _market_cell(org, placement, layer["id"], index, "carrier").rsplit(
        "/cell", 1
    )[0]
    assert f'hx-get="{base}/remove"' in page, "remove is not a confirm fetch"
    assert f'hx-post="{base}/remove"' not in page, "remove still writes on one click"

    confirm = client.get(f"{base}/remove")

    assert confirm.status_code == 200
    assert seat["carrier"] in confirm.text
    assert "the layer stays" in confirm.text
    assert path.read_bytes() == before, "the confirm GET wrote to the file"


def _first_line(conn, placement):
    lines = sync.program_lines(conn, placement.id)
    assert lines, f"{placement.ref} has no lines"
    return lines[0][0]


def _two_line_placement(client, org, tmp_path):
    """A linked placement whose program has TWO lines (gl + cy), so the
    applies-to choice is real."""
    from datetime import date

    from test_linking_flow import write_program
    from towerkit.model import Layer, Line, Participant, Period, Program
    from towerkit.model import Placement as TkPlacement

    conn = client.app.state.conn
    program = Program(
        insured=org.name,
        program="Two Line Program",
        placement=TkPlacement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[
            Line(id="gl", name="General Liability", abbr="GL"),
            Line(id="cy", name="Cyber", abbr="CY"),
        ],
        # equal tops on purpose: an "__all__" layer attaching at the shared
        # top is valid on both lines, so the test refuses only for the
        # reason under test
        layers=[
            Layer(id="primary-gl", name="Primary GL", applies_to=["gl"],
                  attach=0, limit=2_000_000, premium=900_000,
                  participants=[Participant(carrier="Zurich", share_bps=10_000)]),
            Layer(id="primary-cy", name="Primary Cyber", applies_to=["cy"],
                  attach=0, limit=2_000_000, premium=400_000, participants=[]),
        ],
    )
    path = write_program(tmp_path / "two-line.json", program)
    assert sync.confirm_link(conn, path, org.id).ok
    from bookkit.repo import placements as placements_repo

    return placements_repo.by_program_path(conn, str(path))


def test_the_layer_add_form_asks_which_lines(app_and_org, tmp_path):
    """The web used to pass line_ids=[] and towerkit silently defaulted to
    the FIRST line — same keystroke, different data per surface (F5). The
    TUI asks; now the web does too."""
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)

    form = client.get(f"/accounts/{org.ref}/program/{placement.id}/layers/new").text

    assert "<select" in form
    assert "General Liability" in form
    assert "Cyber" in form
    assert "all lines" in form


def test_an_added_layer_lands_on_the_chosen_line(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "1st Excess Cyber", "line": "cy", "attach_cents": "2,000,000",
              "limit_cents": "5,000,000", "premium_cents": ""},
    )

    assert added.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "1st Excess Cyber"
    )
    assert layer["applies_to"] == ["cy"], f"landed on {layer['applies_to']}"


def test_all_lines_means_all_lines(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Umbrella Everything", "line": "__all__",
              "attach_cents": "2,000,000", "limit_cents": "10,000,000",
              "premium_cents": ""},
    )

    assert added.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "Umbrella Everything"
    )
    assert sorted(layer["applies_to"]) == ["cy", "gl"]


def test_a_made_up_line_is_refused(app_and_org, tmp_path):
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Nowhere", "line": "marine", "attach_cents": "0",
              "limit_cents": "1,000,000", "premium_cents": ""},
    )

    assert refused.status_code == 200
    assert "Nowhere" not in [
        ly["name"] for ly in sync.layer_details(client.app.state.conn, placement.id)
    ]


def test_scaffold_honours_a_typed_destination(app_and_org, tmp_path):
    """The TUI's `t` lets you change where the file lands; the web confirm
    showed the path and could not (parity gap, phase 2)."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import placements as placements_repo

    bare = placements_repo.create(
        conn, org.id, "Scaffold Here", "2026-04-01", "2027-04-01"
    )
    custom = tmp_path / "elsewhere" / "custom-name.json"

    made = client.post(
        f"/accounts/{org.ref}/program/{bare.id}/scaffold", data={"path": str(custom)}
    )

    assert made.status_code == 200
    assert custom.exists(), "the typed destination was ignored"
    assert placements_repo.get(conn, bare.id).program_path == str(custom)


def test_the_scaffold_confirm_offers_the_path_as_an_input(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    _configure_roots(conn, tmp_path)
    from bookkit.repo import placements as placements_repo

    bare = placements_repo.create(
        conn, org.id, "Input Check", "2026-04-01", "2027-04-01"
    )

    confirm = client.get(f"/accounts/{org.ref}/program/{bare.id}/scaffold").text

    assert 'name="path"' in confirm


def _submission_url(org, placement):
    return f"/accounts/{org.ref}/program/{placement.id}/submissions"


def test_a_submission_is_sent_from_the_program_section(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import submissions as submissions_repo

    market = orgs_repo.list_orgs(conn, kind="market")[0]

    page = client.get(f"/accounts/{org.ref}/program").text
    assert f'hx-get="{_submission_url(org, placement)}/new"' in page

    form = client.get(f"{_submission_url(org, placement)}/new").text
    assert "<select" in form and market.name in form

    sent = client.post(
        _submission_url(org, placement),
        data={"market_org_id": market.id, "underwriter_contact_id": "",
              "sent_on": "2026-08-19", "notes": ""},
    )

    # 204 + HX-Redirect, the same shape the revert POST uses: htmx follows
    # the header; there is no body to swap
    assert sent.status_code == 204
    subs = [
        sub for sub in submissions_repo.for_placement(conn, placement.id)
        if sub.market_org_id == market.id
    ]
    assert subs, "no submission landed on the placement"
    # success redirects to where the submission is VISIBLE — the pipeline tab
    assert sent.headers.get("HX-Redirect", "").endswith(f"/accounts/{org.ref}/pipeline")


def test_a_refused_submission_keeps_the_typed_notes(app_and_org):
    client, org = app_and_org
    placement = _linked(client.app.state.conn, org)[0]

    refused = client.post(
        _submission_url(org, placement),
        data={"market_org_id": "", "underwriter_contact_id": "",
              "sent_on": "2026-08-19", "notes": "half-typed context"},
    ).text

    assert "half-typed context" in refused
    assert "required" in refused


def _layer_base(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}"


def test_the_details_row_offers_applies_to_chips(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    layer = sync.layer_details(conn, placement.id)[0]

    row = client.get(_details_url(org, placement, layer["id"])).text

    for _line_id, name in sync.program_lines(conn, placement.id):
        assert name in row, f"line {name} not offered"
    assert f'hx-post="{_layer_base(org, placement, layer["id"])}/applies-to"' in row


def test_toggling_a_line_on_rescopes_the_layer(app_and_org, tmp_path):
    """First caller ever for sync.set_applies_to — dead code since the sync
    layer was built. Valid move: an excess spanning both equal-top primaries
    narrows to one, then widens back."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Umbrella Both", "line": "__all__", "attach_cents": "2,000,000",
              "limit_cents": "5,000,000", "premium_cents": ""},
    )
    assert added.status_code == 200
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "Umbrella Both"
    )

    narrowed = client.post(
        f"{_layer_base(org, placement, layer_id)}/applies-to", data={"line": "cy"}
    )

    assert narrowed.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer_id
    )
    assert layer["applies_to"] == ["gl"], f"toggle off left {layer['applies_to']}"

    widened = client.post(
        f"{_layer_base(org, placement, layer_id)}/applies-to", data={"line": "cy"}
    )
    assert widened.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer_id
    )
    assert sorted(layer["applies_to"]) == ["cy", "gl"]


def test_toggling_off_the_last_line_is_refused_with_the_file_untouched(
    app_and_org, tmp_path
):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["applies_to"] == ["gl"]
    )
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        f"{_layer_base(org, placement, layer['id'])}/applies-to", data={"line": "gl"}
    )

    assert refused.status_code == 200
    assert path.read_bytes() == before
    assert "cover" in refused.text or "line" in refused.text


def test_statutory_asks_first_then_replaces_the_limit_with_the_word(
    app_and_org, tmp_path
):
    """Grant, 2026-08-19: statutory was modelled, rendered and never
    changeable from the browser — 'fully built but not accessible'."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    layer = sync.layer_details(conn, placement.id)[0]
    path = Path(placement.program_path)
    before = path.read_bytes()

    row = client.get(_details_url(org, placement, layer["id"])).text
    assert f'hx-get="{_layer_base(org, placement, layer["id"])}/statutory"' in row

    confirm = client.get(f"{_layer_base(org, placement, layer['id'])}/statutory")
    assert confirm.status_code == 200
    assert "statutory" in confirm.text
    assert path.read_bytes() == before, "the confirm GET wrote"

    marked = client.post(
        f"{_layer_base(org, placement, layer['id'])}/statutory",
        data={"statutory": "true"},
    )
    assert marked.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert fresh["statutory"] is True
    assert ">statutory<" in marked.text, "the limit column does not say the word"


def test_leaving_statutory_requires_the_replacing_limit(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    layer = sync.layer_details(conn, placement.id)[0]
    assert client.post(
        f"{_layer_base(org, placement, layer['id'])}/statutory",
        data={"statutory": "true"},
    ).status_code == 200

    restored = client.post(
        f"{_layer_base(org, placement, layer['id'])}/statutory",
        data={"statutory": "false", "limit": "3,000,000"},
    )

    assert restored.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert fresh["statutory"] is False
    assert fresh["limit_cents"] == 3_000_000_00


def test_follows_underlying_toggles_from_the_details_row(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "GL Excess", "line": "gl", "attach_cents": "2,000,000",
              "limit_cents": "3,000,000", "premium_cents": ""},
    )
    assert added.status_code == 200
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "GL Excess"
    )

    row = client.get(_details_url(org, placement, layer_id)).text
    assert f'hx-post="{_layer_base(org, placement, layer_id)}/follows"' in row

    toggled = client.post(
        f"{_layer_base(org, placement, layer_id)}/follows", data={"follows": "true"}
    )

    assert toggled.status_code == 200
    from towerkit.model import load_program

    layer = next(
        ly for ly in load_program(Path(placement.program_path)).layers
        if ly.id == layer_id
    )
    assert layer.follows_underlying is True
    # the returned row must SHOW the state, not just write it — the chip
    # rendered permanently-off until layer_details carried the flag
    assert "is-on" in toggled.text


def _lines_base(org, placement):
    return f"/accounts/{org.ref}/program/{placement.id}/lines"


def test_the_lines_strip_renders_every_line_as_a_cell(app_and_org, tmp_path):
    """D1: the browser can finally NAME the cover. A scaffolded program was
    stuck on 'Coverage TBD' forever because no web control could touch a
    line (review finding F4)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    page = client.get(f"/accounts/{org.ref}/program").text

    for line_id, name in sync.program_lines(conn, placement.id):
        assert (
            f'data-cell-action="{_lines_base(org, placement)}/{line_id}/cell/name"'
            in page
        ), f"line {name} is not an editable cell"
    assert f'hx-get="{_lines_base(org, placement)}/new"' in page, "no + line control"


def test_renaming_a_line_cascades_and_rerenders_the_panel(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    saved = client.post(
        f"{_lines_base(org, placement)}/cy/cell/name", data={"name": "Cyber Liability"}
    )

    assert saved.status_code == 200
    lines = dict(sync.program_lines(conn, placement.id))
    assert "Cyber Liability" in lines.values()
    assert "cy" not in lines, "the id did not follow the name"
    new_id = next(lid for lid, nm in lines.items() if nm == "Cyber Liability")
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "Primary Cyber"
    )
    assert new_id in layer["applies_to"], "the cascade left the layer stranded"
    assert 'hx-swap-oob="true"' in saved.text or 'id="program-' in saved.text


def test_line_remove_asks_first_naming_what_dies_with_it(app_and_org, tmp_path):
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)
    path = Path(placement.program_path)
    before = path.read_bytes()

    confirm = client.get(f"{_lines_base(org, placement)}/cy/remove")

    assert confirm.status_code == 200
    assert "Primary Cyber" in confirm.text, "the confirm hides the dying layer"
    assert path.read_bytes() == before, "the confirm GET wrote"

    removed = client.post(f"{_lines_base(org, placement)}/cy/remove")

    assert removed.status_code == 200
    conn = client.app.state.conn
    assert "cy" not in dict(sync.program_lines(conn, placement.id))
    assert "Primary Cyber" not in [
        ly["name"] for ly in sync.layer_details(conn, placement.id)
    ]


def test_removing_the_last_line_is_refused_in_place(app_and_org, tmp_path):
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)
    assert client.post(f"{_lines_base(org, placement)}/cy/remove").status_code == 200
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(f"{_lines_base(org, placement)}/gl/remove")

    assert refused.status_code == 200
    assert "only line" in refused.text
    assert path.read_bytes() == before


def test_adding_a_line_lands_with_a_pending_layer(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    form = client.get(f"{_lines_base(org, placement)}/new").text
    assert 'name="name"' in form

    added = client.post(
        f"{_lines_base(org, placement)}", data={"name": "Marine Cargo"}
    )

    assert added.status_code == 200
    lines = dict(sync.program_lines(conn, placement.id))
    assert "Marine Cargo" in lines.values()
    new_id = next(lid for lid, nm in lines.items() if nm == "Marine Cargo")
    covering = [
        ly for ly in sync.layer_details(conn, placement.id)
        if new_id in ly["applies_to"]
    ]
    assert covering, "the new line arrived empty — the validator forbids that"


def _renew_url(org, placement):
    return f"/accounts/{org.ref}/program/{placement.id}/renew"


def test_renew_asks_first_and_names_the_consequences(app_and_org):
    """The confirm writes nothing and says what renew DOES: next period
    dates, and for a linked placement the towerkit file cloned and linked at
    birth. (The account header's Renew button stayed unrendered under D4
    because it names no target; this control is placement-scoped.)"""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    from bookkit.repo import placements as placements_repo

    count_before = len(placements_repo.for_org(conn, org.id))

    page = client.get(f"/accounts/{org.ref}/program").text
    assert f'hx-get="{_renew_url(org, placement)}"' in page, "no renew control"

    confirm = client.get(_renew_url(org, placement))

    assert confirm.status_code == 200
    assert "cloned" in confirm.text or "clone" in confirm.text
    assert len(placements_repo.for_org(conn, org.id)) == count_before, (
        "the confirm GET created something"
    )


def test_renew_rolls_the_placement_and_clones_the_file(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    from bookkit.repo import placements as placements_repo

    before_ids = {p.id for p in placements_repo.for_org(conn, org.id)}

    renewed = client.post(_renew_url(org, placement))

    assert renewed.status_code == 200
    new = [p for p in placements_repo.for_org(conn, org.id) if p.id not in before_ids]
    assert len(new) == 1, "renew created no placement"
    assert new[0].period_from > placement.period_from
    assert new[0].program_path, "the renewal was not linked to a cloned file"
    assert Path(new[0].program_path).exists()
    assert 'id="programs-panel"' in renewed.text, "the new program is not shown"


def test_a_refused_renew_keeps_the_panel(app_and_org):
    """Renewing twice collides on the cloned file name; the second attempt
    must refuse with the panel intact and the message in its error slot."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    assert client.post(_renew_url(org, placement)).status_code == 200
    refused = client.post(_renew_url(org, placement))

    assert refused.status_code == 200
    assert 'id="programs-panel"' in refused.text
    assert "already exists" in refused.text


def _layer_remove_url(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/remove"


def test_layer_remove_asks_first_naming_the_seats(app_and_org):
    """D2. The confirm writes nothing and names the markets going with the
    layer — the blast radius is the fact a person needs before answering."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    details = client.get(_details_url(org, placement, layer["id"])).text
    assert f'hx-get="{_layer_remove_url(org, placement, layer["id"])}"' in details

    confirm = client.get(_layer_remove_url(org, placement, layer["id"]))

    assert confirm.status_code == 200
    assert layer["name"] in confirm.text
    assert seat["carrier"] in confirm.text, "the confirm hides the seats going with it"
    assert path.read_bytes() == before, "the confirm GET wrote to the file"


def test_a_removed_layer_is_gone_seats_and_all(app_and_org):
    """A seated EXCESS layer: removing a line's only layer is refused by
    towerkit (line-empty), so the doomed layer must not be a lone primary."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    doomed = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["participants"] and ly["attach_cents"] > 0
    )

    removed = client.post(_layer_remove_url(org, placement, doomed["id"]))

    assert removed.status_code == 200
    fresh = sync.layer_details(conn, placement.id)
    assert doomed["id"] not in [ly["id"] for ly in fresh]
    assert 'id="programs-panel"' in removed.text or 'hx-swap-oob' in removed.text


def test_a_refused_layer_removal_answers_in_place(app_and_org, tmp_path):
    """Removing a middle layer strands the one above; towerkit refuses and
    the refusal must land where the question was asked, file untouched."""
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)
    for name, attach in (("1st Excess", "2,000,000"), ("2nd Excess", "7,000,000")):
        added = client.post(
            f"/accounts/{org.ref}/program/{placement.id}/layers",
            data={"name": name, "line": "gl", "attach_cents": attach,
                  "limit_cents": "5,000,000", "premium_cents": ""},
        )
        assert added.status_code == 200
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(_layer_remove_url(org, placement, "1st-excess"))

    assert refused.status_code == 200
    assert path.read_bytes() == before
    assert "gap" in refused.text.lower()


def _placement_cell(org, placement, key):
    return f"/accounts/{org.ref}/program/{placement.id}/cell/{key}"


def test_placement_header_facts_are_cells(app_and_org):
    """Name, period, status and commission were static text beside editable
    layer cells (the web could not edit a placement's own facts at all —
    parity gap, phase 2)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    page = client.get(f"/accounts/{org.ref}/program").text

    for key in ("program_name", "period_from", "period_to", "status", "commission_bps"):
        assert f'data-cell-action="{_placement_cell(org, placement, key)}"' in page, key


def test_a_placement_name_saves_through_the_file(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    path = Path(placement.program_path)

    saved = client.post(
        _placement_cell(org, placement, "program_name"),
        data={"program_name": "Renamed From The Header"},
    )

    assert saved.status_code == 200
    assert "Renamed From The Header" in path.read_text()
    from bookkit.repo import placements as placements_repo

    assert (
        placements_repo.get(conn, placement.id).program_name
        == "Renamed From The Header"
    )
    assert 'hx-swap-oob="true"' in saved.text, "no panel refresh with the cell"


def test_a_placement_status_saves_to_the_row_only(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    path = Path(placement.program_path)
    sha_before = sync.file_sha256(path)
    new_status = "quoted" if placement.status != "quoted" else "submitted"

    saved = client.post(
        _placement_cell(org, placement, "status"), data={"status": new_status}
    )

    assert saved.status_code == 200
    from bookkit.repo import placements as placements_repo

    assert placements_repo.get(conn, placement.id).status == new_status
    assert sync.file_sha256(path) == sha_before, "a book-owned edit wrote the file"


def test_a_refused_placement_date_keeps_the_editor_and_the_file(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        _placement_cell(org, placement, "period_to"), data={"period_to": "1999-01-01"}
    )

    assert refused.status_code == 200
    assert path.read_bytes() == before
    assert "cell-editing" in refused.text, "the refusal did not keep the editor open"
    assert 'value="1999-01-01"' in refused.text


def test_the_status_editor_is_a_select_of_the_real_statuses(app_and_org):
    client, org = app_and_org
    placement = _linked(client.app.state.conn, org)[0]

    editor = client.get(_placement_cell(org, placement, "status") + "/edit").text

    assert "<select" in editor
    for status in ("prospective", "submitted", "quoted", "bound", "lapsed"):
        assert status in editor


def _details_url(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/details"


def test_layer_details_row_carries_the_three_hidden_fields(app_and_org):
    """policy number and the policy dates were editable by URL and invisible
    in the UI — the exact 'route nothing can reach' smell the 2026-08-19
    review round already caught once on the share edit (F6)."""
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    row = client.get(_details_url(org, placement, layer["id"])).text

    for key in ("policy_number", "period_from", "period_to"):
        assert f'data-field="{key}"' in row, f"{key} still unreachable"
    assert row.lstrip().startswith("<tr"), "the details fragment is not a row"


def test_every_layer_row_offers_its_details(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    page = client.get(f"/accounts/{org.ref}/program").text

    for layer in sync.layer_details(conn, placement.id):
        assert f'hx-get="{_details_url(org, placement, layer["id"])}"' in page


def test_a_detail_cell_saves_as_a_span_not_a_td(app_and_org):
    """The details row's cells live in a <td>, so their own element is a
    <span> — a <td> swapped back in its place would be dropped outright by
    the HTML parser (no table-row ancestor at the swap point), taking the
    saved value's display with it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    saved = client.post(
        _cell(org, placement, layer, "policy_number"), data={"policy_number": "POL-777"}
    )

    assert saved.status_code == 200
    assert saved.text.lstrip().startswith("<span"), "detail cell came back as a td"
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert fresh["policy_number"] == "POL-777"


def _markets_base(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/markets"


def test_market_add_opens_in_the_row_not_a_form_host(app_and_org):
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    page = client.get(f"/accounts/{org.ref}/program").text

    base = _markets_base(org, placement, layer["id"])
    assert f'hx-get="{base}/new"' in page
    assert 'hx-target="closest .market-add"' in page, "+ market still posts elsewhere"

    form = client.get(f"{base}/new").text
    assert 'class="market-add"' in form
    assert "<datalist" in form, "the carrier input offers no completion"


def test_market_add_cancel_restores_the_button(app_and_org):
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    button = client.get(f"{_markets_base(org, placement, layer['id'])}/button")

    assert button.status_code == 200
    assert "+ market" in button.text


def test_a_refused_market_add_keeps_the_typed_carrier_in_place(app_and_org):
    """Commit in place, at the anchor: the refusal re-renders the inline form
    with the input intact — never a panel swap, never a fragment somewhere
    else on the page."""
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    refused = client.post(
        _markets_base(org, placement, layer["id"]),
        data={"carrier": "Half Typed Re", "share_pct": ""},
    ).text

    assert 'value="Half Typed Re"' in refused
    assert "share is required" in refused
    assert f'id="program-{placement.id}"' not in refused, "the refusal swapped a panel"


def test_market_keep_restores_the_chip(app_and_org):
    client, org = app_and_org
    placement, layer, index, seat = _first_seat(client.app.state.conn, org)
    base = _market_cell(org, placement, layer["id"], index, "carrier").rsplit(
        "/cell", 1
    )[0]

    chip = client.get(base)

    assert chip.status_code == 200
    assert seat["carrier"] in chip.text
    assert f'data-cell-action="{base}/cell/carrier"' in chip.text


# --- what the review caught -----------------------------------------------
#
# Six defects, three of which the tests above were too weak to see. Each one
# gets an assertion here rather than a comment.


def test_a_refused_add_keeps_the_panel_and_the_typed_values(app_and_org):
    """The refusal used to be a bare message, and every control that could
    trigger it swapped the whole <section class="program"> — so a refused add
    deleted the layers table, the add controls and the panel's own id, leaving
    the placement unusable until a full page reload."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "Floating Excess",
            "line": _first_line(conn, placement),
            "attach_cents": "900,000,000",
            "limit_cents": "5,000,000",
            "premium_cents": "",
        },
    )

    assert refused.status_code == 200
    assert "<form" in refused.text, "the form did not come back"
    assert "Floating Excess" in refused.text, "the typed name was thrown away"
    assert "900,000,000" in refused.text, "the typed attachment was thrown away"


def test_the_add_form_posts_into_the_form_host_not_over_the_panel(app_and_org):
    """The swap target IS the bug. A form that targets the panel replaces the
    panel with whatever comes back, and a refusal is not a panel."""
    client, org = app_and_org
    placement, _ = _first_layer(client.app.state.conn, org)

    form = client.get(f"/accounts/{org.ref}/program/{placement.id}/layers/new").text

    assert 'hx-target="closest .form-host"' in form
    assert 'hx-swap="innerHTML"' in form
    assert "closest .program" not in form


def test_a_blank_attachment_is_refused_in_the_broker_s_language(app_and_org):
    """It used to reach sync.add_layer as None and come back as
    "unsupported operand type(s) for %: 'NoneType' and 'int'"."""
    client, org = app_and_org
    placement, _ = _first_layer(client.app.state.conn, org)

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "No Money", "line": _first_line(client.app.state.conn, placement),
              "attach_cents": "", "limit_cents": "", "premium_cents": ""},
    )

    assert "attaches at is required" in refused.text
    assert "NoneType" not in refused.text


def test_a_blank_share_is_refused_in_the_broker_s_language(app_and_org):
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    refused = client.post(
        _markets(org, placement, layer["id"]), data={"carrier": "Nobody Re", "share_pct": ""}
    )

    assert "share is required" in refused.text


def test_a_placement_with_no_file_says_so_rather_than_an_errno(app_and_org):
    """Path(str(None)) is the literal path "None", so the first thing a broker
    saw was "[Errno 2] No such file or directory: 'None'"."""
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    bare = placements.create(
        conn, org.id, "Unlinked Program", "2026-03-01", "2027-03-01",
    )

    refused = client.post(
        f"/accounts/{org.ref}/program/{bare.id}/layers",
        data={"name": "Nowhere", "attach_cents": "0", "limit_cents": "1,000,000",
              "premium_cents": ""},
    )

    assert "no program file linked" in refused.text
    assert "Errno" not in refused.text


def test_a_market_can_be_reached_for_editing_from_the_page(app_and_org):
    """market_cell_save existed and nothing rendered a way to it: the share
    test passed by POSTing the URL directly, which is not evidence a broker
    can correct a share. Markets ride the CELL contract now (F1): the way in
    is a data-cell-action on the chip itself, same as a layer cell."""
    import re

    client, org = app_and_org
    placement, _ = _first_layer(client.app.state.conn, org)

    page = client.get(f"/accounts/{org.ref}/program").text

    assert re.search(
        r'data-cell-action="[^"]*markets/\d+/cell/share_pct"', page
    ), "no way in from the page"
    assert re.search(r'data-cell-action="[^"]*markets/\d+/cell/carrier"', page)


def test_a_layer_edit_refreshes_the_rows_it_moved(app_and_org):
    """write_through heals follows-underlying layers, so a limit change re-seats
    OTHER rows. Returning one cell left them showing the pre-write number — a
    tower on screen that does not exist in the file."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    saved = client.post(
        _cell(org, placement, layer, "name"), data={"name": "Refreshed Layer"}
    )

    assert 'hx-swap-oob="true"' in saved.text, "no panel refresh came back with the cell"


# --- phase 3: creating a program ----------------------------------------------


def test_a_new_placement_is_created_from_the_tab(app_and_org):
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    before = {p.id for p in placements.for_org(conn, org.id)}

    created = client.post(
        f"/accounts/{org.ref}/program/placements",
        data={
            "program_name": "2027 Casualty Program",
            "period_from": "2027-01-01",
            "period_to": "2028-01-01",
            "status": "prospective",
            "total_premium": "",
            "total_limit": "",
            "commission_bps": "",
        },
    )

    assert created.status_code == 200
    fresh = [p for p in placements.for_org(conn, org.id) if p.id not in before]
    assert [p.program_name for p in fresh] == ["2027 Casualty Program"]
    assert _latest_batch(conn).source == "web"


def test_a_placement_with_no_period_is_refused_with_the_form_intact(app_and_org):
    client, org = app_and_org

    refused = client.post(
        f"/accounts/{org.ref}/program/placements",
        data={"program_name": "Half Typed", "period_from": "", "period_to": "",
              "status": "prospective"},
    )

    assert refused.status_code == 200
    assert "Half Typed" in refused.text, "the typed name was thrown away"
    assert "required" in refused.text


def test_the_scaffold_confirm_shows_where_the_file_goes_and_writes_nothing(app_and_org, tmp_path):
    """Creating a file is exactly the kind of thing that gets a plan first."""
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    _configure_roots(conn, tmp_path)
    bare = placements.create(
        conn, org.id, "Needs A File", "2026-05-01", "2027-05-01",
    )

    confirm = client.get(f"/accounts/{org.ref}/program/{bare.id}/scaffold")

    assert confirm.status_code == 200
    assert ".json" in confirm.text, "the plan does not say where the file goes"
    assert placements.get(conn, bare.id).program_path is None
    # the destination NAMED in the plan, not "any json under tmp_path" — the
    # snapshot_db fixture projects the seeded programs into that same tree, so
    # the broad glob failed on files this test never touched
    from bookkit.web.routes.program import _scaffold_destination

    assert not _scaffold_destination(conn, org, bare).exists()


def test_scaffolding_writes_the_file_and_links_it(app_and_org, tmp_path):
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    _configure_roots(conn, tmp_path)
    bare = placements.create(
        conn, org.id, "Needs A File", "2026-05-01", "2027-05-01",
    )

    made = client.post(f"/accounts/{org.ref}/program/{bare.id}/scaffold")

    assert made.status_code == 200
    linked = placements.get(conn, bare.id)
    assert linked.program_path, "the placement was not linked to its new file"
    assert Path(linked.program_path).exists()
    assert sync.layer_details(conn, bare.id), "the scaffold carries no layer"


def test_scaffolding_a_placement_that_already_has_a_file_is_refused(app_and_org, tmp_path):
    """And the refusal NAMES the file, so the answer is actionable rather than
    'no'."""
    client, org = app_and_org

    conn = client.app.state.conn
    _configure_roots(conn, tmp_path)
    placement, _ = _first_layer(conn, org)

    refused = client.post(f"/accounts/{org.ref}/program/{placement.id}/scaffold")

    assert refused.status_code == 200
    assert Path(placement.program_path).name in refused.text


def test_scaffolding_with_no_program_root_configured_points_at_the_setting(app_and_org):
    client, org = app_and_org
    from bookkit.repo import placements, settings

    conn = client.app.state.conn
    settings.set_program_roots(conn, [])
    bare = placements.create(
        conn, org.id, "Needs A File", "2026-05-01", "2027-05-01",
    )

    refused = client.post(f"/accounts/{org.ref}/program/{bare.id}/scaffold")

    assert refused.status_code == 200
    assert "program file location" in refused.text
    assert placements.get(conn, bare.id).program_path is None


def _configure_roots(conn, tmp_path):
    from bookkit.repo import settings

    root = tmp_path / "programs"
    root.mkdir(exist_ok=True)
    settings.set_program_roots(conn, [str(root)])
    return root


# --- phase 4: the drawn tower -------------------------------------------------


def test_the_page_prints_no_tower_string_the_renderer_did_not_choose(app_and_org):
    """R66 at the bookkit end.

    towerkit's own suite proves the renderer quotes labels.py and composes
    nothing. This proves bookkit PRINTS what the renderer handed it — the other
    half of the same rule, at the other side of the seam. What it deliberately
    does NOT do is compare the panel to the export: they are allowed to fit
    text differently, and a test that compared them would fail on a legitimate
    wrap and teach everyone to weaken it."""
    from pathlib import Path as _Path

    from towerkit.model import load_program

    from bookkit.web.tower import panel

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    built = panel(load_program(_Path(placement.program_path)))
    assert built["blocks"], "the seeded program draws no blocks — test proves nothing"

    page = client.get(f"/accounts/{org.ref}/program").text

    for block in built["blocks"]:
        for line in block["lines"]:
            assert line in page, f"the panel dropped a line the renderer chose: {line!r}"


def test_the_tower_is_drawn_for_a_linked_placement(app_and_org):
    client, org = app_and_org

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "tower-chart" in page
    assert "tower-block" in page


def test_a_placement_with_no_file_draws_no_tower(app_and_org):
    """None and an empty tower are different facts."""
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    placements.create(conn, org.id, "Unlinked", "2026-03-01", "2027-03-01")

    page = client.get(f"/accounts/{org.ref}/program").text
    after = page.split("Unlinked", 1)[1]

    assert "no program file linked" in after


def test_the_not_to_scale_caveat_is_printed(app_and_org):
    """The vertical scale is compressed so a $2M primary and a $50M excess can
    share one picture. Dropping the caveat leaves the drawing asserting a
    linear scale it does not have."""
    from pathlib import Path as _Path

    from towerkit.model import load_program

    from bookkit.web.tower import panel

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    caveat = panel(load_program(_Path(placement.program_path)))["caveat"]
    assert caveat, "the seeded program is drawn to scale — test proves nothing"

    assert caveat in client.get(f"/accounts/{org.ref}/program").text


# --- phase 5: the file moved under this write ---------------------------------


def _touch_out_of_band(placement) -> str:
    """Simulate towerkit's editor (or an MCP call) writing the file while a
    browser tab is open on it. Returns the new layer name so a test can prove
    it SURVIVED an overwrite rather than being clobbered."""
    path = Path(placement.program_path)
    text = path.read_text()
    marker = "Touched Elsewhere"
    layers = sync.layer_details.__module__  # keep the import honest
    del layers
    import json

    data = json.loads(text)
    data["layers"][-1]["name"] = marker
    path.write_text(json.dumps(data, indent=2) + "\n")
    return marker


def test_a_conflict_offers_three_ways_out_rather_than_an_error(app_and_org):
    """The file moved under this write. That is not the same as a value the
    validator refused, and answering it with the same one-line message leaves
    the user with no way forward except retyping into a form that will refuse
    again."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)

    refused = client.post(
        _cell(org, placement, layer, "name"), data={"name": "My Edit"}
    )

    assert refused.status_code == 200
    body = refused.text.lower()
    assert "reload" in body
    assert "overwrite" in body
    assert "keep editing" in body


def test_a_conflict_on_a_detail_key_is_a_span_with_the_three_ways(app_and_org):
    """The details-row cells are spans; the conflict fragment used to be a
    hardcoded <td>, which the parser DROPS at that swap point (no table-row
    ancestor) — the field silently blanked with no Reload/Overwrite/Keep at
    all (fresh-eyes review, 2026-08-19)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)

    refused = client.post(
        _cell(org, placement, layer, "policy_number"), data={"policy_number": "POL-1"}
    )

    assert refused.status_code == 200
    body = refused.text
    assert body.lstrip().startswith("<span"), "conflict came back as a parser-dropped td"
    assert "</span>" in body and "<td" not in body
    for choice in ("Reload", "Overwrite", "Keep editing"):
        assert choice in body
    assert 'hx-target="closest span"' in body


def test_a_refused_scaffold_keeps_the_programs_panel(app_and_org):
    """The scaffold confirm's POST targets #programs-panel with outerHTML, so
    a refusal that answers with a bare fragment REPLACES the whole panel —
    every placement's rows, the tower, the add control and the panel's own id
    (fresh-eyes review, 2026-08-19). The refusal must come back as the panel
    with the message in its error slot."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]  # already has a file -> guaranteed refusal

    refused = client.post(f"/accounts/{org.ref}/program/{placement.id}/scaffold")

    assert refused.status_code == 200
    assert 'id="programs-panel"' in refused.text, "the refusal swapped the panel away"
    assert "already has a program file" in refused.text
    assert placement.program_name in refused.text, "the panel came back without its rows"


def test_a_conflict_writes_nothing(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)
    before = Path(placement.program_path).read_bytes()

    client.post(_cell(org, placement, layer, "name"), data={"name": "My Edit"})

    assert Path(placement.program_path).read_bytes() == before


def test_reload_catches_the_projection_up_and_shows_what_is_there_now(app_and_org):
    """Reload discards MY draft and takes THEIR file. After it, the page shows
    the out-of-band change."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    theirs = _touch_out_of_band(placement)

    reloaded = client.post(
        _cell(org, placement, layer, "name") + "/reload", data={"name": "My Edit"}
    )

    assert reloaded.status_code == 200
    names = [ly["name"] for ly in sync.layer_details(conn, placement.id)]
    assert theirs in names
    assert "My Edit" not in names


def test_overwrite_lands_my_edit_on_top_without_losing_theirs(app_and_org):
    """THE claim this whole design rests on. Overwrite is a RETRY, not a
    clobber: it re-projects and re-applies the ONE field I changed, so a
    structural change somebody else made survives underneath it. Reusing the
    towerkit TUI's force-write — which pushes a whole in-memory program — would
    discard their change instead."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    theirs = _touch_out_of_band(placement)

    written = client.post(
        _cell(org, placement, layer, "name") + "/overwrite", data={"name": "My Edit"}
    )

    assert written.status_code == 200
    names = [ly["name"] for ly in sync.layer_details(conn, placement.id)]
    assert "My Edit" in names, "my edit did not land"
    assert theirs in names, "their change was clobbered — this is a retry, not a force"


def test_keep_editing_leaves_the_field_open_with_what_was_typed(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)

    still = client.post(
        _cell(org, placement, layer, "name") + "/keep", data={"name": "My Edit"}
    )

    assert still.status_code == 200
    assert "My Edit" in still.text
    assert "<input" in still.text


def test_an_ordinary_refusal_is_not_dressed_as_a_conflict(app_and_org):
    """The two are handled differently and must be told apart by the CODE, not
    by the fact that something went wrong."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    refused = client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "1,234.56"}
    )

    assert "reload" not in refused.text.lower()
    assert "overwrite" not in refused.text.lower()


# --- phase 5: putting a program write back from the rail ----------------------


def test_a_program_write_is_reverted_from_the_rail(app_and_org):
    """File contents are not event_log rows, so batch undo cannot restore a
    program file — it refuses those batches outright. The rail used to stop
    there and say so, which is honest and useless. It now calls the file-side
    revert the MCP server has had all along."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    was = layer["name"]

    client.post(_cell(org, placement, layer, "name"), data={"name": "Renamed By Web"})
    batch = _latest_batch(conn)
    assert batch.tool == "program_layer_edit"

    reverted = client.post(
        f"/accounts/{org.ref}/changes/{batch.ref}/revert?tab=program"
    )

    assert reverted.status_code in (200, 204)
    names = [ly["name"] for ly in sync.layer_details(conn, placement.id)]
    assert was in names, "the pre-image was not put back"
    assert "Renamed By Web" not in names


def test_reverting_a_program_write_is_refused_once_the_file_moved_on(app_and_org):
    """The snapshot records the sha the write left behind. Anything newer —
    towerkit's editor, an MCP call, a later web edit — and putting the
    pre-image back would silently discard it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    client.post(_cell(org, placement, layer, "name"), data={"name": "Renamed By Web"})
    batch = _latest_batch(conn)
    _touch_out_of_band(placement)
    after_theirs = Path(placement.program_path).read_bytes()

    refused = client.post(
        f"/accounts/{org.ref}/changes/{batch.ref}/revert?tab=program"
    )

    assert refused.status_code in (200, 204)
    assert Path(placement.program_path).read_bytes() == after_theirs, "their edit was lost"


def test_the_rail_says_which_of_the_two_happened(app_and_org):
    """A revert that reports nothing is indistinguishable from one that did
    nothing."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    client.post(_cell(org, placement, layer, "name"), data={"name": "Renamed By Web"})
    batch = _latest_batch(conn)
    done = client.post(f"/accounts/{org.ref}/changes/{batch.ref}/revert?tab=program")

    landing = done.headers.get("HX-Redirect")
    assert landing, "no redirect to carry the outcome"
    page = client.get(landing).text
    assert "put back" in page.lower() or "reverted" in page.lower()
