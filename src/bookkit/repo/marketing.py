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
    MarketResponse,
    PlacementLine,
)
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
    _validate_status(str(fields.get("status", "pending")))
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
    which underwriter it will land on."""
    if response.market_org_id is None:
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


def set_placement_line(
    conn: sqlite3.Connection, placement_id: str, line_id: str, **fields: Any
) -> PlacementLine:
    """Create or update the line's expectations. One row per (placement, line)
    — the unique index holds it, and this is the only writer that respects it."""
    existing = placement_line(conn, placement_id, line_id)
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
