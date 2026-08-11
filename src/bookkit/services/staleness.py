"""Stale accounts: active clients with no interaction in > N days, weighted by
premium so the big neglected accounts surface first."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ..models import Org
from ..repo import orgs


@dataclass(frozen=True)
class StaleAccount:
    org: Org
    last_interaction_on: str | None  # DATE, None = never contacted
    days_stale: int
    premium: int  # current premium in cents (0 when unknown)
    weight: int  # days_stale × max(premium, 1) — the sort key


def stale_accounts(
    conn: sqlite3.Connection, today: date | None = None, threshold_days: int = 60
) -> list[StaleAccount]:
    today = today or date.today()
    out: list[StaleAccount] = []
    for row in orgs.clients_with_recency(conn):
        last_on = row["last_on"]
        days_stale = (
            (today - date.fromisoformat(last_on)).days if last_on else threshold_days + 365
        )
        if days_stale <= threshold_days:
            continue
        premium = int(row["premium"] or 0)
        org = Org.from_row({k: row[k] for k in row.keys() if k not in ("last_on", "premium")})
        out.append(
            StaleAccount(org, last_on, days_stale, premium, days_stale * max(premium, 1))
        )
    out.sort(key=lambda s: -s.weight)
    return out
