"""event_log reads: history for an entity, and the source for undo (u)."""

from __future__ import annotations

import sqlite3

from ..models import EventLogEntry
from . import base


def history(
    conn: sqlite3.Connection, entity_type: str, entity_id: str, limit: int = 100
) -> list[EventLogEntry]:
    rows = conn.execute(
        "SELECT * FROM event_log WHERE entity_type = ? AND entity_id = ?"
        " ORDER BY rowid DESC LIMIT ?",
        (entity_type, entity_id, limit),
    ).fetchall()
    return [EventLogEntry.from_row(r) for r in rows]


def field_history(
    conn: sqlite3.Connection, entity_type: str, entity_id: str, field: str
) -> list[EventLogEntry]:
    """'When did this renewal date move, and why.'"""
    rows = conn.execute(
        "SELECT * FROM event_log WHERE entity_type = ? AND entity_id = ? AND field = ?"
        " ORDER BY rowid DESC",
        (entity_type, entity_id, field),
    ).fetchall()
    return [EventLogEntry.from_row(r) for r in rows]


def stage_durations(conn: sqlite3.Connection, stage: str) -> list[sqlite3.Row]:
    """(entered_at, left_at) pairs for every completed stay in a pipeline stage."""
    return conn.execute(
        """
        SELECT entered.entity_id, entered.changed_at AS entered_at,
               MIN(left_.changed_at) AS left_at
        FROM event_log entered
        JOIN event_log left_
          ON left_.entity_type = 'opportunity' AND left_.field = 'stage'
         AND left_.entity_id = entered.entity_id
         AND left_.old_value = ? AND left_.changed_at >= entered.changed_at
         AND left_.rowid > entered.rowid
        WHERE entered.entity_type = 'opportunity' AND entered.field = 'stage'
          AND entered.new_value = ?
        GROUP BY entered.rowid
        """,
        (stage, stage),
    ).fetchall()


def stage_entry_count(conn: sqlite3.Connection, stage: str) -> int:
    """Distinct opportunities that have ever entered a stage (from the log)."""
    if stage == "identified":  # every opportunity starts here, unlogged
        row = conn.execute(
            f"SELECT COUNT(*) FROM opportunity WHERE {base.alive()}"
        ).fetchone()
        return int(row[0])
    row = conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM event_log"
        " WHERE entity_type = 'opportunity' AND field = 'stage' AND new_value = ?",
        (stage,),
    ).fetchone()
    return int(row[0])


def last_mutation(conn: sqlite3.Connection) -> EventLogEntry | None:
    """The most recent undoable event (field changes and deletes, not creates
    and not undo's own bookkeeping)."""
    row = conn.execute(
        "SELECT * FROM event_log WHERE field != 'created'"
        " AND (note IS NULL OR note NOT IN ('undo', 'undelete'))"
        " ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return EventLogEntry.from_row(row) if row else None
