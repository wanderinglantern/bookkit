"""DROPPED IS NOT DONE — the web's second way off a task list.

`done` stamps completed_at; `drop` does not. That is the whole point of the
pair: a task filed in error, or overtaken by events, is not finished work, and
letting the only exit be "Done" quietly writes a completion date for work that
never happened — which is then what "what did I finish this month" answers
with. The TUI has kept them apart since it had a task list (`d` vs `D`,
AccountScreen._drop_task / NavigatorScreen.action_drop_row); this is that key
on the web, on all three surfaces that list a task.

What is asserted here, beyond "it writes":

* the two writes DIFFER in the one field that matters (completed_at), on every
  surface — a drop that stamped it would pass any "the row went away" test;
* all three surfaces make ONE write through routes/work.drop_task, so the
  changes list cannot describe the same action three ways depending on which
  page it was done from (Grant's DRY rule: the copy that quietly differs);
* the drop is revertible as one batch, which is the reason it ships without a
  confirm STEP the way contacts and interactions have one;
* the account route still refuses another account's task.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.models import TaskStatus
from bookkit.repo import batches as batches_repo
from bookkit.repo import orgs as orgs_repo
from bookkit.repo import tasks as tasks_repo
from bookkit.services import batches as batches_svc
from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _an_open_task(conn):
    """An open task that HAS an account — the account route needs a ref, and
    the seeded book's tasks all carry one."""
    task = next(t for t in tasks_repo.open_tasks(conn) if t.org_id)
    return task, orgs_repo.get(conn, task.org_id)


def _due_task(conn):
    """One that Today lists — Today shows only what is due BY today, so a
    task picked at random would not be on that page to drop. date.today() is
    the conftest's frozen clock, the same one the route reads."""
    return next(t for t in tasks_repo.open_tasks(conn, due_by=date.today().isoformat()))


def _latest_batch(conn):
    return batches_repo.recent(conn, "0000", limit=1)[0]


# --- the distinction itself -------------------------------------------------


@pytest.mark.parametrize("surface", ["account", "items", "today"])
def test_drop_marks_the_task_dropped_and_never_stamps_completed_at(client, surface):
    """The one assertion the whole feature exists for. `dropped` with a
    completion date on it is a lie the book would then report as finished
    work, and every "the row disappeared" check would still pass."""
    conn = client.app.state.conn
    if surface == "today":
        task = _due_task(conn)
        url = f"/today/tasks/{task.id}/drop"
    elif surface == "items":
        task, _ = _an_open_task(conn)
        url = f"/items/tasks/{task.id}/drop"
    else:
        task, org = _an_open_task(conn)
        url = f"/accounts/{org.ref}/tasks/{task.id}/drop"
    assert task.completed_at is None

    assert client.post(url).status_code == 200

    after = tasks_repo.get(conn, task.id)
    assert after.status == TaskStatus.DROPPED
    assert after.completed_at is None, "a dropped task is not finished work"


def test_done_still_stamps_completed_at_on_every_surface(client):
    """The other half of the pair, and a regression guard: Today's `done`
    was re-pointed at work.complete_task in the same change that added drop."""
    conn = client.app.state.conn
    task = _due_task(conn)
    assert client.post(f"/today/tasks/{task.id}/done").status_code == 200
    after = tasks_repo.get(conn, task.id)
    assert after.status == TaskStatus.DONE
    assert after.completed_at is not None


def test_a_dropped_task_leaves_every_open_list(client):
    conn = client.app.state.conn
    task, org = _an_open_task(conn)
    client.post(f"/accounts/{org.ref}/tasks/{task.id}/drop")

    assert task.id not in {t.id for t in tasks_repo.open_tasks(conn)}
    assert task.id not in {t.id for t in tasks_repo.open_tasks_for_client(conn, org.id)}
    # by id, not by title: the seeded book has several tasks called the same
    # thing, and a title check would pass on the wrong row's absence
    assert f"/items/tasks/{task.id}/drop" not in client.get("/items").text


# --- one write, three surfaces ----------------------------------------------


@pytest.mark.parametrize("surface", ["account", "items", "today"])
def test_every_surface_files_the_same_batch(client, surface):
    """DRY, asserted at the seam rather than by reading three files. The tool
    name and the sentence come from work.drop_task; if a surface grows its own
    copy, the changes list starts describing one action two ways depending on
    the page it was done from."""
    conn = client.app.state.conn
    if surface == "today":
        task = _due_task(conn)
        url = f"/today/tasks/{task.id}/drop"
    elif surface == "items":
        task, _ = _an_open_task(conn)
        url = f"/items/tasks/{task.id}/drop"
    else:
        task, org = _an_open_task(conn)
        url = f"/accounts/{org.ref}/tasks/{task.id}/drop"

    client.post(url)

    batch = _latest_batch(conn)
    assert batch.source == "web"
    assert batch.tool == "task_drop"
    assert batch.summary == f"dropped {task.title}"
    assert batch.org_id == task.org_id


def test_the_drop_is_revertible_as_one_batch(client):
    """The reason it ships without a confirm STEP: one field write, and Undo
    puts it straight back. If that stopped being true the button would need
    the confirm fragment contacts and interactions have."""
    conn = client.app.state.conn
    task, org = _an_open_task(conn)
    client.post(f"/accounts/{org.ref}/tasks/{task.id}/drop")
    assert tasks_repo.get(conn, task.id).status == TaskStatus.DROPPED

    batches_svc.revert(conn, _latest_batch(conn).ref, now="2026-08-21T00:00:00Z")

    assert tasks_repo.get(conn, task.id).status == TaskStatus.OPEN


# --- the controls -----------------------------------------------------------


@pytest.mark.parametrize(
    "page,post",
    [
        ("/items", r'hx-post="/items/tasks/([^"]+)/drop"'),
        ("/today", r'hx-post="/today/tasks/([^"]+)/drop"'),
    ],
)
def test_the_book_wide_lists_render_a_drop_beside_every_done(client, page, post):
    html = client.get(page).text
    dropped = re.findall(post, html)
    done = re.findall(post.replace("/drop", "/done"), html)
    assert done, "expected at least one task row on this page"
    assert dropped == done, "every done needs its drop — they are the pair"


def test_the_account_work_tab_renders_a_drop_beside_every_done(client):
    conn = client.app.state.conn
    _, org = _an_open_task(conn)
    html = client.get(f"/accounts/{org.ref}/work").text
    pattern = r'hx-post="/accounts/[^/]+/tasks/([^"]+)/(done|drop)"'
    pairs: dict[str, set[str]] = {}
    for task_id, verb in re.findall(pattern, html):
        pairs.setdefault(task_id, set()).add(verb)
    assert pairs, "expected at least one task row on the Work tab"
    assert all(v == {"done", "drop"} for v in pairs.values())


def test_the_drop_control_names_the_task_before_it_writes(client):
    """It sits beside Done and the row simply goes afterwards, so the confirm
    says WHICH task and what dropping does not mean."""
    conn = client.app.state.conn
    task, org = _an_open_task(conn)
    html = client.get(f"/accounts/{org.ref}/work").text
    confirm = re.search(
        rf'hx-post="/accounts/[^/]+/tasks/{task.id}/drop"\s+hx-confirm="([^"]+)"',
        html,
    )
    assert confirm, "drop must ask before it writes"
    assert task.title in confirm.group(1)
    assert "done" in confirm.group(1)


# --- the guards -------------------------------------------------------------


def test_the_account_route_refuses_another_accounts_task(client):
    conn = client.app.state.conn
    task, org = _an_open_task(conn)
    other = next(
        o for o in orgs_repo.list_orgs(conn, kind="client") if o.id != task.org_id
    )
    got = client.post(f"/accounts/{other.ref}/tasks/{task.id}/drop")
    assert got.status_code == 404
    assert tasks_repo.get(conn, task.id).status == TaskStatus.OPEN


@pytest.mark.parametrize(
    "url",
    [
        "/today/tasks/TSK-does-not-exist/drop",
        "/today/tasks/TSK-does-not-exist/done",
        "/items/tasks/TSK-does-not-exist/drop",
        "/items/tasks/TSK-does-not-exist/done",
    ],
)
def test_an_unknown_task_is_a_404_not_a_traceback(client, url):
    """The two BOOK-WIDE surfaces address a task by id alone, so a stale id is
    ordinary — a page held open while the same task is cleared in another tab
    posts one. Both verbs on both pages go through work.task_or_404; /items
    used to let the KeyError out as a 500."""
    assert client.post(url).status_code == 404


def test_a_task_with_no_account_can_still_be_dropped_from_the_book_list(client):
    """It cannot be EDITED there — its cells would have no account route to
    post to — but taking it off the list needs no account, and a row that can
    only ever be completed is a row that lies about what happened to it."""
    conn = client.app.state.conn
    orphan = tasks_repo.create(conn, "orphaned ask", due_on=None)
    assert orphan.org_id is None

    page = client.get("/items").text
    assert f'hx-post="/items/tasks/{orphan.id}/drop"' in page

    assert client.post(f"/items/tasks/{orphan.id}/drop").status_code == 200
    assert tasks_repo.get(conn, orphan.id).status == TaskStatus.DROPPED
