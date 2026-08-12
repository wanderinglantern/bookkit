from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from towerkit.model import load_program

from bookkit import seed, sync
from bookkit.repo import links, orgs, placements, projection
from bookkit.services import exposure

TODAY = date(2026, 8, 11)


@pytest.fixture
def synced(conn: sqlite3.Connection, tmp_path: Path):
    programs = tmp_path / "programs"
    seed.seed(conn, today=TODAY, programs_dir=programs)
    report = sync.project_all(conn, [programs])
    assert report.ok, report.render()
    assert len(report.projected) == 3
    return conn, programs


def test_scan_finds_only_program_files(synced, tmp_path: Path) -> None:
    _, programs = synced
    (tmp_path / "programs" / "not-a-program.json").write_text('{"foo": 1}')
    found = sync.scan([programs])
    assert len(found) == 3
    assert all("not-a-program" not in str(p) for p in found)


def test_project_populates_cache(synced) -> None:
    conn, programs = synced
    placement = placements.by_program_path(conn, str(programs / "atomic-casualty.json"))
    assert placement is not None
    assert placement.source_sha256 and placement.synced_at
    layers = projection.layers_for_placement(conn, placement.id)
    assert [row["layer_id"] for row in layers] == [
        "primary-im", "primary-gl", "primary-al", "umbrella"
    ]
    # money crossed the boundary as cents
    assert layers[0]["lim"] == 5_000_000 * 100  # primary-im
    parts = projection.participants_for_placement(conn, placement.id)
    assert any(row["carrier"] == "Swiss Re" and row["share_bps"] == 6_000 for row in parts)
    # placement totals refreshed from the file
    program = load_program(programs / "atomic-casualty.json")
    assert placement.total_premium == program.total_premium() * 100


def test_project_is_idempotent(synced) -> None:
    conn, programs = synced
    path = programs / "atomic-casualty.json"
    placement = placements.by_program_path(conn, str(path))
    before_layers = [dict(r) for r in projection.layers_for_placement(conn, placement.id)]
    before_parts = [dict(r) for r in projection.participants_for_placement(conn, placement.id)]
    diags = sync.project(conn, path)
    assert diags.ok
    after_layers = [dict(r) for r in projection.layers_for_placement(conn, placement.id)]
    after_parts = [dict(r) for r in projection.participants_for_placement(conn, placement.id)]

    def strip(rows):
        return [{k: v for k, v in r.items() if k != "synced_at"} for r in rows]

    assert strip(before_layers) == strip(after_layers)
    assert strip(before_parts) == strip(after_parts)


def test_unlinked_file_goes_to_review_queue(synced, tmp_path: Path) -> None:
    conn, programs = synced
    src = (programs / "atomic-casualty.json").read_text()
    orphan = programs / "orphan.json"
    orphan.write_text(src.replace("Atomic Industries, Inc.", "Atomic Industies Inc"))
    report = sync.project_all(conn, [programs])
    assert len(report.needs_link) == 1
    suggestion = report.needs_link[0]
    assert suggestion.insured == "Atomic Industies Inc"  # typo'd on purpose
    assert suggestion.candidates, "fuzzy candidates should include the real account"
    assert suggestion.candidates[0][0].name == "Atomic Industries, Inc."
    # nothing was linked silently
    assert links.org_for_path(conn, str(orphan)) is None
    assert placements.by_program_path(conn, str(orphan)) is None


def test_confirm_link_projects(synced, tmp_path: Path) -> None:
    conn, programs = synced
    src = (programs / "atomic-casualty.json").read_text()
    orphan = programs / "orphan2.json"
    orphan.write_text(src.replace("Atomic Industries, Inc.", "Borealis Foods Group"))
    org = orgs.find_by_name(conn, "Borealis Foods Group")
    diags = sync.confirm_link(conn, orphan, org.id)
    assert diags.ok
    placement = placements.by_program_path(conn, str(orphan))
    assert placement is not None and placement.org_id == org.id


def test_write_through_round_trip(synced) -> None:
    conn, programs = synced
    path = programs / "atomic-casualty.json"
    placement = placements.by_program_path(conn, str(path))

    def bump_premium(program) -> None:
        program.layers[0].premium = 1_000_000

    diags = sync.write_through(conn, placement.id, bump_premium)
    assert diags.ok
    assert load_program(path).layers[0].premium == 1_000_000
    # cache and placement refreshed
    refreshed = placements.get(conn, placement.id)
    assert refreshed.total_premium == load_program(path).total_premium() * 100
    assert refreshed.source_sha256 == sync.file_sha256(path)


def test_write_through_refuses_invalid(synced) -> None:
    conn, programs = synced
    path = programs / "atomic-casualty.json"
    placement = placements.by_program_path(conn, str(path))
    before = path.read_text()

    def break_tower(program) -> None:
        program.layers[0].attach = 999  # gap under the primary

    diags = sync.write_through(conn, placement.id, break_tower)
    assert not diags.ok
    assert path.read_text() == before, "invalid mutation must write nothing"


def test_write_through_refuses_changed_hash(synced) -> None:
    conn, programs = synced
    path = programs / "atomic-casualty.json"
    placement = placements.by_program_path(conn, str(path))
    # towerkit's own TUI edits the file underneath us
    path.write_text(path.read_text().replace("Primary GL", "Primary General Liability"))
    before = path.read_text()

    def bump(program) -> None:
        program.layers[0].premium = 123_456

    with pytest.raises(sync.WriteConflict):
        sync.write_through(conn, placement.id, bump)
    assert path.read_text() == before, "conflicted write must not touch the file"


def test_canonical_round_trip(synced) -> None:
    """Write-through with a no-op mutation must produce a zero diff."""
    conn, programs = synced
    path = programs / "delta-marine.json"
    placement = placements.by_program_path(conn, str(path))
    before = path.read_text()
    diags = sync.write_through(conn, placement.id, lambda program: None)
    assert diags.ok
    assert path.read_text() == before


def test_cross_book_exposure(synced) -> None:
    """'Every account where Swiss Re is on the tower, renewing in 90 days' —
    the query the proj_ tables exist for."""
    conn, programs = synced
    rows = exposure.carrier_exposure(conn, "Swiss Re", days=365, today=TODAY)
    assert rows, "Swiss Re sits on every seeded demo tower"
    names = {r.org_name for r in rows}
    assert "Atomic Industries, Inc." in names
    for row in rows:
        assert row.share_bps == 6_000
        assert row.layer_name == "Umbrella"
    # window actually filters
    beyond = exposure.carrier_exposure(conn, "Swiss Re", days=5, today=TODAY)
    assert len(beyond) <= len(rows)
