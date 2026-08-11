"""Book summary: total premium, commission, account count, by program and by
market. Placements have no line column; the program name is the grouping label
(see DECISIONS.md)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..money import commission_cents
from ..repo import orgs, placements, projection


@dataclass(frozen=True)
class BookSummary:
    accounts: int
    bound_placements: int
    total_premium: int  # cents
    total_commission: int  # cents
    by_program: list[tuple[str, int, int]]  # (label, premium, count)
    by_market: list[tuple[str, int, int]]  # (carrier, premium, placements) from projection


def summary(conn: sqlite3.Connection) -> BookSummary:
    bound = [
        p
        for p in placements.expiring_between(conn, "0000-01-01", "9999-12-31", ("bound",))
    ]
    accounts = len({p.org_id for p in bound})
    total_premium = sum(p.total_premium or 0 for p in bound)
    total_commission = sum(
        commission_cents(p.total_premium or 0, p.commission_bps or 0) for p in bound
    )
    programs: dict[str, tuple[int, int]] = {}
    for p in bound:
        label = _program_label(p.program_name)
        premium, count = programs.get(label, (0, 0))
        programs[label] = (premium + (p.total_premium or 0), count + 1)
    by_program = sorted(
        ((label, premium, count) for label, (premium, count) in programs.items()),
        key=lambda t: -t[1],
    )
    by_market = [
        (r["carrier"], int(r["premium"]), int(r["placements"]))
        for r in projection.market_premiums(conn)
    ]
    return BookSummary(
        accounts, len(bound), total_premium, total_commission, by_program, by_market
    )


def _program_label(program_name: str) -> str:
    """'2025 Casualty Program' → 'Casualty Program' so years group together."""
    parts = program_name.split()
    if parts and parts[0].isdigit():
        parts = parts[1:]
    return " ".join(parts) or program_name


def active_client_count(conn: sqlite3.Connection) -> int:
    return len(orgs.list_orgs(conn, kind="client", status="active"))
