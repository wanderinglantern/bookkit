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
    """A layer that attaches at $10M must not render as 1000000000."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    layer = sync.layer_details(conn, placement.id)[0]

    page = client.get(f"/accounts/{org.ref}/program").text

    assert str(layer["attach_cents"]) not in page or layer["attach_cents"] == 0


def test_a_primary_layer_attaching_at_zero_says_zero(app_and_org):
    """An em dash means UNRECORDED. A primary layer attaches at $0 and that is
    a fact about the tower — rendering it as a dash tells the reader the
    attachment is unknown when it is known and is zero.

    The figure is exact rather than compact because these cells are editable
    and one string serves both the display and the editor's pre-fill: "$50M"
    parses back as $50,000,000 and would quietly destroy the odd dollars of a
    layer at $50,123,456."""
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

    assert cell == "0", f"a $0 attachment rendered as {cell!r}"
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
    never happened."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    before = _latest_batch(conn)

    client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "1,234.56"}
    )

    after = _latest_batch(conn)
    assert (after.ref if after else None) == (before.ref if before else None)


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
