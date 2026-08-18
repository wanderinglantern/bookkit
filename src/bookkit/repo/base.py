"""Shared CRUD plumbing: the one insert/update/soft-delete path.

`update` writes an event_log row for every changed field — this is mechanical
bookkeeping, not a business rule, so it lives here where no caller can forget
it. `alive()` is the one soft-delete filter; every read helper uses it.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import utc_now
from ..ids import new_ulid

# Tables whose rows soft-delete and whose mutations are event-logged.
ENTITY_TABLES = {
    "org": "org",
    "appetite": "appetite",
    "contact": "contact",
    "interaction": "interaction",
    "task": "task",
    "placement": "placement",
    "opportunity": "opportunity",
    "submission": "submission",
    "document": "document",
    "team_member": "team_member",
    "team_assignment": "team_assignment",
    "project": "project",
    "project_need": "project_need",
    "rfi_request": "rfi_request",
    "rfi_item": "rfi_item",
}


def alive(alias: str = "") -> str:
    """The soft-delete filter. Compose into WHERE clauses, never inline it."""
    prefix = f"{alias}." if alias else ""
    return f"{prefix}deleted_at IS NULL"


def raw_row(
    conn: sqlite3.Connection, entity_type: str, entity_id: str
) -> sqlite3.Row | None:
    """The row as it IS, dead or alive.

    Two sanctioned callers, both of which have to see past the alive() view:
    the batch-revert planner, which must compare against reality rather than
    the view, and `web/routes/account._owns_contact_row`, which answers "is
    this row this account's?" for a contact that may already be removed — so
    that services.contacts's "already removed" survives as the answer instead
    of being buried under "no contact". Everything else keeps using get()."""
    table = ENTITY_TABLES[entity_type]
    row: sqlite3.Row | None = conn.execute(
        f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
    ).fetchone()
    return row


_columns_cache: dict[str, frozenset[str]] = {}


def _columns(conn: sqlite3.Connection, entity_type: str) -> frozenset[str]:
    cached = _columns_cache.get(entity_type)
    if cached is None:
        table = ENTITY_TABLES[entity_type]
        cached = frozenset(
            str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")
        )
        _columns_cache[entity_type] = cached
    return cached


def _assert_known_field(
    conn: sqlite3.Connection, entity_type: str, field: str
) -> None:
    """An event's field must be a real column, or declared bookkeeping.

    Undo reads event_log back and writes `field` to that column, so an event
    naming something the table does not have is a landmine that only goes off
    when a user presses `u` — as IndexError, days later, on an unrelated
    record. That shipped three times ('source', then 'import', then
    'carrier_alias'/'merged_from'), each time fixed one name at a time. This
    turns the whole class into an immediate, loud failure at the write that
    causes it: declare the name in NON_MUTATION_FIELDS or use a real column."""
    from .events import NON_MUTATION_FIELDS

    if field in NON_MUTATION_FIELDS or field in _columns(conn, entity_type):
        return
    raise ValueError(
        f"event_log field {field!r} is neither a column of {entity_type!r} nor "
        f"declared in events.NON_MUTATION_FIELDS — undo would fail on it later"
    )


def log_event(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    field: str,
    old_value: Any,
    new_value: Any,
    note: str | None = None,
) -> None:
    from .. import db  # function-level: db imports nothing from repo, but keep the seam thin

    _assert_known_field(conn, entity_type, field)
    batch = db.current_batch()
    if batch is not None:
        batch.touch(entity_id)
    conn.execute(
        "INSERT INTO event_log (id, entity_type, entity_id, field, old_value, new_value,"
        " changed_at, note, batch_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_ulid(),
            entity_type,
            entity_id,
            field,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            utc_now(),
            note,
            None if batch is None else batch.batch_id,
        ),
    )


def insert(conn: sqlite3.Connection, entity_type: str, values: dict[str, Any]) -> str:
    """Insert a new row; allocates id/timestamps when absent; logs creation."""
    table = ENTITY_TABLES[entity_type]
    values = dict(values)
    values.setdefault("id", new_ulid())
    now = utc_now()
    if entity_type == "document":
        values.setdefault("added_at", now)
    else:
        values.setdefault("created_at", now)
        values.setdefault("updated_at", now)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
    log_event(conn, entity_type, values["id"], "created", None, values.get("ref"))
    return str(values["id"])


def get(conn: sqlite3.Connection, entity_type: str, entity_id: str) -> sqlite3.Row | None:
    table = ENTITY_TABLES[entity_type]
    row: sqlite3.Row | None = conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND {alive()}", (entity_id,)
    ).fetchone()
    return row


def update(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    changes: dict[str, Any],
    note: str | None = None,
) -> None:
    """Apply field changes, bump updated_at, event-log each real change."""
    table = ENTITY_TABLES[entity_type]
    old = get(conn, entity_type, entity_id)
    if old is None:
        raise KeyError(f"{entity_type} {entity_id} not found")
    real = {k: v for k, v in changes.items() if old[k] != v}
    if not real:
        return
    sets = ", ".join(f"{k} = ?" for k in real)
    params: list[Any] = list(real.values())
    if entity_type != "document":
        sets += ", updated_at = ?"
        params.append(utc_now())
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*params, entity_id))
    for field, new_value in real.items():
        log_event(conn, entity_type, entity_id, field, old[field], new_value, note)


def soft_delete(
    conn: sqlite3.Connection, entity_type: str, entity_id: str, note: str | None = None
) -> None:
    table = ENTITY_TABLES[entity_type]
    now = utc_now()
    conn.execute(f"UPDATE {table} SET deleted_at = ? WHERE id = ?", (now, entity_id))
    log_event(conn, entity_type, entity_id, "deleted_at", None, now, note)


def undelete(conn: sqlite3.Connection, entity_type: str, entity_id: str) -> None:
    table = ENTITY_TABLES[entity_type]
    row = conn.execute(f"SELECT deleted_at FROM {table} WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        raise KeyError(f"{entity_type} {entity_id} not found")
    conn.execute(f"UPDATE {table} SET deleted_at = NULL WHERE id = ?", (entity_id,))
    log_event(conn, entity_type, entity_id, "deleted_at", row["deleted_at"], None, "undelete")
