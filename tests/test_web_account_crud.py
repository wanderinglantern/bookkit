"""Account create (/book) and org edit (account header) — gap 5.

Two rules are load-bearing here, and both are asserted the hard way:

1. THE CREATE DOOR IS DUPLICATE-GUARDED, THROUGH THE SERVICE. mcpparity
   records client_create as "rapidfuzz WRatio at 87 refuses a near-name and
   names the match"; that rule now lives ONCE in services.orgs.find_duplicate
   and the web POST must consult it rather than carry a local copy — so one
   test monkeypatches the service and proves the route's refusal moves with
   it (a green suite proves nothing broke, not that the new path is taken).

2. WEB WRITES ARE BATCHED WRITES. The assertion is never just "the field
   changed" — it is that the batch exists, carries source='web' and the
   TUI's own tool slug, has events to revert, and that reverting it puts the
   record back (the test_web_writes.py discipline).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import db
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs

    org = orgs.list_orgs(app.state.conn, kind="client")[0]
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, since="", limit=1)
    return found[0] if found else None


# --- create -------------------------------------------------------------------


def test_create_lands_in_the_book_and_redirects_to_the_account(app_and_org):
    client, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import orgs as orgs_repo

    response = client.post(
        "/book/accounts",
        data={"name": "Meridian Cold Storage", "kind": "client",
              "status": "prospect", "owner": "Grant", "industry": "Logistics"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    created = orgs_repo.find_by_name(conn, "Meridian Cold Storage")
    assert created is not None
    assert response.headers["location"] == f"/accounts/{created.ref}/relationship"

    landed = client.get(response.headers["location"])
    assert landed.status_code == 200
    assert "Meridian Cold Storage" in landed.text

    assert "Meridian Cold Storage" in client.get("/book").text

    batch = _latest_batch(conn)
    assert batch is not None, "the create wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"
    assert batch.tool == "new_account", "not the TUI's own tool slug for this form"
    assert batches_repo.events_for(conn, batch.id), "the batch carries no events to revert"


def test_create_over_htmx_answers_hx_redirect(app_and_org):
    """The form posts via hx-post into its .form-host; success must be a
    NAVIGATION (HX-Redirect), because the new account is a different page —
    a 200 fragment would render the account inside the book's form host."""
    client, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import orgs as orgs_repo

    response = client.post(
        "/book/accounts",
        data={"name": "Ballast Point Marine", "kind": "client", "status": "prospect"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 204
    created = orgs_repo.find_by_name(conn, "Ballast Point Marine")
    assert created is not None
    assert response.headers["HX-Redirect"] == f"/accounts/{created.ref}/relationship"


def test_near_duplicate_name_is_refused_naming_the_match_input_intact(app_and_org):
    """'Henderson Grp' vs 'Henderson Group' is the canonical near-miss the
    cutoff was measured on (mcpserver's old _DUP_CUTOFF comment). The refusal
    is HTTP 200 with the message IN the re-rendered form — htmx swaps neither
    4xx nor 5xx — and it names the existing account, ref included."""
    client, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import orgs as orgs_repo

    existing = orgs_repo.create(conn, name="Henderson Group", kind="client")
    before = len(orgs_repo.list_orgs(conn, kind="client"))

    response = client.post(
        "/book/accounts",
        data={"name": "Henderson Grp", "kind": "client",
              "status": "prospect", "owner": "Kate"},
    )
    assert response.status_code == 200
    assert "Henderson Group" in response.text, "the refusal does not name the match"
    assert existing.ref in response.text, "the refusal does not name the match's ref"

    # commit-in-place: the typed input comes back exactly, nothing retyped
    assert 'value="Henderson Grp"' in response.text
    assert 'value="Kate"' in response.text

    assert orgs_repo.find_by_name(conn, "Henderson Grp") is None
    assert len(orgs_repo.list_orgs(conn, kind="client")) == before
    assert _latest_batch(conn) is None or _latest_batch(conn).tool != "new_account", (
        "a refused create still opened a batch"
    )


def test_a_missing_name_is_refused_with_input_intact(app_and_org):
    client, _ = app_and_org
    response = client.post(
        "/book/accounts",
        data={"name": "", "kind": "client", "status": "prospect", "owner": "Kate"},
    )
    assert response.status_code == 200
    assert "name is required" in response.text
    assert 'value="Kate"' in response.text


def test_the_create_route_consults_the_service_guard(app_and_org, monkeypatch):
    """THE SEAM TEST. A perfectly distinct name is refused the moment the
    SERVICE says it is a duplicate — so the route provably asks
    services.orgs.find_duplicate rather than running fuzzy matching of its
    own (which is how two copies drift apart)."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import orgs as orgs_repo
    from bookkit.services import orgs as orgs_svc

    asked = []

    def fake(conn_, name):
        asked.append(name)
        return org

    monkeypatch.setattr(orgs_svc, "find_duplicate", fake)
    response = client.post(
        "/book/accounts",
        data={"name": "Completely Distinct Holdings", "kind": "client",
              "status": "prospect"},
    )
    assert asked == ["Completely Distinct Holdings"], "the route never asked the service"
    assert response.status_code == 200
    assert org.name in response.text
    assert orgs_repo.find_by_name(conn, "Completely Distinct Holdings") is None


def test_every_create_door_shares_the_one_guard():
    """The seam, asserted at the source: the guard's WRatio cutoff lives in
    services/orgs.py and NOWHERE else, and each create door (both TUI forms,
    MCP, the web route) names find_duplicate. Before the extraction the
    navigator and mcpserver each carried an inline copy while the book
    screen's form had neither — the repo/team.py story."""
    import inspect

    import bookkit.mcpserver as mcpserver
    import bookkit.services.orgs as orgs_svc
    import bookkit.tui.screens.book as tui_book
    import bookkit.tui.screens.navigator as tui_navigator
    import bookkit.web.routes.orgs as web_orgs

    for module in (mcpserver, tui_book, tui_navigator, web_orgs):
        source = inspect.getsource(module)
        assert "find_duplicate" in source, f"{module.__name__} skips the service guard"
        assert "score_cutoff=87" not in source, (
            f"{module.__name__} carries its own copy of the cutoff"
        )
    assert "DUPLICATE_CUTOFF = 87" in inspect.getsource(orgs_svc)


def test_find_duplicate_matches_near_names_and_passes_distinct_ones(conn):
    from bookkit.repo import orgs as orgs_repo
    from bookkit.services import orgs as orgs_svc

    henderson = orgs_repo.create(conn, name="Henderson Group", kind="client")
    orgs_repo.create(conn, name="Atlas Foundry", kind="client")
    # a market with a near name is NOT a candidate — the guard's list is the book
    orgs_repo.create(conn, name="Meridian Specialty", kind="market")

    match = orgs_svc.find_duplicate(conn, "Henderson Grp")
    assert match is not None and match.id == henderson.id
    assert orgs_svc.find_duplicate(conn, "Blue Harbor Logistics") is None
    assert orgs_svc.find_duplicate(conn, "Meridian Specialty Co") is None


def test_the_create_form_wires_vocabulary_suggestions(app_and_org):
    """org_form(conn=...) completes owner/industry from existing records
    (repo/vocab) — the web's datalist half of the CLAUDE.md suggestions rule.
    Seeded owners include 'grant', so the option must be there."""
    client, _ = app_and_org
    html = client.get("/book/accounts/new").text
    assert '<datalist id="l-owner">' in html
    assert '<option value="grant">' in html
    assert '<datalist id="l-industry">' in html


# --- edit ---------------------------------------------------------------------


def test_the_edit_form_is_prefilled(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/edit")
    assert response.status_code == 200
    assert f'value="{org.name}"' in response.text
    assert f'action="/accounts/{org.ref}/edit"' in response.text
    assert "edit account" in response.text


def test_the_edit_control_is_on_the_header_and_wired(app_and_org):
    client, org = app_and_org
    html = client.get(f"/accounts/{org.ref}/relationship").text
    tag = re.search(r"<button[^>]*>\s*Edit\s*</button>", html)
    assert tag, "no Edit control on the account header"
    assert f'hx-get="/accounts/{org.ref}/edit"' in tag.group(0)
    assert "aria-disabled" not in tag.group(0)
    assert 'id="org-edit-host"' in html, "the Edit control has nowhere to render"


def test_edit_renames_with_the_events_batched_and_revertible(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import orgs as orgs_repo
    from bookkit.services import batches as batches_svc

    response = client.post(
        f"/accounts/{org.ref}/edit",
        data={"name": "Renamed Holdings", "kind": org.kind, "status": org.status},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 204
    assert response.headers.get("HX-Refresh") == "true"
    assert orgs_repo.get(conn, org.id).name == "Renamed Holdings"

    batch = _latest_batch(conn)
    assert batch is not None, "the edit wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"
    assert batch.tool == "edit_account", "not the TUI's own tool slug for this form"
    assert batches_repo.events_for(conn, batch.id), "the batch carries no events to revert"

    batches_svc.revert(conn, batch.ref, now=db.utc_now())
    assert orgs_repo.get(conn, org.id).name == org.name, "one undo unit did not revert"


def test_edit_refusal_keeps_typed_input_and_writes_nothing(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import orgs as orgs_repo

    response = client.post(
        f"/accounts/{org.ref}/edit",
        data={"name": "", "kind": org.kind, "status": org.status,
              "owner": "Somebody New"},
    )
    assert response.status_code == 200
    assert "name is required" in response.text
    assert 'value="Somebody New"' in response.text, "typed input was thrown away"
    assert orgs_repo.get(conn, org.id).name == org.name


def test_renaming_onto_an_existing_exact_name_is_refused_in_place(app_and_org):
    """repo/orgs.guard_name — the rename guard every surface inherits — comes
    back as the form's error line, not a 500 and not a silent non-swap."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import orgs as orgs_repo

    other = orgs_repo.list_orgs(conn, kind="client")[1]
    response = client.post(
        f"/accounts/{org.ref}/edit",
        data={"name": other.name, "kind": org.kind, "status": org.status},
    )
    assert response.status_code == 200
    assert "already holds that name" in response.text
    assert orgs_repo.get(conn, org.id).name == org.name
