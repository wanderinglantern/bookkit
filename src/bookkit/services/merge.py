"""Merge duplicate placements into one unified record.

The target survives; the source's submissions, tasks, and documents move to
it, the file link carries over when only the source has one, and the source is
soft-deleted (recoverable with undo). Two file-backed placements never merge —
that would be two sources of truth, which is the situation §5 exists to
prevent."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..models import Placement
from ..repo import base, documents, placements, projection, submissions, tasks


class MergeError(ValueError):
    pass


@dataclass(frozen=True)
class MergeResult:
    target: Placement
    moved_submissions: int
    moved_tasks: int
    moved_documents: int
    carried_link: bool


def merge_placements(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> MergeResult:
    if source_id == target_id:
        raise MergeError("a placement cannot merge into itself")
    source = placements.get(conn, source_id)
    target = placements.get(conn, target_id)
    if source.org_id != target.org_id:
        raise MergeError(f"{source.ref} and {target.ref} belong to different accounts")
    if source.program_path and target.program_path:
        raise MergeError(
            f"both {source.ref} and {target.ref} are file-backed — two sources of "
            "truth cannot merge; unlink one first"
        )

    moved_subs = submissions.reassign_placement(conn, source.id, target.id)
    moved_tasks = tasks.reassign_placement(conn, source.id, target.id)
    moved_docs = documents.reassign_placement(conn, source.id, target.id)

    carried_link = False
    if source.program_path and not target.program_path:
        projection.reassign(conn, source.id, target.id)
        placements.update(
            conn,
            target.id,
            note=f"link carried from {source.ref} on merge",
            program_path=source.program_path,
            source_sha256=source.source_sha256,
            synced_at=source.synced_at,
        )
        placements.update(
            conn, source.id, note="link moved on merge",
            program_path=None, source_sha256=None, synced_at=None,
        )
        # program_link is path→org and both placements share the org: unchanged.
        carried_link = True

    base.log_event(
        conn, "placement", target.id, "merged_from", None, source.ref,
        note=f"{moved_subs} submissions, {moved_tasks} tasks, {moved_docs} documents",
    )
    base.soft_delete(conn, "placement", source.id, note=f"merged into {target.ref}")
    return MergeResult(
        placements.get(conn, target.id), moved_subs, moved_tasks, moved_docs, carried_link
    )
