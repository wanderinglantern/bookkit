"""Hit rates per market and overall, over a sent_on period.

Quoted ÷ DECIDED, and bound ÷ quoted. A bound submission counts as quoted —
it was quoted on the way to binding — and one still OUT counts in neither:
dividing by everything sent penalised a market for work that had not come
back yet, so AIG read 67% when two of its two decided submissions quoted."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..repo import submissions


@dataclass(frozen=True)
class MarketHitRate:
    market_org_id: str
    market_name: str
    sent: int
    decided: int
    quoted: int
    bound: int
    declined: int

    @property
    def pending(self) -> int:
        """Sent and not yet answered — the count that used to sit silently in
        the denominator, penalising a market for work still in progress."""
        return self.sent - self.decided

    @property
    def quote_rate(self) -> float | None:
        """Quoted ÷ DECIDED. None when nothing has come back yet: a market
        with one submission still out has no hit rate, and rendering that as
        0% is what makes an unlabelled ratio a trap."""
        return self.quoted / self.decided if self.decided else None

    @property
    def bind_rate(self) -> float | None:
        return self.bound / self.quoted if self.quoted else None


def by_market(
    conn: sqlite3.Connection, since: str | None = None, until: str | None = None
) -> list[MarketHitRate]:
    return [
        MarketHitRate(
            r["market_org_id"], r["market_name"],
            r["sent"], r["decided"], r["quoted"], r["bound"], r["declined"],
        )
        for r in submissions.market_counts(conn, since, until)
    ]


def overall(
    conn: sqlite3.Connection, since: str | None = None, until: str | None = None
) -> MarketHitRate:
    rows = by_market(conn, since, until)
    return MarketHitRate(
        "", "overall",
        sum(r.sent for r in rows), sum(r.decided for r in rows),
        sum(r.quoted for r in rows), sum(r.bound for r in rows),
        sum(r.declined for r in rows),
    )
