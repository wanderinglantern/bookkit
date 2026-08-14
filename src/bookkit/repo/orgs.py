"""Organisations (clients, markets, other) + market profiles + appetite."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ids import ORG_REF, next_ref
from ..models import Appetite, MarketProfile, Org
from . import base


def create(conn: sqlite3.Connection, **fields: Any) -> Org:
    fields.setdefault("ref", next_ref(conn, ORG_REF))
    org_id = base.insert(conn, "org", fields)
    return get(conn, org_id)


def get(conn: sqlite3.Connection, org_id: str) -> Org:
    row = base.get(conn, "org", org_id)
    if row is None:
        raise KeyError(f"org {org_id} not found")
    return Org.from_row(row)


def names_for(conn: sqlite3.Connection, org_ids: set[str]) -> dict[str, str]:
    """id → name for the LIVING orgs among org_ids, one query. A missing key
    means the org was merged/deleted away — the caller picks the label."""
    if not org_ids:
        return {}
    marks = ",".join("?" * len(org_ids))
    rows = conn.execute(
        f"SELECT id, name FROM org WHERE id IN ({marks}) AND {base.alive()}",
        tuple(org_ids),
    ).fetchall()
    return {r["id"]: r["name"] for r in rows}


def find(conn: sqlite3.Connection, ref_or_id: str) -> Org | None:
    row = conn.execute(
        f"SELECT * FROM org WHERE (id = ? OR ref = ?) AND {base.alive()}",
        (ref_or_id, ref_or_id),
    ).fetchone()
    return Org.from_row(row) if row else None


def find_by_name(conn: sqlite3.Connection, name: str) -> Org | None:
    row = conn.execute(
        f"SELECT * FROM org WHERE name = ? AND {base.alive()}", (name,)
    ).fetchone()
    return Org.from_row(row) if row else None


def list_orgs(
    conn: sqlite3.Connection,
    kind: str | None = None,
    status: str | None = None,
    owner: str | None = None,
) -> list[Org]:
    where = [base.alive()]
    params: list[Any] = []
    for col, val in (("kind", kind), ("status", status), ("owner", owner)):
        if val is not None:
            where.append(f"{col} = ?")
            params.append(val)
    rows = conn.execute(
        f"SELECT * FROM org WHERE {' AND '.join(where)} ORDER BY name", params
    ).fetchall()
    return [Org.from_row(r) for r in rows]


def update(conn: sqlite3.Connection, org_id: str, note: str | None = None, **changes: Any) -> Org:
    base.update(conn, "org", org_id, changes, note)
    return get(conn, org_id)


def delete(conn: sqlite3.Connection, org_id: str) -> None:
    base.soft_delete(conn, "org", org_id)


# --- market profile (1:1 with org where kind='market') ------------------------


def set_market_profile(conn: sqlite3.Connection, org_id: str, **fields: Any) -> MarketProfile:
    existing = conn.execute(
        "SELECT * FROM market_profile WHERE org_id = ?", (org_id,)
    ).fetchone()
    if existing is None:
        cols = ", ".join(["org_id", *fields])
        marks = ", ".join("?" for _ in range(len(fields) + 1))
        conn.execute(
            f"INSERT INTO market_profile ({cols}) VALUES ({marks})",
            (org_id, *fields.values()),
        )
    elif fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE market_profile SET {sets} WHERE org_id = ?", (*fields.values(), org_id)
        )
    return get_market_profile(conn, org_id)  # type: ignore[return-value]


def get_market_profile(conn: sqlite3.Connection, org_id: str) -> MarketProfile | None:
    row = conn.execute("SELECT * FROM market_profile WHERE org_id = ?", (org_id,)).fetchone()
    return MarketProfile.from_row(row) if row else None


# --- appetite -----------------------------------------------------------------


def reassign_appetite(conn: sqlite3.Connection, from_org_id: str, to_org_id: str) -> int:
    """Bulk move for market merges; the service logs the event."""
    cur = conn.execute(
        "UPDATE appetite SET market_org_id = ? WHERE market_org_id = ?",
        (to_org_id, from_org_id),
    )
    return cur.rowcount


def add_appetite(conn: sqlite3.Connection, market_org_id: str, **fields: Any) -> Appetite:
    appetite_id = base.insert(conn, "appetite", {"market_org_id": market_org_id, **fields})
    row = base.get(conn, "appetite", appetite_id)
    return Appetite.from_row(row)  # type: ignore[arg-type]


def appetite_for_market(conn: sqlite3.Connection, market_org_id: str) -> list[Appetite]:
    rows = conn.execute(
        f"SELECT * FROM appetite WHERE market_org_id = ? AND {base.alive()} ORDER BY line",
        (market_org_id,),
    ).fetchall()
    return [Appetite.from_row(r) for r in rows]


def clients_with_recency(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Active client orgs with their latest interaction date and current bound
    premium — the raw material for the staleness service."""
    return conn.execute(
        f"""
        SELECT o.*,
               (SELECT MAX(i.occurred_on) FROM interaction i
                 WHERE i.org_id = o.id AND {base.alive('i')}) AS last_on,
               COALESCE((SELECT p.total_premium FROM placement p
                 WHERE p.org_id = o.id AND p.status = 'bound' AND {base.alive('p')}
                 ORDER BY p.period_to DESC LIMIT 1), 0) AS premium
        FROM org o
        WHERE o.kind = 'client' AND o.status = 'active' AND {base.alive('o')}
        """,
    ).fetchall()


def markets_for_line(
    conn: sqlite3.Connection,
    line: str,
    min_limit: int | None = None,
    premium: int | None = None,
) -> list[tuple[Org, Appetite]]:
    """'Who should I approach for a $50M cyber excess' — markets with appetite
    for a line, optionally filtered by capacity and minimum premium."""
    rows = conn.execute(
        f"""
        SELECT a.*, o.id AS o_id FROM appetite a
        JOIN org o ON o.id = a.market_org_id
        WHERE a.line = ? AND a.appetite != 'no' AND {base.alive('a')} AND {base.alive('o')}
        ORDER BY CASE a.appetite
            WHEN 'target' THEN 0 WHEN 'will_consider' THEN 1 ELSE 2 END
        """,
        (line,),
    ).fetchall()
    out: list[tuple[Org, Appetite]] = []
    for row in rows:
        appetite = Appetite.from_row({k: row[k] for k in row.keys() if k != "o_id"})
        if min_limit is not None and appetite.max_limit is not None:
            if appetite.max_limit < min_limit:
                continue
        if premium is not None and appetite.min_premium is not None:
            if premium < appetite.min_premium:
                continue
        out.append((get(conn, row["o_id"]), appetite))
    return out


# --- market families -----------------------------------------------------------


def set_parent(conn: sqlite3.Connection, org_id: str, parent_org_id: str | None) -> Org:
    """Nest an underwriting company under a master company (or unnest with
    None). Organizational only — nothing that references the org moves.
    Refuses self-nesting and cycles."""
    if parent_org_id is not None:
        if parent_org_id == org_id:
            raise ValueError("cannot nest a company under itself")
        ancestor: str | None = parent_org_id
        while ancestor is not None:
            if ancestor == org_id:
                raise ValueError("cannot nest a company under its own descendant")
            row = conn.execute(
                "SELECT parent_org_id FROM org WHERE id = ?", (ancestor,)
            ).fetchone()
            ancestor = row["parent_org_id"] if row else None
    return update(conn, org_id, parent_org_id=parent_org_id)


def children(conn: sqlite3.Connection, org_id: str) -> list[Org]:
    rows = conn.execute(
        f"SELECT * FROM org WHERE parent_org_id = ? AND {base.alive()} ORDER BY name",
        (org_id,),
    ).fetchall()
    return [Org.from_row(r) for r in rows]


def market_families(conn: sqlite3.Connection) -> list[tuple[Org, list[Org]]]:
    """Markets as an outline: (top-level market, nested children) pairs,
    alphabetical. A child whose parent is deleted floats back to the top."""
    markets = list_orgs(conn, kind="market")
    by_id = {org.id: org for org in markets}
    kids: dict[str, list[Org]] = {}
    tops: list[Org] = []
    for org in markets:
        if org.parent_org_id and org.parent_org_id in by_id:
            kids.setdefault(org.parent_org_id, []).append(org)
        else:
            tops.append(org)
    return [(top, kids.get(top.id, [])) for top in tops]
