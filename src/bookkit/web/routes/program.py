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
from ...forms.entities import apply_placement, apply_submission, placement_form, submission_form
from ...forms.inline import LAYER_FIELDS, PARTICIPANT_FIELDS, PLACEMENT_FIELDS
from ...forms.spec import Field, initial_text, parse_value
from ...money import format_cents_compact
from ...repo import placements as placements_repo
from ...repo import vocab
from ...services import batches as batches_svc
from ...services import placement_edit, program_files
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display, render_form
from .account import _conn, _context, _org, _owned, _save, layers_for

_LAYER_CELLS: dict[str, Field] = {f.key: f for f in LAYER_FIELDS}
_PLACEMENT_CELLS: dict[str, Field] = {f.key: f for f in PLACEMENT_FIELDS}

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
        "market_chips": [
            _market_chip_html(request, ref, placement_id, layer, i, seat)
            for i, seat in enumerate(layer["participants"])
        ],
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
                "placement_cells": _placement_cells(request, org.ref, placement),
                "line_chips": _line_chips(request, org.ref, placement),
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


# --- editing the placement's own facts where they are read --------------------
#
# The header's name, period, status and commission are cells (phase 2): the
# web could not edit a placement's own facts at all, while the layer table
# below them edited in place. WHICH OWNER a field writes to — the towerkit
# file or the row — is services.placement_edit's call, not this module's.


def _placement_field(key: str) -> Field:
    field = _PLACEMENT_CELLS.get(key)
    if field is None:
        raise HTTPException(
            status_code=404, detail=f"{key} is not an editable placement field"
        )
    return field


def _placement_cell_action(ref: str, placement_id: str, key: str) -> str:
    return f"/accounts/{ref}/program/{placement_id}/cell/{key}"


def _placement_cell_class(key: str, placement: Any) -> str:
    if key == "status":
        # the pill class the static header used — colour stays signal
        return f"status-{placement.status}"
    if key in ("period_from", "period_to", "commission_bps"):
        return "mono"
    return ""


def _placement_display_cell(
    request: Request, ref: str, placement: Any, key: str
) -> HTMLResponse:
    field = _placement_field(key)
    return HTMLResponse(
        render_cell_display(
            request, field, _display_text(field, getattr(placement, key)),
            _placement_cell_action(ref, placement.id, key),
            tag="span", extra_class=_placement_cell_class(key, placement),
        )
    )


def _placement_editor_cell(
    request: Request, ref: str, placement: Any, key: str,
    error: str | None = None, typed: str | None = None,
) -> HTMLResponse:
    field = _placement_field(key)
    value = typed if typed is not None else initial_text(field, getattr(placement, key))
    return HTMLResponse(
        render_cell(
            request, field, value, _placement_cell_action(ref, placement.id, key),
            error=error, tag="span",
            extra_class=_placement_cell_class(key, placement),
        )
    )


def _placement_cells(request: Request, ref: str, placement: Any) -> dict[str, str]:
    return {
        key: _text(_placement_display_cell(request, ref, placement, key))
        for key in _PLACEMENT_CELLS
    }


@router.get(
    "/accounts/{ref}/program/{placement_id}/cell/{key}", response_class=HTMLResponse
)
def placement_cell(
    request: Request, ref: str, placement_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    placement = _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return _placement_display_cell(request, ref, placement, key)


@router.get(
    "/accounts/{ref}/program/{placement_id}/cell/{key}/edit",
    response_class=HTMLResponse,
)
def placement_cell_edit(
    request: Request, ref: str, placement_id: str, key: str
) -> HTMLResponse:
    org = _org(request, ref)
    placement = _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return _placement_editor_cell(request, ref, placement, key)


@router.post(
    "/accounts/{ref}/program/{placement_id}/cell/{key}", response_class=HTMLResponse
)
async def placement_cell_save(
    request: Request, ref: str, placement_id: str, key: str
) -> HTMLResponse:
    """One header fact, routed to its owner by services.placement_edit.split:
    a file-owned field rides the snapshot seam (one batch, one pre-image),
    a book-owned one is a plain batched row write, and an UNCHANGED value
    writes nothing at all. A write-through conflict answers as a one-line
    refusal here (like market cells; the three-way stays layer-cell-shaped).
    """
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    field = _placement_field(key)
    raw = str((await request.form()).get(key, ""))
    try:
        value = parse_value(field, raw)
        if field.required and value in (None, ""):
            raise ValueError(f"{field.label} is required")
        file_changes, book_changes = placement_edit.split(placement, {key: value})
    except ValueError as exc:
        return _placement_editor_cell(request, ref, placement, key, str(exc), raw)

    try:
        if file_changes:
            program_files.write(
                conn, placement,
                tool="program_edit",
                summary=f"edited {placement.ref}: {field.label}",
                mutate=lambda: placement_edit.write_file_fields(
                    conn, placement, file_changes
                ),
                open_batch=_open_batch_web,
            )
        elif book_changes:
            with _open_batch_web(
                conn, tool="placement_edit", org_id=placement.org_id,
                summary=f"edited {placement.ref}: {field.label}",
            ):
                placement_edit.write_book_fields(conn, placement, book_changes)
    except Exception as exc:
        return _placement_editor_cell(request, ref, placement, key, str(exc), raw)

    request.state.layer_details = {}
    fresh = placements_repo.get(conn, placement_id)
    cell = _placement_display_cell(request, ref, fresh, key)
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(cell) + _text(panel))


# --- the lines strip (phase 3, D1) ---------------------------------------------
#
# Lines are the axis a scaffolded program could never escape from the browser:
# "Coverage TBD" stayed TBD forever (F4). Rename rides the cell contract; the
# ID FOLLOWS THE NAME and cascades through every appliesTo, so every rename
# success answers with the whole panel — the cell's own action URL is stale
# the moment the write lands.

_LINE_NAME_FIELD = Field("name", "line of cover", required=True)


def _line_name(conn: sqlite3.Connection, placement_id: str, line_id: str) -> str:
    for lid, name in sync.program_lines(conn, placement_id):
        if lid == line_id:
            return str(name)
    raise HTTPException(status_code=404, detail=f"no line {line_id!r} on this program")


def _lines_base(ref: str, placement_id: str) -> str:
    return f"/accounts/{ref}/program/{placement_id}/lines"


def _line_cell_action(ref: str, placement_id: str, line_id: str) -> str:
    return f"{_lines_base(ref, placement_id)}/{line_id}/cell/name"


def _line_chip_html(
    request: Request, ref: str, placement_id: str, line_id: str, name: str
) -> str:
    cell = render_cell_display(
        request, _LINE_NAME_FIELD, name,
        _line_cell_action(ref, placement_id, line_id),
        tag="span", extra_class="line-name",
    )
    template = TEMPLATES.env.get_template("account/_line_chip.html")
    return template.render(
        base=f"{_lines_base(ref, placement_id)}/{line_id}", name=name, name_cell=cell
    )


def _line_chips(request: Request, ref: str, placement: Any) -> list[str] | None:
    """None for an unlinked placement — no file, no lines, and the strip
    saying 'no lines' about a file that does not exist would mislead."""
    if not placement.program_path:
        return None
    conn = _conn(request)
    return [
        _line_chip_html(request, ref, placement.id, lid, name)
        for lid, name in sync.program_lines(conn, placement.id)
    ]


def _line_blast(
    conn: sqlite3.Connection, placement_id: str, line_id: str
) -> tuple[list[str], list[str]]:
    """(dying, narrowing): layers covering ONLY this line die with it; layers
    spanning several merely stop covering it."""
    dying: list[str] = []
    narrowing: list[str] = []
    for layer in layers_for_conn(conn, placement_id):
        if line_id in layer["applies_to"]:
            (dying if layer["applies_to"] == [line_id] else narrowing).append(
                str(layer["name"])
            )
    return dying, narrowing


def layers_for_conn(conn: sqlite3.Connection, placement_id: str) -> list[dict[str, Any]]:
    return sync.layer_details(conn, placement_id)


# LITERAL SEGMENTS BEFORE {line_id} — the same registration-order rule the
# markets routes carry.
@router.get("/accounts/{ref}/program/{placement_id}/lines/new", response_class=HTMLResponse)
def line_add_form(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return TEMPLATES.TemplateResponse(
        request, "account/_line_add.html",
        {"lines_base": _lines_base(ref, placement_id)},
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/lines/button", response_class=HTMLResponse
)
def line_add_button(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return TEMPLATES.TemplateResponse(
        request, "account/_line_add_button.html",
        {"lines_base": _lines_base(ref, placement_id)},
    )


@router.post("/accounts/{ref}/program/{placement_id}/lines", response_class=HTMLResponse)
async def line_add(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    name = str((await request.form()).get("name", "")).strip()

    def refused(message: str) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request, "account/_line_add.html",
            {"lines_base": _lines_base(ref, placement_id), "error": message,
             "values": {"name": name}},
        )

    if not name:
        return refused("the line needs a name")
    try:
        program_files.write(
            conn, placement,
            tool="program_line_add",
            summary=f"added line {name}",
            mutate=lambda: sync.add_line(conn, placement_id, name),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return refused(str(exc))
    button = TEMPLATES.TemplateResponse(
        request, "account/_line_add_button.html",
        {"lines_base": _lines_base(ref, placement_id)},
    )
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(button) + _text(panel))


@router.get(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/chip",
    response_class=HTMLResponse,
)
def line_chip(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    """What the remove confirm's [keep] restores."""
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    name = _line_name(conn, placement_id, line_id)
    return HTMLResponse(_line_chip_html(request, ref, placement_id, line_id, name))


@router.get(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/cell/name",
    response_class=HTMLResponse,
)
def line_cell(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    name = _line_name(conn, placement_id, line_id)
    return HTMLResponse(
        render_cell_display(
            request, _LINE_NAME_FIELD, name,
            _line_cell_action(ref, placement_id, line_id),
            tag="span", extra_class="line-name",
        )
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/cell/name/edit",
    response_class=HTMLResponse,
)
def line_cell_edit(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    name = _line_name(conn, placement_id, line_id)
    return HTMLResponse(
        render_cell(
            request, _LINE_NAME_FIELD, name,
            _line_cell_action(ref, placement_id, line_id),
            tag="span", extra_class="line-name",
        )
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/cell/name",
    response_class=HTMLResponse,
)
async def line_cell_save(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    """Rename. Success answers with the PANEL ALONE (htmx lifts the OOB
    section out and swaps the empty remainder over the editor): the id
    follows the name, so a returned display cell would carry a dead action
    URL the moment the write lands."""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    current = _line_name(conn, placement_id, line_id)
    typed = str((await request.form()).get("name", "")).strip()

    def editor(error: str) -> HTMLResponse:
        return HTMLResponse(
            render_cell(
                request, _LINE_NAME_FIELD, typed,
                _line_cell_action(ref, placement_id, line_id),
                error=error, tag="span", extra_class="line-name",
            )
        )

    if not typed:
        return editor("the line needs a name")
    if typed == current:
        return HTMLResponse(
            _line_chip_html(request, ref, placement_id, line_id, current)
        )
    try:
        program_files.write(
            conn, placement,
            tool="program_line_edit",
            summary=f"renamed line {current} to {typed}",
            mutate=lambda: sync.rename_line(conn, placement_id, line_id, typed),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return editor(str(exc))
    request.state.layer_details = {}
    return _panel(request, ref, org, placement_id)


def _line_remove_confirm(
    request: Request, ref: str, placement_id: str, line_id: str, name: str,
    error: str | None = None,
) -> HTMLResponse:
    conn = _conn(request)
    dying, narrowing = _line_blast(conn, placement_id, line_id)
    return TEMPLATES.TemplateResponse(
        request, "account/_line_remove_confirm.html",
        {
            "base": f"{_lines_base(ref, placement_id)}/{line_id}",
            "name": name, "dying": dying, "narrowing": narrowing, "error": error,
        },
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/remove",
    response_class=HTMLResponse,
)
def line_remove_confirm(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    name = _line_name(conn, placement_id, line_id)
    return _line_remove_confirm(request, ref, placement_id, line_id, name)


@router.post(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/remove",
    response_class=HTMLResponse,
)
def line_remove(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    name = _line_name(conn, placement_id, line_id)
    try:
        program_files.write(
            conn, placement,
            tool="program_line_remove",
            summary=f"removed line {name}",
            mutate=lambda: sync.remove_line(conn, placement_id, line_id),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _line_remove_confirm(
            request, ref, placement_id, line_id, name, str(exc)
        )
    request.state.layer_details = {}
    return _panel(request, ref, org, placement_id)


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
            # A detail key's cell is a span; a literal <td> swapped into the
            # details row is parser-dropped and the three-way never appears.
            "tag": _layer_cell_tag(key),
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


# The three long-tail keys live in the DETAILS ROW (a span inside a colspan
# td), not the table proper — their cells must be spans or the parser drops
# the swapped-back <td> outright (no table-row ancestor at the swap point).
_DETAIL_KEYS = frozenset({"policy_number", "period_from", "period_to"})


def _layer_cell_tag(key: str) -> str:
    return "span" if key in _DETAIL_KEYS else "td"


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
            tag=_layer_cell_tag(key),
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
            tag=_layer_cell_tag(key),
            extra_class=_LAYER_CELL_CLASS.get(key, ""),
        )
    )


def _details_row(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    error: str | None = None,
) -> HTMLResponse:
    """The details row's one renderer — the chevron's GET, every structure
    write's success, and every structure refusal all answer with this, so
    the row cannot drift between its three producers."""
    conn = _conn(request)
    layer_id = str(layer["id"])

    def cell(key: str) -> str:
        field = _layer_field(key)
        return render_cell_display(
            request, field, _display_text(field, layer.get(key)),
            _layer_cell_action(ref, placement_id, layer_id, key),
            tag="span", extra_class=_LAYER_CELL_CLASS.get(key, ""),
        )

    base = f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}"
    return TEMPLATES.TemplateResponse(
        request, "account/_layer_details.html",
        {
            "policy_cell": cell("policy_number"),
            "from_cell": cell("period_from"),
            "to_cell": cell("period_to"),
            "base": base,
            "remove_url": f"{base}/remove",
            "lines": [
                {"id": lid, "name": name, "on": lid in layer["applies_to"]}
                for lid, name in sync.program_lines(conn, placement_id)
            ],
            "statutory": layer["statutory"],
            "follows": bool(layer.get("follows_underlying")),
            "error": error,
        },
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/details",
    response_class=HTMLResponse,
)
def layer_details_row(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """The chevron's row: the layer's long tail (policy number, policy dates)
    plus its STRUCTURE — applies-to chips, statutory, follows-underlying —
    the writes that used to live only behind the TUI's `o` into towerkit's
    editor, which a browser does not have (Grant, 2026-08-19)."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    return _details_row(request, ref, placement_id, layer)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/applies-to",
    response_class=HTMLResponse,
)
async def layer_applies_to_toggle(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """Toggle ONE line on the layer's applies-to: the server computes
    current±line and writes the whole set through sync.set_applies_to — its
    first caller ever. A move towerkit refuses (the last line, an overlap, a
    stranded gap) re-renders the row with the message, file untouched."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    line = str((await request.form()).get("line", ""))
    current = list(layer["applies_to"])
    wanted = (
        [lid for lid in current if lid != line]
        if line in current
        else [*current, line]
    )
    if not wanted:
        return _details_row(
            request, ref, placement_id, layer,
            f"{layer['name']} must cover at least one line — add another first",
        )
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=f"rescoped {layer['name']}",
            mutate=lambda: sync.set_applies_to(conn, placement_id, layer_id, wanted),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _details_row(request, ref, placement_id, layer, str(exc))
    request.state.layer_details = {}
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    row = _details_row(request, ref, placement_id, fresh)
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(row) + _text(panel))


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/statutory",
    response_class=HTMLResponse,
)
def statutory_confirm(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """The confirm for MARKING statutory — the write replaces a dollar limit
    with the word, and the figure being given up is the one thing only a
    person can decide to lose. Writes nothing."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    base = f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}"
    return TEMPLATES.TemplateResponse(
        request, "account/_statutory_confirm.html",
        {
            "layer": layer,
            "limit_word": format_cents_compact(int(layer["limit_cents"] or 0)),
            "base": base,
            "details_url": f"{base}/details",
        },
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/statutory",
    response_class=HTMLResponse,
)
async def statutory_save(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    form = await request.form()
    on = str(form.get("statutory", "")) == "true"
    limit_cents: int | None = None
    if not on:
        raw = str(form.get("limit", ""))
        try:
            parsed = parse_value(_LAYER_CELLS["limit_cents"], raw)
            if parsed in (None, ""):
                raise ValueError("leaving statutory needs the dollar limit to restore")
            limit_cents = int(parsed)
        except ValueError as exc:
            return _details_row(request, ref, placement_id, layer, str(exc))
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=(
                f"marked {layer['name']} statutory"
                if on
                else f"{layer['name']} left statutory"
            ),
            mutate=lambda: sync.set_statutory(
                conn, placement_id, layer_id, on, limit_cents=limit_cents
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _details_row(request, ref, placement_id, layer, str(exc))
    request.state.layer_details = {}
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    row = _details_row(request, ref, placement_id, fresh)
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(row) + _text(panel))


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/follows",
    response_class=HTMLResponse,
)
async def follows_save(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """One click either way — turning it on hands the attachment to the tower
    (heal_follows recomputes it on every write), off freezes the last healed
    figure; neither destroys a number a person typed."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    follows = str((await request.form()).get("follows", "")) == "true"
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=(
                f"{layer['name']} now follows underlying"
                if follows
                else f"{layer['name']} attachment frozen"
            ),
            mutate=lambda: sync.set_follows_underlying(
                conn, placement_id, layer_id, follows
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _details_row(request, ref, placement_id, layer, str(exc))
    request.state.layer_details = {}
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    row = _details_row(request, ref, placement_id, fresh)
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(row) + _text(panel))


def _layer_remove_confirm(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    error: str | None = None,
) -> HTMLResponse:
    base = f"/accounts/{ref}/program/{placement_id}/layers/{layer['id']}"
    return TEMPLATES.TemplateResponse(
        request, "account/_layer_remove_confirm.html",
        {
            "layer": layer,
            "seats": [seat["carrier"] for seat in layer["participants"]],
            "remove_url": f"{base}/remove",
            "details_url": f"{base}/details",
            "error": error,
        },
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/remove",
    response_class=HTMLResponse,
)
def layer_remove_confirm(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """Confirm-first (D2), naming the seats that go with the layer. Writes
    nothing."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    return _layer_remove_confirm(request, ref, placement_id, layer)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/remove",
    response_class=HTMLResponse,
)
def layer_remove(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """The layer goes, its seats with it — one batched, snapshotted write
    (sync.remove_layer, D2). A refusal — towerkit will not strand the layer
    above over a gap — re-renders the confirm in place with the message."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_remove",
            summary=f"removed layer {layer['name']}",
            mutate=lambda: sync.remove_layer(conn, placement_id, layer_id),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _layer_remove_confirm(request, ref, placement_id, layer, str(exc))
    return _panel(request, ref, org, placement_id)


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


def _layer_add_fields(
    conn: sqlite3.Connection, placement_id: str
) -> tuple[Field, ...]:
    """A new layer's facts. `name`, `attach` and `limit` are required by
    sync.add_layer; premium is optional because a layer is routinely placed
    before it is priced.

    `line` is REQUIRED and asked, never guessed (F5): this form used to pass
    line_ids=[] and towerkit silently defaulted the new layer onto the FIRST
    line — on a multi-line program the web wrote different data than the TUI
    for the same intent, invisibly. Empty options means the program has no
    lines; the caller refuses before rendering a form that cannot succeed.
    """
    lines = sync.program_lines(conn, placement_id)
    options = tuple((name, line_id) for line_id, name in lines)
    if len(lines) > 1:
        options = (("all lines", "__all__"), *options)
    return (
        _LAYER_CELLS["name"],
        Field("line", "applies to", "select", options, required=True),
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
            "placement_cells": _placement_cells(request, ref, placement),
            "line_chips": _line_chips(request, ref, placement),
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


@router.post("/accounts/{ref}/program/{placement_id}/layers", response_class=HTMLResponse)
async def layer_add(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/program/{placement_id}/layers"
    if not placement.program_path:
        # BEFORE the lines guard: an unlinked placement has no lines either,
        # and "no lines" would send someone to towerkit to edit a file that
        # does not exist.
        return _panel_refusal(
            request, ref, org, placement_id,
            f"{placement.ref} has no program file linked — scaffold one first",
        )
    all_lines = sync.program_lines(conn, placement_id)
    if not all_lines:
        return _panel_refusal(
            request, ref, org, placement_id,
            "the program has no lines — build them in towerkit first",
        )
    fields = _layer_add_fields(conn, placement_id)
    try:
        values = _parsed(fields, raw)
    except ValueError as exc:
        return _refused_form(request, fields, action, "new layer", str(exc), raw)

    line_ids = (
        [line_id for line_id, _ in all_lines]
        if values["line"] == "__all__"
        else [values["line"]]
    )
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_add",
            summary=f"added layer {values['name']}",
            mutate=lambda: sync.add_layer(
                conn, placement_id, values["name"], line_ids,
                attach_cents=values["attach_cents"],
                limit_cents=values["limit_cents"],
                premium_cents=values["premium_cents"],
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _refused_form(request, fields, action, "new layer", str(exc), raw)
    return _panel(request, ref, org, placement_id)


def _market_add_form(
    request: Request, conn: sqlite3.Connection, base: str,
    error: str | None = None, values: dict[str, str] | None = None,
) -> HTMLResponse:
    """The inline add form — and, on a refusal, the same form again with the
    message and everything typed still in it (commit in place, at the anchor
    the user is looking at rather than a form host above the tower)."""
    return TEMPLATES.TemplateResponse(
        request, "account/_market_add.html",
        {"base": base, "fields": _participant_fields(conn), "error": error,
         "values": values},
    )


def _market_add_button(request: Request, base: str) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_market_add_button.html", {"base": base}
    )


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
    base = f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets"
    try:
        values = _parsed(PARTICIPANT_FIELDS, raw)
    except ValueError as exc:
        return _market_add_form(request, conn, base, str(exc), raw)

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
        return _market_add_form(request, conn, base, str(exc), raw)
    button = _market_add_button(request, base)
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(button) + _text(panel))


def _market_field(key: str) -> Field:
    field = next((f for f in PARTICIPANT_FIELDS if f.key == key), None)
    if field is None:
        raise HTTPException(status_code=404, detail=f"{key} is not an editable market field")
    return field


def _participant_fields(conn: sqlite3.Connection) -> tuple[Field, ...]:
    """PARTICIPANT_FIELDS with the carrier completing from existing market
    names — the add form's copy of the rule _market_field_for_editor applies
    to the carrier cell."""
    return tuple(_market_field_for_editor(conn, f.key) for f in PARTICIPANT_FIELDS)


def _market_field_for_editor(conn: sqlite3.Connection, key: str) -> Field:
    """The editor's copy of a market field: the carrier input completes from
    the book's existing market names (Field.suggestions -> datalist), the
    same vocabulary rule the TUI's forms follow — freehand carrier spelling
    is how 'Zurich Insurance Group' vs 'Zurich' drift starts."""
    import dataclasses

    field = _market_field(key)
    if key == "carrier":
        return dataclasses.replace(field, suggestions=tuple(vocab.market_names(conn)))
    return field


def _market_base(ref: str, placement_id: str, layer_id: str, index: int) -> str:
    return f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}"


def _market_cell_action(
    ref: str, placement_id: str, layer_id: str, index: int, key: str
) -> str:
    return _market_base(ref, placement_id, layer_id, index) + f"/cell/{key}"


def _market_display_value(key: str, seat: dict[str, Any]) -> str:
    """What the chip SHOWS. The share prints with its % because the cell sits
    beside a Signed column that does too — a bare number would read as money."""
    if key == "carrier":
        return str(seat["carrier"])
    return f"{seat['share_pct']:g}%"


def _market_prefill(key: str, seat: dict[str, Any]) -> str:
    """What the EDITOR pre-fills. The seat carries share_pct as a PERCENT and
    the share parser reads a percent, so the number passes through verbatim.
    The old mini-form fed this percent into initial_text, whose share kind
    formats BPS — a 40% seat pre-filled '0.4', and an unedited save would
    have cut the share 100x. Never route a percent through a bps formatter."""
    if key == "carrier":
        return str(seat["carrier"])
    return f"{seat['share_pct']:g}"


_MARKET_CELL_CLASS = {"carrier": "market-cell", "share_pct": "market-cell market-share"}


def _market_chip_html(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    index: int, seat: dict[str, Any],
) -> str:
    """One market as a chip of two inline cells plus its remove control — the
    same editing grammar as the layer cells beside it (F1)."""
    def cell(key: str) -> str:
        return render_cell_display(
            request, _market_field(key), _market_display_value(key, seat),
            _market_cell_action(ref, placement_id, layer["id"], index, key),
            tag="span", extra_class=_MARKET_CELL_CLASS[key],
        )

    template = TEMPLATES.env.get_template("account/_market_chip.html")
    return template.render(
        base=_market_base(ref, placement_id, layer["id"], index),
        seat=seat, layer_name=layer["name"],
        carrier_cell=cell("carrier"), share_cell=cell("share_pct"),
    )


def _market_display_cell(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    index: int, seat: dict[str, Any], key: str,
) -> HTMLResponse:
    return HTMLResponse(
        render_cell_display(
            request, _market_field(key), _market_display_value(key, seat),
            _market_cell_action(ref, placement_id, layer["id"], index, key),
            tag="span", extra_class=_MARKET_CELL_CLASS[key],
        )
    )


def _market_editor_cell(
    request: Request, conn: sqlite3.Connection, ref: str, placement_id: str,
    layer: dict[str, Any], index: int, seat: dict[str, Any], key: str,
    error: str | None = None, typed: str | None = None,
) -> HTMLResponse:
    field = _market_field_for_editor(conn, key)
    value = typed if typed is not None else _market_prefill(key, seat)
    return HTMLResponse(
        render_cell(
            request, field, value,
            _market_cell_action(ref, placement_id, layer["id"], index, key),
            error=error, tag="span", extra_class=_MARKET_CELL_CLASS[key],
        )
    )


def _seated(layer: dict[str, Any], index: int) -> dict[str, Any]:
    try:
        seat: dict[str, Any] = layer["participants"][index]
    except IndexError:
        raise HTTPException(
            status_code=404, detail=f"no market {index} on {layer['name']}"
        ) from None
    return seat


# LITERAL SEGMENTS BEFORE {index}: Starlette resolves in registration order,
# so /markets/new and /markets/button must be registered before
# /markets/{index} or the int coercion answers them with a 422 — the same
# registration-order trap this module's docstring records for the tab route.
@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/new",
    response_class=HTMLResponse,
)
def market_add_form(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned_layer(request, org, placement_id, layer_id)
    return _market_add_form(
        request, conn,
        f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets",
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/button",
    response_class=HTMLResponse,
)
def market_add_button(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """What the inline form's cancel restores."""
    org = _org(request, ref)
    _owned_layer(request, org, placement_id, layer_id)
    return _market_add_button(
        request,
        f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets",
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}",
    response_class=HTMLResponse,
)
def market_chip(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int
) -> HTMLResponse:
    """The whole chip — what the remove confirm's [keep] restores."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    return HTMLResponse(_market_chip_html(request, ref, placement_id, layer, index, seat))


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}/cell/{key}",
    response_class=HTMLResponse,
)
def market_cell(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int, key: str
) -> HTMLResponse:
    """The display half of the contract — also what Escape and blur revert to."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    return _market_display_cell(request, ref, placement_id, layer, index, seat, key)


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}/cell/{key}/edit",
    response_class=HTMLResponse,
)
def market_cell_edit(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int, key: str
) -> HTMLResponse:
    """Markets ride the SAME inline-cell contract as the layer cells beside
    them (F1, 2026-08-19). A market is addressed by its index within its
    layer — an id would have to be minted for a (carrier, share) pair the
    file stores as a list entry — and every write re-renders the whole panel,
    so an index is never stale by the time it is used."""
    org = _org(request, ref)
    conn = _conn(request)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    return _market_editor_cell(request, conn, ref, placement_id, layer, index, seat, key)


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
    leaves it that way (sync.update_participant carries the same note).

    Cell grammar throughout: a refusal (bad value, towerkit no, or the file
    moved) re-renders the EDITOR with the message and the typed value —
    never a fragment somewhere else on the page. A conflict gets the same
    one-line treatment rather than the layer cells' three-way for now: the
    three-way's forms are built around a layer id and re-deriving them for
    an index-addressed seat is its own reviewed change, not a rider."""
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
        return _market_editor_cell(
            request, conn, ref, placement_id, layer, index, seat, key, str(exc), raw
        )

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
        return _market_editor_cell(
            request, conn, ref, placement_id, layer, index, seat, key, str(exc), raw
        )

    # Re-read: the memo is per request and this one has just written.
    request.state.layer_details = {}
    _, fresh_layer = _owned_layer(request, org, placement_id, layer_id)
    fresh_seat = _seated(fresh_layer, index)
    cell = _market_display_cell(
        request, ref, placement_id, fresh_layer, index, fresh_seat, key
    )
    panel = _panel(request, ref, org, placement_id)
    return HTMLResponse(_text(cell) + _text(panel))


def _market_confirm(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    index: int, seat: dict[str, Any], error: str | None = None,
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_market_confirm.html",
        {
            "base": _market_base(ref, placement_id, layer["id"], index),
            "seat": seat, "layer_name": layer["name"], "error": error,
        },
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}/remove",
    response_class=HTMLResponse,
)
def market_remove_confirm(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int
) -> HTMLResponse:
    """The confirm, IN PLACE over the chip. Writes nothing — contacts and
    interactions already ask before a removal, and a market seat is the same
    severity; a one-click file write with no question was the odd one out."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    return _market_confirm(request, ref, placement_id, layer, index, seat)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}/remove",
    response_class=HTMLResponse,
)
def market_remove(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int
) -> HTMLResponse:
    """The LAYER survives, unplaced. See sync.remove_participant.

    Success returns the panel alone: htmx lifts the hx-swap-oob section out,
    the (empty) remainder lands where the chip was, and the OOB panel then
    replaces the whole section — the same one-write-one-panel shape every
    other market response has. A refusal re-renders the confirm with the
    message, still in place; the old answer put it in the section's form
    host, which is nowhere near the control that asked."""
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
        return _market_confirm(request, ref, placement_id, layer, index, seat, str(exc))
    return _panel(request, ref, org, placement_id)


# --- the two ghost-row forms --------------------------------------------------


def _panel_refusal(
    request: Request, ref: str, org: Any, placement_id: str, message: str
) -> HTMLResponse:
    """A refusal for a form-host control with no form to re-render — said in
    the form host itself, never a status code htmx would drop."""
    return HTMLResponse(f'<p class="form-error" role="alert">{message}</p>')


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
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    if not placement.program_path:
        return _panel_refusal(
            request, ref, org, placement_id,
            f"{placement.ref} has no program file linked — scaffold one first",
        )
    if not sync.program_lines(conn, placement_id):
        return _panel_refusal(
            request, ref, org, placement_id,
            "the program has no lines — build them in towerkit first",
        )
    return _mini_form(
        request, _layer_add_fields(conn, placement_id),
        f"/accounts/{ref}/program/{placement_id}/layers", "new layer",
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


@router.get(
    "/accounts/{ref}/program/{placement_id}/submissions/new",
    response_class=HTMLResponse,
)
def submission_new_form(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """The TUI's `s`, webside: send this program to a market. The whole-record
    submission_form (market select, optional underwriter, sent date, notes)
    renders into the section's form host."""
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    from ...repo import orgs as orgs_repo

    if not orgs_repo.list_orgs(conn, kind="market"):
        return _panel_refusal(
            request, ref, org, placement_id,
            "no markets on file — create one in the terminal app "
            "(m, then a) before sending a submission",
        )
    spec = submission_form(conn)
    action = f"/accounts/{ref}/program/{placement_id}/submissions"
    return HTMLResponse(render_form(request, spec, action))


@router.post(
    "/accounts/{ref}/program/{placement_id}/submissions", response_class=HTMLResponse
)
async def submission_create(
    request: Request, ref: str, placement_id: str
) -> HTMLResponse:
    """Success answers HX-Redirect to the PIPELINE tab, where the submission
    is actually visible — landing back on a tab that shows no trace of what
    was just made is the dishonest option. Refusals re-render the form with
    the input intact via the shared _save seam."""
    from fastapi.responses import Response

    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    spec = submission_form(conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/program/{placement_id}/submissions"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_submission(conn, values, placement_id=placement_id),
    )
    if refused is not None:
        return refused
    return Response(
        status_code=204, headers={"HX-Redirect": f"/accounts/{ref}/pipeline"}
    )  # type: ignore[return-value]


@router.get(
    "/accounts/{ref}/program/{placement_id}/renew", response_class=HTMLResponse
)
def renew_confirm(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """Confirm-first, stating exactly what sync.renew does. Writes nothing.
    (The account header's Renew stayed unrendered under D4 — it names no
    placement; this control is placement-scoped.)"""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    from pathlib import Path

    next_from, next_to = sync.renewal_period(placement)
    file_name = Path(str(placement.program_path)).name if placement.program_path else ""
    return TEMPLATES.TemplateResponse(
        request, "account/_renew_confirm.html",
        {
            "placement": placement,
            "next_from": next_from, "next_to": next_to,
            "file_name": file_name,
            "action": f"/accounts/{ref}/program/{placement_id}/renew",
        },
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/renew", response_class=HTMLResponse
)
def renew_placement(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """sync.renew in one web batch: next period, prospective, the file cloned
    and linked at birth. Answers with the WHOLE panel either way — a renewal
    adds a program to the list, and this POST targets the panel, so a
    refusal must come back as the panel with its error slot filled."""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    try:
        # program_ tool: a plain row revert of a renew would delete the new
        # placement while the CLONED FILE stayed on disk, and the next sync
        # would silently recreate it — refuse-first is the honest answer
        # until a real renew-revert (which must delete the clone) exists.
        with batches_svc.open_batch(
            conn, source="web", tool="program_renew", org_id=org.id,
            summary=f"renewed {placement.ref}",
        ):
            new_placement, new_path, diags = sync.renew(conn, placement_id)
            if new_placement is None or not diags.ok:
                first = diags.errors[0].message if diags.errors else "unknown error"
                raise ValueError(f"renew refused: {first}")
    except Exception as exc:
        return _programs_panel(request, ref, org, error=str(exc))
    return _programs_panel(request, ref, org)


@router.get(
    "/accounts/{ref}/program/{placement_id}/merge", response_class=HTMLResponse
)
def merge_form(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """The TUI's `x`, webside: pick which same-account sibling survives.
    Writes nothing; the rule (children move, source retires, one file link
    carries, two file-backed refuse) is stated in the form."""
    org = _org(request, ref)
    conn = _conn(request)
    source = _owned(conn, org, "placement", placement_id, placements_repo.get)
    siblings = [
        p for p in placements_repo.for_org(conn, org.id) if p.id != source.id
    ]
    if not siblings:
        return _panel_refusal(
            request, ref, org, placement_id,
            f"{source.ref} is this account's only program — nothing to merge into",
        )
    return TEMPLATES.TemplateResponse(
        request, "account/_merge_confirm.html",
        {
            "source": source, "siblings": siblings,
            "action": f"/accounts/{ref}/program/{placement_id}/merge",
        },
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/merge", response_class=HTMLResponse
)
async def merge_placement(
    request: Request, ref: str, placement_id: str
) -> HTMLResponse:
    """services.merge.merge_placements in one web batch — the same call and
    tool the TUI's `x` makes, so the changes list reads identically. Panel
    answers both ways: this POST targets #programs-panel (the list shrinks),
    so a refusal must come back panel-shaped with the error slot filled."""
    from ...services.merge import MergeError, merge_placements

    org = _org(request, ref)
    conn = _conn(request)
    source = _owned(conn, org, "placement", placement_id, placements_repo.get)
    target_id = str((await request.form()).get("target_id", ""))
    if not target_id:
        return _programs_panel(
            request, ref, org, error="pick the program that survives the merge"
        )
    try:
        target = _owned(conn, org, "placement", target_id, placements_repo.get)
        with batches_svc.open_batch(
            conn, source="web", tool="merge_placements", org_id=org.id,
            summary=f"merged {source.ref} into {target.ref}",
        ):
            merge_placements(conn, source.id, target.id)
    except (MergeError, HTTPException) as exc:
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _programs_panel(request, ref, org, error=str(message))
    return _programs_panel(request, ref, org)


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
async def scaffold_create(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """Create the towerkit file and link it.

    Every refusal comes back in the page and NAMES what to do: the file that
    already exists, or the setting that has not been made. A destructive-ish
    control that answers with a status code produces no swap and no message at
    all under htmx."""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)

    if placement.program_path:
        # The confirm's POST targets #programs-panel with outerHTML, so a
        # refusal MUST come back as the panel (with the message in its error
        # slot) — a bare fragment would replace the whole panel, id and all,
        # and no later swap could restore it (fresh-eyes review, 2026-08-19).
        # No "unlink it first": unlink exists on no surface yet (phase 2) and
        # a refusal must never name a verb the app cannot do.
        return _programs_panel(
            request, ref, org,
            error=f"{placement.ref} already has a program file: "
            f"{placement.program_path}. Open it in towerkit.",
        )
    from pathlib import Path

    typed = str((await request.form()).get("path", "")).strip()
    destination = (
        Path(typed).expanduser() if typed else _scaffold_destination(conn, org, placement)
    )
    if destination is None:
        return _programs_panel(
            request, ref, org,
            error="no program file location is set yet — configure the program "
            "roots first (`,` on Today in the terminal app), then scaffold.",
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
        return _programs_panel(request, ref, org, error=str(exc))
    return _programs_panel(request, ref, org)
