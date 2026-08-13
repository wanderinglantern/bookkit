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


def find_request(conn: sqlite3.Connection, ref_or_id: str) -> RfiRequest | None:
    """Resolve a request by its ref (case-insensitively) or its id.

    Refs are what a human — or the MCP assistant reading a chase list — types
    back, and they arrive as "rfi-0001" as often as "RFI-0001"; ids are exact
    by construction. NOCASE is ASCII-only, which is all a ref ever is."""
    row = conn.execute(
        f"""SELECT * FROM rfi_request
            WHERE (id = ? OR ref = ? COLLATE NOCASE) AND {base.alive()}""",
        (ref_or_id, ref_or_id),
    ).fetchone()
    return RfiRequest.from_row(row) if row else None


def known_refs(conn: sqlite3.Connection, limit: int = 10) -> list[str]:
    """"REF Account" labels to name in a "no such request" error, newest
    first. Callers that refuse to guess at a ref still owe the caller real
    ones to retry with."""
    rows = conn.execute(
        f"""SELECT r.ref || ' ' || o.name AS label
            FROM rfi_request r JOIN org o ON o.id = r.org_id
            WHERE {base.alive('r')} AND {base.alive('o')}
            ORDER BY r.requested_on DESC, r.ref
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [str(r["label"]) for r in rows]


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


def reassign_market(conn: sqlite3.Connection, from_org_id: str, to_org_id: str) -> int:
    """Point every request at the surviving market when two markets merge.
    Returns the number of requests moved.

    Row by row through base.update, not one bulk UPDATE: the move is a field
    change like any other and must land in the event log."""
    rows = conn.execute(
        f"""SELECT id FROM rfi_request
            WHERE market_org_id = ? AND {base.alive()}""",
        (from_org_id,),
    ).fetchall()
    for row in rows:
        base.update(
            conn, "rfi_request", row[0], {"market_org_id": to_org_id}, "market merged"
        )
    return len(rows)


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
    the same order the client's sheet renders, so screen and export agree.

    rowid, not id, breaks the tie: created_at is second-precision and a ULID's
    low 80 bits are random, so items added in the same second would otherwise
    come back in arbitrary order — which pasting a list of questions does every
    time. rowid is insertion order, and these rows are only ever soft-deleted,
    so it is never reused."""
    rows = conn.execute(
        f"""SELECT * FROM rfi_item WHERE request_id = ? AND {base.alive()}
            ORDER BY category IS NULL, category, created_at, rowid""",
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


# --- chase feed ------------------------------------------------------------


def outstanding_rows(conn: sqlite3.Connection, horizon: str) -> list[sqlite3.Row]:
    """One row per live, uncancelled request that still has outstanding items
    whose EFFECTIVE due (item's, else the request's) falls on or before the
    horizon — or is already past, so nothing overdue ever falls off.

    NULL effective dues are excluded: an undated request is not yet a chase."""
    return conn.execute(
        f"""
        SELECT r.*, o.name AS org_name, m.name AS market_name,
               COUNT(*)                       AS open_count,
               MIN(COALESCE(i.due_on, r.due_on)) AS earliest_due,
               (SELECT COUNT(*) FROM rfi_item t
                 WHERE t.request_id = r.id AND {base.alive('t')}) AS total_count
        FROM rfi_item i
        JOIN rfi_request r ON r.id = i.request_id
        JOIN org o ON o.id = r.org_id
        -- aliveness rides on the JOIN, not the WHERE: a merged-away market
        -- must blank the name, never drop the whole request from the queue
        LEFT JOIN org m ON m.id = r.market_org_id AND m.deleted_at IS NULL
        WHERE i.status = 'outstanding'
          AND r.cancelled_at IS NULL
          AND {base.alive('i')} AND {base.alive('r')} AND {base.alive('o')}
        GROUP BY r.id
        HAVING earliest_due IS NOT NULL AND earliest_due <= ?
        ORDER BY earliest_due, r.ref
        """,
        (horizon,),
    ).fetchall()


def outstanding_request_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every live, uncancelled request across the book that still has at least
    one outstanding item, with its account and market names — NO date window.

    The deliberate twin of outstanding_rows above, and the difference is the
    point: that one is the 120-day CHASE queue (undated requests are not yet a
    chase); this one backs open_items, which answers "everything a client still
    owes" — an undated question is still owed, and so is one due in a year.
    Ordered soonest-deadline first with undated requests last."""
    return conn.execute(
        f"""
        SELECT r.*, o.name AS org_name, m.name AS market_name
        FROM rfi_request r
        JOIN org o ON o.id = r.org_id
        -- aliveness rides on the JOIN, as in outstanding_rows: a merged-away
        -- market blanks the name, it never drops the request
        LEFT JOIN org m ON m.id = r.market_org_id AND m.deleted_at IS NULL
        WHERE r.cancelled_at IS NULL
          AND {base.alive('r')} AND {base.alive('o')}
          AND EXISTS (
              SELECT 1 FROM rfi_item i
               WHERE i.request_id = r.id AND i.status = 'outstanding'
                 AND {base.alive('i')})
        ORDER BY r.due_on IS NULL, r.due_on, r.ref
        """
    ).fetchall()


def open_item_count(conn: sqlite3.Connection, request_id: str) -> int:
    """How many items are still outstanding. Zero means the request is done —
    services/rfi.is_open turns that into the derived open/closed rule."""
    return int(
        conn.execute(
            f"""SELECT COUNT(*) FROM rfi_item
                WHERE request_id = ? AND status = 'outstanding' AND {base.alive()}""",
            (request_id,),
        ).fetchone()[0]
    )


def item_count(conn: sqlite3.Connection, request_id: str) -> int:
    return int(
        conn.execute(
            f"""SELECT COUNT(*) FROM rfi_item
                WHERE request_id = ? AND {base.alive()}""",
            (request_id,),
        ).fetchone()[0]
    )
