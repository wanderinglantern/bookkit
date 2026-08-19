"""Quotes in hand — the middle of the placement, which nothing tracked.

`services/sla.py` answers "what is still out at market with no answer". This
module answers the question that starts the moment sla.py's stops: a market
HAS answered, the quote is good until a date, and the subjectivities attached
to it have to be cleared before that date or the terms go away. A senior AE
rated the gap between the two "the only one that loses money rather than
time" (ROADMAP, 2026-08-18).

THE DATE AND THE COUNTDOWN COME OFF THE SAME OBJECT. `expires_on` and
`days_remaining` are both properties reading `submission.quote_expires_on`,
so a surface cannot print one date beside a count taken from another — the
defect four independent reviewers found on the renewal countdown, where a
date twenty days in the FUTURE rendered as "70d over" on four surfaces
(CLAUDE.md). Expiry is decided by `days_remaining < 0`, the same rule
renewals use, never by which bucket a row lands in.

Expiring TODAY is not expired. A quote is live through the whole of its last
day; a broker who binds at 4pm on the expiry date has bound in time.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..dates import days_until
from ..models import Submission
from ..repo import submissions

# The attention model's one window, and it is not redefined here — see
# CLAUDE.md. 120 days, bucket-aligned, and nothing overdue ever falls off.
ATTENTION_DAYS = 120

# Below this a quote is being lost rather than worked, so the surfaces say so
# in a word as well as a colour. Two weeks is the shortest turnaround a
# client decision realistically has.
URGENT_DAYS = 14


# --- the one classification, so both surfaces say the same words -------------
#
# The TUI renders through tui/theme.expiry_text and the web through the Jinja
# template; neither may import the other (tests/test_conventions.py), so the
# WORDS live here, in the layer both already depend on. A quote reading
# "expired" in the terminal and "overdue" in the browser would be two
# vocabularies for one fact.

EXPIRED = "expired"
URGENT = "urgent"
LIVE = "live"
UNDATED = "undated"


def expiry_state(days: int | None) -> str:
    """Which of the four states a countdown is in.

    days == 0 is URGENT, never EXPIRED: a quote is good for the whole of its
    last day, and a broker binding at 4pm on the expiry date has bound in
    time. Expiry is `days < 0`, the same rule renewals use for overdue."""
    if days is None:
        return UNDATED
    if days < 0:
        return EXPIRED
    if days <= URGENT_DAYS:
        return URGENT
    return LIVE


def expiry_word(days: int | None) -> str:
    """The phrase a reader sees, in every state. Never a bare number: the
    point of the field is that nobody should have to subtract dates in their
    head to find out the terms have gone away."""
    if days is None:
        return "no expiry"
    if days < 0:
        return f"expired {-days}d ago"
    if days == 0:
        return "expires today"
    if days <= URGENT_DAYS:
        return f"{days}d left"
    return f"{days}d"


@dataclass(frozen=True)
class QuoteItem:
    """One quote in hand, with everything a chase needs on it."""

    submission: Submission
    market_name: str
    org_id: str
    org_name: str
    about: str
    underwriter_name: str | None
    underwriter_email: str | None
    open_subjectivities: int
    total_subjectivities: int
    today: date

    @property
    def expires_on(self) -> str | None:
        """The stored expiry, straight off the submission. Never a second
        date computed nearby — see the module docstring."""
        return self.submission.quote_expires_on

    @property
    def days_remaining(self) -> int | None:
        """Days to `expires_on`, counted to the date this object prints.
        None when the market gave no expiry, which is not the same as zero."""
        if self.expires_on is None:
            return None
        return days_until(self.expires_on, self.today)

    @property
    def is_expired(self) -> bool:
        """Strictly past. Expiring today is NOT expired: a quote is good for
        the whole of its last day."""
        days = self.days_remaining
        return days is not None and days < 0

    @property
    def is_urgent(self) -> bool:
        """Live, but close enough that it is the next thing to do."""
        return self.expiry_state == URGENT

    @property
    def expiry_state(self) -> str:
        return expiry_state(self.days_remaining)

    @property
    def expiry_word(self) -> str:
        return expiry_word(self.days_remaining)


def _item(row: sqlite3.Row, today: date, org_id: str, org_name: str) -> QuoteItem:
    first = row["uw_first"]
    last = row["uw_last"]
    name = " ".join(p for p in (first, last) if p) or None
    # the joined row carries market/account/underwriter/count columns too, so
    # the submission is rebuilt from ITS columns only — the same idiom
    # services/rfi.outstanding_requests uses, and the reason Row models forbid
    # extras rather than absorbing whatever a JOIN happened to select
    fields = {k: row[k] for k in row.keys() if k in Submission.model_fields}
    return QuoteItem(
        submission=Submission.model_validate(fields),
        market_name=row["market_name"],
        org_id=org_id,
        org_name=org_name,
        about=row["about"] or "",
        underwriter_name=name,
        underwriter_email=row["uw_email"],
        open_subjectivities=int(row["open_subjectivities"] or 0),
        total_subjectivities=int(row["total_subjectivities"] or 0),
        today=today,
    )


def expiring(
    conn: sqlite3.Connection, today: date | None = None, days: int = ATTENTION_DAYS
) -> list[QuoteItem]:
    """Every quote in hand whose expiry falls inside the window — or is
    already past, because an overdue item never falls off (CLAUDE.md).

    Soonest first, so a lapsed quote leads and the one lapsing on Friday
    follows it. Quotes with no recorded expiry are absent from THIS list on
    purpose — they are not on a clock and no date is invented for them — but
    they are not therefore invisible: `undated` below is the tail the same
    surfaces render beside this queue.
    """
    today = today or date.today()
    horizon = (today + timedelta(days=days)).isoformat()
    return [
        _item(row, today, row["org_id"], row["org_name"] or "")
        for row in submissions.expiring_quote_rows(conn, horizon)
    ]


def undated(conn: sqlite3.Connection, today: date | None = None) -> list[QuoteItem]:
    """Every quote in hand across the book that nobody gave an expiry.

    The tail on `expiring`, and it exists because refusing to INVENT a date
    and refusing to SHOW the item are two different decisions. This module
    makes the first and not the second: no date is guessed here, the surfaces
    render `expiry_word(None)` — "no expiry" — where a countdown would go,
    and the work the reader is being handed is "go and ask the underwriter
    when this dies", which is a real, doable thing.

    A dated quote outside the window is absent too, but it arrives on its own
    when its date comes round. An undated one never arrives, so it is exactly
    the case that needs carrying rather than the one that can be left.
    """
    today = today or date.today()
    return [
        _item(row, today, row["org_id"], row["org_name"] or "")
        for row in submissions.undated_quote_rows(conn)
    ]


def for_org(
    conn: sqlite3.Connection, org_id: str, org_name: str = "", today: date | None = None
) -> list[QuoteItem]:
    """Every quote in hand for one client, window or no window.

    The deliberate twin of `expiring` above, and the difference is the point:
    that one is the book-wide chase queue; this one backs the account's own
    surfaces, where a quote with no expiry recorded yet is still a quote you
    are holding and still has to be visible.
    """
    today = today or date.today()
    return [
        _item(row, today, org_id, org_name)
        for row in submissions.quoted_rows_for_org(conn, org_id)
    ]
