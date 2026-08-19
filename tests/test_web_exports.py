"""Downloads — the file-response gap closes (phase 4).

The decision, recorded in DECISIONS.md: downloads are PLAIN ANCHOR GETs
answering Content-Disposition: attachment — no htmx (browsers handle download
navigation natively; a swap contract adds nothing), artifacts rendered to a
per-request temp dir by towerkit's own renderers, or by
services.export_open_items which this phase CALLS and never modifies.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


def _linked(client, org):
    from bookkit.repo import placements

    return next(
        p
        for p in placements.for_org(client.app.state.conn, org.id)
        if p.program_path
    )


def test_the_tower_svg_downloads_with_the_renderers_own_words(client_and_org):
    client, org = client_and_org
    placement = _linked(client, org)

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/export/tower.svg"
    )

    assert got.status_code == 200
    assert "svg" in got.headers["content-type"]
    assert "attachment" in got.headers["content-disposition"]
    assert placement.ref in got.headers["content-disposition"]
    # the agreement rule: the words in the artifact are the RENDERER's
    assert placement.program_name in got.text


def test_the_tower_pdf_downloads(client_and_org):
    client, org = client_and_org
    placement = _linked(client, org)

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/export/tower.pdf"
    )

    assert got.status_code == 200
    assert got.content[:5] == b"%PDF-"
    assert "attachment" in got.headers["content-disposition"]


def test_the_schematic_workbook_downloads(client_and_org):
    client, org = client_and_org
    placement = _linked(client, org)

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/export/schematic.xlsx"
    )

    assert got.status_code == 200
    assert got.content[:2] == b"PK", "not an xlsx archive"
    assert "attachment" in got.headers["content-disposition"]


def test_the_open_items_workbook_downloads(client_and_org):
    client, org = client_and_org

    got = client.get(f"/accounts/{org.ref}/export/open-items.xlsx")

    assert got.status_code == 200
    assert got.content[:2] == b"PK"
    assert org.ref in got.headers["content-disposition"]


def test_an_unlinked_placement_gets_a_readable_refusal_page(client_and_org):
    client, org = client_and_org
    from bookkit.repo import placements as placements_repo

    bare = placements_repo.create(
        client.app.state.conn, org.id, "No File", "2026-05-01", "2027-05-01"
    )

    got = client.get(f"/accounts/{org.ref}/program/{bare.id}/export/tower.svg")

    assert got.status_code == 200
    assert "no program file linked" in got.text
    assert "content-disposition" not in got.headers, "a refusal downloaded as a file"


def test_a_foreign_placement_is_not_exportable(client_and_org):
    client, org = client_and_org
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo

    conn = client.app.state.conn
    other = next(
        o for o in orgs_repo.list_orgs(conn, kind="client") if o.id != org.id
    )
    foreign = placements_repo.for_org(conn, other.id)[0]

    got = client.get(
        f"/accounts/{org.ref}/program/{foreign.id}/export/tower.svg"
    )

    assert got.status_code == 404


def test_the_export_links_render_on_the_page(client_and_org):
    client, org = client_and_org
    placement = _linked(client, org)

    page = client.get(f"/accounts/{org.ref}/program").text

    base = f"/accounts/{org.ref}/program/{placement.id}/export"
    for artifact in ("tower.svg", "tower.pdf", "schematic.xlsx"):
        assert f'href="{base}/{artifact}"' in page, f"no link to {artifact}"
    assert f'href="/accounts/{org.ref}/export/open-items.xlsx"' in page
