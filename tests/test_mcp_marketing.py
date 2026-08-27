"""The nine marketing tools on the MCP surface.

A SCHEMA CHANGE IS NOT DONE UNTIL AN AGENT CAN SEE IT, and these are the
assertions that say it can. Each one covers a rule that lives in repo/ or
services/ and would be silently lost if this surface re-implemented it or
swallowed it: the near-match warning, the duplicate refusal, the
carrier-or-intermediary CHECK, the clearance warning, the submission roll-up,
the composed report itself, and the packages in it that no line of coverage
speaks for yet.
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


def test_the_assistant_can_say_when_a_quote_dies(conn) -> None:
    """A SCHEMA CHANGE IS NOT DONE UNTIL AN AGENT CAN SEE IT (CLAUDE.md). The
    expiry is the date `services.quotes` keys the whole chase queue on, so a
    quote the assistant records without one is on no clock at all."""
    from bookkit.repo import marketing, submissions

    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    out = mcpserver._market_responded(
        conn, approach["response_id"], status="quoted",
        responded_on="2026-07-20", premium="120,000",
        quote_expires_on="2026-09-04",
    )
    assert out["quote_expires_on"] == "2026-09-04"
    # …AND CAN READ IT BACK. A tool that can write a fact the assistant can
    # never see again is half a link in the chain (CLAUDE.md).
    report = mcpserver._marketing_report(conn, placement.ref)
    assert [r["quote_expires_on"] for r in report["responses"]] == ["2026-09-04"]
    assert marketing.get_response(
        conn, approach["response_id"]
    ).quote_expires_on == "2026-09-04"
    # …and the submission is the roll-up of it, which is what the queue reads.
    rolled = submissions.get(conn, approach["submission_id"])
    assert rolled.quote_expires_on == "2026-09-04"
    assert rolled.quoted_premium == 12_000_000


def test_an_expiry_before_the_reply_is_refused_on_mcp_too(conn) -> None:
    """The guard is in repo/, where every surface inherits it — a rule beside
    one door is a rule the other writes past."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    with pytest.raises(ValueError, match="cannot lapse before"):
        mcpserver._market_responded(
            conn, approach["response_id"], status="quoted",
            responded_on="2026-07-20", quote_expires_on="2026-07-10",
        )


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
    # A TYPED RATE IS NOT A DERIVED ONE, and the tool says which — an
    # assistant reading this back has to be able to tell a figure off a
    # policy from a division the book did.
    assert out["expiring_rate_derived"] is False


def test_the_assistant_can_write_the_note_the_client_reads(conn) -> None:
    """A SCHEMA CHANGE IS NOT DONE UNTIL AN AGENT CAN SEE IT (CLAUDE.md). The
    note prints under the line's heading on the client's own workbook, so a
    tool that could not write or read it would leave the assistant unable to
    say what the broker just said in the browser."""
    _, placement = _book(conn)
    note = "TIV excludes the Ohio site, added mid-term."
    out = mcpserver._set_placement_line(
        conn, placement.ref, "GL", client_note=note
    )
    assert out["client_note"] == note
    # cleared the way every other nullable text field on this server is
    assert mcpserver._set_placement_line(
        conn, placement.ref, "GL", client_note=""
    )["client_note"] is None


def test_the_tool_answers_with_the_composite_rate_the_browser_shows(conn) -> None:
    """A SCHEMA CHANGE IS NOT DONE UNTIL AN AGENT CAN SEE IT (CLAUDE.md).

    The browser's header works the expiring rate out of the premium and the
    exposure; a tool that answered with the raw column would tell Grant there
    is no expiring rate on a line whose Rate delta is being computed off one
    two screens away. $412,000 over $41.0M per $1,000 is 10.0488."""
    _, placement = _book(conn)
    out = mcpserver._set_placement_line(
        conn, placement.ref, "GL", rating_basis="gross_sales", rate_per="1000",
        expiring_basis="gross_sales", expiring_premium="412,000",
        expiring_exposure="41,000,000",
    )
    assert out["expiring_rate_micros"] == 10_048_780
    assert out["expiring_rate_derived"] is True
    # NOTHING WAS STORED. The column stays empty and the figure is worked out
    # on every read, so correcting the premium corrects the rate.
    assert marketing.placement_line(
        conn, placement.id, "general-liability"
    ).expiring_rate_micros is None


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


# --- the marketing that has no line of coverage yet -------------------------
#
# A change that lands on the web and not on MCP has shipped to two thirds of
# its users. The provisional block is READ by the assistant unaided (it is a
# section of the composed report), so what these hold is the other two links:
# the index that gives it an id to name, and the verb that writes.


def _bare_package(conn, placement, market_name="Sompo", **fields):
    market = _market(conn, market_name)
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2027-07-07",
        placement_id=placement.id,
    )
    if fields:
        submissions.update(conn, sub.id, **fields)
    return market, submissions.get(conn, sub.id)


def test_the_report_shows_a_package_with_no_line_and_names_it(conn) -> None:
    """THE ASSISTANT CAN SEE IT AND CAN NAME IT.

    THE INDEX IS THE LOAD-BEARING HALF, and it is asserted on BOTH audiences.
    The rendered text is whichever sheet was asked for, and since 2026-08-27
    the client's does not carry this block (Grant: "remove the 'line of
    coverage not recorded' from the client deliverable") — so an assistant
    reading the default report would see nothing to fix, and if the ids went
    with the words it would have no ref for `market_assign_line` or
    `submission_remove` either. That is the half-a-link CLAUDE.md says a change
    is not done without, and the index is what keeps it whole: `provisional` is
    composed the same way for both audiences and only the RENDERING withholds
    it.
    """
    _, placement = _book(conn)
    _, package = _bare_package(
        conn, placement, quoted_premium=140_000_000, status="quoted",
    )

    client = mcpserver._marketing_report(conn, placement.ref)
    internal = mcpserver._marketing_report(conn, placement.ref, "internal")

    # THE WORDS: the broker's copy carries the block, the client's does not —
    # the same split the .xlsx makes, from the same composer.
    assert "Line of coverage not recorded" in internal["report"]
    assert "Sompo" in internal["report"]
    assert "Line of coverage not recorded" not in client["report"]

    # THE IDS: on both, or a verb addressed to one of these packages has
    # nothing to take.
    for out in (client, internal):
        named = out["submissions_with_no_line"]
        assert [row["submission_id"] for row in named] == [package.id]
        assert named[0]["market"] == "Sompo"
        assert named[0]["quoted_premium"] == 140_000_000


def test_assigning_a_line_over_mcp_writes_the_response_and_batches_it(conn) -> None:
    _, placement = _book(conn)
    market, package = _bare_package(
        conn, placement, quoted_premium=140_000_000, quoted_limit=5_000_000_000,
        response_on="2027-07-20", status="quoted",
    )

    out = mcpserver._market_assign_line(conn, package.id, "General Liability")

    assert out["line"] == "General Liability"
    assert out["batch"], "the write is not revertible in one act"
    rows = marketing.responses_for_submission(conn, package.id)
    assert len(rows) == 1 and rows[0].id == out["response_id"]
    assert rows[0].premium == 140_000_000
    assert rows[0].market_org_id == market.id
    # The package reads exactly as it read before — every status maps to one
    # that rolls back up to itself.
    assert out["submission_status"] == "quoted"
    assert out["quoted_premium"] == 140_000_000

    # AND IT LEAVES THE BLOCK. The index is what the assistant reads to know
    # there is nothing left to do here.
    assert mcpserver._marketing_report(conn, placement.ref)[
        "submissions_with_no_line"
    ] == []


def test_a_submission_ref_is_exact_and_the_refusal_names_where_one_comes_from(
    conn,
) -> None:
    """Exact id only — a write target is never fuzzy-matched, and the refusal
    names the index the id comes out of, the way `_resolve_response`'s does."""
    with pytest.raises(ValueError) as err:
        mcpserver._market_assign_line(conn, "not-an-id", "General Liability")

    assert "submissions_with_no_line" in str(err.value)


def test_an_unknown_line_names_the_nearest_and_writes_nothing(conn) -> None:
    _, placement = _book(conn)
    _, package = _bare_package(conn, placement)

    with pytest.raises(ValueError, match="no line of coverage matching"):
        mcpserver._market_assign_line(conn, package.id, "General Liabilty Excess")

    assert not marketing.responses_for_submission(conn, package.id)


def test_an_opportunity_s_package_is_sent_to_the_form_that_asks_the_question(
    conn,
) -> None:
    """A REFUSAL NAMES A FIX THAT EXISTS. There is no marketing panel and no
    `placement_line` behind an opportunity, so the line is asked for on the
    Pipeline's own Response form — which is where the refusal points."""
    from bookkit.repo import opportunities

    client, _ = _book(conn)
    opp = opportunities.create(conn, client.id, "New logo")
    market = _market(conn, "Amwins")
    package = submissions.create(
        conn, market_org_id=market.id, sent_on="2027-07-07", opportunity_id=opp.id,
    )

    with pytest.raises(ValueError, match="on an opportunity rather than a placement"):
        mcpserver._market_assign_line(conn, package.id, "General Liability")


# --- and undoing one of them -----------------------------------------------


def test_revert_batch_leaves_the_submission_equal_to_its_rows(conn) -> None:
    """THE ASSISTANT HITS IT TOO. `revert_batch` and the browser's Undo link
    are the same service call, so a revert that replayed the submission's
    derived columns backwards instead of recomputing them from the surviving
    rows made the assistant report "out at market" about a package one of whose
    markets is quoted — with `applied: true` and nothing refused.

    Asserted through the TOOL rather than the service so this says the MCP
    surface reaches the fix, which a green service test does not."""
    from bookkit import db

    _, placement = _book(conn)
    _market(conn, "Travelers")
    first = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    second = mcpserver._market_approach(
        conn, placement.ref, "AL", market="Travelers", sent_on="2026-07-07"
    )
    assert second["submission_id"] == first["submission_id"], (
        "one market, one day: the package is reused and this test needs two rows on it"
    )

    quoted = mcpserver._market_responded(
        conn, second["response_id"], status="quoted", responded_on="2026-07-20",
        premium="120,000", quote_expires_on="2026-09-04",
    )
    mcpserver._market_responded(
        conn, first["response_id"], status="quoted", responded_on="2026-07-18",
    )

    out = mcpserver._revert_batch(conn, quoted["batch"], now=db.utc_now())
    assert out["applied"] and not out["refused"], out

    sub_id = first["submission_id"]
    rows = {r.line_id: r.status for r in marketing.responses_for_submission(conn, sub_id)}
    assert rows == {"general-liability": "quoted", "auto": "pending"}, rows
    assert str(submissions.get(conn, sub_id).status) == "quoted"

    derived = ("status", "quoted_premium", "quoted_limit", "response_on",
               "quote_expires_on", "decline_reason")
    row = conn.execute("SELECT * FROM submission WHERE id = ?", (sub_id,)).fetchone()
    before = {f: row[f] for f in derived}
    marketing.roll_up_submission(conn, sub_id)
    row = conn.execute("SELECT * FROM submission WHERE id = ?", (sub_id,)).fetchone()
    assert {f: row[f] for f in derived} == before, (
        "the six columns the assistant reads disagree with the response rows"
    )
    assert before["response_on"] == "2026-07-18", (
        "the surviving reply is the answer; the reverted one is not"
    )


# --- pulling a package, and putting it back --------------------------------


def test_the_assistant_can_withdraw_a_package_and_put_it_back(conn) -> None:
    """L2, ON THE SURFACE THAT HAD NOTHING AT ALL. `_edit_field` refuses kind
    'submission' and no `submission_*` tool wrote a status, so when the
    Pipeline's Response form stopped offering the submission vocabulary the
    withdraw capability left the product entirely — a state three code paths
    refuse on and nothing can enter.

    THE IDS ARE REACHABLE, which is the other half of the same rule: a
    submission has no ref of its own, so `marketing_report` has to hand one
    back or these verbs have nothing to take."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    sub_id = approach["submission_id"]

    report = mcpserver._marketing_report(conn, placement.ref, audience="internal")
    assert sub_id in {r["submission_id"] for r in report["responses"]}, (
        "no index hands back a submission id, so neither verb has a ref to take"
    )

    pulled = mcpserver._submission_withdraw(conn, sub_id)
    assert pulled["status"] == "withdrawn"
    assert pulled["responses_kept"] == 1
    assert str(submissions.get(conn, sub_id).status) == "withdrawn"
    # WHAT THE MARKET SAID IS UNTOUCHED
    assert len(marketing.responses_for_submission(conn, sub_id)) == 1

    back = mcpserver._submission_reinstate(conn, sub_id)
    assert back["status"] == "out"
    assert str(submissions.get(conn, sub_id).status) == "out"


def test_reinstating_reads_the_rows_rather_than_defaulting_to_out(conn) -> None:
    """A package pulled while a market was quoting comes back QUOTED, with its
    premium and expiry recomputed — `repo.marketing.status_from_rows` is the
    one home for that ladder and both surfaces read it."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    mcpserver._market_responded(
        conn, approach["response_id"], status="quoted", responded_on="2026-07-20",
        premium="120,000", quote_expires_on="2026-09-04",
    )
    sub_id = approach["submission_id"]
    mcpserver._submission_withdraw(conn, sub_id)

    back = mcpserver._submission_reinstate(conn, sub_id)
    assert back["status"] == "quoted", "a package holding a quote came back unanswered"
    assert back["quoted_premium"] == 12_000_000
    assert back["quote_expires_on"] == "2026-09-04"


def test_both_refusals_name_the_other_verb(conn) -> None:
    """A REFUSAL SAYS SOMETHING, and the same sentence reaches both surfaces
    because the rule is services.marketing_entry's rather than each door's."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    sub_id = approach["submission_id"]

    with pytest.raises(ValueError, match="not withdrawn"):
        mcpserver._submission_reinstate(conn, sub_id)

    mcpserver._submission_withdraw(conn, sub_id)
    with pytest.raises(ValueError, match="already withdrawn"):
        mcpserver._submission_withdraw(conn, sub_id)

    with pytest.raises(ValueError, match="submissions_with_no_line"):
        mcpserver._submission_withdraw(conn, "no-such-submission")


def test_a_withdrawn_package_is_not_reused_by_a_later_approach(conn) -> None:
    """THE STATE HAS A SUBJECT AGAIN. `marketing_entry.approach` refuses to
    hang a new approach off a withdrawn package and opens a fresh one — a rule
    that had been unreachable since nothing could withdraw anything."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    first = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    mcpserver._submission_withdraw(conn, first["submission_id"])
    again = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    assert again["submission_id"] != first["submission_id"]
    assert again["submission_is_new"] is True


def test_reinstating_recomputes_figures_the_roll_up_refused_to_touch(conn) -> None:
    """THE ROLL-UP GOES SILENT WHILE A PACKAGE IS WITHDRAWN, deliberately — so
    that editing a stale response cannot quietly un-withdraw one. The cost is
    that every response write made in the meantime leaves the submission's five
    cached figures stating what the rows USED to say, and nothing else ever
    puts them right: `roll_up_submission` refuses on the withdrawn row for as
    long as it is withdrawn. Reinstating is the moment the row can speak again,
    and it has to recompute them on the way back or the Pipeline prints a
    premium no surviving row states."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    sub_id = approach["submission_id"]
    mcpserver._market_responded(
        conn, approach["response_id"], status="quoted", responded_on="2026-07-20",
        premium="120,000",
    )
    mcpserver._submission_withdraw(conn, sub_id)

    # corrected while pulled: the ROW moves, the cache cannot
    mcpserver._market_responded(conn, approach["response_id"], premium="98,000")
    assert submissions.get(conn, sub_id).quoted_premium == 12_000_000, (
        "the roll-up spoke about a withdrawn package; this test needs it silent"
    )

    back = mcpserver._submission_reinstate(conn, sub_id)
    assert back["quoted_premium"] == 9_800_000, (
        "the package came back stating a premium no row says"
    )
    assert submissions.get(conn, sub_id).quoted_premium == 9_800_000


# --- how we reached the paper, on the agent's surface ----------------------


def test_the_assistant_can_turn_an_approach_back_into_a_direct_one(conn) -> None:
    """A SCHEMA CHANGE IS NOT DONE UNTIL AN AGENT CAN SEE IT (CLAUDE.md), and
    this correction had no door on ANY surface: not a cell, not a form, not an
    MCP argument. AN EMPTY STRING IS THE ANSWER — a wholesaler recorded on an
    approach that in fact went straight to the carrier is cleaned up by saying
    there is no intermediary, and there is no other word for that."""
    _, placement = _book(conn)
    _market(conn, "Zurich")
    _market(conn, "RT Specialty")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL",
        market="Zurich", via="RT Specialty", sent_on="2026-07-07",
    )
    assert marketing.get_response(conn, approach["response_id"]).via_org_id

    mcpserver._market_responded(conn, approach["response_id"], via="")

    assert marketing.get_response(conn, approach["response_id"]).via_org_id is None
    # …AND CAN READ IT BACK. A tool that writes a fact the assistant can never
    # see again is half a link in the chain.
    report = mcpserver._marketing_report(conn, placement.ref)
    assert [r["via"] for r in report["responses"]] == [None]


def test_the_assistant_can_name_a_different_access_point(conn) -> None:
    _, placement = _book(conn)
    _market(conn, "Zurich")
    _market(conn, "RT Specialty")
    amwins = _market(conn, "Amwins Brokerage")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL",
        market="Zurich", via="RT Specialty", sent_on="2026-07-07",
    )

    mcpserver._market_responded(
        conn, approach["response_id"], via="Amwins Brokerage"
    )

    assert (
        marketing.get_response(conn, approach["response_id"]).via_org_id == amwins.id
    )


def test_an_access_point_the_book_does_not_carry_is_refused_not_minted(conn) -> None:
    """A MISSING VERB IS NOT A REFUSAL — IT IS A WRONG WRITE (CLAUDE.md), and
    the answer to that rule is that `market_create` EXISTS. So this tool
    refuses rather than minting a market as a side effect of correcting an
    approach, which is precisely the wrong write the rule is about."""
    _, placement = _book(conn)
    _market(conn, "Zurich")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Zurich", sent_on="2026-07-07"
    )

    # THE MESSAGE IS MATCHED, not merely the exception type. `market_responded`
    # refuses an empty `changes` with a sentence of its own, so a bare
    # `pytest.raises(ValueError)` here goes green against a build that ignores
    # the argument entirely — which is the exact regression this test is for.
    with pytest.raises(ValueError, match="Nobody Underwriters"):
        mcpserver._market_responded(
            conn, approach["response_id"], via="Nobody Underwriters"
        )

    assert marketing.get_response(conn, approach["response_id"]).via_org_id is None
    assert orgs.find_by_name(conn, "Nobody Underwriters") is None


def test_a_market_can_say_no_here_and_yes_higher_up(conn) -> None:
    """"Not for us on the primary, but show us the excess" is an ANSWER, and
    the assistant can record it: `declined` alone drops the carrier out of the
    open set and off the clearance check while the work of going back to them
    is still to do."""
    _, placement = _book(conn)
    _market(conn, "Zurich")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Zurich", sent_on="2026-07-07"
    )

    out = mcpserver._market_responded(
        conn, approach["response_id"],
        status="declined_open_elsewhere", responded_on="2026-07-20",
    )

    assert out["status"] == "declined_open_elsewhere"
    # THE PACKAGE IS NOT CLOSED. `declined` would take it off the Pipeline's
    # "out at market" queue with the carrier still in play.
    assert out["submission_status"] == "out"


# --- a row recorded in error, on the agent's surface ------------------------


def test_the_assistant_can_take_back_a_row_recorded_in_error(conn) -> None:
    """A MISSING VERB IS NOT A REFUSAL, IT IS A WRONG WRITE (CLAUDE.md).
    Without this an assistant asked to undo a mis-recorded approach reaches for
    the nearest door it has — `market_responded` — and re-labels the row
    `declined`, crediting a carrier with a refusal it never made on a
    client-facing document."""
    _, placement = _book(conn)
    _market(conn, "Zurich")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Zurich", sent_on="2026-07-07"
    )
    mcpserver._market_responded(
        conn, approach["response_id"], status="quoted",
        responded_on="2026-07-20", premium="120,000",
    )
    assert submissions.get(conn, approach["submission_id"]).status == "quoted"

    out = mcpserver._market_response_remove(conn, approach["response_id"])

    assert out["removed"] == approach["response_id"]
    assert out["answers_left_on_the_approach"] == 0
    # THE CACHE FOLLOWS THE ROWS. A package still saying `quoted` and carrying
    # a premium no live row states is the second-home defect the roll-up exists
    # to close — and it would show on the Pipeline as revenue.
    assert out["submission_status"] == "out"
    rolled = submissions.get(conn, approach["submission_id"])
    assert rolled.status == "out"
    assert rolled.quoted_premium is None
    assert not marketing.responses_for_submission(conn, approach["submission_id"])


def test_the_removal_is_revertible(conn) -> None:
    """SOFT, like every delete in this book. The row keeps its id and its
    history, and the batch it was removed in can put it back — which is what
    makes a destructive control safe to offer at all."""
    from bookkit.services import batches as batches_svc

    _, placement = _book(conn)
    _market(conn, "Zurich")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Zurich", sent_on="2026-07-07"
    )

    out = mcpserver._market_response_remove(conn, approach["response_id"])
    assert not marketing.responses_for_submission(conn, approach["submission_id"])

    # THE REF, which is what the tool hands back and what a person types.
    assert batches_svc.revert(conn, out["batch"], "2026-07-21").applied
    assert [
        r.id for r in marketing.responses_for_submission(conn, approach["submission_id"])
    ] == [approach["response_id"]]


def test_the_assistant_can_rule_a_market_out_on_price(conn) -> None:
    """`not_viable` is OUR judgment, not the market's — the minimum premium is
    above what this account spends, or the economics do not work at any rate
    they would quote. Filing that as `declined` credits a carrier with a
    refusal it never made."""
    _, placement = _book(conn)
    _market(conn, "Zurich")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Zurich", sent_on="2026-07-07"
    )

    out = mcpserver._market_responded(
        conn, approach["response_id"], status="not_viable",
        responded_on="2026-07-20", decline_reason_public="minimum_premium",
    )

    assert out["status"] == "not_viable"
    assert out["submission_status"] == "declined"
    # …AND THE CLIENT-SAFE REASON THAT GOES WITH IT. "Their rate was
    # uncompetitive" and "their minimum premium is more than this account
    # spends in total" are different facts and a client reads them differently.
    assert marketing.get_response(
        conn, approach["response_id"]
    ).decline_reason_public == "minimum_premium"


# --- one ask, three markets: the agent's half --------------------------------
#
# A CHANGE THAT LANDS ON THE WEB AND NOT ON MCP HAS SHIPPED TO TWO THIRDS OF ITS
# USERS. Subjectivities had no MCP tools at all before 2026-08-27 — all four
# verbs deferred — so an assistant asked "what is blocking the Delta renewal"
# could not answer and an assistant told "AIG wants loss runs" could record
# nothing.


def _blocked(conn):
    """A placement with one market condition nobody has asked the client for."""
    _, placement = _book(conn)
    _market(conn, "Travelers")
    approach = mcpserver._market_approach(
        conn, placement.ref, "GL", market="Travelers", sent_on="2026-07-07"
    )
    condition = mcpserver._subjectivity_add(
        conn, approach["submission_id"], "5-year loss runs, currently valued"
    )
    return placement, approach, condition


def test_blocking_list_says_nobody_has_asked_the_client_yet(conn) -> None:
    """The state the ask verb exists to change, and the one a model has to be
    able to SEE before it can act on it."""
    placement, _, condition = _blocked(conn)

    out = mcpserver._blocking_list(conn, placement.ref)

    rows = [r for r in out["blocking"] if r["kind"] == "condition"]
    assert len(rows) == 1
    assert rows[0]["subjectivity_ref"] == condition["subjectivity_ref"]
    assert rows[0]["asked_as"] is None
    assert rows[0]["waiting_on"] == "Travelers"


def test_asking_twice_attaches_rather_than_writing_a_second_ask(conn) -> None:
    """ONE ASK, THREE MARKETS — the whole point, through the tool."""
    _, approach, first = _blocked(conn)
    second = mcpserver._subjectivity_add(
        conn, approach["submission_id"], "Loss runs, 5 yrs"
    )

    made = mcpserver._subjectivity_ask_client(
        conn, first["subjectivity_ref"], prompt="Loss runs — 5 years"
    )
    joined = mcpserver._subjectivity_ask_client(
        conn, second["subjectivity_ref"], item_ref=made["item_ref"]
    )

    assert made["created_a_new_ask"] is True
    assert made["also_waiting"] == 0
    assert joined["created_a_new_ask"] is False
    assert joined["item_ref"] == made["item_ref"]
    assert joined["also_waiting"] == 1, (
        "the figure that tells a model it has just avoided writing a second email"
    )


def test_received_names_what_it_unblocks_without_claiming_it(conn) -> None:
    """RECEIVED IS NOT MET. A model that did not pass `met` still has to be able
    to tell the broker which markets are now waiting to be told."""
    _, _, condition = _blocked(conn)
    made = mcpserver._subjectivity_ask_client(
        conn, condition["subjectivity_ref"], prompt="Loss runs"
    )

    out = mcpserver._request_item_received(conn, made["item_ref"])

    assert out["marked_met"] == 0
    assert [u["subjectivity_ref"] for u in out["unblocks"]] == [
        condition["subjectivity_ref"]
    ]
    from bookkit.repo import submissions as submissions_repo

    still = submissions_repo.get_subjectivity(conn, condition["subjectivity_ref"])
    assert still.status == "outstanding"


def test_received_with_met_settles_them_in_one_batch(conn) -> None:
    _, _, condition = _blocked(conn)
    made = mcpserver._subjectivity_ask_client(
        conn, condition["subjectivity_ref"], prompt="Loss runs"
    )

    out = mcpserver._request_item_received(conn, made["item_ref"], met=True)

    from bookkit.repo import submissions as submissions_repo

    assert out["marked_met"] == 1
    assert submissions_repo.get_subjectivity(
        conn, condition["subjectivity_ref"]
    ).status == "met"


def test_a_missing_condition_names_the_read_that_has_the_ids(conn) -> None:
    """A MISSING VERB IS NOT A REFUSAL, IT IS A WRONG WRITE — and a refusal that
    names no read is what sends a model looking for the nearest wrong door."""
    with pytest.raises(ValueError, match="blocking_list"):
        mcpserver._resolve_subjectivity(conn, "01NOSUCHTHING")


def test_an_item_can_be_added_to_a_request_that_already_exists(conn) -> None:
    """The REAL GAP mcpparity named: request_create took its items at creation
    and nothing added one afterwards, so an underwriter's follow-up had nowhere
    to be filed."""
    orgs.create(conn, name="Acme", kind="client")
    made = mcpserver._request_create(conn, "Acme", "Sompo questions", ["loss runs"])

    added = mcpserver._request_item_add(
        conn, made["request_ref"], "Schedule of vehicles", kind="document"
    )

    assert added["request_ref"] == made["request_ref"]
    assert added["kind"] == "document"
    items = mcpserver._request_items(conn, made["request_ref"])
    assert len(items["items"]) == 2


def test_an_item_kind_the_book_does_not_have_is_refused(conn) -> None:
    orgs.create(conn, name="Acme", kind="client")
    made = mcpserver._request_create(conn, "Acme", "Sompo questions", ["loss runs"])

    with pytest.raises(ValueError, match="kind must be one of"):
        mcpserver._request_item_add(conn, made["request_ref"], "X", kind="telepathy")


# --- merging two records for one market ------------------------------------


def test_market_merge_folds_one_into_the_other_and_keeps_the_name_resolving(
    conn,
) -> None:
    """A MISSING VERB IS NOT A REFUSAL, IT IS A WRONG WRITE (CLAUDE.md). The
    ledger said twice that MERGE is the honest verb for retiring a market and
    there was no tool for it — so an assistant asked to tidy two records for
    one carrier had only renaming one (which leaves two rows a lookup can land
    on) or deactivating it (which strands every submission pointing at it)."""
    from bookkit.repo import aliases, orgs

    mcpserver._market_create(conn, "Hartwell Mutual")
    mcpserver._market_create(conn, "Hartwell Mut.")

    out = mcpserver._market_merge(
        conn, keep="Hartwell Mutual", fold_in="Hartwell Mut."
    )

    keeper = orgs.find_market(conn, "Hartwell Mutual")
    assert keeper is not None
    assert out["kept"] == "Hartwell Mutual"
    assert out["folded_in"] == "Hartwell Mut."
    assert orgs.find_market(conn, "Hartwell Mut.") is None
    # THE FOLDED-IN NAME BECOMES AN ALIAS, which is what makes the merge safe
    # for towers: every spelling that created the duplicate keeps resolving.
    assert aliases.resolve(conn, "Hartwell Mut.") == keeper.id
    assert out["batch"].startswith("MCP-")


def test_market_merge_names_the_direction_and_refuses_a_self_merge(conn) -> None:
    """`keep` and `fold_in`, never source/target — which of the two dies is
    the only thing about this call that can be got wrong in a way nothing
    catches, and it is not something to infer from a position."""
    mcpserver._market_create(conn, "Hartwell Mutual")

    with pytest.raises(ValueError, match="cannot be merged into itself"):
        mcpserver._market_merge(
            conn, keep="Hartwell Mutual", fold_in="Hartwell Mutual"
        )


def test_market_merge_refuses_a_name_the_book_does_not_know(conn) -> None:
    """The refusal names the door, the same as every other market miss."""
    mcpserver._market_create(conn, "Hartwell Mutual")

    with pytest.raises(ValueError, match="no market matching"):
        mcpserver._market_merge(conn, keep="Hartwell Mutual", fold_in="Nobody Re")


def test_reverting_a_merge_hands_the_name_back(conn) -> None:
    """ONE CALL, ONE UNDO UNIT — and it has to be whole. Restoring the emptied
    market while its NAME still resolved to the one it was folded into would
    leave every later lookup landing on the wrong org."""
    from bookkit.repo import aliases, orgs
    from bookkit.services import batches

    mcpserver._market_create(conn, "Hartwell Mutual")
    mcpserver._market_create(conn, "Hartwell Mut.")
    out = mcpserver._market_merge(
        conn, keep="Hartwell Mutual", fold_in="Hartwell Mut."
    )

    # `now` is a STRING here and never the wall clock — services.batches.revert
    # takes the timestamp it stamps its own writes with.
    batches.revert(conn, out["batch"], "2026-08-27T09:00:00+00:00")

    restored = orgs.find_market(conn, "Hartwell Mut.")
    assert restored is not None
    assert aliases.resolve(conn, "Hartwell Mut.") == restored.id
