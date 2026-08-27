"""Information-request rules. A request's open/closed state is DERIVED here
and stored nowhere: it is open while any item is outstanding. The chase feed
follows the house attention rule — a 120-day window, and nothing overdue
ever falls off."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..dates import days_until
from ..models import (
    SUBJECTIVITY_OPEN_STATUS,
    RfiItem,
    RfiRequest,
    Subjectivity,
)
from ..repo import orgs, placements
from ..repo import projects as projects_repo
from ..repo import rfi as rfi_repo
from ..repo import submissions as submissions_repo

# What asker_name returns when there is no market name to show. The TUI dims
# these and leaves a real name plain, so the set is part of the contract.
ASKER_PLACEHOLDERS = frozenset({"—", "(merged market)"})


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


@dataclass(frozen=True)
class Removal:
    """What a removal did, so the caller can say it rather than guess."""

    request_id: str
    title: str
    org_id: str
    items: int
    batch: str


def _answered(items: list[RfiItem]) -> list[RfiItem]:
    """The items a client has already acted on. Either half counts: a response
    is what they told us, and `received` is our record that they sent it."""
    return [i for i in items if i.response or i.status == "received" or i.received_on]


def check_removable(conn: sqlite3.Connection, request_id: str) -> None:
    """Raise if this request may not be removed. Returns nothing when it may.

    Extracted from `remove_request` so a caller can ask BEFORE it starts — the
    program cascade (services/program_remove.py) needs to decline in the
    confirm rather than half-way through a batch, and the alternative was a
    second copy of "what counts as answered", which is the copy that quietly
    differs. One rule, one home; `remove_request` still calls it, so the two
    cannot disagree about a single request.
    """
    from ..repo import base

    row = base.raw_row(conn, "rfi_request", request_id)
    if row is None:
        raise ValueError(
            f"no information request {request_id!r} — read the account's "
            f"requests for exact ids"
        )
    if row["deleted_at"]:
        raise ValueError(f"{row['ref']} was already removed on {row['deleted_at'][:10]}")

    request = rfi_repo.get_request(conn, request_id)
    answered = _answered(rfi_repo.items_for_request(conn, request_id))
    if answered:
        named = ", ".join(f"{i.prompt[:40]!r}" for i in answered[:3])
        raise ValueError(
            f"{request.ref} has {len(answered)} answered item(s) — {named}. "
            f"Deleting the question deletes the client's answer with it. Set "
            f"cancelled_at to withdraw the request instead, and keep the record."
        )


def remove_request(
    conn: sqlite3.Connection, request_id: str, *, source: str
) -> Removal:
    """Take an information request off the book, with its items, as ONE
    revertible batch.

    FILED IN ERROR, not withdrawn — the two are different facts and get
    different verbs. A request WITHDRAWN was a real ask we have since dropped;
    it stays in the book with `cancelled_at` set, and the client's copy can
    still explain why it stopped. A request filed IN ERROR was never true, and
    leaving a withdrawn ghost of it says something about the account that did
    not happen. This is the second one. (Grant hit exactly this on 2026-08-19:
    an MCP call filed an RFI he never asked for, and no surface anywhere could
    take it back — `rfi_repo.delete_request` had been sitting there with no
    caller since the feature shipped.)

    ITS ITEMS GO WITH IT. An item is reachable only through its request, so an
    item left behind is unreachable rather than preserved — and the batch puts
    both back together, because a request restored without its items is a
    heading with nothing under it.

    REFUSED once anybody has answered. An answered ask is history: the client
    told us something, and deleting the question deletes their answer with it.
    The refusal names the alternative rather than just saying no.

    `source` is the surface: 'mcp' | 'tui' | 'web'.
    """
    from ..services import batches as batches_svc

    check_removable(conn, request_id)
    request = rfi_repo.get_request(conn, request_id)
    items = rfi_repo.items_for_request(conn, request_id)

    with batches_svc.open_batch(
        conn, source=source, tool="request_remove", org_id=request.org_id,
        summary=f"removed information request {request.ref}: {request.title}",
    ) as batch:
        for item in items:
            rfi_repo.delete_item(conn, item.id)
        rfi_repo.delete_request(conn, request_id)

    return Removal(
        request_id=request_id, title=request.title, org_id=request.org_id,
        items=len(items), batch=batch.ref,
    )


@dataclass(frozen=True)
class ItemRemoval:
    item_id: str
    prompt: str
    request_id: str
    batch: str
    # HOW MANY MARKET CONDITIONS WERE WAITING ON THIS ASK. The caller says it
    # out loud, because removing an item silently un-answers three markets is
    # exactly the kind of consequence a confirm exists to state.
    unlinked: int = 0


def remove_item(conn: sqlite3.Connection, item_id: str, *, source: str) -> ItemRemoval:
    """Take ONE ask off a request — a line filed in error, not a line answered.

    The request survives even when this was its last item: a request with no
    items is an ask not yet written down (`is_open` says so below), which is
    not the same as a withdrawn one, and silently withdrawing it here would
    make the two indistinguishable.
    """
    from ..repo import base
    from ..services import batches as batches_svc

    row = base.raw_row(conn, "rfi_item", item_id)
    if row is None:
        raise ValueError(f"no request item {item_id!r} — read the request for exact ids")
    if row["deleted_at"]:
        raise ValueError(f"that item was already removed on {row['deleted_at'][:10]}")

    item = rfi_repo.get_item(conn, item_id)
    if _answered([item]):
        raise ValueError(
            f"{item.prompt[:40]!r} has been answered — deleting it deletes the "
            f"client's answer. Waive it instead if it is no longer needed."
        )
    request = rfi_repo.get_request(conn, item.request_id)

    with batches_svc.open_batch(
        conn, source=source, tool="request_item_remove", org_id=request.org_id,
        summary=f"removed an item from {request.ref}: {item.prompt[:60]}",
    ) as batch:
        # AN ASK THAT GOES AWAY TAKES ITS LINKS WITH IT, in the same batch.
        # Both tables are soft-deleted, so the link would still RESOLVE — a
        # market's condition would go on reading "asked 19 Aug" against an ask
        # nobody can see or answer. Inside the batch so `u` puts the pair back
        # together rather than restoring an item nothing points at.
        unlinked = submissions_repo.unlink_rfi_item(conn, item_id)
        rfi_repo.delete_item(conn, item_id)

    return ItemRemoval(
        item_id=item_id, prompt=item.prompt, request_id=item.request_id,
        batch=batch.ref, unlinked=len(unlinked),
    )


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


def scope_label(conn: sqlite3.Connection, request: RfiRequest) -> str:
    """What a request is about, for one column: the placement's ref, the
    project's name, or an em dash for an account-level ask (onboarding).

    One helper, two screens — the resolution rule is not duplicated."""
    if request.placement_id:
        try:
            return placements.get(conn, request.placement_id).ref
        except KeyError:
            return "(deleted placement)"
    if request.project_id:
        try:
            return projects_repo.get_project(conn, request.project_id).name
        except KeyError:
            return "(deleted project)"
    return "—"


def asker_name(conn: sqlite3.Connection, request: RfiRequest) -> str:
    """Who to chase for a response: the market's name, an em dash for an
    internal ask (onboarding) that names no market, or "(merged market)" when
    the market was merged away underneath the request.

    One helper, three surfaces (the chase queue, the account tab, the client
    sheet) — the resolution rule is not duplicated. Callers that style their
    output dim the ASKER_PLACEHOLDERS and leave a real name plain."""
    if not request.market_org_id:
        return "—"
    try:
        return orgs.get(conn, request.market_org_id).name
    except KeyError:
        return "(merged market)"


def effective_due(item: RfiItem, request: RfiRequest) -> str | None:
    """When an item is actually needed: its own due date, falling back to its
    request's. One rule, used by the queue, the tab, and the sheet.

    None means neither side set one — an undated ask, which still has to
    appear everywhere (no date window drops it)."""
    return item.due_on or request.due_on


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
    """d on an item: received, dated.

    This is TWO field writes (status and received_on), so base.update logs
    two events and a single `u` reverts only the later one — leaving the item
    received with received_on NULL. That matches tasks_repo.complete, so it is
    parity rather than a regression, but nothing here should claim otherwise."""
    return rfi_repo.update_item(conn, item_id, status="received", received_on=on)


# --- a market's condition, and the ask that will answer it --------------------
#
# ONE ASK, THREE MARKETS (Grant, 2026-08-27). A subjectivity is something a
# market requires before its quote is bindable; an RFI item is something we have
# asked the client for. They are the same shape — the Subjectivity model says so
# itself — and until migration 021 nothing joined them, so three markets wanting
# five-year loss runs meant three asks to one client.
#
# THE JOIN IS MANY-TO-ONE and everything below follows from that. Promotion
# ATTACHES a condition to an ask; it is not a copy, not a merge, and not
# automatic. Each half keeps its own vocabulary because they describe different
# events: a document is RECEIVED, a condition is MET.


@dataclass(frozen=True)
class Candidate:
    """One ask that might already answer a market's condition, ranked.

    `state` is the item's own status, and it is the field that decides which
    candidate a broker picks — "received 19 Aug" and "outstanding, due 2 Sep"
    mean completely different things about what is left to do:

      outstanding  no new ask; this market joins a queue already out
      received     nothing to chase at all — the document is in hand and
                   needs forwarding
      waived       offered, never defaulted; it was waived for a reason that
                   may not survive a different market asking
    """

    item: RfiItem
    request: RfiRequest
    score: float
    state: str
    already_waiting: int


# HOW CLOSE IS CLOSE ENOUGH TO BE WORTH OFFERING. Below this the candidate is
# noise in a picker, not a suggestion. It is a FLOOR ON THE LIST and never a
# threshold to act on: nothing in this module attaches anything on a score.
_WORTH_OFFERING = 55.0


def candidates(
    conn: sqlite3.Connection, subjectivity_id: str, limit: int = 8
) -> list[Candidate]:
    """The asks already on this placement that might answer this condition.

    THE MATCH IS THE FEATURE. Without it, promotion is just a second place to
    type the same sentence, and the broker asks the client three times anyway
    because they cannot see that they already asked. The useful question when a
    condition lands is not "shall I ask the client" but "HAVE I?".

    RANKED, NEVER SELECTED. The order is a suggestion and the picker opens on a
    blank option (the house rule for every select, and it matters more here than
    anywhere): a wrong attach does not fail loudly — it tells a broker that a
    market's condition is answered by a document that does not answer it, and
    that surfaces at the bind, which is the worst possible moment. Nothing in
    this function or its callers may attach on a score.

    SAME PLACEMENT IS A HARD FILTER, not a weight (Grant, 2026-08-27: "Agree
    same placement"). An ask satisfied on last year's renewal is a real document
    we already hold AND a stale one nobody should forward, and which of the two
    it is depends on the document rather than on a rule — so it is not offered
    at all. An ask on a different client is never a candidate however well the
    words match.

    The ranking, in order: token-set ratio on the words, then a bonus where the
    item's `category` matches, then recency as the tie-break. Token-set is the
    ratio that survives reordering and extra words, which is exactly the shape
    of the real data — "5-year loss runs", "Loss runs, 5 yrs" and "currently
    valued loss runs" are one ask to a broker and three strings to a database.
    """
    from rapidfuzz import fuzz

    subjectivity = submissions_repo.get_subjectivity(conn, subjectivity_id)
    submission = submissions_repo.get(conn, subjectivity.submission_id)
    if submission.placement_id is None:
        # NO PLACEMENT, NO HARD FILTER, SO NO CANDIDATES. An opportunity's
        # marketing has no renewal to scope an ask to, and widening the filter
        # to "this client" would be the prior-term problem wearing a different
        # hat. Better to offer nothing than to offer the wrong year.
        return []

    org_id = placements.get(conn, submission.placement_id).org_id
    out: list[Candidate] = []
    for request in rfi_repo.requests_for_org(conn, org_id):
        # THE HARD FILTER, applied before anything is scored. Same client is
        # not enough: an ask on last year's renewal is a different year's
        # document.
        if request.placement_id != submission.placement_id or request.cancelled_at:
            continue
        for item in rfi_repo.items_for_request(conn, request.id):
            if item.id == subjectivity.rfi_item_id:
                continue  # already attached to this very one
            score = float(fuzz.token_set_ratio(subjectivity.description, item.prompt))
            if item.category and subjectivity.description:
                # A CATEGORY THAT AGREES IS EVIDENCE, not an answer. Small
                # enough that it re-ranks near-ties and never promotes a poor
                # word match over a good one.
                if item.category.casefold() in subjectivity.description.casefold():
                    score += 5.0
            if score < _WORTH_OFFERING:
                continue
            out.append(
                Candidate(
                    item=item,
                    request=request,
                    score=score,
                    state=item.status,
                    already_waiting=len(
                        submissions_repo.subjectivities_waiting_on(conn, item.id)
                    ),
                )
            )
    # RECENCY IS THE TIE-BREAK, and it breaks ties toward the LATER ask:
    # between two equally good matches the newer one is the live conversation.
    out.sort(key=lambda c: (-c.score, _negated(c.item.created_at)))
    return out[:limit]


def _negated(created_at: str) -> tuple[int, ...]:
    """Sort a timestamp string descending inside an otherwise ascending key."""
    return tuple(-ord(ch) for ch in created_at)


@dataclass(frozen=True)
class Promotion:
    """What promoting did, so the caller can say it rather than guess."""

    subjectivity_id: str
    item_id: str
    request_ref: str
    prompt: str
    state: str
    created_item: bool
    also_waiting: int
    batch: str


def promote(
    conn: sqlite3.Connection,
    subjectivity_id: str,
    *,
    source: str,
    item_id: str | None = None,
    prompt: str | None = None,
    due_on: str | None = None,
) -> Promotion:
    """ASK THE CLIENT for what a market requires — once, however many markets
    require it.

    Either ATTACH to an ask already on this placement (`item_id`) or make a new
    one (`prompt`). Exactly one of the two, because they are different acts and
    a caller that supplies both has not decided which it meant.

    ATTACHING IS THE DEFAULT PATH and the picker is ordered to make it so. The
    duplication worth removing is the one the CLIENT experiences, and a flow
    where making a second ask is as easy as reusing the first ends up with three
    asks for one document — which is the thing this exists to stop.

    A NEW ITEM LANDS ON A REQUEST FOR THIS PLACEMENT, made if there is not one
    yet. The request is the envelope — one email — so a condition promoted on
    Tuesday joins the ask that went out on Monday rather than opening a second
    conversation with the same client about the same renewal.

    NOTHING IS COPIED BACK. The subjectivity keeps its own description, its own
    due date and its own status: the market's deadline and the date we asked the
    client to hit are two different facts, and `blocking.py` shows the earlier
    of them rather than collapsing them (the same rule `effective_due` states
    one level down).

    REFUSED when the condition is already met or waived — there is nothing left
    to ask for, and an ask filed against a satisfied condition is a chase the
    client did not need.
    """
    from ..models import SUBJECTIVITY_OPEN_STATUS
    from ..services import batches as batches_svc

    if (item_id is None) == (prompt is None):
        raise ValueError(
            "say either which ask to attach this to, or the wording of a new "
            "one — not both and not neither"
        )

    subjectivity = submissions_repo.get_subjectivity(conn, subjectivity_id)
    if subjectivity.status != SUBJECTIVITY_OPEN_STATUS:
        raise ValueError(
            f"that condition is already {subjectivity.status} — there is "
            f"nothing left to ask the client for. Re-open it first if the "
            f"market has come back on it."
        )
    submission = submissions_repo.get(conn, subjectivity.submission_id)
    if submission.placement_id is None:
        raise ValueError(
            "this package is not on a placement, so there is no renewal to "
            "scope the ask to — give the package a placement first"
        )
    placement = placements.get(conn, submission.placement_id)

    # A NEW ASK THAT IS ALREADY OUT IS THE ONE MISTAKE THIS FEATURE EXISTS TO
    # STOP (found in a browser, 2026-08-27, by making it). The picker offers the
    # asks already out and then offers a free-text box under them, and typing
    # into the box what the list above is already showing writes a SECOND email
    # to the client for one document — silently, with both rows then printing
    # in the Blocking list under the same wording.
    #
    # REFUSED, AND THE REFUSAL NAMES THE ASK. Not a silent attach: `candidates`
    # is a fuzzy score and nothing in this module may act on one (a wrong attach
    # says a market's condition is answered by a document that does not answer
    # it). What a score IS good enough for is stopping and pointing — the broker
    # attaches to the one named, or rewords, and both are one click from here.
    if prompt is not None:
        twin = _already_asked(conn, subjectivity.id, prompt)
        if twin is not None:
            raise ValueError(
                f"the client has already been asked this — {twin.prompt!r} on "
                f"this renewal. Attach this market to that ask instead of "
                f"sending a second one, or reword it if it is genuinely a "
                f"different question."
            )

    with batches_svc.open_batch(
        conn, source=source, tool="subjectivity_ask_client", org_id=placement.org_id,
        summary=(
            f"asked the client for {subjectivity.description[:60]}"
        ),
    ) as batch:
        created = False
        if item_id is None:
            request = _request_for_placement(
                conn, placement.org_id, submission.placement_id
            )
            assert prompt is not None
            item = rfi_repo.add_item(
                conn, request.id, prompt=prompt,
                **({"due_on": due_on} if due_on else {}),
            )
            created = True
        else:
            item = rfi_repo.get_item(conn, item_id)
            request = rfi_repo.get_request(conn, item.request_id)
            if request.placement_id != submission.placement_id:
                raise ValueError(
                    f"{request.ref} is not an ask on this placement — an ask "
                    f"from another renewal is a document from another year, "
                    f"and forwarding it is not the same as answering this"
                )
        submissions_repo.update_subjectivity(
            conn, subjectivity_id, "asked the client", rfi_item_id=item.id
        )

    return Promotion(
        subjectivity_id=subjectivity_id,
        item_id=item.id,
        request_ref=request.ref,
        prompt=item.prompt,
        state=item.status,
        created_item=created,
        # EVERY OTHER MARKET ALREADY WAITING ON THIS ASK, which is the figure
        # that makes the feature visible: "3 markets are waiting on this" is
        # the sentence that stops the fourth ask being written.
        also_waiting=len(
            submissions_repo.subjectivities_waiting_on(conn, item.id)
        ) - 1,
        batch=batch.ref,
    )


# HIGH ENOUGH TO BE THE SAME QUESTION. `_WORTH_OFFERING` (55) is the floor for
# putting a candidate in a LIST a human then reads; this is the bar for calling
# two asks the same thing without being asked, so it sits far above it. "Loss
# runs, 5 yrs" against "Loss runs — 5 years, currently valued" scores ~90 on
# token_set_ratio; two genuinely different document requests do not.
_SAME_QUESTION = 88.0


def _already_asked(
    conn: sqlite3.Connection, subjectivity_id: str, prompt: str
) -> RfiItem | None:
    """An ask on this placement that is, in words, the question being typed.

    Reuses `candidates` so the placement filter and the scoring are stated
    ONCE — a second walk here would be the copy that quietly differs, and the
    two are answering the same question about the same strings.
    """
    from rapidfuzz import fuzz

    for candidate in candidates(conn, subjectivity_id, limit=50):
        if fuzz.token_set_ratio(prompt, candidate.item.prompt) >= _SAME_QUESTION:
            return candidate.item
    return None


def _request_for_placement(
    conn: sqlite3.Connection, org_id: str, placement_id: str
) -> RfiRequest:
    """The open request this placement's asks belong on, made if there is none.

    ONE ENVELOPE PER RENEWAL. A request is an email; a condition promoted on
    Tuesday belongs in the ask that went out on Monday rather than opening a
    second conversation with the same client about the same renewal. Only an
    OPEN one is reused — a request whose items are all answered is a finished
    conversation, and re-opening it would make an answered ask look outstanding
    again.
    """
    for request in rfi_repo.requests_for_org(conn, org_id):
        if request.placement_id == placement_id and not request.cancelled_at:
            if is_open(conn, request.id):
                return request
    return rfi_repo.create_request(
        conn,
        org_id=org_id,
        placement_id=placement_id,
        title="Market subjectivities",
        requested_on=date.today().isoformat(),
        notes=(
            "Opened automatically to carry what the markets require. Conditions "
            "attached to these items came off market quotes; each is a "
            "condition of the bind, not a preference."
        ),
    )


def unlink(
    conn: sqlite3.Connection, subjectivity_id: str, *, source: str
) -> str:
    """Take one condition off the ask it was attached to.

    THE PAIR `promote` NEEDS. Attaching the wrong ask is the mistake this
    feature makes easiest — the picker is ranked by a fuzzy score, and the
    whole reason nothing attaches automatically is that a wrong attach reads as
    "answered" — so the way back has to exist and be one act. The ask itself is
    untouched: it may be answering three other markets, and un-asking a question
    the client has already been sent is not a thing this can do.
    """
    from ..services import batches as batches_svc

    subjectivity = submissions_repo.get_subjectivity(conn, subjectivity_id)
    if subjectivity.rfi_item_id is None:
        raise ValueError(
            "that condition is not attached to an ask, so there is nothing to "
            "take off it"
        )
    submission = submissions_repo.get(conn, subjectivity.submission_id)
    org_id = (
        placements.get(conn, submission.placement_id).org_id
        if submission.placement_id
        else None
    )

    with batches_svc.open_batch(
        conn, source=source, tool="subjectivity_unlink", org_id=org_id,
        summary=f"took {subjectivity.description[:60]} off the ask it was on",
    ) as batch:
        submissions_repo.update_subjectivity(
            conn, subjectivity_id, "took this off the ask", rfi_item_id=None
        )
    return str(batch.ref)


def unblocked_by(
    conn: sqlite3.Connection, item_id: str
) -> list[Subjectivity]:
    """The market conditions an arriving answer would clear — still outstanding.

    RECEIVED IS NOT MET, and this is the list that keeps the two apart. The
    client sending the loss runs does not satisfy AIG's condition; AIG having
    them and accepting them does. So an answer arriving SURFACES this list and
    offers to mark them met as one confirmed act — it never decides, because
    the market that has not actually got the file yet is a real case and the
    broker is the only one who knows which.
    """
    return [
        s
        for s in submissions_repo.subjectivities_waiting_on(conn, item_id)
        if s.status == SUBJECTIVITY_OPEN_STATUS
    ]


def mark_met(
    conn: sqlite3.Connection,
    subjectivity_ids: list[str],
    *,
    on: str,
    source: str,
    org_id: str | None = None,
) -> int:
    """Mark the conditions an answer satisfied, as ONE undo unit.

    The batch is the point: a broker confirming that an arriving document
    clears three markets did ONE thing, and `u` has to put all three back. A
    condition already met is skipped rather than refused — the confirm lists
    what it will do and the list can be stale by a click.
    """
    from ..services import batches as batches_svc

    done = 0
    with batches_svc.open_batch(
        conn, source=source, tool="request_item_received", org_id=org_id,
        summary=f"marked {len(subjectivity_ids)} market condition(s) met",
    ):
        for subjectivity_id in subjectivity_ids:
            subjectivity = submissions_repo.get_subjectivity(conn, subjectivity_id)
            if subjectivity.status != SUBJECTIVITY_OPEN_STATUS:
                continue
            submissions_repo.update_subjectivity(
                conn, subjectivity_id, "the client's answer arrived",
                status="met", satisfied_on=on,
            )
            done += 1
    return done


def unasked_on(
    conn: sqlite3.Connection, placement_id: str
) -> list[tuple[Subjectivity, str]]:
    """Market conditions on this placement that NOBODY HAS ASKED THE CLIENT
    for, each with the market that requires it.

    THE OTHER DIRECTION (Grant, 2026-08-27: "Yes. That makes sense"). `promote`
    starts from the condition and finds the ask; this starts from the ask and
    finds the conditions, because a broker writing an RFI item is just as
    likely to think "that covers what AIG wanted" as the other way round. Both
    doors, so neither becomes the one people learn to avoid.

    OUTSTANDING AND UNLINKED ONLY. A condition already attached is not offered
    — moving it belongs to `unlink` and then a fresh choice, so no picker can
    silently re-point a market's condition at a different document.
    """
    from ..repo import orgs as orgs_repo

    out: list[tuple[Subjectivity, str]] = []
    rows = submissions_repo.subjectivity_rows_for_placement(conn, placement_id)
    names = orgs_repo.names_for_any(
        conn, {str(r["market_org_id"] or "") for r in rows if r["market_org_id"]}
    )
    for row in rows:
        if row["status"] != SUBJECTIVITY_OPEN_STATUS or row["rfi_item_id"]:
            continue
        subjectivity = Subjectivity.model_validate(
            {k: row[k] for k in row.keys() if k in Subjectivity.model_fields}
        )
        out.append(
            (subjectivity, names.get(str(row["market_org_id"] or ""), "a market"))
        )
    return out


def attach(
    conn: sqlite3.Connection,
    item_id: str,
    subjectivity_ids: list[str],
    *,
    source: str,
    org_id: str | None = None,
) -> int:
    """Say that one ask answers these market conditions — the reverse of
    `promote`, and ONE undo unit over all of them.

    Each id goes through `promote`, so every rule holds identically whichever
    door was used: the same-placement filter, the refusal on a condition
    already met, the refusal on an ask from another renewal. A picker that
    wrote directly would be a second copy of those, which is the copy that
    quietly differs.
    """
    from ..services import batches as batches_svc

    done = 0
    with batches_svc.open_batch(
        conn, source=source, tool="subjectivity_ask_client", org_id=org_id,
        summary=f"said one ask answers {len(subjectivity_ids)} market condition(s)",
    ):
        for subjectivity_id in subjectivity_ids:
            promote(conn, subjectivity_id, source=source, item_id=item_id)
            done += 1
    return done
