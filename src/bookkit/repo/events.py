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


# event_log fields that are BOOKKEEPING, not mutations — none of them has a
# column behind it, so undo must never try to write one back:
#   created       a row's birth (undone by deleting, not reverting)
#   source        the MCP provenance stamp
#   import        importers' provenance (source file + sha256)
#   carrier_alias repo/aliases' UPSERT marker on the market org
#   merged_from   services/merge's record of what was folded in
# The last three were missing, which is why `u` raised IndexError straight
# after any import, any alias write and any merge. base._assert_known_field
# now refuses at WRITE time for anything not listed here or a real column, so
# the next one fails loudly instead of arriving as a mystery undo crash.
# One list, two consumers — last_mutation here and SKIP_FIELDS in
# services/batches.py.
NON_MUTATION_FIELDS = (
    "created",
    "source",
    "import",
    "carrier_alias",
    "merged_from",
)


def last_mutation(conn: sqlite3.Connection) -> EventLogEntry | None:
    """The most recent undoable event (field changes and deletes, not creates
    and not undo's own bookkeeping).

    'source' is provenance, not a mutation: the MCP server stamps it after
    every write it makes. It has no column behind it, so treating it as
    undoable made `u` raise IndexError immediately after ANY MCP write —
    it must be skipped the same way 'created' is. NON_MUTATION_FIELDS is the
    single home for that skip-list; services/batches.py derives from it."""
    marks = ",".join("?" * len(NON_MUTATION_FIELDS))
    row = conn.execute(
        f"SELECT * FROM event_log WHERE field NOT IN ({marks})"
        " AND (note IS NULL OR note NOT IN ('undo', 'undelete', 'revert'))"
        " ORDER BY rowid DESC LIMIT 1",
        NON_MUTATION_FIELDS,
    ).fetchone()
    return EventLogEntry.from_row(row) if row else None
