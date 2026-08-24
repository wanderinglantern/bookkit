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
    """The system pass (design 4C): ten equal lists became one morning —
    Needs you today first (overdue renewals never fall off it), then the
    renewal window, then the demoted context sections, each still present
    with its count visible while closed."""
    page = client.get("/today").text

    order = [
        "Needs you today", "Renewals coming", "Quotes expiring",
        "Project needs due", "Onboarding incomplete", "Going stale",
        "Changes, last 14 days",
    ]
    positions = [page.index(section) for section in order]
    assert positions == sorted(positions), "the morning's order drifted"
    assert "Also worth a look" in page
    # the demoted lists are disclosures with the count visible while closed
    assert page.count("<details") >= 5


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
    needs = page[page.index("Needs you today") : page.index("Renewals coming")]

    for item in overdue:
        # the date the countdown counts to, printed beside it — the standing
        # renewal-date rule, now in the merged morning list
        assert item.renewal_on in needs
        assert "Renewal ran out" in needs
        # the countdown, in the state vocabulary's own spelling
        assert f"overdue · {-item.days_remaining}d" in needs
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
    from datetime import date

    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    due = tasks_repo.open_tasks(conn, due_by=date.today().isoformat())
    linked = next((t for t in due if t.org_id), None)
    assert linked is not None, "the seeded book has no due task with an account"
    org = orgs_repo.get(conn, linked.org_id)

    page = client.get("/today").text
    section = page[page.index("Needs you today") : page.index("Renewals coming")]

    assert f"/accounts/{org.ref}/work" in section, (
        "the task's account is named but not linked"
    )
    assert linked.title in section


def _renewal_rows(page: str) -> list[list[str]]:
    """The Renews/Over-by/Account/Cover/Program cells of every renewal row."""
    import re

    body = page[page.index("Renewals coming") : page.index("Quotes expiring")]
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


def test_done_from_needs_you_swaps_the_section_and_keeps_the_morning(client):
    """A task leaves the merged list without a page reload — done answers
    the Needs-you SECTION, one element."""
    from datetime import date

    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    due = tasks_repo.open_tasks(conn, due_by=date.today().isoformat())
    task = next(t for t in due if t.org_id)

    done = client.post(f"/today/tasks/{task.id}/done")

    assert done.status_code == 200
    assert done.text.lstrip().startswith("<section")
    assert 'id="today-needs"' in done.text
    assert task.title not in done.text, "the completed task is still listed"


def test_the_exports_drawer_gathers_the_existing_download_routes(client):
    """The scattered download anchors, in one place (design 4C) — every link
    is a route that already exists; the drawer mints nothing."""
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo

    conn = client.app.state.conn
    page = client.get("/exports")

    assert page.status_code == 200
    linked = [
        p for o in orgs_repo.list_orgs(conn, kind="client")
        for p in placements_repo.for_org(conn, o.id) if p.program_path
    ]
    assert linked, "the seeded book has no linked program — test proves nothing"
    for placement in linked:
        assert f"/program/{placement.id}/export/tower.svg" in page.text
    assert "/export/open-items.xlsx" in page.text


def test_past_sla_reaches_needs_you_with_the_deadline_it_counts_to(client):
    """The review found the SLA loop had zero web coverage (S11) and that the
    first cut printed sent_on under a Due header (S1): the row must carry
    the DEADLINE (sent + SLA), the overdue tone, and the underwriter to
    chase when one is named."""
    from datetime import timedelta

    from conftest import FROZEN_TODAY

    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo
    from bookkit.repo import submissions as submissions_repo
    from bookkit.services.sla import DEFAULT_SLA_DAYS

    conn = client.app.state.conn
    org = next(
        o for o in orgs_repo.list_orgs(conn, kind="client")
        if placements_repo.for_org(conn, o.id)
    )
    market = orgs_repo.list_orgs(conn, kind="market")[0]
    placement = placements_repo.for_org(conn, org.id)[0]
    # the suite runs under the frozen clock — the route's today() is
    # 2026-08-14, and a test on the real date builds a submission the
    # frozen cutoff cannot see
    sent = FROZEN_TODAY - timedelta(days=DEFAULT_SLA_DAYS + 5)
    submissions_repo.create(
        conn, market.id, sent.isoformat(), placement_id=placement.id
    )
    deadline = (sent + timedelta(days=DEFAULT_SLA_DAYS)).isoformat()
    expected_over = 5

    page = client.get("/today").text
    needs = page[page.index("Needs you today") : page.index("Renewals coming")]

    assert deadline in needs, "the row does not print the deadline it counts to"
    assert f"past SLA · {expected_over}d" in needs
    assert "state-overdue" in needs
