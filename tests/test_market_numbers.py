"""Numbers on the markets screens that a broker could not explain to a boss.

Two findings, both about a figure that looks authoritative and is not:

- "ON THE TOWER" counted QUOTED placements as exposure, so the screen that
  answers "what am I on with this carrier" included money not yet placed —
  and disagreed with the book's own bound-only totals from the same data;
- hit rates divided by every submission SENT, including ones still out, so a
  market with work in progress was penalised for it, and the rate was shown
  without the count it was computed from.
"""

from __future__ import annotations

import sqlite3

from bookkit.repo import orgs, placements, projection
from bookkit.repo import submissions as subs_repo
from bookkit.services import hit_rate

WINDOW = ("2020-01-01", "2030-01-01")


def _tower(conn: sqlite3.Connection, status: str) -> str:
    """One account with one carrier on one layer, at the given status."""
    org = orgs.create(conn, kind="client", name=f"Atomic {status}", status="active")
    placement = placements.create(
        conn, org_id=org.id, program_name="Casualty", status=status,
        period_from="2026-01-01", period_to="2026-12-31",
    )
    projection.replace_for_placement(
        conn, placement.id, synced_at="2026-01-01T00:00:00+00:00",
        layers=[{"layer_id": "L1", "name": "Primary", "applies_to": "GL",
                 "attach": 0, "lim": 500, "premium": 65000}],
        participants=[
            {"layer_id": "L1", "carrier": "Travelers", "share_bps": 10000,
             "premium": 65000}
        ],
        retentions=[],
    )
    return placement.id


# --- exposure says what it is ----------------------------------------------


def test_exposure_rows_carry_the_placement_status(conn: sqlite3.Connection) -> None:
    """Without this the screen cannot tell placed business from a live quote,
    and it read $650K where the book's bound-only total read nothing."""
    _tower(conn, "quoted")
    rows = projection.carrier_exposure(conn, ["Travelers"], *WINDOW)
    assert rows, "no exposure rows at all"
    assert "status" in rows[0].keys(), "exposure rows do not carry status"
    assert rows[0]["status"] == "quoted"


def test_bound_and_quoted_exposure_are_distinguishable(
    conn: sqlite3.Connection,
) -> None:
    _tower(conn, "quoted")
    _tower(conn, "bound")
    statuses = {r["status"] for r in projection.carrier_exposure(conn, ["Travelers"], *WINDOW)}
    assert statuses == {"quoted", "bound"}


# --- hit rates a broker can explain -----------------------------------------


def _submission(conn: sqlite3.Connection, market_id: str, status: str) -> None:
    org = orgs.create(conn, kind="client", name=f"Client {status}", status="active")
    placement = placements.create(
        conn, org_id=org.id, program_name="Casualty", status="submitted",
        period_from="2026-01-01", period_to="2026-12-31",
    )
    subs_repo.create(
        conn, placement_id=placement.id, market_org_id=market_id,
        status=status, sent_on="2026-01-15",
    )


def test_a_submission_still_out_is_not_counted_against_the_market(
    conn: sqlite3.Connection,
) -> None:
    """AIG showed quote 67% (2 of 3) when 2 of 2 DECIDED submissions quoted.
    A market is penalised for work in progress that has not come back yet."""
    market = orgs.create(conn, kind="market", name="AIG")
    _submission(conn, market.id, "quoted")
    _submission(conn, market.id, "bound")
    _submission(conn, market.id, "out")

    rate = next(r for r in hit_rate.by_market(conn) if r.market_org_id == market.id)

    assert rate.sent == 3
    assert rate.decided == 2
    assert rate.pending == 1
    assert rate.quote_rate == 1.0, "an undecided submission dragged the rate down"


def test_a_declined_submission_still_counts_in_the_denominator(
    conn: sqlite3.Connection,
) -> None:
    market = orgs.create(conn, kind="market", name="Chubb")
    _submission(conn, market.id, "quoted")
    _submission(conn, market.id, "declined")

    rate = next(r for r in hit_rate.by_market(conn) if r.market_org_id == market.id)

    assert rate.decided == 2
    assert rate.quote_rate == 0.5


def test_a_market_with_nothing_decided_has_no_rate_rather_than_zero(
    conn: sqlite3.Connection,
) -> None:
    """0% and "nothing has come back yet" are different things, and rendering
    both as 0% is what makes an unlabelled ratio a trap."""
    market = orgs.create(conn, kind="market", name="Munich Re")
    _submission(conn, market.id, "out")

    rate = next(r for r in hit_rate.by_market(conn) if r.market_org_id == market.id)

    assert rate.decided == 0
    assert rate.quote_rate is None
    assert rate.bind_rate is None


def test_bind_rate_is_out_of_what_was_quoted(conn: sqlite3.Connection) -> None:
    market = orgs.create(conn, kind="market", name="Beazley")
    _submission(conn, market.id, "bound")
    _submission(conn, market.id, "quoted")
    _submission(conn, market.id, "declined")

    rate = next(r for r in hit_rate.by_market(conn) if r.market_org_id == market.id)

    assert rate.quoted == 2          # bound counts as quoted on the way through
    assert rate.bind_rate == 0.5
