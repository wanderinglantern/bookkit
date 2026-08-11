"""Renewal pipeline: what expires when, bucketed for the home screen."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..dates import days_until
from ..models import Org, Placement
from ..repo import orgs, placements

BUCKETS = ((0, 30), (31, 60), (61, 90), (91, 120))


@dataclass(frozen=True)
class RenewalItem:
    placement: Placement
    org: Org
    days_remaining: int
    bucket: str  # '0-30' | '31-60' | '61-90' | '91-120'


def upcoming(
    conn: sqlite3.Connection, today: date | None = None, days: int = 120
) -> list[RenewalItem]:
    """Placements expiring within `days`, soonest first, with bucket labels."""
    today = today or date.today()
    horizon = today + timedelta(days=days)
    items: list[RenewalItem] = []
    for placement in placements.expiring_between(conn, today.isoformat(), horizon.isoformat()):
        remaining = days_until(placement.period_to, today)
        bucket = next(
            (f"{lo}-{hi}" for lo, hi in BUCKETS if lo <= remaining <= hi),
            f"{BUCKETS[-1][0]}-{BUCKETS[-1][1]}",
        )
        items.append(
            RenewalItem(placement, orgs.get(conn, placement.org_id), remaining, bucket)
        )
    return items


def bucketed(
    conn: sqlite3.Connection, today: date | None = None, days: int = 120
) -> dict[str, list[RenewalItem]]:
    out: dict[str, list[RenewalItem]] = {f"{lo}-{hi}": [] for lo, hi in BUCKETS}
    for item in upcoming(conn, today, days):
        out[item.bucket].append(item)
    return out
