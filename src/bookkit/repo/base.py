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
    "submission_subjectivity": "submission_subjectivity",
    "document": "document",
    "team_member": "team_member",
    "team_assignment": "team_assignment",
    "project": "project",
    "project_need": "project_need",
    "rfi_request": "rfi_request",
    "rfi_item": "rfi_item",
    "line_of_coverage": "line_of_coverage",
    "market_response": "market_response",
    "placement_line": "placement_line",
}


def alive(alias: str = "") -> str:
    """The soft-delete filter. Compose into WHERE clauses, never inline it."""
    prefix = f"{alias}." if alias else ""
    return f"{prefix}deleted_at IS NULL"


def raw_row(
    conn: sqlite3.Connection, entity_type: str, entity_id: str
) -> sqlite3.Row | None:
    """The row as it IS, dead or alive.

    Sanctioned callers, all of which have to see past the alive() view: the
    batch-revert planner, which must compare against reality rather than the
    view; `web/routes/account._owns_raw_row`, which answers "is this row this
    account's?" for a contact or an interaction that may already be gone — so
    that services.contacts's "already removed" and services.interactions's
    "already deleted" survive as the answer instead of being buried under "no
    contact"/"no interaction"; and those two services themselves, which raise
    those sentences; and `repo/lines.get_any`, because a line of coverage that
    has been retired must still be able to NAME the marketing recorded against
    it. Everything else keeps using get()."""
    table = ENTITY_TABLES[entity_type]
    row: sqlite3.Row | None = conn.execute(
        f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
    ).fetchone()
    return row


# --- what still hangs off a row -------------------------------------------
#
# A BATCH THAT CREATED A PARENT CANNOT TAKE IT AWAY WHILE SOMEBODY ELSE'S WORK
# IS STILL HANGING OFF IT (2026-08-26).
#
# `services/batches.plan_revert` reads the event_log, and the event_log records
# a child against the CHILD. So a row created in a LATER batch is invisible to
# the earlier batch's plan: the planner saw no conflict at all and the revert
# soft-deleted the parent out from under live children, which then answered to
# nothing and could not be recovered — the parent is gone, and re-doing the act
# that made it mints a NEW one.
#
# The shape that found it is marketing (services/marketing_entry.py): the FIRST
# approach to a market opens the submission inside its own batch and the second
# and third JOIN that submission, creating only their own response rows.
# Reverting the first approach reported zero conflicts, deleted the shared
# submission, and left two live responses orphaned — after which the client
# workbook said those lines had never been marketed. But nothing about it is
# marketing's: it is every parent a batch creates and a later batch adopts, so
# the check is derived FROM THE SCHEMA rather than taught one table at a time.
# A new table with a foreign key is covered on the day its migration lands, and
# tests/test_revert_dependents.py is the gate that says so.
#
# WHERE THIS CAN LOOK. Only ENTITY_TABLES — the rows that soft-delete and are
# event-logged, which is what makes "created by this batch" an answerable
# question and "still live" a meaningful one. The join tables
# (opportunity_line, team_assignment_line) and carrier_alias have no id, no
# deleted_at and no event of their own, so no revert could attribute one to a
# batch; the gate names them and says why rather than leaving the hole silent.


def child_links(conn: sqlite3.Connection) -> dict[str, list[tuple[str, str]]]:
    """parent entity_type -> [(child entity_type, the column pointing at it)].

    Read off the LIVE schema (`PRAGMA foreign_key_list`), never a hand-kept
    list: the whole point is that the next migration is covered without anyone
    remembering to come here."""
    links: dict[str, list[tuple[str, str]]] = {}
    by_table = {table: entity for entity, table in ENTITY_TABLES.items()}
    for child_entity, child_table in ENTITY_TABLES.items():
        for row in conn.execute(f"PRAGMA foreign_key_list({child_table})"):
            parent_entity = by_table.get(str(row[2]))
            if parent_entity is None:
                continue        # points outside the event-logged world
            links.setdefault(parent_entity, []).append((child_entity, str(row[3])))
    return links


def live_dependents(
    conn: sqlite3.Connection,
    parent_entity: str,
    parent_id: str,
    links: dict[str, list[tuple[str, str]]] | None = None,
) -> list[tuple[str, str, str]]:
    """Every LIVE row pointing at this one, as (entity_type, id, column).

    The COLUMN is part of the answer because the caller has to ask whether the
    link itself is about to be undone — a batch that created an opportunity and
    pointed an existing need at it releases that need on revert, and the need
    must not block the very batch that is letting go of it.

    alive() and not raw_row: a child already soft-deleted stops nothing from
    being orphaned, so it must never block a revert.

    `links` is the map from `child_links`, passed in when a caller is walking
    many parents so the schema is read once."""
    resolved = child_links(conn) if links is None else links
    found: list[tuple[str, str, str]] = []
    for child_entity, column in resolved.get(parent_entity, []):
        table = ENTITY_TABLES[child_entity]
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE {column} = ? AND {alive()}",
            (parent_id,),
        ).fetchall()
        found.extend((child_entity, str(row[0]), column) for row in rows)
    return found


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
