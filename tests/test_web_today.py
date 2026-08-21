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


def test_every_section_names_the_account_rather_than_its_ref(client):
    """One section of Today printed `ACC-0004` in the Account column while the
    other nine printed "Delta Marine Logistics" (Grant, 2026-08-20).

    The cause was DRY, not a typo: ten hand-written copies of the same anchor,
    each picking its label out of whatever its row happened to carry, and the
    tasks table's row carried no name — only the ref lookup. So it printed the
    ref. Every account link on this page now goes through one macro over one
    lookup that returns the ref and the name together.

    Asserted over the WHOLE page rather than the tasks table alone: the next
    section someone adds is the one that would otherwise repeat the mistake.
    """
    import re

    conn = client.app.state.conn
    from bookkit.repo import orgs as orgs_repo

    page = client.get("/today").text
    names = {o.ref: o.name for o in orgs_repo.list_orgs(conn, kind="client")}

    # every account anchor on the page, as (ref-in-the-href, printed-label)
    anchors = re.findall(
        r'<a href="/accounts/(ACC-\d+)/[a-z]+"[^>]*>([^<]+)</a>', page
    )
    assert anchors, "no account links on Today at all — the scan is broken"

    printed_a_ref = [
        (ref, label) for ref, label in anchors
        if label.strip() == ref and names.get(ref) != ref
    ]
    assert not printed_a_ref, (
        f"these Today links print the account's ref instead of its name: "
        f"{printed_a_ref}"
    )


def test_a_task_row_links_to_the_account_it_belongs_to(client):
    """The bug's own section, pinned by content: the tasks table must carry a
    real account name and a link that lands on that account's Work tab."""
    import re

    conn = client.app.state.conn
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import tasks as tasks_repo

    page = client.get("/today").text
    section = page[page.index("Tasks due") :]
    section = section[: section.index("</section>")]

    due = [t for t in tasks_repo.open_tasks(conn) if t.org_id]
    if not due:
        pytest.skip("the seeded book has no account-scoped open task")

    linked = re.findall(r'<a href="/accounts/(ACC-\d+)/work"[^>]*>([^<]+)</a>', section)
    assert linked, "the tasks table has no account links"
    for ref, label in linked:
        org = orgs_repo.find(conn, ref)
        assert org is not None
        assert label.strip() == org.name, f"{ref} rendered as {label!r}"


# --- renewals name the POLICY, not the program (Grant, 2026-08-21) ------------


def _renewal_rows(page: str) -> list[list[str]]:
    """The Renews/Over-by/Account/Cover/Program cells of every renewal row."""
    import re

    body = page[page.index("Overdue renewals") : page.index("Project needs due")]
    rows = re.findall(r"<tr>(.*?)</tr>", body, re.S)
    out = []
    for row in rows:
        cells = [
            re.sub(r"<[^>]+>", "", cell).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        if len(cells) >= 5:
            out.append(cells)
    return out


def test_cover_never_prints_a_program_name(client):
    """THE COMPLAINT. The column was `item.lines or placement.program_name`, so
    an unlinked placement — most of a real book — printed "2025 Casualty
    Program" under a header promising lines of cover. A name is not cover, and
    a column that swaps one for the other silently cannot be read."""
    from bookkit.repo import placements as placements_repo

    conn = client.app.state.conn
    page = client.get("/today").text
    names = {
        p.program_name
        for p in placements_repo.expiring_between(conn, "0001-01-01", "9999-12-31")
        if p.program_name
    }
    assert names, "fixture drifted — no programs to confuse a cover column with"

    for renews, _over, _account, cover, _program in _renewal_rows(page):
        assert cover not in names, (
            f"the row renewing {renews} prints the PROGRAM {cover!r} under Cover"
        )


def test_a_row_with_no_lines_says_so_rather_than_standing_something_in(client):
    """An unlinked placement genuinely has no lines to print. Saying that is a
    fact; substituting the program name is a claim the book cannot support."""
    page = client.get("/today").text
    covers = {row[3] for row in _renewal_rows(page)}

    assert "\u2014" in covers, (
        "no row admits it has no lines — the seeded book has unlinked placements"
    )
    # The dash is the house spelling of an absence; the WHY rides in the title,
    # because a column of shouting words about a state that is not wrong reads
    # as a column of warnings.
    assert "no program file is linked" in page


def test_the_program_is_its_own_column(client):
    """Demoted, not deleted: the row still needs the context, it just is not
    the answer to "what cover expires here"."""
    from bookkit.repo import placements as placements_repo

    conn = client.app.state.conn
    page = client.get("/today").text
    names = {
        p.program_name
        for p in placements_repo.expiring_between(conn, "0001-01-01", "9999-12-31")
        if p.program_name
    }
    printed = {row[4] for row in _renewal_rows(page)}

    assert printed & names, "the program name vanished from the table entirely"


def test_a_program_whose_lines_expire_apart_gets_a_row_each(client):
    """One row per DATE something runs out. Before this, an Inland Marine layer
    expiring months early made the whole program read as overdue — cover label
    and all — so "GL, AL, IM · 70d over" claimed three lines were late when one
    was."""
    from bookkit.services import renewals
    from bookkit.web.routes import today as today_route

    conn = client.app.state.conn
    items = renewals.upcoming(conn, today_route.date.today(), days=120)
    by_placement: dict[str, list] = {}
    for item in items:
        by_placement.setdefault(item.placement.id, []).append(item)
    split = [rows for rows in by_placement.values() if len(rows) > 1]
    assert split, "fixture drifted — no program whose lines expire on two dates"

    page = client.get("/today").text
    covers = {row[3] for row in _renewal_rows(page)}
    for rows in split:
        assert len({r.renewal_on for r in rows}) == len(rows)
        for row in rows:
            assert row.cover in covers, f"{row.cover!r} is not on the page"
            assert row.cover != row.lines or len(rows) == 1
