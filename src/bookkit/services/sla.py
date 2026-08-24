"""Submission SLA: what's out at market with no response after N days."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..models import Contact, Org, Submission
from ..repo import contacts, opportunities, orgs, placements, submissions


@dataclass(frozen=True)
class OverdueSubmission:
    submission: Submission
    market: Org
    account: Org  # the client the submission is for
    days_out: int
    underwriter: Contact | None = None
    """WHO to chase, not just which carrier.

    The AE review's words: Today reports six submissions past SLA and names
    only "Travelers" — which you cannot email. `underwriter_contact_id` was
    declared in models.py and 001_initial.sql and read by nothing in the
    codebase; this is the way out. None is honest and common: no submission
    written before 2026-08-18 could have had one, because no form offered
    the field."""

    @property
    def underwriter_name(self) -> str | None:
        return self.underwriter.name if self.underwriter else None


DEFAULT_SLA_DAYS = 10


def past_sla(
    conn: sqlite3.Connection, today: date | None = None, sla_days: int = DEFAULT_SLA_DAYS
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
                _underwriter(conn, sub),
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


def _underwriter(conn: sqlite3.Connection, sub: Submission) -> Contact | None:
    """The named person at the market, or None.

    A KeyError here means the contact was soft-deleted after the submission
    was written. That must not remove the submission from the SLA queue — the
    chase is still late, you just have to find someone else to chase — so it
    degrades to "no name" rather than propagating, the same rule
    rfi.outstanding_rows applies to a merged-away market."""
    if not sub.underwriter_contact_id:
        return None
    try:
        return contacts.get(conn, sub.underwriter_contact_id)
    except KeyError:
        return None
