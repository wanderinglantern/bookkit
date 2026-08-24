"""Projects tab: a client's construction projects and the cover each one needs.

The fifth account tab, and the last one the TUI had that a browser did not
(Grant, 2026-08-21). It is MASTER/DETAIL, the same shape the TUI's tab 5 uses:
the account's projects on the left, and the selected project's insurance needs
on the right. Which project is selected lives in the QUERY STRING (`?project=`)
for the reason /items' filters do — a view is a link, and the back button
behaves.

WHY A PROJECT IS NOT A PLACEMENT. A project is the client's job: a tower being
built, a plant being rebuilt. Its NEEDS are cover it will require and does not
have yet, each with a date the cover has to be in force by. That date is
attention in exactly the way a renewal is — repo/projects.needs_due feeds the
120-day window on Today and the navigator — and an unmet need never falls off
it. Turning a need into a real pursuit is `need → opportunity`, which is the
one write here that creates a record somewhere else.

WHAT IS AND IS NOT A CELL, per .claude/skills/data-entry-integrity:

* `line` is free text with completion, not a picker. The constrained-input
  rule wants a picker wherever the valid set is KNOWABLE, and lines of coverage
  are not — a broker will name cover this book has never carried, and a picker
  that refuses a real line is worse than no picker. `forms.inline.need_fields`
  wires the book's own lines as suggestions instead.
* `status` on both records IS a picker, over models.PROJECT_STATUSES /
  NEED_STATUSES — controlled but extensible, the TEAM_ROLES pattern — and the
  select renders a blank option like every other, so the browser cannot answer
  a question nobody was asked.
* `opportunity_id` and `placement_id` get NO cells. They are set by linking and
  shown as a derived "linked" column; a cell offering to retype an id writes
  nothing a person can reason about, which is why `signed_pct` is kept out of
  LAYER_FIELDS too.
* No figure is pre-filled. `limit` and `premium indication` come off a
  document, and people do not check prefills.

THE FORM HOST SITS OUTSIDE #projects-panel, the same placement pipeline.py
argues for: writes answer with the whole panel, and an out-of-band swap of a
panel containing the host would detach the element a refusal is swapped into.

Registered BEFORE routes/account.py's generic GET /accounts/{ref}/{tab} (see
web/app.py): Starlette resolves in registration order, not by specificity.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...dates import days_until
from ...forms.entities import apply_need, apply_project, need_form, project_form
from ...forms.inline import NEED_FIELDS, PROJECT_FIELDS, need_fields
from ...forms.spec import Field, initial_text, parse_value
from ...models import Org, Project, ProjectNeed
from ...repo import opportunities as opportunities_repo
from ...repo import projects as projects_repo
from ...services import batches as batches_svc
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display, render_form
from .account import _conn, _context, _org, _owned, _save

router = APIRouter()

# key -> Field, so a URL segment can be checked against the editable set
# server-side. Markup constrains a mouse and nothing else — the same guard
# relationship.py's _contact_field and work.py's _task_field apply.
_PROJECT_CELLS: dict[str, Field] = {f.key: f for f in PROJECT_FIELDS}
_NEED_CELLS: dict[str, Field] = {f.key: f for f in NEED_FIELDS}

_PROJECT_CELL_CLASS: dict[str, str] = {
    "start_on": "num", "end_on": "num", "name": "prose", "description": "prose",
}
_NEED_CELL_CLASS: dict[str, str] = {
    "needed_by": "num", "limit_cents": "num",
    "premium_indication_cents": "num", "notes": "prose",
}


def _project_field(key: str) -> Field:
    field = _PROJECT_CELLS.get(key)
    if field is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"a project has no {key!r} cell")
    return field


def _need_field(key: str) -> Field:
    field = _NEED_CELLS.get(key)
    if field is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"a need has no {key!r} cell")
    return field


def _need_editor_field(conn: sqlite3.Connection, key: str) -> Field:
    """The editor's version of one need field — `line` carries the book's own
    lines. The vocabulary is `forms.inline.need_fields`' call, not this
    module's, so the inline cell and the whole-record form offer the same
    list."""
    _need_field(key)  # the editable-set guard, before any query runs
    return {f.key: f for f in need_fields(conn)}[key]


def _project_cell_action(ref: str, project_id: str, key: str) -> str:
    return f"/accounts/{ref}/projects/{project_id}/cell/{key}"


def _need_cell_action(ref: str, need_id: str, key: str) -> str:
    return f"/accounts/{ref}/projects/needs/{need_id}/cell/{key}"


def _cell_value(field: Field, record: Any) -> str:
    return initial_text(field, getattr(record, field.key, None))


# --- reading ------------------------------------------------------------------


def _open_needs(conn: sqlite3.Connection, project_id: str) -> list[ProjectNeed]:
    return [
        need
        for need in projects_repo.needs_for_project(conn, project_id)
        if need.status in projects_repo.ATTENTION_STATUSES
    ]


def _project_row(
    request: Request, ref: str, project: Project, selected: str
) -> dict[str, Any]:
    conn = _conn(request)

    def cell(key: str) -> str:
        field = _project_field(key)
        return render_cell_display(
            request, field, _cell_value(field, project),
            _project_cell_action(ref, project.id, key),
            extra_class=_PROJECT_CELL_CLASS.get(key, ""),
        )

    return {
        "id": project.id,
        "ref": project.ref,
        "name": project.name,
        "is_selected": project.id == selected,
        "open_needs": len(_open_needs(conn, project.id)),
        "cells": {key: cell(key) for key in _PROJECT_CELLS},
    }


def _need_row(request: Request, ref: str, need: ProjectNeed) -> dict[str, Any]:
    def cell(key: str, suffix: str = "") -> str:
        field = _need_field(key)
        return render_cell_display(
            request, field, _cell_value(field, need),
            _need_cell_action(ref, need.id, key),
            extra_class=_NEED_CELL_CLASS.get(key, ""),
            suffix=suffix,
        )

    days = days_until(need.needed_by, date.today())
    settled = need.status not in projects_repo.ATTENTION_STATUSES
    return {
        "id": need.id,
        "days": days,
        # A SETTLED need does not shout about its date — the TUI's own rule for
        # this table. Placed cover has a date that has stopped being a deadline.
        "overdue": days < 0 and not settled,
        "settled": settled,
        "linked": (
            "opportunity" if need.opportunity_id
            else ("placement" if need.placement_id else "")
        ),
        "cells": {
            key: cell(key, _need_days_suffix(days, settled) if key == "needed_by" else "")
            for key in _NEED_CELLS
        },
        "can_promote": need.opportunity_id is None,
    }


def _need_days_suffix(days: int, settled: bool) -> str:
    """The countdown, in the SAME cell as the date it counts to.

    The four-surface bug's rule, applied here before it can happen: print the
    date you counted to. Nothing else on this row carries a date, so there is
    no second date for this number to drift away from.
    """
    if settled:
        return ""
    if days < 0:
        return f'<span class="badge-overdue">{-days}d over</span>'
    return f'<span class="detail-label">{days}d</span>'


def _selected_project(
    conn: sqlite3.Connection, org: Org, wanted: str | None
) -> Project | None:
    """Which project's needs are shown. The FIRST when nothing is asked for —
    a master/detail with nothing selected is a half-empty screen that makes the
    reader do the app's job — and None only when the account has none."""
    rows = projects_repo.projects_for_org(conn, org.id)
    if not rows:
        return None
    if wanted:
        chosen = next((p for p in rows if p.id == wanted), None)
        if chosen is not None:
            return chosen
    return rows[0]


def _panel(
    request: Request,
    ref: str,
    org: Org,
    *,
    wanted: str | None = None,
    error: str | None = None,
    oob: bool = False,
) -> HTMLResponse:
    conn = _conn(request)
    selected = _selected_project(conn, org, wanted)
    projects = [
        _project_row(request, ref, project, selected.id if selected else "")
        for project in projects_repo.projects_for_org(conn, org.id)
    ]
    needs = (
        [
            _need_row(request, ref, need)
            for need in projects_repo.needs_for_project(conn, selected.id)
        ]
        if selected
        else []
    )
    return TEMPLATES.TemplateResponse(
        request, "account/_projects_panel.html",
        {
            "header": {"org": org},
            "oob": oob,
            "error": error,
            "projects": projects,
            "selected": selected,
            "selected_description": (
                _project_description_cell(request, ref, selected) if selected else ""
            ),
            "needs": needs,
        },
    )


def _project_description_cell(request: Request, ref: str, project: Project) -> str:
    """The description, beside the needs it explains — NOT an eighth column.

    The projects table is scanned: ref, name, site, status, two dates and a
    count, each on one line. A prose column in it would set the row height from
    the longest sentence and destroy exactly the rhythm that makes a list of
    twenty readable. Density is not the enemy; undifferentiated density is, so
    this sits with the SELECTED project's needs, which is the one place a
    reader is asking "what is this job?".

    It is still an inline cell, and it must be: `description` is in
    PROJECT_FIELDS, and a field declared editable that no surface renders is
    the "built but not accessible" class — caught here by
    tests/test_web_projects.py before it shipped.
    """
    field = _project_field("description")
    return render_cell_display(
        request, field, _cell_value(field, project),
        _project_cell_action(ref, project.id, "description"),
        tag="span", extra_class="prose",
    )


@router.get("/accounts/{ref}/projects", response_class=HTMLResponse)
def projects_tab(
    request: Request, ref: str, project: str | None = None
) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    context = _context(conn, org, "projects", request)
    selected = _selected_project(conn, org, project)
    context["projects"] = [
        _project_row(request, ref, row, selected.id if selected else "")
        for row in projects_repo.projects_for_org(conn, org.id)
    ]
    context["selected"] = selected
    context["selected_description"] = (
        _project_description_cell(request, ref, selected) if selected else ""
    )
    context["needs"] = (
        [
            _need_row(request, ref, need)
            for need in projects_repo.needs_for_project(conn, selected.id)
        ]
        if selected
        else []
    )
    return TEMPLATES.TemplateResponse(request, "account/projects.html", context)


# --- adding -------------------------------------------------------------------


@router.get("/accounts/{ref}/projects/new", response_class=HTMLResponse)
def project_new_form(request: Request, ref: str) -> HTMLResponse:
    _org(request, ref)
    return HTMLResponse(
        render_form(request, project_form(), f"/accounts/{ref}/projects/new")
    )


@router.post("/accounts/{ref}/projects/new", response_class=HTMLResponse)
async def project_create(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    spec = project_form()
    action = f"/accounts/{ref}/projects/new"
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_project(conn, values, org.id),
    )
    return refused or _panel(request, ref, org, oob=True)


@router.get(
    "/accounts/{ref}/projects/{project_id}/needs/new", response_class=HTMLResponse
)
def need_new_form(request: Request, ref: str, project_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "project", project_id, projects_repo.get_project)
    return HTMLResponse(
        render_form(
            request, need_form(conn=conn),
            f"/accounts/{ref}/projects/{project_id}/needs/new",
        )
    )


@router.post(
    "/accounts/{ref}/projects/{project_id}/needs/new", response_class=HTMLResponse
)
async def need_create(request: Request, ref: str, project_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "project", project_id, projects_repo.get_project)
    spec = need_form(conn=conn)
    action = f"/accounts/{ref}/projects/{project_id}/needs/new"
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_need(conn, values, project_id),
    )
    return refused or _panel(request, ref, org, wanted=project_id, oob=True)


# --- editing in place ---------------------------------------------------------


def _project_display(
    request: Request, ref: str, project: Project, key: str
) -> HTMLResponse:
    field = _project_field(key)
    return HTMLResponse(
        render_cell_display(
            request, field, _cell_value(field, project),
            _project_cell_action(ref, project.id, key),
            extra_class=_PROJECT_CELL_CLASS.get(key, ""),
        )
    )


def _project_editor(
    request: Request, ref: str, project: Project, key: str,
    error: str | None = None, typed: str | None = None,
) -> HTMLResponse:
    field = _project_field(key)
    value = typed if typed is not None else _cell_value(field, project)
    return HTMLResponse(
        render_cell(
            request, field, value, _project_cell_action(ref, project.id, key),
            error=error, extra_class=_PROJECT_CELL_CLASS.get(key, ""),
        )
    )


@router.get(
    "/accounts/{ref}/projects/{project_id}/cell/{key}", response_class=HTMLResponse
)
def project_cell(
    request: Request, ref: str, project_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    project = _owned(_conn(request), org, "project", project_id, projects_repo.get_project)
    return _project_display(request, ref, project, key)


@router.get(
    "/accounts/{ref}/projects/{project_id}/cell/{key}/edit",
    response_class=HTMLResponse,
)
def project_cell_edit(
    request: Request, ref: str, project_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    project = _owned(_conn(request), org, "project", project_id, projects_repo.get_project)
    return _project_editor(request, ref, project, key)


@router.post(
    "/accounts/{ref}/projects/{project_id}/cell/{key}", response_class=HTMLResponse
)
async def project_cell_save(
    request: Request, ref: str, project_id: str, key: str
) -> HTMLResponse:
    """One field, one writer action, one batch — the contract every other
    inline cell in this app follows."""
    org = _org(request, ref)
    conn = _conn(request)
    project = _owned(conn, org, "project", project_id, projects_repo.get_project)
    field = _project_field(key)
    raw = str((await request.form()).get(key, ""))
    try:
        value = parse_value(field, raw)
    except Exception as exc:
        return _project_editor(request, ref, project, key, error=str(exc), typed=raw)
    if field.required and value in (None, ""):
        return _project_editor(
            request, ref, project, key,
            error=f"{field.label} is required", typed=raw,
        )
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="edit_project",
            summary=f"set {field.label} on {project.name}", org_id=org.id,
        ):
            projects_repo.update_project(conn, project_id, **{key: value})
    except Exception as exc:
        return _project_editor(request, ref, project, key, error=str(exc), typed=raw)
    return _project_display(
        request, ref, projects_repo.get_project(conn, project_id), key
    )


def _need_display(
    request: Request, ref: str, need: ProjectNeed, key: str
) -> HTMLResponse:
    field = _need_field(key)
    days = days_until(need.needed_by, date.today())
    settled = need.status not in projects_repo.ATTENTION_STATUSES
    return HTMLResponse(
        render_cell_display(
            request, field, _cell_value(field, need),
            _need_cell_action(ref, need.id, key),
            extra_class=_NEED_CELL_CLASS.get(key, ""),
            # The badge comes back WITH the saved cell, or moving a date leaves
            # the old countdown beside the new date until a refresh — the same
            # reason work.py's task cells re-render their suffixes.
            suffix=_need_days_suffix(days, settled) if key == "needed_by" else "",
        )
    )


def _need_editor(
    request: Request, ref: str, need: ProjectNeed, key: str,
    error: str | None = None, typed: str | None = None,
) -> HTMLResponse:
    field = _need_editor_field(_conn(request), key)
    value = typed if typed is not None else _cell_value(field, need)
    return HTMLResponse(
        render_cell(
            request, field, value, _need_cell_action(ref, need.id, key),
            error=error, extra_class=_NEED_CELL_CLASS.get(key, ""),
        )
    )


@router.get(
    "/accounts/{ref}/projects/needs/{need_id}/cell/{key}",
    response_class=HTMLResponse,
)
def need_cell(request: Request, ref: str, need_id: str, key: str) -> HTMLResponse:
    org = _org(request, ref)
    need = _owned(_conn(request), org, "need", need_id, projects_repo.get_need)
    return _need_display(request, ref, need, key)


@router.get(
    "/accounts/{ref}/projects/needs/{need_id}/cell/{key}/edit",
    response_class=HTMLResponse,
)
def need_cell_edit(
    request: Request, ref: str, need_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    need = _owned(_conn(request), org, "need", need_id, projects_repo.get_need)
    return _need_editor(request, ref, need, key)


@router.post(
    "/accounts/{ref}/projects/needs/{need_id}/cell/{key}",
    response_class=HTMLResponse,
)
async def need_cell_save(
    request: Request, ref: str, need_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    need = _owned(conn, org, "need", need_id, projects_repo.get_need)
    field = _need_editor_field(conn, key)
    raw = str((await request.form()).get(key, ""))
    try:
        value = parse_value(field, raw)
    except Exception as exc:
        return _need_editor(request, ref, need, key, error=str(exc), typed=raw)
    if field.required and value in (None, ""):
        return _need_editor(
            request, ref, need, key, error=f"{field.label} is required", typed=raw
        )
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="edit_project_need",
            summary=f"set {field.label} on {need.line}", org_id=org.id,
        ):
            projects_repo.update_need(conn, need_id, **{key: value})
    except Exception as exc:
        return _need_editor(request, ref, need, key, error=str(exc), typed=raw)
    return _need_display(request, ref, projects_repo.get_need(conn, need_id), key)


# --- a need becomes a pursuit ---------------------------------------------------


@router.post(
    "/accounts/{ref}/projects/needs/{need_id}/opportunity",
    response_class=HTMLResponse,
)
def need_to_opportunity(request: Request, ref: str, need_id: str) -> HTMLResponse:
    """The TUI's `o` on a need row: create the pre-filled opportunity and link
    it, in one batch.

    THE FIGURES CARRY OVER, and that is not a prefill nobody checks — they are
    values already recorded on the need by the person doing this, moving to a
    record that is about to be worked. What is NOT invented is anything the
    need does not state: an opportunity from a need with no premium indication
    gets none.

    Refuses the second press rather than making a second opportunity, and says
    so: a need already linked has nothing to promote.
    """
    org = _org(request, ref)
    conn = _conn(request)
    need = _owned(conn, org, "need", need_id, projects_repo.get_need)
    project = projects_repo.get_project(conn, need.project_id)
    if need.opportunity_id is not None:
        return _panel(
            request, ref, org, wanted=project.id,
            error=f"{need.line} already has an opportunity — it is on the Pipeline tab",
        )
    with batches_svc.open_batch(
        conn, source="web", tool="need_to_opportunity",
        summary=f"opportunity from {project.name} — {need.line}", org_id=org.id,
    ):
        opportunity = opportunities_repo.create(
            conn, project.org_id, f"{project.name} — {need.line}",
            lines=need.line,
            target_effective=need.needed_by,
            target_premium=need.premium_indication_cents,
        )
        projects_repo.update_need(conn, need.id, opportunity_id=opportunity.id)
    return _panel(request, ref, org, wanted=project.id)


__all__ = ["router"]
