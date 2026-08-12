"""Renewal pipeline: what expires when, bucketed for the home screen.

Every program of every client is scanned — a client with Property AND
Casualty shows both — and a program that EXPIRED without being renewed does
not fall off the radar: it surfaces as overdue until a successor placement
exists (or it's marked lapsed, the deliberate let-it-go)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..dates import days_until
from ..models import Org, Placement
from ..repo import orgs, placements
from ..sync import _bump_years  # renew-at-birth's own year-bump naming rule

BUCKETS = ((0, 30), (31, 60), (61, 90), (91, 120))
OVERDUE = "overdue"


@dataclass(frozen=True)
class RenewalItem:
    placement: Placement
    org: Org
    days_remaining: int  # negative when overdue
    bucket: str  # 'overdue' | '0-30' | '31-60' | '61-90' | '91-120'


def _renewed(placement: Placement, others: list[Placement]) -> bool:
    """A successor exists: same program (allowing the renew year-bump in the
    name), starting on or after this period's end."""
    for other in others:
        if other.id == placement.id or other.period_from < placement.period_to:
            continue
        if other.program_name in (
            placement.program_name,
            _bump_years(placement.program_name),
        ):
            return True
    return False


def _bucket(remaining: int) -> str:
    if remaining < 0:
        return OVERDUE
    return next(
        (f"{lo}-{hi}" for lo, hi in BUCKETS if lo <= remaining <= hi),
        f"{BUCKETS[-1][0]}-{BUCKETS[-1][1]}",
    )


def upcoming(
    conn: sqlite3.Connection, today: date | None = None, days: int = 120
) -> list[RenewalItem]:
    """Placements expiring within `days` — plus overdue unrenewed ones —
    soonest first, with bucket labels."""
    today = today or date.today()
    horizon = today + timedelta(days=days)
    by_org: dict[str, list[Placement]] = {}
    items: list[RenewalItem] = []
    for placement in placements.expiring_between(conn, "0001-01-01", horizon.isoformat()):
        if placement.status == "lapsed":
            continue
        remaining = days_until(placement.period_to, today)
        if remaining < 0:
            others = by_org.setdefault(
                placement.org_id, placements.for_org(conn, placement.org_id)
            )
            if _renewed(placement, others):
                continue
        items.append(
            RenewalItem(
                placement, orgs.get(conn, placement.org_id), remaining,
                _bucket(remaining),
            )
        )
    return items


def next_for_org(
    conn: sqlite3.Connection, org_id: str, today: date | None = None
) -> RenewalItem | None:
    """The client's most urgent renewal across ALL programs: the most overdue
    unrenewed one first, else the soonest upcoming expiry."""
    today = today or date.today()
    candidates = [p for p in placements.for_org(conn, org_id) if p.status != "lapsed"]
    live: list[tuple[int, Placement]] = []
    for placement in candidates:
        remaining = days_until(placement.period_to, today)
        if remaining < 0 and _renewed(placement, candidates):
            continue
        live.append((remaining, placement))
    if not live:
        return None
    remaining, placement = min(live, key=lambda pair: pair[0])
    return RenewalItem(
        placement, orgs.get(conn, org_id), remaining, _bucket(remaining)
    )


def bucketed(
    conn: sqlite3.Connection, today: date | None = None, days: int = 120
) -> dict[str, list[RenewalItem]]:
    out: dict[str, list[RenewalItem]] = {OVERDUE: []}
    out.update({f"{lo}-{hi}": [] for lo, hi in BUCKETS})
    for item in upcoming(conn, today, days):
        out[item.bucket].append(item)
    return out
