"""Documents — paths only, never blobs. The database is not a file server."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import Document
from . import base


def add(conn: sqlite3.Connection, org_id: str, title: str, path: str, **fields: Any) -> Document:
    doc_id = base.insert(
        conn, "document", {"org_id": org_id, "title": title, "path": path, **fields}
    )
    row = base.get(conn, "document", doc_id)
    return Document.from_row(row)  # type: ignore[arg-type]


def for_org(conn: sqlite3.Connection, org_id: str) -> list[Document]:
    rows = conn.execute(
        f"SELECT * FROM document WHERE org_id = ? AND {base.alive()} ORDER BY added_at DESC",
        (org_id,),
    ).fetchall()
    return [Document.from_row(r) for r in rows]


def reassign_org(conn: sqlite3.Connection, from_org_id: str, to_org_id: str) -> int:
    """Move every row to the survivor on a merge."""
    # Row by row through base.update, not one bulk UPDATE: the move is a
    # field change like any other and must land in the event log, or the
    # merge cannot be reverted — the record would come back while the rows
    # that moved stayed moved. Same rule as rfi.reassign_market.
    rows = conn.execute(
        f"""SELECT id FROM document
            WHERE org_id = ? AND {base.alive()}""",
        (from_org_id,),
    ).fetchall()
    for row in rows:
        base.update(conn, "document", row[0], {"org_id": to_org_id}, "market merged")
    return len(rows)


def reassign_placement(conn: sqlite3.Connection, from_id: str, to_id: str) -> int:
    """Move every row to the surviving placement on a merge."""
    # Row by row through base.update, not one bulk UPDATE: the move is a
    # field change like any other and must land in the event log, or the
    # merge cannot be reverted — the record would come back while the rows
    # that moved stayed moved. Same rule as rfi.reassign_market.
    rows = conn.execute(
        f"""SELECT id FROM document
            WHERE placement_id = ? AND {base.alive()}""",
        (from_id,),
    ).fetchall()
    for row in rows:
        base.update(conn, "document", row[0], {"placement_id": to_id}, "placement merged")
    return len(rows)


def delete(conn: sqlite3.Connection, doc_id: str) -> None:
    base.soft_delete(conn, "document", doc_id)
