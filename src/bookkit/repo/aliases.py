"""carrier_alias — every towerkit spelling of a carrier maps to one market org."""

from __future__ import annotations

import sqlite3

from ..db import utc_now
from . import base


def set_alias(conn: sqlite3.Connection, alias: str, market_org_id: str) -> None:
    conn.execute(
        "INSERT INTO carrier_alias (alias, market_org_id, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT (alias) DO UPDATE SET market_org_id = excluded.market_org_id,"
        " created_at = excluded.created_at",
        (alias, market_org_id, utc_now()),
    )
    base.log_event(conn, "org", market_org_id, "carrier_alias", None, alias)


def remove(conn: sqlite3.Connection, alias: str) -> None:
    conn.execute("DELETE FROM carrier_alias WHERE alias = ?", (alias,))


def for_market(conn: sqlite3.Connection, market_org_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT alias FROM carrier_alias WHERE market_org_id = ? ORDER BY alias",
        (market_org_id,),
    ).fetchall()
    return [r["alias"] for r in rows]


def resolve(conn: sqlite3.Connection, carrier: str) -> str | None:
    """Carrier string → market org id: exact name match wins, then aliases."""
    row = conn.execute(
        f"SELECT id FROM org WHERE kind = 'market' AND name = ? AND {base.alive()}",
        (carrier,),
    ).fetchone()
    if row:
        return str(row["id"])
    row = conn.execute(
        "SELECT market_org_id FROM carrier_alias WHERE alias = ?", (carrier,)
    ).fetchone()
    return str(row["market_org_id"]) if row else None


def alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    """alias → market_org_id, for bulk canonicalisation."""
    rows = conn.execute("SELECT alias, market_org_id FROM carrier_alias").fetchall()
    return {r["alias"]: r["market_org_id"] for r in rows}


def unresolved_carriers(conn: sqlite3.Connection) -> list[str]:
    """Carrier strings on projected towers that match no market org name and
    no alias — the strings that would silently miss every cross-book join."""
    rows = conn.execute(
        f"""
        SELECT DISTINCT pp.carrier FROM proj_participant pp
        WHERE pp.carrier NOT IN (
            SELECT name FROM org WHERE kind = 'market' AND {base.alive()}
        )
        AND pp.carrier NOT IN (SELECT alias FROM carrier_alias)
        ORDER BY pp.carrier
        """
    ).fetchall()
    return [r["carrier"] for r in rows]
