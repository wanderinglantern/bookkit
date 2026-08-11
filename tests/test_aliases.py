"""Carrier alias mapping: every towerkit spelling of a carrier finds the one
bookkit market in exposure, book summary, and the sync review."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from test_linking_flow import make_program, write_program
from towerkit.model import dump_program, load_program

from bookkit import sync
from bookkit.repo import aliases, orgs, placements
from bookkit.services import book, exposure

TODAY = date(2026, 8, 11)


@pytest.fixture
def linked(conn: sqlite3.Connection, tmp_path: Path):
    """A client with a projected tower whose carrier is spelled
    'Swiss Reinsurance', plus a market org named 'Swiss Re'."""
    client = orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")
    market = orgs.create(conn, kind="market", name="Swiss Re", status="active")
    path = write_program(
        tmp_path / "p" / "test.json",
        make_program("Test Client, Inc.", "2026-09-01", "2027-09-01"),
    )
    program = load_program(path)
    program.layers[0].participants[0].carrier = "Swiss Reinsurance"
    dump_program(program, path)
    assert sync.confirm_link(conn, path, client.id).ok
    return conn, client, market, path


def test_resolve_prefers_exact_name_then_alias(linked) -> None:
    conn, _, market, _ = linked
    assert aliases.resolve(conn, "Swiss Re") == market.id
    assert aliases.resolve(conn, "Swiss Reinsurance") is None
    aliases.set_alias(conn, "Swiss Reinsurance", market.id)
    assert aliases.resolve(conn, "Swiss Reinsurance") == market.id
    assert aliases.for_market(conn, market.id) == ["Swiss Reinsurance"]


def test_unresolved_carriers_surface_and_clear(linked) -> None:
    conn, _, market, _ = linked
    unresolved = aliases.unresolved_carriers(conn)
    assert "Swiss Reinsurance" in unresolved
    assert "Zurich" not in unresolved or orgs.find_by_name(conn, "Zurich") is None
    aliases.set_alias(conn, "Swiss Reinsurance", market.id)
    assert "Swiss Reinsurance" not in aliases.unresolved_carriers(conn)


def test_exposure_finds_market_under_alias(linked) -> None:
    conn, _, market, _ = linked
    # before the alias, the market view misses the tower entirely
    assert exposure.for_market(conn, market.id, days=500, today=TODAY) == []
    aliases.set_alias(conn, "Swiss Reinsurance", market.id)
    rows = exposure.for_market(conn, market.id, days=500, today=TODAY)
    assert len(rows) == 1
    assert rows[0].carrier == "Swiss Reinsurance"  # as the file wrote it
    # and querying by either spelling expands to the whole market
    assert exposure.carrier_exposure(conn, "Swiss Re", days=500, today=TODAY) == rows
    assert exposure.carrier_exposure(conn, "Swiss Reinsurance", days=500, today=TODAY) == rows


def test_book_summary_rolls_up_aliased_spellings(linked) -> None:
    conn, client, market, path = linked
    placement = placements.by_program_path(conn, str(path))
    placements.update(conn, placement.id, status="bound")
    before = {label: premium for label, premium, _ in book.summary(conn).by_market}
    assert "Swiss Reinsurance" in before
    aliases.set_alias(conn, "Swiss Reinsurance", market.id)
    after = {label: premium for label, premium, _ in book.summary(conn).by_market}
    assert "Swiss Reinsurance" not in after
    assert after["Swiss Re"] == before["Swiss Reinsurance"]


def test_sync_report_offers_unknown_carriers(linked, tmp_path: Path) -> None:
    conn, _, market, _ = linked
    report = sync.project_all(conn, [tmp_path / "p"])
    offered = {c.carrier: c for c in report.unresolved_carriers}
    assert "Swiss Reinsurance" in offered
    assert offered["Swiss Reinsurance"].candidates[0][0].id == market.id  # fuzzy hit
    sync.alias_carrier(conn, "Swiss Reinsurance", market.id)
    report = sync.project_all(conn, [tmp_path / "p"])
    assert all(c.carrier != "Swiss Reinsurance" for c in report.unresolved_carriers)


def test_create_market_for_carrier(linked) -> None:
    conn, *_ = linked
    market = sync.create_market_for_carrier(conn, "Odd Mutual Indemnity")
    assert market.kind == "market"
    assert aliases.resolve(conn, "Odd Mutual Indemnity") == market.id  # by name, no alias row
