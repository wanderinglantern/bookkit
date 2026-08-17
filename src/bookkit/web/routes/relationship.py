"""Relationship tab: contacts (Task 8), interactions land in Task 10.

Split out of routes/account.py (Task 8's structural requirement — that file
is edited by four more tasks and needs to stay one shell, not accrue every
tab's routes). Imports the shared helpers from account.py rather than
redefining them.

Editing is IN PLACE, cell by cell — not behind an Edit button (Grant's
2026-08-17 amendment to the original brief, which still had a whole-form
`.../contacts/{id}/edit`). The cell contract was settled in Task 6
(web/forms_render.py, templates/macros/cell.html):

    GET  .../cell/{key}       -> the display cell
    GET  .../cell/{key}/edit  -> the editor cell
    POST .../cell/{key}       -> save; display cell, or editor + error + typed

Both macros render a whole <td> and swap outerHTML, so activating a cell
REPLACES the display element and its htmx listener — an innerHTML swap left
the old listener in place and a click into the editor bubbled back to it,
discarding the edit before it could be typed."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...forms.entities import apply_contact, contact_form
from ...forms.inline import CONTACT_FIELDS
from ...forms.spec import Field, initial_text, parse_value
from ...models import Org
from ...repo import contacts as contacts_repo
from ...services import batches as batches_svc
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display, render_form
from .account import _conn, _context, _org, _save

router = APIRouter()

# key -> Field, so a URL segment can be checked against the editable set
# server-side (never trusting the template alone to keep first_name/
# last_name off the cell routes — a non-editable key 404s here).
_CONTACT_CELLS: dict[str, Field] = {f.key: f for f in CONTACT_FIELDS}


def _cell_action(ref: str, contact_id: str, key: str) -> str:
    return f"/accounts/{ref}/contacts/{contact_id}/cell/{key}"


def _contact_field(key: str) -> Field:
    field = _CONTACT_CELLS.get(key)
    if field is None:
        raise HTTPException(status_code=404, detail=f"{key!r} is not editable here")
    return field


_CONTACT_FIELD_LABELS: dict[str, str] = {"role": "ROLE", "email": "EMAIL", "phone": "PHONE"}


def _contact_row(request: Request, ref: str, contact: Any) -> dict[str, Any]:
    """One contact rendered as a card (Account View.dc.html's PEOPLE block,
    Grant's fix round 1: cards, not table.rows). DOM order here IS tab
    order for inline-cell.js's Tab-hop (it walks `.cell[data-field]` inside
    the nearest `.contact-card`, not CONTACT_FIELDS' own declared order) —
    title sits with the name per the design, role/email/phone stack below
    it, so that's the order cells are built in.

    Every cell passes tag="div": there's no <table> here for a bare <td> to
    live in, and one dropped silently by the HTML parser is worse than one
    that's merely wrong. first_name/last_name stay plain text — the TUI's
    CONTACT_INLINE has no entry for them either, so neither surface makes
    them inline-editable."""
    fields_by_key = {f.key: f for f in CONTACT_FIELDS}

    def cell(key: str) -> str:
        field = fields_by_key[key]
        value = initial_text(field, getattr(contact, key, None))
        return render_cell_display(
            request, field, value, _cell_action(ref, contact.id, key), tag="div"
        )

    return {
        "id": contact.id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "is_primary": contact.is_primary,
        "title_cell": cell("title"),
        "fields": [
            {"label": _CONTACT_FIELD_LABELS[key], "cell": cell(key)}
            for key in ("role", "email", "phone")
        ],
    }


def _contacts_context(request: Request, org: Org) -> dict[str, Any]:
    contacts = contacts_repo.for_org(_conn(request), org.id)
    rows = [_contact_row(request, org.ref, c) for c in contacts]
    return {"rows": rows, "count": len(rows)}


def _contacts_panel(request: Request, org: Org, *, oob: bool = False) -> HTMLResponse:
    """`oob=True` renders the panel as an out-of-band swap (a lone
    `#contacts-panel` element carrying `hx-swap-oob="true"`, nothing else in
    the response) rather than the plain fragment a full tab-page render
    embeds. contact_create's success path needs this: the form that POSTed
    still targets "closest .form-host" with an innerHTML swap (unchanged),
    and .form-host lives INSIDE this panel — returning the whole panel as
    that primary swap's content nested a second copy of it inside itself.
    OOB makes it two independent swaps instead of one nested one: the
    primary swap clears .form-host (closing the form), the OOB swap
    replaces #contacts-panel."""
    context = {"header": {"org": org}, "oob": oob, **_contacts_context(request, org)}
    return TEMPLATES.TemplateResponse(request, "account/_contacts_panel.html", context)


@router.get("/accounts/{ref}/relationship", response_class=HTMLResponse)
def relationship_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    context = _context(conn, org, "relationship")
    context.update(_contacts_context(request, org))
    context["oob"] = False  # a full tab-page render is never an OOB swap
    return TEMPLATES.TemplateResponse(request, "account/relationship.html", context)


@router.get("/accounts/{ref}/contacts/new", response_class=HTMLResponse)
def contact_new_form(request: Request, ref: str) -> HTMLResponse:
    _org(request, ref)
    spec = contact_form()
    return HTMLResponse(render_form(request, spec, f"/accounts/{ref}/contacts/new"))


@router.post("/accounts/{ref}/contacts/new", response_class=HTMLResponse)
async def contact_create(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    spec = contact_form()
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/contacts/new"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_contact(_conn(request), org.id, values),
    )
    # oob=True on success only: a refusal returns just the form + error
    # (from _save, unmodified), which correctly swaps into .form-host as
    # it always did — only a successful add needs the OOB panel replace.
    return refused or _contacts_panel(request, org, oob=True)


def _contact_display_cell(request: Request, ref: str, contact_id: str, key: str) -> HTMLResponse:
    field = _contact_field(key)
    existing = contacts_repo.get(_conn(request), contact_id)
    value = initial_text(field, getattr(existing, key, None))
    action = _cell_action(ref, contact_id, key)
    # tag="div": these swaps land inside a .contact-card, not a <table> row
    # (see _contact_row and forms_render.render_cell_display's docstring).
    return HTMLResponse(render_cell_display(request, field, value, action, tag="div"))


def _contact_editor_cell(
    request: Request, ref: str, contact_id: str, key: str,
    error: str | None = None, typed: str | None = None,
) -> HTMLResponse:
    field = _contact_field(key)
    existing = contacts_repo.get(_conn(request), contact_id)
    value = typed if typed is not None else initial_text(field, getattr(existing, key, None))
    action = _cell_action(ref, contact_id, key)
    return HTMLResponse(render_cell(request, field, value, action, error=error, tag="div"))


@router.get("/accounts/{ref}/contacts/{contact_id}/cell/{key}", response_class=HTMLResponse)
def contact_cell(request: Request, ref: str, contact_id: str, key: str) -> HTMLResponse:
    _org(request, ref)
    return _contact_display_cell(request, ref, contact_id, key)


@router.get("/accounts/{ref}/contacts/{contact_id}/cell/{key}/edit", response_class=HTMLResponse)
def contact_cell_edit(request: Request, ref: str, contact_id: str, key: str) -> HTMLResponse:
    _org(request, ref)
    return _contact_editor_cell(request, ref, contact_id, key)


@router.post("/accounts/{ref}/contacts/{contact_id}/cell/{key}", response_class=HTMLResponse)
async def contact_cell_save(request: Request, ref: str, contact_id: str, key: str) -> HTMLResponse:
    """One field, one writer action, one batch. The contact id is in the
    URL — never the row position, because a refresh can reorder rows
    mid-edit (inline_edit.py's row_key-captured-at-open rule, mirrored
    here: the cell action URL is baked in when the row renders)."""
    org = _org(request, ref)
    conn = _conn(request)
    field = _contact_field(key)
    raw = str((await request.form()).get(key, ""))
    try:
        value = parse_value(field, raw)
    except ValueError as exc:
        return _contact_editor_cell(request, ref, contact_id, key, error=str(exc), typed=raw)
    if field.required and value in (None, ""):
        return _contact_editor_cell(
            request, ref, contact_id, key, error=f"{field.label} is required", typed=raw
        )
    existing = contacts_repo.get(conn, contact_id)
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="edit_contact",
            summary=f"set {field.label} on {existing.first_name} {existing.last_name}",
            org_id=org.id,
        ):
            contacts_repo.update(conn, contact_id, **{key: value})
    except Exception as exc:
        return _contact_editor_cell(request, ref, contact_id, key, error=str(exc), typed=raw)
    return _contact_display_cell(request, ref, contact_id, key)
