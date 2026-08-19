"""Contacts — the people at client and market organisations."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import transaction
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


def at_market_orgs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every active contact who works at a MARKET, with their market's name.

    This is the underwriter picker's source. It is a repo query rather than a
    loop over `for_org` per market because the picker needs one ordered list
    across every market in the book, and because the label has to carry the
    market — two underwriters called Chen at two carriers is the same
    ambiguity that made search unusable (AE review, 2026-08-18).

    Market contacts ARE records in this book: the correction recorded in the
    overnight plan. Nothing had ever read them for this purpose because
    submission.underwriter_contact_id had no way in."""
    return conn.execute(
        f"""
        SELECT c.*, o.name AS org_name
        FROM contact c JOIN org o ON o.id = c.org_id
        WHERE o.kind = 'market' AND c.active = 1
          AND {base.alive('c')} AND {base.alive('o')}
        ORDER BY o.name, c.last_name, c.first_name
        """
    ).fetchall()


def update(
    conn: sqlite3.Connection, contact_id: str, note: str | None = None, **changes: Any
) -> Contact:
    base.update(conn, "contact", contact_id, changes, note)
    return get(conn, contact_id)


def reassign_org(conn: sqlite3.Connection, from_org_id: str, to_org_id: str) -> int:
    """Move every row to the survivor on a merge."""
    # Row by row through base.update, not one bulk UPDATE: the move is a
    # field change like any other and must land in the event log, or the
    # merge cannot be reverted — the record would come back while the rows
    # that moved stayed moved. Same rule as rfi.reassign_market.
    rows = conn.execute(
        f"""SELECT id FROM contact
            WHERE org_id = ? AND {base.alive()}""",
        (from_org_id,),
    ).fetchall()
    for row in rows:
        base.update(conn, "contact", row[0], {"org_id": to_org_id}, "market merged")
    return len(rows)


def set_primary(conn: sqlite3.Connection, contact_id: str) -> None:
    """Exactly one primary per org — ALL OR NOTHING.

    This clears every other primary and then sets one, and the connection is
    AUTOCOMMIT, so without a transaction each of those writes landed on its
    own: a failure between the clear and the set left the org with ZERO
    primary contacts, which is not a state the invariant this function exists
    to hold allows, and no surface shows it as wrong — the primary simply
    vanishes from every header and every export (2026-08-18).

    db.transaction NESTS BY JOINING, so a caller that has already opened a
    batch keeps owning the undo unit; this only guarantees the writes cannot
    be torn apart."""
    contact = get(conn, contact_id)
    with transaction(conn):
        for other in for_org(conn, contact.org_id, active_only=False):
            if other.is_primary and other.id != contact_id:
                base.update(conn, "contact", other.id, {"is_primary": 0})
        base.update(conn, "contact", contact_id, {"is_primary": 1})


def delete(conn: sqlite3.Connection, contact_id: str) -> None:
    base.soft_delete(conn, "contact", contact_id)
