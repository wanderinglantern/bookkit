"""The account page. The renewal-date assertion is named after the bug: Today,
Book, the account header and the calendar all printed placement.period_to
beside a countdown computed from renewal_on, so a date twenty days in the
future rendered red as '70d over'. The header badge and the right rail's
snapshot row are what carry that invariant now that the renewal rail is
gone (Grant, 2026-08-17)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


def _tab_badge(html: str, label: str) -> str:
    match = re.search(rf"{label}\s*<span class=\"tab-badge\">(\d+)</span>", html)
    assert match, f"tab {label!r} badge not found in response"
    return match.group(1)


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


def test_account_root_redirects_to_relationship(app_and_org):
    """Relationship is the default tab (Grant, 2026-08-17) — Program, Work
    and Pipeline all need writes this task doesn't build yet."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"].endswith("/relationship")


def test_relationship_names_the_account(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    assert org.name in response.text


def test_unknown_account_is_404(app_and_org):
    client, _ = app_and_org
    assert client.get("/accounts/nope-does-not-exist/relationship").status_code == 404


def test_unknown_tab_is_404(app_and_org):
    client, org = app_and_org
    assert client.get(f"/accounts/{org.ref}/bogus-tab").status_code == 404


def test_header_prints_the_date_it_counts_to(app_and_org):
    """THE RENEWAL DATE IS RenewalItem.renewal_on, never placement.period_to.
    Print the same date you count to, or a future date renders as overdue.
    Both the header's overdue badge and the right rail's snapshot row must
    come from the one RenewalItem the account's tabs share."""
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    assert item is not None, "fixture guarantees a live renewal"
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert item.renewal_on in response.text
    assert item.placement.period_to not in response.text or \
        item.placement.period_to == item.renewal_on


def test_overdue_badge_matches_days_remaining(app_and_org):
    """Overdue is decided by days_remaining < 0, never by layout — and the
    header badge is the only place '◆ renewal ... overdue' can appear."""
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    assert item is not None
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert ("badge-overdue" in response.text) == (item.days_remaining < 0)
    if item.days_remaining < 0:
        assert f"◆ renewal {-item.days_remaining}d overdue" in response.text


def test_no_renewal_rail_markup_remains(app_and_org):
    """The renewal rail is gone (Grant, 2026-08-17) — replaced by the header
    badge and the snapshot's 'next renewal' row."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    for stale_class in ("rail-track", "rail-marker", "rail-scale", "rail-overrun"):
        assert stale_class not in response.text


def test_four_tabs_render_with_real_counts(app_and_org):
    client, org = app_and_org
    from bookkit.repo import contacts, interactions, placements

    conn = client.app.state.conn
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200

    expected_program = len(placements.for_org(conn, org.id))
    expected_relationship = (
        len(contacts.for_org(conn, org.id)) + len(interactions.for_org(conn, org.id, limit=200))
    )
    assert _tab_badge(response.text, "Program") == str(expected_program)
    assert _tab_badge(response.text, "Relationship") == str(expected_relationship)
    # Work and Pipeline pull in project needs / RFI requests / submissions —
    # just confirm they're real (non-negative integers), not that a route
    # exists that never wired counts at all.
    assert _tab_badge(response.text, "Work").isdigit()
    assert _tab_badge(response.text, "Pipeline").isdigit()


@pytest.mark.parametrize(
    "tab,heading_text",
    [
        ("program", "empty — add the first row"),
        ("relationship", "empty — add the first row"),
        ("work", "no open tasks — add one"),
        ("pipeline", "empty — add the first row"),
    ],
)
def test_each_tab_renders_its_empty_state_and_marks_itself_current(app_and_org, tab, heading_text):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/{tab}")
    assert response.status_code == 200
    assert heading_text in response.text
    assert f'href="/accounts/{org.ref}/{tab}" class="tab is-current"' in response.text


def test_right_rail_sections_present(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    for heading in ("Snapshot", "Team", "Documents", "Recent changes"):
        assert heading in response.text, f"missing right-rail section: {heading}"


def test_documents_empty_state_copy(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "no documents yet" in response.text
    assert "Drop a binder, loss run or SOV here — BookKit records the path, not the file." \
        in response.text


def test_snapshot_omits_rows_it_has_no_real_read_for(app_and_org):
    """program premium, top of tower and unplaced have no towerkit-tower read
    behind them this task — they must not appear at all."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "bound premium" in response.text
    assert "open work" in response.text
    for invented in ("program premium", "top of tower", "unplaced"):
        assert invented not in response.text


def test_undo_pill_absent_when_nothing_to_undo(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "undo-pill" not in response.text
    assert "no changes yet" in response.text


def test_undo_pill_and_recent_change_appear_after_a_batch_and_revert_is_inert(app_and_org):
    client, org = app_and_org
    from bookkit.services import batches

    conn = client.app.state.conn
    with batches.open_batch(
        conn, source="tui", tool="edit_field", summary="premium PLC-0001 → $4.13M",
        org_id=org.id,
    ):
        pass

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    assert "undo-pill" in response.text
    assert "premium PLC-0001 → $4.13M" in response.text
    # Rendered, not wired — Task 15's work. The revert affordance is a bare
    # span with no click target: no href, no hx-post/hx-get anywhere on the
    # page (the tab links are the only href/hx-bearing elements this task
    # renders, and neither uses hx-post/hx-get).
    assert '<span class="revert-link">Revert</span>' in response.text
    assert "hx-post" not in response.text
    assert "hx-get" not in response.text
