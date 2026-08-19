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
    attachment is unknown when it is known and is zero."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    primaries = [
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["attach_cents"] == 0
    ]
    assert primaries, "the seeded book has no ground-up layer — test proves nothing"

    page = client.get(f"/accounts/{org.ref}/program").text
    row = page.split(primaries[0]["name"], 1)[1][:400]

    assert "$0" in row, "a $0 attachment rendered as unknown"
