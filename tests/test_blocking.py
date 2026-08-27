"""ONE ASK, THREE MARKETS — the join between what a market requires and what
we have asked the client for.

Grant, 2026-08-27: three markets asking for five-year loss runs must not become
three asks to one client. These are the rules that make that true, and the ones
that keep the two halves from collapsing into each other.
"""

from __future__ import annotations

from datetime import date

import pytest

from bookkit.models import SUBJECTIVITY_OPEN_STATUS
from bookkit.repo import marketing, orgs, placements, submissions
from bookkit.repo import rfi as rfi_repo
from bookkit.services import blocking
from bookkit.services import rfi as rfi_svc

GL = "general-liability"


@pytest.fixture
def book(conn):
    org = orgs.create(conn, kind="client", name="Delta Marine")
    placement = placements.create(
        conn, org_id=org.id, program_name="Casualty",
        period_from="2026-01-05", period_to="2027-01-05",
    )
    return conn, org, placement


def _condition(conn, placement, market_name: str, description: str, **fields):
    market = orgs.create(conn, kind="market", name=market_name)
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2026-07-01",
        placement_id=placement.id,
    )
    marketing.create_response(conn, sub.id, GL, market_org_id=market.id)
    return submissions.add_subjectivity(conn, sub.id, description, **fields)


def test_one_ask_carries_every_market_that_wanted_it(book):
    """THE WHOLE POINT. The second and third markets ATTACH to the ask already
    out; the client is asked once."""
    conn, org, placement = book
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    second = _condition(conn, placement, "Chubb", "Loss runs, 5 yrs")
    third = _condition(conn, placement, "Travelers", "Currently valued loss runs")

    made = rfi_svc.promote(
        conn, first.id, source="web", prompt="Loss runs — 5 years, currently valued"
    )
    for other in (second, third):
        rfi_svc.promote(conn, other.id, source="web", item_id=made.item_id)

    waiting = submissions.subjectivities_waiting_on(conn, made.item_id)

    assert made.created_item is True
    assert len(waiting) == 3, "three markets, one ask"
    assert len(rfi_repo.items_for_request(conn, _request_id(conn, made))) == 1


def _request_id(conn, promotion) -> str:
    return rfi_repo.get_item(conn, promotion.item_id).request_id


def test_the_second_market_does_not_open_a_second_request(book):
    """ONE ENVELOPE PER RENEWAL. A request is an email; a condition promoted on
    Tuesday belongs in the ask that went out on Monday."""
    conn, org, placement = book
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    second = _condition(conn, placement, "Chubb", "Signed application")

    rfi_svc.promote(conn, first.id, source="web", prompt="Loss runs")
    rfi_svc.promote(conn, second.id, source="web", prompt="Signed application")

    requests = [
        r for r in rfi_repo.requests_for_org(conn, org.id)
        if r.placement_id == placement.id
    ]

    assert len(requests) == 1, "two conditions, two items, ONE conversation"


def test_received_is_not_met(book):
    """The client sending loss runs does not satisfy AIG's condition — AIG
    having them and accepting them does. Receiving SURFACES, it never decides."""
    conn, org, placement = book
    condition = _condition(conn, placement, "AIG", "5-year loss runs")
    made = rfi_svc.promote(conn, condition.id, source="web", prompt="Loss runs")

    rfi_svc.mark_received(conn, made.item_id, "2026-08-19")

    after = submissions.get_subjectivity(conn, condition.id)
    assert after.status == SUBJECTIVITY_OPEN_STATUS, (
        "an arriving document must not silently satisfy a market's condition"
    )
    assert [s.id for s in rfi_svc.unblocked_by(conn, made.item_id)] == [condition.id]


def test_marking_met_is_one_undo_unit_over_every_market(book):
    """A broker confirming that one document clears three markets did ONE
    thing, and `u` has to put all three back."""
    conn, org, placement = book
    made = None
    ids = []
    for market in ("AIG", "Chubb", "Travelers"):
        condition = _condition(conn, placement, market, "5-year loss runs")
        ids.append(condition.id)
        made = (
            rfi_svc.promote(conn, condition.id, source="web", prompt="Loss runs")
            if made is None
            else rfi_svc.promote(
                conn, condition.id, source="web", item_id=made.item_id
            )
        ) or made
    item_id = submissions.get_subjectivity(conn, ids[0]).rfi_item_id
    assert item_id is not None

    settled = rfi_svc.mark_met(
        conn, ids, on="2026-08-19", source="web", org_id=org.id
    )

    assert settled == 3
    assert all(
        submissions.get_subjectivity(conn, i).status == "met" for i in ids
    )


def test_an_ask_that_goes_away_takes_its_links_with_it(book):
    """Both tables are soft-deleted, so the link would still RESOLVE — a
    condition would go on reading 'asked 19 Aug' against an ask nobody can see
    or answer."""
    conn, org, placement = book
    condition = _condition(conn, placement, "AIG", "5-year loss runs")
    made = rfi_svc.promote(conn, condition.id, source="web", prompt="Loss runs")

    removal = rfi_svc.remove_item(conn, made.item_id, source="web")

    assert removal.unlinked == 1
    assert submissions.get_subjectivity(conn, condition.id).rfi_item_id is None


def test_a_condition_already_met_cannot_be_asked_for(book):
    """There is nothing left to ask the client for, and a chase filed against a
    satisfied condition is one the client did not need."""
    conn, org, placement = book
    condition = _condition(conn, placement, "AIG", "5-year loss runs")
    submissions.update_subjectivity(conn, condition.id, None, status="met")

    with pytest.raises(ValueError, match="already met"):
        rfi_svc.promote(conn, condition.id, source="web", prompt="Loss runs")


def test_promote_wants_exactly_one_of_attach_or_new(book):
    conn, org, placement = book
    condition = _condition(conn, placement, "AIG", "5-year loss runs")

    with pytest.raises(ValueError, match="not both and not neither"):
        rfi_svc.promote(conn, condition.id, source="web")


def test_an_ask_from_another_renewal_is_refused(book):
    """SAME PLACEMENT IS THE HARD FILTER (Grant, 2026-08-27). An ask satisfied
    on last year's placement is a document from another year, and forwarding it
    is not the same as answering this."""
    conn, org, placement = book
    other = placements.create(
        conn, org_id=org.id, program_name="Casualty (expiring)",
        period_from="2025-01-05", period_to="2026-01-05",
    )
    elsewhere = rfi_repo.create_request(
        conn, org_id=org.id, placement_id=other.id,
        title="Last year", requested_on="2025-06-01",
    )
    stale = rfi_repo.add_item(conn, elsewhere.id, "Loss runs")
    condition = _condition(conn, placement, "AIG", "5-year loss runs")

    with pytest.raises(ValueError, match="not an ask on this placement"):
        rfi_svc.promote(conn, condition.id, source="web", item_id=stale.id)


def test_candidates_rank_the_words_and_never_cross_the_placement(book):
    """The match is the feature — and same placement is a filter, not a weight,
    however well the words agree."""
    conn, org, placement = book
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    rfi_svc.promote(
        conn, first.id, source="web", prompt="Loss runs — 5 years, currently valued"
    )
    other = placements.create(
        conn, org_id=org.id, program_name="Property",
        period_from="2026-01-05", period_to="2027-01-05",
    )
    elsewhere = rfi_repo.create_request(
        conn, org_id=org.id, placement_id=other.id,
        title="Property", requested_on="2026-06-01",
    )
    rfi_repo.add_item(conn, elsewhere.id, "Loss runs — 5 years, currently valued")

    second = _condition(conn, placement, "Chubb", "Loss runs, 5 yrs")
    found = rfi_svc.candidates(conn, second.id)

    assert [c.item.prompt for c in found] == [
        "Loss runs — 5 years, currently valued"
    ], "the identical ask on ANOTHER placement must not be offered"
    assert found[0].already_waiting == 1
    assert found[0].state == "outstanding"


def test_a_candidate_says_the_answer_is_already_in_hand(book):
    """The prize case: a condition arriving AFTER the client already sent the
    document. Nothing to chase at all — it needs forwarding."""
    conn, org, placement = book
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    made = rfi_svc.promote(conn, first.id, source="web", prompt="Loss runs, 5 years")
    rfi_svc.mark_received(conn, made.item_id, "2026-08-19")

    late = _condition(conn, placement, "Chubb", "Loss runs 5 yrs")
    found = rfi_svc.candidates(conn, late.id)

    assert found and found[0].state == "received"


def test_unrelated_wording_is_not_offered_at_all(book):
    """A floor on the list, so a picker is a suggestion rather than noise."""
    conn, org, placement = book
    first = _condition(conn, placement, "AIG", "Completed fire inspection")
    rfi_svc.promote(conn, first.id, source="web", prompt="Completed fire inspection")

    second = _condition(conn, placement, "Chubb", "Signed application")

    assert rfi_svc.candidates(conn, second.id) == []


def test_the_blocking_list_shows_the_answer_in_hand(book):
    """The state this whole feature exists to make visible: no longer waiting on
    the client, still outstanding to the market, invisible before today."""
    conn, org, placement = book
    condition = _condition(conn, placement, "AIG", "5-year loss runs")
    made = rfi_svc.promote(conn, condition.id, source="web", prompt="Loss runs")
    rfi_svc.mark_received(conn, made.item_id, "2026-08-19")

    rows = blocking.for_placement(conn, placement.id, org.ref, date(2026, 8, 20))
    condition_rows = [r for r in rows if r.kind == blocking.CONDITION]

    assert len(condition_rows) == 1
    assert condition_rows[0].answer_in_hand is True
    assert condition_rows[0].asked_as == "Loss runs"
    assert condition_rows[0].who == "AIG"


def test_the_blocking_list_shows_the_earlier_of_two_due_dates(book):
    """BOTH DATES ARE KEPT (Grant, 2026-08-27). The market's deadline and the
    date we asked the client to hit are different facts, and collapsing them
    loses the one that actually binds."""
    conn, org, placement = book
    condition = _condition(
        conn, placement, "AIG", "5-year loss runs", due_on="2026-09-10"
    )
    rfi_svc.promote(
        conn, condition.id, source="web", prompt="Loss runs", due_on="2026-09-02"
    )

    rows = blocking.for_placement(conn, placement.id, org.ref, date(2026, 8, 20))

    assert rows[0].due_on == "2026-09-02", "the earlier date is the binding one"


def test_a_met_condition_is_not_blocking_and_an_undated_one_sorts_last(book):
    conn, org, placement = book
    _condition(conn, placement, "AIG", "Undated condition")
    dated = _condition(conn, placement, "Chubb", "Dated", due_on="2026-09-01")
    settled = _condition(conn, placement, "Travelers", "Already met")
    submissions.update_subjectivity(conn, settled.id, None, status="met")

    rows = blocking.for_placement(conn, placement.id, org.ref, date(2026, 8, 20))

    assert [r.what for r in rows] == ["Dated", "Undated condition"]
    assert rows[-1].days_remaining is None, "undated is unmeasured, never 0d"
    assert dated.id in {r.id for r in rows}


def test_an_ask_no_market_asked_for_is_still_blocking(book):
    """Most RFIs are submission prep, made before a single market saw the risk.
    They belong in the list on their own account, carrying nothing."""
    conn, org, placement = book
    request = rfi_repo.create_request(
        conn, org_id=org.id, placement_id=placement.id,
        title="Submission prep", requested_on="2026-06-01",
    )
    rfi_repo.add_item(conn, request.id, "Signed application", due_on="2026-08-25")

    rows = blocking.for_placement(conn, placement.id, org.ref, date(2026, 8, 20))

    assert [(r.kind, r.what, r.carries) for r in rows] == [
        (blocking.ASK, "Signed application", 0)
    ]
    assert rows[0].who == "the client"


def test_a_new_ask_that_is_already_out_is_refused_by_naming_it(book):
    """FOUND IN A BROWSER BY MAKING IT, 2026-08-27. The picker offers the asks
    already out and then a free-text box under them, and typing into the box
    what the list above is already showing writes a SECOND email to the client
    for one document — which is the exact duplication this feature exists to
    stop, arriving through the control built to stop it."""
    conn, org, placement = book
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    rfi_svc.promote(
        conn, first.id, source="web", prompt="Loss runs — 5 years, currently valued"
    )
    second = _condition(conn, placement, "Chubb", "Loss runs, 5 yrs")

    with pytest.raises(ValueError, match="already been asked this"):
        rfi_svc.promote(
            conn, second.id, source="web", prompt="Loss runs, 5 yrs currently valued"
        )

    assert submissions.get_subjectivity(conn, second.id).rfi_item_id is None


def test_a_genuinely_different_ask_still_goes_out(book):
    """THE REFUSAL MUST NOT BECOME A WALL. Two conditions on one renewal are
    usually different documents, and a guard that stopped the second one would
    be worse than the duplication it prevents."""
    conn, org, placement = book
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    rfi_svc.promote(conn, first.id, source="web", prompt="Loss runs, five years")
    second = _condition(conn, placement, "Chubb", "Signed application")

    made = rfi_svc.promote(
        conn, second.id, source="web", prompt="Signed and dated application form"
    )

    assert made.created_item is True
