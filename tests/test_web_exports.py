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


def test_every_download_route_is_reachable_from_the_drawer(client_and_org):
    """THE DRAWER'S WHOLE JOB is that no download is reachable only from the
    tab that happens to serve it — and it missed `work.xlsx`, the Work tab's
    own workbook (surface sweep, 2026-08-24).

    A LIST OF ROUTES, not a list of links: this walks every registered
    `/export/` GET and asserts the drawer offers each one, so the next
    download added anywhere goes red here instead of being reachable from one
    tab. `open-items.xlsx` and the three program artifacts are the ones that
    were already right.
    """
    import re

    client, org = client_and_org
    page = client.get("/exports").text

    routes = {
        path
        for path, methods in _get_routes(client.app)
        if "/export/" in path and "GET" in methods
    }
    assert routes, "no export routes found at all — the scan is broken"

    offered = set(re.findall(r'href="([^"]*/export/[^"]+)"', page))
    missing = []
    for path in sorted(routes):
        # the route template's own shape, as a regex over the hrefs rendered
        pattern = re.sub(r"\{[^}]+\}", "[^/]+", path)
        if not any(re.fullmatch(pattern, href) for href in offered):
            missing.append(path)
    assert not missing, (
        f"the exports drawer does not offer these download routes: {missing}"
    )


def test_the_exports_drawer_is_reachable_from_every_page(client_and_org):
    """It was linked from Today and from nowhere else, so the last step of the
    morning meant going home first. The top bar is the one nav every page
    renders."""
    client, org = client_and_org

    for path in ("/book", "/towers", "/markets", f"/accounts/{org.ref}/program"):
        page = client.get(path).text
        nav = page.split('class="topbar-nav"', 1)[1].split("</nav>", 1)[0]
        assert 'href="/exports"' in nav, f"no way to the exports drawer from {path}"

    drawer = client.get("/exports").text
    nav = drawer.split('class="topbar-nav"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/exports" class="topbar-nav-item is-current-section"' in nav, (
        "the drawer does not light its own nav item"
    )


def _get_routes(app) -> list[tuple[str, set[str]]]:
    """(path, methods) for every route the app serves.

    Walks the included routers rather than `app.routes`: FastAPI wraps each
    `include_router` in a holder whose own `path` is empty, so a scan of the
    top level finds four static mounts and nothing else — and a gate that
    finds nothing passes.
    """
    found: list[tuple[str, set[str]]] = []

    def walk(routes) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
                continue
            path = getattr(route, "path", None)
            if path:
                found.append((path, set(getattr(route, "methods", ()) or ())))

    walk(app.routes)
    return found


# --- the marketing report --------------------------------------------------


def test_the_marketing_workbook_downloads_without_a_program_file(client_and_org):
    """NO `_linked` here, deliberately. Marketing happens BEFORE a tower
    exists — every figure on this sheet lives in SQLite — so a download gated
    on a linked file would be unreachable on exactly the placements it is for.
    The same built-but-not-accessible class the open-items anchor hit."""
    from bookkit.repo import placements as placements_repo

    client, org = client_and_org
    placement = placements_repo.create(
        client.app.state.conn,
        org_id=org.id,
        program_name="unlinked, being marketed",
        period_from="2027-01-01",
        period_to="2028-01-01",
    )
    assert placement.program_path is None

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/export/marketing.xlsx"
    )

    assert got.status_code == 200
    assert got.content[:2] == b"PK", "not an xlsx archive"
    assert "attachment" in got.headers["content-disposition"]
    assert placement.ref in got.headers["content-disposition"]


def test_the_internal_marketing_workbook_is_named_as_such(client_and_org):
    client, org = client_and_org
    placement = _linked(client, org)

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/export/marketing.xlsx"
        "?audience=internal"
    )

    assert got.status_code == 200
    assert "marketing-internal.xlsx" in got.headers["content-disposition"]


def test_an_unknown_audience_is_refused_not_defaulted(client_and_org):
    """A typo must not fall through to the client sheet in one direction or
    leak the underwriter's own words in the other."""
    client, org = client_and_org
    placement = _linked(client, org)

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/export/marketing.xlsx"
        "?audience=internl"
    )

    assert got.content[:2] != b"PK"
    assert "audience" in got.text
