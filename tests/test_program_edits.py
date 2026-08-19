"""Transactional program edits from bookkit — all via write_through, with
towerkit's validator as the gatekeeper — plus market merging."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_linking_flow import make_program, write_program
from towerkit.model import load_program

from bookkit import sync
from bookkit.money import MoneyParseError, parse_share_bps
from bookkit.repo import aliases, contacts, orgs, placements, submissions
from bookkit.services.merge import MergeError, merge_markets


@pytest.fixture
def linked(conn: sqlite3.Connection, tmp_path: Path):
    client = orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")
    path = write_program(
        tmp_path / "p" / "test.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01", tbd_line=True),
    )
    assert sync.confirm_link(conn, path, client.id).ok
    placement = placements.by_program_path(conn, str(path))
    return conn, client, placement, path


def test_parse_share_bps() -> None:
    assert parse_share_bps("25%") == 2500
    assert parse_share_bps("25") == 2500
    assert parse_share_bps("12.5") == 1250
    assert parse_share_bps("33.34%") == 3334
    assert parse_share_bps("100") == 10_000
    assert parse_share_bps("0.25") == 25  # a quarter PERCENT — one rule, no guessing
    for bad in ("0", "101", "33.333", "a third"):
        with pytest.raises(MoneyParseError):
            parse_share_bps(bad)


def test_update_program_dates(linked) -> None:
    conn, _, placement, path = linked
    diags = sync.update_program(
        conn, placement.id, period_from="2026-02-01", period_to="2027-02-01"
    )
    assert diags.ok
    program = load_program(path)
    assert program.period.start.isoformat() == "2026-02-01"
    refreshed = placements.get(conn, placement.id)
    assert refreshed.period_from == "2026-02-01"  # projection followed
    # nonsense dates are refused before anything is written
    bad = sync.update_program(conn, placement.id, period_to="2025-01-01")
    assert not bad.ok
    assert load_program(path).period.end.isoformat() == "2027-02-01"


def test_update_layer_premium_and_policy(linked) -> None:
    conn, _, placement, path = linked
    diags = sync.update_layer(
        conn, placement.id, "primary-gl",
        premium_cents=1_100_000_00, policy_number="GLP-2026-0042",
        period_from="2026-02-01", period_to="2027-02-01",
    )
    assert diags.ok
    layer = load_program(path).layers[0]
    assert layer.premium == 1_100_000
    assert layer.policy_number == "GLP-2026-0042"
    assert layer.period is not None and layer.period.start.isoformat() == "2026-02-01"


def test_update_layer_refuses_gap(linked) -> None:
    conn, _, placement, path = linked
    before = path.read_text()
    diags = sync.update_layer(conn, placement.id, "primary-gl", attach_cents=999_00)
    assert not diags.ok  # line no longer starts at $0 → towerkit refuses
    assert path.read_text() == before


def test_add_layer_pending(linked) -> None:
    conn, _, placement, path = linked
    diags = sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=10_000_000_00,
    )
    assert diags.ok
    program = load_program(path)
    added = next(ly for ly in program.layers if ly.name == "1st Excess")
    assert added.id == "1st-excess"
    assert added.participants == []  # pending — 'To be placed'
    assert added.attach == 2_000_000 and added.limit == 10_000_000
    details = sync.layer_details(conn, placement.id)
    assert any(d["id"] == "1st-excess" and d["signed_pct"] == 0 for d in details)
    # statutory travels with the layer: without it a reader of these dicts
    # cannot tell unlimited cover from a limit that happens to be zero.
    assert all("statutory" in d for d in details)
    assert not any(d["statutory"] for d in details)


def test_add_participant_and_oversign_refused(linked) -> None:
    conn, _, placement, path = linked
    diags = sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=10_000_000_00,
    )
    assert diags.ok
    assert sync.add_participant(conn, placement.id, "1st-excess", "Chubb", 6000).ok
    assert sync.add_participant(conn, placement.id, "1st-excess", "AXA XL", 4000).ok
    program = load_program(path)
    layer = next(ly for ly in program.layers if ly.id == "1st-excess")
    assert layer.signed_bps == 10_000
    before = path.read_text()
    # a third market at any share would over-sign: refused, file untouched
    over = sync.add_participant(conn, placement.id, "1st-excess", "Zurich", 500)
    assert not over.ok
    assert path.read_text() == before
    # same carrier twice is refused too
    dup = sync.add_participant(conn, placement.id, "1st-excess", "Chubb", 100)
    assert not dup.ok


def test_edit_conflict_surfaces_as_diagnostic(linked) -> None:
    conn, _, placement, path = linked
    path.write_text(path.read_text().replace("Primary GL", "Primary General Liability"))
    diags = sync.update_layer(conn, placement.id, "primary-gl", premium_cents=100_00)
    assert not diags.ok
    assert any(d.code == "conflict" for d in diags.errors)


def test_merge_markets_folds_duplicate_with_alias(conn: sqlite3.Connection, tmp_path) -> None:
    client = orgs.create(conn, kind="client", name="Client A", status="active")
    real = orgs.create(conn, kind="market", name="AXA XL", status="active")
    orgs.set_market_profile(conn, real.id, market_type="carrier", am_best_rating="A+")
    dupe = orgs.create(conn, kind="market", name="Axa XL", status="active")
    contacts.create(conn, dupe.id, first_name="Ute", last_name="Meyer", role="underwriter")
    placement = placements.create(conn, client.id, "Casualty", "2026-01-01", "2027-01-01")
    submissions.create(conn, dupe.id, "2026-05-01", placement_id=placement.id)

    result = merge_markets(conn, dupe.id, real.id)
    assert result.alias_added == "Axa XL"
    assert result.moved_contacts == 1 and result.moved_submissions == 1
    assert aliases.resolve(conn, "Axa XL") == real.id  # towers keep resolving
    assert [c.name for c in contacts.for_org(conn, real.id)] == ["Ute Meyer"]
    assert submissions.for_market(conn, real.id)
    with pytest.raises(KeyError):
        orgs.get(conn, dupe.id)  # soft-deleted

    with pytest.raises(MergeError):
        merge_markets(conn, real.id, client.id)  # clients are not markets


def test_program_lines_helper(linked) -> None:
    conn, _, placement, _ = linked
    assert sync.program_lines(conn, placement.id) == [
        ("gl", "General Liability"), ("cy", "Cyber"),
    ]


# --- the panel a layer is actually placed with -------------------------------


def test_layer_details_carries_the_carrier_panel(linked) -> None:
    """AE review: program_layers' description promised participants and
    sync.layer_details returned none, so an assistant reading the contract
    believed it could see who is on the 2nd excess when it could not. The
    DESCRIPTION was right and the data was thin — program_summary is the tool
    that is deliberately slim, and says so; this is the tower.

    Three layers on purpose, each a different shape, and every one asserted:
    a fixture that decorates only the first layer passes a per-layer bug."""
    conn, _, placement, path = linked
    assert sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=10_000_000_00, premium_cents=300_000_00,
    ).ok
    assert sync.add_participant(conn, placement.id, "1st-excess", "Chubb", 6000).ok
    assert sync.add_participant(conn, placement.id, "1st-excess", "AXA XL", 4000).ok

    panels = {d["id"]: d["participants"] for d in sync.layer_details(conn, placement.id)}
    assert panels["primary-gl"] == [
        {"carrier": "Zurich", "share_pct": 100.0, "premium_cents": 900_000_00},
    ]
    # a layer with no panel is 'To be placed' — an EMPTY list, never absent,
    # because absent and unplaced are different facts to a reader
    assert panels["primary-cy"] == []
    assert panels["1st-excess"] == [
        {"carrier": "Chubb", "share_pct": 60.0, "premium_cents": 180_000_00},
        {"carrier": "AXA XL", "share_pct": 40.0, "premium_cents": 120_000_00},
    ]
    # the shares add up to the signed figure already on the layer — same units
    # in the same dict, which is the whole reason share is a percentage here
    for detail in sync.layer_details(conn, placement.id):
        assert sum(p["share_pct"] for p in detail["participants"]) == detail["signed_pct"]


def test_layer_details_premium_share_is_none_when_the_layer_has_none(linked) -> None:
    """A share of an unknown premium is unknown, not zero — the same rule the
    projection already applies (sync.project_file)."""
    conn, _, placement, _ = linked
    assert sync.add_layer(
        conn, placement.id, "2nd Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=5_000_000_00,
    ).ok
    assert sync.add_participant(conn, placement.id, "2nd-excess", "Berkley", 10_000).ok
    detail = next(
        d for d in sync.layer_details(conn, placement.id) if d["id"] == "2nd-excess"
    )
    assert detail["premium_cents"] is None
    assert detail["participants"] == [
        {"carrier": "Berkley", "share_pct": 100.0, "premium_cents": None},
    ]


def test_layer_details_still_opens_the_file_once(linked, monkeypatch) -> None:
    """The panel comes off the program already in memory. layer_details does
    file I/O per call and the web page was deliberately reduced to ONE call per
    render; a per-layer load would undo that silently, and three layers is the
    smallest fixture that can tell one load from several."""
    conn, _, placement, _ = linked
    assert sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=10_000_000_00,
    ).ok
    calls = []
    real = sync.load_program

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(sync, "load_program", counting)
    details = sync.layer_details(conn, placement.id)
    assert len(details) == 3
    assert len(calls) == 1, calls
