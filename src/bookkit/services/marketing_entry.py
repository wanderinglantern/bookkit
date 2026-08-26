"""Recording an approach and what came back: who we went to, on which line of
coverage, through whom — and what they then said.

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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..models import MarketResponse, MarketType, Submission
from ..repo import marketing, orgs
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


# THE PACKAGE'S STATUS, READ AS WHAT ONE MARKET SAID. Two vocabularies, and
# they are two on purpose (models.SUBMISSION_STATUS_LABELS): a submission is a
# summary of its rows and a response is one market's answer about one line.
#
# EVERY ROW OF THIS TABLE ROUND-TRIPS. `roll_up_submission` recomputes the
# parent from the single response an assignment creates, and each mapping below
# is chosen so that the status it derives is the status the submission already
# had — assigning a line does not silently restate what the Pipeline says about
# the package. `out` → `pending` is the one worth naming: `pending` means asked
# and nothing back yet, which is exactly `out`, while `non_response` is a
# JUDGMENT somebody makes about a market that went quiet and is never a state a
# row falls into on its own (models.MARKET_RESPONSE_STATUSES).
#
# `withdrawn` is deliberately absent and `assign_line` refuses on it: pulling a
# package is a decision about the submission, the roll-up never writes over it,
# and a response hung off it would have a parent that can never be recomputed
# from it — the same permanently-mis-stated row this module already refuses to
# create when it declines to reuse a withdrawn submission for a new approach.
_AS_RESPONSE_STATUS: dict[str, str] = {
    "out": "pending",
    "quoted": "quoted",
    "declined": "declined",
    "bound": "bound",
}

# The five quote facts the submission carries, and the `market_response` column
# each becomes. A DICT rather than five keyword arguments, because it is the
# same list `repo.marketing._rolled_figures` derives back out of the rows and
# the two must be read side by side.
_CARRIED: dict[str, str] = {
    "quoted_premium": "premium",
    "quoted_limit": "lim",
    "response_on": "responded_on",
    "quote_expires_on": "quote_expires_on",
    "decline_reason": "decline_reason",
}


# THE MARKET TYPES THAT PLACE SOMEBODY ELSE'S PAPER. A package addressed to
# one of these has no carrier yet, and "out to RT Specialty, carrier TBD" is
# the truth rather than a gap. A carrier, a reinsurer and a Lloyd's syndicate
# all ARE the paper. Read from models.MarketType so the vocabulary has one home.
_INTERMEDIARY_TYPES = frozenset({MarketType.WHOLESALER, MarketType.MGA})


def who_was_asked(
    conn: sqlite3.Connection, submission: Submission
) -> dict[str, str | None]:
    """WHOSE PAPER, OR WHO CARRIED THE PACKAGE — for a line being answered for
    the first time.

    `submission.market_org_id` is who the package was ADDRESSED to, and where
    there is a wholesaler in the chain that is the WHOLESALER: `approach` above
    says so in as many words ("a package sent to RT Specialty went to RT
    Specialty, whatever paper they come back with"). Recording it as
    `market_org_id` here would put RT Specialty in the Market column of a
    CLIENT's workbook as the carrier — the shape `_one_market_twice` was
    written for, arriving from the other side.

    The book already knows which it is: another line on this same package
    reaching that org as `via_org_id` is the book saying it is the
    intermediary. Then the new row is "out to RT Specialty, carrier TBD" — the
    truth, and what `ReportRow.market_cell` prints in those words — and naming
    the paper is one cell on the Marketing panel. No carrier is guessed either
    way.

    HERE rather than in `forms/entities.py`, where it was written: two writers
    now create the first response on a line — the Pipeline's Response form and
    `assign_line` below — and a rule about who a package went to belongs where
    both inherit it rather than beside whichever one happened to need it first.
    """
    asked = submission.market_org_id
    carried = any(
        r.via_org_id == asked
        for r in marketing.responses_for_submission(conn, submission.id)
    )
    if not carried:
        # AND THE BOOK'S OWN RECORD OF WHAT THAT MARKET IS. The check above can
        # only speak once a line on this package has already been answered
        # through the org — which is never true of the FIRST line answered, and
        # never true at all of `assign_line`, whose whole precondition is a
        # package with no responses. A wholesaler would then have been recorded
        # as the carrier every single time this rule was most needed.
        #
        # `market_profile.market_type` is a fact somebody RECORDED, not a
        # guess: a wholesaler and an MGA both place other people's paper, and a
        # package addressed to one has no carrier yet. Where the book says
        # nothing about the market, nothing is inferred and the addressee is
        # the carrier — which is the ordinary case and the truthful reading of
        # a direct approach.
        profile = orgs.get_market_profile(conn, asked)
        carried = bool(profile and profile.market_type in _INTERMEDIARY_TYPES)
    return {"via_org_id": asked} if carried else {"market_org_id": asked}


def start_marketing(
    conn: sqlite3.Connection, placement_id: str | None, line_id: str
) -> None:
    """THIS PLACEMENT IS MARKETING THAT LINE OF COVERAGE — said once, here.

    A bare `placement_line` row is the declaration and nothing more: no
    expiring premium, no exposure, no basis, no rate. Every one of those comes
    off a document and none of them is guessed (data-entry-integrity §8), so
    this writes the row and stops — which is exactly what the Marketing
    panel's `+ line of coverage` control writes when it opens a block
    (`web/routes/marketing.py::_add_line`).

    SAFE TO SAY TWICE, and NOT by a check of its own: `set_placement_line`
    updates an existing row only when it is given fields, and this gives it
    none — so a second declaration writes nothing and logs nothing. A
    re-check here would be a second copy of the lookup that writer already
    does on its first line, and the copy that eventually differs.

    NO BATCH IS OPENED HERE, for the reason this module's header gives: the
    caller's batch is the undo unit, and `db.transaction` nests by joining —
    so the declaration and the answer that made it are reverted together or
    not at all.

    `placement_id` is OPTIONAL and None is a no-op, not an error: a submission
    hung off an OPPORTUNITY has no placement to declare anything about, and
    the caller should not have to branch on that to call this.
    """
    if placement_id is None:
        return
    marketing.set_placement_line(conn, placement_id, line_id)


def carried_figures(
    submission: Submission, restated: Iterable[str] = ()
) -> dict[str, Any]:
    """THE FIGURES THE PACKAGE ALREADY RECORDED, as the columns of the response
    row that will state them from now on.

    ONE RULE, TWO DOORS. `assign_line` below and the Pipeline's Response form
    (`forms.entities.apply_response`) are the two ways a submission gets its
    FIRST response row, and they are the same act — "this is the line that
    $1.4M quote is for". Only one of them carried the figures across: recording
    a corrected reply date through the Pipeline moved the answer onto a row,
    the roll-up made the rows the authority for all six columns, and the
    premium and the limit the submission had been carrying went to NULL because
    the rows said nothing about them (r6 A/B, 2026-08-26). The panel's door lost
    nothing on the same submission, which is what makes it a defect rather than
    a cost: one act, two doors, two outcomes.

    THIS IS NOT PRE-FILLING A FIGURE OFF A DOCUMENT (data-entry-integrity §8).
    Nothing is invented and nothing is shown to a person to be waved through —
    the figure is ALREADY IN THE BOOK, typed by a broker into the submission's
    own columns, and this moves it from the column to the row that is about to
    become its only home. The form still shows every amount empty.

    NULL STAYS NULL. A figure nobody entered must not arrive on the response as
    a zero, so the walk is over the values that are actually set — `NULL` is
    "nobody has told us" and 0 is "there is none", and only 0 reaches a total.

    `restated` is what the caller has just been TOLD, and it wins: an answer
    that states a premium is the current fact about that line, and carrying the
    package's older figure over the top of it would be this function inventing
    a disagreement. Keys are `market_response` column names, the same spelling
    `_CARRIED`'s values use, so the two cannot drift.
    """
    keep = set(restated)
    return {
        column: getattr(submission, key)
        for key, column in _CARRIED.items()
        if column not in keep and getattr(submission, key) is not None
    }


def assign_line(
    conn: sqlite3.Connection, submission_id: str, line_id: str
) -> MarketResponse:
    """Give a package the LINE OF COVERAGE nobody recorded, carrying its own
    stored figures onto the row that will state them from now on.

    A SUBMISSION WITH NO RESPONSE ROWS IS REAL MARKETING THAT HAPPENED, and
    until this exists there is nothing a broker can do about it: the marketing
    report shows it in its provisional block, and the block's whole purpose is
    to be emptied one row at a time. Fourteen seeded placements are in that
    state, and every one of Grant's own (2026-08-26).

    NOTHING IS INVENTED. Only the six facts the submission itself recorded move
    — status, premium, limit, reply date, expiry, decline reason — and the line
    of coverage is the one thing the caller supplies, because it is the one
    thing that is not in the data. NULL stays NULL: a figure nobody entered
    must not arrive on the response as a zero (`_CARRIED` is walked over the
    values that are actually set).

    IT IS A NO-OP DRESSED AS A WRITE, and that is the test worth having. Every
    status maps to one that rolls back up to itself, the premium is the only
    priced response so it sums to itself, the reply date is the only one so it
    is the max of itself, and the expiry the min — so the Pipeline reads
    exactly what it read before. THE ONE EXCEPTION, stated rather than
    discovered: `quoted_limit` is cached only from a submission with exactly
    one PRICED response (`repo.marketing._rolled_figures` says why the limit
    and the premium must come off one answer), so a limit recorded with no
    premium beside it drops out of the cache and is read from the row that
    states it — on the Marketing panel, where it now prints.

    REFUSED ON A WITHDRAWN PACKAGE. See `_AS_RESPONSE_STATUS`. The surface does
    not offer the control on those rows at all, which is why this refusal names
    no fix beyond the fact: there is nothing the reader is being asked to
    correct, and nothing on any surface can un-withdraw a package.

    The batch is NOT opened here, for the reason the module header gives: each
    surface stamps its own source and its own sentence.
    """
    submission = submissions_repo.get(conn, submission_id)
    status = _status_of(submission)
    if status == _WITHDRAWN:
        raise ValueError(
            "this package was withdrawn, so it does not take a line of "
            "coverage — what it recorded stays reported where it is, and going "
            "back to that market is a NEW approach, recorded on the line you "
            "want with the add-market row in that line's grid"
        )
    if marketing.responses_for_submission(conn, submission_id):
        # THE ROWS ARE ALREADY THE AUTHORITY. Once one response exists this
        # package is reported under the lines it answered and its columns are a
        # cache of them (`roll_up_submission`) — assigning a line here would
        # copy a cache back onto a second row and re-open the very second home
        # the roll-up closed. The panel's add-market row is where another line
        # is added to a package already answered, and it is on the same screen.
        raise ValueError(
            "this package already has an answer recorded against a line of "
            "coverage — add another line to it with the add-market row in "
            "that line's grid, which records what the market said there"
        )
    return marketing.create_response(
        conn,
        submission_id,
        line_id,
        status=_AS_RESPONSE_STATUS[status],
        **who_was_asked(conn, submission),
        **carried_figures(submission),
    )


# A DATE THAT WITNESSES AN ACT CANNOT BE IN THE FUTURE, and every one of them
# on a market response is named here rather than checked at whichever door
# somebody remembered.
#
# `sent_on` gained this guard on 2026-08-25 (see `approach` above) and
# `responded_on` — the cell one column to the RIGHT of it on the same row —
# did not, which is this book's recurring shape: the rule applied at one site
# and not at the adjacent one. `parse_human_date` FUTURE-BIASES a bare month
# and day, so a reply typed "aug 5" on 14 August 2026 stored 2027-08-05 and
# was accepted in silence; the client's workbook then printed "5 Aug 2027" as
# the day a market answered (D2, found 2026-08-26). `_reply_guard`'s own
# refusal already says "check the year on the reply" — the book knew this was
# the failure mode and had no guard for it.
#
# A DICT, not a branch, because the walk is the point: a second date added to
# `MARKET_RESPONSE_FIELDS` is either declared here or reported by
# tests/test_marketing_gates.py's date gate, which reads this table and the
# Field tuple and refuses to let them differ.
WITNESS_DATES: dict[str, str] = {
    "responded_on": "a market's reply dated",
}


def responded(
    conn: sqlite3.Connection,
    response_id: str,
    changes: dict[str, Any],
    *,
    today: str | None = None,
) -> MarketResponse:
    """Record what a market said, from either surface.

    ONE HOME, the same reading `approach` is placed by: the web's response
    cell and MCP's `market_responded` both land here, so a rule stated once
    binds both — and a rule stated in one route is a rule the other writes
    past. `repo.marketing.edit_response` stays the writer (it owns the reply
    guard, the status vocabulary, the rate stamp and the submission roll-up);
    what lives HERE is the one rule repo/ cannot hold, because it needs a
    today and a wall clock in repo/ cannot know the caller's
    (repo.submissions._sent_guard says exactly this about the same field on
    the other table).

    The batch is NOT opened here, for the reason the module header gives:
    each surface stamps its own source and its own sentence.
    """
    when = today or date.today().isoformat()
    for key, label in WITNESS_DATES.items():
        if key in changes:
            consistency.check_not_future(changes[key], label=label, today=when)
    return marketing.edit_response(conn, response_id, changes)


# --- pulling a package, and putting it back ---------------------------------
#
# WITHDRAWING IS A DECISION ABOUT THE SUBMISSION, and that is why it needs a
# control of its own rather than an option on a response picker. What a market
# SAID is `market_response.status`; that we PULLED the package is not something
# any market said, which is exactly why `repo.marketing.roll_up_submission`
# never writes `withdrawn` and never writes over it.
#
# It had no home at all between 2026-08-26 and this. The Pipeline's Response
# form used to offer the SUBMISSION statuses, so its outcome picker was the one
# writer of `withdrawn` on any surface; re-pointing that form at
# `market_response` correctly gave it the response vocabulary, which has no
# such word — and left three code paths (`assign_line`'s refusal, `approach`'s
# reuse rule, `roll_up_submission`'s guard) refusing on a state nobody could
# enter (r6 blocker 2). A status that can be read and reasoned about but never
# written is a rule with no subject.


def withdraw(conn: sqlite3.Connection, submission_id: str) -> Submission:
    """WE PULLED THIS PACKAGE. One column, on the submission, in the caller's
    batch.

    IT IS REVERSIBLE, which is why it does not have to be refused into a
    corner: `reinstate` below puts it back, on every surface this is on, and
    the account's own change list reverts the batch. The web still asks first
    — nothing about the act is visible on the row afterwards, because a
    withdrawn package leaves the "out at market" queue entirely — and the
    confirm's job is to say where it goes and how it comes back rather than to
    stand in for an undo that exists.

    WHAT IT DOES NOT DO is touch the response rows or the figures. Marketing
    that happened stays reported: the panel keeps printing what each market
    said, the workbook keeps carrying it, and the quote figures stay where
    they are. Withdrawing says we stopped pursuing the package, not that it
    never went out — deleting is a different verb this book deliberately does
    not have (`mcpparity`, market_response/delete).

    Refused on a package already withdrawn, because the alternative is a
    control that reports success for a write it did not make — and the fix it
    names is the one control that changes anything from there.
    """
    submission = submissions_repo.get(conn, submission_id)
    if _status_of(submission) == _WITHDRAWN:
        raise ValueError(
            "this package was already withdrawn — nothing to pull. Put it back "
            "at market with Reinstate on the Pipeline tab (or submission_reinstate)"
        )
    return submissions_repo.update(conn, submission_id, status=_WITHDRAWN)


def reinstate(conn: sqlite3.Connection, submission_id: str) -> Submission:
    """PUT A WITHDRAWN PACKAGE BACK AT MARKET, at whatever its rows say it is.

    NOT 'out' BY DEFAULT. A package pulled while two markets were quoting comes
    back QUOTED — the status is derived from the response rows the way every
    other package's is (`repo.marketing.status_from_rows`, the one home for
    that ladder), and a flat 'out' would tell the Pipeline no answer had
    arrived on a package holding two. A package with no rows comes back 'out',
    which is what 'out' means: asked, nothing back yet.

    THE ROLL-UP CANNOT DO THIS. It refuses to speak about a withdrawn row at
    all — deliberately, so that editing a stale response cannot quietly
    un-withdraw a package — so the un-withdrawing is this function's, and the
    figures are then recomputed by the roll-up on the row it has just made
    speakable. Both writes are in the caller's batch, so Revert takes them
    together.

    Refused on a package that is not withdrawn: there is nothing to put back,
    and saying so is better than a no-op that reports success.
    """
    submission = submissions_repo.get(conn, submission_id)
    if _status_of(submission) != _WITHDRAWN:
        raise ValueError(
            "this package is not withdrawn, so there is nothing to put back — "
            "what a market said is corrected on its own row, in the Marketing "
            "section of the Program tab (or with market_responded)"
        )
    rows = marketing.responses_for_submission(conn, submission_id)
    submissions_repo.update(
        conn, submission_id, status=marketing.status_from_rows(rows)
    )
    # AND THE FIVE FIGURES BESIDE IT. They were frozen at whatever they held
    # when the package was pulled, because the roll-up has been declining to
    # write them ever since; now that the row speaks again they are derived
    # from the rows like any other package's. A no-op where nothing moved.
    marketing.roll_up_submission(conn, submission_id)
    return submissions_repo.get(conn, submission_id)
