"""Submissions — a request to a specific market for a placement or opportunity."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import Submission
from . import base


def create(
    conn: sqlite3.Connection,
    market_org_id: str,
    sent_on: str,
    placement_id: str | None = None,
    opportunity_id: str | None = None,
    **fields: Any,
) -> Submission:
    if (placement_id is None) == (opportunity_id is None):
        raise ValueError("exactly one of placement_id / opportunity_id must be set")
    sub_id = base.insert(
        conn,
        "submission",
        {
            "market_org_id": market_org_id,
            "sent_on": sent_on,
            "placement_id": placement_id,
            "opportunity_id": opportunity_id,
            **fields,
        },
    )
    return get(conn, sub_id)


def get(conn: sqlite3.Connection, sub_id: str) -> Submission:
    row = base.get(conn, "submission", sub_id)
    if row is None:
        raise KeyError(f"submission {sub_id} not found")
    return Submission.from_row(row)


def for_placement(conn: sqlite3.Connection, placement_id: str) -> list[Submission]:
    rows = conn.execute(
        f"SELECT * FROM submission WHERE placement_id = ? AND {base.alive()} ORDER BY sent_on",
        (placement_id,),
    ).fetchall()
    return [Submission.from_row(r) for r in rows]


def for_opportunity(conn: sqlite3.Connection, opportunity_id: str) -> list[Submission]:
    rows = conn.execute(
        f"SELECT * FROM submission WHERE opportunity_id = ? AND {base.alive()} ORDER BY sent_on",
        (opportunity_id,),
    ).fetchall()
    return [Submission.from_row(r) for r in rows]


def for_market(
    conn: sqlite3.Connection, market_org_id: str, status: str | None = None
) -> list[Submission]:
    where = ["market_org_id = ?", base.alive()]
    params: list[Any] = [market_org_id]
    if status is not None:
        where.append("status = ?")
        params.append(status)
    rows = conn.execute(
        f"SELECT * FROM submission WHERE {' AND '.join(where)} ORDER BY sent_on DESC", params
    ).fetchall()
    return [Submission.from_row(r) for r in rows]


def outstanding(conn: sqlite3.Connection, sent_on_or_before: str | None = None) -> list[Submission]:
    """Everything still out at the market."""
    where = [base.alive(), "status = 'out'"]
    params: list[Any] = []
    if sent_on_or_before is not None:
        where.append("sent_on <= ?")
        params.append(sent_on_or_before)
    rows = conn.execute(
        f"SELECT * FROM submission WHERE {' AND '.join(where)} ORDER BY sent_on", params
    ).fetchall()
    return [Submission.from_row(r) for r in rows]


def reassign_market(conn: sqlite3.Connection, from_org_id: str, to_org_id: str) -> int:
    """Bulk move for market merges; the service logs the event."""
    cur = conn.execute(
        "UPDATE submission SET market_org_id = ? WHERE market_org_id = ?",
        (to_org_id, from_org_id),
    )
    return cur.rowcount


def reassign_placement(conn: sqlite3.Connection, from_id: str, to_id: str) -> int:
    """Bulk move for placement merges; the service logs the event."""
    cur = conn.execute(
        "UPDATE submission SET placement_id = ? WHERE placement_id = ?", (to_id, from_id)
    )
    return cur.rowcount


def outstanding_for_org(conn: sqlite3.Connection, org_id: str) -> list[sqlite3.Row]:
    """Everything still out at market for ONE client, joined for display:
    market name plus what it's about (program name or opportunity title)."""
    return conn.execute(
        f"""
        SELECT s.*, m.name AS market_name,
               COALESCE(p.program_name, o.title) AS about,
               COALESCE(p.id, '') AS about_placement_id
        FROM submission s
        JOIN org m ON m.id = s.market_org_id
        LEFT JOIN placement p ON p.id = s.placement_id
        LEFT JOIN opportunity o ON o.id = s.opportunity_id
        WHERE s.status = 'out' AND {base.alive('s')}
          AND (p.org_id = ? OR o.org_id = ?)
        ORDER BY s.sent_on
        """,
        (org_id, org_id),
    ).fetchall()


def market_counts(
    conn: sqlite3.Connection, since: str | None = None, until: str | None = None
) -> list[sqlite3.Row]:
    """Per-market submission outcome counts over a sent_on window."""
    where = [base.alive("s")]
    params: list[Any] = []
    if since is not None:
        where.append("s.sent_on >= ?")
        params.append(since)
    if until is not None:
        where.append("s.sent_on <= ?")
        params.append(until)
    return conn.execute(
        f"""
        SELECT s.market_org_id, o.name AS market_name,
               COUNT(*) AS sent,
               SUM(CASE WHEN s.status IN ('quoted', 'bound') THEN 1 ELSE 0 END) AS quoted,
               SUM(CASE WHEN s.status = 'bound' THEN 1 ELSE 0 END) AS bound,
               SUM(CASE WHEN s.status = 'declined' THEN 1 ELSE 0 END) AS declined
        FROM submission s JOIN org o ON o.id = s.market_org_id
        WHERE {' AND '.join(where)}
        GROUP BY s.market_org_id ORDER BY o.name
        """,
        params,
    ).fetchall()


def update(
    conn: sqlite3.Connection, sub_id: str, note: str | None = None, **changes: Any
) -> Submission:
    base.update(conn, "submission", sub_id, changes, note)
    return get(conn, sub_id)


def delete(conn: sqlite3.Connection, sub_id: str) -> None:
    base.soft_delete(conn, "submission", sub_id)
