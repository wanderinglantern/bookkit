"""D4 on every page: never render an inert control — wire it or don't render
it. The one sanctioned middle state is the PENDING treatment (a span with
aria-disabled="true" and a title saying it is a later task), which is
deliberately not a <button>.

So the page-level rule is checkable: every <button> must DO something (an
htmx verb, a submit, or one of the delegated data-* hooks inline-cell.js
owns), and every aria-disabled control must say why it is pending. Each
feature branch adds the pages it ships to PAGES — the same accretion
discipline as tests/test_dead_keys.py on the TUI side.

The XOR half of this rule (a control may be wired or marked pending, never
both, never neither) is asserted per-class-marker in test_web_account.py's
_assert_inert_controls_are_consistently_marked; this file is the per-PAGE
sweep over every button.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app

# Routes rendered and swept. "{ref}" is replaced with the first seeded
# client's ref at run time.
PAGES = [
    "/book",
    "/accounts/{ref}/relationship",
    "/accounts/{ref}/program",
    "/accounts/{ref}/work",
    "/accounts/{ref}/pipeline",
    # gap 5's two form fragments — a form is a page's worth of controls too
    "/book/accounts/new",
    "/accounts/{ref}/edit",
]

# What counts as "this button does something". Only verbs and the delegated
# hooks count — hx-swap/hx-target alone modify a verb that must be present
# (the F8 lesson from test_web_account.py).
_WIRED = (
    "hx-get", "hx-post", "hx-delete", "hx-put",
    'type="submit"',
    "data-form-cancel", "data-toast-close",
)


@pytest.fixture
def client_and_ref(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs

    ref = orgs.list_orgs(app.state.conn, kind="client")[0].ref
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, ref


@pytest.mark.parametrize("page", PAGES)
def test_every_button_on_the_page_is_wired(client_and_ref, page):
    client, ref = client_and_ref
    response = client.get(page.format(ref=ref))
    assert response.status_code == 200, page

    for tag in re.findall(r"<button[^>]*>", response.text):
        assert any(marker in tag for marker in _WIRED), (
            f"{page}: button renders live but does nothing: {tag}"
        )
        assert 'aria-disabled="true"' not in tag, (
            f"{page}: a wired <button> must not also claim to be pending: {tag}"
        )


@pytest.mark.parametrize("page", PAGES)
def test_every_pending_control_says_so(client_and_ref, page):
    """aria-disabled without a title is a control that looks broken instead
    of pending — the refusal-says-something rule, applied to chrome."""
    client, ref = client_and_ref
    response = client.get(page.format(ref=ref))
    for tag in re.findall(r"<[a-zA-Z][^>]*aria-disabled=\"true\"[^>]*>", response.text):
        assert "title=" in tag, f"{page}: pending control gives no reason: {tag}"
