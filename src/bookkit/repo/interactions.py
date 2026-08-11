"""Interactions — the relationship log, plus who was in the room."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import Contact, Interaction
from . import base


def log(
    conn: sqlite3.Connection,
    org_id: str,
    type: str,
    subject: str,
    occurred_on: str,
    body: str | None = None,
    contact_ids: list[str] | None = None,
    **fields: Any,
) -> Interaction:
    interaction_id = base.insert(
        conn,
        "interaction",
        {
            "org_id": org_id,
            "type": type,
            "subject": subject,
            "occurred_on": occurred_on,
            "body": body,
            **fields,
        },
    )
    for contact_id in contact_ids or []:
        conn.execute(
            "INSERT OR IGNORE INTO interaction_contact (interaction_id, contact_id) VALUES (?, ?)",
            (interaction_id, contact_id),
        )
    return get(conn, interaction_id)


def get(conn: sqlite3.Connection, interaction_id: str) -> Interaction:
    row = base.get(conn, "interaction", interaction_id)
    if row is None:
        raise KeyError(f"interaction {interaction_id} not found")
    return Interaction.from_row(row)


def for_org(conn: sqlite3.Connection, org_id: str, limit: int = 200) -> list[Interaction]:
    rows = conn.execute(
        f"SELECT * FROM interaction WHERE org_id = ? AND {base.alive()}"
        " ORDER BY occurred_on DESC, created_at DESC LIMIT ?",
        (org_id, limit),
    ).fetchall()
    return [Interaction.from_row(r) for r in rows]


def reassign_org(conn: sqlite3.Connection, from_org_id: str, to_org_id: str) -> int:
    """Bulk move for org merges; the service logs the event."""
    cur = conn.execute(
        "UPDATE interaction SET org_id = ? WHERE org_id = ?", (to_org_id, from_org_id)
    )
    return cur.rowcount


def attendees(conn: sqlite3.Connection, interaction_id: str) -> list[Contact]:
    rows = conn.execute(
        f"""
        SELECT c.* FROM contact c
        JOIN interaction_contact ic ON ic.contact_id = c.id
        WHERE ic.interaction_id = ? AND {base.alive('c')}
        ORDER BY c.last_name
        """,
        (interaction_id,),
    ).fetchall()
    return [Contact.from_row(r) for r in rows]


def last_for_org(conn: sqlite3.Connection, org_id: str) -> Interaction | None:
    rows = for_org(conn, org_id, limit=1)
    return rows[0] if rows else None


def update(
    conn: sqlite3.Connection, interaction_id: str, note: str | None = None, **changes: Any
) -> Interaction:
    base.update(conn, "interaction", interaction_id, changes, note)
    return get(conn, interaction_id)


def delete(conn: sqlite3.Connection, interaction_id: str) -> None:
    base.soft_delete(conn, "interaction", interaction_id)
