"""The account page. The renewal-date assertion is named after the bug: Today,
Book, the account header and the calendar all printed placement.period_to
beside a countdown computed from renewal_on, so a date twenty days in the
future rendered red as '70d over'."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs
    from bookkit.services import renewals

    with TestClient(app) as client:
        conn = app.state.conn
        clients = orgs.list_orgs(conn, kind="client")
        # The renewal test only has teeth if the picked account has a live
        # renewal where renewal_on != period_to — otherwise the assertion
        # passes no matter what the header prints and protects nothing.
        org = next(
            (
                o for o in clients
                if (item := renewals.next_for_org(conn, o.id)) is not None
                and item.renewal_on != item.placement.period_to
            ),
            None,
        )
        assert org is not None, (
            "no seeded client has a live renewal where renewal_on != "
            "period_to — the renewal-date test would be worthless"
        )
        yield client, org


def test_account_root_redirects_to_overview(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"].endswith("/overview")


def test_overview_names_the_account(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/overview")
    assert response.status_code == 200
    assert org.name in response.text


def test_unknown_account_is_404(app_and_org):
    client, _ = app_and_org
    assert client.get("/accounts/nope-does-not-exist/overview").status_code == 404


def test_header_prints_the_date_it_counts_to(app_and_org):
    """THE RENEWAL DATE IS RenewalItem.renewal_on, never placement.period_to.
    Print the same date you count to, or a future date renders as overdue."""
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    if item is None:
        pytest.skip("seeded account has no live renewal")
    response = client.get(f"/accounts/{org.ref}/overview")
    assert item.renewal_on in response.text
    assert item.placement.period_to not in response.text or \
        item.placement.period_to == item.renewal_on


def test_overdue_is_decided_by_days_remaining(app_and_org):
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    if item is None:
        pytest.skip("seeded account has no live renewal")
    response = client.get(f"/accounts/{org.ref}/overview")
    assert ("is-overdue" in response.text) == (item.days_remaining < 0)


def test_overview_shows_the_five_sections(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/overview")
    for heading in ("Team", "Key contacts", "Recent interactions",
                    "Open tasks", "Open opportunities"):
        assert heading in response.text, f"missing section: {heading}"
