"""Where a placement's towerkit file is, and repairing the link when it moves.

The bug these exist for: on 2026-08-20 Grant moved his towerkit checkout out of
OneDrive and all five of his linked programs became FileNotFoundError, which
five silent `except Exception: return []` blocks rendered as "the linked file
has no layers yet". The reads now say why (tests/test_web_program.py), and
these pin the two halves that stop it happening again — storing paths relative
to a root, and `bookctl relink`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from bookkit import db, programpath, sync
from bookkit.repo import links, orgs, placements, settings
from bookkit.services import relink


def _write_program(path: Path, insured: str = "Atomic Industries") -> None:
    """Build the fixture through towerkit's own model and serialiser.

    Hand-written JSON here failed the schema on a key the model fills in for
    free (`retentions`), which is the sort of fixture that rots the moment
    towerkit adds a field. dump_program is the canonical writer bookkit itself
    uses, so a file written here is exactly a file bookkit would write."""
    from towerkit.model import Layer, Line, Participant, Period, Placement, Program, dump_program

    dump_program(
        Program(
            insured=insured,
            program="Casualty",
            placement=Placement.BOUND,
            period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
            lines=[Line(id="gl", name="General Liability")],
            layers=[
                Layer(
                    id="primary",
                    name="Primary GL",
                    applies_to=["gl"],
                    attach=0,
                    limit=1_000_000,
                    premium=50_000,
                    participants=[Participant(carrier="Chubb", share_bps=10_000)],
                )
            ],
        ),
        path,
    )


@pytest.fixture
def book(tmp_path: Path):
    """A book with one root, one account and one projected program."""
    conn = db.connect(tmp_path / "book.db")
    db.apply_migrations(conn)
    root = tmp_path / "programs"
    root.mkdir()
    settings.set_program_roots(conn, [str(root)])
    org = orgs.create(conn, kind="client", name="Atomic Industries")
    path = root / "atomic-2026.json"
    _write_program(path)
    links.confirm(conn, programpath.store(conn, path), org.id, "Atomic Industries")
    assert sync.project(conn, path).ok
    yield conn, org, root, path
    conn.close()


def _placement(conn: sqlite3.Connection, org):
    return placements.for_org(conn, org.id)[0]


# --- storing --------------------------------------------------------------


def test_a_file_under_a_root_is_stored_relative_to_it(book):
    """The whole point: moving the tree is then one `bookctl roots` call, not
    a per-row repair."""
    conn, org, _root, _path = book

    stored = _placement(conn, org).program_path

    assert stored == "atomic-2026.json"


def test_a_file_outside_every_root_is_stored_absolute(book, tmp_path):
    """A program kept outside the roots is still a legitimate link. Inventing
    a relative path against a root it does not live under would point at
    nothing at all."""
    conn, _org, _root, _path = book
    outside = tmp_path / "elsewhere" / "stray.json"
    outside.parent.mkdir()
    _write_program(outside, "Stray Co")

    assert programpath.store(conn, outside) == str(outside.resolve())


def test_the_deepest_containing_root_wins(book, tmp_path):
    """Roots nest in real setups (`~/towerkit` and `~/towerkit/programs`).
    Relative to the shallower one survives a move of the deeper one only by
    accident."""
    conn, _org, root, path = book
    settings.set_program_roots(conn, [str(root.parent), str(root)])

    assert programpath.store(conn, path) == "atomic-2026.json"


# --- resolving and recovering ---------------------------------------------


def test_moving_the_root_moves_every_placement_with_it(book, tmp_path):
    """Grant's exact failure, as the fix experiences it."""
    conn, org, root, _path = book
    moved = tmp_path / "Scripts" / "towerkit" / "programs"
    moved.parent.mkdir(parents=True)
    root.rename(moved)
    settings.set_program_roots(conn, [str(moved)])

    assert len(sync.layer_details(conn, _placement(conn, org).id)) == 1
    assert sync.program_load_error(conn, _placement(conn, org).id) is None


def test_an_absolute_path_whose_tree_moved_is_recovered_and_says_so(book, tmp_path):
    """A book written before paths were relative still holds absolute ones.
    The read succeeds from the new location and reports that it did — a file
    answering from a path the database does not record is how one program
    quietly ends up serving two placements."""
    conn, org, root, path = book
    placements.update(
        conn, _placement(conn, org).id,
        program_path=str(tmp_path / "OneDrive" / "programs" / path.name),
    )

    linked = sync.linked_program(conn, _placement(conn, org).id)

    assert linked.program is not None
    assert linked.moved_from is not None
    assert linked.path == (root / path.name).resolve()


def test_the_same_name_under_two_roots_is_refused_not_guessed(book, tmp_path):
    """Attaching a client's tower to the wrong account is worse than a
    refusal — the rule sync's link review already follows."""
    conn, org, root, path = book
    other = tmp_path / "archive"
    other.mkdir()
    (other / path.name).write_text(path.read_text())
    settings.set_program_roots(conn, [str(root), str(other)])
    placements.update(
        conn, _placement(conn, org).id, program_path=str(tmp_path / "gone" / path.name)
    )

    error = sync.program_load_error(conn, _placement(conn, org).id)

    assert error is not None
    assert "more than one program root" in error
    assert "relink" in error


def test_a_deeper_tail_disambiguates_before_the_basename_is_reached(book, tmp_path):
    """`programs/2026/atomic.json` beats `atomic.json`: depth is the only
    evidence available that two same-named files are the same program."""
    conn, _org, root, _path = book
    (root / "2026").mkdir()
    _write_program(root / "2026" / "twin.json")
    (root / "2027").mkdir()
    _write_program(root / "2027" / "twin.json")

    where = programpath.resolve(conn, "/old/place/2026/twin.json")

    assert where.path == (root / "2026" / "twin.json").resolve()


def test_a_missing_file_reports_where_it_looked(book, tmp_path):
    """"File not found" tells a broker nothing about a path they never typed."""
    conn, org, root, _path = book
    placements.update(conn, _placement(conn, org).id, program_path="ghost.json")

    error = sync.program_load_error(conn, _placement(conn, org).id)

    assert error is not None
    assert "ghost.json" in error
    assert str(root) in error


def test_a_corrupt_file_reports_towerkit_s_own_complaint(book):
    """The panel prints this. towerkit's ValidationError names the field and
    the value; a bare "could not read" would not."""
    conn, org, _root, path = book
    bad = json.loads(path.read_text())
    bad["layers"][0]["limit"] = "not-a-number"
    path.write_text(json.dumps(bad))

    error = sync.program_load_error(conn, _placement(conn, org).id)

    assert error is not None
    assert path.name in error
    assert "limit" in error


# --- lookups tolerate both spellings --------------------------------------


def test_a_placement_is_found_by_either_spelling_of_its_path(book):
    """A book part-way through the change holds both. An equality test against
    one spelling misses the other, and `sync` would then decide it had never
    seen the file — adopting a different placement, or creating a duplicate
    beside the real one."""
    conn, org, _root, path = book
    by_absolute = placements.by_program_path(conn, str(path))
    placements.update(conn, _placement(conn, org).id, program_path=str(path.resolve()))
    by_relative = placements.by_program_path(conn, str(path))

    assert by_absolute is not None and by_relative is not None
    assert by_absolute.id == by_relative.id


def test_re_syncing_after_a_move_does_not_duplicate_the_placement(book, tmp_path):
    """The failure the spelling-agnostic lookup exists to stop."""
    conn, org, root, _path = book
    moved = tmp_path / "moved"
    root.rename(moved)
    settings.set_program_roots(conn, [str(moved)])

    report = sync.project_all(conn, [moved])

    assert report.ok, report.render()
    assert len(placements.for_org(conn, org.id)) == 1


# --- bookctl relink --------------------------------------------------------


def test_relink_reports_without_writing(book, tmp_path):
    conn, org, root, path = book
    placements.update(
        conn, _placement(conn, org).id, program_path=str(tmp_path / "old" / path.name)
    )

    findings = relink.inspect(conn)
    text = relink.render(findings, repaired=False)

    assert [f.verdict for f in findings] == ["moved"]
    assert "would repair" in text
    assert "nothing was written" in text
    assert _placement(conn, org).program_path.endswith("old/atomic-2026.json")


def test_relink_write_repairs_a_moved_tree(book, tmp_path):
    conn, org, _root, path = book
    placements.update(
        conn, _placement(conn, org).id, program_path=str(tmp_path / "old" / path.name)
    )

    relink.repair(conn, relink.inspect(conn))

    assert _placement(conn, org).program_path == "atomic-2026.json"
    assert sync.program_load_error(conn, _placement(conn, org).id) is None
    assert [f.verdict for f in relink.inspect(conn)] == ["ok"]


def test_relink_restates_an_absolute_path_that_already_works(book):
    """The repair that stops the NEXT move from breaking anything. A row that
    resolves fine today is still wrong if it is absolute."""
    conn, org, _root, path = book
    placements.update(conn, _placement(conn, org).id, program_path=str(path.resolve()))

    findings = relink.inspect(conn)
    assert [f.verdict for f in findings] == ["restate"]
    relink.repair(conn, findings)

    assert _placement(conn, org).program_path == "atomic-2026.json"


def test_relink_finds_a_renamed_file_by_its_content(book):
    """Byte-identical content is the only evidence strong enough to re-point a
    link on its own — the rule sync._detect_rename already applies."""
    conn, org, root, path = book
    renamed = root / "atomic-industries-2026.json"
    path.rename(renamed)

    findings = relink.inspect(conn)
    assert [f.verdict for f in findings] == ["renamed"]
    relink.repair(conn, findings)

    assert _placement(conn, org).program_path == "atomic-industries-2026.json"


def test_relink_leaves_a_genuinely_lost_file_alone_and_names_it(book, tmp_path):
    """Inventing a link to whatever else is lying around is how a client's
    tower ends up attached to the wrong account."""
    conn, org, _root, path = book
    path.unlink()

    findings = relink.inspect(conn)

    assert [f.verdict for f in findings] == ["lost"]
    assert not findings[0].repairable
    assert "atomic-2026.json" in relink.render(findings, repaired=False)
    assert relink.repair(conn, findings) == []


def test_relink_refuses_two_files_with_the_same_content(book, tmp_path):
    conn, org, root, path = book
    (root / "copy-a.json").write_text(path.read_text())
    (root / "copy-b.json").write_text(path.read_text())
    path.unlink()

    findings = relink.inspect(conn)

    assert [f.verdict for f in findings] == ["ambiguous"]
    assert not findings[0].repairable
    assert "2 files have this placement's content" in findings[0].detail


def test_relink_repairs_the_link_row_as_well_as_the_placement(book, tmp_path):
    """Half a repair leaves the old link row to re-claim the file on the next
    sweep, under whichever account it used to name."""
    conn, org, root, path = book
    stale = str(tmp_path / "old" / path.name)
    links.forget(conn, programpath.store(conn, path))
    links.confirm(conn, stale, org.id, "Atomic Industries")
    placements.update(conn, _placement(conn, org).id, program_path=stale)

    relink.repair(conn, relink.inspect(conn))

    assert links.org_for_path(conn, str(path)) == org.id
    rows = [dict(r) for r in links.all_links(conn)]
    assert [r["path"] for r in rows] == ["atomic-2026.json"]
