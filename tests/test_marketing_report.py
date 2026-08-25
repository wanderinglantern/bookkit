"""The marketing report, built against the worked example the design settled
on — so the numbers in the artifact and the numbers this ships are the same
numbers.

General Liability, expiring $412,000 on $41.0M of sales; this year $48.5M.
Six markets, one of them reached through a wholesaler, covering every status.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from bookkit.repo import marketing, orgs, placements, submissions
from bookkit.services import marketing_report

TODAY = date(2027, 7, 28)

# Cents, and rate in micros (rate x 1,000,000). The expiring rate is the
# ROUNDED figure a broker actually writes down (412,000 / 41,000 = 10.0488),
# which is why the bridge below reconciles to within a couple of cents rather
# than exactly: it is a decomposition of a rounded input, not an identity.
EXPIRING_PREMIUM = 41_200_000
EXPIRING_EXPOSURE = 4_100_000_000
EXPIRING_RATE = 10_048_780
EXPOSURE = 4_850_000_000


def _book(conn: sqlite3.Connection):
    client = orgs.create(conn, kind="client", name="Legibility Inc", status="active")
    placement = placements.create(
        conn,
        org_id=client.id,
        program_name="2027 casualty",
        period_from="2027-09-01",
        period_to="2028-09-01",
    )
    marketing.set_placement_line(
        conn,
        placement.id,
        "general-liability",
        expiring_premium=EXPIRING_PREMIUM,
        expiring_exposure=EXPIRING_EXPOSURE,
        expiring_rate_micros=EXPIRING_RATE,
        expiring_basis="gross_sales",
        expected_exposure=EXPOSURE,
        rating_basis="gross_sales",
        rate_per=1000,
        limit_sought=100_000_000,
    )
    return client, placement


def _market(conn, name, best=None):
    org = orgs.create(conn, kind="market", name=name, status="active")
    if best:
        conn.execute(
            "INSERT INTO market_profile (org_id, am_best_rating, market_type)"
            " VALUES (?, ?, 'carrier')",
            (org.id, best),
        )
    return org


def _approach(conn, placement_id, market, **fields):
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2027-07-07", placement_id=placement_id
    )
    return marketing.create_response(
        conn, sub.id, "general-liability", market_org_id=market.id, **fields
    )


def _full_book(conn):
    client, placement = _book(conn)
    travelers = _market(conn, "Travelers", "A++ XV")
    cna = _market(conn, "CNA", "A XV")
    chubb = _market(conn, "Chubb", "A++ XV")
    zurich = _market(conn, "Zurich", "A+ XV")
    liberty = _market(conn, "Liberty Mutual", "A XV")
    aig = _market(conn, "AIG", "A XV")
    rt = _market(conn, "RT Specialty")

    _approach(
        conn, placement.id, travelers, status="quoted", responded_on="2027-07-21",
        rate_micros=8_100_000, premium=39_285_000, tria_premium=785_000,
        policy_fees=390_000, surplus_lines_tax=0,
    )
    sub_cna = submissions.create(
        conn, market_org_id=cna.id, sent_on="2027-07-07", placement_id=placement.id
    )
    marketing.create_response(
        conn, sub_cna.id, "general-liability", market_org_id=cna.id, via_org_id=rt.id,
        status="quoted", responded_on="2027-07-24", rate_micros=7_950_000,
        premium=38_557_500, tria_premium=771_000, policy_fees=40_000,
        surplus_lines_tax=1_928_000,
    )
    _approach(
        conn, placement.id, chubb, status="indicated", responded_on="2027-07-18",
        rate_micros=7_900_000, premium=38_315_000,
    )
    _approach(conn, placement.id, zurich, status="pending")
    _approach(
        conn, placement.id, liberty, status="declined", responded_on="2027-07-15",
        decline_reason="underwriter hates the loss runs, off the record",
        decline_reason_public="loss_history",
    )
    _approach(conn, placement.id, aig, status="non_response")
    return placement


def test_the_block_reads_live_options_first(conn) -> None:
    placement = _full_book(conn)
    report = marketing_report.compose(conn, placement.id, TODAY)
    block = report.blocks[0]
    assert block.line_name == "General Liability"
    assert [row.status for row in block.rows] == [
        "Quoted", "Quoted", "Indicated", "Pending", "Declined", "Non-response",
    ]
    # Within Quoted, cheapest first.
    assert [row.market for row in block.rows[:2]] == ["CNA", "Travelers"]


def test_a_wholesaler_is_named_beside_the_paper(conn) -> None:
    placement = _full_book(conn)
    block = marketing_report.compose(conn, placement.id, TODAY).blocks[0]
    cna = next(row for row in block.rows if row.market == "CNA")
    assert cna.market_cell == "CNA (via RT Specialty)"


def test_the_rate_change_is_what_the_broker_achieved(conn) -> None:
    """Premium fell 4.6%; the RATE fell 19.4% while the client's sales grew
    18.3%. A premium-only report tells the client the wrong story."""
    placement = _full_book(conn)
    block = marketing_report.compose(conn, placement.id, TODAY).blocks[0]
    travelers = next(row for row in block.rows if row.market == "Travelers")
    assert travelers.rate_move.pct is not None
    assert round(travelers.rate_move.pct, 1) == -19.4
    assert travelers.rate_move.cell == "-19.4%"
    assert block.exposure_change_pct is not None
    assert round(block.exposure_change_pct, 1) == 18.3


def test_the_bridge_splits_rate_from_growth_and_reconciles(conn) -> None:
    placement = _full_book(conn)
    block = marketing_report.compose(conn, placement.id, TODAY).blocks[0]
    bridge = block.bridge
    assert bridge is not None
    assert bridge.expiring_premium == EXPIRING_PREMIUM
    # The LEADING quote is CNA, cheapest premium among the live options.
    assert bridge.market.startswith("CNA")
    assert round(bridge.rate_effect / 100) == -86_050       # dollars
    assert round(bridge.exposure_effect / 100) == 59_625
    walked = bridge.expiring_premium + bridge.rate_effect + bridge.exposure_effect
    assert abs(walked - bridge.quoted_premium) <= 100  # within a dollar


def test_a_total_cost_appears_only_where_every_component_is_known(conn) -> None:
    """THE CHEAPER PREMIUM IS THE DEARER PLACEMENT. CNA quotes $385,575
    against Travelers' $392,850 — and lands $8,365 higher once its surplus
    lines tax is on, because it is E&S paper through a wholesaler. A premium
    column alone recommends the wrong market. Chubb has given an indication
    and nothing else, so its total is BLANK, not zero."""
    placement = _full_book(conn)
    block = marketing_report.compose(conn, placement.id, TODAY).blocks[0]
    rows = {row.market: row for row in block.rows}
    cna, travelers, chubb = rows["CNA"], rows["Travelers"], rows["Chubb"]

    assert cna.premium == 38_557_500 and travelers.premium == 39_285_000
    assert cna.premium < travelers.premium          # cheaper on the quote
    assert cna.total_cost == 41_296_500             # $412,965
    assert travelers.total_cost == 40_460_000       # $404,600
    assert cna.total_cost > travelers.total_cost    # dearer on the money paid
    assert chubb.total_cost is None


def test_a_rate_change_across_two_bases_is_refused_in_words(conn) -> None:
    """Last year on payroll, this year on sales. A percentage here would be
    the same lie as ranking two carriers rated on different bases."""
    _, placement = _book(conn)
    marketing.set_placement_line(
        conn, placement.id, "general-liability", expiring_basis="payroll"
    )
    market = _market(conn, "Travelers")
    _approach(conn, placement.id, market, status="quoted", rate_micros=8_100_000,
              premium=39_285_000)
    block = marketing_report.compose(conn, placement.id, TODAY).blocks[0]
    assert block.rows[0].rate_move.pct is None
    assert block.rows[0].rate_move.cell == "basis changed"


def test_a_missing_expiring_rate_leaves_the_comparison_blank(conn) -> None:
    """Most of the book starts here. Assuming exposure was flat would put a
    premium change in the rate column wearing a rate's clothes."""
    client = orgs.create(conn, kind="client", name="No History Co", status="active")
    placement = placements.create(
        conn, org_id=client.id, program_name="2027", period_from="2027-01-01",
        period_to="2028-01-01",
    )
    marketing.set_placement_line(
        conn, placement.id, "general-liability", expiring_premium=41_200_000
    )
    market = _market(conn, "Travelers")
    _approach(conn, placement.id, market, status="quoted", rate_micros=8_100_000,
              premium=39_285_000)
    block = marketing_report.compose(conn, placement.id, TODAY).blocks[0]
    assert block.rows[0].rate_move.cell == "no expiring rate recorded"
    assert block.bridge is None


def test_the_client_sheet_withholds_what_the_internal_one_shows(conn) -> None:
    placement = _full_book(conn)
    client_report = marketing_report.compose(conn, placement.id, TODAY)
    internal = marketing_report.compose(conn, placement.id, TODAY, audience="internal")

    liberty_client = next(r for r in client_report.blocks[0].rows if r.market == "Liberty Mutual")
    liberty_internal = next(r for r in internal.blocks[0].rows if r.market == "Liberty Mutual")

    assert liberty_client.public_reason == "Loss history"
    assert liberty_client.internal_reason == ""
    assert "off the record" in liberty_internal.internal_reason

    client_text = "\n".join(
        "\t".join(cells)
        for section in marketing_report.to_sections(client_report)
        for cells in section.rows
    )
    assert "off the record" not in client_text


def test_a_line_with_no_approaches_says_so_in_words(conn) -> None:
    """An empty table is ambiguous: a client cannot tell a line nobody has
    gone to market on from a rendering bug."""
    _, placement = _book(conn)
    marketing.set_placement_line(conn, placement.id, "auto")
    report = marketing_report.compose(conn, placement.id, TODAY)
    sections = marketing_report.to_sections(report)
    auto = next(s for s in sections if s.label.startswith("Commercial Auto"))
    assert "No markets approached" in auto.rows[0][0]


def test_the_section_label_carries_the_header_facts(conn) -> None:
    placement = _full_book(conn)
    report = marketing_report.compose(conn, placement.id, TODAY)
    label = marketing_report.to_sections(report)[0].label
    assert "General Liability" in label
    assert "submitted 7 Jul" in label
    assert "Gross sales" in label
    assert "+18.3%" in label
    assert "expiring $412,000.00 at 10.05" in label or "expiring $412,000 at 10.05" in label
