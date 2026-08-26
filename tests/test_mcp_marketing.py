"""The six marketing tools on the MCP surface.

A SCHEMA CHANGE IS NOT DONE UNTIL AN AGENT CAN SEE IT, and these are the
assertions that say it can. Each one covers a rule that lives in repo/ or
services/ and would be silently lost if this surface re-implemented it or
swallowed it: the near-match warning, the duplicate refusal, the
carrier-or-intermediary CHECK, the clearance warning, the submission roll-up
and the composed report itself.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bookkit import mcpserver
from bookkit.repo import lines as lines_repo
from bookkit.repo import marketing, orgs, placements, submissions


def _book(conn: sqlite3.Connection):
    client = orgs.create(conn, kind="client", name="Legibility Inc", status="active")
    placement = placements.create(
        conn,
        org_id=client.id,
        program_name="2027 casualty",
        period_from="2027-01-01",
        period_to="2028-01-01",
    )
    return client, placement


def _market(conn: sqlite3.Connection, name: str):
    return orgs.create(conn, kind="market", name=name, status="active")


# --- the vocabulary --------------------------------------------------------


def test_a_near_match_reaches_the_reply_rather_than_only_the_refusal(conn) -> None:
    """repo/lines.py deliberately does NOT refuse a near match — Excess
    Liability and Employers Liability are four letters apart and are not the
    same line. A human sees the warning beside the field; an assistant sees it
    only if the tool hands it back, and an assistant that never sees it is
    exactly how a fifth spelling of General Liability gets in."""
    out = mcpserver._line_add(conn, "General Liabilty")

    assert out["line_id"] != "general-liability"          # it was written
    names = [m["name"] for m in out["near_matches"]]
    assert "General Liability" in names, out["near_matches"]
    assert out["near_matches"][0]["score"] >= lines_repo.NEAR_MATCH_CUTOFF


def test_a_duplicate_line_is_refused_by_naming_the_one_that_exists(conn) -> None:
    """A refusal says something: repo's DuplicateLine carries the existing row
    precisely so the caller can offer to USE it, and a bare KeyError-shaped
    'already exists' would throw that away."""
    with pytest.raises(ValueError) as err:
        mcpserver._line_add(conn, "general liability")

    assert "General Liability" in str(err.value)
    assert "general-liability" in str(err.value)
    # nothing was written, and the batch rolled back with it
    assert len(lines_repo.all_lines(conn)) == 17


def test_lines_list_is_the_whole_vocabulary_in_reading_order(conn) -> None:
    listed = mcpserver._lines_list(conn)
    assert [row["line_id"] for row in listed][:2] == ["general-liability", "auto"]
    assert listed[0]["abbr"] == "GL"


def test_an_unknown_line_refuses_with_the_nearest_and_writes_nothing(conn) -> None:
    _, placement = _book(conn)
    _market(conn, "Travelers")
    with pytest.raises(ValueError) as err:
        mcpserver._market_approach(
            conn, placement.ref, "Genral Liability", market="Travelers"
        )
    assert "General Liability" in str(err.value)
    assert "lines_list" in str(err.value)
    assert marketing.responses_for_placement(conn, placement.id) == []


# --- approaching -----------------------------------------------------------


def test_an_approach_through_a_wholesaler_alone_is_a_real_row(conn) -> None:
    """"Out to RT Specialty, carrier TBD" is the truth rather than a gap, and
    the tool must not invent a carrier to satisfy its own arguments."""
    _, placement = _book(conn)
    _market(conn, "RT Specialty")

    out = mcpserver._market_approach(
        conn, placement.ref, "GL", via="RT Specialty", sent_on="2026-07-07"
    )

    assert out["market"] is None
    assert out["via"] == "RT Specialty"
    assert out["status"] == "pending"
    response = marketing.get_response(conn, out["response_id"])
    assert response.market_org_id is None
    # the package went to the intermediary, because that is who it went to
    assert submissions.get(conn, out["submission_id"]).market_org_id == orgs.find_by_name(
        conn, "RT Specialty"
    ).id


def test_an_approach_with_neither_carrier_nor_intermediary_is_refused(conn) -> None:
    _, placement = _book(conn)
    with pytest.raises(ValueError, match="carrier"):
        mcpserver._market_approach(conn, placement.ref, "GL")


def test_a_second_line_to_the_same_market_reuses_the_submission(conn) -> None:
    """One submission goes out carrying every line; the responses hang off it.
    Filing a second submission per line would make "who did we approach"
    unanswerable, which is what migration 015 exists to prevent."""
    _, placement = _book(conn)
    _market(conn, "Travelers")

    first = mcpserver._market_approach(conn, placement.ref, "GL", market="Travelers")
    second = mcpserver._market_approach(conn, placement.ref, "AL", market="Travelers")

    assert first["submission_is_new"] is True
    assert second["submission_is_new"] is False
    assert second["submission_id"] == first["submission_id"]
    assert len(submissions.for_placement(conn, placement.id)) == 1


def test_a_clearance_conflict_is_warned_and_never_refused(conn) -> None:
    """Two wholesalers reaching the same carrier on the same line is the
    collision that gets one of them shut out — and it is sometimes deliberate.
    A refusal would make the legitimate double approach impossible, so this is
    the `line-gap` rule: reported, not refused."""
    _, placement = _book(conn)
    _market(conn, "CNA")
    _market(conn, "RT Specialty")
    _market(conn, "Amwins")

    mcpserver._market_approach(
        conn, placement.ref, "GL", market="CNA", via="RT Specialty"
    )
    second = mcpserver._market_approach(
        conn, placement.ref, "GL", market="CNA", via="Amwins"
    )

    assert second["clearance_warnings"], "the collision was not surfaced"
    assert "RT Specialty" in second["clearance_warnings"][0]
    # and the write happened anyway
    assert marketing.get_response(conn, second["response_id"]).market_org_id


# --- what the market said --------------------------------------------------


def test_a_response_rolls_the_submissions_status_up(conn) -> None:
    """The submission's status is DERIVED from its response rows and is never
    typed a second time. The rolled-up value comes back in the reply so the
    caller can see what its edit did to the parent."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    # THE LINE'S DENOMINATOR FIRST. A rate is stamped with the denominator it
    # was typed against and refused when there is none to stamp — this test
    # records `rate="1.42"`, and 1.42 per $100 is ten times 1.42 per $1,000.
    mcpserver._set_placement_line(conn, placement.ref, "GL", rate_per="100")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    assert submissions.get(conn, approach["submission_id"]).status == "out"

    out = mcpserver._market_responded(
        conn, approach["response_id"], status="quoted",
        # A REPLY IS NOT IN THE FUTURE. This book's period runs through 2027
        # and the fixture dated the reply inside it, which is a year past the
        # wall clock — and `marketing_entry.responded` now refuses that
        # everywhere, because parse_human_date future-biases a bare month and
        # day and the year is the thing that goes wrong (D2, 2026-08-26).
        responded_on="2026-07-20", rate="1.42", premium="120,000", fees="2,500",
    )

    assert out["status"] == "quoted"
    assert out["submission_status"] == "quoted"
    assert submissions.get(conn, approach["submission_id"]).status == "quoted"
    response = marketing.get_response(conn, approach["response_id"])
    assert response.rate_micros == 1_420_000     # a rate is not money
    assert response.premium == 12_000_000        # dollars in, cents stored
    assert response.policy_fees == 250_000


def test_a_rate_is_not_read_through_the_money_parser(conn) -> None:
    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers"
    )
    with pytest.raises(ValueError, match="rate like"):
        mcpserver._market_responded(conn, approach["response_id"], rate="$1.42")


def test_an_unknown_public_decline_reason_is_refused_with_the_list(conn) -> None:
    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers"
    )
    with pytest.raises(ValueError, match="decline_reason_public"):
        mcpserver._market_responded(
            conn, approach["response_id"], status="declined",
            decline_reason_public="they hate us",
        )


def test_an_unknown_response_ref_names_where_a_real_one_comes_from(conn) -> None:
    with pytest.raises(ValueError, match="marketing_report"):
        mcpserver._market_responded(conn, "NOPE-1", status="quoted")


# --- what the line is expected to do ---------------------------------------


def test_an_exposure_without_a_basis_is_refused_rather_than_guessed(conn) -> None:
    """42 power units and $0.42 are the same digits, and a client-facing
    report renders them a hundred-thousandfold apart. models.RatingBasis
    .monetary is the one place that decides, so the figure is refused until
    it can be read there."""
    _, placement = _book(conn)
    with pytest.raises(ValueError, match="rating basis"):
        mcpserver._set_placement_line(
            conn, placement.ref, "GL", expected_exposure="48,500,000"
        )


def test_a_non_monetary_exposure_is_stored_as_a_count(conn) -> None:
    _, placement = _book(conn)
    out = mcpserver._set_placement_line(
        conn, placement.ref, "AL", rating_basis="power_units",
        expected_exposure="42", rate_per="1",
    )
    assert out["expected_exposure"] == 42        # not 4200 cents


def test_set_placement_line_upserts_one_row_per_line(conn) -> None:
    _, placement = _book(conn)
    mcpserver._set_placement_line(
        conn, placement.ref, "GL", rating_basis="gross_sales",
        expiring_premium="100,000",
    )
    out = mcpserver._set_placement_line(
        conn, placement.ref, "GL", expected_exposure="48,500,000",
        # WITH ITS DENOMINATOR: an expiring rate is refused while the line has
        # none, in the same call or already stored (D4's adjacent site). This
        # test is about the upsert, and the pair is what makes the row
        # writable at all.
        expiring_rate="1.61", rate_per="1000",
    )
    assert out["expiring_premium"] == 10_000_000     # the first call survived
    assert out["expected_exposure"] == 4_850_000_000
    assert out["expiring_rate_micros"] == 1_610_000
    assert len(marketing.placement_lines(conn, placement.id)) == 1


# --- the report ------------------------------------------------------------


def _a_marketed_line(conn):
    client, placement = _book(conn)
    _market(conn, "Travelers")
    mcpserver._set_placement_line(
        conn, placement.ref, "GL", rating_basis="gross_sales", rate_per="1000",
        expiring_premium="100,000", expiring_exposure="48,500,000",
        expiring_rate="2.06", expiring_basis="gross_sales",
        expected_exposure="52,000,000",
    )
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    mcpserver._market_responded(
        conn, approach["response_id"], status="quoted",
        responded_on="2026-07-20", rate="2.31", premium="120,120",
    )
    return placement, approach


def test_the_report_text_carries_a_block_for_the_line(conn) -> None:
    placement, _ = _a_marketed_line(conn)

    out = mcpserver._marketing_report(conn, placement.ref, as_of="2026-07-21")

    text = out["report"]
    assert "General Liability" in text, text
    assert "Travelers" in text
    assert "Quoted" in text
    assert "$120,120" in text
    assert out["as_of"] == "2026-07-21"


def test_the_report_indexes_the_ids_market_responded_takes(conn) -> None:
    """A composed row carries no id, so without this index there is no way to
    name the row to update — the report would be a dead end."""
    placement, approach = _a_marketed_line(conn)

    out = mcpserver._marketing_report(conn, placement.ref)

    assert [r["response_id"] for r in out["responses"]] == [approach["response_id"]]
    assert out["responses"][0]["market"] == "Travelers"
    assert out["responses"][0]["line"] == "General Liability"


def test_the_client_report_withholds_what_only_the_internal_one_prints(conn) -> None:
    placement, approach = _a_marketed_line(conn)
    mcpserver._market_responded(
        conn, approach["response_id"],
        decline_reason="underwriter doesn't like the loss runs, off the record",
    )

    client = mcpserver._marketing_report(conn, placement.ref, audience="client")
    internal = mcpserver._marketing_report(conn, placement.ref, audience="internal")

    assert "off the record" not in client["report"]
    assert "off the record" in internal["report"]


def test_an_unknown_audience_is_refused_with_the_list(conn) -> None:
    _, placement = _book(conn)
    with pytest.raises(ValueError, match="audience must be one of"):
        mcpserver._marketing_report(conn, placement.ref, audience="underwriter")


def test_the_report_never_reads_the_wall_clock_below_this_tool(conn) -> None:
    """`today` is a parameter all the way down: two runs of the same as_of
    agree, and the default is applied HERE and named in the reply."""
    placement, _ = _a_marketed_line(conn)

    first = mcpserver._marketing_report(conn, placement.ref, as_of="2026-07-21")
    second = mcpserver._marketing_report(conn, placement.ref, as_of="2026-07-21")
    assert first["report"] == second["report"]

    defaulted = mcpserver._marketing_report(conn, placement.ref)
    assert defaulted["as_of"] == date.today().isoformat()


def test_a_withdrawn_submission_is_not_reused_by_a_later_approach(conn) -> None:
    """We pulled that package. repo/marketing's roll-up never writes over
    `withdrawn` and never un-withdraws one, so a response hung off it would be
    permanently mis-stated — and going back to a market we withdrew from is a
    new submission in the world too."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    first = mcpserver._market_approach(conn, placement.ref, "GL", market="Travelers")
    submissions.update(conn, first["submission_id"], status="withdrawn")

    second = mcpserver._market_approach(conn, placement.ref, "AL", market="Travelers")

    assert second["submission_is_new"] is True
    assert second["submission_id"] != first["submission_id"]
    assert submissions.get(conn, second["submission_id"]).status == "out"
