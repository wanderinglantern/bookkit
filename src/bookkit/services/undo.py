"""Undo the last mutation (u), sourced from event_log."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..repo import base, events


@dataclass(frozen=True)
class UndoResult:
    entity_type: str
    entity_id: str
    field: str
    restored_value: str | None
    description: str


def undo_last(conn: sqlite3.Connection) -> UndoResult | None:
    """Revert the most recent field change or soft delete. Returns None when
    there is nothing to undo."""
    event = events.last_mutation(conn)
    if event is None:
        return None
    if event.field == "deleted_at":
        base.undelete(conn, event.entity_type, event.entity_id)
        return UndoResult(
            event.entity_type, event.entity_id, "deleted_at", None,
            f"restored deleted {event.entity_type}",
        )
    base.update(
        conn, event.entity_type, event.entity_id,
        {event.field: event.old_value}, note="undo",
    )
    return UndoResult(
        event.entity_type, event.entity_id, event.field, event.old_value,
        f"{event.entity_type}.{event.field}: {event.new_value!r} → {event.old_value!r}",
    )
