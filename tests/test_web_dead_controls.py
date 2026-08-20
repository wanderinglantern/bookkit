"""D4: never render an inert control — wire it or don't render it.

Every new full page joins PAGES below. The scanner walks each page's real
HTML and refuses the two ways a control goes dead: an <a> with nowhere to go
(no href, `#`, or empty), and a <button> that neither submits a form nor
carries an htmx verb. Controls that are deliberately PENDING are exempt only
when they say so themselves (aria-disabled="true" with a title naming why) —
the pending treatment is the existing pages' explicit contract, not a
loophole, and a page listed here should prefer rendering nothing at all.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app

# every full page shipped with this guard in force — path (optionally with a
# query) as a browser would request it
PAGES = [
    "/capture",
]


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


class _ControlScan(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.dead: list[str] = []
        self._form_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form":
            self._form_depth += 1
            if not a.get("action") and not a.get("hx-post") and not a.get("hx-get"):
                self.dead.append("<form> with no action and no hx verb")
        if a.get("aria-disabled") == "true":
            # the explicit pending treatment: allowed only when it names why
            if not a.get("title"):
                self.dead.append(f"<{tag}> aria-disabled without a title saying why")
            return
        if tag == "a":
            href = a.get("href") or ""
            if href in ("", "#"):
                self.dead.append("<a> with nowhere to go")
        if tag == "button":
            wired = (
                (self._form_depth > 0 and a.get("type", "submit") == "submit")
                or any(k.startswith("hx-") for k in a)
                or any(k.startswith("data-") for k in a)
            )
            if not wired:
                self.dead.append("<button> that neither submits nor carries a verb")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._form_depth:
            self._form_depth -= 1


@pytest.mark.parametrize("path", PAGES)
def test_the_page_renders_no_dead_control(client: TestClient, path: str) -> None:
    page = client.get(path)
    assert page.status_code == 200, f"{path} did not render"
    scan = _ControlScan()
    scan.feed(page.text)
    assert not scan.dead, f"{path} renders dead controls: {scan.dead}"
