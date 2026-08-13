"""Undo batches — the event_log grouping that makes one writer action one
undoable unit. SQL only; the revert rules live in services/batches.py."""

from __future__ import annotations

import sqlite3

from ..db import utc_now
from ..ids import new_ulid, next_ref
from ..models import EventBatch, EventLogEntry

BATCH_REF = "MCP"


def create(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    source: str,
    tool: str,
    summary: str,
    org_id: str | None,
) -> EventBatch:
    """The caller supplies batch_id because events written inside the same
    transaction must be stamped with it before this row is queried back."""
    conn.execute(
        "INSERT INTO event_batch (id, ref, source, tool, summary, org_id,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (batch_id, next_ref(conn, BATCH_REF), source, tool, summary, org_id,
         utc_now()),
    )
    return get(conn, batch_id)


def get(conn: sqlite3.Connection, batch_id: str) -> EventBatch:
    row = conn.execute(
        "SELECT * FROM event_batch WHERE id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"batch {batch_id} not found")
    return EventBatch.from_row(row)


def get_by_ref(conn: sqlite3.Connection, ref: str) -> EventBatch:
    row = conn.execute(
        "SELECT * FROM event_batch WHERE ref = ?", (ref,)
    ).fetchone()
    if row is None:
        raise KeyError(f"batch {ref} not found")
    return EventBatch.from_row(row)


def recent(
    conn: sqlite3.Connection, since: str, limit: int = 20
) -> list[EventBatch]:
    """Newest first. `since` is an ISO timestamp the caller computes — no wall
    clock in here."""
    rows = conn.execute(
        "SELECT * FROM event_batch WHERE created_at >= ?"
        " ORDER BY created_at DESC, ref DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    return [EventBatch.from_row(r) for r in rows]


def events_for(conn: sqlite3.Connection, batch_id: str) -> list[EventLogEntry]:
    """Oldest first (rowid order) — the collapse in services/batches.py takes
    the first old_value and the last new_value, so order is load-bearing."""
    rows = conn.execute(
        "SELECT * FROM event_log WHERE batch_id = ? ORDER BY rowid", (batch_id,)
    ).fetchall()
    return [EventLogEntry.from_row(r) for r in rows]


def mark_reverted(conn: sqlite3.Connection, batch_id: str, at: str) -> None:
    conn.execute(
        "UPDATE event_batch SET reverted_at = ? WHERE id = ?", (at, batch_id)
    )


def new_batch_id() -> str:
    return new_ulid()
