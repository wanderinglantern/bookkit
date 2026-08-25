from __future__ import annotations

import sqlite3
from datetime import date, timedelta
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
    # `len(beyond) <= len(rows)` — measured at 0 and 3 — CANNOT FAIL, so
    # ignoring the `days` argument outright was green (2026-08-18). Pin the
    # horizon to its own last day, computed from the data rather than guessed.
    #
    # MEASURED ON `renewal_on`, not on `period_to`: the window is the earliest
    # LINE end capped by the program period, because an Inland Marine layer
    # runs out months before its program does and this page was the one
    # surface still counting to the program (2026-08-24). The rows are still
    # live programs — the repo query keeps expired ones out — but a row's own
    # renewal date can be in the PAST, and an overdue line never falls off.
    assert all(r.period_to >= TODAY.isoformat() for r in rows)
    horizon = (TODAY + timedelta(days=365)).isoformat()
    assert all(r.renewal_on <= horizon for r in rows)

    future = sorted({r.renewal_on for r in rows if r.renewal_on >= TODAY.isoformat()})
    assert future, "no seeded tower renews in the future at all"
    soonest = future[0]
    gap = (date.fromisoformat(soonest) - TODAY).days
    on_the_day = exposure.carrier_exposure(conn, "Swiss Re", days=gap, today=TODAY)
    assert soonest in {r.renewal_on for r in on_the_day}, (
        "the horizon must include its own last day"
    )
    assert all(r.renewal_on <= soonest for r in on_the_day), (
        "and nothing past it"
    )
    assert len(on_the_day) < len(rows), "the window is not narrowing at all"
    # and the window really does widen — a staircase, not a constant
    assert 0 < len(on_the_day) < len(rows)
    assert all(r.renewal_on <= r.period_to for r in rows), (
        "a renewal date past the program's own period end is uncapped"
    )


def test_seed_projects_its_program_files_it_does_not_just_point_at_them(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """seed was the ONE writer that set `program_path` without going through
    `sync.project` (every other path sets path and sha together), so every
    seeded book carried three placements whose `source_sha256` was NULL
    forever — and an empty proj_* cache under a program file that exists.

    Both halves are asserted here because they are the same omission: the sha
    is what the write-conflict guard compares against, and the proj_ rows are
    what every tower surface reads."""
    programs = tmp_path / "programs"
    seed.seed(conn, today=TODAY, programs_dir=programs)

    linked = conn.execute(
        "SELECT ref, program_path, source_sha256, synced_at FROM placement"
        " WHERE program_path IS NOT NULL"
    ).fetchall()
    assert len(linked) == 3
    for row in linked:
        assert row["source_sha256"] == sync.file_sha256(Path(row["program_path"])), (
            f"{row['ref']} was linked to a file bookkit never verified"
        )
        assert row["synced_at"]

    for table in ("proj_layer", "proj_participant", "proj_retention"):
        count = conn.execute(f"SELECT count(*) AS c FROM {table}").fetchone()["c"]
        assert count > 0, f"{table} is empty — the seeded book has no tower under it"


def test_a_placement_bookkit_never_verified_is_refused_not_waved_through(
    synced,
) -> None:
    """A NULL `source_sha256` USED TO SHORT-CIRCUIT THE GUARD ENTIRELY —
    `if placement.source_sha256 and file_sha256(path) != ...` — so a placement
    bookkit had never verified was the one placement it would write through
    without a single check.

    That is the guard inverted. A mismatched sha means "bookkit saw this file
    and it has moved on"; a missing one means "bookkit has never seen this
    file at all", which is strictly less knowledge and therefore the case to
    refuse hardest. The file here is UNTOUCHED on disk: there is nothing wrong
    with it except that nothing can vouch for it, and that alone must refuse."""
    conn, programs = synced
    path = programs / "atomic-casualty.json"
    placement = placements.by_program_path(conn, str(path))
    conn.execute(
        "UPDATE placement SET source_sha256 = NULL WHERE id = ?", (placement.id,)
    )
    before = path.read_text()

    def bump(program) -> None:
        program.layers[0].premium = 123_456

    with pytest.raises(sync.WriteConflict, match="never verified"):
        sync.write_through(conn, placement.id, bump)
    assert path.read_text() == before, "an unverifiable placement was written anyway"


def test_a_line_running_out_early_is_visible_on_the_market_page(synced) -> None:
    """THE DEFECT (surface sweep, 2026-08-24). The market page's window
    filtered `placement.period_to`, so a program renewing well past the
    horizon was invisible here even when one of its lines ran out inside it —
    and CLAUDE.md's rule is that the renewal date is the earliest LINE end,
    never `placement.period_to`.

    Built rather than hoped for: the seeded towers all renew inside a
    generous window, so a scan over them proves nothing about the boundary.
    """
    from towerkit.model import Period, dump_program

    conn, programs = synced
    path = programs / "atomic-casualty.json"
    program = load_program(path)

    # the program itself renews FAR out — past any window this page offers
    far = Period(start=program.period.start, end=date(2029, 1, 1))
    program.period = far
    for layer in program.layers:
        layer.period = far
    # ...but one line's cover runs out in three weeks
    soon = TODAY + timedelta(days=21)
    early = next(ly for ly in program.layers if "Swiss Re" in [p.carrier for p in ly.participants])
    early.period = Period(start=program.period.start, end=soon)
    dump_program(program, path)
    sync.project(conn, path)

    refreshed = placements.by_program_path(conn, str(path))
    assert refreshed.period_to == "2029-01-01", "the fixture did not move the period"

    rows = exposure.carrier_exposure(conn, "Swiss Re", days=90, today=TODAY)

    hit = [r for r in rows if r.placement_id == refreshed.id]
    assert hit, (
        "a line running out in three weeks is invisible on the market page "
        "because the program itself renews in 2029"
    )
    assert all(r.renewal_on == soon.isoformat() for r in hit), (
        "the row prints the program's period end, not the date it renews"
    )
