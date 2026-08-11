"""Cross-book market exposure — 'every account where Swiss Re is on the tower,
renewing in the next 90 days.' Alias-aware: a market is found under its org
name AND every carrier_alias spelling towerkit files use."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..repo import aliases, orgs, projection


@dataclass(frozen=True)
class ExposureRow:
    org_id: str
    org_ref: str
    org_name: str
    placement_id: str
    placement_ref: str
    program_name: str
    period_to: str
    layer_name: str
    attach: int  # cents
    limit: int  # cents
    carrier: str  # the string as the file wrote it
    share_bps: int
    premium: int | None  # carrier's premium share, cents


def _rows(
    conn: sqlite3.Connection, carriers: list[str], days: int, today: date | None
) -> list[ExposureRow]:
    today = today or date.today()
    horizon = today + timedelta(days=days)
    return [
        ExposureRow(
            r["org_id"], r["org_ref"], r["org_name"],
            r["placement_id"], r["placement_ref"], r["program_name"], r["period_to"],
            r["layer_name"], r["attach"], r["lim"], r["carrier"],
            r["share_bps"], r["premium"],
        )
        for r in projection.carrier_exposure(
            conn, carriers, today.isoformat(), horizon.isoformat()
        )
    ]


def for_market(
    conn: sqlite3.Connection,
    market_org_id: str,
    days: int = 90,
    today: date | None = None,
) -> list[ExposureRow]:
    """Exposure for a market org across every spelling of its name."""
    org = orgs.get(conn, market_org_id)
    carriers = [org.name, *aliases.for_market(conn, market_org_id)]
    return _rows(conn, carriers, days, today)


def carrier_exposure(
    conn: sqlite3.Connection,
    carrier: str,
    days: int = 90,
    today: date | None = None,
) -> list[ExposureRow]:
    """Exposure by carrier string; expands to the full market when the string
    resolves to one (name or alias), so every spelling finds everything."""
    market_org_id = aliases.resolve(conn, carrier)
    if market_org_id is not None:
        return for_market(conn, market_org_id, days, today)
    return _rows(conn, [carrier], days, today)


def known_carriers(conn: sqlite3.Connection) -> list[str]:
    return projection.carriers(conn)
