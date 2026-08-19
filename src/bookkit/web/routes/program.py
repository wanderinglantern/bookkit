"""The Program tab: placements, their layers, and the markets on them.

Registered BEFORE account.router in app.py. This module's
GET /accounts/{ref}/program and account.py's generic GET /accounts/{ref}/{tab}
match the same two-segment path, and Starlette resolves across routers by
registration order rather than by specificity — the same trap
routes/relationship.py already carries a comment about.

Phase 1 of docs/superpowers/plans/2026-08-19-programs-on-the-web.md reads
only. When the writes land here they go through services.program_files, the
same batched, snapshot-taking wrapper the MCP server uses: a direct sync.*
call from a route would write outside a batch and leave no pre-image, which is
the one thing that makes a program write unrevertible.

What this replaces printed "empty — add the first row" unconditionally while
the tab badge counted the placements it was claiming did not exist.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ... import sync
from ...forms.inline import LAYER_FIELDS, PARTICIPANT_FIELDS
from ...forms.spec import Field, initial_text, parse_value
from ...repo import placements as placements_repo
from ...services import program_files
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display
from .account import _conn, _context, _org, _owned, layers_for

_LAYER_CELLS: dict[str, Field] = {f.key: f for f in LAYER_FIELDS}

# The column class a layer cell carries, in ONE place. Three literals — the
# panel's first render, the display route htmx swaps back after a save, and the
# editor — is how a cell loses its formatting the moment it is edited and the
# column changes shape mid-session (fixed on the request items table,
# 2026-08-19).
_LAYER_CELL_CLASS: dict[str, str] = {
    "name": "prose",
    "attach_cents": "num",
    "limit_cents": "num",
    "premium_cents": "num",
    "period_from": "num",
    "period_to": "num",
}

router = APIRouter()


def _layer_row(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any]
) -> dict[str, Any]:
    """One layer, ready to render — the editable fields as CELLS, the derived
    ones as plain text.

    MONEY IS SHOWN EXACT, not compact. A cell is one string for both the
    display and the editor's pre-fill, and "$50M" parses back as $50,000,000 —
    so a layer at $50,123,456 would lose $123,456 the first time anybody
    clicked its cell and pressed Enter without typing. That is the same class
    of bug as pre-filling a value the parser refuses (CLAUDE.md, the cents
    rule): the difference is that this one saves successfully and destroys the
    number quietly. Scannability loses to precision on an editable money
    column.

    `signed_pct` and `statutory` stay plain: both are derived — signed is the
    sum of the participants' shares — and a cell offering to edit a derived
    value writes nothing and reads as broken.
    """
    def cell(key: str) -> str:
        field = _LAYER_CELLS[key]
        return render_cell_display(
            request, field, initial_text(field, layer.get(key)),
            _layer_cell_action(ref, placement_id, layer["id"], key),
            extra_class=_LAYER_CELL_CLASS.get(key, ""),
        )

    return {
        "id": layer["id"],
        "name": layer["name"],
        "cells": {key: cell(key) for key in _LAYER_CELLS},
        # A statutory layer carries benefits and NO dollar limit: towerkit
        # forces limit == 0 and draws it off-scale. Printing "$0" would render
        # unlimited statutory cover as cover worth nothing — opposite facts
        # that arithmetic alone makes identical. So the limit CELL is replaced
        # by the word, and the layer is not editable into or out of statutory
        # from here.
        "statutory": layer["statutory"],
        "signed_pct": layer["signed_pct"],
        "participants": layer["participants"],
    }


def _programs(request: Request, org: Any) -> list[dict[str, Any]]:
    """Every placement on the account, each with its layers.

    Through account.layers_for, which memoises per request: the shell has
    already read the RENEWAL placement's file for the right rail, and this tab
    wants that same file plus every other placement's. Calling layer_details
    directly here parsed one file twice per render — caught by
    test_layer_details_is_read_once_per_page, which was right and whose
    assertion I had talked myself out of in a comment before running it.
    """
    conn = _conn(request)
    out: list[dict[str, Any]] = []
    for placement in placements_repo.for_org(conn, org.id):
        layers = layers_for(request, conn, placement.id) if placement.program_path else []
        out.append(
            {
                "placement": placement,
                "linked": bool(placement.program_path),
                "layers": [
                    _layer_row(request, org.ref, placement.id, layer) for layer in layers
                ],
            }
        )
    return out


@router.get("/accounts/{ref}/program", response_class=HTMLResponse)
def program_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    context = _context(conn, org, "program", request)
    context["programs"] = _programs(request, org)
    return TEMPLATES.TemplateResponse(request, "account/program.html", context)


# --- editing a layer where it is read -----------------------------------------
#
# The inline-cell contract, third table to use it: GET .../cell/{key},
# GET .../cell/{key}/edit, POST .../cell/{key}. The only difference from tasks
# and request items is what sits behind the save — services.program_files.write
# instead of a repo call, because the row being edited lives in a towerkit file
# rather than a column.


def _open_batch_web(conn: sqlite3.Connection, **kwargs: Any) -> Any:
    """This surface's stamp on the shared write seam. The tool names are the
    MCP server's own, so the changes list reads the same whichever surface made
    the edit."""
    from ...services import batches as batches_svc

    return batches_svc.open_batch(conn, source="web", **kwargs)


def _owned_layer(
    request: Request, org: Any, placement_id: str, layer_id: str
) -> tuple[Any, dict[str, Any]]:
    """A layer is reached through TWO ids, so both are checked: the placement
    is this account's, and the layer is that placement's file's. Without the
    second, a layer could be edited under a placement it does not belong to —
    and the row that came back would belong to a program the write never
    touched."""
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    for layer in layers_for(request, conn, placement_id):
        if layer["id"] == layer_id:
            return placement, layer
    raise HTTPException(status_code=404, detail=f"layer {layer_id} is not on {placement.ref}")


def _text(response: Any) -> str:
    """A rendered response's body as text. Starlette types `.body` as
    bytes | memoryview, and only one of those decodes."""
    body = response.body
    return bytes(body).decode()


def _layer_field(key: str) -> Field:
    """Only what LAYER_FIELDS declares. signed_pct and statutory are derived,
    and an editor for a derived value writes nothing and reads as broken."""
    field = _LAYER_CELLS.get(key)
    if field is None:
        raise HTTPException(status_code=404, detail=f"{key} is not an editable layer field")
    return field


def _layer_cell_action(ref: str, placement_id: str, layer_id: str, key: str) -> str:
    return f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}/cell/{key}"


def _layer_display_cell(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any], key: str
) -> HTMLResponse:
    field = _layer_field(key)
    action = _layer_cell_action(ref, placement_id, layer["id"], key)
    return HTMLResponse(
        render_cell_display(
            request, field, initial_text(field, layer.get(key)), action,
            extra_class=_LAYER_CELL_CLASS.get(key, ""),
        )
    )


def _layer_editor_cell(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any], key: str,
    error: str | None = None, typed: str | None = None,
) -> HTMLResponse:
    field = _layer_field(key)
    value = typed if typed is not None else initial_text(field, layer.get(key))
    action = _layer_cell_action(ref, placement_id, layer["id"], key)
    return HTMLResponse(
        render_cell(
            request, field, value, action, error=error,
            extra_class=_LAYER_CELL_CLASS.get(key, ""),
        )
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/cell/{key}",
    response_class=HTMLResponse,
)
def layer_cell(
    request: Request, ref: str, placement_id: str, layer_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    return _layer_display_cell(request, ref, placement_id, layer, key)


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/cell/{key}/edit",
    response_class=HTMLResponse,
)
def layer_cell_edit(
    request: Request, ref: str, placement_id: str, layer_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    return _layer_editor_cell(request, ref, placement_id, layer, key)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/cell/{key}",
    response_class=HTMLResponse,
)
async def layer_cell_save(
    request: Request, ref: str, placement_id: str, layer_id: str, key: str
) -> HTMLResponse:
    """One field, one write-through, one batch, one snapshot.

    LAYER_FIELDS' keys are sync.update_layer's own keyword names, so the value
    goes straight through with no translation table to drift.

    A conflict arrives here as an ordinary refusal for now — the file moved
    under this write and the message says so. The three-way Reload / Overwrite
    / Keep editing is phase 5, deliberately: it deserves its own review rather
    than riding in on a phase this size.
    """
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    field = _layer_field(key)
    raw = str((await request.form()).get(key, ""))

    try:
        value = parse_value(field, raw)
    except ValueError as exc:
        return _layer_editor_cell(request, ref, placement_id, layer, key, str(exc), raw)
    if field.required and value in (None, ""):
        return _layer_editor_cell(
            request, ref, placement_id, layer, key, f"{field.label} is required", raw
        )

    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=f"set {field.label} on {layer['name']}",
            mutate=lambda: sync.update_layer(conn, placement.id, layer_id, **{key: value}),
            open_batch=_open_batch_web,
        )
    except Exception as exc:  # a refused write is a message, never a 500
        return _layer_editor_cell(request, ref, placement_id, layer, key, str(exc), raw)

    # Re-read rather than reusing `layer`: the memo is per REQUEST and this
    # one has just written, so the cached parse is now the pre-image.
    request.state.layer_details = {}
    _, fresh = _owned_layer(request, org, placement_id, layer_id)

    # THE CELL, PLUS THE WHOLE PANEL OUT OF BAND. A layer write can move rows
    # this cell knows nothing about: write_through runs heal_follows, which
    # re-seats the attachment of every follows-underlying layer above the one
    # edited. Swapping back only the edited cell left those rows showing the
    # pre-write attachment — a tower with a gap or an overlap that does not
    # exist in the file, and the next edit made from that row would be made
    # against a number that is already gone (found by review, 2026-08-19).
    cell = _layer_display_cell(request, ref, placement_id, fresh, key)
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(cell) + _text(panel))


# --- adding a layer, and working the markets on one ---------------------------
#
# Creating a row does not fit the inline-cell contract — there is no existing
# cell to click into — so these are forms posting into the panel, the same
# pattern contacts and request items already use for their adds.
#
# A market is addressed by its INDEX within its layer, not by its carrier name.
# A name is the thing being edited (a market can be corrected to its right
# name), and carrier names carry spaces and slashes that would have to survive
# a URL. Index is also what towerkit's own editor uses for retentions and
# sublimits. Every write re-renders the whole panel, so an index is never
# stale by the time it is used.


def _layer_add_fields() -> tuple[Field, ...]:
    """A new layer's facts. `name`, `attach` and `limit` are required by
    sync.add_layer; premium is optional because a layer is routinely placed
    before it is priced."""
    return (
        _LAYER_CELLS["name"],
        _LAYER_CELLS["attach_cents"],
        _LAYER_CELLS["limit_cents"],
        _LAYER_CELLS["premium_cents"],
    )


def _parsed(fields: tuple[Field, ...], raw: dict[str, str]) -> dict[str, Any]:
    """Parse a whole small form, refusing on the first bad value. Mirrors
    forms.spec.parse_values without the FormSpec wrapper, since these forms are
    field tuples rather than whole-record specs (D7: this sub-project adds no
    FormSpec builders)."""
    values: dict[str, Any] = {}
    for field in fields:
        value = parse_value(field, raw.get(field.key))
        if field.required and value in (None, ""):
            raise ValueError(f"{field.label} is required")
        values[field.key] = value
    return values


def _panel(request: Request, ref: str, org: Any, placement_id: str) -> HTMLResponse:
    """Re-render this placement's whole panel. A program write can move more
    than the cell that caused it — a market's share changes the layer's signed
    percentage, and adding a layer changes the table — so no single-cell swap
    is honest here."""
    request.state.layer_details = {}
    conn = _conn(request)
    placement = placements_repo.get(conn, placement_id)
    return TEMPLATES.TemplateResponse(
        request, "account/_layers_panel.html",
        {
            "header": {"org": org},
            "placement": placement,
            "linked": bool(placement.program_path),
            "layers": [
                _layer_row(request, ref, placement_id, layer)
                for layer in layers_for(request, conn, placement_id)
            ],
            "oob": True,
        },
    )


def _refused_form(
    request: Request, fields: tuple[Field, ...], action: str, title: str,
    message: str, typed: dict[str, str],
) -> HTMLResponse:
    """The form again, with the message and everything that was typed.

    COMMIT IN PLACE, the platform default since 2026-08-12: a refused save
    keeps the form open with its input intact. Returning a bare message
    instead threw away what the broker had entered — and, because the control
    that triggered it swapped the whole panel, took the panel with it."""
    return TEMPLATES.TemplateResponse(
        request, "account/_program_form.html",
        {
            "fields": fields, "action": action, "title": title,
            "error": message, "values": typed,
        },
    )


def _refusal(request: Request, message: str) -> HTMLResponse:
    """A refused write with no form behind it — the market remove button.

    Deliberately 200: htmx swaps 2xx only, and a refusal the user cannot read
    is the silent failure this codebase keeps finding. It lands in the
    placement's .form-host, never over the panel."""
    return TEMPLATES.TemplateResponse(
        request, "account/_program_refusal.html", {"message": message}
    )


@router.post("/accounts/{ref}/program/{placement_id}/layers", response_class=HTMLResponse)
async def layer_add(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/program/{placement_id}/layers"
    fields = _layer_add_fields()
    try:
        values = _parsed(fields, raw)
    except ValueError as exc:
        return _refused_form(request, fields, action, "new layer", str(exc), raw)

    try:
        program_files.write(
            conn, placement,
            tool="program_layer_add",
            summary=f"added layer {values['name']}",
            mutate=lambda: sync.add_layer(
                conn, placement_id, values["name"], [],
                attach_cents=values["attach_cents"],
                limit_cents=values["limit_cents"],
                premium_cents=values["premium_cents"],
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _refused_form(request, fields, action, "new layer", str(exc), raw)
    return _panel(request, ref, org, placement_id)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets",
    response_class=HTMLResponse,
)
async def market_add(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets"
    title = f"bind a market to {layer['name']}"
    try:
        values = _parsed(PARTICIPANT_FIELDS, raw)
    except ValueError as exc:
        return _refused_form(request, PARTICIPANT_FIELDS, action, title, str(exc), raw)

    try:
        program_files.write(
            conn, placement,
            tool="program_bind",
            summary=f"{values['carrier']} on {layer['name']}",
            mutate=lambda: sync.add_participant(
                conn, placement_id, layer_id, values["carrier"], values["share_pct"]
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _refused_form(request, PARTICIPANT_FIELDS, action, title, str(exc), raw)
    return _panel(request, ref, org, placement_id)


def _market_field(key: str) -> Field:
    field = next((f for f in PARTICIPANT_FIELDS if f.key == key), None)
    if field is None:
        raise HTTPException(status_code=404, detail=f"{key} is not an editable market field")
    return field


def _seated(layer: dict[str, Any], index: int) -> dict[str, Any]:
    try:
        seat: dict[str, Any] = layer["participants"][index]
    except IndexError:
        raise HTTPException(
            status_code=404, detail=f"no market {index} on {layer['name']}"
        ) from None
    return seat


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}/edit/{key}",
    response_class=HTMLResponse,
)
def market_cell_edit(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int, key: str
) -> HTMLResponse:
    """One market field, in a form. Not the inline-cell macro: a market is
    addressed by its index rather than an id, and its edit re-renders the whole
    panel (a share changes the layer's signed percentage), so it rides the same
    form-host contract the adds do rather than the cell contract."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    field = _market_field(key)
    return TEMPLATES.TemplateResponse(
        request, "account/_program_form.html",
        {
            "fields": (field,),
            "action": (
                f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}"
                f"/markets/{index}/cell/{key}"
            ),
            "title": f"{seat['carrier']} on {layer['name']}",
            "values": {key: initial_text(field, seat.get(key))},
        },
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}/cell/{key}",
    response_class=HTMLResponse,
)
async def market_cell_save(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int, key: str
) -> HTMLResponse:
    """Corrected IN PLACE — never removed and re-added. The two writes are
    separate mutations with a validator run between them, so the intermediate
    state is a layer short of its share and a refusal on the second half
    leaves it that way (sync.update_participant carries the same note)."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    field = _market_field(key)
    raw = str((await request.form()).get(key, ""))
    try:
        value = parse_value(field, raw)
        if field.required and value in (None, ""):
            raise ValueError(f"{field.label} is required")
    except ValueError as exc:
        return _refusal(request, str(exc))

    changes: dict[str, Any] = (
        {"share_bps": value} if key == "share_pct" else {"new_carrier": value}
    )
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=f"corrected {seat['carrier']} on {layer['name']}",
            mutate=lambda: sync.update_participant(
                conn, placement_id, layer_id, seat["carrier"], **changes
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _refusal(request, str(exc))
    return _panel(request, ref, org, placement_id)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}/remove",
    response_class=HTMLResponse,
)
def market_remove(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int
) -> HTMLResponse:
    """The LAYER survives, unplaced. See sync.remove_participant."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=f"took {seat['carrier']} off {layer['name']}",
            mutate=lambda: sync.remove_participant(
                conn, placement_id, layer_id, seat["carrier"]
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _refusal(request, str(exc))
    return _panel(request, ref, org, placement_id)


# --- the two ghost-row forms --------------------------------------------------


def _mini_form(
    request: Request, fields: tuple[Field, ...], action: str, title: str
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_program_form.html",
        {"fields": fields, "action": action, "title": title},
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/new", response_class=HTMLResponse
)
def layer_add_form(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return _mini_form(
        request, _layer_add_fields(),
        f"/accounts/{ref}/program/{placement_id}/layers", "new layer",
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/new",
    response_class=HTMLResponse,
)
def market_add_form(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    return _mini_form(
        request, PARTICIPANT_FIELDS,
        f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets",
        f"bind a market to {layer['name']}",
    )
