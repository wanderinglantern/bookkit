"""Opportunities — new business and non-renewal work.

Stage transitions do NOT happen here: services/pipeline.py owns them so the
event_log always records the move. Plain field edits are fine here.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ids import OPPORTUNITY_REF, next_ref
from ..models import Opportunity
from . import base


def create(conn: sqlite3.Connection, org_id: str, title: str, **fields: Any) -> Opportunity:
    fields.setdefault("ref", next_ref(conn, OPPORTUNITY_REF))
    opp_id = base.insert(conn, "opportunity", {"org_id": org_id, "title": title, **fields})
    return get(conn, opp_id)


def get(conn: sqlite3.Connection, opp_id: str) -> Opportunity:
    row = base.get(conn, "opportunity", opp_id)
    if row is None:
        raise KeyError(f"opportunity {opp_id} not found")
    return Opportunity.from_row(row)


def find(conn: sqlite3.Connection, ref_or_id: str) -> Opportunity | None:
    row = conn.execute(
        f"SELECT * FROM opportunity WHERE (id = ? OR ref = ?) AND {base.alive()}",
        (ref_or_id, ref_or_id),
    ).fetchone()
    return Opportunity.from_row(row) if row else None


def for_org(conn: sqlite3.Connection, org_id: str, open_only: bool = False) -> list[Opportunity]:
    where = f"org_id = ? AND {base.alive()}"
    if open_only:
        where += " AND stage NOT IN ('won', 'lost')"
    rows = conn.execute(
        f"SELECT * FROM opportunity WHERE {where} ORDER BY created_at DESC", (org_id,)
    ).fetchall()
    return [Opportunity.from_row(r) for r in rows]


def by_stage(conn: sqlite3.Connection, stage: str | None = None) -> list[Opportunity]:
    where = [base.alive()]
    params: list[Any] = []
    if stage is not None:
        where.append("stage = ?")
        params.append(stage)
    rows = conn.execute(
        f"SELECT * FROM opportunity WHERE {' AND '.join(where)} ORDER BY stage, created_at",
        params,
    ).fetchall()
    return [Opportunity.from_row(r) for r in rows]


def update(
    conn: sqlite3.Connection, opp_id: str, note: str | None = None, **changes: Any
) -> Opportunity:
    if "stage" in changes:
        raise ValueError("stage transitions go through services.pipeline.move_stage")
    base.update(conn, "opportunity", opp_id, changes, note)
    return get(conn, opp_id)


def delete(conn: sqlite3.Connection, opp_id: str) -> None:
    base.soft_delete(conn, "opportunity", opp_id)
