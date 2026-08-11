"""Contacts — the people at client and market organisations."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import Contact
from . import base


def create(conn: sqlite3.Connection, org_id: str, **fields: Any) -> Contact:
    contact_id = base.insert(conn, "contact", {"org_id": org_id, **fields})
    return get(conn, contact_id)


def get(conn: sqlite3.Connection, contact_id: str) -> Contact:
    row = base.get(conn, "contact", contact_id)
    if row is None:
        raise KeyError(f"contact {contact_id} not found")
    return Contact.from_row(row)


def for_org(conn: sqlite3.Connection, org_id: str, active_only: bool = True) -> list[Contact]:
    where = f"org_id = ? AND {base.alive()}"
    if active_only:
        where += " AND active = 1"
    rows = conn.execute(
        f"SELECT * FROM contact WHERE {where} ORDER BY is_primary DESC, last_name, first_name",
        (org_id,),
    ).fetchall()
    return [Contact.from_row(r) for r in rows]


def update(
    conn: sqlite3.Connection, contact_id: str, note: str | None = None, **changes: Any
) -> Contact:
    base.update(conn, "contact", contact_id, changes, note)
    return get(conn, contact_id)


def set_primary(conn: sqlite3.Connection, contact_id: str) -> None:
    """Exactly one primary per org."""
    contact = get(conn, contact_id)
    for other in for_org(conn, contact.org_id, active_only=False):
        if other.is_primary and other.id != contact_id:
            base.update(conn, "contact", other.id, {"is_primary": 0})
    base.update(conn, "contact", contact_id, {"is_primary": 1})


def delete(conn: sqlite3.Connection, contact_id: str) -> None:
    base.soft_delete(conn, "contact", contact_id)
