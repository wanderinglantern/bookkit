"""Team on the web (gap 7): the roster page, and the account rail's Team
section gone live.

Two surfaces in one module because they are one subject. GET /team is the
TUI TeamScreen's list — name, title, specialty, where they're assigned, the
"who for cyber?" filter as a plain querystring — plus what the TUI does not
have yet: retiring (deactivate) behind a confirm step, and reactivating.
The /accounts/{ref}/team/* routes are the rail's Assign / edit / remove,
mirroring the TUI's `w` and `D` on the ov-team table.

The business rules all live below this layer, on purpose:
- name uniqueness is repo/team.py's guard — create and rename go through
  team.create_member / team.update_member and inherit it; no re-check here.
- deactivate/reactivate are services.team.member_deactivate/_reactivate,
  the SAME calls mcpserver delegates to — refusal-while-assigned, cascade
  as ONE revertible batch, and the sentences are all the service's.
- assignments are corrected IN PLACE over role/lines/notes and never
  re-scoped: the edit form is forms.entities.assignment_form(existing=...)
  which deliberately renders no client/placement field, and the write is
  apply_assignment — the TUI's own.
- removal is team.unassign inside a batch stamped tool="team_unassign"
  with the TUI's own summary sentence, behind a confirm STEP (a GET that
  writes nothing), not an hx-confirm — same bar as contact removal.

htmx contract (routes/program.py's): refusals are HTTP 200 with the
sentence in the page; forms target "closest .form-host"; a POST that
targets a panel answers panel-shaped on refusal too (the error slot), or
it would delete the panel it refused into."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ... import db
from ...forms.entities import (
    NEW_MEMBER,
    apply_assignment,
    assignment_form,
    member_form,
)
from ...forms.spec import BatchSpec, FieldError, FormSpec, dropped, parse_values
from ...models import Org, TeamMember
from ...repo import base as base_repo
from ...repo import placements as placements_repo
from ...repo import team as team_repo
from ...services import batches as batches_svc
from ...services import team as team_svc
from ..app import TEMPLATES
from ..forms_render import render_form
from .account import _conn, _org

router = APIRouter()


# --- the roster page ---------------------------------------------------------


def _member_row(conn: sqlite3.Connection, member: TeamMember, match: str) -> dict[str, Any]:
    """One member, TeamScreen's columns: name, title, specialty, where
    they're assigned (the org names, like the TUI's `assignments` column)
    plus the count the web brief asks for, the match evidence when a filter
    is on, and the active state the TUI never needs to show (it lists active
    members only; reactivate needs the retired ones on screen)."""
    assignments = team_repo.for_member(conn, member.id)
    where = ", ".join(
        sorted({str(r["org_name"]) for r in assignments if r["org_name"]})
    )
    return {
        "id": member.id,
        "name": member.name,
        "title": member.title or "—",
        "specialty": member.specialty or "—",
        "count": len(assignments),
        "where": where or "—",
        "match": match,
        "active": member.active,
    }


def _members_context(conn: sqlite3.Connection, line: str) -> dict[str, Any]:
    """Filtered like the TUI's `f`: services.team.find_specialists over
    specialties AND live assignment lines. Retired members render below the
    active list, unfiltered — a filter answers "who do I go to?", and the
    answer is never someone who left."""
    if line:
        rows = [
            _member_row(conn, m.member, f"{m.score:.0f}% · {m.evidence}")
            for m in team_svc.find_specialists(conn, line)
        ]
    else:
        rows = [_member_row(conn, m, "") for m in team_repo.list_members(conn)]
    retired = [
        _member_row(conn, m, "")
        for m in team_repo.list_members(conn, active_only=False)
        if not m.active
    ]
    return {"rows": rows, "retired": retired, "line": line}


def _line_param(request: Request) -> str:
    return str(request.query_params.get("line", "")).strip()


def _members_panel(
    request: Request, *, oob: bool = False, error: str | None = None
) -> HTMLResponse:
    """The roster panel fragment. `oob`/`error` follow _contacts_panel's
    contract exactly: a successful form POST returns the panel out-of-band
    (the primary swap clears the .form-host the form sits in), a refused
    panel-targeting POST returns the panel WITH the sentence in its error
    slot — answering panel-shaped is what keeps a refusal from deleting the
    panel it refused into."""
    context = {
        "oob": oob, "error": error,
        **_members_context(_conn(request), _line_param(request)),
    }
    return TEMPLATES.TemplateResponse(request, "team/_members_panel.html", context)


@router.get("/team", response_class=HTMLResponse)
def team_page(request: Request) -> HTMLResponse:
    context = {
        "section": "team",
        "oob": False,
        **_members_context(_conn(request), _line_param(request)),
    }
    return TEMPLATES.TemplateResponse(request, "team.html", context)


def _save_unscoped(
    request: Request, spec: FormSpec, action: str, raw: dict[str, str], write: Any
) -> HTMLResponse | None:
    """routes/account._save without the org: team members belong to no
    account, and the TUI's FormModal default is the same BatchSpec.for_title
    with org_id None. Returns the re-rendered form on refusal (input intact,
    nothing written — the repo's duplicate-name guard surfaces here), or
    None on success."""
    try:
        values = parse_values(spec, raw)
    except FieldError as exc:
        return HTMLResponse(render_form(request, spec, action, exc.message, raw))
    batch = BatchSpec.for_title(spec.title)
    try:
        with batches_svc.open_batch(
            _conn(request), source="web", tool=batch.tool,
            summary=batch.sentence(values),
        ):
            write(values)
    except Exception as exc:  # a refused save is a message, never a 500
        return HTMLResponse(render_form(request, spec, action, str(exc), raw))
    return None


@router.get("/team/members/new", response_class=HTMLResponse)
def member_new_form(request: Request) -> HTMLResponse:
    spec = member_form(conn=_conn(request))
    return HTMLResponse(render_form(request, spec, "/team/members/new"))


@router.post("/team/members/new", response_class=HTMLResponse)
async def member_create(request: Request) -> HTMLResponse:
    """team.create_member — the duplicate-name guard is repo/team.py's own
    and its refusal comes back as the form with the sentence in it."""
    conn = _conn(request)
    spec = member_form(conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _save_unscoped(
        request, spec, "/team/members/new", raw,
        lambda values: team_repo.create_member(conn, **dropped(values)),
    )
    return refused or _members_panel(request, oob=True)


def _member_or_404(conn: sqlite3.Connection, member_id: str) -> TeamMember:
    try:
        return team_repo.get_member(conn, member_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"no team member {member_id!r}"
        ) from None


@router.get("/team/members/{member_id}/edit", response_class=HTMLResponse)
def member_edit_form(request: Request, member_id: str) -> HTMLResponse:
    conn = _conn(request)
    existing = _member_or_404(conn, member_id)
    spec = member_form(existing, conn=conn)
    return HTMLResponse(
        render_form(request, spec, f"/team/members/{member_id}/edit")
    )


@router.post("/team/members/{member_id}/edit", response_class=HTMLResponse)
async def member_update(request: Request, member_id: str) -> HTMLResponse:
    """Renames go through team.update_member, behind the same duplicate
    guard as creation — two members sharing a name makes every lookup
    ambiguous, and the guard lives in repo/ so this surface cannot skip it."""
    conn = _conn(request)
    existing = _member_or_404(conn, member_id)
    spec = member_form(existing, conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _save_unscoped(
        request, spec, f"/team/members/{member_id}/edit", raw,
        lambda values: team_repo.update_member(conn, member_id, **dropped(values)),
    )
    return refused or _members_panel(request, oob=True)


@router.get("/team/members/{member_id}/deactivate", response_class=HTMLResponse)
def member_deactivate_confirm(request: Request, member_id: str) -> HTMLResponse:
    """The confirm step: writes NOTHING, and names the blast radius — every
    live assignment, in the service's own words, because retiring someone
    with cascade removes all of them. Already-inactive answers as the panel
    with the service's sentence (a stale tab's click), never a 404 htmx
    would drop in silence."""
    conn = _conn(request)
    member = _member_or_404(conn, member_id)
    if not member.active:
        return _members_panel(
            request, oob=True, error=f"{member.name} is already inactive"
        )
    rows = team_repo.for_member(conn, member.id)
    assignments = [
        {
            "label": team_svc.assignment_label(row),
            "lines": row["lines"] or "",
        }
        for row in rows
    ]
    return TEMPLATES.TemplateResponse(
        request,
        "team/_member_confirm_deactivate.html",
        {"member": member, "assignments": assignments, "line": _line_param(request)},
    )


@router.post("/team/members/{member_id}/deactivate", response_class=HTMLResponse)
async def member_deactivate(request: Request, member_id: str) -> HTMLResponse:
    """services.team.member_deactivate — the same call mcpserver makes.
    `cascade` rides as a form field from the confirm step's own button, so
    removing N assignments and retiring is ONE revertible batch. The form is
    read before the service opens its batch (nothing may await inside one)."""
    conn = _conn(request)
    member = _member_or_404(conn, member_id)
    raw = await request.form()
    cascade = str(raw.get("cascade", "")) == "1"
    try:
        team_svc.member_deactivate(conn, member.id, cascade=cascade, source="web")
    except (ValueError, db.BlastRadiusExceeded) as exc:
        # the blast cap is a refusal like any other: a cascade over 250
        # assignments lands in the page, not as a 500
        return _members_panel(request, error=str(exc))
    return _members_panel(request)


@router.post("/team/members/{member_id}/reactivate", response_class=HTMLResponse)
def member_reactivate(request: Request, member_id: str) -> HTMLResponse:
    """No confirm step: one field flips back, the batch reverts it, and
    nothing cascades — assignments a deactivation removed do NOT come back
    (revert the deactivation batch for that, as the service's docstring
    says)."""
    conn = _conn(request)
    member = _member_or_404(conn, member_id)
    try:
        team_svc.member_reactivate(conn, member.id, source="web")
    except ValueError as exc:
        return _members_panel(request, error=str(exc))
    return _members_panel(request)


# --- the account rail: assign / edit / remove --------------------------------


def _team_panel(
    request: Request, org: Org, *, oob: bool = False, error: str | None = None
) -> HTMLResponse:
    """The rail's Team section, standalone — the same partial page.html
    includes, same context keys. Refusals land in its error slot for the
    reason _contacts_panel's docstring gives: a POST targeting the panel
    must answer panel-shaped or the refusal deletes the panel."""
    context = {
        "header": {"org": org},
        "team": team_repo.for_org(_conn(request), org.id),
        "oob": oob,
        "team_error": error,
    }
    return TEMPLATES.TemplateResponse(request, "account/_team_panel.html", context)


def _assign_action(ref: str) -> str:
    return f"/accounts/{ref}/team/assign"


def _assign_spec(conn: sqlite3.Connection) -> FormSpec:
    """The TUI's own option labels: name, with the specialty in brackets
    when there is one."""
    members = team_repo.list_members(conn)
    options = tuple(
        (f"{m.name} ({m.specialty})" if m.specialty else m.name, m.id)
        for m in members
    )
    return assignment_form(options, conn=conn)


@router.get("/accounts/{ref}/team/assign", response_class=HTMLResponse)
def assignment_new_form(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    if not team_repo.list_members(conn):
        # the TUI's own refusal for `w` with an empty roster, pointed at the
        # web's own add flow rather than at a keyboard it doesn't have
        return _team_panel(
            request, org, oob=True,
            error="no team members yet — add one on the Team page first",
        )
    spec = _assign_spec(conn)
    return HTMLResponse(render_form(request, spec, _assign_action(ref)))


@router.post("/accounts/{ref}/team/assign", response_class=HTMLResponse)
async def assignment_create(request: Request, ref: str) -> HTMLResponse:
    """Account-level assignment (org_id, no placement) — the rail is
    account-scoped, matching the TUI's `w` anywhere but the placements
    table. One batch via the same BatchSpec.for_title default the TUI's
    FormModal derives from this same form title."""
    org = _org(request, ref)
    conn = _conn(request)
    spec = _assign_spec(conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = _assign_action(ref)
    if raw.get("team_member_id") == NEW_MEMBER:
        # the TUI chains this sentinel into a second modal; the web keeps the
        # roster's add in one place instead of forking the member form here
        return HTMLResponse(render_form(
            request, spec, action,
            "add the colleague on the Team page first, then assign them here",
            raw,
        ))

    def write(values: dict[str, Any]) -> None:
        cleaned = dropped(values)
        member_id = str(cleaned.pop("team_member_id"))
        team_repo.assign(conn, member_id, org_id=org.id, **cleaned)

    try:
        values = parse_values(spec, raw)
    except FieldError as exc:
        return HTMLResponse(render_form(request, spec, action, exc.message, raw))
    batch = BatchSpec.for_title(spec.title, org_id=org.id)
    try:
        with batches_svc.open_batch(
            conn, source="web", tool=batch.tool,
            summary=batch.sentence(values), org_id=org.id,
        ):
            write(values)
    except Exception as exc:  # a refused save is a message, never a 500
        return HTMLResponse(render_form(request, spec, action, str(exc), raw))
    return _team_panel(request, org, oob=True)


def _owned_assignment_row(
    conn: sqlite3.Connection, org: Org, assignment_id: str
) -> sqlite3.Row:
    """Ownership for the compound claim /accounts/{ref}/team/{assignment_id}
    — this account, AND this row of it. Checked against the RAW row so the
    destructive flow can answer "already removed" instead of a bare 404
    (routes/account._owns_raw_row's rule); deal-level rows carry no org_id of
    their own, so ownership resolves through the placement, the same rule
    _assignment_org_id applies for batch stamping."""
    row = base_repo.raw_row(conn, "team_assignment", assignment_id)
    owner: str | None = None
    if row is not None:
        owner = row["org_id"]
        if owner is None and row["placement_id"] is not None:
            try:
                owner = placements_repo.get(conn, str(row["placement_id"])).org_id
            except KeyError:  # a deleted placement owns nothing on this page
                owner = None
    if row is None or owner != org.id:
        raise HTTPException(
            status_code=404,
            detail=f"no team assignment {assignment_id!r} on {org.name}",
        )
    return row


def _assignment_edit_action(ref: str, assignment_id: str) -> str:
    return f"/accounts/{ref}/team/{assignment_id}/edit"


@router.get("/accounts/{ref}/team/{assignment_id}/edit", response_class=HTMLResponse)
def assignment_edit_form(request: Request, ref: str, assignment_id: str) -> HTMLResponse:
    """IN PLACE over role/lines/notes, never re-scoped: assignment_form with
    `existing` renders no client and no placement field — moving someone
    between clients is unassign + assign, kept deliberately separate (the
    same rule the MCP edit_field follows)."""
    org = _org(request, ref)
    conn = _conn(request)
    row = _owned_assignment_row(conn, org, assignment_id)
    if row["deleted_at"] is not None:
        return _team_panel(
            request, org, oob=True, error="that assignment was already removed"
        )
    existing = team_repo.get_assignment(conn, assignment_id)
    spec = assignment_form(title="edit assignment", conn=conn, existing=existing)
    return HTMLResponse(
        render_form(request, spec, _assignment_edit_action(ref, assignment_id))
    )


@router.post("/accounts/{ref}/team/{assignment_id}/edit", response_class=HTMLResponse)
async def assignment_update(request: Request, ref: str, assignment_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    row = _owned_assignment_row(conn, org, assignment_id)
    if row["deleted_at"] is not None:
        return _team_panel(
            request, org, oob=True, error="that assignment was already removed"
        )
    existing = team_repo.get_assignment(conn, assignment_id)
    spec = assignment_form(title="edit assignment", conn=conn, existing=existing)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = _assignment_edit_action(ref, assignment_id)
    try:
        values = parse_values(spec, raw)
    except FieldError as exc:
        return HTMLResponse(render_form(request, spec, action, exc.message, raw))
    batch = BatchSpec.for_title(spec.title, org_id=org.id)
    try:
        with batches_svc.open_batch(
            conn, source="web", tool=batch.tool,
            summary=batch.sentence(values), org_id=org.id,
        ):
            apply_assignment(conn, values, existing)
    except Exception as exc:  # a refused save is a message, never a 500
        return HTMLResponse(render_form(request, spec, action, str(exc), raw))
    return _team_panel(request, org, oob=True)


@router.get("/accounts/{ref}/team/{assignment_id}/remove", response_class=HTMLResponse)
def assignment_remove_confirm(request: Request, ref: str, assignment_id: str) -> HTMLResponse:
    """The confirm step — a GET that writes NOTHING, naming who comes off
    and which lines, because with several rows for one person "remove Rosa
    Delgado" alone reads as though she came off the account entirely (the
    TUI's own toast rule)."""
    org = _org(request, ref)
    conn = _conn(request)
    row = _owned_assignment_row(conn, org, assignment_id)
    member = team_repo.get_member(conn, str(row["team_member_id"]))
    if row["deleted_at"] is not None:
        return _team_panel(
            request, org, oob=True,
            error=f"{member.name}'s assignment was already removed",
        )
    return TEMPLATES.TemplateResponse(
        request,
        "account/_assignment_confirm_remove.html",
        {
            "org": org,
            "assignment_id": assignment_id,
            "who": member.name,
            "lines": row["lines"] or "no lines",
        },
    )


@router.post("/accounts/{ref}/team/{assignment_id}/remove", response_class=HTMLResponse)
def assignment_remove(request: Request, ref: str, assignment_id: str) -> HTMLResponse:
    """team.unassign inside one batch — tool and summary are the TUI `D`
    flow's own (account.py's _remove_assignment), so `R` reads one sentence
    whichever surface removed the row. Soft, and revertible."""
    org = _org(request, ref)
    conn = _conn(request)
    row = _owned_assignment_row(conn, org, assignment_id)
    member = team_repo.get_member(conn, str(row["team_member_id"]))
    if row["deleted_at"] is not None:
        return _team_panel(
            request, org,
            error=f"{member.name}'s assignment was already removed",
        )
    with batches_svc.open_batch(
        conn, source="web", tool="team_unassign",
        summary=f"removed {member.name} from this team", org_id=org.id,
    ):
        team_repo.unassign(conn, assignment_id)
    return _team_panel(request, org)
