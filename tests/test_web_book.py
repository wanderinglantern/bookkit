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
    with TestClient(app) as client:
        yield client, app.state.conn


def test_the_root_path_lands_somewhere_useful(app_and_conn):
    """GET / was a 404. You could not reach the app without knowing a ref."""
    client, _ = app_and_conn
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/book"
    landed = client.get("/")
    assert landed.status_code == 200
    assert "The book" in landed.text


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
    with TestClient(app) as client:
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


def test_new_account_and_export_workbook_are_marked_pending(app_and_conn):
    client, _ = app_and_conn
    html = client.get("/book").text
    for label in ("New account", "Export workbook"):
        tag = re.search(rf'<span class="btn-pill[^"]*"[^>]*>{re.escape(label)}</span>', html)
        assert tag, f"{label!r} pill not found"
        assert 'aria-disabled="true"' in tag.group(0)
        assert "title=" in tag.group(0)
        assert "href=" not in tag.group(0)
        assert "hx-" not in tag.group(0)


def test_filter_field_is_marked_pending_not_a_dead_input(app_and_conn):
    """A text <input> that accepts typing and silently does nothing is worse
    than a static pill that says so — the filter isn't wired this task, so
    it must not render as a live-looking input."""
    client, _ = app_and_conn
    html = client.get("/book").text
    assert "<input" not in html
    tag = re.search(r'<span class="book-filter-pill"[^>]*>', html)
    assert tag, "filter pill not found"
    assert 'aria-disabled="true"' in tag.group(0)
    assert "title=" in tag.group(0)


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
