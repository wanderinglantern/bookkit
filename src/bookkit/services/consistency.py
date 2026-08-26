"""Cross-field consistency — the rules where one field must relate correctly
to another.

Insurance data-quality practice sorts checks into completeness, conformance,
consistency and timeliness (see the data-entry-integrity skill). bookkit is
strong on conformance — forms.spec.parse_value owns types and bounds, and
checked_option makes a select's own options the authority — and strong on
timeliness, through the sha guard and the three-way conflict. CONSISTENCY was
the thin one, and an audit on 2026-08-20 found five pairs that saved happily
in disagreement:

* a placement whose period ends before it starts (the rule existed in
  sync.update_program for linked programs and in imports/mappers/book.py for
  spreadsheets, and was missing from the form — which is the door a human
  types through),
* a subjectivity marked met with no date, or left outstanding still carrying
  a satisfied date,
* a quote whose response predates the submission it answers, or whose expiry
  predates the response (a year typo puts a live quote straight into EXPIRED),
* a project whose end predates its start — which export_open_items prints
  into the CLIENT-FACING workbook as "HQ Tower (2026-01-01 → 2020-01-01)",
* an information request due before it was asked, which makes the chase queue
  report it overdue on the day it was created.

THE CHECKS LIVE HERE, NOT IN A DB CHECK CONSTRAINT. A constraint would be a
migration, and it would refuse to apply against any row in Grant's real book
that already violates it — turning a data-quality improvement into an upgrade
that cannot run. A service-layer rule leaves an existing bad row readable and
editable, and refuses only the next write that would make it worse.

THE CHECKS LIVE IN ONE MODULE, NOT AT EACH DOOR. forms/entities.py's apply_*
functions are where the TUI and the web both land; mcpserver's `_edit_field`,
`_project_create` and `_request_create` are where the assistant lands. Both
call these functions, so the rule has one definition and cannot drift by
surface — the same reason repo/team.py owns name uniqueness rather than
whichever caller remembered it (CLAUDE.md).

EVERY REFUSAL NAMES THE FIX, following forms/spec.py's `date_refusal`: the
offending value AND what would be accepted. "expiry is before inception" is
half a message.

The functions here are PURE — dates in, refusal out. No connection, no repo,
no SQL. The caller reads the row it is about to write and hands over the
values; that keeps the rules testable in isolation and keeps this module out
of the raw-SQL convention test's way.
"""

from __future__ import annotations

# ISO-8601 dates sort lexicographically, which is why every comparison below
# is a plain string compare rather than a date.fromisoformat round-trip. Every
# stored date reaches here through forms.spec.parse_value or mcpserver's
# _clean_typed, both of which emit `date.isoformat()`.


def order_refusal(
    *,
    earlier_label: str,
    earlier: str,
    later_label: str,
    later: str,
    strict: bool,
) -> str:
    """The one sentence every ordered-date refusal gives.

    One function so the wording cannot drift between the five pairs, and so
    the remedy is never left off: it names the offending value, the value it
    disagrees with, and BOTH ends of the fix — because which of the two dates
    is the typo is the caller's knowledge, not ours.
    """
    if strict:
        return (
            f"{later_label} {later} is not after {earlier_label} {earlier} — "
            f"enter a {later_label} later than {earlier}, or correct the "
            f"{earlier_label}"
        )
    return (
        f"{later_label} {later} is before {earlier_label} {earlier} — enter a "
        f"{later_label} on or after {earlier}, or correct the {earlier_label}"
    )


def check_order(
    earlier: str | None,
    later: str | None,
    *,
    earlier_label: str,
    later_label: str,
    strict: bool = False,
) -> None:
    """Refuse when `later` lands before `earlier`.

    A MISSING DATE IS NEVER A VIOLATION. Every optional date in the book means
    "not known yet" — a project with no end date, a request with no due date,
    a quote with no stated expiry — and an unknown cannot contradict anything.
    Refusing the pair when one half is blank would turn a normal working state
    into an unsaveable record, which is worse than the gap being closed.

    `strict` is the difference between "must not go backwards" and "must be a
    real span". Only the placement period is strict, and not by choice here:
    towerkit's own validator refuses `end <= start`, and imports/mappers/
    book.py refuses `period_to <= period_from`. A third spelling of the same
    rule that accepted a zero-day period would mean a program the form saved
    and the file refused.
    """
    if not earlier or not later:
        return
    if later > earlier or (later == earlier and not strict):
        return
    raise ValueError(
        order_refusal(
            earlier_label=earlier_label,
            earlier=earlier,
            later_label=later_label,
            later=later,
            strict=strict,
        )
    )


def check_not_future(value: str | None, *, label: str, today: str) -> None:
    """Refuse a date that records something as having already happened when it
    has not.

    NOT EVERY DATE IS PAST-ONLY, and this is deliberately not applied to any
    that are not: a task is due next week, a quote expires next month, a policy
    period runs a year out. The shape this is for is a date that WITNESSES an
    act — the day a submission went to the market — where "next year" is not a
    plan, it is a typo, and where nothing downstream will ever object.

    `submission.sent_on` is the one the book has been hurt by. It is typed with
    no upper bound, so 2027 for 2026 is one keystroke, and the consequence is
    not cosmetic: `repo.marketing._reply_guard` refuses every reply dated
    before a submission went out, so a send date in the future makes the
    Replied cell on that row unanswerable — permanently, and with a refusal
    naming a correction (found 2026-08-26).

    `today` IS A PARAMETER, never `date.today()` read in here. Half this
    book's rendering takes today from its caller for exactly this reason, and
    a rule that reads the wall clock cannot be tested against a book whose own
    world is a different year.
    """
    if not value or value <= today:
        return
    raise ValueError(
        f"{label} {value} has not happened yet — today is {today}. Enter the "
        f"date it actually happened; check the year if this was meant to be a "
        f"date in the past."
    )


# --- the five pairs -----------------------------------------------------------


def check_placement_period(period_from: str | None, period_to: str | None) -> None:
    """A program period must be a real span: expiry strictly after inception.

    Equal dates are refused, unlike every other pair here, because a zero-day
    policy period is not a thing anyone means to type and because towerkit
    already refuses it — a placement the form accepted and the file rejected
    would be unlinkable and unrepairable from the form that made it.
    """
    check_order(
        period_from,
        period_to,
        earlier_label="period from",
        later_label="period to",
        strict=True,
    )


def check_project_dates(start_on: str | None, end_on: str | None) -> None:
    """Equal dates are LEGAL: a one-day site job, a single-day event.

    This pair reaches a client. services/export_open_items.py prints
    "HQ Tower (start → end)" into the workbook that goes out, so a reversed
    pair is not an internal untidiness — it is a document with a nonsense date
    range on it, sent under Grant's name.
    """
    check_order(start_on, end_on, earlier_label="start", later_label="end")


def check_request_dates(requested_on: str | None, due_on: str | None) -> None:
    """Equal dates are LEGAL and common: "asked this morning, need it today".

    A due date before the ask makes services/rfi's chase queue report the
    request overdue from the moment it is created, so the queue that exists to
    say what to chase opens with a row nobody can act on.
    """
    check_order(requested_on, due_on, earlier_label="asked on", later_label="response due")


def check_item_due(requested_on: str | None, due_on: str | None) -> None:
    """An item's own deadline against the date its REQUEST was asked.

    The effective deadline the chase queue prints is `item.due_on or
    request.due_on`, so an item-level date is exactly as capable of opening
    life overdue as the request-level one, and the request's own guard does
    not cover it.
    """
    check_order(
        requested_on, due_on, earlier_label="request asked on", later_label="needed by"
    )


def check_submission_dates(
    sent_on: str | None,
    response_on: str | None = None,
    quote_expires_on: str | None = None,
) -> None:
    """sent_on <= response_on <= quote_expires_on, on whichever pairs exist.

    Equal dates are LEGAL throughout. A market answering the day it is asked
    is the ordinary case on a small account, and a quote that names its own
    arrival day as the last day to bind is a real (if unwelcome) thing to be
    told.

    An expiry in the past relative to TODAY is also legal and must stay so —
    quotes lapse, and recording the lapse is the whole point of the field.
    Only the relationship between the three is checked here.

    When there is no response date, the expiry is compared against `sent_on`
    instead of being let through: the failure this catches is a year typed
    from last year's diary, and that is just as wrong on a submission whose
    response date has not been filled in.
    """
    check_order(sent_on, response_on, earlier_label="sent", later_label="response date")
    check_order(
        response_on or sent_on,
        quote_expires_on,
        earlier_label="response date" if response_on else "sent",
        later_label="quote expires",
    )


# --- status/date pairs --------------------------------------------------------


def settlement_date(
    status: str,
    supplied: str | None,
    existing: str | None,
    *,
    settled_status: str,
    date_label: str,
    today: str,
) -> str | None:
    """The value a "when was this settled" column must hold, given the status
    that owns it. Returns the date to write — never raises for the ordinary
    corrections, only for a live contradiction.

    Two chaseable line items have this shape: rfi_item (status/received_on)
    and subjectivity (status/satisfied_on). The first was explicitly coupled
    in apply_rfi_item and the second was not, so `met` with no date and
    `outstanding` still carrying a satisfied date both saved — and the
    datasheet then printed a date in the "satisfied" column of a row it also
    printed as outstanding. The rule was already written down (mcpsurface.py:
    "settling a subjectivity moves status and satisfied_on together, which is
    a transition rather than a field edit"); it just had no code.

    Three outcomes, and the middle one is the reason this is a function rather
    than an assertion:

    * settled, no date given — stamp `today`, keeping any date already
      recorded so back-dating survives an unrelated edit.
    * not settled, a LEFTOVER date — clear it. Putting an item back to
      outstanding is a normal correction, and the stale date is a consequence
      of it, not a second mistake to shout about. Refusing here would mean the
      only way back from a mis-marked item is to clear two fields in the right
      order.
    * not settled, a date typed NOW that differs from what was stored — refuse
      and name both fixes. This is the case the leftover rule must not
      swallow: the user is actively asserting two things that cannot both be
      true, and silently discarding what they typed loses the input without
      saying so.
    """
    if status == settled_status:
        return supplied or existing or today
    if supplied and supplied != existing:
        raise ValueError(
            f"{date_label} {supplied} does not go with status {status!r} — set "
            f"status to {settled_status!r} to keep the date, or clear "
            f"{date_label!r}"
        )
    return None
