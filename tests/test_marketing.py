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
        # A NO ABOUT ONE BAND IS NOT A CLOSED PACKAGE. `declined` here would
        # take the package off the "out at market" queue while the work of
        # going back to that carrier higher up the tower is still to do.
        (["declined_open_elsewhere"], "out"),
        (["declined", "declined_open_elsewhere"], "out"),
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


# --- terms cannot lapse before they were quoted -----------------------------


def test_an_expiry_before_the_reply_is_refused(conn) -> None:
    """A year typed from last year's diary puts a LIVE quote straight into the
    expired bucket of the chase queue, where it reads as terms already lost."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    with pytest.raises(ValueError, match="cannot lapse before the market quoted"):
        _respond(
            conn, sub.id, market.id, "general-liability", "quoted",
            responded_on="2027-07-20", quote_expires_on="2026-08-20",
        )


def test_an_expiry_before_the_package_went_out_is_refused(conn) -> None:
    """With no reply date recorded the send date is what it is checked
    against — the failure is just as wrong on a row nobody has dated."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    with pytest.raises(ValueError, match="cannot lapse before the package went out"):
        _respond(
            conn, sub.id, market.id, "general-liability", "quoted",
            quote_expires_on="2026-08-20",
        )


def test_moving_the_reply_past_a_stored_expiry_is_refused(conn) -> None:
    """EITHER HALF OF THE PAIR CAN BE THE ONE MOVING. A guard that watched only
    the field it is named after would let the reply date walk straight past an
    expiry already recorded."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    row = _respond(
        conn, sub.id, market.id, "general-liability", "quoted",
        responded_on="2027-07-20", quote_expires_on="2027-08-20",
    )
    with pytest.raises(ValueError, match="cannot lapse before the market quoted"):
        marketing.edit_response(conn, row.id, {"responded_on": "2027-09-01"})


def test_an_expiry_already_past_is_recorded_without_complaint(conn) -> None:
    """Quotes lapse, and recording the lapse is the whole point of the field.
    Only the relationship between the row's own dates is checked."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    row = _respond(
        conn, sub.id, market.id, "general-liability", "quoted",
        responded_on="2027-07-20", quote_expires_on="2027-07-25",
    )
    assert row.quote_expires_on == "2027-07-25"


# --- the five figures beside the status ------------------------------------
#
# `status` was derived from the response rows and the five quote facts beside
# it were not, so `submission.quoted_premium` and friends were a SECOND HOME
# for what a market said. Each test below holds ONE of the rules that decides
# what adds and what does not.


def _figures(conn, sub_id) -> dict:
    row = conn.execute(
        "SELECT quoted_premium, quoted_limit, response_on, quote_expires_on,"
        " decline_reason FROM submission WHERE id = ?",
        (sub_id,),
    ).fetchone()
    return dict(row)


def test_a_submission_with_no_responses_keeps_every_stored_figure(conn) -> None:
    """THE LOAD-BEARING RULE. A submission nobody has assigned lines to yet has
    its typed figures as the ONLY record of that marketing — 23 of them on the
    seeded book, two quoted at $1.4M — and a roll-up that wrote NULL over them
    because it found nothing to derive from would destroy the very data it
    exists to make trustworthy."""
    _, placement = _setup(conn)
    _, sub = _submission(conn, placement.id, "Travelers")
    submissions.update(
        conn, sub.id, status="quoted", quoted_premium=140_000_00,
        quoted_limit=5_000_000_00, response_on="2027-07-20",
        quote_expires_on="2027-08-20", decline_reason="none given",
    )

    assert marketing.roll_up_submission(conn, sub.id) is None

    assert _figures(conn, sub.id) == {
        "quoted_premium": 140_000_00,
        "quoted_limit": 5_000_000_00,
        "response_on": "2027-07-20",
        "quote_expires_on": "2027-08-20",
        "decline_reason": "none given",
    }
    assert _submission_status(conn, sub.id) == "quoted"


def test_money_adds_across_the_lines_a_package_carries(conn) -> None:
    """$100k of GL and $40k of Auto costs the client $140k, and that is what
    the Pipeline's premium column has always meant."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    _respond(conn, sub.id, market.id, "general-liability", "quoted", premium=100_000_00)
    _respond(conn, sub.id, market.id, "auto", "quoted", premium=40_000_00)
    assert _figures(conn, sub.id)["quoted_premium"] == 140_000_00


def test_an_unpriced_line_contributes_nothing_rather_than_zero(conn) -> None:
    """NULL is "nobody has told us" and 0 is "there is none". A market that has
    not priced yet must not drag the sum down, and a package where NOBODY has
    priced has no premium at all rather than $0.00."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    first = _respond(conn, sub.id, market.id, "general-liability", "quoted")
    assert _figures(conn, sub.id)["quoted_premium"] is None
    marketing.edit_response(conn, first.id, {"premium": 100_000_00})
    _respond(conn, sub.id, market.id, "auto", "pending")
    assert _figures(conn, sub.id)["quoted_premium"] == 100_000_00


def test_limits_do_not_add_across_lines(conn) -> None:
    """$1M of GL and $5M of property is not $6M of anything, and a summed
    figure on the Pipeline would be a number no market ever quoted."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    _respond(
        conn, sub.id, market.id, "general-liability", "quoted",
        premium=100_000_00, lim=1_000_000_00,
    )
    assert _figures(conn, sub.id)["quoted_limit"] == 1_000_000_00
    _respond(
        conn, sub.id, market.id, "property", "quoted",
        premium=40_000_00, lim=5_000_000_00,
    )
    assert _figures(conn, sub.id)["quoted_limit"] is None


def test_the_package_is_answered_when_the_last_market_speaks(conn) -> None:
    """MAX. The column answers "when was this package fully answered", and it
    is not answered until the last line has been."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    _respond(
        conn, sub.id, market.id, "general-liability", "quoted",
        responded_on="2027-07-20",
    )
    _respond(conn, sub.id, market.id, "auto", "declined", responded_on="2027-07-28")
    assert _figures(conn, sub.id)["response_on"] == "2027-07-28"


def test_the_earliest_lapse_is_the_deadline(conn) -> None:
    """MIN. A chase queue that took the latest expiry would let the first
    quote die quietly, which is the whole failure services.quotes exists to
    stop."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    _respond(
        conn, sub.id, market.id, "general-liability", "quoted",
        quote_expires_on="2027-09-30",
    )
    _respond(
        conn, sub.id, market.id, "auto", "quoted", quote_expires_on="2027-08-15",
    )
    assert _figures(conn, sub.id)["quote_expires_on"] == "2027-08-15"


def test_a_part_declined_package_is_not_a_declined_package(conn) -> None:
    """One carrier saying "class appetite" while another quotes says nothing
    about the PACKAGE, and a reason printed on the parent would attribute one
    market's words to all of them."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    _respond(
        conn, sub.id, market.id, "general-liability", "declined",
        decline_reason="loss history",
    )
    assert _figures(conn, sub.id)["decline_reason"] == "loss history"
    _respond(conn, sub.id, market.id, "auto", "quoted", premium=40_000_00)
    assert _figures(conn, sub.id)["decline_reason"] is None


def test_a_market_that_never_answered_did_not_give_a_reason(conn) -> None:
    """EVERY response declined, not merely every reason agreeing. A line that
    got no reply at all is a line nobody declined, so a reason printed on the
    parent would put words in the mouth of a market that never spoke — even
    where the broker's own internal note beside it says the same thing."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    _respond(
        conn, sub.id, market.id, "general-liability", "declined",
        decline_reason="outside appetite for this class",
    )
    _respond(
        conn, sub.id, market.id, "auto", "non_response",
        decline_reason="outside appetite for this class",
    )
    assert _submission_status(conn, sub.id) == "declined"
    assert _figures(conn, sub.id)["decline_reason"] is None


def test_two_declines_that_disagree_leave_the_parent_silent(conn) -> None:
    """There is no ONE sentence to print, so none is: the reasons stay on the
    rows that said them."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    _respond(
        conn, sub.id, market.id, "general-liability", "declined",
        decline_reason="loss history",
    )
    _respond(conn, sub.id, market.id, "auto", "declined", decline_reason="capacity")
    assert _figures(conn, sub.id)["decline_reason"] is None


def test_the_roll_up_leaves_a_withdrawn_submission_s_figures_alone(conn) -> None:
    """The status half of this rule is held above; the five figures are the
    same decision. We pulled the package — what a stale response says about it
    afterwards does not restate its terms."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Travelers")
    submissions.update(conn, sub.id, quoted_premium=140_000_00)
    conn.execute("UPDATE submission SET status = 'withdrawn' WHERE id = ?", (sub.id,))
    _respond(conn, sub.id, market.id, "general-liability", "quoted", premium=1_00)
    assert _figures(conn, sub.id)["quoted_premium"] == 140_000_00


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


# --- the marketing that has no line of coverage yet -------------------------
#
# A submission with no `market_response` row is REAL MARKETING THAT HAPPENED,
# and it has no line of coverage because nobody recorded one — not because the
# marketing did not happen. The composer reports it in a block of its own and
# `assign_line` is how a row leaves that block.


def _compose(conn, placement_id, audience="client"):
    from datetime import date

    from bookkit.services import marketing_report

    return marketing_report.compose(conn, placement_id, date(2027, 8, 1), audience)


def test_a_submission_with_no_responses_is_reported_not_dropped(conn) -> None:
    """THE DEFECT, at composer level. It rendered nothing at all — the panel
    said no line of coverage on the placement was being marketed, and the
    workbook downloaded one header row."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Sompo")
    submissions.update(
        conn, sub.id, status="quoted", quoted_premium=140_000_000,
        quoted_limit=5_000_000_000, response_on="2027-07-20",
    )

    report = _compose(conn, placement.id)

    assert report.blocks == ()
    assert len(report.provisional) == 1
    row = report.provisional[0]
    assert row.market == "Sompo"
    assert row.premium == 140_000_000
    assert row.lim == 5_000_000_000
    assert row.responded_on == "2027-07-20"
    assert row.status == "Quoted" and row.status_key == "quoted"


def test_the_provisional_block_carries_no_line_of_coverage_facts(conn) -> None:
    """It must never read as a line of coverage. `ProvisionalRow` cannot HOLD a
    rating basis, a rate, a comparison against expiring or a response id — what
    cannot be said about a package with no line is absent from the type, so no
    renderer can print it."""
    _, placement = _setup(conn)
    _submission(conn, placement.id, "Sompo")

    row = _compose(conn, placement.id).provisional[0]

    for absent in (
        "rating_basis", "rate_micros", "rate_move", "rate_per_override",
        "basis_override", "exposure_override", "line_id", "response_id",
    ):
        assert not hasattr(row, absent), (
            f"a provisional row carries {absent!r}, which belongs to a line of "
            f"coverage nobody has recorded"
        )


def test_the_client_workbook_carries_the_rows_with_no_line(conn) -> None:
    """AN EMPTY WORKBOOK OVER LIVE MARKETING is the failure this ends."""
    from bookkit.services import marketing_report

    _, placement = _setup(conn)
    _, sub = _submission(conn, placement.id, "Sompo")
    submissions.update(conn, sub.id, status="quoted", quoted_premium=140_000_000)

    sections = marketing_report.to_sections(_compose(conn, placement.id))

    assert len(sections) == 1
    assert sections[0].label == marketing_report.PROVISIONAL_LABEL
    assert "Sompo" in sections[0].rows[0]
    assert "$1,400,000" in sections[0].rows[0]
    # Every row is the width of the sheet, or the columns shift under it.
    width = len(marketing_report.columns(marketing_report.CLIENT))
    assert all(len(row) == width for row in sections[0].rows)


def test_a_free_text_decline_reason_never_reaches_a_client(conn) -> None:
    """`submission.decline_reason` is free text with NO client-safe
    counterpart — unlike `market_response`, which carries the private note and
    the client wording in two separate fields for the reason
    models.PUBLIC_DECLINE_REASONS gives. So it prints on the internal sheet and
    on no other."""
    from bookkit.services import marketing_report

    _, placement = _setup(conn)
    _, sub = _submission(conn, placement.id, "Sompo")
    submissions.update(
        conn, sub.id, status="declined",
        decline_reason="underwriter hates the loss runs, off the record",
    )

    client_rows = marketing_report.to_sections(_compose(conn, placement.id))[0].rows
    internal_rows = marketing_report.to_sections(
        _compose(conn, placement.id, marketing_report.INTERNAL)
    )[0].rows

    assert not any("off the record" in cell for cell in client_rows[0])
    assert any("off the record" in cell for cell in internal_rows[0])


def test_an_answered_submission_leaves_the_provisional_block(conn) -> None:
    """THE GATE IS THE ROW SET, the same one `roll_up_submission` uses: a
    package cannot be counted twice or fall between the two."""
    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Sompo")
    marketing.create_response(
        conn, sub.id, "general-liability", market_org_id=market.id, status="quoted"
    )

    report = _compose(conn, placement.id)

    assert report.provisional == ()
    assert [b.line_id for b in report.blocks] == ["general-liability"]


def test_assigning_a_line_carries_the_figures_and_restates_nothing(conn) -> None:
    """Assigning is a NO-OP DRESSED AS A WRITE: every status maps to one that
    rolls back up to itself, so the Pipeline reads what it read before."""
    from bookkit.services import marketing_entry

    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Sompo")
    submissions.update(
        conn, sub.id, status="quoted", quoted_premium=140_000_000,
        quoted_limit=5_000_000_000, response_on="2027-07-20",
        quote_expires_on="2027-08-20",
    )
    before = submissions.get(conn, sub.id)

    response = marketing_entry.assign_line(conn, sub.id, "general-liability")

    assert response.premium == 140_000_000
    assert response.lim == 5_000_000_000
    assert response.responded_on == "2027-07-20"
    assert response.quote_expires_on == "2027-08-20"
    assert response.status == "quoted"
    assert response.market_org_id == market.id

    after = submissions.get(conn, sub.id)
    for field in (
        "status", "quoted_premium", "quoted_limit", "response_on",
        "quote_expires_on",
    ):
        assert getattr(after, field) == getattr(before, field), field


def test_an_unanswered_package_keeps_its_status_through_an_assignment(conn) -> None:
    """`out` → `pending` → `out`. `pending` means asked and nothing back yet,
    which is exactly `out`; `non_response` is a JUDGMENT somebody makes about a
    market that went quiet and would restate the package as a market that
    ignored us."""
    from bookkit.services import marketing_entry

    _, placement = _setup(conn)
    _, sub = _submission(conn, placement.id, "Amwins")

    response = marketing_entry.assign_line(conn, sub.id, "general-liability")

    assert response.status == "pending"
    assert str(submissions.get(conn, sub.id).status) == "out"


def test_nothing_is_invented_when_a_line_is_assigned(conn) -> None:
    """NULL STAYS NULL. A figure nobody entered must not arrive on the response
    as a zero — the whole point of the column being nullable."""
    from bookkit.services import marketing_entry

    _, placement = _setup(conn)
    _, sub = _submission(conn, placement.id, "Amwins")

    response = marketing_entry.assign_line(conn, sub.id, "general-liability")

    assert response.premium is None
    assert response.lim is None
    assert response.responded_on is None
    assert response.quote_expires_on is None


def test_a_wholesalers_package_does_not_gain_it_as_the_carrier(conn) -> None:
    """`who_was_asked` — the rule shared with the Pipeline's Response form.

    Putting RT Specialty in the Market column of a client's workbook as the
    CARRIER is the shape `_one_market_twice` exists to refuse, arriving from
    the other side. The half of that rule which reads another answered line on
    the same package cannot speak here at all — `assign_line`'s precondition is
    a package with NO responses — so the book's own record of what the market
    is has to, and `market_profile.market_type` is a fact somebody recorded.
    """
    from bookkit.services import marketing_entry

    _, placement = _setup(conn)
    wholesaler, sub = _submission(conn, placement.id, "RT Specialty")
    orgs.set_market_profile(conn, wholesaler.id, market_type="wholesaler")

    response = marketing_entry.assign_line(conn, sub.id, "general-liability")

    assert response.via_org_id == wholesaler.id
    assert response.market_org_id is None, (
        "the wholesaler was recorded as the paper it has not named yet"
    )


def test_a_market_the_book_says_nothing_about_is_the_carrier(conn) -> None:
    """NOTHING IS INFERRED where the book has recorded nothing. A direct
    approach is the ordinary case and the addressee IS the paper; guessing
    otherwise would empty the Market column of every workbook on a book that
    has not filled in its market profiles."""
    from bookkit.services import marketing_entry

    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Sompo")

    response = marketing_entry.assign_line(conn, sub.id, "general-liability")

    assert response.market_org_id == market.id
    assert response.via_org_id is None


def test_a_withdrawn_package_does_not_take_a_line(conn) -> None:
    """`roll_up_submission` never writes over `withdrawn`, so a response hung
    off one would have a parent that can never be recomputed from it — the same
    permanently-mis-stated row `approach` refuses to create when it declines to
    reuse a withdrawn submission."""
    from bookkit.services import marketing_entry

    _, placement = _setup(conn)
    _, sub = _submission(conn, placement.id, "Amwins")
    submissions.update(conn, sub.id, status="withdrawn")

    with pytest.raises(ValueError, match="this package was withdrawn"):
        marketing_entry.assign_line(conn, sub.id, "general-liability")
    assert not marketing.responses_for_submission(conn, sub.id)


def test_a_package_already_answered_is_not_assigned_again(conn) -> None:
    """Once one response exists the ROWS are the authority and the submission's
    columns are a cache of them — copying that cache back onto a second row is
    the second home the roll-up closed."""
    from bookkit.services import marketing_entry

    _, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Sompo")
    marketing.create_response(
        conn, sub.id, "auto", market_org_id=market.id, status="quoted"
    )

    with pytest.raises(ValueError, match="already has an answer recorded"):
        marketing_entry.assign_line(conn, sub.id, "general-liability")
    assert len(marketing.responses_for_submission(conn, sub.id)) == 1


# --- a revert re-derives the parent it just moved ---------------------------
#
# THE CACHE IS NOT A FACT ABOUT THE PAST. `roll_up_submission` writes the
# submission's six derived columns as ordinary field events inside the caller's
# batch, and `services.batches.revert` replays events BACKWARDS — so a revert
# restored the value the cache HELD at that moment instead of recomputing it
# from the rows that survive. `plan_revert`'s guard passes precisely because
# that column never moved, so the trigger is the plain Undo button: no force,
# no conflict, no stale tab (Grant, 2027-08-26).
#
# The three tests below are the three shapes the aggregate can take — a status
# ladder, a MAX and a MIN — because the defect needs a later write that moves
# the ROWS without moving the CACHED column, and what counts as one differs per
# column. `test_every_revert_leaves_the_submission_equal_to_its_rows` is the
# rule itself, over all six at once.

_DERIVED = (
    "status",
    "quoted_premium",
    "quoted_limit",
    "response_on",
    "quote_expires_on",
    "decline_reason",
)


def _cached(conn: sqlite3.Connection, sub_id: str) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT * FROM submission WHERE id = ?", (sub_id,)
    ).fetchone()
    return None if row is None else {field: row[field] for field in _DERIVED}


def _assert_cache_is_derived(conn: sqlite3.Connection, sub_id: str) -> None:
    """THE RULE, stated as the only honest test of it: re-deriving changes
    nothing.

    Not a hand-written expected value — that would assert one scenario's answer
    and say nothing about the invariant. `roll_up_submission` is idempotent and
    `base.update` writes only real changes, so running it again and finding the
    six columns unmoved IS "the cache equals what the roll-up would compute
    from the surviving rows". It also carries the no-rows case correctly: with
    nothing left to derive from, the roll-up writes nothing and the restored
    figures are the right answer (they are the only record of that marketing)."""
    before = _cached(conn, sub_id)
    marketing.roll_up_submission(conn, sub_id)
    assert _cached(conn, sub_id) == before, (
        "the submission's derived columns disagree with its response rows"
    )


def _batched(conn: sqlite3.Connection, org_id: str, tool: str = "market_responded"):
    from bookkit import db
    from bookkit.repo import batches as batches_repo

    batch_id = batches_repo.new_batch_id()
    created = batches_repo.create(
        conn, batch_id=batch_id, source="web", tool=tool,
        summary="a marketing write", org_id=org_id,
    )
    return created, db.transaction(conn, batch=db.BatchState(batch_id=batch_id))


def _revert(conn: sqlite3.Connection, batch, *, force: bool = False) -> object:
    from bookkit.services import batches as batches_svc

    return batches_svc.revert(
        conn, batch.ref, now="2027-08-26T09:00:00+00:00", force=force
    )


def _two_lines(conn: sqlite3.Connection):
    """One package out to one market on two lines of coverage — the shape the
    repro needs, and the ordinary one: a submission carries every line."""
    client, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Chubb")
    gl = marketing.create_response(
        conn, sub.id, "general-liability", market_org_id=market.id
    )
    prop = marketing.create_response(
        conn, sub.id, "property", market_org_id=market.id
    )
    return client, sub, gl, prop


def test_a_revert_does_not_leave_the_pipeline_saying_no_quotes_are_in_hand(conn) -> None:
    """The repro, exactly: two markets' worth of answers on one package, and
    undoing the FIRST of them.

    Recording Property=quoted rolls the package to 'quoted' and logs that.
    Recording GL=quoted logs nothing on the parent — it was already 'quoted' —
    so the guard sees an unmoved column and the revert applies cleanly, then
    puts 'out' back over a package one of whose markets is still quoted. The
    Pipeline counted "quotes in hand 0" four inches from a panel row reading
    Quoted."""
    client, sub, gl, prop = _two_lines(conn)

    first, tx = _batched(conn, client.id)
    with tx:
        marketing.edit_response(conn, prop.id, {"status": "quoted"})
    second, tx = _batched(conn, client.id)
    with tx:
        marketing.edit_response(conn, gl.id, {"status": "quoted"})

    result = _revert(conn, first)
    assert result.applied, "the revert was refused — the repro needs it applied"

    rows = {r.line_id: r.status for r in marketing.responses_for_submission(conn, sub.id)}
    assert rows == {"general-liability": "quoted", "property": "pending"}
    assert _submission_status(conn, sub.id) == "quoted", (
        "a market is still quoted — the package cannot be back 'out at market'"
    )
    _assert_cache_is_derived(conn, sub.id)


def test_a_revert_does_not_silence_a_reply_the_book_still_holds(conn) -> None:
    """`response_on` is a MAX. Undoing the LATER reply leaves the earlier one
    standing, and restoring NULL over it made the Pipeline say the market had
    never come back at all — while the panel printed the date."""
    client, sub, gl, prop = _two_lines(conn)

    late, tx = _batched(conn, client.id)
    with tx:
        marketing.edit_response(conn, gl.id, {"responded_on": "2027-08-12"})
    _, tx = _batched(conn, client.id)
    with tx:
        marketing.edit_response(conn, prop.id, {"responded_on": "2027-08-10"})

    assert _revert(conn, late).applied

    fresh = submissions.get(conn, sub.id)
    assert fresh.response_on == "2027-08-10", (
        "the surviving reply is the answer; NULL says the market never replied"
    )
    _assert_cache_is_derived(conn, sub.id)


def test_a_revert_does_not_run_the_chase_clock_past_the_date_the_market_gave(conn) -> None:
    """`quote_expires_on` is a MIN — the earliest lapse is the deadline, which
    is the whole reason `services.quotes` reads it. Undoing the answer that
    SET the minimum, after a later write moved the other row without moving
    that minimum, put a date ten days later back in the cache: the chase queue
    ran past the day the surviving quote actually dies."""
    client, sub, gl, prop = _two_lines(conn)

    with _batched(conn, client.id)[1]:
        marketing.edit_response(
            conn, prop.id, {"status": "quoted", "responded_on": "2027-08-10",
                            "quote_expires_on": "2027-09-30"}
        )
    lowered, tx = _batched(conn, client.id)
    with tx:
        marketing.edit_response(
            conn, gl.id, {"status": "quoted", "responded_on": "2027-08-10",
                          "quote_expires_on": "2027-09-20"}
        )
    with _batched(conn, client.id)[1]:
        marketing.edit_response(conn, prop.id, {"quote_expires_on": "2027-09-25"})

    assert _revert(conn, lowered).applied

    fresh = submissions.get(conn, sub.id)
    assert fresh.quote_expires_on == "2027-09-25", (
        "the chase clock must count to the earliest date a SURVIVING quote gives"
    )
    _assert_cache_is_derived(conn, sub.id)


def test_a_revert_with_no_rows_left_keeps_the_figures_it_restored(conn) -> None:
    """The other half, and the reason the derived columns stay event-logged
    rather than stopping being logged the way proj_* never was: with the last
    response gone there is nothing to derive from, so the roll-up writes
    nothing and the values the replay restored ARE the answer. A cache that
    could not be restored would destroy the only record of the marketing."""
    client, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Chubb")
    submissions.update(conn, sub.id, quoted_premium=140_000_000, status="quoted")

    made, tx = _batched(conn, client.id, tool="market_approach")
    with tx:
        marketing.create_response(
            conn, sub.id, "general-liability", market_org_id=market.id,
            status="quoted", premium=50_000_000,
        )

    assert _revert(conn, made).applied
    assert not marketing.responses_for_submission(conn, sub.id)

    fresh = submissions.get(conn, sub.id)
    assert fresh.quoted_premium == 140_000_000, (
        "the $1.4M typed before responses existed is the only record of it"
    )
    _assert_cache_is_derived(conn, sub.id)


def _worked_package(conn: sqlite3.Connection):
    """One package, three lines, six writer actions over it — quotes, a
    lowered expiry, two declines and a late third market. Returns the
    submission and the batch behind each action, in the order they happened."""
    client, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Chubb")
    gl = marketing.create_response(
        conn, sub.id, "general-liability", market_org_id=market.id
    )
    auto = marketing.create_response(conn, sub.id, "auto", market_org_id=market.id)

    steps = [
        ("edit", gl.id, {"status": "quoted", "responded_on": "2027-08-11",
                         "premium": 100_000_000, "lim": 500_000_000,
                         "quote_expires_on": "2027-09-30"}),
        ("edit", auto.id, {"status": "quoted", "responded_on": "2027-08-09",
                           "premium": 40_000_000, "quote_expires_on": "2027-09-20"}),
        ("edit", gl.id, {"quote_expires_on": "2027-09-25"}),
        ("edit", auto.id, {"status": "declined", "decline_reason": "capacity",
                           "premium": None, "quote_expires_on": None}),
        ("edit", gl.id, {"status": "declined", "decline_reason": "capacity",
                         "premium": None, "quote_expires_on": None}),
        ("create", "property", {"status": "quoted", "premium": 25_000_000,
                                "lim": 100_000_000, "responded_on": "2027-08-13"}),
    ]

    made = []
    for kind, target, changes in steps:
        batch, tx = _batched(conn, client.id)
        with tx:
            if kind == "edit":
                marketing.edit_response(conn, target, changes)
            else:
                marketing.create_response(
                    conn, sub.id, target, market_org_id=market.id, **changes
                )
        made.append(batch)
    return sub, made


def test_every_revert_leaves_the_submission_equal_to_its_rows() -> None:
    """THE RULE, over all six derived columns and ANY ONE of the batches — not
    just the last.

    Undoing in strict last-in-first-out order is self-consistent and proves
    nothing: the cache the replay restores is the one the previous step
    derived. The defect is a revert reaching PAST a later write, which is the
    ordinary thing the changes rail offers — every row on it has a Revert link,
    not only the top one. So each pass rebuilds the same six-step package on a
    fresh book and undoes ONE of them.

    A refused revert is a legal outcome (`plan_revert` sees a column that moved
    since and says so) and writes nothing, so the invariant holds trivially
    there. Each pass therefore runs TWICE — plain, and forced. Forced is not an
    exotic mode here: it is the one the derive report declared untouched, it
    applies every clean change while refusing the conflicted ones, and it is
    precisely how a response row moves while the cache change beside it is
    thrown away. The count of applied reverts is asserted too, so this cannot
    go green on a build where everything refuses."""
    from bookkit import db

    applied = 0
    for index in range(6):
        for force in (False, True):
            conn = db.connect(":memory:")
            try:
                sub, made = _worked_package(conn)
                if _revert(conn, made[index], force=force).applied:
                    applied += 1
                _assert_cache_is_derived(conn, sub.id)
            finally:
                conn.close()
    assert applied >= 6, (
        f"only {applied} of 12 reverts applied — the walk is asserting refusals"
    )


def test_the_re_derive_is_the_reverts_own_write_and_not_a_new_edit(conn) -> None:
    """A revert's writes are stamped 'revert' so that a revert cannot itself be
    reverted and `u` skips it — and the re-derive is one of them.

    Stamping it 'roll-up' instead would put a CACHE column at the top of the
    undo stack: the next undo would offer to put back a figure nobody typed and
    that nothing derives, and `repo.batches.external_change_count` would read
    it as a user's edit made since. The whole point of the re-derive is that it
    is bookkeeping, not an edit."""
    from bookkit.repo import events as events_repo

    client, sub, gl, prop = _two_lines(conn)

    first, tx = _batched(conn, client.id)
    with tx:
        marketing.edit_response(conn, prop.id, {"status": "quoted"})
    second, tx = _batched(conn, client.id)
    with tx:
        marketing.edit_response(conn, gl.id, {"status": "quoted"})

    assert _revert(conn, first).applied

    last = events_repo.last_mutation(conn)
    assert last is not None
    assert (last.entity_type, last.entity_id, last.field) == (
        "market_response", gl.id, "status"
    ), f"the undo stack now points at {last.entity_type}.{last.field}"


def test_undoing_an_added_market_re_derives_from_the_rows_that_are_left(conn) -> None:
    """The other kind of revert: one that SOFT-DELETES the response instead of
    editing it back.

    The parent still has to be recomputed, and by then the row that names it is
    dead — so the lookup from response to submission is dead-or-alive on
    purpose (declared in tests/test_repo.py's alive() exemptions). Through the
    living view an added market undone names nothing, nothing is re-derived,
    and the submission goes on stating a reply date it took from the market
    that was just removed."""
    client, placement = _setup(conn)
    market, sub = _submission(conn, placement.id, "Chubb")
    gl = marketing.create_response(
        conn, sub.id, "general-liability", market_org_id=market.id,
        status="quoted", responded_on="2027-08-11",
    )

    added, tx = _batched(conn, client.id, tool="market_approach")
    with tx:
        marketing.create_response(
            conn, sub.id, "property", market_org_id=market.id,
            status="quoted", responded_on="2027-08-13",
        )
    # moves a ROW without moving the cached MAX, so the revert below is clean
    with _batched(conn, client.id)[1]:
        marketing.edit_response(conn, gl.id, {"responded_on": "2027-08-12"})

    assert _revert(conn, added).applied
    assert [r.line_id for r in marketing.responses_for_submission(conn, sub.id)] == [
        "general-liability"
    ]
    assert submissions.get(conn, sub.id).response_on == "2027-08-12", (
        "the reply date came off the market that was just removed"
    )
    _assert_cache_is_derived(conn, sub.id)


# --- "not for us on the primary, but show us the excess" -------------------


def test_a_market_open_elsewhere_is_still_cleared_against(conn) -> None:
    """DECLINED HERE IS NOT GONE. `declined_open_elsewhere` is in
    MARKET_RESPONSE_OPEN_STATUSES, so a carrier that said no to one band and
    yes to being asked about another still collides with a second broker
    reaching for the same paper on the same line — which is the whole point of
    a clearance check, and exactly the state a plain `declined` would hide."""
    _, placement = _setup(conn)
    carrier, sub = _submission(conn, placement.id, "Zurich")
    wholesaler = orgs.create(
        conn, kind="market", name="RT Specialty Grp", status="active"
    )
    direct = marketing.create_response(
        conn, sub.id, "general-liability",
        market_org_id=carrier.id, status="declined_open_elsewhere",
    )
    _, other = _submission(conn, placement.id, "Amwins Grp")
    marketing.create_response(
        conn, other.id, "general-liability",
        market_org_id=carrier.id, via_org_id=wholesaler.id, status="pending",
    )

    assert marketing.clearance_conflicts(conn, marketing.get_response(conn, direct.id))


def test_a_plainly_declined_market_is_not_cleared_against(conn) -> None:
    """The other half of the pair, so the test above is measuring the STATUS
    and not merely that clearance works at all."""
    _, placement = _setup(conn)
    carrier, sub = _submission(conn, placement.id, "Zurich")
    wholesaler = orgs.create(
        conn, kind="market", name="RT Specialty Grp", status="active"
    )
    direct = marketing.create_response(
        conn, sub.id, "general-liability",
        market_org_id=carrier.id, status="declined",
    )
    _, other = _submission(conn, placement.id, "Amwins Grp")
    marketing.create_response(
        conn, other.id, "general-liability",
        market_org_id=carrier.id, via_org_id=wholesaler.id, status="pending",
    )

    assert not marketing.clearance_conflicts(
        conn, marketing.get_response(conn, direct.id)
    )
