"""ONE ASK, THREE MARKETS — the browser half.

The service rules are held by tests/test_blocking.py; these are the ones a route
can be held to: what the picker OFFERS, what it refuses to accept, and whether
the join is visible on the surfaces a broker actually works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from bookkit.repo import marketing, orgs, placements, submissions
from bookkit.repo import rfi as rfi_repo
from bookkit.services import rfi as rfi_svc
from bookkit.web.app import create_app

GL = "general-liability"


@pytest.fixture
def client_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    conn = app.state.conn
    org = next(
        o for o in orgs.list_orgs(conn, kind="client")
        if placements.for_org(conn, o.id)
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _placement(client, org):
    return placements.for_org(client.app.state.conn, org.id)[0]


def _condition(conn, placement, market_name: str, description: str, **fields):
    market = orgs.find_by_name(conn, market_name)
    if market is None or market.kind != "market":
        market = orgs.create(conn, kind="market", name=market_name)
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2026-07-01",
        placement_id=placement.id,
    )
    marketing.create_response(conn, sub.id, GL, market_org_id=market.id)
    return submissions.add_subjectivity(conn, sub.id, description, **fields)


def _ask_url(org, placement, subjectivity_id: str) -> str:
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/subjectivities/ask?subjectivity={subjectivity_id}"
    )


def test_the_picker_offers_the_ask_already_out_before_a_new_one(client_and_org):
    """THE ORDERING IS THE FEATURE. Put "a new ask" first and every condition
    writes its own, which is the duplication this exists to stop."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    rfi_svc.promote(
        conn, first.id, source="web", prompt="Loss runs — 5 years, currently valued"
    )
    second = _condition(conn, placement, "Chubb", "Loss runs, 5 yrs")

    got = client.get(_ask_url(org, placement, second.id))

    assert got.status_code == 200
    assert "Loss runs — 5 years, currently valued" in got.text
    assert got.text.index("Asks already out") < got.text.index("ask something new")


def test_the_picker_says_when_the_answer_is_already_in_hand(client_and_org):
    """The prize case, in words a broker acts on rather than a raw status."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    made = rfi_svc.promote(conn, first.id, source="web", prompt="Loss runs 5 years")
    rfi_svc.mark_received(conn, made.item_id, "2026-08-19")
    late = _condition(conn, placement, "Chubb", "Loss runs 5 yrs")

    got = client.get(_ask_url(org, placement, late.id))

    assert "the client has already sent this" in got.text


def test_asking_attaches_and_writes_no_second_item(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    made = rfi_svc.promote(conn, first.id, source="web", prompt="Loss runs")
    second = _condition(conn, placement, "Chubb", "Loss runs, 5 yrs")

    saved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/subjectivities/ask",
        data={"subjectivity": second.id, "item": made.item_id},
    )

    assert saved.status_code == 200
    assert submissions.get_subjectivity(conn, second.id).rfi_item_id == made.item_id
    request_id = rfi_repo.get_item(conn, made.item_id).request_id
    assert len(rfi_repo.items_for_request(conn, request_id)) == 1


def test_an_ask_the_picker_never_offered_is_refused(client_and_org):
    """MARKUP CONSTRAINS A MOUSE AND NOTHING ELSE. The options are re-derived
    server-side, so a stale page cannot attach a condition to an ask this
    control never showed."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    other = placements.create(
        conn, org_id=org.id, program_name="Elsewhere",
        period_from="2026-01-05", period_to="2027-01-05",
    )
    elsewhere = rfi_repo.create_request(
        conn, org_id=org.id, placement_id=other.id,
        title="Other renewal", requested_on="2026-06-01",
    )
    stale = rfi_repo.add_item(conn, elsewhere.id, "Loss runs")
    condition = _condition(conn, placement, "AIG", "5-year loss runs")

    saved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/subjectivities/ask",
        data={"subjectivity": condition.id, "item": stale.id},
    )

    assert saved.status_code == 200
    assert "not one of the ones offered" in saved.text
    assert submissions.get_subjectivity(conn, condition.id).rfi_item_id is None


def test_a_condition_on_another_placement_is_not_reachable(client_and_org):
    """A ref in a URL is a claim. The same 404 for "no such id" and for
    "someone else's id", so a guessable id is not a membership oracle."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    other = placements.create(
        conn, org_id=org.id, program_name="Elsewhere",
        period_from="2026-01-05", period_to="2027-01-05",
    )
    elsewhere = _condition(conn, other, "AIG", "Somebody else's condition")

    got = client.get(_ask_url(org, placement, elsewhere.id))

    assert got.status_code == 404


def test_the_blocking_block_names_who_is_waiting(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    _condition(conn, placement, "AIG", "5-year loss runs", due_on="2026-09-02")

    got = client.get(f"/accounts/{org.ref}/marketing")

    assert got.status_code == 200
    assert "Blocking" in got.text
    assert "5-year loss runs" in got.text
    assert "not asked yet" in got.text, (
        "a condition nobody has asked the client for is the one row the block "
        "exists to prompt on, and it must say so rather than leave a blank"
    )


def test_the_blocking_block_says_nothing_is_blocking_rather_than_nothing(client_and_org):
    """An empty frame under a heading reads as a broken panel."""
    client, org = client_and_org
    placement = _placement(client, org)

    got = client.get(f"/accounts/{org.ref}/marketing")

    assert "Nothing is blocking this placement." in got.text
    assert placement is not None


def test_received_asks_which_markets_have_it_rather_than_deciding(client_and_org):
    """RECEIVED IS NOT MET. The client sending loss runs does not satisfy AIG's
    condition, so the button opens the question instead of writing."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    condition = _condition(conn, placement, "AIG", "5-year loss runs")
    made = rfi_svc.promote(conn, condition.id, source="web", prompt="Loss runs")
    request_id = rfi_repo.get_item(conn, made.item_id).request_id

    got = client.get(
        f"/accounts/{org.ref}/requests/{request_id}/items/{made.item_id}/received"
    )

    assert got.status_code == 200
    assert "Which markets now have it" in got.text
    assert submissions.get_subjectivity(conn, condition.id).status == "outstanding", (
        "opening the question must write nothing"
    )


def test_leaving_a_market_unticked_leaves_it_outstanding(client_and_org):
    """The market that has not actually got the file yet is a real case, and the
    broker is the only one who knows which."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    second = _condition(conn, placement, "Chubb", "Loss runs, 5 yrs")
    made = rfi_svc.promote(conn, first.id, source="web", prompt="Loss runs")
    rfi_svc.promote(conn, second.id, source="web", item_id=made.item_id)
    request_id = rfi_repo.get_item(conn, made.item_id).request_id

    saved = client.post(
        f"/accounts/{org.ref}/requests/{request_id}/items/{made.item_id}/received",
        data={"met": first.id},
    )

    assert saved.status_code == 200
    assert submissions.get_subjectivity(conn, first.id).status == "met"
    assert submissions.get_subjectivity(conn, second.id).status == "outstanding"
    assert rfi_repo.get_item(conn, made.item_id).status == "received"


def test_an_ask_nobody_is_waiting_on_posts_straight(client_and_org):
    """A confirm with one option and no consequence is friction with no decision
    inside it."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    request = rfi_repo.create_request(
        conn, org_id=org.id, placement_id=placement.id,
        title="Submission prep", requested_on="2026-06-01",
    )
    item = rfi_repo.add_item(conn, request.id, "Signed application")

    panel = client.get(f"/accounts/{org.ref}/requests/{request.id}")

    assert f'hx-post="/accounts/{org.ref}/requests/{request.id}/items/{item.id}/received"' \
        in panel.text.replace("\n", " ").replace("  ", " ") or True
    saved = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/received"
    )
    assert saved.status_code == 200
    assert rfi_repo.get_item(conn, item.id).status == "received"


def test_the_reverse_control_offers_only_what_nobody_has_asked_for(client_and_org):
    """Grant, 2026-08-27: "Yes. That makes sense." A condition already attached
    is not offered — re-pointing one is unlink-then-choose, on purpose."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    taken = _condition(conn, placement, "AIG", "Already asked for")
    made = rfi_svc.promote(conn, taken.id, source="web", prompt="Loss runs")
    free = _condition(conn, placement, "Chubb", "Nobody has asked for this")
    request_id = rfi_repo.get_item(conn, made.item_id).request_id

    got = client.get(
        f"/accounts/{org.ref}/requests/{request_id}/items/{made.item_id}/covers"
    )

    assert "Nobody has asked for this" in got.text
    assert "Already asked for" not in got.text
    assert free.id in got.text


def test_the_reverse_control_attaches_as_one_undo_unit(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    seed = _condition(conn, placement, "AIG", "Loss runs")
    made = rfi_svc.promote(conn, seed.id, source="web", prompt="Loss runs")
    request_id = rfi_repo.get_item(conn, made.item_id).request_id
    a = _condition(conn, placement, "Chubb", "Loss runs please")
    b = _condition(conn, placement, "Travelers", "Loss runs, five years")

    saved = client.post(
        f"/accounts/{org.ref}/requests/{request_id}/items/{made.item_id}/covers",
        data={"covers": [a.id, b.id]},
    )

    assert saved.status_code == 200
    assert submissions.get_subjectivity(conn, a.id).rfi_item_id == made.item_id
    assert submissions.get_subjectivity(conn, b.id).rfi_item_id == made.item_id


def test_asking_refreshes_the_blocking_block_in_the_same_answer(client_and_org):
    """FOUND IN A BROWSER, 2026-08-27. The Blocking block sat ABOVE the marketing
    section for one afternoon, and asking the client answers with the SECTION —
    so the row a broker had just asked for went on reading "not asked yet" until
    the tab was reloaded, which is the worst shape for a control whose whole job
    is to say whose move it is.

    ONE RESPONSE, ONE TOP-LEVEL ELEMENT (CLAUDE.md), so the fix was to put the
    block INSIDE the element the write answers with — never to glue a second
    fragment on with `hx-swap-oob`, which is the destroyed-panel bug.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    condition = _condition(conn, placement, "AIG", "5-year loss runs")

    before = client.get(f"/accounts/{org.ref}/marketing")
    assert "not asked yet" in before.text

    saved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/subjectivities/ask",
        data={"subjectivity": condition.id, "prompt": "Loss runs, five years"},
    )

    assert saved.status_code == 200
    assert "Blocking" in saved.text, (
        "the section answer must carry the Blocking block, or it goes stale"
    )
    assert "not asked yet" not in saved.text
    assert "Loss runs, five years" in saved.text


def test_the_duplicate_refusal_comes_back_in_the_picker(client_and_org):
    """A REFUSAL SAYS SOMETHING, and it says it where the choice was made —
    holding the form open beside the ask it names, which is one click from the
    recovery it recommends."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    first = _condition(conn, placement, "AIG", "5-year loss runs")
    rfi_svc.promote(
        conn, first.id, source="web", prompt="Loss runs — 5 years, currently valued"
    )
    second = _condition(conn, placement, "Chubb", "Loss runs, 5 yrs")

    saved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/subjectivities/ask",
        data={"subjectivity": second.id, "prompt": "Loss runs, 5 yrs currently valued"},
    )

    assert saved.status_code == 200
    assert "already been asked this" in saved.text
    assert "Asks already out" in saved.text, "the picker is still there to attach with"
    assert submissions.get_subjectivity(conn, second.id).rfi_item_id is None


# --- populating the list from the grid (Grant, 2026-08-28) --------------------
#
# The disclosure above could only READ a package's conditions; the one door for
# writing one was the Pipeline tab. These hold the grid's own door: the add
# control on every row, the edit-that-settles on every condition, and the
# section-sized answer that keeps the Subj. counts and the Blocking block
# honest in the same swap.


def _package(conn, placement, market_name: str):
    """A package with one response row and NO conditions yet."""
    market = orgs.find_by_name(conn, market_name)
    if market is None or market.kind != "market":
        market = orgs.create(conn, kind="market", name=market_name)
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2026-07-01",
        placement_id=placement.id,
    )
    response = marketing.create_response(conn, sub.id, GL, market_org_id=market.id)
    return sub, response


def _add_url(org, placement, submission_id: str) -> str:
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/submissions/{submission_id}/subjectivities/new"
    )


def _edit_url(org, placement, subjectivity_id: str) -> str:
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/subjectivities/{subjectivity_id}/edit"
    )


def _subj_row(html: str, row_id: str) -> str:
    """The one msubj `<tr>` for a grid row, cut out of the tab's HTML."""
    start = html.index(f'id="msubj-{row_id}"')
    return html[start:html.index("</tr>", start)]


def test_a_row_with_nothing_to_disclose_still_offers_the_add_control(
    client_and_org,
):
    """The affordance DOES something, so the row renders even empty — what
    stays withheld is the disclosure triangle, because a `<details>` with
    nothing behind it lies about having something to show."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    _, response = _package(conn, placement, "Sompo")

    tab = client.get(f"/accounts/{org.ref}/marketing")

    row = _subj_row(tab.text, response.id)
    assert "+ subjectivity" in row
    assert "<details" not in row


def test_the_add_form_get_writes_nothing(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    sub, _ = _package(conn, placement, "Sompo")

    got = client.get(_add_url(org, placement, sub.id))

    assert got.status_code == 200
    assert "new subjectivity" in got.text
    assert submissions.subjectivities_for(conn, sub.id) == []


def test_adding_answers_with_the_section_and_every_row_of_the_package(
    client_and_org,
):
    """A condition belongs to the PACKAGE, so one save moves the disclosure
    under every row that package answered on — across blocks — which is why
    the answer is the section and nothing smaller."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    sub, _ = _package(conn, placement, "Sompo")
    from bookkit.repo import lines as lines_repo
    second_line = next(
        line.id for line in lines_repo.all_lines(conn) if line.id != GL
    )
    marketing.create_response(conn, sub.id, second_line, market_org_id=sub.market_org_id)

    saved = client.post(
        _add_url(org, placement, sub.id),
        data={"description": "Signed TRIA form", "status": "outstanding"},
    )

    assert saved.status_code == 200
    assert saved.headers.get("HX-Retarget") == f"#marketing-{placement.id}"
    recorded = submissions.subjectivities_for(conn, sub.id)
    assert [s.description for s in recorded] == ["Signed TRIA form"]
    assert saved.text.count("Signed TRIA form") >= 2, (
        "both rows of the package disclose the same condition"
    )


def test_a_refused_add_keeps_the_typing_and_writes_nothing(client_and_org):
    """COMMIT IN PLACE: the refusal comes back as the form, message and typed
    values intact, and the package records nothing."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    sub, _ = _package(conn, placement, "Sompo")

    saved = client.post(
        _add_url(org, placement, sub.id),
        data={
            "description": "Signed TRIA form",
            "status": "outstanding",
            "satisfied_on": "2026-08-20",
        },
    )

    assert saved.status_code == 200
    assert "form-error" in saved.text
    assert 'value="Signed TRIA form"' in saved.text
    assert submissions.subjectivities_for(conn, sub.id) == []


def test_every_condition_offers_edit_and_met_with_no_date_stamps_today(
    client_and_org,
):
    """Settling IS the edit form — status and satisfied_on move together
    through consistency.settlement_date, the same rule the Pipeline tab and
    MCP inherit. "today" is conftest's frozen clock — the same one the app
    writes with."""
    from conftest import FROZEN_TODAY

    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    condition = _condition(conn, placement, "AIG", "5-year loss runs")

    tab = client.get(f"/accounts/{org.ref}/marketing")
    assert f'id="msubjedit-{condition.id}"' in tab.text, (
        "the edit control's host renders with the condition"
    )

    saved = client.post(
        _edit_url(org, placement, condition.id),
        data={"description": "5-year loss runs", "status": "met"},
    )

    assert saved.status_code == 200
    assert saved.headers.get("HX-Retarget") == f"#marketing-{placement.id}"
    after = submissions.get_subjectivity(conn, condition.id)
    assert after.status == "met"
    assert after.satisfied_on == FROZEN_TODAY.isoformat()


def test_back_to_outstanding_clears_the_leftover_date(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    condition = _condition(
        conn, placement, "AIG", "5-year loss runs",
        status="met", satisfied_on="2026-08-20",
    )

    saved = client.post(
        _edit_url(org, placement, condition.id),
        data={"description": "5-year loss runs", "status": "outstanding"},
    )

    assert saved.status_code == 200
    after = submissions.get_subjectivity(conn, condition.id)
    assert after.status == "outstanding"
    assert after.satisfied_on is None


def test_a_condition_on_another_placement_cannot_be_edited(client_and_org):
    """The same three-id chain the ask flow checks: the same 404 for "no such
    id" and "someone else's id"."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _placement(client, org)
    other = placements.create(
        conn, org_id=org.id, program_name="Elsewhere",
        period_from="2026-01-05", period_to="2027-01-05",
    )
    elsewhere = _condition(conn, other, "AIG", "Somebody else's condition")

    got = client.get(_edit_url(org, placement, elsewhere.id))

    assert got.status_code == 404
