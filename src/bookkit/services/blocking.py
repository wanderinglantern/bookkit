"""WHAT IS BLOCKING THIS PLACEMENT — market conditions and client asks, in one
list, ordered by what runs out first.

Until 2026-08-27 this question had no single answer. A market's subjectivity
sat under its package on the Marketing tab; the ask that would answer it sat
under the requests panel; and the only thing holding the two together was the
broker. Chasing these IS the three weeks between a quote arriving and a policy
being bound, which is the stretch the tool tracked in two halves.

THE PLACEMENT IS THE SPINE (Grant, 2026-08-27), so this composes for one
placement and nothing wider. `/items` is the cross-book queue and stays what it
is; a third blocking source there is a separate decision Grant deferred until
he has used this for a week.

IT OWNS NO WRITES. Every row here is rendered with the controls of the surface
that already owns it — a subjectivity's own cells on the Marketing tab, an
item's own cells on the requests panel — for the reason routes/items.py gives
about itself: those routes answer with the cell alone and are therefore correct
on any page that renders them. One parser, one guard, one batch, one refusal
path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ..dates import days_until
from ..models import SUBJECTIVITY_OPEN_STATUS, RfiItem, RfiRequest, Subjectivity
from ..repo import orgs as orgs_repo
from ..repo import rfi as rfi_repo
from ..repo import submissions as submissions_repo
from . import rfi as rfi_svc

# The two kinds of blocker, as the surface names them. They are NOT merged into
# one word: a market requires a CONDITION and we have asked the client a
# QUESTION, and a broker reading the list needs to know which door to knock on.
CONDITION = "condition"
ASK = "ask"


@dataclass(frozen=True)
class Blocker:
    """One thing standing between this placement and a bind."""

    kind: str
    id: str
    what: str
    # WHO IS WAITING ON WHOM. A condition names the market that requires it; an
    # ask names the client. Never blank — a row that cannot say whose move it
    # is cannot be worked.
    who: str
    due_on: str | None
    # NONE MEANS UNDATED, never zero. An undated blocker is not due today and
    # is not overdue — it is UNMEASURED, and a 0 here would render as "0d" and
    # read as the most urgent row on the page. Every surface prints the house
    # em dash instead. The same distinction `RenewalItem` draws between a
    # countdown and a blank.
    days_remaining: int | None
    # WHERE THE ROW LIVES, so the list can send a reader to the surface that
    # owns the write rather than growing controls of its own.
    href: str
    # --- what makes this list worth having ---------------------------------
    # THE ASK A CONDITION IS WAITING ON, where one has been made. None means
    # nobody has asked the client for this yet, which is the state the "ask the
    # client" control exists to change.
    asked_as: str | None = None
    # THE ANSWER IS IN HAND but the market has not been told. This is the state
    # the whole feature exists to make visible: no longer waiting on the
    # client, still outstanding to the market, and invisible before today.
    answer_in_hand: bool = False
    # HOW MANY MARKETS ONE ASK IS CARRYING. Printed on the ask so the sentence
    # "3 markets are waiting on this" appears where a broker is deciding
    # whether to chase it.
    carries: int = 0


def _subjectivity_row(
    conn: sqlite3.Connection,
    subjectivity: Subjectivity,
    *,
    market: str,
    ref: str,
    placement_id: str,
    today: date,
    item: RfiItem | None,
    request: RfiRequest | None,
) -> Blocker:
    # BOTH DATES ARE KEPT AND THE EARLIER ONE IS SHOWN (Grant, 2026-08-27:
    # "Agree w/ your recommendation"). The market's deadline and the date we
    # asked the client to hit are different facts, and collapsing them loses
    # the one that actually binds. Same shape as `rfi.effective_due` between an
    # item and its request, one level down.
    dates = [d for d in (subjectivity.due_on, _item_due(item, request)) if d]
    due = min(dates) if dates else None
    return Blocker(
        kind=CONDITION,
        id=subjectivity.id,
        what=subjectivity.description,
        who=market,
        due_on=due,
        days_remaining=days_until(due, today) if due else None,
        href=f"/accounts/{ref}/marketing#{placement_id}",
        asked_as=item.prompt if item else None,
        # RECEIVED IS NOT MET. The item being answered does not mark the
        # condition met — it says the document is here and the market has not
        # been told, which is a different row in the list and a different act.
        answer_in_hand=bool(item and item.status == "received"),
    )


def _item_due(item: RfiItem | None, request: RfiRequest | None) -> str | None:
    if item is None or request is None:
        return None
    return rfi_svc.effective_due(item, request)


def for_placement(
    conn: sqlite3.Connection, placement_id: str, ref: str, today: date
) -> list[Blocker]:
    """Everything blocking this placement, soonest first.

    ORDERED BY WHAT RUNS OUT FIRST, with undated last — the same rule every
    attention surface in this book follows, and the reason it is a rule is that
    an undated blocker is not less urgent, it is unmeasured, and floating it to
    the top on a null would bury the dated ones.

    A CONDITION ALREADY MET IS NOT BLOCKING and is not here. An ASK already
    received still is, when a market is waiting to be told — that is the
    `answer_in_hand` row, and dropping it would hide the one piece of work this
    whole feature was built to surface.
    """
    market_names = _market_names(conn, placement_id)
    requests = {
        r.id: r
        for r in rfi_repo.requests_for_org(conn, _org_id(conn, placement_id))
        if r.placement_id == placement_id and not r.cancelled_at
    }
    items = {
        item.id: item
        for request in requests.values()
        for item in rfi_repo.items_for_request(conn, request.id)
    }

    out: list[Blocker] = []
    for row in submissions_repo.subjectivity_rows_for_placement(conn, placement_id):
        # THE ROW IS A JOIN, not a table row: it carries the market that asked
        # and the lines that package answered on, which the model does not have
        # and must not gain (a subjectivity hangs off the PACKAGE, so its lines
        # are the package's). Same narrowing `RfiChase` does over
        # `outstanding_rows` one module across.
        subjectivity = Subjectivity.model_validate(
            {k: row[k] for k in row.keys() if k in Subjectivity.model_fields}
        )
        if subjectivity.status != SUBJECTIVITY_OPEN_STATUS:
            continue
        item = items.get(subjectivity.rfi_item_id or "")
        out.append(
            _subjectivity_row(
                conn, subjectivity,
                market=market_names.get(row["market_org_id"] or "", "a market"),
                ref=ref, placement_id=placement_id, today=today,
                item=item,
                request=requests.get(item.request_id) if item else None,
            )
        )

    for item in items.values():
        if item.status != "outstanding":
            continue
        request = requests[item.request_id]
        due = rfi_svc.effective_due(item, request)
        out.append(
            Blocker(
                kind=ASK,
                id=item.id,
                what=item.prompt,
                who="the client",
                due_on=due,
                days_remaining=days_until(due, today) if due else None,
                href=f"/accounts/{ref}/requests/{request.id}",
                # AN ASK THAT NO MARKET ASKED FOR IS STILL AN ASK. Most RFIs
                # are submission prep, made before a single market saw the
                # risk, and they belong in this list on their own account —
                # `carries` is 0 and the surface simply does not print the
                # "N markets waiting" line.
                carries=len(rfi_svc.unblocked_by(conn, item.id)),
            )
        )

    # UNDATED LAST, then soonest first. `days_remaining` is already the house
    # answer for "how long have I got" and is negative when overdue, so sorting
    # on it puts the overdue at the top without a second rule about lateness.
    out.sort(
        key=lambda b: (
            b.due_on is None,
            b.days_remaining if b.days_remaining is not None else 0,
            b.what.casefold(),
        )
    )
    return out


def _org_id(conn: sqlite3.Connection, placement_id: str) -> str:
    from ..repo import placements

    return placements.get(conn, placement_id).org_id


def _market_names(conn: sqlite3.Connection, placement_id: str) -> dict[str, str]:
    """Every market named on this placement, in ONE read.

    A query per printed row is the shape this book has been bitten by before
    (marketing_report._block says the same thing about the same lookup), and a
    blocking list on a well-marketed renewal is thirty rows.
    """
    ids = {
        row["market_org_id"]
        for row in submissions_repo.subjectivity_rows_for_placement(
            conn, placement_id
        )
        if row["market_org_id"]
    }
    return {
        org_id: label.name for org_id, label in orgs_repo.labels_for(conn, ids).items()
    }
