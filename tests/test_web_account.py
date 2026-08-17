"""The account page. The renewal-date assertion is named after the bug: Today,
Book, the account header and the calendar all printed placement.period_to
beside a countdown computed from renewal_on, so a date twenty days in the
future rendered red as '70d over'. The header badge and the right rail's
snapshot row are what carry that invariant now that the renewal rail is
gone (Grant, 2026-08-17)."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
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
    This is the smoke-test version against the real seeded fixture; the
    parametrized test below (`test_header_count_and_date_come_from_one_
    renewal_item`) is the one with teeth on the badge and on the exact day
    count — the seed has no account that is both divergent AND overdue, so
    that test uses a synthetic RenewalItem instead of fighting the fixture."""
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    assert item is not None, "fixture guarantees a live renewal"
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert item.renewal_on in response.text
    assert item.placement.period_to not in response.text or \
        item.placement.period_to == item.renewal_on


def test_overdue_badge_absent_on_the_seeded_non_overdue_fixture(app_and_org):
    """The seeded fixture (ACC-0004) is never overdue (days_remaining=38) —
    this only pins the negative case against real data. The positive case
    (badge present, exact count, boundary at 0) is covered by the
    synthetic-RenewalItem test below, because the seed cannot produce an
    account that is both overdue AND has renewal_on != period_to."""
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    assert item is not None
    assert item.days_remaining >= 0, "fixture is expected to be non-overdue"
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "badge-overdue" not in response.text


@pytest.mark.parametrize(
    "days_remaining,renewal_on",
    [(-345, "2025-09-03"), (38, "2026-09-24"), (0, "2026-08-14")],
    ids=["overdue", "upcoming", "boundary-zero"],
)
def test_header_count_and_date_come_from_one_renewal_item(
    app_and_org, monkeypatch, days_remaining, renewal_on
):
    """The four-surface bug was printing period_to beside a count computed
    from renewal_on. Both the badge and the snapshot row must carry THIS
    item's date and THIS item's count — including the number, not just its
    sign. The seed can't give an account that is both overdue and
    date-divergent (only ACC-0004 diverges, and it's never overdue), so this
    builds a synthetic RenewalItem with `renewal_on`/`days_remaining`
    deliberately inconsistent with the real placement's `period_to`,
    monkeypatches `next_for_org` to return it, and checks the rendered page."""
    client, org = app_and_org
    from bookkit.services import renewals as renewals_service

    conn = client.app.state.conn
    real_item = renewals_service.next_for_org(conn, org.id)
    assert real_item is not None
    fake_item = replace(real_item, renewal_on=renewal_on, days_remaining=days_remaining)
    assert fake_item.placement.period_to != renewal_on, (
        "fixture's period_to must differ from the synthetic renewal_on or "
        "this test can't distinguish the two"
    )

    monkeypatch.setattr(
        "bookkit.web.routes.account.renewals.next_for_org",
        lambda conn, org_id: fake_item,
    )

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    assert fake_item.placement.period_to not in response.text

    count = abs(days_remaining)
    if days_remaining < 0:
        assert f"◆ renewal {count}d overdue" in response.text
        assert "badge-overdue" in response.text
        suffix = f"{count}d over"
    else:
        assert "badge-overdue" not in response.text
        assert "◆ renewal" not in response.text
        suffix = f"{count}d"
    # the snapshot row: exact date AND exact count, from the same item
    assert f"{renewal_on} · {suffix}" in response.text


def test_no_renewal_rail_markup_remains(app_and_org):
    """The renewal rail is gone (Grant, 2026-08-17) — replaced by the header
    badge and the snapshot's 'next renewal' row."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    for stale_class in ("rail-track", "rail-marker", "rail-scale", "rail-overrun"):
        assert stale_class not in response.text


def test_change_time_shows_time_for_today_and_date_otherwise():
    """A change from three days ago must not read as if it just happened."""
    from bookkit.web.routes.account import _change_time

    today = date(2026, 8, 14)
    assert _change_time("2026-08-14T09:12:00+00:00", today) == "09:12"
    assert _change_time("2026-08-11T16:30:00+00:00", today) == "2026-08-11"


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
    """The design source (Account View.dc.html) wins over the visual-direction
    spec's paraphrase where they disagree — its copy is 'No documents yet'
    (capital N), not the spec doc's lowercase rendering."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "No documents yet" in response.text
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


def test_team_and_recent_changes_empty_states_use_canonical_copy(app_and_org):
    """TEAM is addable (the Assign link adds to it) — the spec's addable-list
    phrasing. RECENT CHANGES having nothing yet isn't a problem — the spec's
    attention-list phrasing, not invented copy."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "empty — add the first row" in response.text  # TEAM (no assignments seeded)
    assert "nothing here — that's good" in response.text  # RECENT CHANGES


def test_undo_pill_absent_when_nothing_to_undo(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "undo-pill" not in response.text
    assert "nothing here — that's good" in response.text


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
    # span with no click target: no href, no hx-post/hx-get on the
    # revert-link ITSELF — and it carries aria-disabled so a hover never
    # promises a click that does nothing. This used to assert no hx-get/
    # hx-post appeared anywhere on the page at all, back when nothing was
    # wired yet; Task 8 wires the contacts panel's cell editing and "+ Add
    # contact" button, so the check narrows to the one tag it was always
    # actually about.
    revert_tag = re.search(r'<span class="revert-link"[^>]*>', response.text)
    assert revert_tag, "no revert-link span found"
    assert 'aria-disabled="true"' in revert_tag.group(0)
    assert "hx-post" not in revert_tag.group(0)
    assert "hx-get" not in revert_tag.group(0)


# Every control that isn't wired to anything yet must say so: no href/hx-*
# attribute WITHOUT aria-disabled="true" (looks live, is dead — the bug),
# and no href/hx-* attribute WITH aria-disabled="true" either (a control
# that's actually wired must drop the disabled marker — this is what forces
# the next task that wires one to remove it, or this test breaks).
_INERT_CONTROL_MARKERS = frozenset(
    {"btn-pill", "undo-pill", "revert-link", "rail-action", "row-action"}
)


def _assert_inert_controls_are_consistently_marked(html: str) -> int:
    tags = re.findall(r"<[a-zA-Z][^>]*>", html)
    matched = []
    for tag in tags:
        class_match = re.search(r'class="([^"]*)"', tag)
        if class_match and set(class_match.group(1).split()) & _INERT_CONTROL_MARKERS:
            matched.append(tag)
    for tag in matched:
        has_action = "href=" in tag or "hx-" in tag
        has_disabled = 'aria-disabled="true"' in tag
        assert has_action != has_disabled, (
            f"control is neither clearly pending nor clearly wired: {tag}"
        )
    return len(matched)


def test_inert_controls_carry_aria_disabled_xor_a_real_action(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    matched = _assert_inert_controls_are_consistently_marked(response.text)
    assert matched >= 5, "expected at least the four header pills plus the Assign link"


def test_inert_controls_stay_marked_with_a_recent_change_present(app_and_org):
    """Same check with the undo-pill and revert-link actually rendered
    (they're conditional on there being a batch to show)."""
    client, org = app_and_org
    from bookkit.services import batches

    conn = client.app.state.conn
    with batches.open_batch(
        conn, source="tui", tool="edit_field", summary="premium PLC-0001 → $4.13M",
        org_id=org.id,
    ):
        pass

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "undo-pill" in response.text and "revert-link" in response.text
    _assert_inert_controls_are_consistently_marked(response.text)
