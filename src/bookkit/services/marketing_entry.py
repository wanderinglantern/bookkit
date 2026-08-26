"""Recording an approach: who we went to, on which line of coverage, through
whom.

ONE HOME FOR ONE RULE. The rule is THE SUBMISSION IS THE PACKAGE AND THE
RESPONSE IS THE ANSWER: one submission goes to one market carrying every line,
and the response rows hang off it one per line — so an approach REUSES the live
submission already out to that market on this placement ON THAT DAY, and
creates one only when there is none. Otherwise one email becomes three
submissions and "who did we approach" stops being answerable, which is the
shape migration 015's header refuses.

THE DAY IS PART OF THE PACKAGE. Reuse keyed on the market alone threw away the
`sent_on` the caller typed, so the header printed a date nobody entered; going
back to a market a week later is a second approach and a second email, and it
gets a submission of its own.

It lived inside mcpserver's `market_approach` until the web grew an add-market
row (2026-08-25). A second copy in a route would have been the copy that
quietly differs — and the way it would have differed is the worst one
available: the web would have opened a fresh submission per line, so the
assistant and the browser would disagree about how many markets a placement had
been out to.

The batch is NOT opened here. Each surface stamps its own source ('mcp' /
'web') and its own sentence, and `db.transaction` nests by joining, so the
caller's batch is the undo unit either way.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..models import MarketResponse, Submission
from ..repo import marketing
from ..repo import submissions as submissions_repo
from . import consistency

# We pulled that package. Hanging a new approach off it would leave the row
# permanently mis-stated, because repo.marketing.roll_up_submission never
# writes over `withdrawn` and never un-withdraws one. Going back to a market we
# withdrew from is a NEW submission, which is also what happened in the world.
_WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class Approach:
    response: MarketResponse
    submission: Submission
    # Whether this approach OPENED the package or joined one already out.
    # Both callers report it: it is the difference between "we sent Chubb a
    # submission" and "we asked Chubb about one more line".
    submission_is_new: bool


def _status_of(submission: Submission) -> str:
    """`submission.status` is a str on some reads and an enum on others; the
    withdrawn check must not depend on which."""
    status = submission.status
    return str(getattr(status, "value", status))


def approach(
    conn: sqlite3.Connection,
    placement_id: str,
    line_id: str,
    *,
    sent_on: str,
    market_org_id: str | None = None,
    via_org_id: str | None = None,
    today: str | None = None,
    **fields: Any,
) -> Approach:
    """Record an approach and return the row it created.

    The submission is addressed to the INTERMEDIARY where there is one: a
    package sent to RT Specialty went to RT Specialty, whatever paper they come
    back with. `submission.market_org_id` is NOT NULL, so this is not a
    preference — it is the only truthful value available at that moment.

    Clearance is NOT checked here and must not be: the double approach is
    sometimes deliberate and a hard block would make a legitimate entry
    impossible (the `line-gap` rule again). Callers report
    `repo.marketing.clearance_conflicts` afterwards, as a warning.
    """
    # A SUBMISSION DOES NOT GO OUT NEXT YEAR. `sent_on` is typed with no upper
    # bound on both surfaces, so one wrong year is one keystroke — and the
    # consequence is not cosmetic: `_reply_guard` then refuses every reply to
    # that row, forever. Refused HERE because this is the one home both the
    # web add row and MCP's `market_approach` share, and because `today` can
    # be a parameter here (a wall clock in repo/ cannot know the caller's).
    consistency.check_not_future(
        sent_on, label="a submission sent", today=today or date.today().isoformat()
    )
    addressed = via_org_id or market_org_id
    if addressed is None:
        # repo.marketing.create_response holds this too, and the DB CHECK holds
        # it under that. Raising here as well is what lets the sentence name
        # the intermediary as the way out BEFORE a submission has been created
        # that would then be orphaned by the refusal.
        raise ValueError(
            "an approach needs a carrier or an intermediary — if the wholesaler "
            "has not named the paper yet, record the intermediary alone and "
            "fill the carrier in when they come back with it"
        )
    # A DIFFERENT SENT DATE IS A DIFFERENT SUBMISSION (2026-08-26).
    #
    # The reuse rule is about LINES, not about time: one email to one market
    # carries every line, so a second line joins the package already out. It
    # was matching on the market alone, which meant the `sent` date the caller
    # typed was DROPPED whenever any live package existed — silently, with no
    # message and nothing on the row to show for it. The block header then
    # printed a submission date the broker never entered, and `_reply_guard`
    # refused every reply before it while naming a correction that, on the day
    # this was found, no surface could make.
    #
    # Going back to a market on ANOTHER DAY is a second approach and it is a
    # second package in the world — the same reading that makes a withdrawn
    # submission un-reusable below. Same date, same package: the ordinary case
    # (three lines entered in one sitting) still opens one submission, which is
    # what `test_an_approach_joins_the_live_submission_rather_than_opening_a_
    # second` holds.
    existing = next(
        (
            sub
            for sub in submissions_repo.for_placement(conn, placement_id)
            if sub.market_org_id == addressed
            and _status_of(sub) != _WITHDRAWN
            and sub.sent_on == sent_on
        ),
        None,
    )
    submission = existing or submissions_repo.create(
        conn, market_org_id=addressed, sent_on=sent_on, placement_id=placement_id
    )
    if market_org_id is not None:
        fields["market_org_id"] = market_org_id
    if via_org_id is not None:
        fields["via_org_id"] = via_org_id
    response = marketing.create_response(conn, submission.id, line_id, **fields)
    return Approach(
        response=response, submission=submission, submission_is_new=existing is None
    )
