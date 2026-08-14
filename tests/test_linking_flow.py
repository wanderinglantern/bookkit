"""The unified-record linking flow: adoption, standing confirmation, rename
detection, renew-with-clone, opportunity offers from proposed programs, and
placement merges."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from towerkit.model import (
    Layer,
    Line,
    Participant,
    Period,
    Program,
    Retention,
    RetentionType,
    dump_program,
    load_program,
)
from towerkit.model import Placement as TkPlacement

from bookkit import sync
from bookkit.repo import documents, links, orgs, placements, projection, submissions
from bookkit.repo import tasks as tasks_repo
from bookkit.services.merge import MergeError, merge_placements


def make_program(
    insured: str,
    start: str,
    end: str,
    placed: bool = True,
    tbd_line: bool = False,
) -> Program:
    lines = [Line(id="gl", name="General Liability", abbr="GL")]
    layers = [
        Layer(
            id="primary-gl", name="Primary GL", applies_to=["gl"],
            attach=0, limit=2_000_000, premium=900_000,
            participants=[Participant(carrier="Zurich", share_bps=10_000)],
        )
    ]
    if tbd_line:
        lines.append(Line(id="cy", name="Cyber", abbr="CY"))
        layers.append(
            Layer(
                id="primary-cy", name="Primary Cyber", applies_to=["cy"],
                attach=0, limit=5_000_000, premium=400_000, participants=[],
            )
        )
    retentions = [
        Retention(applies_to=[ln.id], type=RetentionType.DEDUCTIBLE, amount=100_000)
        for ln in lines
    ]
    return Program(
        insured=insured,
        program="Casualty Program",
        placement=TkPlacement.BOUND if placed else TkPlacement.PROPOSED,
        period=Period(start=date.fromisoformat(start), end=date.fromisoformat(end)),
        lines=lines,
        layers=layers,
        retentions=retentions,
    )


def write_program(path: Path, program: Program) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_program(program, path)
    return path


@pytest.fixture
def client(conn: sqlite3.Connection):
    return orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")


def test_adoption_of_single_manual_placement(conn, client, tmp_path: Path) -> None:
    """A hand-made placement is adopted by the file instead of duplicated."""
    manual = placements.create(
        conn, client.id, "Casualty Program", "2026-06-01", "2027-06-01",
        commission_bps=1250,
    )
    path = write_program(
        tmp_path / "p" / "test.json", make_program("Test Client, Inc.", "2026-07-01", "2027-07-01")
    )
    diags = sync.confirm_link(conn, path, client.id)  # single candidate → adopts
    assert diags.ok
    refreshed = placements.get(conn, manual.id)
    assert refreshed.program_path == str(path)
    assert refreshed.period_from == "2026-07-01"  # file corrected the guess
    assert refreshed.commission_bps == 1250  # bookkit-owned field survives
    assert len(placements.for_org(conn, client.id)) == 1  # no duplicate


def test_contested_candidates_queue_instead_of_racing(conn, client, tmp_path: Path) -> None:
    """Bound + proposed files for one insured must not race for one manual
    placement — the real two-JSON case."""
    manual = placements.create(
        conn, client.id, "Casualty Program", "2026-06-01", "2027-06-01"
    )
    bound = write_program(
        tmp_path / "p" / "bound.json",
        make_program("Test Client, Inc.", "2026-07-01", "2027-07-01"),
    )
    proposed = write_program(
        tmp_path / "p" / "proposed.json",
        make_program("Test Client, Inc.", "2026-07-01", "2027-07-01", placed=False),
    )
    links.confirm(conn, str(bound), client.id, "Test Client, Inc.")
    links.confirm(conn, str(proposed), client.id, "Test Client, Inc.")
    report = sync.project_all(conn, [tmp_path / "p"])
    assert len(report.needs_placement) == 2, "both files must queue, neither adopts"
    assert len(placements.for_org(conn, client.id)) == 1, "nothing created while contested"

    # user resolves: bound file → the manual record; proposed → a fresh one
    assert sync.confirm_placement(conn, bound, manual.id).ok
    assert sync.confirm_placement(conn, proposed, None).ok
    rows = placements.for_org(conn, client.id)
    assert len(rows) == 2
    by_path = {p.program_path for p in rows}
    assert by_path == {str(bound), str(proposed)}


def test_standing_confirmation_auto_links_new_year(conn, client, tmp_path: Path) -> None:
    first = write_program(
        tmp_path / "p" / "test-2026.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01"),
    )
    assert sync.confirm_link(conn, first, client.id).ok  # user confirms once
    second = write_program(
        tmp_path / "p" / "test-2027.json",
        make_program("Test Client, Inc.", "2027-01-01", "2028-01-01"),
    )
    report = sync.project_all(conn, [tmp_path / "p"])
    assert report.ok and not report.needs_link
    assert any("insured previously confirmed" in how for _, how in report.relinked)
    row = [r for r in links.all_links(conn) if r["path"] == str(second)][0]
    assert row["source"] == "insured_match"
    assert placements.by_program_path(conn, str(second)) is not None


def test_fuzzy_insured_still_queues(conn, client, tmp_path: Path) -> None:
    """Anything short of byte-identical goes to review, exactly as before."""
    first = write_program(
        tmp_path / "p" / "a.json", make_program("Test Client, Inc.", "2026-01-01", "2027-01-01")
    )
    assert sync.confirm_link(conn, first, client.id).ok
    typo = write_program(
        tmp_path / "p" / "b.json", make_program("Test Client Inc", "2027-01-01", "2028-01-01")
    )
    report = sync.project_all(conn, [tmp_path / "p"])
    assert [s.path for s in report.needs_link] == [typo]


def test_rename_detection(conn, client, tmp_path: Path) -> None:
    old = write_program(
        tmp_path / "p" / "old-name.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01"),
    )
    assert sync.confirm_link(conn, old, client.id).ok
    placement = placements.by_program_path(conn, str(old))
    new = old.with_name("renamed.json")
    old.rename(new)
    report = sync.project_all(conn, [tmp_path / "p"])
    assert any("rename" in how for _, how in report.relinked)
    refreshed = placements.get(conn, placement.id)
    assert refreshed.program_path == str(new)
    assert links.org_for_path(conn, str(old)) is None
    assert len(placements.for_org(conn, client.id)) == 1


def test_renamed_and_edited_queues_for_repoint(conn, client, tmp_path: Path) -> None:
    """Content changed AND path changed: the org auto-links (same insured, a
    standing confirmation), but the dangling placement is only OFFERED for
    re-pointing — never auto-adopted, and no duplicate is created."""
    old = write_program(
        tmp_path / "p" / "old.json", make_program("Old Insured Name", "2026-01-01", "2027-01-01")
    )
    assert sync.confirm_link(conn, old, client.id).ok
    dangling = placements.by_program_path(conn, str(old))
    program = load_program(old)
    program.layers[0].premium = 950_000
    new = old.with_name("moved.json")
    dump_program(program, new)
    old.unlink()
    report = sync.project_all(conn, [tmp_path / "p"])
    assert not report.needs_link, "insured is byte-identical — org link is automatic"
    assert [s.path for s in report.needs_placement] == [new]
    assert [c.id for c in report.needs_placement[0].candidates] == [dangling.id]
    assert len(placements.for_org(conn, client.id)) == 1, "no duplicate created"

    # user confirms the re-point: link follows, dead path forgotten
    assert sync.confirm_placement(conn, new, dangling.id).ok
    refreshed = placements.get(conn, dangling.id)
    assert refreshed.program_path == str(new)
    assert refreshed.total_premium == 950_000 * 100
    assert links.org_for_path(conn, str(old)) is None


def test_exact_period_never_steals_linked_placement(conn, client, tmp_path: Path) -> None:
    a = write_program(
        tmp_path / "p" / "a.json", make_program("Test Client, Inc.", "2026-01-01", "2027-01-01")
    )
    assert sync.confirm_link(conn, a, client.id).ok
    b = write_program(
        tmp_path / "p" / "b.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01", placed=False),
    )
    links.confirm(conn, str(b), client.id, "Test Client, Inc.")
    assert sync.project(conn, b).ok
    rows = placements.for_org(conn, client.id)
    assert len(rows) == 2
    assert {p.program_path for p in rows} == {str(a), str(b)}


def test_opportunity_candidates_from_proposed_tbd_lines(conn, client, tmp_path: Path) -> None:
    path = write_program(
        tmp_path / "p" / "proposed.json",
        make_program("Test Client, Inc.", "2027-01-01", "2028-01-01", placed=False, tbd_line=True),
    )
    assert sync.confirm_link(conn, path, client.id).ok
    candidates = sync.opportunities_for_path(conn, path)
    assert [c.line_id for c in candidates] == ["cy"], "only the unplaced line is offered"
    candidate = candidates[0]
    assert candidate.premium == 400_000 * 100
    assert candidate.target_effective == "2027-01-01"
    opp = sync.create_opportunity(conn, candidate)
    assert opp.lines == "cy" and opp.target_premium == 400_000 * 100
    assert sync.opportunities_for_path(conn, path) == [], "not re-offered once tracked"


def test_bound_program_offers_nothing(conn, client, tmp_path: Path) -> None:
    path = write_program(
        tmp_path / "p" / "bound.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01", tbd_line=True),
    )
    assert sync.confirm_link(conn, path, client.id).ok
    assert sync.opportunities_for_path(conn, path) == []


def test_renew_file_backed_links_at_birth(conn, client, tmp_path: Path) -> None:
    path = write_program(
        tmp_path / "p" / "test-2026.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01"),
    )
    assert sync.confirm_link(conn, path, client.id).ok
    placement = placements.by_program_path(conn, str(path))
    placements.update(conn, placement.id, commission_bps=1500)

    new_placement, new_path, diags = sync.renew(conn, placement.id)
    assert diags.ok and new_placement is not None and new_path is not None
    assert new_path.name == "test-2027.json"
    clone = load_program(new_path)
    assert clone.placement.value == "proposed"
    assert clone.period.start.isoformat() == "2027-01-01"
    assert new_placement.program_path == str(new_path)
    assert new_placement.status == "prospective"
    assert new_placement.commission_bps == 1500  # carried forward
    assert projection.layers_for_placement(conn, new_placement.id), "projected at birth"
    row = [r for r in links.all_links(conn) if r["path"] == str(new_path)][0]
    assert row["source"] == "renewal"

    # renewing again must refuse rather than overwrite next year's file
    blocked, blocked_path, blocked_diags = sync.renew(conn, placement.id)
    assert blocked is None and not blocked_diags.ok


def test_renew_bookkit_only(conn, client) -> None:
    placement = placements.create(
        conn, client.id, "2026 Marine Program", "2026-04-01", "2027-04-01",
        total_premium=50_000_00, commission_bps=1000,
    )
    new_placement, new_path, diags = sync.renew(conn, placement.id)
    assert diags.ok and new_path is None
    assert new_placement.period_from == "2027-04-01"
    assert new_placement.period_to == "2028-04-01"
    assert new_placement.program_name == "2027 Marine Program"
    assert new_placement.total_premium == 50_000_00  # expiring indication


def test_merge_moves_children_and_carries_link(conn, client, tmp_path: Path) -> None:
    market = orgs.create(conn, kind="market", name="Chubb", status="active")
    path = write_program(
        tmp_path / "p" / "dup.json", make_program("Test Client, Inc.", "2026-01-01", "2027-01-01")
    )
    assert sync.confirm_link(conn, path, client.id).ok
    linked = placements.by_program_path(conn, str(path))
    # a manual duplicate accumulated its own children
    manual = placements.create(
        conn, client.id, "Casualty (dup)", "2026-01-02", "2027-01-01"
    )
    submissions.create(conn, market.id, "2026-05-01", placement_id=manual.id)
    tasks_repo.create(conn, "Chase quote", org_id=client.id, placement_id=manual.id)
    documents.add(conn, client.id, "SOV", "/tmp/sov.xlsx", placement_id=manual.id)

    # merge the file-backed one INTO the manual (link must carry)
    result = merge_placements(conn, linked.id, manual.id)
    assert (result.moved_submissions, result.moved_tasks, result.moved_documents) == (0, 0, 0)
    assert result.carried_link
    survivor = placements.get(conn, manual.id)
    assert survivor.program_path == str(path)
    assert projection.layers_for_placement(conn, survivor.id)
    with pytest.raises(KeyError):
        placements.get(conn, linked.id)  # soft-deleted
    assert len(submissions.for_placement(conn, survivor.id)) == 1  # survivor kept its own

    # and the other direction: children move to the target
    other = placements.create(conn, client.id, "Other", "2027-02-01", "2028-02-01")
    submissions.create(conn, market.id, "2026-06-01", placement_id=other.id)
    result2 = merge_placements(conn, other.id, manual.id)
    assert result2.moved_submissions == 1
    assert len(submissions.for_placement(conn, survivor.id)) == 2


def test_merge_refuses_two_file_backed(conn, client, tmp_path: Path) -> None:
    a = write_program(
        tmp_path / "p" / "a.json", make_program("Test Client, Inc.", "2026-01-01", "2027-01-01")
    )
    b = write_program(
        tmp_path / "p" / "b.json",
        make_program("Test Client, Inc.", "2027-01-01", "2028-01-01", placed=False),
    )
    assert sync.confirm_link(conn, a, client.id).ok
    links.confirm(conn, str(b), client.id, "Test Client, Inc.")
    assert sync.project(conn, b, create_new=True).ok
    pa = placements.by_program_path(conn, str(a))
    pb = placements.by_program_path(conn, str(b))
    with pytest.raises(MergeError):
        merge_placements(conn, pa.id, pb.id)


def test_program_link_source_migration(conn) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(program_link)").fetchall()}
    assert "source" in cols


def test_renamed_line_does_not_duplicate_its_opportunity(conn, client, tmp_path: Path) -> None:
    """towerkit ids follow renames now (towerkit 67ac42f): fixing a typo in a
    line's name re-slugs its id, and every appliesTo in the file follows. The
    opportunity created before the rename still holds the OLD id, so an exact
    dedupe would re-offer the line and the next sync would create a duplicate.
    A near-identical id on the same effective date is the same line."""
    prog = make_program(
        "Test Client, Inc.", "2027-01-01", "2028-01-01",
        placed=False, tbd_line=True,
    )
    # the typo'd id, as it was when the opportunity was created
    prog.lines[1] = Line(id="cybr", name="Cybr", abbr="CY")
    prog.layers[1] = Layer(
        id="primary-cy", name="Primary Cyber", applies_to=["cybr"],
        attach=0, limit=5_000_000, premium=400_000, participants=[],
    )
    prog.retentions = [
        Retention(applies_to=[ln.id], type=RetentionType.DEDUCTIBLE, amount=100_000)
        for ln in prog.lines
    ]
    path = write_program(tmp_path / "p" / "proposed.json", prog)
    assert sync.confirm_link(conn, path, client.id).ok
    (candidate,) = sync.opportunities_for_path(conn, path)
    sync.create_opportunity(conn, candidate)  # stores lines="cybr"

    # Grant fixes the typo in towerkit: id follows, references cascade
    prog.lines[1] = Line(id="cyber", name="Cyber", abbr="CY")
    prog.layers[1] = Layer(
        id="primary-cy", name="Primary Cyber", applies_to=["cyber"],
        attach=0, limit=5_000_000, premium=400_000, participants=[],
    )
    prog.retentions = [
        Retention(applies_to=[ln.id], type=RetentionType.DEDUCTIBLE, amount=100_000)
        for ln in prog.lines
    ]
    write_program(path, prog)

    assert sync.opportunities_for_path(conn, path) == [], (
        "a renamed line must not re-offer the opportunity it already has"
    )


def test_a_genuinely_new_line_is_still_offered(conn, client, tmp_path: Path) -> None:
    """The fuzzy bridge must not swallow real new lines."""
    prog = make_program(
        "Test Client, Inc.", "2027-01-01", "2028-01-01",
        placed=False, tbd_line=True,
    )
    path = write_program(tmp_path / "p" / "proposed.json", prog)
    assert sync.confirm_link(conn, path, client.id).ok
    (candidate,) = sync.opportunities_for_path(conn, path)
    sync.create_opportunity(conn, candidate)          # cyber is now tracked

    # a genuinely different unplaced line appears
    prog.lines.append(Line(id="el", name="Employers Liability", abbr="EL"))
    prog.layers.append(Layer(
        id="primary-el", name="Primary EL", applies_to=["el"],
        attach=0, limit=1_000_000, premium=50_000, participants=[],
    ))
    prog.retentions = []
    write_program(path, prog)

    offered = [c.line_id for c in sync.opportunities_for_path(conn, path)]
    assert offered == ["el"]


def test_rename_cutoff_boundaries_hold() -> None:
    """The bridge's floor sits between real-world clusters: one-typo id fixes
    score above it, genuinely distinct sibling lines below. If rapidfuzz's
    scoring shifts under an upgrade, this is the test that says so."""
    from rapidfuzz import fuzz

    from bookkit.sync import _RENAME_CUTOFF

    # same line, typo fixed — must bridge
    assert fuzz.ratio("cybr", "cyber") >= _RENAME_CUTOFF
    assert fuzz.ratio("genral-liability", "general-liability") >= _RENAME_CUTOFF
    # different lines — must NOT bridge
    assert fuzz.ratio("cyber", "cyber-2") < _RENAME_CUTOFF
    assert fuzz.ratio("property", "property-dic") < _RENAME_CUTOFF
    assert fuzz.ratio("auto-liability", "general-liability") < _RENAME_CUTOFF


def test_a_numbered_sibling_line_is_not_swallowed_by_the_rename_bridge(
    conn, client, tmp_path: Path
) -> None:
    """fuzz.ratio scales with length: 'general-liability-2' scores 94 against
    'general-liability', so a pure-ratio bridge would declare the NEW sibling
    already tracked and its opportunity would silently never exist. The bridge
    must only fire when the stored id matches no current line (i.e. the old id
    actually vanished in a rename)."""
    prog = make_program(
        "Test Client, Inc.", "2027-01-01", "2028-01-01",
        placed=False, tbd_line=False,
    )
    prog.lines = [Line(id="general-liability", name="General Liability")]
    prog.layers = [Layer(
        id="primary", name="Primary", applies_to=["general-liability"],
        attach=0, limit=2_000_000, premium=900_000, participants=[],
    )]
    prog.retentions = [Retention(
        applies_to=["general-liability"],
        type=RetentionType.DEDUCTIBLE, amount=100_000,
    )]
    path = write_program(tmp_path / "p" / "proposed.json", prog)
    assert sync.confirm_link(conn, path, client.id).ok
    (candidate,) = sync.opportunities_for_path(conn, path)
    sync.create_opportunity(conn, candidate)      # stores "general-liability"

    # a second, genuinely distinct line whose slug collided (unique_id's -2)
    prog.lines.append(Line(id="general-liability-2", name="General Liability 2"))
    prog.layers.append(Layer(
        id="xs", name="Excess", applies_to=["general-liability-2"],
        attach=0, limit=5_000_000, premium=100_000, participants=[],
    ))
    prog.retentions.append(Retention(
        applies_to=["general-liability-2"],
        type=RetentionType.DEDUCTIBLE, amount=100_000,
    ))
    write_program(path, prog)

    offered = [c.line_id for c in sync.opportunities_for_path(conn, path)]
    assert offered == ["general-liability-2"], (
        "the new sibling must be offered — its near-identical slug is not a rename"
    )
