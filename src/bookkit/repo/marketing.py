"""Marketing: what each market said, line by line, and what each line is
expected to do.

The report this feeds is client-facing, so every rule here is about not
printing something the book cannot stand behind. Two carry most of the
weight: `submission.status` is DERIVED from its response rows rather than
typed a second time, and a rate comparison is refused whenever the two sides
were rated on different bases.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import (
    MARKET_RESPONSE_OPEN_STATUSES,
    MARKET_RESPONSE_STATUSES,
    RATE_PER_LABELS,
    MarketResponse,
    PlacementLine,
    rating_basis,
)
from ..money import format_rate_micros
from . import base

_RESPONSE = "market_response"
_PLACEMENT_LINE = "placement_line"


# --- responses -------------------------------------------------------------


def _validate_status(status: str) -> str:
    if status not in MARKET_RESPONSE_STATUSES:
        raise ValueError(
            f"unknown market response status {status!r} — "
            f"one of {', '.join(MARKET_RESPONSE_STATUSES)}"
        )
    return status


def _sent_on(conn: sqlite3.Connection, submission_id: str) -> str | None:
    row = conn.execute(
        f"SELECT sent_on FROM submission WHERE id = ? AND {base.alive()}",
        (submission_id,),
    ).fetchone()
    return str(row["sent_on"]) if row and row["sent_on"] else None


def _reply_guard(
    conn: sqlite3.Connection, submission_id: str, fields: dict[str, Any]
) -> None:
    """A MARKET CANNOT ANSWER A PACKAGE THAT HAD NOT GONE OUT YET.

    CONSISTENCY IS THE THIN CATEGORY (CLAUDE.md): conformance is well covered
    on this row — every date parses, every status is in the vocabulary — and
    the cross-field rules mostly are not. `responded_on` before the
    submission's `sent_on` is the one that matters here, because the Replied
    date is now typed into a cell on the Program tab and a mistyped year is
    the ordinary way to produce it: "12 Aug 2025" for a submission sent in
    2026 reads as an answer that arrived a year early.

    NOT A DB CHECK, for the reason CLAUDE.md gives: a CHECK is a migration and
    refuses to apply against rows already violating it. IN repo/ rather than a
    service because this is where every surface meets for this write — the web
    cell posts to `edit_response` and mcpserver's `market_responded` calls the
    same function, so a rule in a service beside one of them would be a rule
    the other writes straight past (repo/team.py's duplicate guard, again).
    """
    replied = fields.get("responded_on")
    if not replied:
        return
    sent = _sent_on(conn, submission_id)
    if sent is None or str(replied) >= sent:
        return
    raise ValueError(
        f"the reply is dated {replied} and the submission did not go out until "
        f"{sent} — a market cannot answer a package it has not been sent. "
        f"Check the year on the reply, or correct the date the submission went "
        f"out if that is the one that is wrong."
    )


# THE SENTENCE A RATE WITH NO DENOMINATOR IS REFUSED IN, stated once and
# raised from both sites that can be handed one — the response's rate and the
# line's expiring rate. It was the exposure cell's guard that existed and the
# rate cell's that did not, which is this book's recurring shape: the rule
# applied at one site and not at the one three inches away (D4, 2026-08-26).
# One constant so the two refusals cannot drift into two different remedies
# for one rule.
_NO_DENOMINATOR = (
    "a rate needs the denominator it is quoted against, and this line of "
    "coverage has none — set `rate per` on the line first, then enter the "
    "rate. 1.42 per $100 is ten times 1.42 per $1,000, and nothing inside the "
    "figure says which one it is."
)


def _stamp_rate_per(
    conn: sqlite3.Connection,
    submission_id: str,
    line_id: str,
    fields: dict[str, Any],
) -> None:
    """A RATE IS STORED WITH THE DENOMINATOR IT WAS TYPED AGAINST.

    `market_response.rate_per` is the column that says what a stored
    `rate_micros` is per, and nothing was ever writing it: every rate on the
    grid inherited the LINE's denominator, live, at read time. So the door
    `_rate_per_guard` deliberately leaves open — clear the expiring rate, move
    the picker, enter the rate again — silently re-read every rate already
    quoted on that line by a factor of ten, and the Rate Δ column printed an
    88% reduction nobody achieved on the CLIENT's workbook (2026-08-26).

    Stamped rather than refused, for the reason `_rate_per_guard` gives for
    not refusing here: correcting a denominator must stay possible without
    first clearing every quote on the line. Once the rate carries its own
    denominator, `marketing_report._rate_move` can SEE that the two sides
    disagree and say so in words instead of dividing them.

    HERE, not in a route: this is where the web cell, the add row and MCP's
    `market_responded` all meet (the reason `_reply_guard` is here). A rate
    arriving WITH its own `rate_per` is left alone — the caller stated both
    halves in one act. A rate being CLEARED takes its denominator with it,
    because a denominator with no rate under it marks nothing.
    """
    if "rate_micros" not in fields or "rate_per" in fields:
        return
    if fields["rate_micros"] is None:
        fields["rate_per"] = None
        return
    row = conn.execute(
        "SELECT pl.rate_per FROM placement_line pl"
        " JOIN submission s ON s.placement_id = pl.placement_id"
        f" WHERE s.id = ? AND pl.line_id = ? AND {base.alive('pl')}",
        (submission_id, line_id),
    ).fetchone()
    if row is None or row["rate_per"] is None:
        # REFUSED, NOT STORED BARE. There is nothing to stamp, so the rate
        # would land as a figure with no unit anywhere in the book — and
        # setting the line's denominator afterwards would silently CLAIM it,
        # because every reader inherits the line's. The exposure cell one
        # column over has refused exactly this since the day it shipped ("42
        # power units and $0.42 are the same digits"); a rate is the same
        # sentence with a different unit.
        raise ValueError(_NO_DENOMINATOR)
    fields["rate_per"] = int(row["rate_per"])


def _one_market_twice(market_org_id: Any, via_org_id: Any) -> None:
    """PAPER IS NOT REACHED THROUGH ITSELF.

    `market_org_id` is whose paper it is and `via_org_id` is who carried the
    package there; naming the same org as both says Chubb wholesaled a Chubb
    submission to Chubb. It is not an exotic typo — a broker unsure whether a
    name is the carrier or the wholesaler fills in both, and the route only
    ever checked that AT LEAST ONE was given (found 2026-08-26). What it
    printed on the CLIENT's workbook was `Chubb (via Chubb)`, out of
    `ReportRow.market_cell`, whose own contract has no case for it.

    HERE, not in the route: the web add row, MCP's `market_approach` and any
    later importer all land on this writer, and a guard on what two ids
    together MEAN belongs where every surface inherits it — the same reading
    `_reply_guard` and `_basis_guard` are placed by.

    The refusal names BOTH fixes, because which of the two the broker meant is
    their knowledge and not ours.
    """
    if market_org_id and via_org_id and market_org_id == via_org_id:
        raise ValueError(
            "the carrier and the intermediary are the same market — paper is "
            "not reached through itself. Give the carrier alone if the "
            "submission went direct, or name the wholesaler that carried it."
        )


def create_response(
    conn: sqlite3.Connection,
    submission_id: str,
    line_id: str,
    **fields: Any,
) -> MarketResponse:
    """Record an approach. At least one of `market_org_id` / `via_org_id` must
    be given — a submission out to a wholesaler whose carrier is not yet known
    is a real row, a submission out to nobody is not. The DB CHECK holds it
    too; this raises the sentence a person can read."""
    if not (fields.get("market_org_id") or fields.get("via_org_id")):
        raise ValueError(
            "a market response needs a carrier or an intermediary — "
            "if the wholesaler has not named the paper yet, give via_org_id alone"
        )
    _one_market_twice(fields.get("market_org_id"), fields.get("via_org_id"))
    _validate_status(str(fields.get("status", "pending")))
    _reply_guard(conn, submission_id, fields)
    _stamp_rate_per(conn, submission_id, line_id, fields)
    response_id = base.insert(
        conn, _RESPONSE, {"submission_id": submission_id, "line_id": line_id, **fields}
    )
    roll_up_submission(conn, submission_id)
    return get_response(conn, response_id)


def get_response(conn: sqlite3.Connection, response_id: str) -> MarketResponse:
    row = base.get(conn, _RESPONSE, response_id)
    if row is None:
        raise KeyError(f"market response {response_id} not found")
    return MarketResponse.from_row(row)


def edit_response(
    conn: sqlite3.Connection, response_id: str, changes: dict[str, Any]
) -> MarketResponse:
    if "status" in changes:
        _validate_status(str(changes["status"]))
    if "responded_on" in changes:
        _reply_guard(conn, get_response(conn, response_id).submission_id, changes)
    if "rate_micros" in changes:
        existing = get_response(conn, response_id)
        _stamp_rate_per(conn, existing.submission_id, existing.line_id, changes)
    if "market_org_id" in changes or "via_org_id" in changes:
        # THE OTHER HALF IS THE STORED ONE. `market_approach` deliberately
        # allows an approach through a wholesaler whose paper is not named yet
        # and tells the assistant to "fill the carrier in later with
        # market_responded" — which is the one call that can name the carrier
        # as the org already recorded as the intermediary, without either
        # value being wrong on its own.
        was = get_response(conn, response_id)
        _one_market_twice(
            changes.get("market_org_id", was.market_org_id),
            changes.get("via_org_id", was.via_org_id),
        )
    base.update(conn, _RESPONSE, response_id, changes)
    response = get_response(conn, response_id)
    roll_up_submission(conn, response.submission_id)
    return response


def responses_for_submission(
    conn: sqlite3.Connection, submission_id: str
) -> list[MarketResponse]:
    rows = conn.execute(
        f"SELECT * FROM market_response WHERE submission_id = ? AND {base.alive()}"
        " ORDER BY line_id, attach, id",
        (submission_id,),
    ).fetchall()
    return [MarketResponse.from_row(r) for r in rows]


def responses_for_placement(
    conn: sqlite3.Connection, placement_id: str
) -> list[MarketResponse]:
    """Every response across every submission on this placement — the grid's
    whole population, in one query rather than one per submission."""
    rows = conn.execute(
        "SELECT r.* FROM market_response r"
        " JOIN submission s ON s.id = r.submission_id"
        f" WHERE s.placement_id = ? AND {base.alive('r')} AND {base.alive('s')}"
        " ORDER BY r.line_id, r.attach, r.id",
        (placement_id,),
    ).fetchall()
    return [MarketResponse.from_row(r) for r in rows]


# --- the submission's status is a roll-up, not a second opinion ------------

_WITHDRAWN = "withdrawn"


def roll_up_submission(conn: sqlite3.Connection, submission_id: str) -> str | None:
    """Recompute `submission.status` from its response rows.

    TWO HAND-MAINTAINED COPIES OF ONE FACT DISAGREE, and then nobody knows
    which is right — so the submission's status is derived here after every
    response write rather than typed on its own form.

    `withdrawn` is NEVER written by this function and never overwritten by
    it: withdrawing is a decision about the SUBMISSION (we pulled it), not a
    summary of what markets said back, and a roll-up that clobbered it would
    quietly un-withdraw a submission the moment a stale response was edited."""
    row = conn.execute(
        f"SELECT status FROM submission WHERE id = ? AND {base.alive()}",
        (submission_id,),
    ).fetchone()
    if row is None or row["status"] == _WITHDRAWN:
        return None
    statuses = {r.status for r in responses_for_submission(conn, submission_id)}
    if not statuses:
        return None
    if "bound" in statuses:
        rolled = "bound"
    elif "quoted" in statuses or "indicated" in statuses:
        rolled = "quoted"
    elif statuses <= {"declined", "non_response"}:
        rolled = "declined"
    else:
        rolled = "out"
    base.update(conn, "submission", submission_id, {"status": rolled}, note="roll-up")
    return rolled


# --- clearance -------------------------------------------------------------


def clearance_conflicts(
    conn: sqlite3.Connection, response: MarketResponse
) -> list[MarketResponse]:
    """Other LIVE approaches reaching the same carrier, on the same line of
    the same placement, through a DIFFERENT intermediary.

    This is the collision that gets one of the two shut out at the carrier,
    and the book can only see it because both orgs are recorded. Reported,
    never refused — the double approach is sometimes deliberate, and a hard
    block would make a legitimate entry impossible. Same rule as `line-gap`.

    A response with no carrier yet cannot collide with anything: nobody knows
    which underwriter it will land on.

    AND NEITHER CAN A DEAD ONE. The other rows were always filtered to the
    OPEN statuses; the row DOING the asking never was, so declining the
    duplicate — the very act that resolves the conflict, and what the panel's
    `_BLOCK_CELLS` comment says the block answer exists to show — left the
    warning standing on the row that had just been withdrawn from the fight:
    one live approach remaining, and a strip still saying the carrier was
    being reached twice (found 2026-08-26). A clearance conflict is two LIVE
    approaches, and "live" has to mean the same thing on both sides of the
    comparison or the count is of one thing and one other thing.

    HERE and not in the composer, because MCP's `market_approach` reads the
    same function to write its `clearance_warnings` — a fix beside one caller
    is the copy that quietly differs."""
    if response.market_org_id is None:
        return []
    if response.status not in MARKET_RESPONSE_OPEN_STATUSES:
        return []
    rows = conn.execute(
        "SELECT r.* FROM market_response r"
        " JOIN submission s ON s.id = r.submission_id"
        " WHERE s.placement_id = ("
        "     SELECT placement_id FROM submission WHERE id = ?"
        " ) AND r.market_org_id = ? AND r.line_id = ? AND r.id != ?"
        f" AND {base.alive('r')} AND {base.alive('s')}"
        f" AND r.status IN ({','.join('?' * len(MARKET_RESPONSE_OPEN_STATUSES))})",
        (
            response.submission_id,
            response.market_org_id,
            response.line_id,
            response.id,
            *MARKET_RESPONSE_OPEN_STATUSES,
        ),
    ).fetchall()
    others = [MarketResponse.from_row(r) for r in rows]
    # Same carrier through the SAME intermediary is one approach recorded
    # twice (two layers of one tower, say), not a clearance problem.
    return [o for o in others if o.via_org_id != response.via_org_id]


# --- what a line is expected to do ----------------------------------------


def placement_lines(conn: sqlite3.Connection, placement_id: str) -> list[PlacementLine]:
    rows = conn.execute(
        "SELECT pl.* FROM placement_line pl"
        " JOIN line_of_coverage l ON l.id = pl.line_id"
        f" WHERE pl.placement_id = ? AND {base.alive('pl')}"
        " ORDER BY l.sort_order, l.name COLLATE NOCASE",
        (placement_id,),
    ).fetchall()
    return [PlacementLine.from_row(r) for r in rows]


def placement_line(
    conn: sqlite3.Connection, placement_id: str, line_id: str
) -> PlacementLine | None:
    row = conn.execute(
        "SELECT * FROM placement_line WHERE placement_id = ? AND line_id = ?"
        f" AND {base.alive()}",
        (placement_id, line_id),
    ).fetchone()
    return PlacementLine.from_row(row) if row else None


_EXPOSURE_OF = {
    "rating_basis": "expected_exposure",
    "expiring_basis": "expiring_exposure",
}


def _basis_guard(existing: PlacementLine | None, fields: dict[str, Any]) -> None:
    """A BASIS CANNOT BE SWAPPED OUT FROM UNDER A FIGURE ALREADY STORED.

    `RatingBasis.monetary` decides whether an exposure column holds integer
    CENTS or a whole COUNT. Nothing marks the stored integer as one or the
    other — the basis beside it IS the marking — so moving the basis from
    `Gross sales` to `Power units` re-reads $48,500,000 as 48,500,000 power
    units without touching a byte, and the rate per unit printed on a client's
    report is then a hundredfold out with no bad value anywhere to find.

    HERE AND NOT IN A CALLER, for the reason repo/team.py's duplicate guard
    gives: a guard on what a stored value MEANS belongs where every surface
    inherits it. The web made this one click away (the block header's basis is
    a picker); MCP could always do it by passing one argument.

    REFUSED, NOT SILENTLY CONVERTED. Converting would have to invent an
    exchange rate between dollars and power units. The refusal names the fix —
    clear the exposure, set the basis, enter the figure again — which is three
    acts because they are three decisions.

    A basis arriving WITH its own exposure in the same call is fine and is the
    ordinary correction: the caller parsed the figure against the basis it
    sent (mcpserver._set_placement_line does exactly that), so nothing is left
    meaning something else.
    """
    for basis_key, exposure_key in _EXPOSURE_OF.items():
        if basis_key not in fields or exposure_key in fields:
            continue
        was = getattr(existing, basis_key, None) if existing else None
        now = fields[basis_key]
        if now == was:
            continue
        exposure = getattr(existing, exposure_key, None) if existing else None
        if exposure is None:
            continue
        was_money = rating_basis(was).monetary if was else None
        now_money = rating_basis(now).monetary if now else None
        if was_money == now_money:
            continue
        held = (
            f"stored against {rating_basis(was).label}"
            if was
            else "stored with no basis to say whether it is money or a count"
        )
        wanted = rating_basis(now).label if now else "no basis"
        raise ValueError(
            f"{exposure_key.replace('_', ' ')} is {held}, and {wanted} would "
            f"change what that figure MEANS — one basis measures money and the "
            f"other counts things. Clear the exposure first, then set the "
            f"basis, then enter the figure again: 42 power units and $0.42 are "
            f"the same digits."
        )


def _rate_per_guard(existing: PlacementLine | None, fields: dict[str, Any]) -> None:
    """A DENOMINATOR CANNOT BE SWAPPED OUT FROM UNDER A RATE ALREADY STORED.

    `rate_per` is the unit a rate is quoted against, and 1.42 per $100 of
    payroll is ten times 1.42 per $1,000 of it. Nothing inside
    `expiring_rate_micros` says which one it was — the denominator beside it IS
    the marking — so moving `rate_per` re-reads the stored rate by a factor of
    ten without touching a byte. The header then prints "expiring 1.00" under
    "per $1,000" for a rate quoted per $100, and the premium bridge on the
    CLIENT's workbook lands $9,000 short of the quote it sits beneath (found
    2026-08-25).

    The same shape as `_basis_guard`, and here for the same reason: a guard on
    what a stored value MEANS belongs in repo/ where every surface inherits it,
    not in the one route that happens to render a picker for it. The web made
    it one click away; MCP could always do it with one argument.

    A `rate_per` arriving WITH its own `expiring_rate_micros` is the ordinary
    correction and is fine — the caller restated both halves in one act, so
    nothing is left meaning something else.

    The RESPONSE rates on this line are not checked here, and deliberately:
    each response carries its own `rate_per`, and where it does not,
    `marketing_report._reconciles` drops the premium bridge rather than print
    a walk that no longer adds up. A refusal that made the broker clear every
    quoted rate on the line before correcting one picker would be the guard
    making a legitimate entry impossible.
    """
    if "rate_per" not in fields or "expiring_rate_micros" in fields:
        return
    if existing is None or existing.expiring_rate_micros is None:
        return
    was, now = existing.rate_per, fields["rate_per"]
    if now == was or was is None:
        return
    held = RATE_PER_LABELS.get(int(was), str(was))
    wanted = RATE_PER_LABELS.get(int(now), str(now)) if now is not None else "no denominator"
    raise ValueError(
        f"the expiring rate {format_rate_micros(existing.expiring_rate_micros)} "
        f"is stated per {held}, and per {wanted} would change what that rate "
        f"MEANS — the same digits over a different denominator. Clear the "
        f"expiring rate first, then set the denominator, then enter the rate "
        f"again: 1.42 per $100 and 1.42 per $1,000 differ by a factor of ten."
    )


def _expiring_rate_guard(
    existing: PlacementLine | None, fields: dict[str, Any]
) -> None:
    """AN EXPIRING RATE NEEDS A DENOMINATOR TOO.

    The adjacent site to `_stamp_rate_per`'s, and the reason this exists as a
    second function rather than as one more branch there: the two rates are
    stored on two tables and reached by two writers, and the rule is the same
    one. `_rate_per_guard` below protects a denominator that is already under
    a stored rate; this refuses the rate that has none yet. Together they say
    the figure and its unit are recorded as one act or not at all.

    A denominator arriving in the SAME call is the ordinary way to record both
    (the MCP tool takes `rate_per` and `expiring_rate` together), and clearing
    the rate is always allowed — a rate being removed needs no unit.
    """
    if fields.get("expiring_rate_micros") is None:
        return
    if fields.get("rate_per") is not None:
        return
    if existing is not None and existing.rate_per is not None:
        return
    raise ValueError(_NO_DENOMINATOR)


def set_placement_line(
    conn: sqlite3.Connection, placement_id: str, line_id: str, **fields: Any
) -> PlacementLine:
    """Create or update the line's expectations. One row per (placement, line)
    — the unique index holds it, and this is the only writer that respects it."""
    existing = placement_line(conn, placement_id, line_id)
    _basis_guard(existing, fields)
    _rate_per_guard(existing, fields)
    _expiring_rate_guard(existing, fields)
    if existing is None:
        base.insert(
            conn,
            _PLACEMENT_LINE,
            {"placement_id": placement_id, "line_id": line_id, **fields},
        )
    elif fields:
        base.update(conn, _PLACEMENT_LINE, existing.id, fields)
    got = placement_line(conn, placement_id, line_id)
    assert got is not None  # just written
    return got
