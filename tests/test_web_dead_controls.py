"""D4: never render an inert control — wire it or don't render it.

Every page the web surface ships joins PAGES below. The check is
mechanical: a <button> must be wired (hx-get/hx-post, a submit inside a
form, or one of the two delegated data- handlers inline-cell.js owns), an
<a> must go somewhere, and the one sanctioned pending treatment — a SPAN
carrying aria-disabled — must carry a title saying why it is dead and
where the action lives instead (Grant's bar: a control that looks live
and is dead is worse than one that says so up front)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app

# Fixed-path pages. Account tabs need a seeded ref and are appended by the
# test itself from the fixture — new account-relative pages join _ACCOUNT_TABS.
PAGES: list[str] = [
    "/team",
]

_ACCOUNT_TABS: list[str] = [
    "relationship",
]

# what counts as "wired" on a <button>
_WIRED = ("hx-get", "hx-post", "hx-delete", "data-form-cancel", "data-toast-close")


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _first_org_ref(client: TestClient) -> str:
    from bookkit.repo import orgs

    return orgs.list_orgs(client.app.state.conn, kind="client")[0].ref


def _page_urls(client: TestClient) -> list[str]:
    ref = _first_org_ref(client)
    return PAGES + [f"/accounts/{ref}/{tab}" for tab in _ACCOUNT_TABS]


def _tags(html: str, name: str) -> list[str]:
    return re.findall(rf"<{name}\b[^>]*>", html)


def test_every_rendered_control_is_wired_or_says_why_not(client):
    for url in _page_urls(client):
        response = client.get(url)
        assert response.status_code == 200, url
        html = response.text
        for tag in _tags(html, "button"):
            wired = any(attr in tag for attr in _WIRED) or 'type="submit"' in tag
            assert wired, f"{url}: dead <button>: {tag}"
        for tag in _tags(html, "a"):
            match = re.search(r'href="([^"]*)"', tag)
            assert match and match.group(1), f"{url}: <a> with nowhere to go: {tag}"
        for tag in re.findall(r"<[a-z]+\b[^>]*aria-disabled[^>]*>", html):
            assert "title=" in tag, (
                f"{url}: pending control with no explanation: {tag}"
            )
            assert not re.match(r"<(button|a)\b", tag), (
                f"{url}: a real control marked aria-disabled — render a span "
                f"or wire it: {tag}"
            )


def test_submit_buttons_sit_inside_a_form_that_posts(client):
    """type=submit counts as wired only when an enclosing <form> actually
    goes somewhere — method=get, an action, or an hx- verb."""
    for url in _page_urls(client):
        html = client.get(url).text
        for form in re.findall(r"<form\b[^>]*>", html):
            assert (
                "hx-post" in form or "hx-get" in form
                or 'method="get"' in form or "action=" in form
            ), f"{url}: <form> that submits nowhere: {form}"
