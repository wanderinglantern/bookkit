"""The Compare screen (spec D8 slice 5): towerkit's compare_programs as a
delta table. The pair auto-detects by renewal adjacency (expiring period_to
== proposed period_from, same account, linked), a picker answers ambiguity,
`?with=` overrides — never a guess. Read-only; no tower graphic, per the
spec's own recommend-against."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import sync
from bookkit.web.app import create_app


@pytest.fixture
def client_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o
        for o in orgs.list_orgs(conn, kind="client")
        if any(p.program_path for p in placements.for_org(conn, o.id))
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _renewed_pair(client, org):
    """Renew the linked placement through the real route, then change a share
    on the clone so the delta has something to say."""
    from bookkit.repo import placements as placements_repo

    conn = client.app.state.conn
    expiring = next(
        p for p in placements_repo.for_org(conn, org.id) if p.program_path
    )
    renewed = client.post(f"/accounts/{org.ref}/program/{expiring.id}/renew")
    assert renewed.status_code == 200
    proposed = next(
        p
        for p in placements_repo.for_org(conn, org.id)
        if p.program_path and p.id != expiring.id
        and p.period_from == expiring.period_to
    )
    seated = next(
        ly for ly in sync.layer_details(conn, proposed.id) if ly["participants"]
    )
    carrier = seated["participants"][0]["carrier"]
    assert sync.update_participant(
        conn, proposed.id, str(seated["id"]), carrier, share_bps=2_500
    ).ok
    return expiring, proposed, carrier


def test_the_adjacent_pair_auto_detects_and_the_delta_reads(client_and_org):
    client, org = client_and_org
    expiring, proposed, carrier = _renewed_pair(client, org)

    page = client.get(f"/accounts/{org.ref}/program/{proposed.id}/compare")

    assert page.status_code == 200
    assert expiring.ref in page.text and proposed.ref in page.text
    assert "RENEWED" in page.text
    assert carrier in page.text
    assert "25%" in page.text, "the changed share is not in the delta"


def test_the_compare_control_renders_for_linked_programs(client_and_org):
    client, org = client_and_org
    from bookkit.repo import placements as placements_repo

    linked = next(
        p
        for p in placements_repo.for_org(client.app.state.conn, org.id)
        if p.program_path
    )

    page = client.get(f"/accounts/{org.ref}/program").text

    assert f'href="/accounts/{org.ref}/program/{linked.id}/compare"' in page


def test_no_adjacent_partner_offers_the_picker(client_and_org):
    client, org = client_and_org
    from bookkit.repo import placements as placements_repo

    linked = next(
        p
        for p in placements_repo.for_org(client.app.state.conn, org.id)
        if p.program_path
    )

    page = client.get(f"/accounts/{org.ref}/program/{linked.id}/compare")

    assert page.status_code == 200
    assert "Compare" in page.text
    assert "with…" in page.text or "no other linked program" in page.text


def test_with_overrides_the_auto_detect(client_and_org):
    client, org = client_and_org
    expiring, proposed, _ = _renewed_pair(client, org)

    page = client.get(
        f"/accounts/{org.ref}/program/{expiring.id}/compare?with={proposed.id}"
    )

    assert page.status_code == 200
    assert "RENEWED" in page.text


def test_a_foreign_with_is_refused(client_and_org):
    client, org = client_and_org
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo

    conn = client.app.state.conn
    linked = next(
        p for p in placements_repo.for_org(conn, org.id) if p.program_path
    )
    other = next(
        o for o in orgs_repo.list_orgs(conn, kind="client") if o.id != org.id
    )
    foreign = placements_repo.for_org(conn, other.id)[0]

    page = client.get(
        f"/accounts/{org.ref}/program/{linked.id}/compare?with={foreign.id}"
    )

    assert page.status_code == 404
