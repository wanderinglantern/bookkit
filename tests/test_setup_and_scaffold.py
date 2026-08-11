"""Setup dialogue plumbing (program roots) and the scaffold path — the DRY
inverse of adoption: bookkit placement → towerkit file, nothing typed twice."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from towerkit.validate import validate_file

from bookkit import sync
from bookkit.cli import main
from bookkit.repo import links, orgs, placements, projection, settings


@pytest.fixture
def client(conn: sqlite3.Connection):
    return orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")


def test_roots_setting_round_trip(conn, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BOOKKIT_PROGRAM_ROOTS", raising=False)
    assert sync.configured_roots(conn) == []
    settings.set_program_roots(conn, [str(tmp_path)])
    assert sync.configured_roots(conn) == [tmp_path]
    # env var is only a fallback; the saved setting wins
    monkeypatch.setenv("BOOKKIT_PROGRAM_ROOTS", "/somewhere/else")
    assert sync.configured_roots(conn) == [tmp_path]


def test_env_fallback(conn, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BOOKKIT_PROGRAM_ROOTS", f"{tmp_path}:{tmp_path / 'more'}")
    assert sync.configured_roots(conn) == [tmp_path, tmp_path / "more"]


def test_cli_roots_and_sync(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BOOKKIT_DB", str(tmp_path / "cli.db"))
    monkeypatch.delenv("BOOKKIT_PROGRAM_ROOTS", raising=False)
    programs = tmp_path / "programs"
    main(["seed", "--demo", "--programs-dir", str(programs)])
    capsys.readouterr()

    assert main(["sync"]) == 2  # unconfigured → clear next step
    assert "bookctl roots" in capsys.readouterr().out

    assert main(["roots", str(programs)]) == 0
    out = capsys.readouterr().out
    assert "3 program file(s)" in out

    assert main(["sync"]) == 0  # no --roots needed any more
    assert "projected 3 file(s)" in capsys.readouterr().out

    assert main(["roots", str(tmp_path / "nope")]) == 2


def test_scaffold_from_placement(conn, client, tmp_path: Path) -> None:
    placement = placements.create(
        conn, client.id, "2026 Cyber Program", "2026-10-01", "2027-10-01",
        total_premium=750_000_00, total_limit=10_000_000_00, commission_bps=1500,
    )
    dest = tmp_path / "programs" / "test-client-2026.json"
    made, diags = sync.scaffold_program(conn, placement.id, dest)
    assert diags.ok and made == dest

    # the file is valid towerkit, carrying everything the placement knew
    program, file_diags = validate_file(dest)
    assert program is not None and file_diags.ok
    assert program.insured == "Test Client, Inc."
    assert program.program == "2026 Cyber Program"
    assert program.period.start.isoformat() == "2026-10-01"
    assert program.layers[0].limit == 10_000_000
    assert program.layers[0].premium == 750_000
    assert program.layers[0].participants == []  # pending — 'To be placed'

    # linked at birth and projected; bookkit fields survive
    refreshed = placements.get(conn, placement.id)
    assert refreshed.program_path == str(dest)
    assert refreshed.commission_bps == 1500
    assert projection.layers_for_placement(conn, placement.id)
    row = [r for r in links.all_links(conn) if r["path"] == str(dest)][0]
    assert row["source"] == "scaffold"

    # a proposed scaffold's TBD line feeds the opportunity offers
    assert [c.line_id for c in sync.opportunities_for_path(conn, dest)] == ["tbd"]


def test_scaffold_refuses_linked_and_existing(conn, client, tmp_path: Path) -> None:
    placement = placements.create(
        conn, client.id, "Casualty", "2026-01-01", "2027-01-01"
    )
    dest = tmp_path / "scaffold.json"
    assert sync.scaffold_program(conn, placement.id, dest)[1].ok
    # already linked now
    made, diags = sync.scaffold_program(conn, placement.id, tmp_path / "again.json")
    assert made is None and not diags.ok
    other = placements.create(conn, client.id, "Marine", "2026-01-01", "2027-01-01")
    made, diags = sync.scaffold_program(conn, other.id, dest)  # path taken
    assert made is None and any(d.code == "exists" for d in diags.errors)


def test_scaffold_round_trips_canonically(conn, client, tmp_path: Path) -> None:
    placement = placements.create(
        conn, client.id, "Casualty", "2026-01-01", "2027-01-01"
    )
    dest = tmp_path / "canon.json"
    assert sync.scaffold_program(conn, placement.id, dest)[1].ok
    before = dest.read_text()
    diags = sync.write_through(conn, placement.id, lambda program: None)
    assert diags.ok
    assert dest.read_text() == before
