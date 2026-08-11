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
    """Bulk move for org merges; the service logs the event."""
    cur = conn.execute(
        "UPDATE document SET org_id = ? WHERE org_id = ?", (to_org_id, from_org_id)
    )
    return cur.rowcount


def reassign_placement(conn: sqlite3.Connection, from_id: str, to_id: str) -> int:
    """Bulk move for placement merges; the service logs the event."""
    cur = conn.execute(
        "UPDATE document SET placement_id = ? WHERE placement_id = ?", (to_id, from_id)
    )
    return cur.rowcount


def delete(conn: sqlite3.Connection, doc_id: str) -> None:
    base.soft_delete(conn, "document", doc_id)
