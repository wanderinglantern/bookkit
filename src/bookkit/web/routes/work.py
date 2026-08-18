"""Work tab: open tasks, information requests, and (per request) their items.

Split out of routes/account.py alongside routes/relationship.py and
routes/pipeline.py (Task 8's structural requirement).

Tasks and RFI items are edited IN PLACE, cell by cell — the same contract
Task 6/8 settled for contacts (GET .../cell/{key} -> display, GET
.../cell/{key}/edit -> editor, POST .../cell/{key} -> save; both cell
macros swap outerHTML). Which fields are inline-editable comes from
bookkit.forms.inline.{TASK_FIELDS, RFI_ITEM_FIELDS} — not a web-only list.

Requests are the one exception: they carry FK selects (market, placement,
project) that the inline cell contract has no editor for, so — like the
TUI's own edit_request — they stay a whole form, reached the same way
contacts' "+ Add contact" is: a GET into a .form-host, a POST that saves
in place.

`Mark done` (tasks) and `Mark received` (RFI items) are POST buttons, not
cells: completing a task stamps completed_at AND status together
(tasks_repo.complete), and receiving an item stamps status AND received_on
together (services.rfi.mark_received) — a cell edit would let either pair
disagree. Both target the panel's own id directly (hx-swap="outerHTML"),
not "closest .form-host": the button lives inside a table row, not inside
the form, so there is no nested-panel risk to route around the way
contact_create's OOB swap does."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from markupsafe import escape

from ...forms.entities import (
    apply_request,
    apply_rfi_item,
    apply_task,
    request_form,
    rfi_item_form,
    task_form,
)
from ...forms.inline import RFI_ITEM_FIELDS, TASK_FIELDS
from ...forms.spec import Field, initial_text, parse_value
from ...models import Org, RfiRequest, Task
from ...repo import rfi as rfi_repo
from ...repo import tasks as tasks_repo
from ...services import batches as batches_svc
from ...services import rfi as rfi_svc
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display, render_form
from .account import _conn, _context, _org, _owned, _owned_item, _save

router = APIRouter()

# key -> Field, so a URL segment can be checked against the editable set
# server-side, the same guard relationship.py's _contact_field applies.
_TASK_CELLS: dict[str, Field] = {f.key: f for f in TASK_FIELDS}
_ITEM_CELLS: dict[str, Field] = {f.key: f for f in RFI_ITEM_FIELDS}


# --- tasks -----------------------------------------------------------------


def _task_cell_action(ref: str, task_id: str, key: str) -> str:
    return f"/accounts/{ref}/tasks/{task_id}/cell/{key}"


def _task_field(key: str) -> Field:
    field = _TASK_CELLS.get(key)
    if field is None:
        raise HTTPException(status_code=404, detail=f"{key!r} is not editable here")
    return field


def _task_due_suffix(task: Task) -> str:
    """The overdue badge, rendered INSIDE the due_on cell's own <td> via
    render_cell_display's `suffix` — never as a sibling appended by wrapping
    the cell in a second <td> (see macros/cell.html's `display` docstring:
    that nests illegally and silently misaligns every later column)."""
    overdue = task.due_on is not None and task.due_on < date.today().isoformat()
    return '<span class="tag-overdue">over</span>' if overdue else ""


def _task_row(request: Request, ref: str, task: Task) -> dict[str, Any]:
    def cell(key: str, *, extra_class: str = "", suffix: str = "") -> str:
        field = _TASK_CELLS[key]
        value = initial_text(field, getattr(task, key, None))
        action = _task_cell_action(ref, task.id, key)
        return render_cell_display(
            request, field, value, action, extra_class=extra_class, suffix=suffix
        )

    return {
        "id": task.id,
        "due_cell": cell("due_on", extra_class="num", suffix=_task_due_suffix(task)),
        "title_cell": cell("title"),
        "category_cell": cell("category"),
        "description_cell": cell("description"),
    }


def _task_rows(request: Request, org: Org) -> list[dict[str, Any]]:
    tasks = tasks_repo.open_tasks_for_client(_conn(request), org.id)
    return [_task_row(request, org.ref, t) for t in tasks]


def _tasks_panel(request: Request, org: Org, *, oob: bool = False) -> HTMLResponse:
    """`oob` mirrors _contacts_panel's fix: task_create's form still targets
    "closest .form-host" with an innerHTML swap (macros/form.html, shared and
    unchanged), and .form-host lives INSIDE this panel — a success has to
    replace the panel as an independent out-of-band swap, or the response
    nests a second copy of the panel inside itself."""
    context = {"header": {"org": org}, "oob": oob, "task_rows": _task_rows(request, org)}
    return TEMPLATES.TemplateResponse(request, "account/_tasks_panel.html", context)


@router.get("/accounts/{ref}/tasks/new", response_class=HTMLResponse)
def task_new_form(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    spec = task_form(conn=_conn(request), default_org_id=org.id)
    return HTMLResponse(render_form(request, spec, f"/accounts/{ref}/tasks/new"))


@router.post("/accounts/{ref}/tasks/new", response_class=HTMLResponse)
async def task_create(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    spec = task_form(conn=conn, default_org_id=org.id)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/tasks/new"
    refused = _save(
        request, org, spec, action, raw, lambda values: apply_task(conn, values, org.id)
    )
    return refused or _tasks_panel(request, org, oob=True)


def _task_display_cell(request: Request, ref: str, task_id: str, key: str) -> HTMLResponse:
    field = _task_field(key)
    existing = tasks_repo.get(_conn(request), task_id)
    value = initial_text(field, getattr(existing, key, None))
    action = _task_cell_action(ref, task_id, key)
    extra_class = "num" if key == "due_on" else ""
    suffix = _task_due_suffix(existing) if key == "due_on" else ""
    return HTMLResponse(
        render_cell_display(request, field, value, action, extra_class=extra_class, suffix=suffix)
    )


def _task_editor_cell(
    request: Request, ref: str, task_id: str, key: str,
    error: str | None = None, typed: str | None = None,
) -> HTMLResponse:
    field = _task_field(key)
    existing = tasks_repo.get(_conn(request), task_id)
    value = typed if typed is not None else initial_text(field, getattr(existing, key, None))
    action = _task_cell_action(ref, task_id, key)
    extra_class = "num" if key == "due_on" else ""
    return HTMLResponse(
        render_cell(request, field, value, action, error=error, extra_class=extra_class)
    )


@router.get("/accounts/{ref}/tasks/{task_id}/cell/{key}", response_class=HTMLResponse)
def task_cell(request: Request, ref: str, task_id: str, key: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "task", task_id, tasks_repo.get)
    return _task_display_cell(request, ref, task_id, key)


@router.get("/accounts/{ref}/tasks/{task_id}/cell/{key}/edit", response_class=HTMLResponse)
def task_cell_edit(request: Request, ref: str, task_id: str, key: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "task", task_id, tasks_repo.get)
    return _task_editor_cell(request, ref, task_id, key)


@router.post("/accounts/{ref}/tasks/{task_id}/cell/{key}", response_class=HTMLResponse)
async def task_cell_save(request: Request, ref: str, task_id: str, key: str) -> HTMLResponse:
    """One field, one writer action, one batch. task_id travels in the URL,
    never the row position — a refresh can reorder open tasks mid-edit."""
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned(conn, org, "task", task_id, tasks_repo.get)
    field = _task_field(key)
    raw = str((await request.form()).get(key, ""))
    try:
        value = parse_value(field, raw)
    except ValueError as exc:
        return _task_editor_cell(request, ref, task_id, key, error=str(exc), typed=raw)
    if field.required and value in (None, ""):
        return _task_editor_cell(
            request, ref, task_id, key, error=f"{field.label} is required", typed=raw
        )
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="edit_task",
            summary=f"set {field.label} on {existing.title}", org_id=org.id,
        ):
            tasks_repo.update(conn, task_id, **{key: value})
    except Exception as exc:  # a refused save is a message, never a 500
        return _task_editor_cell(request, ref, task_id, key, error=str(exc), typed=raw)
    return _task_display_cell(request, ref, task_id, key)


@router.post("/accounts/{ref}/tasks/{task_id}/done", response_class=HTMLResponse)
def task_done(request: Request, ref: str, task_id: str) -> HTMLResponse:
    """Done is a distinct writer action, not a field edit: tasks_repo.complete
    stamps completed_at AND status together, and a cell edit only ever writes
    one column."""
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned(conn, org, "task", task_id, tasks_repo.get)
    with batches_svc.open_batch(
        conn, source="web", tool="task_done",
        summary=f"completed {existing.title}", org_id=org.id,
    ):
        tasks_repo.complete(conn, task_id)
    return _tasks_panel(request, org)


# --- requests (master) ------------------------------------------------------


def _request_rows(request: Request, org: Org) -> list[dict[str, Any]]:
    conn = _conn(request)
    rows = []
    for r in rfi_repo.requests_for_org(conn, org.id):
        rows.append({
            "request": r,
            "asker": rfi_svc.asker_name(conn, r),
            "scope": rfi_svc.scope_label(conn, r),
            "open": rfi_svc.is_open(conn, r.id),
            "open_count": rfi_repo.open_item_count(conn, r.id),
            "total_count": rfi_repo.item_count(conn, r.id),
        })
    return rows


def _requests_panel(request: Request, org: Org, *, oob: bool = False) -> HTMLResponse:
    context = {"header": {"org": org}, "oob": oob, "request_rows": _request_rows(request, org)}
    return TEMPLATES.TemplateResponse(request, "account/_requests_panel.html", context)


@router.get("/accounts/{ref}/requests/new", response_class=HTMLResponse)
def request_new_form(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    spec = request_form(conn=_conn(request), org_id=org.id)
    return HTMLResponse(render_form(request, spec, f"/accounts/{ref}/requests/new"))


@router.post("/accounts/{ref}/requests/new", response_class=HTMLResponse)
async def request_create(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    spec = request_form(conn=conn, org_id=org.id)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/requests/new"
    refused = _save(
        request, org, spec, action, raw, lambda values: apply_request(conn, values, org.id)
    )
    return refused or _requests_panel(request, org, oob=True)


@router.get("/accounts/{ref}/requests/{request_id}/edit", response_class=HTMLResponse)
def request_edit_form(request: Request, ref: str, request_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned(conn, org, "request", request_id, rfi_repo.get_request)
    spec = request_form(existing, conn=conn, org_id=org.id)
    action = f"/accounts/{ref}/requests/{request_id}/edit"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/requests/{request_id}/edit", response_class=HTMLResponse)
async def request_update(request: Request, ref: str, request_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned(conn, org, "request", request_id, rfi_repo.get_request)
    spec = request_form(existing, conn=conn, org_id=org.id)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/requests/{request_id}/edit"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_request(conn, values, org.id, existing),
    )
    return refused or _requests_panel(request, org, oob=True)


# --- request items (detail) -------------------------------------------------


def _item_cell_action(ref: str, request_id: str, item_id: str, key: str) -> str:
    return f"/accounts/{ref}/requests/{request_id}/items/{item_id}/cell/{key}"


def _item_field(key: str) -> Field:
    field = _ITEM_CELLS.get(key)
    if field is None:
        raise HTTPException(status_code=404, detail=f"{key!r} is not editable here")
    return field


def _item_due_suffix(item: Any, request_row: RfiRequest) -> str:
    """The 'req: <date>' fallback note, rendered INSIDE the due_on cell's own
    <td> via render_cell_display's `suffix` — never as a sibling appended by
    wrapping the cell in a second <td> (see macros/cell.html's `display`
    docstring: that nests illegally and silently misaligns every later
    column). Only shown when the item has no due date of its own and falls
    back to the request's."""
    if item.due_on:
        return ""
    due_effective = rfi_svc.effective_due(item, request_row)
    if not due_effective:
        return ""
    return f'<span class="due-fallback">req: {escape(due_effective)}</span>'


def _item_row(
    request: Request, ref: str, request_id: str, item: Any, request_row: RfiRequest
) -> dict[str, Any]:
    def cell(key: str, *, extra_class: str = "", suffix: str = "") -> str:
        field = _ITEM_CELLS[key]
        value = initial_text(field, getattr(item, key, None))
        action = _item_cell_action(ref, request_id, item.id, key)
        return render_cell_display(
            request, field, value, action, extra_class=extra_class, suffix=suffix
        )

    return {
        "id": item.id,
        "kind": item.kind,
        "status": item.status,
        "received_on": item.received_on,
        "prompt_cell": cell("prompt"),
        "category_cell": cell("category"),
        "due_cell": cell("due_on", extra_class="num", suffix=_item_due_suffix(item, request_row)),
        "response_cell": cell("response"),
    }


def _items_context(request: Request, org: Org, request_id: str) -> dict[str, Any]:
    """Key is `rfi_request`, deliberately NOT `request`: Jinja2Templates'
    TemplateResponse does `context.setdefault("request", request)` to inject
    the FastAPI Request for url_for and its own debug-extension check — a
    context already carrying a "request" key (this RfiRequest) would shadow
    it, and Starlette's own _TemplateResponse.__call__ then calls .get() on
    the wrong object and blows up with an AttributeError that names neither
    of these two objects by name."""
    conn = _conn(request)
    request_row = rfi_repo.get_request(conn, request_id)
    items = rfi_repo.items_for_request(conn, request_id)
    return {
        "rfi_request": request_row,
        "asker": rfi_svc.asker_name(conn, request_row),
        "scope": rfi_svc.scope_label(conn, request_row),
        "is_open": rfi_svc.is_open(conn, request_id),
        "item_rows": [_item_row(request, org.ref, request_id, i, request_row) for i in items],
    }


def _items_panel(request: Request, org: Org, request_id: str, *, oob: bool = False) -> HTMLResponse:
    context = {"header": {"org": org}, "oob": oob, **_items_context(request, org, request_id)}
    return TEMPLATES.TemplateResponse(request, "account/_items_panel.html", context)


@router.get("/accounts/{ref}/requests/{request_id}/items/new", response_class=HTMLResponse)
def item_new_form(request: Request, ref: str, request_id: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "request", request_id, rfi_repo.get_request)
    spec = rfi_item_form(conn=_conn(request))
    action = f"/accounts/{ref}/requests/{request_id}/items/new"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/requests/{request_id}/items/new", response_class=HTMLResponse)
async def item_create(request: Request, ref: str, request_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "request", request_id, rfi_repo.get_request)
    spec = rfi_item_form(conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/requests/{request_id}/items/new"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_rfi_item(conn, values, request_id),
    )
    return refused or _items_panel(request, org, request_id, oob=True)


def _item_display_cell(
    request: Request, ref: str, request_id: str, item_id: str, key: str
) -> HTMLResponse:
    field = _item_field(key)
    conn = _conn(request)
    existing = rfi_repo.get_item(conn, item_id)
    value = initial_text(field, getattr(existing, key, None))
    action = _item_cell_action(ref, request_id, item_id, key)
    extra_class = ""
    suffix = ""
    if key == "due_on":
        extra_class = "num"
        suffix = _item_due_suffix(existing, rfi_repo.get_request(conn, request_id))
    return HTMLResponse(
        render_cell_display(request, field, value, action, extra_class=extra_class, suffix=suffix)
    )


def _item_editor_cell(
    request: Request, ref: str, request_id: str, item_id: str, key: str,
    error: str | None = None, typed: str | None = None,
) -> HTMLResponse:
    field = _item_field(key)
    existing = rfi_repo.get_item(_conn(request), item_id)
    value = typed if typed is not None else initial_text(field, getattr(existing, key, None))
    action = _item_cell_action(ref, request_id, item_id, key)
    extra_class = "num" if key == "due_on" else ""
    return HTMLResponse(
        render_cell(request, field, value, action, error=error, extra_class=extra_class)
    )


@router.get(
    "/accounts/{ref}/requests/{request_id}/items/{item_id}/cell/{key}",
    response_class=HTMLResponse,
)
def item_cell(request: Request, ref: str, request_id: str, item_id: str, key: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned_item(_conn(request), org, request_id, item_id)
    return _item_display_cell(request, ref, request_id, item_id, key)


@router.get(
    "/accounts/{ref}/requests/{request_id}/items/{item_id}/cell/{key}/edit",
    response_class=HTMLResponse,
)
def item_cell_edit(
    request: Request, ref: str, request_id: str, item_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    _owned_item(_conn(request), org, request_id, item_id)
    return _item_editor_cell(request, ref, request_id, item_id, key)


@router.post(
    "/accounts/{ref}/requests/{request_id}/items/{item_id}/cell/{key}",
    response_class=HTMLResponse,
)
async def item_cell_save(
    request: Request, ref: str, request_id: str, item_id: str, key: str
) -> HTMLResponse:
    """One field, one writer action, one batch. `status` is deliberately NOT
    in RFI_ITEM_FIELDS (apply_rfi_item owns the status/received_on pair), so
    this bare column write never touches either — confirmed against
    bookkit.forms.inline.RFI_ITEM_FIELDS, not assumed."""
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned_item(conn, org, request_id, item_id)
    field = _item_field(key)
    raw = str((await request.form()).get(key, ""))
    try:
        value = parse_value(field, raw)
    except ValueError as exc:
        return _item_editor_cell(request, ref, request_id, item_id, key, error=str(exc), typed=raw)
    if field.required and value in (None, ""):
        return _item_editor_cell(
            request, ref, request_id, item_id, key,
            error=f"{field.label} is required", typed=raw,
        )
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="edit_rfi_item",
            summary=f"set {field.label} on {existing.prompt}", org_id=org.id,
        ):
            rfi_repo.update_item(conn, item_id, **{key: value})
    except Exception as exc:
        return _item_editor_cell(request, ref, request_id, item_id, key, error=str(exc), typed=raw)
    return _item_display_cell(request, ref, request_id, item_id, key)


@router.post(
    "/accounts/{ref}/requests/{request_id}/items/{item_id}/received",
    response_class=HTMLResponse,
)
def item_received(request: Request, ref: str, request_id: str, item_id: str) -> HTMLResponse:
    """Received, dated, in one batch. mark_received writes status AND
    received_on; the batch is what makes the pair revert together."""
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned_item(conn, org, request_id, item_id)
    with batches_svc.open_batch(
        conn, source="web", tool="task_done",
        summary=f"received {existing.prompt}", org_id=org.id,
    ):
        rfi_svc.mark_received(conn, item_id, date.today().isoformat())
    return _items_panel(request, org, request_id)


@router.get("/accounts/{ref}/requests/{request_id}", response_class=HTMLResponse)
def request_detail(request: Request, ref: str, request_id: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    _owned(conn, org, "request", request_id, rfi_repo.get_request)
    context = _context(conn, org, "work", request)
    context.update(_items_context(request, org, request_id))
    return TEMPLATES.TemplateResponse(request, "account/request_detail.html", context)


# --- the tab itself ----------------------------------------------------------


@router.get("/accounts/{ref}/work", response_class=HTMLResponse)
def work_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    context = _context(conn, org, "work", request)
    context["task_rows"] = _task_rows(request, org)
    context["request_rows"] = _request_rows(request, org)
    return TEMPLATES.TemplateResponse(request, "account/work.html", context)
