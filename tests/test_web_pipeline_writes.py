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


# --- recording a market response ----------------------------------------------


def test_a_response_moves_the_submission_and_stores_the_quote(client):
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={
            "status": "quoted",
            "response_on": "2026-08-13",
            "quoted_premium": "850,000",
            "quoted_limit": "10,000,000",
            "quote_expires_on": "2026-09-12",
        },
    )
    assert response.status_code == 200

    fresh = submissions_repo.get(conn, sub.id)
    assert str(fresh.status) == "quoted"
    assert fresh.quoted_premium == 85_000_000  # cents: entry accepts cents
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


def test_a_refused_response_keeps_the_typed_input(client):
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"status": "quoted", "quoted_premium": "garbage-not-money"},
    )
    assert response.status_code == 200, "a refusal is a message, never an error status"
    assert "garbage-not-money" in response.text, "the typed input was thrown away"
    assert "form-error" in response.text
    # nothing written
    assert str(submissions_repo.get(conn, sub.id).status) == "out"


def test_a_bound_response_without_a_tower_offers_no_bind(client):
    conn = client.app.state.conn
    org, sub = _an_outstanding_submission(conn, file_linked=False)

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"status": "bound", "response_on": "2026-08-13"},
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

    response = client.post(
        f"/accounts/{org.ref}/pipeline/submissions/{sub.id}/response",
        data={"status": "bound", "response_on": "2026-08-13"},
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
