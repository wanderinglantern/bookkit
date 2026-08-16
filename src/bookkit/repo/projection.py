"""proj_* tables — the derived cache of towerkit program files (§5).

Never authoritative: rows are wholesale-replaced per placement at projection
time and only ever read for cross-book queries.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import base


def replace_for_placement(
    conn: sqlite3.Connection,
    placement_id: str,
    synced_at: str,
    layers: list[dict[str, Any]],
    participants: list[dict[str, Any]],
    retentions: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM proj_layer WHERE placement_id = ?", (placement_id,))
    conn.execute("DELETE FROM proj_participant WHERE placement_id = ?", (placement_id,))
    conn.execute("DELETE FROM proj_retention WHERE placement_id = ?", (placement_id,))
    for layer in layers:
        conn.execute(
            "INSERT INTO proj_layer (placement_id, layer_id, name, applies_to, attach, lim,"
            " premium, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                placement_id, layer["layer_id"], layer["name"], layer["applies_to"],
                layer["attach"], layer["lim"], layer["premium"], synced_at,
            ),
        )
    for part in participants:
        conn.execute(
            "INSERT INTO proj_participant (placement_id, layer_id, carrier, share_bps,"
            " premium, synced_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                placement_id, part["layer_id"], part["carrier"], part["share_bps"],
                part["premium"], synced_at,
            ),
        )
    for idx, ret in enumerate(retentions):
        conn.execute(
            "INSERT INTO proj_retention (placement_id, idx, applies_to, type, amount,"
            " aggregate, vehicle, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                placement_id, idx, ret["applies_to"], ret["type"], ret["amount"],
                ret["aggregate"], ret["vehicle"], synced_at,
            ),
        )


def reassign(conn: sqlite3.Connection, from_id: str, to_id: str) -> None:
    """Move a placement's projection cache during a merge (target's is cleared
    first — proj rows always mirror exactly one file)."""
    for table in ("proj_layer", "proj_participant", "proj_retention"):
        conn.execute(f"DELETE FROM {table} WHERE placement_id = ?", (to_id,))
        conn.execute(
            f"UPDATE {table} SET placement_id = ? WHERE placement_id = ?", (to_id, from_id)
        )


def layers_for_placement(conn: sqlite3.Connection, placement_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM proj_layer WHERE placement_id = ? ORDER BY attach, rowid",
        (placement_id,),
    ).fetchall()


def participants_for_placement(conn: sqlite3.Connection, placement_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT pp.*, pl.name AS layer_name, pl.attach, pl.lim
        FROM proj_participant pp
        JOIN proj_layer pl ON pl.placement_id = pp.placement_id AND pl.layer_id = pp.layer_id
        WHERE pp.placement_id = ? ORDER BY pl.attach, pp.share_bps DESC
        """,
        (placement_id,),
    ).fetchall()


def carriers(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT carrier FROM proj_participant ORDER BY carrier"
        ).fetchall()
    ]


def carrier_exposure(
    conn: sqlite3.Connection,
    carriers: list[str],
    expiring_from: str,
    expiring_to: str,
) -> list[sqlite3.Row]:
    """Every account where any of these carrier strings (a market's name plus
    its aliases) is on the tower, renewing in the window — the query the
    proj_ tables exist for.

    Carries `status`, and does NOT filter by it. A tower a carrier has quoted
    but not bound is real exposure worth seeing, but it is not placed
    business: rendering both alike made "ON THE TOWER" read $650K where the
    book's bound-only total read nothing, from the same data. The caller
    labels each row; market_premiums stays bound-only, because a premium
    TOTAL is a different question from a list."""
    if not carriers:
        return []
    marks = ", ".join("?" for _ in carriers)
    return conn.execute(
        f"""
        SELECT o.name AS org_name, o.ref AS org_ref, o.id AS org_id,
               p.id AS placement_id, p.ref AS placement_ref, p.program_name,
               p.period_to, p.status, pl.name AS layer_name, pl.attach, pl.lim,
               pp.carrier, pp.share_bps, pp.premium
        FROM proj_participant pp
        JOIN placement p ON p.id = pp.placement_id
        JOIN proj_layer pl ON pl.placement_id = pp.placement_id AND pl.layer_id = pp.layer_id
        JOIN org o ON o.id = p.org_id
        WHERE pp.carrier IN ({marks}) AND p.period_to >= ? AND p.period_to <= ?
          AND {base.alive('p')} AND {base.alive('o')}
        ORDER BY p.period_to, o.name
        """,
        (*carriers, expiring_from, expiring_to),
    ).fetchall()


def market_premiums(
    conn: sqlite3.Connection, statuses: tuple[str, ...] = ("bound",)
) -> list[sqlite3.Row]:
    """Premium by carrier across current placements, from the projection."""
    marks = ", ".join("?" for _ in statuses)
    return conn.execute(
        f"""
        SELECT pp.carrier, SUM(COALESCE(pp.premium, 0)) AS premium,
               COUNT(DISTINCT pp.placement_id) AS placements
        FROM proj_participant pp
        JOIN placement p ON p.id = pp.placement_id
        WHERE p.status IN ({marks}) AND {base.alive('p')}
        GROUP BY pp.carrier ORDER BY premium DESC
        """,
        statuses,
    ).fetchall()
