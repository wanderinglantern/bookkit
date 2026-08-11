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


def delete(conn: sqlite3.Connection, doc_id: str) -> None:
    base.soft_delete(conn, "document", doc_id)
