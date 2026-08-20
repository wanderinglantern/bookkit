"""GET /search — the TUI's `/` search modal as a page (gap 3).

The one load-bearing security test here is the script-tag query: the page
echoes q back into its own input, so an unescaped echo is reflected XSS in
the most-typed-into box in the app. Jinja autoescape is the guard; the test
proves the guard is actually in the path (a hand-built HTMLResponse would
pass every other test and fail this one).

Everything else mirrors repo/search.py's contract: FTS5 over orgs, contacts
and interactions plus the email LIKE pass (email is NOT in the FTS index —
see _by_email's docstring), grouped under kind headers, every hit linking to
the owning ACCOUNT because that is what the TUI's enter key opens too
(tui/screens/search.py dismisses with org_id, never the entity)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def app_and_conn(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app.state.conn


def test_an_org_name_finds_the_org_with_a_working_link(app_and_conn):
    client, conn = app_and_conn
    from bookkit.repo import orgs

    org = next(o for o in orgs.list_orgs(conn, kind="client") if "Borealis" in o.name)
    response = client.get("/search", params={"q": "Borealis"})
    assert response.status_code == 200
    assert org.name in response.text
    assert "Accounts" in response.text
    # the link is to the account's relationship tab — and it must WORK
    href = f'href="/accounts/{org.ref}/relationship"'
    assert href in response.text
    followed = client.get(f"/accounts/{org.ref}/relationship")
    assert followed.status_code == 200


def test_a_contact_email_finds_the_contact(app_and_conn):
    """Email is answered by repo.search's LIKE pass, not FTS — the page must
    carry those hits like any other, linked to the OWNING account."""
    client, conn = app_and_conn
    from bookkit.repo import contacts, orgs

    org = orgs.list_orgs(conn, kind="client")[0]
    contact = contacts.for_org(conn, org.id)[0]
    assert contact.email, "fixture must seed contact emails"
    response = client.get("/search", params={"q": contact.email})
    assert response.status_code == 200
    assert "Contacts" in response.text
    assert contact.first_name in response.text
    assert contact.last_name in response.text
    # a contact hit opens the account it belongs to, same as the TUI
    assert f'href="/accounts/{org.ref}/relationship"' in response.text


def test_the_interaction_kind_appears(app_and_conn):
    """Every seeded interaction body starts "Seed interaction #…" (seed.py),
    and "Seed" matches nothing else — so the Interactions group must render,
    and under its own header."""
    client, _ = app_and_conn
    response = client.get("/search", params={"q": "Seed interaction"})
    assert response.status_code == 200
    assert "Interactions" in response.text
    assert "Seed interaction" in response.text


def test_a_script_query_comes_back_escaped(app_and_conn):
    """The query is echoed into the page's own input — reflected XSS if the
    template ever stops autoescaping (or the route grows a hand-built
    HTMLResponse without markupsafe)."""
    client, _ = app_and_conn
    evil = '<script>alert("q")</script>'
    response = client.get("/search", params={"q": evil})
    assert response.status_code == 200
    assert "<script>alert" not in response.text
    assert "&lt;script&gt;" in response.text


def test_empty_q_renders_just_the_box(app_and_conn):
    """Missing q and blank q are both the page with the box — never a 422
    (FastAPI's default for a missing required param) and never an error."""
    client, _ = app_and_conn
    for url in ("/search", "/search?q=", "/search?q=%20%20"):
        response = client.get(url)
        assert response.status_code == 200
        assert 'name="q"' in response.text


def test_a_hit_whose_org_is_gone_renders_unlinked(app_and_conn):
    """The email LIKE pass joins org without an alive filter, so a contact of
    a merged-away org can still hit. repo.orgs.get raises KeyError for a
    soft-deleted org — the page renders that hit UNLINKED, it does not 500
    and does not fabricate a dead /accounts link."""
    client, conn = app_and_conn
    from bookkit import db as db_mod
    from bookkit.repo import contacts, orgs

    org = orgs.list_orgs(conn, kind="client")[1]
    contact = contacts.for_org(conn, org.id)[0]
    with db_mod.transaction(conn):
        conn.execute(
            "UPDATE org SET deleted_at = '2026-08-19T00:00:00Z' WHERE id = ?",
            (org.id,),
        )
    response = client.get("/search", params={"q": contact.email})
    assert response.status_code == 200
    assert contact.first_name in response.text
    assert f'href="/accounts/{org.ref}/relationship"' not in response.text


def test_the_topbar_search_form_is_on_other_pages(app_and_conn):
    """The old inert Search pill (D4 casualty) is back as a real GET form —
    on EVERY page that renders the shared topbar, not just /search."""
    client, _ = app_and_conn
    response = client.get("/book")
    assert response.status_code == 200
    assert 'action="/search"' in response.text
    assert 'name="q"' in response.text


def test_search_does_not_pretend_to_be_the_book_section(app_and_conn):
    """The topbar's section defaults to "book"; /search must not inherit
    that and highlight a nav item the user is not on."""
    client, _ = app_and_conn
    html = client.get("/search", params={"q": "Atomic"}).text
    assert 'is-current-section">Book<' not in html
