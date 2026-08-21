"""Open items across the whole book — the working list, not an account's list.

Every other task surface in bookkit is scoped to one account: you go to the
account, then you see its work. That is the wrong shape for the question a
broker actually asks in the morning, which is "what is open, everywhere, and
what is late". Today answers a narrow version of it (what is due *by today*)
and stops there; this is the full list, editable where it is read.

WHAT THIS MODULE DOES NOT DO IS WRITE. Every cell on this page points at the
account-scoped routes that already exist — `/accounts/{ref}/tasks/{id}/cell/
{key}` in routes/work.py — because those already answer with the cell alone
rather than an account panel, which makes them correct on any page that
renders them. One parser, one guard, one batch, one refusal path, whether the
edit is made from the account's Work tab or from here. A second write path for
the same field is how two surfaces come to disagree about what a task is, and
it is the thing the derived-field work spent all of D6 avoiding.

The one exception is a task LEAVING the list — done or dropped — which the
account tab answers with its own panel; both writes are shared
(`work.complete_task`, `work.drop_task`) and only the re-render differs.

Data-entry rules, from the research pass (see .claude/skills/data-entry-
integrity): the account is a picker and never free text, the picker offers a
blank first option so the browser cannot answer the question for you, labels
stay visible, and nothing on this page pre-fills a figure that has to come off
a document.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...forms.entities import apply_task, task_form
from ...forms.spec import FieldError
from ...models import Org, Task, is_internal_category
from ...repo import orgs as orgs_repo
from ...repo import rfi as rfi_repo
from ...repo import tasks as tasks_repo
from ..app import TEMPLATES
from ..forms_render import render_cell_display, render_form
from .account import _conn
from .work import (
    _task_category_suffix,
    _task_cell_action,
    _task_cell_value,
    _task_due_suffix,
    complete_task,
    drop_task,
    task_or_404,
)

router = APIRouter()

_ADD_ACTION = "/items/tasks/new"


def _labels(conn: sqlite3.Connection, tasks: list[Task]) -> dict[str, Any]:
    """One lookup for every account on the page — ref and name together, so a
    row can be named and linked without a second query per task."""
    return orgs_repo.labels_for(conn, {t.org_id for t in tasks if t.org_id})


def _view_query(*, overdue_only: bool, ref: str | None) -> str:
    """The current filter as a query string, ready to append — "" when the view
    is the whole book. One builder, because the two writer buttons and any
    later one must all send back the view they were rendered under."""
    parts = []
    if ref:
        parts.append(f"account={quote(ref)}")
    if overdue_only:
        parts.append("overdue=1")
    return f"?{'&'.join(parts)}" if parts else ""


def _row(
    request: Request, task: Task, labels: dict[str, Any], view: str = ""
) -> dict[str, Any]:
    """One task, with its cells pointing at ITS OWN account's edit routes.

    The ref comes from the task's account rather than from the page, which is
    the whole trick that lets a book-wide list reuse account-scoped routes: a
    row for ACC-0004 posts to ACC-0004's URL and inherits that route's
    ownership check unchanged.

    A task with no account cannot be edited from here — its cells would have no
    URL to post to — so it renders as plain text and says why. That is rarer
    than it sounds (quick capture always attaches one) but it is not
    impossible, and a silently-uneditable row reads as a broken cell.
    """
    label = labels.get(task.org_id) if task.org_id else None
    conn = _conn(request)

    def cell(ref: str, key: str, suffix: str = "") -> str:
        from .work import _TASK_CELL_CLASS, _TASK_CELLS

        return render_cell_display(
            request,
            _TASK_CELLS[key],
            _task_cell_value(conn, task, key),
            _task_cell_action(ref, task.id, key),
            extra_class=_TASK_CELL_CLASS.get(key, ""),
            suffix=suffix,
        )

    overdue = task.due_on is not None and task.due_on < date.today().isoformat()
    return {
        "id": task.id,
        "org_id": task.org_id,
        "editable": label is not None,
        "overdue": overdue,
        "title": task.title,
        "due_on": task.due_on,
        "category": task.category,
        "internal": is_internal_category(task.category),
        "cells": (
            {
                "due_on": cell(label.ref, "due_on", _task_due_suffix(task)),
                "title": cell(label.ref, "title"),
                "category": cell(label.ref, "category", _task_category_suffix(task)),
                "assignee": cell(label.ref, "assignee"),
                "description": cell(label.ref, "description"),
            }
            if label is not None
            else {}
        ),
        # THE URLS CARRY THE PAGE'S OWN FILTER. The filters live in the query
        # string so a view is a link; a write that answers with the UNFILTERED
        # page throws that away, and the broker who was looking at one client's
        # open items is handed the whole book instead (Grant, 2026-08-21 —
        # "clicked done on a task in a client view but was redirected to /items
        # showing all open items"). The task completed correctly; what was lost
        # was where he was standing. Same query-string vocabulary as the GET,
        # so there is one spelling of "which view is this".
        "done_url": f"/items/tasks/{task.id}/done{view}",
        # Drop needs no account — it is one field on the task itself — so a
        # row with no client, which cannot open a single cell here, can still
        # be taken off the list.
        "drop_url": f"/items/tasks/{task.id}/drop{view}",
    }


def _open_tasks(conn: sqlite3.Connection, *, overdue_only: bool, ref: str | None) -> list[Task]:
    tasks = tasks_repo.open_tasks(conn)
    if ref:
        org = orgs_repo.find(conn, ref)
        tasks = [t for t in tasks if org is not None and t.org_id == org.id]
    if overdue_only:
        today = date.today().isoformat()
        tasks = [t for t in tasks if t.due_on is not None and t.due_on < today]
    return tasks


def _context(request: Request, *, overdue_only: bool, ref: str | None) -> dict[str, Any]:
    conn = _conn(request)
    tasks = _open_tasks(conn, overdue_only=overdue_only, ref=ref)
    labels = _labels(conn, tasks)
    today = date.today().isoformat()
    # Read ONCE. The first cut of this called outstanding_request_rows twice —
    # for the rows and again for their account labels — which is the same
    # repetition, one query lower down, that the account-link macro exists to
    # stop in the markup.
    requests = [dict(r) for r in rfi_repo.outstanding_request_rows(conn)]
    return {
        "section": "items",
        "tasks": [
            _row(request, t, labels, _view_query(overdue_only=overdue_only, ref=ref))
            for t in tasks
        ],
        "accounts": labels,
        "overdue_count": sum(
            1 for t in tasks if t.due_on is not None and t.due_on < today
        ),
        "undated_count": sum(1 for t in tasks if t.due_on is None),
        # The chase queue, read-only here: an outstanding item belongs to a
        # request, and editing one properly means seeing its request. The row
        # links to the account's Work tab rather than growing a second, thinner
        # editor for the same record.
        "requests": requests,
        "request_accounts": orgs_repo.labels_for(
            conn, {str(r["org_id"]) for r in requests if r.get("org_id")}
        ),
        "overdue_only": overdue_only,
        "filter_ref": ref or "",
        "all_accounts": sorted(
            orgs_repo.list_orgs(conn, kind="client"), key=lambda o: o.name
        ),
        "add_action": _ADD_ACTION,
    }


def _page(request: Request, *, overdue_only: bool, ref: str | None) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "items.html", _context(request, overdue_only=overdue_only, ref=ref)
    )


@router.get("/items", response_class=HTMLResponse)
def items_page(
    request: Request, overdue: str | None = None, account: str | None = None
) -> HTMLResponse:
    """The filters live in the QUERY STRING so a view is a link: "everything
    overdue" is a URL a broker can keep, and the back button behaves."""
    return _page(request, overdue_only=overdue == "1", ref=account)


@router.get("/items/tasks/new", response_class=HTMLResponse)
def task_new_form(request: Request) -> HTMLResponse:
    """The shared task form, carrying its account select — `task_form(conn=…)`
    is the same spec the TUI's app-level capture uses, so a task captured from
    the book-wide list is captured by exactly the rule that applies anywhere
    else. No `default_org_id`: this page belongs to no account, and guessing
    one would be a prefill nobody checks."""
    return HTMLResponse(
        render_form(request, task_form(conn=_conn(request)), _ADD_ACTION)
    )


@router.post("/items/tasks/new", response_class=HTMLResponse)
async def task_create(request: Request) -> HTMLResponse:
    conn = _conn(request)
    values = dict(await request.form())
    spec = task_form(conn=conn)
    try:
        apply_task(conn, {k: str(v) for k, v in values.items()})
    except (FieldError, ValueError) as exc:
        # Commit-in-place: the typed values come back exactly as typed.
        return HTMLResponse(
            render_form(
                request, spec, _ADD_ACTION, error=str(exc),
                submitted={k: str(v) for k, v in values.items()},
            )
        )
    return _page(request, overdue_only=False, ref=None)


@router.post("/items/tasks/{task_id}/done", response_class=HTMLResponse)
def task_done(
    request: Request,
    task_id: str,
    overdue: str | None = None,
    account: str | None = None,
) -> HTMLResponse:
    """Done is a distinct writer action — `complete` stamps status AND
    completed_at together, which a one-column cell edit cannot.

    The WRITE is work.complete_task, shared with the account tab; only the
    re-render differs, because there is no account panel to answer with here.
    """
    conn = _conn(request)
    task = task_or_404(conn, task_id)
    org: Org | None = orgs_repo.get(conn, task.org_id) if task.org_id else None
    complete_task(conn, org, task_id)
    return _page(request, overdue_only=overdue == "1", ref=account)


@router.post("/items/tasks/{task_id}/drop", response_class=HTMLResponse)
def task_drop(
    request: Request,
    task_id: str,
    overdue: str | None = None,
    account: str | None = None,
) -> HTMLResponse:
    """Dropped is not done. `complete` stamps completed_at and `drop` does
    not, because a task filed in error or overtaken by events is not finished
    work and must not be counted as any.

    The WRITE is work.drop_task, shared with the account tab and Today; only
    the re-render differs, for the same reason Done's does.
    """
    conn = _conn(request)
    task = task_or_404(conn, task_id)
    org: Org | None = orgs_repo.get(conn, task.org_id) if task.org_id else None
    drop_task(conn, org, task_id)
    return _page(request, overdue_only=overdue == "1", ref=account)
