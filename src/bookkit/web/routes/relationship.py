"""Relationship tab: contacts (Task 8) and the interactions timeline (Task 10).

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
discarding the edit before it could be typed.

Interactions are the exception, and deliberately so (R49). They are edited
through the WHOLE `interaction_form`, not cell by cell, because
bookkit.forms.inline owns which fields are inline-editable for both surfaces
and declares no INTERACTION_FIELDS — the TUI edits an interaction through that
same modal. A web-only inline set would fork the two surfaces on exactly the
axis that module exists to keep unified, so the prototype's dashed underline
on the subject is dropped. Creation is absent for the matching reason:
forms.entities has an interaction EDIT builder and no create builder, because
logging one is quick capture's job (account matching, the follow-up-task
offer), so the header's "+ Log interaction" pill stays inert."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...forms.entities import apply_contact, apply_interaction, contact_form, interaction_form
from ...forms.inline import CONTACT_FIELDS
from ...forms.spec import Field, initial_text, parse_value
from ...models import Interaction, InteractionType, Org
from ...repo import contacts as contacts_repo
from ...repo import interactions as interactions_repo
from ...services import batches as batches_svc
from ...services import contacts as contacts_svc
from ...services import interactions as interactions_svc
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display, render_form
from .account import (
    _conn,
    _context,
    _org,
    _owned,
    _owns_contact_row,
    _owns_raw_row,
    _save,
)

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


def _contacts_panel(
    request: Request, org: Org, *, oob: bool = False, error: str | None = None
) -> HTMLResponse:
    """`oob=True` renders the panel as an out-of-band swap (a lone
    `#contacts-panel` element carrying `hx-swap-oob="true"`, nothing else in
    the response) rather than the plain fragment a full tab-page render
    embeds. contact_create's success path needs this: the form that POSTed
    still targets "closest .form-host" with an innerHTML swap (unchanged),
    and .form-host lives INSIDE this panel — returning the whole panel as
    that primary swap's content nested a second copy of it inside itself.
    OOB makes it two independent swaps instead of one nested one: the
    primary swap clears .form-host (closing the form), the OOB swap
    replaces #contacts-panel.

    `error` is a refusal's own sentence, rendered at the top of the panel. The
    panel is the one fragment a refusal can reach on either half of the remove
    control, so both send it here: the POST as the primary swap (its confirm
    button targets #contacts-panel), the confirm GET as `oob=True` with the
    error inside — see contact_remove and contact_remove_confirm for why the
    GET has no other place to put it."""
    context = {
        "header": {"org": org}, "oob": oob, "error": error,
        **_contacts_context(request, org),
    }
    return TEMPLATES.TemplateResponse(request, "account/_contacts_panel.html", context)


# --- the timeline (Task 10) --------------------------------------------------
#
# The same read the tab badge counts (routes/account._counts), so the number
# beside TIMELINE and the number on the tab cannot describe different books.
_TIMELINE_LIMIT = 200

_INTERACTION_TYPES = frozenset(t.value for t in InteractionType)


def _filter_type(params: Mapping[str, str]) -> str | None:
    """The `type` query param, or None — and NEVER the raw string.

    Validated against models.InteractionType rather than passed through: an
    unrecognised value renders the unfiltered timeline instead of echoing a
    crafted word back into the page as a selected pill, and there is no
    template name or SQL fragment it could reach either way."""
    raw = params.get("type")
    return raw if raw in _INTERACTION_TYPES else None


def _timeline_query(type_filter: str | None) -> str:
    """`type` ALONE, never the request's whole query string.

    ?undo=&outcome=&n= live on this same tab url (routes/changes.py's revert
    toast). Carrying them through a filter link — or through an edit form's
    action — would re-show a message about a revert that already happened, on
    a page the user reached by clicking something else entirely."""
    return f"?{urlencode({'type': type_filter})}" if type_filter else ""


def _type_label(interaction_type: str) -> str:
    """`site_visit` reads as "site visit" — the same de-slugging the TUI's
    _pretty does. The stored value is untouched; only the label changes."""
    return interaction_type.replace("_", " ")


def _timeline_row(request: Request, ref: str, entry: Interaction, query: str) -> dict[str, Any]:
    people = interactions_repo.attendees(_conn(request), entry.id)
    return {
        "id": entry.id,
        "date": entry.occurred_on,
        "type": _type_label(str(entry.type)),
        "subject": entry.subject,
        # who was in the room. A logged call with nobody on it is half a
        # record, and the attendee list is alive-filtered by the repo, so a
        # removed contact drops off here without touching the interaction.
        "who": ", ".join(f"{c.first_name} {c.last_name}" for c in people),
        # the body was stored and shown NOWHERE before review F33
        "body": entry.body,
        "edit_href": f"/accounts/{ref}/interactions/{entry.id}/edit{query}",
        "delete_href": f"/accounts/{ref}/interactions/{entry.id}/delete{query}",
    }


def _timeline_context(request: Request, org: Org, type_filter: str | None) -> dict[str, Any]:
    conn = _conn(request)
    entries = interactions_repo.for_org(conn, org.id, limit=_TIMELINE_LIMIT)
    shown = [e for e in entries if type_filter is None or str(e.type) == type_filter]
    query = _timeline_query(type_filter)
    base = f"/accounts/{org.ref}/relationship"
    # A pill per type that actually OCCURS here, in the vocabulary's own order
    # — a filter that can only ever return nothing is not a filter.
    present = [t.value for t in InteractionType if any(str(e.type) == t.value for e in entries)]
    if type_filter:
        empty = (
            f"no {_type_label(type_filter)} logged — "
            f"clear the filter to see all {len(entries)}"
        )
    else:
        empty = "empty — add the first row"
    return {
        "timeline_rows": [_timeline_row(request, org.ref, e, query) for e in shown],
        "timeline_count": len(shown),
        "timeline_type": type_filter,
        "timeline_base": base,
        "timeline_query": query,
        "timeline_filters": [
            {"id": t, "label": _type_label(t), "href": f"{base}?{urlencode({'type': t})}"}
            for t in present
        ],
        "timeline_empty": empty,
    }


def _interactions_panel(
    request: Request,
    org: Org,
    type_filter: str | None,
    *,
    oob: bool = False,
    error: str | None = None,
) -> HTMLResponse:
    """`oob=True` renders the panel as an out-of-band swap and NOTHING else —
    _contacts_panel's docstring explains the nesting bug that forces it, and
    the addendum-two half is just as load-bearing: htmx applies out-of-band
    content BEFORE the primary swap, so an error rendered outside the OOB
    element lands in a node the OOB replace has already detached, and shows as
    nothing at all. One element, out of band, error inside it.

    `type_filter` rides along so a panel refresh keeps the filter the user was
    looking at: dropping back to All after every edit would re-hide the row
    they just corrected in a list of two hundred."""
    context = {
        "header": {"org": org}, "oob": oob, "error": error,
        **_timeline_context(request, org, type_filter),
    }
    return TEMPLATES.TemplateResponse(request, "account/_interactions_panel.html", context)


@router.get("/accounts/{ref}/relationship", response_class=HTMLResponse)
def relationship_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    context = _context(conn, org, "relationship", request)
    context.update(_contacts_context(request, org))
    context.update(_timeline_context(request, org, _filter_type(request.query_params)))
    context["oob"] = False  # a full tab-page render is never an OOB swap
    return TEMPLATES.TemplateResponse(request, "account/relationship.html", context)


@router.get("/accounts/{ref}/interactions/{interaction_id}/edit", response_class=HTMLResponse)
def interaction_edit_form(request: Request, ref: str, interaction_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned(conn, org, "interaction", interaction_id, interactions_repo.get)
    spec = interaction_form(existing)
    query = _timeline_query(_filter_type(request.query_params))
    action = f"/accounts/{ref}/interactions/{interaction_id}/edit{query}"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/interactions/{interaction_id}/edit", response_class=HTMLResponse)
async def interaction_update(request: Request, ref: str, interaction_id: str) -> HTMLResponse:
    """One writer action, one batch — through the shared `_save`, so a refused
    save re-renders the form with the input intact and writes nothing (the
    exception propagates out of open_batch and the transaction rolls back).

    A bare number is refused as a date here exactly as in the TUI: the parser
    is forms.spec's, the sentence is forms.spec.date_refusal's, and the refusal
    comes back as a 200 with the form in it — htmx swaps neither 4xx nor 5xx,
    so a refusal answered with an HTTPException renders as silence."""
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned(conn, org, "interaction", interaction_id, interactions_repo.get)
    type_filter = _filter_type(request.query_params)
    spec = interaction_form(existing)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/interactions/{interaction_id}/edit{_timeline_query(type_filter)}"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_interaction(conn, values, existing),
    )
    return refused or _interactions_panel(request, org, type_filter, oob=True)


@router.get("/accounts/{ref}/interactions/{interaction_id}/delete", response_class=HTMLResponse)
def interaction_delete_confirm(request: Request, ref: str, interaction_id: str) -> HTMLResponse:
    """The confirm step. Writes nothing, and refuses IN THE PAGE.

    Ownership is checked against the RAW row for the reason the contact
    removal's is: this control is the one a stale tab clicks — two tabs open,
    or a TUI/MCP delete while the timeline is on screen — and `_owned` is
    alive-filtered, so it would answer that with a flat 404 that htmx does not
    swap: no modal, no message, no change. Unknown and foreign ids keep their
    404; those urls were never rendered by this page."""
    org = _org(request, ref)
    conn = _conn(request)
    _owns_raw_row(conn, org, "interaction", interaction_id)
    type_filter = _filter_type(request.query_params)
    gone = interactions_svc.already_deleted(conn, interaction_id)
    if gone is not None:
        return _interactions_panel(request, org, type_filter, oob=True, error=gone)
    return TEMPLATES.TemplateResponse(
        request,
        "account/_interaction_confirm_delete.html",
        {
            "org": org,
            "interaction": interactions_repo.get(conn, interaction_id),
            "notes": interactions_svc.consequences(conn, interaction_id),
            "timeline_query": _timeline_query(type_filter),
        },
    )


@router.post("/accounts/{ref}/interactions/{interaction_id}/delete", response_class=HTMLResponse)
def interaction_delete(request: Request, ref: str, interaction_id: str) -> HTMLResponse:
    """The confirmed delete — SOFT, and one revertible batch, both owned by
    services.interactions.delete so the TUI's `D` and this button cannot differ
    on either. `def`, not `async def`: nothing may await inside a batch
    (tests/test_conventions.py), so this belongs on the threadpool.

    "Already deleted" comes back as the refreshed panel carrying the service's
    own sentence — a 200 with the truth on screen, nothing written — because a
    double-submitted confirm is the likeliest way to reach it and htmx would
    drop a 4xx in silence."""
    org = _org(request, ref)
    conn = _conn(request)
    _owns_raw_row(conn, org, "interaction", interaction_id)
    type_filter = _filter_type(request.query_params)
    try:
        interactions_svc.delete(conn, interaction_id, source="web")
    except ValueError as exc:
        return _interactions_panel(request, org, type_filter, error=str(exc))
    return _interactions_panel(request, org, type_filter)


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


# --- removing a contact (2026-08-18) ----------------------------------------
# GET renders the confirm and writes NOTHING; POST removes. Two routes, not one
# hx-confirm: this is destructive and one click away from a card the user is
# already editing in place, and a browser confirm() shows no plan — the same
# objection the revert control's ledger entry records (web/parity.py).


@router.get("/accounts/{ref}/contacts/{contact_id}/remove", response_class=HTMLResponse)
def contact_remove_confirm(request: Request, ref: str, contact_id: str) -> HTMLResponse:
    """The confirm step. Writes nothing, and refuses in the page.

    Ownership is checked against the RAW row, and liveness separately, for the
    reason the POST does it (fix round 2): the stale tab this whole control
    anticipates — two tabs open, or a TUI/MCP removal while a card is on screen
    — hits THIS GET first, and `_owned` answered it with a bare 404. htmx does
    not swap 4xx and nothing listens for htmx:responseError, so the Remove
    button produced no swap, no message and no change at all. Unknown and
    foreign ids keep their 404: those urls were never rendered by this page.

    The refusal is the refreshed panel and NOTHING else, carrying the sentence
    the POST refuses with. It cannot be the primary swap — the button targets
    `next .form-host` with innerHTML, so returning the panel there nests a
    second panel inside the first, the trap `contact_create` solved with
    `oob=True`. And the sentence cannot ride OUTSIDE the OOB element either:
    htmx swaps out-of-band content BEFORE the primary swap, so by the time the
    primary swap lands, `next .form-host` is a child of the panel this response
    has already replaced — detached, invisible, the same nothing again. One
    element, out of band, error inside it.

    The ownership guard is also what makes the confirm's OWN two sentences
    agree: the header names `org` and the consequences name the contact's
    account, and before it those could be two different accounts (fix round 1).
    """
    org = _org(request, ref)
    conn = _conn(request)
    _owns_contact_row(conn, org, contact_id)
    gone = contacts_svc.already_removed(conn, contact_id)
    if gone is not None:
        return _contacts_panel(request, org, oob=True, error=gone)
    contact = contacts_repo.get(conn, contact_id)
    notes = contacts_svc.consequences(conn, contact_id)
    return TEMPLATES.TemplateResponse(
        request,
        "account/_contact_confirm_remove.html",
        {"org": org, "contact": contact, "notes": notes},
    )


@router.post("/accounts/{ref}/contacts/{contact_id}/remove", response_class=HTMLResponse)
def contact_remove(request: Request, ref: str, contact_id: str) -> HTMLResponse:
    """The confirmed removal. `def`, not `async def`: the write runs inside
    services.contacts.remove's batch and nothing may await in there
    (tests/test_conventions.py), so this belongs on the threadpool with the
    other sync routes.

    A refusal SAYS SOMETHING, and here it has to say it in the page: htmx does
    not swap 4xx by default and nothing listens for htmx:responseError, so the
    404 this used to raise for "already removed" — the case its own docstring
    anticipated, and what a double-clicked confirm produces — rendered no swap,
    no message, nothing at all, on a destructive control (fix round 1). The
    refusal now comes back as the refreshed panel carrying the service's own
    sentence, the same shape `_save` uses for a refused form: 200, the truth on
    screen, nothing written.

    Ownership is checked against the RAW row (`_owns_contact_row`) so that
    "already removed" survives as the answer — the alive-filtered guard would
    have turned it back into "no contact"."""
    org = _org(request, ref)
    _owns_contact_row(_conn(request), org, contact_id)
    try:
        contacts_svc.remove(_conn(request), contact_id, source="web")
    except ValueError as exc:
        return _contacts_panel(request, org, error=str(exc))
    return _contacts_panel(request, org)


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
    org = _org(request, ref)
    _owned(_conn(request), org, "contact", contact_id, contacts_repo.get)
    return _contact_display_cell(request, ref, contact_id, key)


@router.get("/accounts/{ref}/contacts/{contact_id}/cell/{key}/edit", response_class=HTMLResponse)
def contact_cell_edit(request: Request, ref: str, contact_id: str, key: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "contact", contact_id, contacts_repo.get)
    return _contact_editor_cell(request, ref, contact_id, key)


@router.post("/accounts/{ref}/contacts/{contact_id}/cell/{key}", response_class=HTMLResponse)
async def contact_cell_save(request: Request, ref: str, contact_id: str, key: str) -> HTMLResponse:
    """One field, one writer action, one batch. The contact id is in the
    URL — never the row position, because a refresh can reorder rows
    mid-edit (inline_edit.py's row_key-captured-at-open rule, mirrored
    here: the cell action URL is baked in when the row renders)."""
    org = _org(request, ref)
    conn = _conn(request)
    existing = _owned(conn, org, "contact", contact_id, contacts_repo.get)
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
