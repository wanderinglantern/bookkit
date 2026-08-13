"""Information requests (RFIs) — batches of questions and document requests
a client owes. A request's open/closed state is DERIVED from its items
(services/rfi.py owns that rule); nothing here stores it."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ids import RFI_REF, next_ref
from ..models import RfiItem, RfiRequest
from . import base


def create_request(
    conn: sqlite3.Connection, org_id: str, title: str, requested_on: str, **fields: Any
) -> RfiRequest:
    fields.setdefault("ref", next_ref(conn, RFI_REF))
    request_id = base.insert(
        conn,
        "rfi_request",
        {"org_id": org_id, "title": title, "requested_on": requested_on, **fields},
    )
    return get_request(conn, request_id)


def get_request(conn: sqlite3.Connection, request_id: str) -> RfiRequest:
    row = base.get(conn, "rfi_request", request_id)
    if row is None:
        raise KeyError(f"rfi request {request_id} not found")
    return RfiRequest.from_row(row)


def requests_for_org(conn: sqlite3.Connection, org_id: str) -> list[RfiRequest]:
    rows = conn.execute(
        f"""SELECT * FROM rfi_request WHERE org_id = ? AND {base.alive()}
            ORDER BY cancelled_at IS NOT NULL, due_on IS NULL, due_on,
                     requested_on DESC""",
        (org_id,),
    ).fetchall()
    return [RfiRequest.from_row(r) for r in rows]


def update_request(
    conn: sqlite3.Connection, request_id: str, note: str | None = None, **changes: Any
) -> RfiRequest:
    base.update(conn, "rfi_request", request_id, changes, note)
    return get_request(conn, request_id)


def delete_request(conn: sqlite3.Connection, request_id: str) -> None:
    base.soft_delete(conn, "rfi_request", request_id)


# --- items ---------------------------------------------------------------------


def add_item(
    conn: sqlite3.Connection, request_id: str, prompt: str, **fields: Any
) -> RfiItem:
    item_id = base.insert(
        conn, "rfi_item", {"request_id": request_id, "prompt": prompt, **fields}
    )
    return get_item(conn, item_id)


def get_item(conn: sqlite3.Connection, item_id: str) -> RfiItem:
    row = base.get(conn, "rfi_item", item_id)
    if row is None:
        raise KeyError(f"rfi item {item_id} not found")
    return RfiItem.from_row(row)


def items_for_request(conn: sqlite3.Connection, request_id: str) -> list[RfiItem]:
    """Category groups first (uncategorised last), creation order within —
    the same order the client's sheet renders, so screen and export agree."""
    rows = conn.execute(
        f"""SELECT * FROM rfi_item WHERE request_id = ? AND {base.alive()}
            ORDER BY category IS NULL, category, created_at, id""",
        (request_id,),
    ).fetchall()
    return [RfiItem.from_row(r) for r in rows]


def update_item(
    conn: sqlite3.Connection, item_id: str, note: str | None = None, **changes: Any
) -> RfiItem:
    base.update(conn, "rfi_item", item_id, changes, note)
    return get_item(conn, item_id)


def delete_item(conn: sqlite3.Connection, item_id: str) -> None:
    base.soft_delete(conn, "rfi_item", item_id)
