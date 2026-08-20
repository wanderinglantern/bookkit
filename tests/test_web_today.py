"""Today — the web's front door (audit gap #1): the TUI's four panes and the
Navigator's attention leaves in one page, overdue first and never off the
list, tasks completable where the list surfaced them, and the cross-account
changes list with Revert."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def test_the_root_redirects_to_today(client):
    got = client.get("/", follow_redirects=False)
    assert got.status_code in (302, 307)
    assert got.headers["location"] == "/today"


def test_every_attention_section_renders_and_overdue_leads(client):
    page = client.get("/today").text

    order = [
        "Overdue renewals", "Renewals ≤ 120d", "Tasks due", "Project needs due",
        "Submissions past SLA", "Quotes expiring", "Onboarding incomplete",
        "Requests to chase", "Going stale", "Recent changes",
    ]
    positions = [page.index(section) for section in order]
    assert positions == sorted(positions), "the attention order drifted"


def test_overdue_renewals_actually_list_with_their_counted_date(client):
    """The seeded book has overdue renewals; each row prints renewal_on (the
    date the count is computed to — the four-surface bug's invariant) and a
    Nd-over figure, linking into the account's program tab."""
    from bookkit.services import renewals
    from bookkit.web.routes import today as today_route

    # the conftest freezes date.today() PER MODULE — expectations must use
    # the route's clock, or the day counts drift by however long ago the
    # snapshot seed's frozen day was
    conn = client.app.state.conn
    items = renewals.upcoming(conn, today_route.date.today(), days=120)
    overdue = [i for i in items if i.days_remaining < 0]
    assert overdue, "the seeded book shows nothing overdue — test proves nothing"

    page = client.get("/today").text

    for item in overdue:
        assert item.renewal_on in page
        assert f"{-item.days_remaining}d over" in page
        assert f"/accounts/{item.org.ref}/program" in page


def test_a_task_completes_from_the_list_that_surfaced_it(client):
    from bookkit.repo import tasks as tasks_repo
    from bookkit.web.routes import today as today_route

    conn = client.app.state.conn
    due = tasks_repo.open_tasks(conn, due_by=today_route.date.today().isoformat())
    assert due, "the seeded book has no due tasks — test proves nothing"
    task = due[0]

    page = client.get("/today").text
    assert f'hx-post="/today/tasks/{task.id}/done"' in page

    done = client.post(f"/today/tasks/{task.id}/done")

    assert done.status_code == 200
    assert task.title not in done.text, "the completed task still lists"
    fresh = tasks_repo.get(conn, task.id)
    assert fresh.status != "open"
    from bookkit.repo import batches as batches_repo

    batch = batches_repo.most_recent(conn)
    assert batch is not None and batch.tool == "task_done", "the done was unbatched"


def test_recent_changes_lists_cross_account_with_revert(client):
    """The Navigator's changes list, folded in: after a write on ANY account,
    Today shows it with a Revert that posts to the account's revert route."""
    from bookkit.repo import orgs, placements

    conn = client.app.state.conn
    org = next(
        o for o in orgs.list_orgs(conn, kind="client")
        if any(p.program_path for p in placements.for_org(conn, o.id))
    )
    placement = next(p for p in placements.for_org(conn, org.id) if p.program_path)
    renewed = client.post(f"/accounts/{org.ref}/program/{placement.id}/renew")
    assert renewed.status_code == 200

    page = client.get("/today").text

    assert f"renewed {placement.ref}" in page
    assert f"/accounts/{org.ref}/changes/" in page
