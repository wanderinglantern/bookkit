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


def test_the_filters_carry_real_counts_and_the_open_filter_excludes_ok(client):
    """The review found the old assertions satisfied by static chrome (C24):
    the counts and the filtering are asserted against the data now."""
    import re

    from bookkit.repo import placements

    conn = client.app.state.conn
    placement, _ = _unplace_a_seat(conn)
    total = len(placements.all_linked(conn))

    page = client.get("/towers?show=open").text

    counts = {
        m.group(1): int(m.group(2))
        for m in re.finditer(
            r'href="/towers\?show=([a-z-]+)">[^<]*<span class="mono">(\d+)</span>',
            page,
        )
    }
    assert counts["all"] == total
    assert counts["open"] == 1, counts
    assert counts["needs-work"] >= 1
    # the open filter lists exactly the unplaced program — one card, its
    # reason the validator's own unplaced sentence
    assert page.count('<section class="tower-card') == 1
    assert "% placed" in page and "unplaced" in page
    assert "?layer=" in page, "the card does not land on the layer"


def test_a_gap_program_lands_in_needs_work_with_towerkits_warning(client):
    """A gap is a WARNING by design — and a warning IS work. The old page
    badged it; the queue must not file it under ok (review C8)."""
    from bookkit import sync
    from bookkit.repo import placements

    conn = client.app.state.conn
    placement = placements.all_linked(conn)[0]
    rows = sync.layer_details(conn, placement.id)
    # build the mid-stack case: pin a new layer on the current top of one
    # line, then remove the layer beneath it — the pinned attachment leaves
    # the hole a follows-underlying layer would have healed away
    below = max(
        (r for r in rows if not r["buffer"] and not r["statutory"]
         and r["attach_cents"] > 0 and len(r["applies_to"]) == 1),
        key=lambda r: r["top_cents"],
        default=None,
    )
    if below is None:
        below = max(
            (r for r in rows if not r["buffer"] and not r["statutory"]
             and r["attach_cents"] > 0),
            key=lambda r: r["top_cents"],
        )
    line = below["applies_to"][0]
    assert sync.add_layer(
        conn, placement.id, "Pinned Excess", [line],
        attach_cents=below["top_cents"], limit_cents=5_000_000_00,
    ).ok
    pinned = next(
        r["id"] for r in sync.layer_details(conn, placement.id)
        if r["name"] == "Pinned Excess"
    )
    # fully place it, so the GAP is the only new fact about this program
    assert sync.add_participant(
        conn, placement.id, str(pinned), "Gap Carrier", 10_000
    ).ok
    assert sync.remove_layer(conn, placement.id, str(below["id"])).ok

    page = client.get("/towers?show=needs-work").text

    assert placement.ref in page, (
        "a program with only warnings hides from the needs-work queue"
    )
    assert "towers-badge-warn" in page
    # and the reason line is a warning in towerkit's own words, never a
    # bookkit-composed health sentence
    assert "fully signed" not in page.split(placement.ref, 1)[1].split("</section>", 1)[0]
