"""D4: never render an inert control — wire it or don't render it.

Every full page the web serves joins PAGES below, and the scan asserts that
every interactive element on it is either genuinely wired (an <a> with a
real href, a <button>/<input> that submits a form with an action or carries
an hx-* attribute) or EXPLICITLY marked pending with aria-disabled="true" —
the app.css convention that also strips the hover affordance, so "looks
live, is dead" cannot happen by omission.

The TUI's twin is tests/test_dead_keys.py (every hinted key is bound); this
is the same invariant in the browser's vocabulary."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app

# Every full page (not fragment) the web serves. A new page JOINS THIS LIST
# in the same commit that adds its route.
PAGES: list[str] = [
    "/search",
    "/search?q=Borealis",
]


class _ControlScan(HTMLParser):
    """Interactive elements and whether each is wired.

    HTML forms cannot nest, so one flag tracks "inside a form with an
    action". hx-* on the element itself counts as wired wherever it appears
    (htmx buttons live outside forms here — the undo pill)."""

    def __init__(self) -> None:
        super().__init__()
        self._in_wired_form = False
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._in_wired_form = bool(a.get("action")) or any(
                k.startswith("hx-") for k in a
            )
            if not self._in_wired_form:
                self.violations.append(f"<form> with no action and no hx-*: {a}")
            return
        wired_by_htmx = any(k.startswith("hx-") for k in a)
        inert_ok = a.get("aria-disabled") == "true" or "disabled" in a
        if tag == "a":
            if not a.get("href") and not wired_by_htmx and not inert_ok:
                self.violations.append(f"<a> with no href: {a}")
        elif tag == "button":
            wired = wired_by_htmx or (
                self._in_wired_form and a.get("type", "submit") == "submit"
            )
            if not wired and not inert_ok:
                self.violations.append(f"inert <button>: {a}")
        elif tag in ("input", "select", "textarea"):
            if a.get("type") == "hidden":
                return
            wired = wired_by_htmx or self._in_wired_form
            if not wired and not inert_ok and "readonly" not in a:
                self.violations.append(f"inert <{tag}>: {a}")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_wired_form = False


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_every_rendered_control_is_wired_or_marked_pending(client, path):
    response = client.get(path)
    assert response.status_code == 200
    scan = _ControlScan()
    scan.feed(response.text)
    assert not scan.violations, f"{path}: {scan.violations}"
