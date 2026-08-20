"""D4 (Grant, 2026-08-19): never draw an inert control — wire it or don't
render it. The TUI learned this as test_dead_keys.py (every key a hint line
names must be bound); this is the same arbiter for the web: every control a
page renders must resolve to a real route, and nothing may render as
admitted-dead chrome.

Three guards, meant to be inherited by every later phase:

1. no "Not wired yet" markers and no non-anchor nav items anywhere;
2. every hx-get / hx-post / href / form action a page renders matches a
   registered route with that method — a control pointing nowhere turns the
   suite red the day it is introduced;
3. every editable layer field (forms.inline.LAYER_FIELDS, the keys the cell
   routes accept) is REACHABLE from the page — the program tab plus each
   layer's details fragment must render a cell for every one of them. The
   share-edit routes shipped unreachable once and the policy/date cells
   twice (F6); this closes the class.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

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


def _pages(org) -> list[str]:
    return [
        "/book",
        "/search",
        "/search?q=Atomic",
        "/capture",
        "/today",
        "/calendar",
        "/towers",
        f"/accounts/{org.ref}/program",
        f"/accounts/{org.ref}/relationship",
        f"/accounts/{org.ref}/work",
        f"/accounts/{org.ref}/pipeline",
    ]


def test_no_control_admits_it_is_not_wired(client_and_org):
    client, org = client_and_org
    for page in _pages(org):
        html = client.get(page).text
        assert "Not wired yet" not in html, f"{page} renders admitted-dead chrome"
        assert 'aria-disabled="true"' not in html, (
            f"{page} renders a disabled placeholder — unbuilt is unrendered (D4)"
        )
        assert not re.search(r"<span[^>]*topbar-nav-item", html), (
            f"{page} renders a nav item that goes nowhere"
        )


def _resolves(app, path: str, method: str) -> bool:
    scope = {"type": "http", "path": path, "method": method, "headers": []}
    return any(route.matches(scope)[0] == Match.FULL for route in app.routes)


def test_every_rendered_action_resolves(client_and_org):
    """The URL is checked against the ROUTER, not fetched: hx-post targets
    write, and a test that has to fire every write to prove it exists would
    leave the fixture different on every run."""
    client, org = client_and_org
    app = client.app
    checked = 0
    for page in _pages(org):
        html = client.get(page).text
        # a form's action follows its method; only method="get" forms exist
        # outside htmx, and the topbar search is one (gap 3)
        get_form_actions = set(
            re.findall(r'<form[^>]*method="get"[^>]*action="([^"]+)"', html)
        ) | set(re.findall(r'<form[^>]*action="([^"]+)"[^>]*method="get"', html))
        for attr, method in (
            ("hx-get", "GET"),
            ("hx-post", "POST"),
            ("href", "GET"),
            ("action", "POST"),
        ):
            for url in re.findall(rf'{attr}="([^"]+)"', html):
                if attr == "action" and url in get_form_actions:
                    method = "GET"  # noqa: PLW2901
                path = url.split("?")[0].split("#")[0]
                if not path.startswith("/"):
                    continue
                checked += 1
                assert _resolves(app, path, method), (
                    f"{page} renders {attr}={url!r} and no {method} route matches"
                )
    assert checked > 40, f"the sweep found suspiciously few controls ({checked})"


def test_every_editable_layer_field_is_reachable(client_and_org):
    client, org = client_and_org
    from bookkit.repo import placements
    from bookkit.web.routes.program import _LAYER_CELLS

    conn = client.app.state.conn
    placement = next(
        p for p in placements.for_org(conn, org.id) if p.program_path
    )
    layers = sync.layer_details(conn, placement.id)
    assert layers, "the seeded book has no layers — test proves nothing"

    html = client.get(f"/accounts/{org.ref}/program").text
    for layer in layers:
        html += client.get(
            f"/accounts/{org.ref}/program/{placement.id}/layers/{layer['id']}/details"
        ).text

    rendered = set(re.findall(r'data-field="([^"]+)"', html))
    missing = set(_LAYER_CELLS) - rendered
    assert not missing, f"editable layer fields with no way in from the page: {missing}"
