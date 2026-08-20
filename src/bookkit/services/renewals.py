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
    days_remaining: int  # negative when overdue — counted to renewal_on
    bucket: str  # 'overdue' | '0-30' | '31-60' | '61-90' | '91-120'
    lines: str = ""  # compact cover labels ("GL, AL, EL"); "" when unlinked
    # per-line renewal dates (label, iso end), soonest first — policies are
    # issued per layer, so a LINE can run out before its program does
    line_ends: tuple[tuple[str, str], ...] = ()
    # the date days_remaining counts to: the earliest live line end when the
    # placement is file-linked, else the program period end
    renewal_on: str = ""


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


def _labels(
    conn: sqlite3.Connection, program_path: str | None, cache: dict[str, str]
) -> str:
    from .. import sync

    if not program_path:
        return ""
    if program_path not in cache:
        cache[program_path] = sync.line_labels(program_path, conn)
    return cache[program_path]


def _line_ends(
    conn: sqlite3.Connection,
    program_path: str | None,
    cache: dict[str, tuple[tuple[str, str], ...]],
) -> tuple[tuple[str, str], ...]:
    """`conn` is not decoration: a program_path stored relative to a program
    root cannot be resolved without it, and an unresolved path here means an
    attention row counting down to the PROGRAM period end instead of to the
    earliest line end — the exact "twenty days in the future rendered red as
    70d over" class CLAUDE.md records."""
    from .. import sync

    if not program_path:
        return ()
    if program_path not in cache:
        cache[program_path] = tuple(
            (label, end.isoformat()) for label, end in sync.line_ends(program_path, conn)
        )
    return cache[program_path]


def _renewal_on(placement: Placement, ends: tuple[tuple[str, str], ...]) -> str:
    """The date this placement actually needs attention: the earliest line
    end when one is known, capped by the program period end."""
    if ends:
        return min(ends[0][1], placement.period_to)
    return placement.period_to


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
    """Placements needing renewal attention within `days` — plus overdue
    unrenewed ones — soonest first, with bucket labels.

    Attention is counted to the earliest LINE end, not the program end: the
    scan covers every live placement because an Inland Marine layer can run
    out months before its program period does, and it must surface here the
    moment ITS window opens."""
    today = today or date.today()
    horizon_iso = (today + timedelta(days=days)).isoformat()
    by_org: dict[str, list[Placement]] = {}
    label_cache: dict[str, str] = {}
    ends_cache: dict[str, tuple[tuple[str, str], ...]] = {}
    items: list[RenewalItem] = []
    for placement in placements.expiring_between(conn, "0001-01-01", "9999-12-31"):
        if placement.status == "lapsed":
            continue
        ends = _line_ends(conn, placement.program_path, ends_cache)
        renewal_on = _renewal_on(placement, ends)
        remaining = days_until(renewal_on, today)
        if remaining >= 0 and renewal_on > horizon_iso:
            continue
        if remaining < 0:
            others = by_org.setdefault(
                placement.org_id, placements.for_org(conn, placement.org_id)
            )
            if _renewed(placement, others):
                continue
        items.append(
            RenewalItem(
                placement, orgs.get(conn, placement.org_id), remaining,
                _bucket(remaining), _labels(conn, placement.program_path, label_cache),
                ends, renewal_on,
            )
        )
    return sorted(items, key=lambda item: item.days_remaining)


def next_for_org(
    conn: sqlite3.Connection, org_id: str, today: date | None = None
) -> RenewalItem | None:
    """The client's most urgent renewal across ALL programs: the most overdue
    unrenewed one first, else the soonest upcoming expiry."""
    today = today or date.today()
    candidates = [p for p in placements.for_org(conn, org_id) if p.status != "lapsed"]
    ends_cache: dict[str, tuple[tuple[str, str], ...]] = {}
    live: list[tuple[int, Placement, tuple[tuple[str, str], ...], str]] = []
    for placement in candidates:
        ends = _line_ends(conn, placement.program_path, ends_cache)
        renewal_on = _renewal_on(placement, ends)
        remaining = days_until(renewal_on, today)
        if remaining < 0 and _renewed(placement, candidates):
            continue
        live.append((remaining, placement, ends, renewal_on))
    if not live:
        return None
    remaining, placement, ends, renewal_on = min(live, key=lambda entry: entry[0])
    return RenewalItem(
        placement, orgs.get(conn, org_id), remaining, _bucket(remaining),
        _labels(conn, placement.program_path, {}), ends, renewal_on,
    )


def bucketed(
    conn: sqlite3.Connection, today: date | None = None, days: int = 120
) -> dict[str, list[RenewalItem]]:
    out: dict[str, list[RenewalItem]] = {OVERDUE: []}
    out.update({f"{lo}-{hi}": [] for lo, hi in BUCKETS})
    for item in upcoming(conn, today, days):
        out[item.bucket].append(item)
    return out
