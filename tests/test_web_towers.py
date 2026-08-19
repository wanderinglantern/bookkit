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
    page = client.get("/towers")

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
