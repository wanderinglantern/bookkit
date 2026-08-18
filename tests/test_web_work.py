"""The Work tab — open tasks, information requests, and (per request) their
items.

The assertion is deliberately NOT 'the field changed'. A plain outcome check
passes even when a route writes outside a batch (see test_web_writes.py's
docstring for the 33-call-site precedent this guards against). What is
asserted is that the batch exists, that it carries source='web', that it
carries real events, and that reverting it puts the record back.

seed.py seeds ~25 tasks at random but never seeds any RFI request — the
app_and_org fixture below makes both deterministic rather than relying on
chance or skipping (a skipped test protects nothing)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import db
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs
    from bookkit.repo import rfi as rfi_repo
    from bookkit.repo import tasks as tasks_repo

    conn = app.state.conn
    org = orgs.list_orgs(conn, kind="client")[0]

    if not tasks_repo.open_tasks_for_client(conn, org.id):
        tasks_repo.create(conn, "Chase loss runs", org_id=org.id, due_on="2026-08-20")

    request = rfi_repo.create_request(conn, org.id, "Loss run refresh", "2026-08-10")
    rfi_repo.add_item(conn, request.id, "loss runs 2021-2025", category="Financials")

    with TestClient(app) as client:
        yield client, org, request


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, since="", limit=1)
    return found[0] if found else None


# --- the tab shell -----------------------------------------------------------


def test_work_tab_lists_tasks_and_requests(app_and_org):
    client, org, request = app_and_org
    response = client.get(f"/accounts/{org.ref}/work")
    assert response.status_code == 200
    assert request.title in response.text


def test_requests_say_who_was_asked_and_what_they_are_about(app_and_org):
    """A request with no asker and no scope is an ask you cannot chase."""
    client, org, request = app_and_org
    conn = client.app.state.conn
    from bookkit.services import rfi as rfi_svc

    response = client.get(f"/accounts/{org.ref}/work")
    assert rfi_svc.scope_label(conn, request) in response.text
    assert rfi_svc.asker_name(conn, request) in response.text


# --- tasks: inline cell editing -----------------------------------------------


def _category_cell(html: str) -> str:
    """The category cell's own element, start tag to its close — the badge
    must live INSIDE it (a <td> nested in a <td> silently drifts every later
    column out from under its header; see macros/cell.html)."""
    start = html.index('data-field="category"')
    start = html.rindex("<td", 0, start)
    return html[start:html.index("</td>", start)]


def test_internal_task_row_says_not_exported(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    tasks_repo.create(conn, "our own file note", org_id=org.id, category="Internal")
    tasks_repo.create(conn, "renew GL", org_id=org.id, category="Renewal")
    body = client.get(f"/accounts/{org.ref}/work").text
    assert body.count("tag-internal") == 1
    assert body.count("not exported") == 1
    cell = _category_cell(body[body.index("our own file note"):])
    assert "not exported" in cell
    assert "<td" not in cell[3:], "the badge opened a second <td>"


def test_saving_a_category_to_internal_returns_the_badge(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.create(conn, "our own file note", org_id=org.id, category="Renewal")
    response = client.post(
        f"/accounts/{org.ref}/tasks/{task.id}/cell/category", data={"category": "Internal"}
    )
    assert response.status_code == 200
    assert "tag-internal" in response.text and "not exported" in response.text
    assert "<td" not in _category_cell(response.text)[3:]


def test_a_non_internal_category_carries_no_badge(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.create(conn, "audit support", org_id=org.id, category="Renewal")
    response = client.post(
        f"/accounts/{org.ref}/tasks/{task.id}/cell/category",
        data={"category": "Internal Review"},
    )
    assert response.status_code == 200
    assert "not exported" not in response.text


def test_the_task_category_cell_editor_offers_the_vocabulary(app_and_org):
    """The inline cell is the PRIMARY edit path here — click the cell, no
    modal — so the completion that makes "Internal" discoverable has to be in
    it. The whole-record modal already had it; this is where the risk lives."""
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.create(conn, "audit support", org_id=org.id, category="Renewal")
    response = client.get(f"/accounts/{org.ref}/tasks/{task.id}/cell/category/edit")
    assert response.status_code == 200
    assert 'list="cl-category"' in response.text
    assert '<datalist id="cl-category">' in response.text
    # the one category nobody has typed yet, offered anyway (repo.vocab)
    assert '<option value="Internal">' in response.text
    assert '<option value="Renewal">' in response.text


def test_a_non_vocabulary_cell_editor_carries_no_datalist(app_and_org):
    """Only the category field has a vocabulary — a stray datalist on `title`
    would mean the enrichment is being applied by position, not by key."""
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    response = client.get(f"/accounts/{org.ref}/tasks/{task.id}/cell/title/edit")
    assert response.status_code == 200
    assert "datalist" not in response.text


def test_a_non_editable_key_is_404_before_the_vocabulary_query(app_and_org):
    """The editable-set guard still runs first on the editor route — the
    vocabulary lookup must not become a way to reach a field the cell
    contract does not expose."""
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    assert client.get(
        f"/accounts/{org.ref}/tasks/{task.id}/cell/priority/edit"
    ).status_code == 404


def test_editing_a_task_cell_writes_one_web_batch(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    response = client.post(
        f"/accounts/{org.ref}/tasks/{task.id}/cell/title",
        data={"title": "Chase the loss runs — updated"},
    )
    assert response.status_code == 200
    assert tasks_repo.get(conn, task.id).title == "Chase the loss runs — updated"

    batch = _latest_batch(conn)
    assert batch is not None, "the edit wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"
    assert batches_repo.events_for(conn, batch.id), "the batch carries no events to revert"


def test_a_task_cell_edit_reverts(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo
    from bookkit.services import batches as batches_svc

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    before = task.title

    client.post(f"/accounts/{org.ref}/tasks/{task.id}/cell/title", data={"title": "Renamed"})
    batch = _latest_batch(conn)
    assert batch is not None
    batches_svc.revert(conn, batch.ref, now=db.utc_now())
    assert tasks_repo.get(conn, task.id).title == before


def test_a_task_cell_blanking_the_required_title_is_refused_intact(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    before = task.title
    response = client.post(f"/accounts/{org.ref}/tasks/{task.id}/cell/title", data={"title": ""})
    assert response.status_code == 200
    assert "required" in response.text
    assert tasks_repo.get(conn, task.id).title == before


def test_a_task_cell_bare_number_due_date_is_refused_intact(app_and_org):
    """A bare number is not a date, on every surface."""
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    before = task.due_on
    response = client.post(
        f"/accounts/{org.ref}/tasks/{task.id}/cell/due_on", data={"due_on": "5"}
    )
    assert response.status_code == 200
    assert "a bare number is ambiguous" in response.text
    assert tasks_repo.get(conn, task.id).due_on == before


def test_a_non_editable_task_key_is_404_not_a_write(app_and_org):
    """priority is on the whole-record form but not in TASK_FIELDS — which
    fields are inline-editable is not a per-surface choice."""
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    response = client.post(
        f"/accounts/{org.ref}/tasks/{task.id}/cell/priority", data={"priority": "1"}
    )
    assert response.status_code == 404
    assert tasks_repo.get(conn, task.id).priority == task.priority


def test_adding_a_task_writes_one_web_batch(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    before = len(tasks_repo.open_tasks_for_client(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/tasks/new",
        data={"title": "Prepare submission", "description": "", "category": "",
              "due_on": "2026-09-01", "priority": "2", "org_id": org.id, "detail": ""},
    )
    assert response.status_code == 200
    assert len(tasks_repo.open_tasks_for_client(conn, org.id)) == before + 1

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"


def test_a_task_with_a_bare_number_due_date_is_refused(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    before = len(tasks_repo.open_tasks_for_client(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/tasks/new",
        data={"title": "Chase the loss runs", "description": "", "category": "",
              "due_on": "5", "priority": "2", "org_id": org.id, "detail": ""},
    )
    assert response.status_code == 200
    assert "a bare number is ambiguous" in response.text
    assert "Chase the loss runs" in response.text
    assert len(tasks_repo.open_tasks_for_client(conn, org.id)) == before


def test_completing_a_task_is_one_web_batch_and_reverts(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo
    from bookkit.services import batches as batches_svc

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    response = client.post(f"/accounts/{org.ref}/tasks/{task.id}/done")
    assert response.status_code == 200
    assert tasks_repo.get(conn, task.id).status != task.status

    batch = _latest_batch(conn)
    assert batch is not None, "completion wrote outside any batch"
    assert batch.source == "web"

    batches_svc.revert(conn, batch.ref, now=db.utc_now())
    assert tasks_repo.get(conn, task.id).status == task.status


# --- requests: whole-form add and edit ---------------------------------------


def test_creating_a_request_writes_one_web_batch(app_and_org):
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    before = len(rfi_repo.requests_for_org(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/requests/new",
        data={"title": "Renewal questionnaire", "requested_on": "2026-08-14",
              "due_on": "", "market_org_id": "", "placement_id": "",
              "project_id": "", "cancelled_at": "", "notes": ""},
    )
    assert response.status_code == 200
    assert len(rfi_repo.requests_for_org(conn, org.id)) == before + 1

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"


def test_a_request_with_a_bad_date_is_refused_intact(app_and_org):
    """A bare number is not a date, on every surface."""
    client, org, _ = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    before = len(rfi_repo.requests_for_org(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/requests/new",
        data={"title": "Renewal questionnaire", "requested_on": "5",
              "due_on": "", "market_org_id": "", "placement_id": "",
              "project_id": "", "cancelled_at": "", "notes": "chase Friday"},
    )
    assert response.status_code == 200
    assert "a bare number is ambiguous" in response.text
    assert "Renewal questionnaire" in response.text
    assert "chase Friday" in response.text
    assert len(rfi_repo.requests_for_org(conn, org.id)) == before


def test_editing_a_request_writes_one_web_batch_and_reverts(app_and_org):
    client, org, request = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo
    from bookkit.services import batches as batches_svc

    before_title = request.title
    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/edit",
        data={"title": "Loss run refresh — round 2", "requested_on": request.requested_on,
              "due_on": request.due_on or "", "market_org_id": request.market_org_id or "",
              "placement_id": request.placement_id or "", "project_id": request.project_id or "",
              "cancelled_at": request.cancelled_at or "", "notes": request.notes or ""},
    )
    assert response.status_code == 200
    assert rfi_repo.get_request(conn, request.id).title == "Loss run refresh — round 2"

    batch = _latest_batch(conn)
    assert batch is not None, "the edit wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"

    batches_svc.revert(conn, batch.ref, now=db.utc_now())
    assert rfi_repo.get_request(conn, request.id).title == before_title


def test_a_refused_request_edit_keeps_every_value_and_writes_nothing(app_and_org):
    """The refusal contract on the form that shipped after Task 8. It routes
    through the shared `_save`, so this asserts the seam is actually taken —
    a green suite proves nothing broke, not that the new path is used.

    The batch count is not redundant with the row check: `_save` opens its
    batch BEFORE calling `write`, so a swallowed refusal strands an empty
    EventBatch in RECENT CHANGES with nothing for `Revert` to put back, even
    when the record itself is untouched."""
    client, org, request = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import rfi as rfi_repo

    before_batches = len(batches_repo.recent(conn, since="", limit=50))

    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/edit",
        data={"title": "", "requested_on": request.requested_on,
              "due_on": "2026-08-31", "market_org_id": "", "placement_id": "",
              "project_id": "", "cancelled_at": "", "notes": "chase Friday"},
    )

    assert response.status_code == 200, "htmx drops 4xx — a refusal must be a 200"
    assert '<p class="form-error" role="alert">request is required</p>' in response.text
    # every other value survives the refusal
    assert "2026-08-31" in response.text
    assert "chase Friday" in response.text
    # and nothing was written — not the record, not a stranded batch
    assert rfi_repo.get_request(conn, request.id).title == request.title
    assert rfi_repo.get_request(conn, request.id).due_on == request.due_on
    assert len(batches_repo.recent(conn, since="", limit=50)) == before_batches


# --- items: detail page, inline editing, mark received -----------------------


def test_request_detail_lists_its_items(app_and_org):
    client, org, request = app_and_org
    from bookkit.repo import rfi as rfi_repo

    items = rfi_repo.items_for_request(client.app.state.conn, request.id)
    response = client.get(f"/accounts/{org.ref}/requests/{request.id}")
    assert response.status_code == 200
    assert items[0].prompt in response.text


def test_editing_an_item_cell_writes_one_web_batch(app_and_org):
    client, org, request = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import rfi as rfi_repo

    item = rfi_repo.items_for_request(conn, request.id)[0]
    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/cell/category",
        data={"category": "Loss history"},
    )
    assert response.status_code == 200
    assert rfi_repo.get_item(conn, item.id).category == "Loss history"

    batch = _latest_batch(conn)
    assert batch is not None, "the edit wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"
    assert batches_repo.events_for(conn, batch.id), "the batch carries no events to revert"


def test_a_non_editable_item_key_is_404_not_a_write(app_and_org):
    """status is deliberately not in RFI_ITEM_FIELDS — apply_rfi_item owns
    the status/received_on pair, and a bare cell write would let them
    disagree."""
    client, org, request = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    item = rfi_repo.items_for_request(conn, request.id)[0]
    before = item.status
    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/cell/status",
        data={"status": "received"},
    )
    assert response.status_code == 404
    assert rfi_repo.get_item(conn, item.id).status == before


def test_an_item_cell_bare_number_due_date_is_refused_intact(app_and_org):
    client, org, request = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    item = rfi_repo.items_for_request(conn, request.id)[0]
    before = item.due_on
    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/cell/due_on",
        data={"due_on": "5"},
    )
    assert response.status_code == 200
    assert "a bare number is ambiguous" in response.text
    assert rfi_repo.get_item(conn, item.id).due_on == before


def test_adding_an_item_writes_one_web_batch(app_and_org):
    client, org, request = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    before = len(rfi_repo.items_for_request(conn, request.id))
    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/items/new",
        data={"prompt": "SOV update", "kind": "document", "category": "Property",
              "due_on": "", "detail": "", "status": "outstanding",
              "received_on": "", "response": ""},
    )
    assert response.status_code == 200
    assert len(rfi_repo.items_for_request(conn, request.id)) == before + 1

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"


def test_marking_an_item_received_stamps_the_date_in_one_batch_and_reverts(app_and_org):
    """status OWNS received_on — the pair can never disagree. Both writes land
    in one batch, so a revert restores both."""
    client, org, request = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo
    from bookkit.services import batches as batches_svc

    item = rfi_repo.items_for_request(conn, request.id)[0]
    assert item.status == "outstanding"

    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/received"
    )
    assert response.status_code == 200

    after = rfi_repo.get_item(conn, item.id)
    assert after.status == "received"
    assert after.received_on, "received without a date — the pair disagreed"

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"

    batches_svc.revert(conn, batch.ref, now=db.utc_now())
    restored = rfi_repo.get_item(conn, item.id)
    assert restored.status == "outstanding"
    assert not restored.received_on, "revert left a stale received date"
