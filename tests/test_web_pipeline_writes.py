"""Pipeline writes on the web — gap 4.

The assertions follow test_web_writes.py's rule: never only "the field
changed". A plain outcome check passes even when the route writes outside a
batch, so what is asserted is the batch too — source='web', the TUI's own
tool name, events to revert.

The bind-to-layer test asserts against the FILE, not the projection: towerkit
JSON files are the sole authority for program structure, and a participant
that exists only in proj_* is a participant the next re-project erases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import sync
from bookkit.repo import opportunities as opportunities_repo
from bookkit.repo import orgs as orgs_repo
from bookkit.repo import placements as placements_repo
from bookkit.repo import submissions as submissions_repo
from bookkit.services import pipeline as pipeline_svc
from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, since="", limit=1)
    return found[0] if found else None


def _events(conn, batch):
    from bookkit.repo import batches as batches_repo

    return batches_repo.events_for(conn, batch.id)


def _org_of_placement(conn, placement_id):
    return orgs_repo.get(conn, placements_repo.get(conn, placement_id).org_id)


def _an_outstanding_submission(conn, *, file_linked: bool):
    """(org, submission) for a status='out' submission on a placement —
    file-linked with layers when asked, unlinked (or layerless) otherwise."""
    for sub in submissions_repo.outstanding(conn):
        if sub.placement_id is None:
            continue
        placement = placements_repo.get(conn, sub.placement_id)
        linked = bool(placement.program_path) and bool(
            sync.layer_details(conn, placement.id)
        )
        if linked == file_linked:
            return _org_of_placement(conn, sub.placement_id), sub
    pytest.skip(f"seed produced no outstanding submission with file_linked={file_linked}")


GL = "general-liability"


def _marketed(conn, placement_id: str, line_id: str = GL) -> str:
    """This placement is marketing that line of coverage.

    The Response form asks WHICH LINE an answer is about, because a market
    answers a line and not a package. The seed leaves every placement with no
    `placement_line` rows, so the tests below that want a KNOWN line to post
    against declare it first, the same way the Marketing panel's `+ line of
    coverage` control does — they are not exercising the picker's offered set,
    which `test_a_placement_that_has_declared_no_line_still_records_the_answer`
    owns.
    """
    from bookkit.repo import marketing as marketing_repo

    marketing_repo.set_placement_line(conn, placement_id, line_id)
    return line_id


# --- recording a market response ----------------------------------------------


def test_a_response_moves_the_submission_and_stores_the_quote(client):
    """ONE HOME. The figures land on a `market_response` and the submission is
    RECOMPUTED from it — the Pipeline's own columns are a cache of the rows,
    not a second place a broker types the same quote (Grant, 2026-08-26)."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={
            "line_id": GL,
            "status": "quoted",
            "responded_on": "2026-08-13",
            "premium": "850,000",
            "lim": "10,000,000",
            "quote_expires_on": "2026-09-12",
        },
    )
    assert response.status_code == 200

    rows = marketing_repo.responses_for_submission(conn, sub.id)
    assert len(rows) == 1, "the answer did not land on a market response row"
    assert rows[0].line_id == GL
    assert rows[0].premium == 85_000_000  # cents: entry accepts cents
    assert rows[0].lim == 1_000_000_000
    assert rows[0].quote_expires_on == "2026-09-12"
    assert rows[0].market_org_id == sub.market_org_id, (
        "the market is the submission's and is never asked again"
    )

    fresh = submissions_repo.get(conn, sub.id)
    assert str(fresh.status) == "quoted"
    assert fresh.quoted_premium == 85_000_000
    assert fresh.quoted_limit == 1_000_000_000
    assert fresh.quote_expires_on == "2026-09-12"
    assert fresh.response_on == "2026-08-13"

    batch = _latest_batch(conn)
    assert batch is not None, "the response wrote outside any batch"
    assert batch.source == "web"
    assert batch.tool == "record_market_response"  # FormModal's own slug
    assert _events(conn, batch), "the batch carries no events to revert"

    # and the row moved sections: the refreshed panel rides along out of band
    assert 'id="pipeline-panel"' in response.text
    assert "hx-swap-oob" in response.text


def test_re_opening_the_form_corrects_the_answer_rather_than_filing_a_second(client):
    """CREATE OR EDIT, on the line the answer is about. A form that filed a
    second row every time it was saved would put one market on a client's
    marketing report twice, at two prices."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))
    url = f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response"

    client.post(url, data={"line_id": GL, "status": "quoted", "premium": "850,000"})
    client.post(url, data={"line_id": GL, "status": "quoted", "premium": "790,000"})

    rows = marketing_repo.responses_for_submission(conn, sub.id)
    assert len(rows) == 1
    assert rows[0].premium == 79_000_000
    assert submissions_repo.get(conn, sub.id).quoted_premium == 79_000_000


def test_what_the_pipeline_records_is_on_the_marketing_report(client):
    """THE DEFECT, END TO END. A premium entered here used to be invisible to
    the Marketing panel and to the workbook the client is sent, because the
    two tabs wrote two different homes. There is one home now, so the row the
    Pipeline created IS a row on the report."""
    from datetime import date

    from bookkit.services import marketing_report

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))
    market = orgs_repo.get(conn, sub.market_org_id)

    client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "850,000"},
    )

    report = marketing_report.compose(
        conn, str(sub.placement_id), today=date(2026, 8, 14)
    )
    rows = [row for block in report.blocks for row in block.rows]
    assert [(r.market, r.premium) for r in rows] == [(market.name, 85_000_000)]


def test_a_placement_that_has_declared_no_line_still_records_the_answer(client):
    """THE REGRESSION. Not one `placement_line` row exists on the seeded book,
    so a picker built only from what the placement has DECLARED was empty on
    every submission in it — forty of forty, over placements with four markets
    approached and two quoting $1.4M — and the Response form, which recorded an
    answer on every one of them before the responses moved onto their own rows,
    refused instead.

    The line is still ASKED. What changes is the offered set where the
    placement has said nothing (the book's living vocabulary, the same list the
    Marketing panel's assign control offers) and what saving MEANS: the answer
    declares the line it names, in the same batch, so the broker does not have
    to go and say it again on another tab first."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    placement_id = str(sub.placement_id)
    assert marketing_repo.placement_lines(conn, placement_id) == [], (
        "the seeded state this test is about is gone: the placement already "
        "declares a line of coverage"
    )
    url = f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response"

    opened = client.get(url)
    assert opened.status_code == 200
    assert "form-error" not in opened.text, "refused a form it can answer"
    assert f'value="{GL}"' in opened.text, (
        "the picker does not offer the book's own vocabulary"
    )

    saved = client.post(
        url,
        data={
            "line_id": GL,
            "status": "quoted",
            "responded_on": "2026-08-13",
            "premium": "850,000",
        },
    )
    assert saved.status_code == 200
    assert "form-error" not in saved.text

    rows = marketing_repo.responses_for_submission(conn, sub.id)
    assert [(r.line_id, r.premium) for r in rows] == [(GL, 85_000_000)]
    assert str(submissions_repo.get(conn, sub.id).status) == "quoted"

    # THE DECLARATION IS PART OF THE SAME ACT: the placement is marketing that
    # line now, and one Revert takes both back.
    assert marketing_repo.placement_line(conn, placement_id, GL) is not None, (
        "the answer did not start marketing the line it was recorded on"
    )
    batch = _latest_batch(conn)
    assert batch is not None and batch.tool == "record_market_response"
    touched = {event.entity_type for event in _events(conn, batch)}
    assert {"market_response", "placement_line"} <= touched, (
        f"the declaration is outside the answer's undo unit: {sorted(touched)}"
    )


def test_the_one_refusal_left_is_a_book_carrying_no_line_of_coverage(client):
    """A REFUSAL SAYS SOMETHING, AND WHAT IT SAYS IS TRUE. The only state this
    form cannot record in is a book with no line of coverage at all — there is
    nothing storable to offer — and the fix it names is the control that
    creates one from a name the book has never carried.

    It must NOT say the placement is marketing nothing. That sentence was
    false on all forty seeded submissions and contradicted the live quotes
    printed on the same screen (2026-08-26)."""
    from bookkit.repo import base as base_repo
    from bookkit.repo import lines as lines_repo

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    for line in lines_repo.all_lines(conn):
        base_repo.soft_delete(conn, "line_of_coverage", line.id)

    opened = client.get(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response"
    )
    assert opened.status_code == 200
    assert "form-error" in opened.text
    assert "<select" not in opened.text, "offered a picker with nothing in it"
    assert "this book has no line of coverage recorded yet" in opened.text
    assert "on this placement" not in opened.text, (
        "the refusal states a condition of the placement it did not check"
    )
    assert "Marketing" in opened.text, "the refusal does not name where to fix it"


def test_a_package_sent_through_a_wholesaler_does_not_gain_a_carrier(client):
    """The submission is addressed to whoever it was SENT to, which is the
    WHOLESALER when there is one in the chain. Recording the next line's
    answer against that org as the carrier would print RT Specialty in the
    Market column of a client's workbook as the paper. "Out to RT Specialty,
    carrier TBD" is the truth, and naming the paper is one cell away on the
    Marketing panel."""
    from bookkit.repo import marketing as marketing_repo
    from bookkit.services import marketing_entry

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))
    _marketed(conn, str(sub.placement_id), "auto")
    wholesaler = orgs_repo.get(conn, sub.market_org_id)
    # the book's own record that this org CARRIED the package rather than
    # wrote it: the Marketing panel's add-market row, on another line
    marketing_entry.approach(
        conn, str(sub.placement_id), "auto",
        sent_on=sub.sent_on, via_org_id=wholesaler.id, today="2026-08-14",
    )

    client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "850,000"},
    )

    written = next(
        r
        for r in marketing_repo.responses_for_submission(conn, sub.id)
        if r.line_id == GL
    )
    assert written.market_org_id is None, "claimed the wholesaler's paper"
    assert written.via_org_id == wholesaler.id


def test_two_answers_on_one_line_are_refused_rather_than_guessed(client):
    """A tower marketed at two attachments is ONE market answering ONE line
    twice, and this form has no attachment field to tell them apart. Picking
    one would overwrite a band the broker was not looking at, so it refuses
    and names the surface where each is edited where it sits."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))
    primary = marketing_repo.create_response(
        conn, sub.id, GL, market_org_id=sub.market_org_id,
        lim=500_000_00, premium=100_000_00,
    )
    excess = marketing_repo.create_response(
        conn, sub.id, GL, market_org_id=sub.market_org_id,
        attach=500_000_00, lim=1_000_000_00, premium=40_000_00,
    )

    refused = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "9,000"},
    )
    assert refused.status_code == 200
    assert "more than one answer recorded on that line" in refused.text
    assert "Marketing on the Program tab" in refused.text
    assert "9,000" in refused.text, "the refusal threw away what was typed"
    assert marketing_repo.get_response(conn, primary.id).premium == 100_000_00
    assert marketing_repo.get_response(conn, excess.id).premium == 40_000_00


def test_a_refused_response_keeps_the_typed_input(client):
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "garbage-not-money"},
    )
    assert response.status_code == 200, "a refusal is a message, never an error status"
    assert "garbage-not-money" in response.text, "the typed input was thrown away"
    assert "form-error" in response.text
    # nothing written
    assert str(submissions_repo.get(conn, sub.id).status) == "out"


def test_a_bound_response_without_a_tower_offers_no_bind(client):
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "bound", "responded_on": "2026-08-13"},
    )
    assert response.status_code == 200
    assert str(submissions_repo.get(conn, sub.id).status) == "bound"
    assert "/bind" not in response.text, "offered layers with no tower to bind into"


# --- the bind offer -----------------------------------------------------------


def _bindable(conn):
    """(org, submission, placement, layer) where the market is NOT already on
    the layer and a 1% share cannot over-sign.

    The seed signs every layer to 100%, so a fully-signed program gets a NEW
    top layer through the real seam (sync.add_layer) — an unplaced layer is
    exactly the state a just-bound market is bound into."""
    for sub in submissions_repo.outstanding(conn):
        if sub.placement_id is None:
            continue
        placement = placements_repo.get(conn, sub.placement_id)
        if not placement.program_path:
            continue
        market = orgs_repo.get(conn, sub.market_org_id)
        layers = sync.layer_details(conn, placement.id)
        if not layers:
            continue
        for layer in layers:
            seated = {p["carrier"] for p in layer["participants"]}
            if market.name in seated:
                continue
            if (layer["signed_pct"] or 0) > 99:
                continue
            return _org_of_placement(conn, sub.placement_id), sub, placement, layer
        # every layer is full — stack a pending one on top, gap-free
        top = max(ly["attach_cents"] + ly["limit_cents"] for ly in layers)
        diags = sync.add_layer(
            conn, placement.id, "Excess Test Layer", [],
            attach_cents=top, limit_cents=500_000_000,
        )
        assert diags.ok, [str(e) for e in diags.errors]
        fresh = next(
            ly for ly in sync.layer_details(conn, placement.id)
            if ly["name"] == "Excess Test Layer"
        )
        return _org_of_placement(conn, sub.placement_id), sub, placement, fresh
    pytest.skip("seed produced no outstanding submission on a file-linked placement")


def test_a_bound_response_offers_the_layers(client):
    conn = client.app.state.conn
    org, sub, placement, layer = _bindable(conn)
    _marketed(conn, placement.id)

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "bound", "responded_on": "2026-08-13"},
    )
    assert response.status_code == 200
    # the offer is labeled the way the TUI's Picker labels layers
    assert f"/pipeline/submissions/{sub.id}/bind" in response.text
    assert layer["name"] in response.text
    assert "xs" in response.text
    assert "% placed)" in response.text
    # and it is skippable — the shared form macro's Cancel
    assert "data-form-cancel" in response.text


def test_the_bind_writes_the_participant_into_the_file(client):
    conn = client.app.state.conn
    org, sub, placement, layer = _bindable(conn)
    market = orgs_repo.get(conn, sub.market_org_id)

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/bind",
        data={"layer_id": str(layer["id"]), "share_bps": "1"},
    )
    assert response.status_code == 200

    # THE FILE is the authority: the carrier must be in the towerkit JSON
    program = json.loads(Path(str(placement.program_path)).read_text())
    on_disk = {
        p["carrier"]
        for ly in program["layers"]
        if str(ly["id"]) == str(layer["id"])
        for p in ly.get("participants", [])
    }
    assert market.name in on_disk, "the participant never reached the file"

    batch = _latest_batch(conn)
    assert batch is not None
    assert batch.source == "web"
    assert batch.tool == "program_bind"  # the Program tab's own seam and name


def test_a_refused_bind_keeps_the_offer_open_with_input(client):
    conn = client.app.state.conn
    org, sub, placement, layer = _bindable(conn)

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/bind",
        data={"layer_id": str(layer["id"]), "share_bps": "not-a-share"},
    )
    assert response.status_code == 200
    assert "form-error" in response.text
    assert "not-a-share" in response.text, "the typed input was thrown away"


# --- opportunities ------------------------------------------------------------


@pytest.fixture
def client_org(client):
    conn = client.app.state.conn
    return orgs_repo.list_orgs(conn, kind="client")[0]


def test_creating_an_opportunity(client, client_org):
    conn = client.app.state.conn
    before = {o.id for o in opportunities_repo.for_org(conn, client_org.id)}

    response = client.post(
        f"/accounts/{client_org.ref}/pipeline/opportunities/new",
        data={"title": "Cyber upsell", "target_premium": "250,000"},
    )
    assert response.status_code == 200

    created = [
        o for o in opportunities_repo.for_org(conn, client_org.id) if o.id not in before
    ]
    assert len(created) == 1
    assert created[0].title == "Cyber upsell"
    assert created[0].target_premium == 25_000_000
    assert str(created[0].stage) == "identified"

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"
    assert batch.tool == "new_opportunity"
    assert _events(conn, batch)


def test_editing_an_opportunity(client, client_org):
    conn = client.app.state.conn
    opp = opportunities_repo.create(conn, client_org.id, "Editable deal")

    response = client.post(
        f"/accounts/{client_org.ref}/pipeline/opportunities/{opp.id}/edit",
        data={"title": "Renamed deal", "probability_pct": "60"},
    )
    assert response.status_code == 200
    fresh = opportunities_repo.get(conn, opp.id)
    assert fresh.title == "Renamed deal"
    assert fresh.probability_pct == 60
    batch = _latest_batch(conn)
    assert batch is not None and batch.tool == "edit_opportunity"


def test_advance_moves_exactly_one_gate(client, client_org):
    conn = client.app.state.conn
    opp = opportunities_repo.create(conn, client_org.id, "Gated deal")
    assert str(opp.stage) == "identified"

    response = client.post(
        f"/accounts/{client_org.ref}/pipeline/opportunities/{opp.id}/advance"
    )
    assert response.status_code == 200
    assert str(opportunities_repo.get(conn, opp.id).stage) == "qualified"
    batch = _latest_batch(conn)
    assert batch is not None and batch.tool == "advance_card" and batch.source == "web"


def test_advance_refuses_to_close_a_deal(client, client_org):
    """At `presented` the forward gate is won — a close, which the unconfirmed
    button must never make. The refusal says what to do instead."""
    conn = client.app.state.conn
    opp = opportunities_repo.create(conn, client_org.id, "Presented deal")
    for stage in ("qualified", "submitted", "quoted", "presented"):
        pipeline_svc.move_stage(conn, opp.id, stage)

    response = client.post(
        f"/accounts/{client_org.ref}/pipeline/opportunities/{opp.id}/advance"
    )
    assert response.status_code == 200
    assert "won or lost" in response.text
    assert str(opportunities_repo.get(conn, opp.id).stage) == "presented"


def test_the_close_confirm_get_writes_nothing(client, client_org):
    conn = client.app.state.conn
    opp = opportunities_repo.create(conn, client_org.id, "Confirmed deal")
    before = _latest_batch(conn)

    response = client.get(
        f"/accounts/{client_org.ref}/pipeline/opportunities/{opp.id}/close/won"
    )
    assert response.status_code == 200
    assert "Closed is closed" in response.text, "the confirm does not name the blast radius"
    assert str(opportunities_repo.get(conn, opp.id).stage) == "identified"
    after = _latest_batch(conn)
    assert (before.id if before else None) == (after.id if after else None), (
        "a confirm GET opened a batch — it wrote something"
    )


def test_won_from_any_open_stage_sets_probability_100(client, client_org):
    """Deals close from anywhere (DECISIONS.md) — the service owns the
    probability side-effect, and the route must be calling it."""
    conn = client.app.state.conn
    opp = opportunities_repo.create(conn, client_org.id, "Early win", probability_pct=20)
    assert str(opp.stage) == "identified"

    response = client.post(
        f"/accounts/{client_org.ref}/pipeline/opportunities/{opp.id}/close/won"
    )
    assert response.status_code == 200
    fresh = opportunities_repo.get(conn, opp.id)
    assert str(fresh.stage) == "won"
    assert fresh.probability_pct == 100
    assert fresh.closed_at is not None


def test_lost_sets_probability_0(client, client_org):
    conn = client.app.state.conn
    opp = opportunities_repo.create(conn, client_org.id, "Dead deal", probability_pct=75)
    pipeline_svc.move_stage(conn, opp.id, "qualified")

    response = client.post(
        f"/accounts/{client_org.ref}/pipeline/opportunities/{opp.id}/close/lost"
    )
    assert response.status_code == 200
    fresh = opportunities_repo.get(conn, opp.id)
    assert str(fresh.stage) == "lost"
    assert fresh.probability_pct == 0
    batch = _latest_batch(conn)
    assert batch is not None and batch.tool == "close_lost"


def test_a_closed_row_refuses_further_moves_and_renders_no_controls(client, client_org):
    conn = client.app.state.conn
    opp = opportunities_repo.create(conn, client_org.id, "Closed deal")
    pipeline_svc.move_stage(conn, opp.id, "lost")

    response = client.post(
        f"/accounts/{client_org.ref}/pipeline/opportunities/{opp.id}/advance"
    )
    assert response.status_code == 200
    assert "closed is closed" in response.text
    assert str(opportunities_repo.get(conn, opp.id).stage) == "lost"

    # D4: the page renders no stage controls on the closed row
    page = client.get(f"/accounts/{client_org.ref}/pipeline").text
    assert f"/pipeline/opportunities/{opp.id}/advance" not in page
    assert f"/pipeline/opportunities/{opp.id}/close/" not in page


# --- subjectivities -----------------------------------------------------------


def _a_quote(conn):
    """(org, submission) for a quote in hand."""
    for org in orgs_repo.list_orgs(conn, kind="client"):
        for row in submissions_repo.quoted_rows_for_org(conn, org.id):
            return org, submissions_repo.get(conn, row["id"])
    pytest.skip("seed produced no quotes in hand")


def test_the_subjectivity_lifecycle(client):
    conn = client.app.state.conn
    org, sub = _a_quote(conn)

    # add to the quote
    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/subjectivities/new",
        data={"description": "signed application", "due_on": "2026-08-28",
              "status": "outstanding"},
    )
    assert response.status_code == 200
    added = [
        s for s in submissions_repo.subjectivities_for(conn, sub.id)
        if s.description == "signed application"
    ]
    assert len(added) == 1
    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"
    assert batch.tool == "new_subjectivity"

    # it shows on the tab, with its edit control
    page = client.get(f"/accounts/{org.ref}/pipeline").text
    assert "signed application" in page
    assert f"/pipeline/subjectivities/{added[0].id}/edit" in page

    # edit: mark it met — status is a field on the form, the TUI's own shape
    response = client.post(
        f"/accounts/{org.ref}/pipeline/subjectivities/{added[0].id}/edit",
        data={"description": "signed application", "status": "met",
              "satisfied_on": "2026-08-14"},
    )
    assert response.status_code == 200
    fresh = submissions_repo.get_subjectivity(conn, added[0].id)
    assert str(fresh.status) == "met"
    assert fresh.satisfied_on == "2026-08-14"
    batch = _latest_batch(conn)
    assert batch is not None and batch.tool == "edit_subjectivity"

    # met means off the outstanding list, and off the quote's open count
    open_count, total = submissions_repo.subjectivity_counts(conn, sub.id)
    assert total >= 1 and open_count < total
    page = client.get(f"/accounts/{org.ref}/pipeline").text
    assert f"/pipeline/subjectivities/{added[0].id}/edit" not in page


def test_a_refused_subjectivity_keeps_the_typed_input(client):
    conn = client.app.state.conn
    org, sub = _a_quote(conn)
    before = len(submissions_repo.subjectivities_for(conn, sub.id))

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/subjectivities/new",
        data={"description": "loss runs to 8/1", "due_on": "5",
              "status": "outstanding"},
    )
    # "5" is a bare number, which is never a date (CLAUDE.md) — refused, and
    # everything typed survives in the re-rendered form
    assert response.status_code == 200
    assert "loss runs to 8/1" in response.text
    assert "form-error" in response.text
    assert len(submissions_repo.subjectivities_for(conn, sub.id)) == before


# --- ownership ----------------------------------------------------------------


def test_another_accounts_submission_is_a_404(client):
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    other = next(
        o for o in orgs_repo.list_orgs(conn, kind="client") if o.id != org.id
    )

    response = client.post(
        f"/accounts/{other.ref}/pipeline/submissions/{sub.id}/response",
        data={"status": "quoted"},
    )
    assert response.status_code == 404
    assert str(submissions_repo.get(conn, sub.id).status) == "out"


# --- the figures the package already carried ---------------------------------


def _with_stored_figures(conn):
    """(org, submission) for an outstanding package carrying typed quote
    figures on its own columns and NO response rows — the state 30 of the 40
    seeded submissions and every one of Grant's are in, and the one the
    Pipeline's Response form used to destroy."""
    from bookkit.repo import marketing as marketing_repo

    org, sub = _an_outstanding_submission(conn, file_linked=False)
    assert marketing_repo.responses_for_submission(conn, sub.id) == []
    submissions_repo.update(
        conn,
        sub.id,
        quoted_premium=140_000_000,
        quoted_limit=1_000_000_000,
        response_on="2026-07-28",
        quote_expires_on="2026-09-15",
    )
    return org, submissions_repo.get(conn, sub.id)


def test_the_first_answer_carries_the_figures_the_submission_already_recorded(client):
    """L1. THE SAME ACT THROUGH TWO DOORS, and it used to have two outcomes.

    The panel's `assign a line` moved a package's stored $1.4M quote onto the
    row that states it from then on; this form created the row EMPTY, the
    roll-up then made the rows the authority for all six columns, and the
    premium and limit went to NULL — on the daily driver's Response button,
    with nothing on the form saying so (r6 A/B, 2026-08-26).

    NOT a prefill: the form still shows every amount empty. The figure was
    already in the book and this moves it from a column to a row."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _with_stored_figures(conn)
    _marketed(conn, str(sub.placement_id))

    saved = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        # only the reply date is corrected — the exact act that lost both
        # figures on main's successor
        data={"line_id": GL, "status": "quoted", "responded_on": "2026-08-03"},
    )
    assert saved.status_code == 200

    rows = marketing_repo.responses_for_submission(conn, sub.id)
    assert len(rows) == 1
    assert rows[0].premium == 140_000_000, "the quoted premium was destroyed"
    assert rows[0].lim == 1_000_000_000, "the quoted limit was destroyed"
    assert rows[0].quote_expires_on == "2026-09-15", "the expiry was destroyed"
    assert rows[0].responded_on == "2026-08-03", "what was typed did not win"

    fresh = submissions_repo.get(conn, sub.id)
    assert fresh.quoted_premium == 140_000_000
    assert fresh.quoted_limit == 1_000_000_000
    assert fresh.quote_expires_on == "2026-09-15"
    assert fresh.response_on == "2026-08-03"


def test_what_the_broker_typed_wins_over_the_figure_the_package_carried(client):
    """The carry is a floor, never an override. A market that has just told us
    $790k is not quoting $1.4M because the package's old column says so."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _with_stored_figures(conn)
    _marketed(conn, str(sub.placement_id))

    client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "790,000"},
    )
    rows = marketing_repo.responses_for_submission(conn, sub.id)
    assert rows[0].premium == 79_000_000
    # and the ones the answer said nothing about still came across
    assert rows[0].lim == 1_000_000_000
    assert rows[0].quote_expires_on == "2026-09-15"


def test_a_second_line_carries_nothing_because_the_rows_are_already_the_authority(
    client,
):
    """THE PRECONDITION IS THE ROW SET, NOT THE FIELD. Once one response
    exists the submission's columns are a CACHE of it, and carrying them onto
    a second line would copy the GL's premium onto the Property row — the very
    second home the roll-up closed. `assign_line` refuses outside this
    precondition; this is the same rule read from the other side."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _with_stored_figures(conn)
    placement_id = str(sub.placement_id)
    _marketed(conn, placement_id)
    _marketed(conn, placement_id, "property")
    url = f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response"

    client.post(url, data={"line_id": GL, "status": "quoted", "premium": "790,000"})
    client.post(url, data={"line_id": "property", "status": "declined"})

    rows = {r.line_id: r for r in marketing_repo.responses_for_submission(conn, sub.id)}
    assert set(rows) == {GL, "property"}
    assert rows["property"].premium is None, (
        "a cached figure was copied onto a line no market priced"
    )
    assert rows["property"].lim is None
    assert rows["property"].quote_expires_on is None


# --- pulling a package, and putting it back ----------------------------------


def test_a_package_can_be_withdrawn_from_the_pipeline_row(client):
    """L2. NOTHING ON ANY SURFACE COULD WITHDRAW A SUBMISSION.

    `response_form`'s outcome picker offered the SUBMISSION statuses and was
    the one writer of `withdrawn` anywhere in the app; pointing that form at
    `market_response` gave it the response vocabulary, which rightly has no
    such word, and left `assign_line`, `approach` and `roll_up_submission` all
    refusing on a state nobody could enter (r6 blocker 2)."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)

    # the control is on the row, and the confirm writes nothing
    tab = client.get(f"/accounts/{org.ref}/pipeline")
    assert f"/pipeline/submissions/{sub.id}/withdraw" in tab.text
    confirm = client.get(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")
    assert confirm.status_code == 200
    assert "confirm-remove" in confirm.text
    assert str(submissions_repo.get(conn, sub.id).status) == "out", (
        "the confirm step wrote"
    )

    saved = client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")
    assert saved.status_code == 200
    assert str(submissions_repo.get(conn, sub.id).status) == "withdrawn"

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"
    assert batch.tool == "submission_withdraw"
    assert _events(conn, batch), "the withdrawal wrote outside any batch"


def test_a_withdrawn_package_is_still_readable_and_can_be_put_back(client):
    """IT HAS TO BE VISIBLE SOMEWHERE OR IT CANNOT BE UNDONE. A withdrawn
    package is in no Pipeline queue — 'out at market' is status='out' — so
    without its own section it left the tab entirely, taking the only control
    that could put it back with it."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")

    tab = client.get(f"/accounts/{org.ref}/pipeline")
    assert "Withdrawn" in tab.text
    assert f"/pipeline/submissions/{sub.id}/reinstate" in tab.text

    back = client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/reinstate")
    assert back.status_code == 200
    assert str(submissions_repo.get(conn, sub.id).status) == "out"
    batch = _latest_batch(conn)
    assert batch is not None and batch.tool == "submission_reinstate"


def test_reinstating_returns_the_package_to_what_its_rows_say(client):
    """NOT 'out' BY DEFAULT. A package pulled while a market was quoting comes
    back QUOTED — the status is derived from the response rows the way every
    other package's is, and a flat 'out' would tell the Pipeline no answer had
    arrived on a package holding one."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))
    client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "850,000"},
    )
    assert str(submissions_repo.get(conn, sub.id).status) == "quoted"

    client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")
    assert str(submissions_repo.get(conn, sub.id).status) == "withdrawn"
    client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/reinstate")

    fresh = submissions_repo.get(conn, sub.id)
    assert str(fresh.status) == "quoted", "the package came back as unanswered"
    assert fresh.quoted_premium == 85_000_000, "the figures were not re-derived"


def test_withdrawing_keeps_every_answer_the_markets_gave(client):
    """MARKETING THAT HAPPENED STAYS REPORTED. Withdrawing says we stopped
    pursuing the package, not that it never went out — the rows keep printing
    on the Marketing panel and in the client's workbook."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))
    client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "850,000"},
    )
    before = marketing_repo.responses_for_submission(conn, sub.id)
    client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")
    after = marketing_repo.responses_for_submission(conn, sub.id)
    assert [r.model_dump() for r in after] == [r.model_dump() for r in before]


def test_a_refusal_says_something_and_names_a_reachable_fix(client):
    """Both refusals name the other control, and both controls exist."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)

    not_pulled = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/reinstate"
    )
    assert not_pulled.status_code == 200
    assert "form-error" in not_pulled.text
    assert "not withdrawn" in not_pulled.text

    client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")
    twice = client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")
    assert twice.status_code == 200
    assert "form-error" in twice.text
    assert "Reinstate" in twice.text, "the refusal names no way forward"


def test_another_accounts_submission_is_not_withdrawable_from_this_one(client):
    """Both ids in the URL are claims. The same 404 for 'no such id' and
    'someone else's id', deliberately."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    other = next(
        o for o in orgs_repo.list_orgs(conn, kind="client") if o.id != org.id
    )
    assert client.post(
        f"/accounts/{other.ref}/pipeline/submissions/{sub.id}/withdraw"
    ).status_code == 404
    assert client.get(
        f"/accounts/{other.ref}/pipeline/submissions/{sub.id}/withdraw"
    ).status_code == 404


def test_reinstating_moves_the_status_once_not_through_out(client):
    """ONE WRITER ACTION IS ONE UNDO UNIT, and a package that comes back
    quoted was never momentarily 'out'. Writing a flat 'out' and letting the
    roll-up correct it lands the SAME final status by way of a second event —
    an intermediate state the book never occupied, sitting in the replay a
    Revert walks backwards."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))
    client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "850,000"},
    )
    client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")
    client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/reinstate")

    batch = _latest_batch(conn)
    assert batch is not None and batch.tool == "submission_reinstate"
    moves = [e for e in _events(conn, batch) if e.field == "status"]
    assert [(e.old_value, e.new_value) for e in moves] == [("withdrawn", "quoted")], (
        f"the status went through a state the book never held: {moves}"
    )


# --- what the form shows, and the way back from a wrong pick -----------------


def _answered(client, org, sub, line_id=GL, **fields):
    data = {"line_id": line_id, "status": "quoted", "premium": "1,200,000"}
    data.update(fields)
    return client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response", data=data
    )


def test_the_re_opened_form_shows_what_this_package_has_already_answered(client):
    """L3, PART ONE. Re-opened, this form was COMPLETELY BLANK on a package a
    market had already answered — no selected option, no value, nothing naming
    the line — so the only way to see the state was to save and look. That is
    what made one wrong pick expensive."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    placement_id = str(sub.placement_id)
    _marketed(conn, placement_id)
    _marketed(conn, placement_id, "property")
    _answered(client, org, sub, responded_on="2026-08-12")

    again = client.get(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response")
    assert again.status_code == 200
    assert "already answered" in again.text
    assert "$1,200,000" in again.text, "the figure the package holds is not shown"
    assert "2026-08-12" in again.text

    # AND IT IS SHOWN, NOT PRE-FILLED: no input carries the amount, so the
    # form still asks for every figure empty (data-entry-integrity §8).
    assert 'value="1,200,000"' not in again.text
    assert 'value="$1,200,000"' not in again.text


def test_the_line_already_answered_arrives_selected_and_says_what_saving_does(client):
    """L3, PARTS TWO AND THREE. `line_id` carried no initial, so re-picking was
    compulsory on every re-open — and nothing distinguished picking the line
    being corrected from picking a new one. Correcting the only answer a
    package has is what re-opening this form nearly always means, so that is
    the default, and it is one the reader can SEE."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    placement_id = str(sub.placement_id)
    _marketed(conn, placement_id)
    _marketed(conn, placement_id, "property")
    _answered(client, org, sub)

    again = client.get(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response")
    assert f'<option value="{GL}" selected>' in again.text, (
        "the one line this package has answered is not the default"
    )
    assert "saving corrects it" in again.text
    # the line nobody has answered is NOT marked as a correction
    prop = again.text.split('value="property"')[1].split("</option>")[0]
    assert "corrects" not in prop


def test_two_answered_lines_are_never_guessed_between(client):
    """The default exists because there is one honest answer. With two answers
    recorded there is no such thing, and the blank option is the truth."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    placement_id = str(sub.placement_id)
    _marketed(conn, placement_id)
    _marketed(conn, placement_id, "property")
    _answered(client, org, sub)
    _answered(client, org, sub, line_id="property", status="declined", premium="")

    again = client.get(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response")
    assert " selected>" not in again.text, "a line of coverage was guessed"


def test_the_change_list_says_which_answer_each_save_was(client):
    """L3, PART FOUR — AND THE WAY BACK. Recording Chubb on the GL and then
    filing a second answer against Property by mistake left two entries in the
    account's Recent changes rail both reading `record market response`, same
    market, same minute: the one surface that can undo the mistake could not
    say which of them to undo.

    REVERT-BY-NAME rather than a delete, deliberately: the erroneous act is
    "started marketing Property AND recorded an answer on it", one batch, and
    a delete on the response row would reverse half of it. The rail is on this
    same tab."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    placement_id = str(sub.placement_id)
    _marketed(conn, placement_id)
    _marketed(conn, placement_id, "property")
    market = orgs_repo.get(conn, sub.market_org_id).name

    _answered(client, org, sub)
    first = _latest_batch(conn)
    assert first is not None and first.tool == "record_market_response"
    assert first.summary == f"recorded what {market} said about General Liability"

    _answered(client, org, sub, premium="1,150,000")
    corrected = _latest_batch(conn)
    assert corrected is not None
    assert corrected.summary == (
        f"corrected what {market} said about General Liability"
    ), "a correction is indistinguishable from a first answer in the rail"

    _answered(client, org, sub, line_id="property")
    mistake = _latest_batch(conn)
    assert mistake is not None
    assert mistake.summary == f"recorded what {market} said about Property"
    assert mistake.ref != corrected.ref


def test_reverting_the_wrong_pick_takes_the_whole_act_back(client):
    """THE WAY BACK, DRIVEN — the r6 scenario exactly: two lines open, one
    market recorded on the GL, the form re-opened to correct the figure and
    Property picked by mistake. The batch the rail now NAMES puts it back, and
    the client's workbook stops saying that market quoted cover it never
    quoted.

    Both halves of the act go, whichever halves this one had: here the
    placement had already declared Property (which is why the picker offered
    it at all), so the batch is the response alone and the declaration is
    untouched — correctly, because nobody declared it by mistake. Where the
    answer DID declare the line, `test_a_placement_that_has_declared_no_line_
    still_records_the_answer` holds that both are in the one batch, and this
    revert takes whatever is in it."""
    from bookkit.repo import marketing as marketing_repo

    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    placement_id = str(sub.placement_id)
    _marketed(conn, placement_id)
    _marketed(conn, placement_id, "property")
    _answered(client, org, sub)
    _answered(client, org, sub, line_id="property")  # the wrong pick

    mistake = _latest_batch(conn)
    assert mistake is not None and "Property" in mistake.summary
    done = client.post(f"/accounts/{org.ref}/changes/{mistake.ref}/revert?tab=pipeline")
    assert done.status_code in (200, 204), done.text

    lines = {r.line_id for r in marketing_repo.responses_for_submission(conn, sub.id)}
    assert lines == {GL}, "the answer recorded in error is still on the book"
    # and the cache is re-derived from what survives, not restored to what it
    # happened to hold (services.batches._rederive_caches)
    assert submissions_repo.get(conn, sub.id).quoted_premium == 120_000_000


def test_a_quote_in_hand_can_be_withdrawn_from_the_row_it_is_read_on(client):
    """A PACKAGE IS SEEN IN TWO PLACES on this tab, and a control on only one
    of them is an affordance left behind. Withdrawing a quote we decided not to
    pursue is the commonest reason to pull a package at all, and that row lives
    in 'Quotes in hand' rather than in 'Out at market'."""
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)
    _marketed(conn, str(sub.placement_id))
    client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"line_id": GL, "status": "quoted", "premium": "850,000"},
    )
    tab = client.get(f"/accounts/{org.ref}/pipeline")
    quotes = tab.text.split('id="quotes-panel"')[1].split('id="submissions-panel"')[0]
    assert f"/pipeline/submissions/{sub.id}/withdraw" in quotes, (
        "a quote in hand cannot be withdrawn from the row it is read on"
    )

    client.post(f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/withdraw")
    assert str(submissions_repo.get(conn, sub.id).status) == "withdrawn"
    after = client.get(f"/accounts/{org.ref}/pipeline")
    assert "no quotes in hand" in after.text, "a pulled package is still being chased"
    assert f"/pipeline/submissions/{sub.id}/reinstate" in after.text
