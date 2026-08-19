"""The dual-owner placement edit, as ONE rule with two callers.

A linked placement's name and period live in the towerkit FILE (the file is
the authority for program structure; the row is a projection), while status
and commission are bookkit's own. This split used to live inside
tui/widgets/entity_actions.edit_placement — business logic in a TUI widget
module, unreachable from a web route — so building web placement-edit meant
re-deriving it and letting two copies drift (review finding F12, 2026-08-19).
The rule lives here now; both surfaces consult it.

BATCHING BELONGS TO THE CALLER, deliberately. services.batches.open_batch
creates a batch ROW even when its transaction joins an outer one, so a
service that opened batches of its own would leave phantom empty entries in
the changes rail every time a form save wrapped it. The TUI's FormModal
batch wraps `apply` (one form, one undo unit, exactly as before); the web's
cell routes write ONE field at a time, so they batch per owner — the file
half through services.program_files.write (snapshot + batch), the book half
in a plain open_batch — using `split` so the ownership rule cannot fork.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import sync
from ..repo import placements as placements_repo

# Owned by the towerkit file WHEN the placement is linked; on the row when it
# is not (an unlinked placement's name and dates have nowhere else to live).
FILE_OWNED = ("program_name", "period_from", "period_to")
BOOK_OWNED = ("status", "commission_bps")


def split(
    placement: Any, changes: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(file_changes, book_changes) — diffed, with None ("unchanged", the
    forms.spec.dropped rule) and already-current values stripped, and unknown
    keys refused. For an UNLINKED placement the file-owned keys fold into the
    book half: the row is the only home those facts have."""
    diffs = {
        key: value
        for key, value in changes.items()
        if value is not None and value != getattr(placement, key)
    }
    unknown = set(diffs) - set(FILE_OWNED) - set(BOOK_OWNED)
    if unknown:
        raise ValueError(f"not editable placement fields: {sorted(unknown)}")
    file_changes = {k: v for k, v in diffs.items() if k in FILE_OWNED}
    book_changes = {k: v for k, v in diffs.items() if k in BOOK_OWNED}
    if file_changes and not placement.program_path:
        book_changes.update(file_changes)
        file_changes = {}
    return file_changes, book_changes


def write_file_fields(
    conn: sqlite3.Connection, placement: Any, file_changes: dict[str, Any]
) -> Any:
    """The file half, as the Diagnostics-returning writer the snapshot seam
    (services.program_files.write) takes as its `mutate`."""
    return sync.update_program(conn, placement.id, **file_changes)


def write_book_fields(
    conn: sqlite3.Connection, placement: Any, book_changes: dict[str, Any]
) -> None:
    placements_repo.update(conn, placement.id, **book_changes)


def apply(conn: sqlite3.Connection, placement: Any, changes: dict[str, Any]) -> None:
    """A whole-form edit, split by owner, INSIDE THE CALLER'S BATCH — the
    TUI's FormModal already opens one around every save, which is what makes
    a mixed edit one undo unit. Raises ValueError on refusal, file half
    first, so a refused write-through rolls the whole save back before the
    book half lands.

    Refuses to run unbatched: an event written outside any batch is
    unreachable by R, which is exactly the class of quiet gap the batch
    spine exists to close.
    """
    if not conn.in_transaction:
        raise RuntimeError(
            "placement_edit.apply must run inside a batch — wrap the call in "
            "services.batches.open_batch (FormModal does this for TUI forms)"
        )
    file_changes, book_changes = split(placement, changes)
    if file_changes:
        diags = write_file_fields(conn, placement, file_changes)
        if not diags.ok:
            raise ValueError(f"refused: {diags.errors[0]}")
    if book_changes:
        write_book_fields(conn, placement, book_changes)
