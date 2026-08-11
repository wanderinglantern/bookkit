"""Cross-book market exposure — 'every account where Swiss Re is on the tower,
renewing in the next 90 days.' The reason the proj_ tables exist."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..repo import projection


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
    share_bps: int
    premium: int | None  # carrier's premium share, cents


def carrier_exposure(
    conn: sqlite3.Connection,
    carrier: str,
    days: int = 90,
    today: date | None = None,
) -> list[ExposureRow]:
    today = today or date.today()
    horizon = today + timedelta(days=days)
    return [
        ExposureRow(
            r["org_id"], r["org_ref"], r["org_name"],
            r["placement_id"], r["placement_ref"], r["program_name"], r["period_to"],
            r["layer_name"], r["attach"], r["lim"], r["share_bps"], r["premium"],
        )
        for r in projection.carrier_exposure(
            conn, carrier, today.isoformat(), horizon.isoformat()
        )
    ]


def known_carriers(conn: sqlite3.Connection) -> list[str]:
    return projection.carriers(conn)
