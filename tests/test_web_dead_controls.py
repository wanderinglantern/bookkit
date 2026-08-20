"""D4 — never render an inert control: wire it or don't render it.

Every page the web surface serves joins PAGES below and gets scanned for
controls that promise an action they cannot perform:

- a <button> must be wired — an hx-get/hx-post of its own, a
  data-form-cancel (the delegated close in inline-cell.js), or it is a
  submit button inside a <form> that itself posts somewhere;
- an <a> must go somewhere (a real href, never "" or "#");
- the ONE sanctioned exception is an element that says so out loud:
  aria-disabled="true" plus a title explaining what is not wired yet (the
  book page's filter pill pattern). An aria-disabled without a title is a
  dead control with a costume on, and fails.

The scan is over the rendered page, not the templates, so a control a
template renders conditionally is judged in the state a user actually sees.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app

# Route templates for every page this suite covers. Placeholders are filled
# by _resolve below from the seeded book. NEW PAGES JOIN THIS LIST in the
# same commit that adds them.
PAGES: list[str] = [
    "/markets",
    "/markets/{market_ref}",
]


class _ControlScan(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.dead: list[str] = []
        self._form_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "form":
            self._form_depth += 1
            if not (a.get("hx-get") or a.get("hx-post") or a.get("action")):
                self.dead.append(f"<form> with no action or hx verb: {a}")
        if a.get("aria-disabled") == "true":
            if not a.get("title"):
                self.dead.append(f"aria-disabled <{tag}> with no explaining title: {a}")
            return  # explicitly-pending affordance, sanctioned
        if tag == "button":
            wired = (
                a.get("hx-get")
                or a.get("hx-post")
                or "data-form-cancel" in a
                or (self._form_depth > 0 and a.get("type", "submit") == "submit")
            )
            if not wired:
                self.dead.append(f"inert <button>: {a}")
        if tag == "a":
            href = a.get("href", "")
            if (not href or href == "#") and not (a.get("hx-get") or a.get("hx-post")):
                self.dead.append(f"<a> that goes nowhere: {a}")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._form_depth:
            self._form_depth -= 1


def _resolve(url: str, conn) -> str:
    from bookkit.repo import orgs

    if "{market_ref}" in url:
        market = orgs.list_orgs(conn, kind="market")[0]
        url = url.replace("{market_ref}", market.ref)
    return url


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


@pytest.mark.parametrize("url", PAGES)
def test_no_dead_controls_on(url: str, client: TestClient) -> None:
    resolved = _resolve(url, client.app.state.conn)
    page = client.get(resolved)
    assert page.status_code == 200, f"{resolved} did not render"
    scan = _ControlScan()
    scan.feed(page.text)
    assert not scan.dead, f"{resolved} renders dead controls:\n" + "\n".join(scan.dead)
