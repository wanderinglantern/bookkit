"""Cross-book market exposure — 'every account where Swiss Re is on the tower,
renewing in the next 90 days.' Alias-aware: a market is found under its org
name AND every carrier_alias spelling towerkit files use."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from .. import sync
from ..repo import aliases, orgs, placements, projection


@dataclass(frozen=True)
class ExposureRow:
    org_id: str
    org_ref: str
    org_name: str
    placement_id: str
    placement_ref: str
    program_name: str
    period_to: str
    status: str  # the PLACEMENT's status — quoted exposure is not placed
    layer_name: str
    attach: int  # cents
    limit: int  # cents
    carrier: str  # the string as the file wrote it
    share_bps: int
    premium: int | None  # carrier's premium share, cents
    # THE DATE THE WINDOW IS MEASURED TO, and the one a surface must print:
    # the earliest LINE end capped by the program period, `renewals.renewal_on`
    # (CLAUDE.md — never `placement.period_to`). `period_to` stays beside it
    # because the program's own period is a different, still-useful fact.
    renewal_on: str


def _rows(
    conn: sqlite3.Connection, carriers: list[str], days: int, today: date | None
) -> list[ExposureRow]:
    """THE WINDOW IS MEASURED ON THE RENEWAL DATE, not on the program period
    end. The SQL used to filter `p.period_to` between today and the horizon,
    so a layer running out three months before its program's period was
    invisible here while Today, Book, the account header and the calendar all
    counted to it — the exact fact CLAUDE.md's rule names (2026-08-24).

    The repo query is now a coarse floor (a program that has already run out
    is not renewing) and the real window is applied here, where the line ends
    can be read. One file open per PLACEMENT, cached: a market sits on many
    layers of the same tower and re-reading it per row is the same defect
    `layer_details` was reduced for.
    """
    from . import renewals

    today = today or date.today()
    horizon = today + timedelta(days=days)
    ends: dict[str, list[date]] = {}
    rows: list[ExposureRow] = []
    for r in projection.carrier_exposure(conn, carriers, today.isoformat()):
        placement = placements.get(conn, r["placement_id"])
        path = placement.program_path
        if path is not None and path not in ends:
            ends[path] = [end for _, end in sync.line_ends(path, conn)]
        renewal_on = renewals.renewal_on(placement, ends.get(path or "", []))
        # NO LOWER BOUND ON THE RENEWAL DATE. The repo's floor already keeps
        # programs that have wholly run out off this list; a line that ran out
        # while the rest of the tower still stands is OVERDUE, and CLAUDE.md's
        # rule is that overdue renewals never fall off. The soonest line end
        # is what this row is about, so an overdue one belongs here loudest.
        if renewal_on > horizon:
            continue
        rows.append(
            ExposureRow(
                r["org_id"], r["org_ref"], r["org_name"],
                r["placement_id"], r["placement_ref"], r["program_name"],
                r["period_to"],
                r["status"], r["layer_name"], r["attach"], r["lim"], r["carrier"],
                r["share_bps"], r["premium"], renewal_on.isoformat(),
            )
        )
    # The date it is a queue OF, not the program period the query happened to
    # order by.
    rows.sort(key=lambda row: (row.renewal_on, row.org_name))
    return rows


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
