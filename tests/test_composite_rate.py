"""The composite rate: premium / exposure, when nobody typed a rate.

Grant, 2026-08-27: "in marketing tab - calculating a composite rate if given
expiring premium and exposure basis". The block header already carries the
expiring premium, the expiring exposure and the basis that says what the
exposure MEANS — so the fourth field was asking for a figure the book can
work out, which is the DRY rule pointed at data entry.

THE WORKED EXAMPLE IS THE SAME ONE the report suite uses: $412,000 on $41.0M
of gross sales, rated per $1,000, which is 10.0488 — and 10_048_780 micros is
the figure that book has stored by hand since the report shipped. The
derivation reproducing it exactly is the point: it is not a new number, it is
the one a broker was being asked to type.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bookkit.repo import marketing, orgs, placements, submissions
from bookkit.services import marketing_report
from bookkit.services.marketing_report import expiring_rate

GL = "general-liability"
TODAY = date(2027, 7, 28)

EXPIRING_PREMIUM = 41_200_000        # $412,000
EXPIRING_EXPOSURE = 4_100_000_000    # $41.0M of gross sales
EXPIRING_RATE = 10_048_780           # 10.0488 per $1,000


def _line(conn: sqlite3.Connection, **fields):
    client = orgs.create(conn, kind="client", name="Legibility Inc", status="active")
    placement = placements.create(
        conn, org_id=client.id, program_name="2027 casualty",
        period_from="2027-09-01", period_to="2028-09-01",
    )
    base = dict(
        expiring_premium=EXPIRING_PREMIUM,
        expiring_exposure=EXPIRING_EXPOSURE,
        expiring_basis="gross_sales",
        rating_basis="gross_sales",
        rate_per=1000,
    )
    base.update(fields)
    line = marketing.set_placement_line(conn, placement.id, GL, **{
        k: v for k, v in base.items() if v is not None
    })
    return client, placement, line


# --- the arithmetic --------------------------------------------------------


def test_the_rate_is_worked_out_from_the_premium_and_the_exposure(
    conn: sqlite3.Connection,
) -> None:
    """The figure a broker would have typed, to the micro."""
    _, _, line = _line(conn)
    got = expiring_rate(line)
    assert got.micros == EXPIRING_RATE
    assert got.derived is True


def test_a_typed_rate_outranks_the_division_and_says_so(
    conn: sqlite3.Connection,
) -> None:
    """A rate read off last year's policy is a SOURCE, and the division is
    not allowed to overwrite it — the same rule a market stating its own
    premium follows (`stated-market-premium`). Deliberately a figure the
    division would NOT produce, so a fallback that ignored it would show."""
    _, _, line = _line(conn, expiring_rate_micros=9_500_000)
    got = expiring_rate(line)
    assert got.micros == 9_500_000
    assert got.derived is False


def test_a_zero_exposure_makes_the_rate_unknown_and_never_zero(
    conn: sqlite3.Connection,
) -> None:
    """A zero here would print as a 100% rate reduction on a client's own
    workbook, which is the worst available lie about this column."""
    _, _, line = _line(conn, expiring_exposure=0)
    assert expiring_rate(line) == marketing_report.ExpiringRate(None, False)


@pytest.mark.parametrize(
    "missing", ["expiring_premium", "expiring_exposure", "expiring_basis", "rate_per"],
)
def test_every_figure_the_division_needs_is_required(
    conn: sqlite3.Connection, missing: str,
) -> None:
    """Four inputs and no guesses. The basis says whether the exposure is
    money or a count; the denominator says how much of it one unit of rate
    buys. Neither is inferable, and inferring either is the hundredfold
    class of error this module has already shipped twice."""
    _, _, line = _line(conn, **{missing: None})
    assert expiring_rate(line).micros is None


def test_a_counted_basis_divides_in_dollars_not_cents(
    conn: sqlite3.Connection,
) -> None:
    """320 power units carrying $412,000 is $1,287.50 a unit. On a COUNT
    basis the exposure is a number of things, so the premium comes DOWN to
    dollars before it is divided — the inverse of the correction
    `_premium_from` carries, and getting it backwards is a factor of 100."""
    _, _, line = _line(
        conn,
        expiring_basis="power_units", rating_basis="power_units",
        expiring_exposure=320, rate_per=1,
    )
    got = expiring_rate(line)
    assert got.micros == 1_287_500_000
    assert got.derived is True
    assert marketing_report.fmt_rate(got.micros) == "1287.50"


def test_the_derived_rate_and_the_premium_are_each_other_s_inverse(
    conn: sqlite3.Connection,
) -> None:
    """Round-trip, because the two functions live beside each other precisely
    so they cannot drift: the rate worked out of a premium must buy that
    premium back."""
    _, _, line = _line(conn)
    micros = expiring_rate(line).micros
    assert micros is not None
    assert marketing_report._premium_from(
        micros, EXPIRING_EXPOSURE, 1000, monetary=True
    ) == pytest.approx(EXPIRING_PREMIUM, abs=100)


# --- what it unlocks -------------------------------------------------------


def _quote(conn, placement_id, rate_micros: int, premium: int):
    market = orgs.create(conn, kind="market", name="Travelers", status="active")
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2027-07-07",
        placement_id=placement_id,
    )
    return marketing.create_response(
        conn, sub.id, GL, market_org_id=market.id, status="quoted",
        premium=premium, rate_micros=rate_micros, rating_basis="gross_sales",
        rate_per=1000, exposure_amount=EXPIRING_EXPOSURE,
    )


def test_the_rate_delta_is_computed_off_the_derived_rate(
    conn: sqlite3.Connection,
) -> None:
    """THE HEADER AND THE COLUMN ARE ONE REPORT. Before this the header cell
    would have shown a composite rate while the Rate cell one row down said
    "no expiring rate recorded" — two answers about the same two numbers, on
    the same screen."""
    _, placement, _ = _line(conn)
    _quote(conn, placement.id, rate_micros=11_053_658, premium=45_320_000)
    report = marketing_report.compose(conn, placement.id, TODAY)
    block = next(b for b in report.blocks if b.line_id == GL)
    assert block.expiring_rate.derived is True
    assert block.expiring_rate.micros == EXPIRING_RATE
    assert block.rows[0].rate_move.pct == pytest.approx(10.0, abs=0.1)
    assert block.rows[0].rate_move.note == ""


def test_the_premium_bridge_walks_off_a_derived_rate(
    conn: sqlite3.Connection,
) -> None:
    """The bridge needs the expiring premium AND the expiring exposure
    already, so every walk the derivation unlocks is one where the rate was
    the only figure missing — and it is exact by construction, where a typed
    rate is a rounded input `_reconciles` has to leave slack for."""
    _, placement, _ = _line(conn)
    _quote(conn, placement.id, rate_micros=11_053_658, premium=45_320_000)
    report = marketing_report.compose(conn, placement.id, TODAY)
    block = next(b for b in report.blocks if b.line_id == GL)
    assert block.bridge is not None
    walked = (
        block.bridge.expiring_premium
        + block.bridge.rate_effect
        + block.bridge.exposure_effect
    )
    assert walked == pytest.approx(block.bridge.quoted_premium, abs=100)


def test_a_line_with_no_exposure_still_says_there_is_no_expiring_rate(
    conn: sqlite3.Connection,
) -> None:
    """The state the stored column exists for: an expiring premium recorded
    and the exposure never captured. Deriving is impossible and the report
    refuses in the words it always used, rather than inventing a rate off a
    flat-exposure assumption nobody made."""
    _, placement, _ = _line(conn, expiring_exposure=None)
    _quote(conn, placement.id, rate_micros=11_053_658, premium=45_320_000)
    report = marketing_report.compose(conn, placement.id, TODAY)
    block = next(b for b in report.blocks if b.line_id == GL)
    assert block.expiring_rate.micros is None
    assert block.rows[0].rate_move.note == marketing_report.NO_EXPIRING_RATE


# --- what the heading says, and the note under it --------------------------


def test_a_line_with_only_its_expiring_side_still_states_the_basis_and_the_value(
    conn: sqlite3.Connection,
) -> None:
    """Grant, 2026-08-27: "I would like to provide the basis value ... GL:
    Basis: Total Insured Value: $10,000, Expiring Premium: $1,000, Rate: $1.00
    per $1,000".

    The heading read the basis and the exposure off THIS TERM's columns while
    the premium and rate beside them came off the EXPIRING ones — so the shape
    the composite rate makes ordinary (an expiring premium and exposure, and
    nothing about this term yet) printed `expiring $100,000 at 100.00`: a rate
    with no basis, no denominator and no exposure to divide by. On a client's
    workbook."""
    _, placement, _ = _line(
        conn, expiring_basis="tiv", expiring_exposure=1_000_000_00,
        expiring_premium=100_000_00, rating_basis=None,
    )
    _quote(conn, placement.id, rate_micros=11_053_658, premium=45_320_000)
    report = marketing_report.compose(conn, placement.id, TODAY)
    label = marketing_report.to_sections(report)[0].label

    assert "Total insured value $1,000,000" in label
    assert "premium $100,000" in label
    # 100,000 / 1,000,000 x 1,000 = 100.00 — and the reader can now do that
    # division, which is the whole point of naming the exposure beside it.
    assert "rate 100.00 per $1,000" in label


def test_a_clause_never_borrows_the_other_term_s_basis(
    conn: sqlite3.Connection,
) -> None:
    """AN AXIS NOBODY STATED IS NOT AN AXIS THAT AGREES — the same reading
    `_rate_move` makes, in prose, where no guard can see it. A heading that
    filled last year's basis in from this year's would state a measurement
    nobody made."""
    _, placement, _ = _line(
        conn, expiring_basis="payroll", rating_basis="gross_sales",
        expected_exposure=4_850_000_000,
    )
    _quote(conn, placement.id, rate_micros=11_053_658, premium=45_320_000)
    label = marketing_report.to_sections(
        marketing_report.compose(conn, placement.id, TODAY)
    )[0].label

    assert "this term: Gross sales $48,500,000" in label
    assert "expiring: Payroll $41,000,000" in label


def test_the_client_note_leads_the_section_on_both_copies(
    conn: sqlite3.Connection,
) -> None:
    """Grant, 2026-08-27: "a notes field to be able to make notes on a line
    that would be present for a client in the table".

    It is a ROW and not a heading clause — prose of any length inside the
    heading pushes the labelled figures out of the cell — and it comes FIRST,
    because it is context for reading the rows under it."""
    note = "TIV excludes the Ohio site, added mid-term."
    _, placement, _ = _line(conn, client_note=note)
    _quote(conn, placement.id, rate_micros=11_053_658, premium=45_320_000)

    for audience in (marketing_report.CLIENT, marketing_report.INTERNAL):
        report = marketing_report.compose(conn, placement.id, TODAY, audience)
        section = marketing_report.to_sections(report)[0]
        assert section.rows[0][0] == "Note", audience
        assert section.rows[0][1] == note, audience
        assert len(section.rows[0]) == len(marketing_report.columns(audience))
        # and it did not leak into the heading, which is figures only
        assert note not in section.label


def test_a_line_with_no_note_renders_no_note_row(conn: sqlite3.Connection) -> None:
    """A row labelled Note with a blank beside it reads to a client as
    something that failed to print."""
    _, placement, _ = _line(conn)
    _quote(conn, placement.id, rate_micros=11_053_658, premium=45_320_000)
    section = marketing_report.to_sections(
        marketing_report.compose(conn, placement.id, TODAY)
    )[0]
    assert section.rows[0][0] != "Note"


def test_a_note_on_a_line_nobody_has_marketed_still_prints(
    conn: sqlite3.Connection,
) -> None:
    """The empty-block branch is a second renderer of the same section and it
    forgot the bridge once already. A line nobody has approached is exactly
    where a note explaining WHY belongs."""
    note = "Not going to market — renewing with the incumbent."
    _, placement, _ = _line(conn, client_note=note)
    section = marketing_report.to_sections(
        marketing_report.compose(conn, placement.id, TODAY)
    )[0]
    assert section.rows[0][:2] == ("Note", note)
    assert "No markets approached" in section.rows[1][0]


def test_clearing_the_note_stores_nothing_rather_than_an_empty_string(
    conn: sqlite3.Connection,
) -> None:
    """ONE WAY TO SAY NOTHING. Both surfaces clear this by sending an empty
    box, and a column holding both NULL and "" is one every later reader has
    to remember to test twice — the first one that writes `is not None` prints
    an empty Note row on a client's workbook."""
    _, placement, _ = _line(conn, client_note="something")
    line = marketing.set_placement_line(
        conn, placement.id, GL, client_note="   "
    )
    assert line.client_note is None


# --- a typed rate the two figures beside it do not support ------------------


def test_a_typed_rate_that_contradicts_the_division_is_flagged(
    conn: sqlite3.Connection,
) -> None:
    """Grant, 2026-08-27, asked for this after it was first reported as a
    design cost. A typed rate that premium / exposure does not support is two
    figures where one is a lie, and the Rate delta a client reads is composed
    from the typed one."""
    _, _, line = _line(conn, expiring_rate_micros=9_500_000)
    got = expiring_rate(line)

    assert got.micros == 9_500_000       # the typed figure still wins
    assert got.derived is False
    assert got.computed == EXPIRING_RATE  # and the division is carried beside it
    assert got.disagrees is True


def test_a_rounded_entry_is_not_a_disagreement(conn: sqlite3.Connection) -> None:
    """412,000 / 41,000 is 10.0488 and goes on the file as 10.05. A marker
    that fired on that would be on every correctly-entered line in the book,
    which is how a warning stops being read."""
    _, _, line = _line(conn, expiring_rate_micros=10_050_000)
    got = expiring_rate(line)

    assert got.micros == 10_050_000
    assert got.computed == EXPIRING_RATE
    assert got.disagrees is False


def test_a_derived_rate_can_never_disagree_with_itself(
    conn: sqlite3.Connection,
) -> None:
    _, _, line = _line(conn)
    assert expiring_rate(line).derived is True
    assert expiring_rate(line).disagrees is False


def test_nothing_to_divide_by_means_nothing_to_disagree_with(
    conn: sqlite3.Connection,
) -> None:
    """The state the stored column exists for: a rate off last year's policy
    and no exposure ever captured. There is no second figure, so there is no
    contradiction to report — and a marker here would be permanent."""
    _, _, line = _line(conn, expiring_exposure=None, expiring_rate_micros=9_500_000)
    got = expiring_rate(line)

    assert got.micros == 9_500_000
    assert got.computed is None
    assert got.disagrees is False
