"""Hit rates: per market and overall, submissions quoted ÷ sent and
bound ÷ quoted, over a sent_on period. A bound submission counts as quoted —
it was quoted on the way to binding."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..repo import submissions


@dataclass(frozen=True)
class MarketHitRate:
    market_org_id: str
    market_name: str
    sent: int
    quoted: int
    bound: int
    declined: int

    @property
    def quote_rate(self) -> float:
        return self.quoted / self.sent if self.sent else 0.0

    @property
    def bind_rate(self) -> float:
        return self.bound / self.quoted if self.quoted else 0.0


def by_market(
    conn: sqlite3.Connection, since: str | None = None, until: str | None = None
) -> list[MarketHitRate]:
    return [
        MarketHitRate(
            r["market_org_id"], r["market_name"],
            r["sent"], r["quoted"], r["bound"], r["declined"],
        )
        for r in submissions.market_counts(conn, since, until)
    ]


def overall(
    conn: sqlite3.Connection, since: str | None = None, until: str | None = None
) -> MarketHitRate:
    rows = by_market(conn, since, until)
    return MarketHitRate(
        "", "overall",
        sum(r.sent for r in rows), sum(r.quoted for r in rows),
        sum(r.bound for r in rows), sum(r.declined for r in rows),
    )
