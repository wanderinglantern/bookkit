"""Open items across the book — the working list.

The point of this page is that it OWNS NO WRITES. Every cell posts to the
account-scoped route that already exists, so editing from here and editing from
the account's Work tab are the same write, the same guard and the same undo
batch. Most of what is asserted here is that the reuse is real, because a
book-wide list that quietly forked its own edit path is exactly how two
surfaces come to disagree about what a task is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _cell_actions(page: str) -> list[str]:
    return re.findall(r'data-cell-action="([^"]+)"', page)


def test_the_page_lists_open_tasks_from_more_than_one_account(client):
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    page = client.get("/items").text
    open_tasks = tasks_repo.open_tasks(conn)
    assert len(open_tasks) > 1

    accounts = {a.split("/")[2] for a in _cell_actions(page) if a.startswith("/accounts/")}
    assert len(accounts) > 1, "a book-wide list showing one account's work is not book-wide"


def test_every_cell_posts_to_its_own_accounts_existing_route(client):
    """THE REUSE, ASSERTED. Not "a write happened" — that the URL each cell
    carries is the account-scoped one routes/work.py already serves, for the
    account that task actually belongs to.

    A row for ACC-0004 posting to ACC-0001's URL would be caught by `_owned`
    at request time, but only if somebody clicked it; this catches it here.
    """
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    page = client.get("/items").text
    by_task = {}
    for action in _cell_actions(page):
        m = re.match(r"/accounts/(ACC-\d+)/tasks/([^/]+)/cell/(\w+)$", action)
        assert m, f"a cell on the book-wide list has an unexpected URL: {action}"
        by_task.setdefault(m.group(2), set()).add(m.group(1))

    assert by_task, "no editable cells on the page"
    for task_id, refs in by_task.items():
        assert len(refs) == 1, f"task {task_id} renders cells for {refs}"
        task = tasks_repo.get(conn, task_id)
        org = orgs_repo.get(conn, task.org_id)
        assert refs == {org.ref}, f"{task_id} posts to {refs}, belongs to {org.ref}"


def test_a_cell_edited_from_here_is_saved_and_read_back(client):
    """End to end through the shared route, then re-read from THIS page."""
    page = client.get("/items").text
    action = next(a for a in _cell_actions(page) if a.endswith("/cell/category"))

    saved = client.post(action, data={"category": "Renewal prep"})

    assert saved.status_code == 200
    assert "Renewal prep" in saved.text
    assert "Renewal prep" in client.get("/items").text


def test_the_account_column_names_the_account(client):
    """The Today bug, one page over: this list is nothing but cross-account
    rows, so a ref in the Account column would be its most visible failure."""
    from bookkit.repo import orgs as orgs_repo

    conn = client.app.state.conn
    page = client.get("/items").text
    linked = re.findall(r'<a href="/accounts/(ACC-\d+)/work"[^>]*>([^<]+)</a>', page)
    assert linked, "no account links on the book-wide list"
    for ref, label in linked:
        org = orgs_repo.find(conn, ref)
        assert org is not None and label.strip() == org.name


def test_the_overdue_filter_is_a_url_and_narrows_the_list(client):
    everything = client.get("/items").text
    overdue = client.get("/items?overdue=1").text

    assert len(_cell_actions(overdue)) <= len(_cell_actions(everything))
    assert "Clear" in overdue, "a filtered view offers no way back"


def test_the_account_filter_narrows_to_that_account(client):
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    task = next(t for t in tasks_repo.open_tasks(conn) if t.org_id)
    from bookkit.repo import orgs as orgs_repo

    ref = orgs_repo.get(conn, task.org_id).ref

    page = client.get(f"/items?account={ref}").text

    refs = {a.split("/")[2] for a in _cell_actions(page) if a.startswith("/accounts/")}
    assert refs == {ref}, f"filtering to {ref} still shows {refs}"


def test_the_add_form_makes_the_account_a_picker_with_a_blank_first_option(client):
    """A book-wide capture has to ask which account, and a select with no blank
    option lets the browser answer that question with whichever account sorts
    first — silently, on a field nobody looked at."""
    form = client.get("/items/tasks/new").text

    assert 'name="org_id"' in form, "the book-wide add form does not ask for an account"
    select = form[form.index('name="org_id"') :]
    select = select[: select.index("</select>")]
    options = re.findall(r'<option value="([^"]*)"', select)
    assert options, "the account select has no options"
    assert options[0] == "", (
        f"the first option is {options[0]!r}, so the browser pre-selects an "
        "account nobody chose"
    )
    assert len(options) > 1, "the picker offers no accounts"


def test_a_task_is_captured_from_the_book_wide_list(client):
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    org = orgs_repo.list_orgs(conn, kind="client")[0]
    before = len(tasks_repo.open_tasks(conn))

    made = client.post(
        "/items/tasks/new",
        data={"org_id": org.id, "title": "Chase the signed application",
              "due_on": "2026-12-01", "priority": "2"},
    )

    assert made.status_code == 200
    assert len(tasks_repo.open_tasks(conn)) == before + 1
    assert "Chase the signed application" in made.text


def test_a_refused_capture_keeps_what_was_typed(client):
    """Commit-in-place, the house default: a title is required, and losing the
    rest of the form to a missing one is the failure the rule exists for."""
    from bookkit.repo import orgs as orgs_repo

    conn = client.app.state.conn
    org = orgs_repo.list_orgs(conn, kind="client")[0]

    refused = client.post(
        "/items/tasks/new",
        data={"org_id": org.id, "title": "", "due_on": "2026-12-01"},
    )

    assert refused.status_code == 200
    assert "2026-12-01" in refused.text, "the typed date was thrown away"


def test_completing_a_task_from_here_uses_the_same_write_as_the_account_tab(client):
    """Only the re-render differs between the two surfaces. The batch, its tool
    name and its sentence are shared, or the changes list would describe the
    same action two ways depending on where it was done."""
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    task = next(t for t in tasks_repo.open_tasks(conn) if t.org_id)

    done = client.post(f"/items/tasks/{task.id}/done")

    assert done.status_code == 200
    assert tasks_repo.get(conn, task.id).status == "done"
    recent = batches_repo.recent(conn, "2000-01-01", limit=5)
    assert any(b.tool == "task_done" for b in recent), "not recorded as task_done"


def test_the_nav_carries_the_section_and_marks_it_current(client):
    """EVERY ROUTED SECTION IS IN THE NAV AND EVERY NAV ITEM IS ROUTED — the
    inert-label rule (D4): six labels sat in this bar as dead spans for two
    days and read as a broken app."""
    page = client.get("/items").text
    assert 'href="/items"' in page
    at = page.index('href="/items"')
    assert "is-current-section" in page[at - 200 : at + 200]


# --- the filter is the view, and a write must not throw it away ----------------


def test_completing_a_task_keeps_the_account_filter(client):
    """Grant, 2026-08-21: "clicked done on a task in a client view but was
    redirected to /items showing all open items".

    /items filtered to one account IS the client view of open items — the
    filters live in the query string precisely so a view is a link you can
    keep. `done` re-rendered the page with `ref=None`, so the write silently
    threw the filter away and answered with the whole book. The task was
    completed correctly; what was lost was where you were standing.
    """
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    task = next(t for t in tasks_repo.open_tasks(conn) if t.org_id)
    org = orgs_repo.get(conn, task.org_id)
    others = [t for t in tasks_repo.open_tasks(conn) if t.org_id != task.org_id]
    assert others, "fixture drifted — need another account's task to leak"

    done = client.post(f"/items/tasks/{task.id}/done?account={org.ref}")

    assert done.status_code == 200
    assert f'value="{org.ref}" selected' in done.text, "the account filter was lost"
    for stray in others:
        assert f"/items/tasks/{stray.id}/done" not in done.text, (
            "another account's tasks came back on a filtered view"
        )


def test_dropping_a_task_keeps_the_account_filter(client):
    """Same route shape, same trap — drop copied done's re-render."""
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    task = next(t for t in tasks_repo.open_tasks(conn) if t.org_id)
    org = orgs_repo.get(conn, task.org_id)

    dropped = client.post(f"/items/tasks/{task.id}/drop?account={org.ref}")

    assert f'value="{org.ref}" selected' in dropped.text


def test_a_write_keeps_the_overdue_filter_too(client):
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    task = next(t for t in tasks_repo.open_tasks(conn) if t.org_id)

    done = client.post(f"/items/tasks/{task.id}/done?overdue=1")

    assert "checkbox" in done.text and "checked" in done.text


def test_the_buttons_carry_the_filter_they_were_rendered_under(client):
    """The route can only keep a filter the button sends it. Asserted on the
    RENDERED page, because a route that handles the query string and a template
    that never puts one there is the same bug with more code."""
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    task = next(t for t in tasks_repo.open_tasks(conn) if t.org_id)
    org = orgs_repo.get(conn, task.org_id)

    page = client.get(f"/items?account={org.ref}").text

    assert f"/items/tasks/{task.id}/done?account={org.ref}" in page
    assert f"/items/tasks/{task.id}/drop?account={org.ref}" in page
