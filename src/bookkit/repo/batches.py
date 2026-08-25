"""Undo batches — the event_log grouping that makes one writer action one
undoable unit. SQL only; the revert rules live in services/batches.py."""

from __future__ import annotations

import sqlite3

from .. import db
from ..ids import BATCH_REF, new_ulid, next_ref
from ..models import EventBatch, EventLogEntry


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
    transaction must be stamped with it before this row is queried back.

    Reads `db.utc_now()` by module attribute rather than importing the name,
    so a test that monkeypatches `db.utc_now` controls this stamp too — the
    same clock the join-window check in services/batches.py reads."""
    conn.execute(
        "INSERT INTO event_batch (id, ref, source, tool, summary, org_id,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (batch_id, next_ref(conn, BATCH_REF), source, tool, summary, org_id,
         db.utc_now()),
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
    conn: sqlite3.Connection, since: str, limit: int = 20,
    org_id: str | None = None,
) -> list[EventBatch]:
    """Newest first, EVERY source — tui, web and mcp alike. `since` is an ISO
    timestamp the caller computes — no wall clock in here.

    `org_id` narrows to one account. It matches the batch's own org_id, which
    a few batches legitimately do not have: client_create opens its batch
    before the org exists, so an account's own creation is not in its own
    filtered history."""
    where = ["created_at >= ?"]
    params: list[str | int] = [since]
    if org_id is not None:
        where.append("org_id = ?")
        params.append(org_id)
    rows = conn.execute(
        f"SELECT * FROM event_batch WHERE {' AND '.join(where)}"
        " ORDER BY created_at DESC, ref DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [EventBatch.from_row(r) for r in rows]


def events_for(conn: sqlite3.Connection, batch_id: str) -> list[EventLogEntry]:
    """Oldest first (rowid order) — the collapse in services/batches.py takes
    the first old_value and the last new_value, so order is load-bearing."""
    rows = conn.execute(
        "SELECT * FROM event_log WHERE batch_id = ? ORDER BY rowid", (batch_id,)
    ).fetchall()
    return [EventLogEntry.from_row(r) for r in rows]


def external_change_count(
    conn: sqlite3.Connection, entity_type: str, entity_id: str, batch_id: str
) -> int:
    """Mutation events on this entity that did NOT come from this batch —
    how the revert planner knows a batch-created row was edited since.
    Revert/undo bookkeeping doesn't count; a user's undo of their own edit
    still leaves the edit event, which is the conservative reading."""
    row = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE entity_type = ? AND entity_id = ?"
        " AND (batch_id IS NULL OR batch_id != ?)"
        " AND field NOT IN ('created', 'source')"
        " AND (note IS NULL OR note NOT IN ('undo', 'undelete', 'revert'))",
        (entity_type, entity_id, batch_id),
    ).fetchone()
    return int(row[0])


def most_recent(conn: sqlite3.Connection) -> EventBatch | None:
    """The single most recently created batch, across every source — the join
    candidate for open_batch(entity_id=...). Ordered by rowid, matching the
    event_log ordering rule (created_at has only second precision)."""
    row = conn.execute(
        "SELECT * FROM event_batch ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return EventBatch.from_row(row) if row else None


def last_undoable(conn: sqlite3.Connection, source: str) -> EventBatch | None:
    """The newest batch this surface wrote that has not been reverted yet.

    Scoped by `source` on purpose: `u` is the TUI's undo and must never reach
    an assistant batch (that is what `R` on the changes table is for) or a
    machine write. Ordered by rowid, matching the event_log ordering rule —
    created_at has second precision, so two batches inside one second would
    otherwise come back in an arbitrary order."""
    row = conn.execute(
        "SELECT * FROM event_batch WHERE source = ? AND reverted_at IS NULL"
        " ORDER BY rowid DESC LIMIT 1",
        (source,),
    ).fetchone()
    return EventBatch.from_row(row) if row else None


def mark_reverted(conn: sqlite3.Connection, batch_id: str, at: str) -> None:
    conn.execute(
        "UPDATE event_batch SET reverted_at = ? WHERE id = ?", (at, batch_id)
    )


def new_batch_id() -> str:
    return new_ulid()
