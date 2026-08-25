"""Market responses: the roll-up, the clearance warning, and the totals a
client-facing grid may and may not print."""

from __future__ import annotations

import sqlite3

import pytest

from bookkit.models import MarketResponse
from bookkit.repo import marketing, orgs, placements, submissions


def _setup(conn: sqlite3.Connection):
    client = orgs.create(conn, kind="client", name="Legibility Inc", status="active")
    placement = placements.create(
        conn,
        org_id=client.id,
        program_name="2027 casualty",
        period_from="2027-01-01",
        period_to="2028-01-01",
    )
    return client, placement


def _submission(conn: sqlite3.Connection, placement_id: str, market_name: str):
    market = orgs.create(conn, kind="market", name=market_name, status="active")
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2027-07-07", placement_id=placement_id
    )
    return market, sub


# --- creating --------------------------------------------------------------


def test_a_response_needs_a_carrier_or_an_intermediary(conn) -> None:
    _, placement = _setup(conn)
    _, sub = _submission(conn, placement.id, "Travelers")
    with pytest.raises(ValueError, match="carrier or an intermediary"):
        marketing.create_response(conn, sub.id, "general-liability")


def test_a_wholesaler_alone_is_a_real_row(conn) -> None:
    """You send to RT Specialty and THEY come back with CNA. Until they do,
    "out to RT Specialty, carrier TBD" is the truth, not a gap."""
    _, placement = _setup(conn)
    _, sub = _submission(conn, placement.id, "RT Specialty")
    wholesaler = orgs.create(conn, kind="market", name="RT Specialty Inc", status="active")
    response = marketing.create_response(
        conn, sub.id, "general-liability", via_org_id=wholesaler.id
    )
    assert response.market_org_id is None
    assert response.status == "pending"


def test_an_unknown_status_is_refused(conn) -> None:
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    with pytest.raises(ValueError, match="unknown market response status"):
        marketing.create_response(
            conn, sub.id, "general-liability", market_org_id=market.id, status="maybe"
        )


# --- the roll-up -----------------------------------------------------------


def _respond(conn, sub_id, market_id, line_id, status, **kw):
    return marketing.create_response(
        conn, sub_id, line_id, market_org_id=market_id, status=status, **kw
    )


def _submission_status(conn, sub_id) -> str:
    return conn.execute("SELECT status FROM submission WHERE id = ?", (sub_id,)).fetchone()[0]


@pytest.mark.parametrize(
    "statuses, expected",
    [
        (["pending"], "out"),
        (["declined", "non_response"], "declined"),
        (["declined", "indicated"], "quoted"),
        (["declined", "quoted"], "quoted"),
        (["quoted", "bound"], "bound"),
    ],
)
def test_the_submission_status_rolls_up_from_its_responses(conn, statuses, expected) -> None:
    """Typed a second time, the two copies disagree and nobody knows which is
    right — so the submission's status is derived after every response write."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    for n, status in enumerate(statuses):
        _respond(conn, sub.id, market.id, "general-liability", status, attach=n * 100)
    assert _submission_status(conn, sub.id) == expected


def test_the_roll_up_never_un_withdraws_a_submission(conn) -> None:
    """Withdrawing is a decision about the SUBMISSION — we pulled it — not a
    summary of what markets said back. A roll-up that clobbered it would
    quietly un-withdraw the moment a stale response was edited."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    conn.execute("UPDATE submission SET status = 'withdrawn' WHERE id = ?", (sub.id,))
    _respond(conn, sub.id, market.id, "general-liability", "quoted")
    assert _submission_status(conn, sub.id) == "withdrawn"


# --- clearance -------------------------------------------------------------


def _two_routes_to(conn, placement_id, carrier, line_id="general-liability"):
    rt, sub_rt = _submission(conn, placement_id, "RT Specialty")
    amwins, sub_am = _submission(conn, placement_id, "Amwins")
    first = marketing.create_response(
        conn, sub_rt.id, line_id, market_org_id=carrier.id, via_org_id=rt.id
    )
    second = marketing.create_response(
        conn, sub_am.id, line_id, market_org_id=carrier.id, via_org_id=amwins.id
    )
    return first, second


def test_two_intermediaries_reaching_one_carrier_collide(conn) -> None:
    _, placement = _setup(conn)
    cna = orgs.create(conn, kind="market", name="CNA", status="active")
    first, second = _two_routes_to(conn, placement.id, cna)
    conflicts = marketing.clearance_conflicts(conn, second)
    assert [c.id for c in conflicts] == [first.id]


def test_the_same_intermediary_twice_is_not_a_collision(conn) -> None:
    """Two layers of one tower through one wholesaler is one approach recorded
    twice, not a clearance problem."""
    _, placement = _setup(conn)
    cna = orgs.create(conn, kind="market", name="CNA", status="active")
    rt, sub = _submission(conn, placement.id, "RT Specialty")
    marketing.create_response(
        conn, sub.id, "general-liability", market_org_id=cna.id, via_org_id=rt.id, attach=0
    )
    second = marketing.create_response(
        conn, sub.id, "general-liability", market_org_id=cna.id, via_org_id=rt.id,
        attach=1_000_000_00,
    )
    assert marketing.clearance_conflicts(conn, second) == []


def test_a_closed_approach_no_longer_blocks(conn) -> None:
    """A declined approach is not holding the market against anyone."""
    _, placement = _setup(conn)
    cna = orgs.create(conn, kind="market", name="CNA", status="active")
    first, second = _two_routes_to(conn, placement.id, cna)
    marketing.edit_response(conn, first.id, {"status": "declined"})
    assert marketing.clearance_conflicts(conn, second) == []


def test_a_response_with_no_carrier_cannot_collide(conn) -> None:
    """Nobody knows which underwriter it will land on yet."""
    _, placement = _setup(conn)
    rt, sub = _submission(conn, placement.id, "RT Specialty")
    response = marketing.create_response(
        conn, sub.id, "general-liability", via_org_id=rt.id
    )
    assert marketing.clearance_conflicts(conn, response) == []


# --- money the grid may print ---------------------------------------------


def _priced(**kw) -> MarketResponse:
    return MarketResponse(
        id="1", submission_id="s", line_id="general-liability", market_org_id="o",
        created_at="x", updated_at="x", **kw,
    )


def test_a_total_is_blank_until_every_component_is_known(conn) -> None:
    """NULL is "nobody has told us"; 0 is "we asked, there is none". Only 0
    contributes. A total that treats an unquoted surplus lines tax as zero
    understates an E&S placement by the amount that decides it."""
    assert _priced(premium=39_285_000, tria_premium=785_000).total_cost is None
    assert _priced().total_cost is None
    known = _priced(
        premium=39_285_000, tria_premium=785_000, policy_fees=390_000,
        surplus_lines_tax=0,
    )
    assert known.total_cost == 40_460_000  # $404,600 — the Travelers row


# --- what a line is expected to do ----------------------------------------


def test_a_line_has_one_row_per_placement_and_upserts(conn) -> None:
    _, placement = _setup(conn)
    marketing.set_placement_line(
        conn, placement.id, "general-liability", expiring_premium=41_200_000
    )
    marketing.set_placement_line(
        conn, placement.id, "general-liability", expiring_exposure=4_100_000_000
    )
    rows = marketing.placement_lines(conn, placement.id)
    assert len(rows) == 1
    assert rows[0].expiring_premium == 41_200_000
    assert rows[0].expiring_exposure == 4_100_000_000


def test_placement_lines_come_back_in_vocabulary_order(conn) -> None:
    _, placement = _setup(conn)
    for line_id in ("property", "general-liability", "auto"):
        marketing.set_placement_line(conn, placement.id, line_id)
    assert [r.line_id for r in marketing.placement_lines(conn, placement.id)] == [
        "general-liability",
        "auto",
        "property",
    ]
