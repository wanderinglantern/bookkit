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
from ...forms.entities import apply_placement, placement_form
from ...forms.inline import LAYER_FIELDS, PARTICIPANT_FIELDS
from ...forms.spec import Field, initial_text, parse_value
from ...money import format_cents_compact
from ...repo import placements as placements_repo
from ...services import batches as batches_svc
from ...services import program_files
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display, render_form
from .account import _conn, _context, _org, _owned, _save, layers_for

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

    MONEY DISPLAYS COMPACT, EDITS EXACT (D5, 2026-08-19). The display string
    and the editor's pre-fill used to be the same string, which forced exact
    display: "$50M" would have parsed back as $50,000,000 and quietly lost the
    odd dollars of a layer at $50,123,456 on an unedited save. D5 severed the
    two — _display_text renders the compact form the tower drawing above this
    table already uses, and the EDITOR routes keep pre-filling the exact
    figure through initial_text, so the loss scenario cannot occur. Weakening
    the pre-fill back to a compact string reintroduces silent data loss.

    `signed_pct` and `statutory` stay plain: both are derived — signed is the
    sum of the participants' shares — and a cell offering to edit a derived
    value writes nothing and reads as broken.
    """
    def cell(key: str) -> str:
        field = _LAYER_CELLS[key]
        return render_cell_display(
            request, field, _display_text(field, layer.get(key)),
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


def _tower_for(placement: Any) -> dict[str, Any] | None:
    """The drawn tower for a placement's linked file, or None.

    None and an empty tower are different facts — "no program file" against "a
    program with nothing in it" — and the template says different things about
    them. A file that will not load is also None rather than an exception: the
    layers table above it still renders from the projection, and a tab that
    500s because one drawing failed is worse than a tab with one drawing
    missing.
    """
    if not placement.program_path:
        return None
    from pathlib import Path

    from towerkit.model import load_program

    from ..tower import panel

    try:
        return panel(load_program(Path(placement.program_path)))
    except Exception:
        return None


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
                "tower": _tower_for(placement),
            }
        )
    return out


def _programs_panel(
    request: Request, ref: str, org: Any, *, error: str | None = None
) -> HTMLResponse:
    """The whole tab body. Creating a placement and scaffolding a file both
    change the LIST rather than one row of it, so neither can honestly swap a
    single panel."""
    request.state.layer_details = {}
    return TEMPLATES.TemplateResponse(
        request, "account/_programs_panel.html",
        {
            "header": {"org": org}, "error": error,
            "programs": _programs(request, org),
        },
    )


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


def _is_conflict(refused: Any) -> bool:
    """One code, checked exactly. sync._mutate folds WriteConflict into the
    diagnostics as code='conflict'; every other refusal is a value towerkit's
    validator would not accept, which is a different question with a different
    answer."""
    return any(d.code == "conflict" for d in refused.diags.errors)


def _write_layer_field(
    conn: sqlite3.Connection, placement: Any, layer_id: str, key: str,
    value: Any, field: Field, layer: dict[str, Any],
) -> None:
    """The one write, so the save path and the overwrite retry cannot drift
    into doing different things."""
    program_files.write(
        conn, placement,
        tool="program_layer_edit",
        summary=f"set {field.label} on {layer['name']}",
        mutate=lambda: sync.update_layer(conn, placement.id, layer_id, **{key: value}),
        open_batch=_open_batch_web,
    )


def _conflict(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    key: str, typed: str, message: str,
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_layer_conflict.html",
        {
            "action": _layer_cell_action(ref, placement_id, layer["id"], key),
            "field": _layer_field(key),
            "typed": typed,
            "message": message,
            "layer": layer,
        },
    )


def _reproject(conn: sqlite3.Connection, placement: Any) -> None:
    """Catch the recorded sha up with what is on disk NOW.

    Both Reload and Overwrite do this first, and it is not a write to the file
    — it re-reads it and refreshes the proj_* cache, which is exactly what the
    conflict said had gone stale."""
    from pathlib import Path

    sync.project(conn, Path(str(placement.program_path)), placement_id=placement.id)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/cell/{key}/reload",
    response_class=HTMLResponse,
)
def layer_cell_reload(
    request: Request, ref: str, placement_id: str, layer_id: str, key: str
) -> HTMLResponse:
    """THEIRS wins. Re-project, discard the draft, show what the file holds."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, _ = _owned_layer(request, org, placement_id, layer_id)
    _reproject(conn, placement)
    request.state.layer_details = {}
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    cell = _layer_display_cell(request, ref, placement_id, fresh, key)
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(cell) + _text(panel))


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/cell/{key}/overwrite",
    response_class=HTMLResponse,
)
async def layer_cell_overwrite(
    request: Request, ref: str, placement_id: str, layer_id: str, key: str
) -> HTMLResponse:
    """MINE lands on top of theirs — a RETRY, not a force.

    Re-project so the sha check passes, then re-apply the SAME single field.
    write_through loads the file fresh on every call, so whatever else changed
    while this tab was open survives underneath the one value being written.

    Deliberately narrower than towerkit's own TUI offers.
    EditSession.save(force=True) pushes an entire in-memory program, which is
    right there — one long-lived session, "mine is authoritative now" — and
    wrong here, where each POST is one field freshly loaded. Reusing it would
    silently discard a layer somebody else had just added.
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

    _reproject(conn, placement)
    request.state.layer_details = {}
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    try:
        _write_layer_field(conn, placement, layer_id, key, value, field, layer)
    except Exception as exc:
        return _layer_editor_cell(request, ref, placement_id, layer, key, str(exc), raw)

    request.state.layer_details = {}
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    cell = _layer_display_cell(request, ref, placement_id, fresh, key)
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(cell) + _text(panel))


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/cell/{key}/keep",
    response_class=HTMLResponse,
)
async def layer_cell_keep(
    request: Request, ref: str, placement_id: str, layer_id: str, key: str
) -> HTMLResponse:
    """Neither. Put the editor back with what was typed still in it, and the
    message still saying why nothing was written."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    raw = str((await request.form()).get(key, ""))
    return _layer_editor_cell(
        request, ref, placement_id, layer, key,
        "the file moved under this edit — nothing has been written", raw,
    )


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


def _display_text(field: Field, value: Any) -> str:
    """The DISPLAY string for a cell — distinct from the editor's pre-fill on
    purpose (D5): money reads compact here, matching the tower drawing, while
    every editor keeps pre-filling the exact figure via initial_text. One
    string used to serve both, which is why display had to be exact; the
    split is what makes compact display safe."""
    if value is None:
        return ""
    if field.kind == "money":
        return format_cents_compact(int(value))
    return initial_text(field, value)


def _layer_display_cell(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any], key: str
) -> HTMLResponse:
    field = _layer_field(key)
    action = _layer_cell_action(ref, placement_id, layer["id"], key)
    return HTMLResponse(
        render_cell_display(
            request, field, _display_text(field, layer.get(key)), action,
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
        _write_layer_field(conn, placement, layer_id, key, value, field, layer)
    except program_files.ProgramWriteRefused as refused:
        if _is_conflict(refused):
            # NOT an ordinary refusal. The file moved under this write, and
            # answering it with the same one-line message leaves the user
            # retyping into a form that will refuse again.
            return _conflict(request, ref, placement_id, layer, key, raw, str(refused))
        return _layer_editor_cell(request, ref, placement_id, layer, key, str(refused), raw)
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


# --- phase 3: creating a program ----------------------------------------------
#
# Two steps, because they are two facts. A PLACEMENT is the bookkit record of a
# program you are working; a towerkit FILE is the tower's structure. A
# placement can exist for weeks before anyone draws its tower, and scaffolding
# is what turns the second into a thing on disk — which is why it gets a
# confirmation of its own.


@router.get("/accounts/{ref}/program/placements/new", response_class=HTMLResponse)
def placement_new_form(request: Request, ref: str) -> HTMLResponse:
    _org(request, ref)  # the 404 guard; this form needs nothing else off it
    spec = placement_form(conn=_conn(request))
    action = f"/accounts/{ref}/program/placements"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/program/placements", response_class=HTMLResponse)
async def placement_create(request: Request, ref: str) -> HTMLResponse:
    """The whole-record form seam, unchanged — placement_form already existed
    for the TUI, so this adds no builder (spec D7: this sub-project adds none).
    A refusal re-renders the form with the input intact, via _save."""
    org = _org(request, ref)
    conn = _conn(request)
    spec = placement_form(conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/program/placements"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_placement(conn, values, org.id),
    )
    return refused or _programs_panel(request, ref, org)


def _scaffold_destination(conn: Any, org: Any, placement: Any) -> Any:
    """Where a new program file goes, by the same rule the TUI's `t` uses —
    first configured root, `<two-word-slug>-<period year>.json`. Mirrored
    rather than reinvented so a file scaffolded from either surface lands in
    the same place with the same name."""
    from pathlib import Path

    roots = sync.configured_roots(conn)
    if not roots:
        return None
    slug = "-".join(org.name.lower().split()[:2]).strip(",.")
    year = placement.period_from[:4]
    return Path(roots[0]) / f"{slug}-{year}.json"


@router.get("/accounts/{ref}/program/{placement_id}/scaffold", response_class=HTMLResponse)
def scaffold_confirm(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """The confirm step. Writes NOTHING — and shows the path, because where a
    file lands is the part a person can only check beforehand."""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    return TEMPLATES.TemplateResponse(
        request, "account/_scaffold_confirm.html",
        {
            "header": {"org": org},
            "placement": placement,
            "destination": _scaffold_destination(conn, org, placement),
            "existing": placement.program_path,
        },
    )


@router.post("/accounts/{ref}/program/{placement_id}/scaffold", response_class=HTMLResponse)
def scaffold_create(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """Create the towerkit file and link it.

    Every refusal comes back in the page and NAMES what to do: the file that
    already exists, or the setting that has not been made. A destructive-ish
    control that answers with a status code produces no swap and no message at
    all under htmx."""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)

    if placement.program_path:
        return _refusal(
            request,
            f"{placement.ref} already has a program file: {placement.program_path}. "
            f"Open it in towerkit, or unlink it first.",
        )
    destination = _scaffold_destination(conn, org, placement)
    if destination is None:
        return _refusal(
            request,
            "no program file location is set yet — configure the program roots "
            "first (`,` on Today in the terminal app), then scaffold.",
        )

    try:
        with batches_svc.open_batch(
            conn, source="web", tool="scaffold_tower", org_id=org.id,
            summary=f"scaffolded a program file for {placement.ref}",
        ):
            made, diags = sync.scaffold_program(conn, placement_id, destination)
            if made is None or not diags.ok:
                first = diags.errors[0].message if diags.errors else "unknown error"
                raise ValueError(f"scaffold refused: {first}")
    except Exception as exc:
        return _refusal(request, str(exc))
    return _programs_panel(request, ref, org)
