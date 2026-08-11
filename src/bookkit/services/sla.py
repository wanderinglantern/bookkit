"""Submission SLA: what's out at market with no response after N days."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..models import Org, Submission
from ..repo import opportunities, orgs, placements, submissions


@dataclass(frozen=True)
class OverdueSubmission:
    submission: Submission
    market: Org
    account: Org  # the client the submission is for
    days_out: int


def past_sla(
    conn: sqlite3.Connection, today: date | None = None, sla_days: int = 10
) -> list[OverdueSubmission]:
    today = today or date.today()
    cutoff = (today - timedelta(days=sla_days)).isoformat()
    out: list[OverdueSubmission] = []
    for sub in submissions.outstanding(conn, sent_on_or_before=cutoff):
        days_out = (today - date.fromisoformat(sub.sent_on)).days
        account_org_id = _account_org_id(conn, sub)
        out.append(
            OverdueSubmission(
                sub,
                orgs.get(conn, sub.market_org_id),
                orgs.get(conn, account_org_id),
                days_out,
            )
        )
    out.sort(key=lambda o: -o.days_out)
    return out


def _account_org_id(conn: sqlite3.Connection, sub: Submission) -> str:
    if sub.placement_id:
        return placements.get(conn, sub.placement_id).org_id
    if sub.opportunity_id is None:  # schema CHECK guarantees one parent
        raise ValueError(f"submission {sub.id} has no parent")
    return opportunities.get(conn, sub.opportunity_id).org_id
