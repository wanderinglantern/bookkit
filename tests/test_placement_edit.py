"""services/placement_edit — the dual-owner split as ONE rule, two callers.

The split (name/period write through the towerkit file when linked;
status/commission live on the row) used to live inside the TUI widget module
where no route could reach it, so building web placement-edit meant
re-deriving it and letting two copies drift (review finding F12,
2026-08-19). These tests pin the rule where both surfaces now get it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_linking_flow import make_program, write_program
from towerkit.model import load_program

from bookkit import sync
from bookkit.repo import batches as batches_repo
from bookkit.repo import orgs, placements
from bookkit.services import batches as batches_svc
from bookkit.services import placement_edit


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


def _apply(conn, placement, changes):
    """apply inside a batch, the way FormModal calls it."""
    with batches_svc.open_batch(
        conn, source="tui", tool="placement_edit",
        summary="edit (test)", org_id=placement.org_id,
    ):
        placement_edit.apply(conn, placement, changes)


def test_a_file_owned_field_writes_through_the_file(linked):
    conn, _, placement, path = linked

    _apply(conn, placement, {"program_name": "Renamed Program"})

    assert load_program(path).program == "Renamed Program"
    assert placements.get(conn, placement.id).program_name == "Renamed Program"


def test_a_book_owned_field_never_touches_the_file(linked):
    conn, _, placement, path = linked
    before = path.read_bytes()

    _apply(conn, placement, {"status": "quoted", "commission_bps": 1250})

    refreshed = placements.get(conn, placement.id)
    assert refreshed.status == "quoted"
    assert refreshed.commission_bps == 1250
    assert path.read_bytes() == before, "a book-owned edit wrote to the program file"


def test_an_unlinked_placement_takes_file_keys_on_the_row(conn):
    client = orgs.create(conn, kind="client", name="Bare Co", status="active")
    placement = placements.create(
        conn, client.id, "Bare Program", "2026-03-01", "2027-03-01"
    )

    _apply(conn, placement, {"program_name": "Bare Program II", "status": "submitted"})

    refreshed = placements.get(conn, placement.id)
    assert refreshed.program_name == "Bare Program II"
    assert refreshed.status == "submitted"


def test_a_refusal_carries_towerkits_words_and_writes_nothing(linked):
    conn, _, placement, path = linked
    before = path.read_bytes()

    with pytest.raises(ValueError) as refused:
        _apply(conn, placement, {"period_to": "2025-01-01"})

    assert path.read_bytes() == before
    assert "period" in str(refused.value).lower() or "end" in str(refused.value).lower()


def test_a_mixed_edit_is_one_batch(linked):
    """A TUI form save that moves both a file fact and a book fact must stay
    ONE undo unit — the inner batches join the outer one (db.transaction
    nests by joining)."""
    conn, _, placement, path = linked
    before = [b.ref for b in batches_repo.recent(conn, "2000-01-01", limit=50)]

    _apply(conn, placement, {"program_name": "Mixed Edit", "status": "quoted"})

    after = [
        b for b in batches_repo.recent(conn, "2000-01-01", limit=50)
        if b.ref not in before
    ]
    assert len(after) == 1, f"a mixed edit split into {len(after)} undo units"
    assert load_program(path).program == "Mixed Edit"
    assert placements.get(conn, placement.id).status == "quoted"


def test_unchanged_values_write_nothing(linked):
    """The TUI form posts every field back; only actual diffs may write —
    otherwise every save stamps file and row with no-op events."""
    conn, _, placement, path = linked
    sha_before = sync.file_sha256(path)

    _apply(
        conn, placement,
        {"program_name": placement.program_name, "status": placement.status},
    )

    assert sync.file_sha256(path) == sha_before, "a no-op edit rewrote the file"


def test_apply_refuses_to_run_unbatched(linked):
    """An event written outside any batch is unreachable by R — apply must
    refuse rather than write one."""
    conn, _, placement, path = linked

    with pytest.raises(RuntimeError, match="inside a batch"):
        placement_edit.apply(conn, placement, {"status": "quoted"})

    assert placements.get(conn, placement.id).status != "quoted"


def test_split_folds_file_keys_for_unlinked(conn):
    client = orgs.create(conn, kind="client", name="Split Co", status="active")
    placement = placements.create(conn, client.id, "P", "2026-03-01", "2027-03-01")

    file_changes, book_changes = placement_edit.split(
        placement, {"program_name": "Q", "status": "submitted"}
    )

    assert file_changes == {}
    assert book_changes == {"program_name": "Q", "status": "submitted"}


def test_split_rejects_unknown_keys(linked):
    conn, _, placement, _ = linked
    with pytest.raises(ValueError, match="not editable"):
        placement_edit.split(placement, {"total_premium": 1})


def test_tui_edit_placement_routes_through_the_service():
    """A green suite proves nothing broke, not that the new path is taken
    (CLAUDE.md): the TUI widget must actually call the service, and must no
    longer carry its own copy of the split."""
    src = Path("src/bookkit/tui/widgets/entity_actions.py").read_text()
    assert "placement_edit.apply" in src, "the TUI does not use the service"
    assert "update_program" not in src, "the TUI still carries its own split"


def test_a_file_writing_batch_refuses_the_row_only_revert(linked):
    """The undo-corruption class the phase-2 review caught: a TUI form batch
    with a title-derived tool ("edit_plc-0001") sailed past revert()'s
    program_ guard, so `u` rolled back program_name and source_sha256 on the
    ROW while the towerkit file kept the change — a silent cache/file split
    whose next symptom is a false write-through conflict. File-writing TUI
    forms now stamp program_* tools; this asserts the guard actually bites."""
    from bookkit.db import utc_now

    conn, _, placement, path = linked
    with batches_svc.open_batch(
        conn, source="tui", tool="program_edit",
        summary=f"edited {placement.ref}", org_id=placement.org_id,
    ):
        placement_edit.apply(conn, placement, {"program_name": "Renamed By TUI"})
    batch = batches_repo.most_recent(conn)
    assert batch is not None and batch.tool == "program_edit"

    with pytest.raises(ValueError, match="program"):
        batches_svc.revert(conn, batch.ref, utc_now())

    # nothing moved: the file and the row still agree
    from towerkit.model import load_program

    assert load_program(path).program == "Renamed By TUI"
    assert placements.get(conn, placement.id).program_name == "Renamed By TUI"


def test_every_file_writing_tui_form_stamps_a_program_tool():
    """The seam check for the fix above: a bare FormModal(spec, commit=commit)
    whose commit writes a towerkit file gets the title-derived tool and slips
    the revert guard. The two modules that push file-writing forms must pass
    explicit program_* BatchSpecs."""
    ea = Path("src/bookkit/tui/widgets/entity_actions.py").read_text()
    acct = Path("src/bookkit/tui/screens/account.py").read_text()

    assert '"program_edit"' in ea, "edit_placement lost its program_ tool"
    assert '"program_layer_edit"' in ea, "edit_layer lost its program_ tool"
    for tool in ('"program_layer_add"', '"program_layer_edit"', '"program_bind"'):
        assert tool in acct, f"account.py lost {tool}"
