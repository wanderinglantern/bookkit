"""No page renders an inert control (D4): everything clickable resolves.

The web's version of tests/test_dead_keys.py. Every URL a page wires into a
control — hx-get, hx-post, a form's action — must match a registered route
for its method, or the click produces no swap, no message and no change,
which reads as a broken app (the dead-nav defect the AE review called the
worst thing on the web surface).

PAGES lists one representative URL per page under guarantee. A new page
joins the list in the same commit that builds it.

Controls that are deliberately pending are exempt only when they carry
aria-disabled and no hx-* attribute at all (the contacts panel's
mark-primary treatment) — a control that LOOKS live must BE live.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

from bookkit.web.app import create_app

# (name, path template) — {ref} is filled with a real client account's ref.
PAGES = [
    ("pipeline", "/accounts/{ref}/pipeline"),
]


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _resolves(app, method: str, path: str) -> bool:
    scope = {"type": "http", "method": method, "path": path}
    for route in app.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return True
    return False


def _controls(html: str) -> list[tuple[str, str]]:
    """(method, url) for every wired control in the page."""
    out: list[tuple[str, str]] = []
    for attr, method in (("hx-get", "GET"), ("hx-post", "POST")):
        for url in re.findall(rf'{attr}="([^"]+)"', html):
            out.append((method, url))
    # forms post to their action (the house pattern: method=post + hx-post)
    for url in re.findall(r'<form[^>]+action="([^"]+)"', html):
        out.append(("POST", url))
    return out


@pytest.mark.parametrize("name,template", PAGES, ids=[p[0] for p in PAGES])
def test_every_control_on_the_page_resolves(client, name, template):
    from bookkit.repo import orgs

    org = orgs.list_orgs(client.app.state.conn, kind="client")[0]
    page = client.get(template.format(ref=org.ref))
    assert page.status_code == 200

    controls = _controls(page.text)
    assert controls, f"the {name} page renders no controls at all — wrong URL?"
    for method, url in controls:
        path = url.split("?")[0]
        assert path.startswith("/"), f"control points off-site: {url}"
        assert _resolves(client.app, method, path), (
            f"{name}: {method} {url} matches no route — an inert control (D4)"
        )
