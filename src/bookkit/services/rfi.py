"""Information-request rules. A request's open/closed state is DERIVED here
and stored nowhere: it is open while any item is outstanding. The chase feed
follows the house attention rule — a 120-day window, and nothing overdue
ever falls off."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..dates import days_until
from ..models import RfiItem, RfiRequest
from ..repo import rfi as rfi_repo


@dataclass(frozen=True)
class RfiChase:
    """One row of the chase queue: a request you would send one email about."""

    request: RfiRequest
    org_name: str
    market_name: str | None
    open_count: int
    total_count: int
    earliest_due: str | None
    days_remaining: int


def is_open(conn: sqlite3.Connection, request_id: str) -> bool:
    """Open while anything is still outstanding. A request with NO items reads
    open by convention — it is an ask you have not yet written down, not a
    finished one."""
    request = rfi_repo.get_request(conn, request_id)
    if request.cancelled_at:
        return False
    if rfi_repo.item_count(conn, request_id) == 0:
        return True
    return rfi_repo.open_item_count(conn, request_id) > 0


def outstanding_requests(
    conn: sqlite3.Connection, today: date, days: int = 120
) -> list[RfiChase]:
    horizon = (today + timedelta(days=days)).isoformat()
    out: list[RfiChase] = []
    for row in rfi_repo.outstanding_rows(conn, horizon):
        earliest = row["earliest_due"]
        fields = {k: row[k] for k in row.keys() if k in RfiRequest.model_fields}
        out.append(
            RfiChase(
                request=RfiRequest.model_validate(fields),
                org_name=row["org_name"],
                market_name=row["market_name"],
                open_count=int(row["open_count"]),
                total_count=int(row["total_count"]),
                earliest_due=earliest,
                days_remaining=days_until(earliest, today),
            )
        )
    return out


def mark_received(conn: sqlite3.Connection, item_id: str, on: str) -> RfiItem:
    """d on an item: received, dated. One field write, so u undoes it."""
    return rfi_repo.update_item(conn, item_id, status="received", received_on=on)
