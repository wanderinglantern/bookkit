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


def reassign_market(conn: sqlite3.Connection, from_org_id: str, to_org_id: str) -> int:
    """Move every alias to the survivor on a market merge."""
    # Row by row WITH AN EVENT EACH, not one bulk UPDATE: this was the single
    # sub-write of a market merge that left no trace at all — zero event_log
    # rows — so reverting the merge brought the duplicate market back to life
    # with every one of its aliases still pointing at the survivor, and any
    # towerkit file spelling the carrier that way went on resolving to the
    # wrong org (2026-08-18). Its seven siblings (contacts, submissions,
    # tasks, documents, interactions, orgs, rfi) all say the same thing: the
    # move is a change like any other, or the merge cannot be reverted.
    #
    # The event is shaped exactly like set_alias's — entity is the org that
    # NOW owns the alias, field 'carrier_alias', new_value the alias string —
    # with old_value carrying the org it came from instead of None. That one
    # difference is what services/batches reads to put it back: an old_value
    # means "return it", None means "it did not exist before, remove it".
    rows = conn.execute(
        "SELECT alias FROM carrier_alias WHERE market_org_id = ? ORDER BY alias",
        (from_org_id,),
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE carrier_alias SET market_org_id = ? WHERE alias = ?",
            (to_org_id, row["alias"]),
        )
        base.log_event(
            conn, "org", to_org_id, "carrier_alias", from_org_id, row["alias"],
            note="market merged",
        )
    return len(rows)


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
