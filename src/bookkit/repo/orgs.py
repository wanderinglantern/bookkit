"""Organisations (clients, markets, other) + market profiles + appetite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..ids import ORG_REF, next_ref
from ..models import Appetite, MarketProfile, Org
from . import base


def guard_name(
    conn: sqlite3.Connection, name: str, org_id: str | None = None
) -> None:
    """Refuse a rename onto a name another live org already holds.

    THIS IS THE `repo/team._guard_name` STORY, ONE TABLE OVER. Every
    client-scoped MCP tool resolves through `mcpserver._resolve_client`, which
    falls back to `find_by_name` — a plain `WHERE name = ?` that returns the
    FIRST match. Rename Acme to "Henderson Group" while a real Henderson Group
    exists and every later client-scoped call lands on Acme's row instead:
    the account whose name you typed is now unreachable, and the writes meant
    for it go somewhere else. That is the failure CLAUDE.md records for two
    colleagues sharing a name, and it is why the guard belongs HERE rather
    than in a caller — forms.entities.apply_org (the TUI's edit form and the
    web's) calls `update` too, so a guard in mcpserver would leave both of
    those writing straight past it, exactly as the member guard once did.

    NOT case-sensitive, because the resolver's own fallbacks are not: a
    second "henderson group" is just as ambiguous to a human reading a list.

    NOT ON `create`, deliberately. Duplicate orgs legitimately ARRIVE — the
    spreadsheet importer, sync's carrier auto-create and seed all mint orgs
    from data nobody hand-checked — and the cure there is services.merge, not
    an exception that aborts an import half-way. The MCP create door has its
    own rapidfuzz guard (mcpserver._client_create) where a human is on the
    other end to answer it. What is guarded here is the operation that TAKES
    a name away from the row that answers to it.
    """
    row = conn.execute(
        f"""SELECT id, name FROM org
             WHERE lower(name) = lower(?) AND id IS NOT ? AND {base.alive()}
             LIMIT 1""",
        (name, org_id),
    ).fetchone()
    if row is not None:
        raise ValueError(
            f"{row['name']} already holds that name — every client-scoped "
            f"lookup takes the first match, so renaming onto it would send "
            f"later writes to the wrong account; rename or merge that one "
            f"first"
        )


def create(conn: sqlite3.Connection, **fields: Any) -> Org:
    fields.setdefault("ref", next_ref(conn, ORG_REF))
    org_id = base.insert(conn, "org", fields)
    return get(conn, org_id)


def get(conn: sqlite3.Connection, org_id: str) -> Org:
    row = base.get(conn, "org", org_id)
    if row is None:
        raise KeyError(f"org {org_id} not found")
    return Org.from_row(row)


@dataclass(frozen=True)
class OrgLabel:
    """How an account is NAMED and REACHED, together.

    The two always travel as a pair — a surface that shows an account shows its
    name and links on its ref — and separating them is how Today came to print
    `ACC-0004` in the Account column of one section while every other section
    printed "Delta Marine Logistics" (Grant, 2026-08-20). The tasks table was
    the one place holding only the ref lookup, so the ref is what it printed.
    """

    ref: str
    name: str


def labels_for(conn: sqlite3.Connection, org_ids: set[str]) -> dict[str, OrgLabel]:
    """id → (ref, name) for the LIVING orgs among org_ids, ONE query.

    A missing key means the org was merged or deleted away — the caller picks
    the label, and `macros/account.html` picks an em-dash.

    One query, not one per id: the caller before this looped `orgs.get` over
    every account on the page, which on a busy Today is thirty round trips for
    a column of links.
    """
    if not org_ids:
        return {}
    marks = ",".join("?" * len(org_ids))
    rows = conn.execute(
        f"SELECT id, ref, name FROM org WHERE id IN ({marks}) AND {base.alive()}",
        tuple(org_ids),
    ).fetchall()
    return {r["id"]: OrgLabel(ref=r["ref"], name=r["name"]) for r in rows}


def names_for(conn: sqlite3.Connection, org_ids: set[str]) -> dict[str, str]:
    """id → name. Delegates, so there is ONE definition of the query and of
    what "living" means for this lookup."""
    return {org_id: label.name for org_id, label in labels_for(conn, org_ids).items()}


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
    if changes.get("name"):
        guard_name(conn, str(changes["name"]), org_id)
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
    """Move every row to the surviving market on a merge."""
    # Row by row through base.update, not one bulk UPDATE: the move is a
    # field change like any other and must land in the event log, or the
    # merge cannot be reverted — the record would come back while the rows
    # that moved stayed moved. Same rule as rfi.reassign_market.
    rows = conn.execute(
        f"""SELECT id FROM appetite
            WHERE market_org_id = ? AND {base.alive()}""",
        (from_org_id,),
    ).fetchall()
    for row in rows:
        base.update(conn, "appetite", row[0], {"market_org_id": to_org_id}, "market merged")
    return len(rows)


def add_appetite(conn: sqlite3.Connection, market_org_id: str, **fields: Any) -> Appetite:
    appetite_id = base.insert(conn, "appetite", {"market_org_id": market_org_id, **fields})
    row = base.get(conn, "appetite", appetite_id)
    return Appetite.from_row(row)  # type: ignore[arg-type]


def get_appetite(conn: sqlite3.Connection, appetite_id: str) -> Appetite:
    row = base.get(conn, "appetite", appetite_id)
    if row is None:
        raise KeyError(f"appetite {appetite_id} not found")
    return Appetite.from_row(row)


def update_appetite(
    conn: sqlite3.Connection, appetite_id: str, **fields: Any
) -> Appetite:
    """Correct an appetite row. `appetite` is already in ENTITY_TABLES, so this
    is event-logged and `u`-undoable like every other field write (review
    F18 — add_appetite existed with no way back)."""
    base.update(conn, "appetite", appetite_id, fields)
    return get_appetite(conn, appetite_id)


def delete_appetite(conn: sqlite3.Connection, appetite_id: str) -> None:
    """Soft, so `u` puts it back — the same promise every other delete makes."""
    base.soft_delete(conn, "appetite", appetite_id)


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
