"""The Towers page — every drawn tower across the book, read-only, linking
into each account's Program tab. A broken file yields a badge and its
reasons, never a 500 that hides every other tower."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def test_every_linked_placement_is_drawn_and_no_unlinked_one(client):
    from bookkit.repo import placements

    conn = client.app.state.conn
    # ?show=all — the queue's default filter is "needs work" (design 2D),
    # and a clean book would honestly show nothing there.
    page = client.get("/towers?show=all")

    assert page.status_code == 200
    for placement in placements.all_linked(conn):
        assert placement.ref in page.text
    from bookkit.repo import orgs

    for org in orgs.list_orgs(conn, kind="client"):
        for p in placements.for_org(conn, org.id):
            if not p.program_path:
                assert p.ref not in page.text, f"unlinked {p.ref} on the towers page"


def test_a_corrupted_file_is_a_badge_not_a_500(client):
    from bookkit.repo import placements

    conn = client.app.state.conn
    victim = placements.all_linked(conn)[0]
    Path(victim.program_path).write_text("{not json")

    page = client.get("/towers")

    assert page.status_code == 200
    assert victim.ref in page.text
    assert "error" in page.text


def test_the_nav_reaches_towers_from_everywhere(client):
    from bookkit.repo import orgs

    org = orgs.list_orgs(conn=client.app.state.conn, kind="client")[0]
    for page_url in ("/book", f"/accounts/{org.ref}/program"):
        page = client.get(page_url).text
        assert 'href="/towers"' in page


def test_a_deleted_account_does_not_500_the_towers_page(client):
    """Org deletion is a soft delete with no cascade to placements, so a
    linked placement can outlive its account — the page renders it unlinked
    as "(deleted account)" instead of 500ing every other tower (fresh-eyes
    review, phase 4)."""
    from bookkit.repo import base, placements

    conn = client.app.state.conn
    victim = placements.all_linked(conn)[0]
    base.soft_delete(conn, "org", victim.org_id, note="review regression")

    page = client.get("/towers?show=all")

    assert page.status_code == 200
    assert "(deleted account)" in page.text
    assert victim.ref in page.text


def _unplace_a_seat(conn):
    """Take one market off one layer, leaving real unplaced capacity — and
    return (placement, layer_id, carrier) so the test can point at it."""
    from bookkit import sync
    from bookkit.repo import placements

    for placement in placements.all_linked(conn):
        for layer in sync.layer_details(conn, placement.id):
            if layer["participants"] and not layer["buffer"] and not layer["statutory"]:
                carrier = layer["participants"][0]["carrier"]
                assert sync.remove_participant(
                    conn, placement.id, layer["id"], carrier
                ).ok
                return placement, layer["id"]
    raise AssertionError("the seeded book has no seat to take off")


def test_the_queue_states_the_reason_and_lands_on_the_layer(client):
    """Design 2D: a card states the one fact that would make you open it —
    towerkit's own layer-unplaced sentence — and opening it lands on the
    layer that fact is about, not the top of the program."""
    conn = client.app.state.conn
    placement, layer_id = _unplace_a_seat(conn)

    page = client.get("/towers")  # the default filter is needs-work

    assert page.status_code == 200
    assert "unplaced" in page.text, "the reason line does not state the fact"
    assert f"?layer={layer_id}" in page.text, (
        "opening the card does not land on the layer the fact is about"
    )


def test_the_filters_carry_counts_and_the_order_note(client):
    conn = client.app.state.conn
    _unplace_a_seat(conn)

    page = client.get("/towers?show=open")

    assert page.status_code == 200
    assert "Needs work" in page.text and "Open capacity" in page.text
    assert "the validator decides this order" in page.text
    # the open filter shows the unplaced program and only unplaced ones
    assert "unplaced" in page.text
