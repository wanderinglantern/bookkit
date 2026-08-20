"""GET / and GET /book — the way in. Verified 2026-08-17: GET / returned 404
and so did /accounts; every account tab rendered but nothing linked to them,
so the only way to reach the app was already knowing a ref and typing
/accounts/ACC-0001/relationship by hand. This is the front door.

The money column is the one load-bearing decision here: CLAUDE.md records
that this exact column, wired to orgs.clients_with_recency's `premium`
(the single latest-period BOUND placement), once "showed revenue that did
not exist" — an account with $15.6M across two bound placements read $8M.
The fix is services.book.bound_premium_for_org, the summed figure four
other call sites already use. test_the_premium_column_is_the_account_total_
not_one_placement seeds TWO bound placements with different premiums so the
sum and either single figure differ — with one placement the assertion
would pass no matter which the column reads, and protect nothing."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import money
from bookkit.web.app import create_app


@pytest.fixture
def app_and_conn(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app.state.conn


def test_the_root_path_lands_somewhere_useful(app_and_conn):
    """GET / was a 404. You could not reach the app without knowing a ref."""
    client, _ = app_and_conn
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    # /today since the audit's gap #1 landed: the day's work is the front
    # door, matching the TUI's own default screen
    assert response.headers["location"] == "/today"
    landed = client.get("/")
    assert landed.status_code == 200
    assert "Today" in landed.text


def test_the_book_lists_every_client_regardless_of_status(app_and_conn):
    """Fix round 1, 2026-08-18: this used to filter to status='active',
    which silently hid every prospect (and any dormant account) — the
    screen already has a status column and a filter field, so narrowing by
    status is the user's job, not the query's. Matches
    tui/screens/book.py's own orgs.list_orgs(kind="client") call, which
    takes no status= argument."""
    client, conn = app_and_conn
    from bookkit.repo import orgs
    from bookkit.services import renewals

    response = client.get("/book")
    assert response.status_code == 200

    all_clients = orgs.list_orgs(conn, kind="client")
    active = [o for o in all_clients if o.status == "active"]
    assert active, "fixture must seed at least one active client"
    org = active[0]
    assert org.name in response.text
    assert org.ref in response.text

    item = renewals.next_for_org(conn, org.id)
    if item is not None:
        # THE RENEWAL DATE IS RenewalItem.renewal_on, never
        # placement.period_to — print the date the countdown was counted to.
        assert item.renewal_on in response.text

    # A prospect seeded by seed.py MUST appear — picked by ref so this
    # fails again if the status filter comes back.
    prospects = [o for o in all_clients if o.status == "prospect"]
    assert prospects, "fixture must seed at least one prospect client"
    prospect = prospects[0]
    assert prospect.ref in response.text
    assert prospect.name in response.text


def test_the_premium_column_is_the_account_total_not_one_placement(db_path: Path):
    from bookkit import db as db_mod
    from bookkit.repo import orgs, placements

    conn = db_mod.connect(db_path)
    org = orgs.create(conn, kind="client", name="Two Placement Company", status="active")
    placements.create(
        conn, org.id, "2026 Casualty Program", "2026-01-01", "2027-01-01",
        status="bound", total_premium=500_000_00,
    )
    placements.create(
        conn, org.id, "2026 Property Program", "2026-06-01", "2027-06-01",
        status="bound", total_premium=1_200_000_00,
    )
    conn.close()

    app = create_app(db_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/book")

    assert response.status_code == 200
    assert money.format_cents_compact(1_700_000_00) in response.text
    # Neither single placement's own figure may appear as this account's
    # premium — clients_with_recency.premium would print one of these.
    assert money.format_cents_compact(500_000_00) not in response.text
    assert money.format_cents_compact(1_200_000_00) not in response.text


def test_a_row_links_to_that_account(app_and_conn):
    client, conn = app_and_conn
    from bookkit.repo import orgs

    org = orgs.list_orgs(conn, kind="client", status="active")[0]
    response = client.get("/book")
    assert f'href="/accounts/{org.ref}"' in response.text

    followed = client.get(f"/accounts/{org.ref}", follow_redirects=False)
    assert followed.status_code in (302, 307)
    assert followed.headers["location"].endswith("/relationship")


def test_unbuilt_book_controls_are_not_rendered(app_and_conn):
    """D4: New account, Export workbook and the filter were drawn as pending
    pills; unbuilt means unrendered now — the parity ledger is the roadmap,
    not the UI. A dead filter <input> stays banned for the same reason."""
    client, _ = app_and_conn
    html = client.get("/book").text
    assert "New account" not in html
    assert "Export workbook" not in html
    assert "book-filter-pill" not in html
    assert "<input" not in html
    assert 'aria-disabled' not in html


def test_book_nav_item_is_a_real_link(app_and_conn):
    client, _ = app_and_conn
    html = client.get("/book").text
    assert '<a href="/book" class="topbar-nav-item is-current-section">Book</a>' in html
    assert 'aria-disabled' not in re.search(r"<a[^>]*>Book</a>", html).group(0)


def test_book_nav_item_is_also_a_real_link_from_the_account_page(app_and_conn):
    client, conn = app_and_conn
    from bookkit.repo import orgs

    org = orgs.list_orgs(conn, kind="client", status="active")[0]
    html = client.get(f"/accounts/{org.ref}/relationship").text
    assert '<a href="/book" class="topbar-nav-item is-current-section">Book</a>' in html
