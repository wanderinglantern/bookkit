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

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ... import db, sync, towerfields
from ...forms.entities import apply_placement, placement_form
from ...forms.inline import LAYER_FIELDS, PARTICIPANT_FIELDS, PLACEMENT_FIELDS
from ...forms.spec import (
    Field,
    FormSpec,
    checked_option,
    initial_text,
    parse_value,
)
from ...money import format_cents, format_cents_compact
from ...repo import placements as placements_repo
from ...repo import projection, vocab
from ...services import batches as batches_svc
from ...services import placement_edit, program_files
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display, render_form
from .account import (
    _conn,
    _context,
    _org,
    _owned,
    _save,
    forget_program_reads,
    layers_for,
    linked_for,
)

# ATTACHMENT IS NOT AN EDITABLE WEB FIELD (program-worksheet redesign,
# 2026-08-24). A slab's attachment comes from its position — the worksheet
# states it as a sentence and changes it with move/insert/split — so the web
# offers no cell for it, and the cell routes refuse the key rather than keep
# a URL-only editor alive. `sync.update_layer(attach_cents=...)` remains for
# MCP/TUI callers whose figure is already known.
_LAYER_CELLS: dict[str, Field] = {
    f.key: f for f in LAYER_FIELDS if f.key != "attach_cents"
}
# The add form's amount input, parsed by bookkit's own money field so that
# "1.5m", "250k" and "1,234.56" mean here exactly what they mean in the layer's
# own limit cell. The EDIT of the same value goes through towerfields (it is
# `named_limit.amount` on towerkit's surface); this one is the row that does not
# exist yet, so there is no entry to derive it from.
_NAMED_LIMIT_AMOUNT: Field = Field("amount", "amount", "money", required=True)
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


def _view_state(request: Request) -> tuple[str | None, frozenset[str]]:
    """The worksheet's two pieces of URL state: the selected layer and the
    collapsed index groups.

    SERVER-HELD, IN THE URL, BY DESIGN (program-worksheet hand-off,
    2026-08-24): the section is re-rendered by htmx on every write, so
    client-only state would be lost on each one — and a refresh must land
    back on the same layer. Explicit query params win (the worksheet GET and
    the full tab both carry them); every other request recovers them from the
    browser URL htmx reports in HX-Current-URL, which is what keeps a cell
    save from throwing the broker back to the first layer."""
    from urllib.parse import parse_qsl, urlparse

    params: dict[str, str] = dict(request.query_params)
    recovered: dict[str, str] = {}
    current = request.headers.get("HX-Current-URL", "")
    if current:
        recovered = dict(parse_qsl(urlparse(current).query))
    # PER KEY, not all-or-nothing: a link carrying only ?layer= (the tower
    # click, a preview's Discard) must not wipe the collapse state the URL
    # still holds — and vice versa. An EXPLICIT empty closed= ("expand all")
    # still wins, which is why presence is the test, not truthiness.
    layer = (
        params.get("layer") if "layer" in params else recovered.get("layer")
    ) or None
    raw_closed = (
        params["closed"] if "closed" in params else recovered.get("closed", "")
    )
    closed = frozenset(part for part in raw_closed.split(",") if part)
    return layer, closed


def _closed_param(closed: frozenset[str]) -> str:
    return ",".join(sorted(closed))


def _band_stats(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """The program band's derived figures — sums of the per-layer derivations
    sync.layer_details already made, so nothing here converts or multiplies
    money. Buffers and statutory layers carry no capacity to place, so they
    are outside the open figure by definition, not by rounding."""
    placeable = [
        row for row in layers if not row["buffer"] and not row["statutory"]
    ]
    premiums = [row["premium_cents"] for row in layers if row["premium_cents"]]
    open_rows = [row for row in placeable if row["open_limit_cents"] > 0]
    return {
        "premium": format_cents_compact(sum(premiums)) if premiums else None,
        "open": (
            format_cents_compact(sum(row["open_limit_cents"] for row in open_rows))
            if open_rows
            else None
        ),
        "open_layers": len(open_rows),
    }


def _tower_for(linked: sync.LinkedProgram) -> dict[str, Any] | None:
    """The drawn tower for an already-loaded program, or None.

    None and an empty tower are different facts — "no program file" against "a
    program with nothing in it" — and the template says different things about
    them. This no longer opens the file itself: it takes the one
    `LinkedProgram` the render already parsed, so the drawing and the table
    below it can never disagree about which bytes they are showing, and a
    panel costs one file read rather than two.

    A tower that fails to DRAW is still None (towerkit's renderer can refuse a
    program the model accepted), but the panel now says why the FILE would not
    load, which is the case that actually happens.
    """
    if linked.program is None:
        return None
    from ..tower import panel

    try:
        return panel(linked.program)
    except Exception:
        return None


def _marketing(
    request: Request, conn: sqlite3.Connection, placement: Any, ref: str
) -> dict[str, Any]:
    """The marketing grid for this placement.

    NOT gated on `program_path`. Marketing happens BEFORE a tower exists and
    every figure in the grid lives in SQLite, which is the same reasoning that
    put the "Marketing XLSX" anchor in the band rather than in the
    linked-only export strip: gating it would put the section out of reach on
    exactly the placements it is for.

    `date.today()` is read here rather than inside the view model, so the one
    module that formats the report keeps its "today is a parameter" rule.

    THE ORDER SURVIVES A FULL PAGE LOAD. `?sort=` is what the marketing
    section's own header control puts in the URL, and this is the render that
    happens when a broker refreshes, follows a link back, or opens the tab in a
    second window. Reading it here is what makes the sort a real view of the
    page rather than something that only exists between two htmx swaps — and
    it is the SAME parameter name routes/marketing.py's router dependency
    reads, because there is one spelling of this.
    """
    from datetime import date as _date

    from ..marketing_grid import panel

    return panel(
        request, conn, placement.id, today=_date.today(), ref=ref,
        sort_spec=str(request.query_params.get("sort", "")),
    )


def _last_synced(conn: sqlite3.Connection, placement_id: str) -> dict[str, Any] | None:
    """What the LAST SUCCESSFUL SYNC recorded for a placement whose file will
    not open right now — read-only, and labelled as such.

    Grant's five broken placements still held 12, 1, 8, 14 and 10 projected
    layers between them while the web showed an empty panel and said the files
    had no layers. The data was in the database the whole time. Showing it
    beats showing nothing, but ONLY with the date on it and no editors: a
    stale figure a broker can quote from is worse than a blank, and an editor
    over it would write through to a file that is not there.
    """
    rows = projection.layers_for_placement(conn, placement_id)
    if not rows:
        return None
    seats: dict[str, list[str]] = {}
    for seat in projection.participants_for_placement(conn, placement_id):
        seats.setdefault(str(seat["layer_id"]), []).append(
            f"{seat['carrier']} {seat['share_bps'] / 100:g}%"
        )
    return {
        "synced_at": rows[0]["synced_at"][:10],
        "layers": [
            {
                "name": row["name"],
                "attach": format_cents_compact(int(row["attach"])),
                "limit": format_cents_compact(int(row["lim"])),
                "premium": (
                    format_cents_compact(int(row["premium"]))
                    if row["premium"] is not None
                    else "—"
                ),
                "markets": ", ".join(seats.get(str(row["layer_id"]), [])) or "To be placed",
            }
            for row in rows
        ],
    }


def _diagnostics(linked: Any) -> list[dict[str, Any]]:
    """Every error and warning towerkit reports for this file, for display.

    ERRORS FIRST, because an error is a claim the tower is wrong and a warning
    is a claim it is incomplete — and the reader is looking for the first one.
    Each carries `ref`, towerkit's own ("layer", id) / ("line", id) pointer, so
    the strip can say WHICH layer without this module parsing a message.

    Loading is somebody else's failure and is already printed as `load_error`:
    a file that will not parse has no diagnostics to give, and repeating the
    same sentence twice under two headings reads as two problems.
    """
    from towerkit.validate import validate_program

    if linked.program is None:
        return []
    diags = validate_program(linked.program)
    ordered = list(diags.errors) + list(diags.warnings)
    return [
        {
            "severity": d.severity,
            "code": d.code,
            "message": d.message,
            "kind": d.ref[0] if d.ref else "",
            "target": d.ref[1] if d.ref and d.ref[1] is not None else "",
        }
        for d in ordered
    ]


def _index_groups(
    request: Request,
    ref: str,
    placement: Any,
    layers: list[dict[str, Any]],
    *,
    selected: str | None,
    closed: frozenset[str],
) -> dict[str, Any] | None:
    """The structure index — the one list of everything the tower holds,
    grouped by line group and collapsible, replacing the per-line stack
    editor (design 3A, 2026-08-24).

    Built from the SAME `layer_details` rows the worksheet reads (`layers_for`
    memo) — never a second walk of the towerkit model. `attach`/`limit` come
    off the rows' `*_cents` values, already converted at the source, so this
    function does no money conversion of its own.

    GROUPED BY LINE OF COVERAGE, which is the structure the data actually
    has: a line of coverage holds layers (Grant, 2026-08-24). It used to
    group by `line.group` — towerkit's BUCKET label (project / location /
    entity) — and almost no program sets one, so every layer in the tower
    fell into a single pile headed "COVER · GL AL IM". The rail showed a flat
    list where the file holds two levels, and a broker with nowhere to put a
    new line of coverage typed one into the layer form instead.

    The bucket is not lost, it is demoted: on a program whose lines DO carry
    groups, the group name rides on the line's own header, where it says
    something, instead of swallowing every line into one heap.

    A layer sits under the FIRST line it covers, ONCE — a spanning slab
    announces its span on the row rather than appearing three times, so the
    group counts still sum to the tower's own count. Lines are in the
    program's own order, which is COLUMN order in the drawing and never
    alphabetical. A line with no layers still gets its group, with a count of
    zero: towerkit reports `line-empty` as an error, and a rail that hid the
    line would hide the thing the diagnostics are pointing at. Rows are
    top-of-tower first, matching the drawing.

    Collapse state is a QUERY PARAM, not JS state (see `_view_state`); the
    group count stays visible while collapsed, which is the point of
    collapsing. Group slugs are prefixed with the placement id so two
    programs' 'Casualty' groups collapse independently.
    """
    from towerkit.edit import slugify

    conn = _conn(request)
    linked = linked_for(request, conn, placement.id)
    # `not layers` was here, and it contradicted the paragraph above: a linked
    # file with lines and NO layers returned None, so the whole workbench gate
    # in `_layers_panel.html` went false and the program rendered neither its
    # diagnostics nor either terms strip — while towerkit reported one
    # `line-empty` ERROR per line. The one file the app knows is broken was the
    # one it said nothing about (2026-08-24). A program with no LINES still
    # returns None: there is no rail to draw, and the panel's own empty state
    # is the right answer.
    if linked.program is None or not linked.program.lines:
        return None
    lines = linked.program.lines
    base = f"/accounts/{ref}/program/{placement.id}"
    known = {line.id for line in lines}

    def url(layer: str | None, closed_set: frozenset[str]) -> str:
        # closed rides EXPLICITLY even when empty: under per-key recovery an
        # omitted param means "keep what the URL has", and expand-all means
        # the opposite.
        query = f"layer={layer}&" if layer else ""
        query += f"closed={_closed_param(closed_set)}"
        return f"{base}/worksheet?{query}"

    groups: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for index, line in enumerate(lines):
        slug = f"{placement.id}.{slugify(line.id)}"
        while slug in seen_slugs:  # ids are unique; belt and braces
            slug += "-"
        seen_slugs.add(slug)
        rows = sorted(
            (row for row in layers if row["applies_to"][0] == line.id),
            key=lambda row: (-row["attach_cents"], -row["limit_cents"]),
        )
        is_closed = slug in closed
        groups.append({
            "slug": slug,
            "name": line.name,
            # COLUMN ORDER IS EDITED WHERE THE STRUCTURE IS READ. The chips in
            # the band above have always carried these two, but the rail is
            # where a broker works the tower now, and reaching back up to a
            # strip of chips to reorder the columns beside it is a seam nobody
            # should have to know about (Grant, 2026-08-24: "unable to reorder
            # lines in the schematic"). Same route, same write, same one undo
            # unit — this is a second door, never a second definition.
            "move_base": f"{base}/lines/{line.id}/move",
            "first": index == 0,
            "last": index == len(lines) - 1,
            # THE LINE'S OWN CONTROLS, the same chip the band above renders —
            # not a second copy of them. The rail is where the structure is
            # worked now, and it could reorder a line but not rename it,
            # relabel it or remove it: the affordances stayed at the old home,
            # which is the shape of the bug Grant reported about reordering
            # (2026-08-24). One partial, one set of routes, two doors.
            "chip": _line_chip_html(
                request, ref, placement.id, line.id, line.name,
                first=index == 0, last=index == len(lines) - 1,
            ),
            # The bucket, where it says something. A line with no group shows
            # its column label instead — the letters the drawing prints in
            # that column's header, which is how a reader ties the rail to
            # the picture.
            "label": line.group or line.label,
            # The bucket ALONE, for the rail: the chip prints the column label
            # in its own cell, so repeating it in the header would be the same
            # word twice — while a real bucket says something neither says.
            "bucket": line.group or None,
            "count": len(rows),
            "closed": is_closed,
            "toggle_url": url(selected, closed ^ {slug}),
            "rows": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "limit": (
                        "statutory"
                        if row["statutory"]
                        else format_cents_compact(row["limit_cents"])
                    ),
                    "buffer": row["buffer"],
                    "statutory": row["statutory"],
                    # A buffer carries no carriers, so a signed figure on it
                    # would claim placement of a band nobody insures.
                    "signed": (
                        None
                        if row["buffer"] or row["statutory"]
                        else f"{row['signed_pct']:g}"
                    ),
                    "placed": row["signed_pct"] >= 100,
                    "spans": len(row["applies_to"]) if len(row["applies_to"]) > 1 else 0,
                    "selected": row["id"] == selected,
                    "url": url(row["id"], closed),
                }
                for row in rows
            ] if not is_closed else [],
        })

    # A layer whose first line the program does not declare (towerkit loads
    # the file and flags layer-unknown-line as an ERROR) still gets a row —
    # the diagnostics point at it, so the index must be able to reach it
    # (review C7).
    orphans = [
        row for row in layers
        if row["applies_to"] and row["applies_to"][0] not in known
    ]
    if orphans:
        slug = f"{placement.id}.unknown-line"
        groups.append({
            "slug": slug,
            "name": "Unknown line",
            "label": " ".join(sorted({row["applies_to"][0] for row in orphans})),
            "count": len(orphans),
            # No chip either: there is no line to rename, relabel or remove.
            "chip": None,
            "bucket": None,
            # No move controls: this group is not a line, it is the layers
            # whose line the file does not declare. There is nothing to
            # reorder and towerkit has no id to move.
            "move_base": None,
            "closed": slug in closed,
            "toggle_url": url(selected, closed ^ {slug}),
            "rows": [] if slug in closed else [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "limit": (
                        "statutory" if row["statutory"]
                        else format_cents_compact(row["limit_cents"])
                    ),
                    "buffer": row["buffer"],
                    "statutory": row["statutory"],
                    "signed": (
                        None if row["buffer"] or row["statutory"]
                        else f"{row['signed_pct']:g}"
                    ),
                    "placed": row["signed_pct"] >= 100,
                    "spans": len(row["applies_to"]) if len(row["applies_to"]) > 1 else 0,
                    "selected": row["id"] == selected,
                    "url": url(row["id"], closed),
                }
                for row in orphans
            ],
        })

    all_slugs = frozenset(group["slug"] for group in groups)
    return {
        "total": len(layers),
        "groups": groups,
        "all_closed": closed >= all_slugs,
        "collapse_all_url": url(selected, all_slugs),
        "expand_all_url": url(selected, frozenset()),
    }


def _default_selection(
    layers: list[dict[str, Any]], index: dict[str, Any] | None, wanted: str | None
) -> str | None:
    """The layer the worksheet shows: the one asked for if it exists, else the
    top of the first group — never a guess between programs (an id from
    another placement simply is not in this one's rows)."""
    if wanted and any(row["id"] == wanted for row in layers):
        return wanted
    if index:
        for group in index["groups"]:
            if group["rows"]:
                return str(group["rows"][0]["id"])
    if layers:
        return str(layers[0]["id"])
    return None


def _worksheet_ctx(
    request: Request,
    ref: str,
    placement_id: str,
    layer: dict[str, Any],
    layers: list[dict[str, Any]],
    error: str | None = None,
) -> dict[str, Any]:
    """One layer's whole worksheet — the pane's single context builder, for
    the section render and every structure write's answer alike, so the pane
    cannot drift between its producers (the same one-renderer rule the old
    details row carried; this absorbs it).

    The position sentence replaces the attachment FIELD: attachment is never
    typed here, it is stated — 'Sits on 2nd Excess → attaches at
    $102,000,000' — and changed only by the move/insert controls (design 1C,
    2026-08-24). Statutory and ground layers get their own true sentences
    rather than a $0 dressed up as a figure.
    """
    conn = _conn(request)
    layer_id = str(layer["id"])
    base = f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}"
    linked = linked_for(request, conn, placement_id)
    lines = linked.program.lines if linked.program else []
    label_of = {line.id: line.label for line in lines}
    # The line's full NAME, for anywhere a reader is choosing between lines
    # rather than reading a column header — a picker that says "(WC)" makes
    # somebody translate; "(Workers Compensation)" does not.
    line_named = {line.id: line.name for line in lines}

    def cell(key: str) -> str:
        field = _layer_field(key)
        return render_cell_display(
            request, field, _display_text(field, layer.get(key)),
            _layer_cell_action(ref, placement_id, layer_id, key),
            tag="span", extra_class=_LAYER_CELL_CLASS.get(key, ""),
        )

    def tower_cell(kind: str, name: str, addr: str) -> str:
        placement = placements_repo.get(conn, placement_id)
        return _field_display(request, ref, placement, kind, name, addr)

    # The slab this one sits on: the same column (its first line), the
    # nearest attachment below — derived from the rows already read, never a
    # second model walk.
    column = layer["applies_to"][0] if layer["applies_to"] else None
    underneath = [
        row for row in layers
        if column in row["applies_to"]
        and row["id"] != layer_id
        and row["attach_cents"] < layer["attach_cents"]
    ]
    below = max(underneath, key=lambda row: row["attach_cents"], default=None)

    named = sync.layer_named_limits(linked.program, layer_id)
    spans = len(layer["applies_to"])
    signed = layer["signed_pct"]
    open_pct = layer["open_pct"]
    participants = layer["participants"]
    return {
        "id": layer_id,
        "base": base,
        "error": error,
        "name_cell": cell("name"),
        "spans": spans if spans > 1 else 0,
        "buffer": layer["buffer"],
        "statutory": layer["statutory"],
        "below_name": below["name"] if below else None,
        "attach": format_cents(layer["attach_cents"]),
        "top": format_cents(layer["top_cents"]),
        "covers_label": " · ".join(
            label_of.get(lid, lid) for lid in layer["applies_to"]
        ),
        "limit_cell": cell("limit_cents"),
        "premium_cell": cell("premium_cents"),
        # THE LAYER'S PREMIUM IS ITS MARKETS' SUM once any of them states its
        # own — towerkit refuses a write to it while that holds, because it IS
        # the sum and typing over it would make one of the two figures a lie.
        # The cell stays live so the refusal can say that in the place the
        # broker typed; this is what tells them before they do.
        "premium_from_markets": any(
            part.get("premium_stated") for part in participants
        ),
        "signed": f"{signed:g}",
        "placed": signed >= 100,
        "open_pct": f"{open_pct:g}",
        "open_limit": (
            format_cents_compact(layer["open_limit_cents"])
            if layer["open_limit_cents"]
            else None
        ),
        "open_limit_exact": (
            format_cents(layer["open_limit_cents"])
            if layer["open_limit_cents"]
            else None
        ),
        "market_rows": [
            _market_row_html(request, ref, placement_id, layer, i, seat)
            for i, seat in enumerate(participants)
        ],
        "open_row": (
            not layer["buffer"] and not layer["statutory"]
            and layer["open_limit_cents"] > 0
        ),
        "markets_base": f"{base}/markets",
        "add_fields": _participant_fields(conn),
        "policy_cell": cell("policy_number"),
        "from_cell": cell("period_from"),
        "to_cell": cell("period_to"),
        "policy_link_action": f"{base}/policy",
        "policy_link_options": _policy_link_options(layers, layer_id, line_named),
        "policy_linked_to": sync.policy_partners_of(linked.program, layer_id),
        "tower_cells": {
            key.split(".", 1)[1]: tower_cell(
                "layer", key.split(".", 1)[1], _addr(layer_id, None)
            )
            for key in _PLACED
            if key.startswith("layer.")
        },
        "named_limits": [
            {
                "index": item["index"],
                "name_cell": tower_cell(
                    "named_limit", "name", _addr(layer_id, item["index"])
                ),
                "amount_cell": tower_cell(
                    "named_limit", "amount", _addr(layer_id, item["index"])
                ),
            }
            for item in named
        ],
        "lines": [
            {"id": lid, "name": name, "label": label_of.get(lid, name),
             "on": lid in layer["applies_to"]}
            for lid, name in sync.program_lines_of(linked.program)
        ],
        "follows": bool(layer.get("follows_underlying")),
        "remove_url": f"{base}/remove",
    }


def _section_html(
    request: Request,
    ref: str,
    org: Any,
    placement: Any,
    *,
    refocus: str | None = None,
    selected: str | None = None,
    worksheet_error: str | None = None,
) -> str:
    """ONE renderer for a program section, for every caller.

    The full page and every write response used to build this context
    SEPARATELY — the page in `_programs`, the write in `_panel` — with the
    keys re-listed a third time in the `{% with %}` block that included the
    template. Three lists, and a key added to any one of them was silently
    absent from the other two. That is not hypothetical: `tower` was in the
    page's list and not the write's, so every save quietly erased the drawing;
    then `load_error` was in both routes' lists and not the `{% with %}`, so
    the fix for "this file will not load" rendered on writes and not on the
    page it was written for. Both found the same afternoon (2026-08-20).

    Now there is one list, here, and the template is included with the whole
    context rather than a hand-copied subset of it.
    """
    conn = _conn(request)
    linked = linked_for(request, conn, placement.id)
    layers = layers_for(request, conn, placement.id) if placement.program_path else []
    wanted, closed = _view_state(request)
    index = _index_groups(
        request, ref, placement, layers,
        selected=selected or wanted, closed=closed,
    )
    chosen = _default_selection(layers, index, selected or wanted)
    if index is not None and (selected or wanted) != chosen:
        # The row the index highlights must be the layer the worksheet shows,
        # so a fallback selection re-derives the index with it.
        index = _index_groups(
            request, ref, placement, layers, selected=chosen, closed=closed,
        )
    selected_layer = next((row for row in layers if row["id"] == chosen), None)
    # A pane that fails to build must not take the section with it: the index
    # and the band still render so another layer stays selectable — the same
    # rule that kept a broken details row from reading as a dead chevron. The
    # exception is logged, not swallowed; the pane prints why.
    worksheet = None
    worksheet_failure = None
    if selected_layer is not None:
        try:
            worksheet = _worksheet_ctx(
                request, ref, placement.id, selected_layer, layers,
                error=worksheet_error,
            )
        except Exception as exc:  # noqa: BLE001 - logged, then shown
            logging.getLogger(__name__).exception(
                "worksheet build failed for %s/%s", placement.id, chosen
            )
            worksheet_failure = (
                f"this layer's worksheet could not be read — "
                f"{type(exc).__name__}: {exc}"
            )
    template = TEMPLATES.env.get_template("account/_layers_panel.html")
    return template.render(
        header={"org": org},
        placement=placement,
        placement_cells=_placement_cells(request, ref, placement),
        line_chips=_line_chips(request, ref, placement),
        term_chips=_term_chips(request, ref, placement),
        linked=bool(placement.program_path),
        # The two facts the panel used to conflate. `load_error` is a sentence
        # to PRINT, not a flag: "no layers yet" is what an unreadable file
        # looked like for as long as the reads swallowed their exceptions.
        load_error=linked.error,
        # WHAT TOWERKIT SAYS ABOUT THIS FILE, printed rather than kept.
        # Diagnostics reached the browser ONLY when a write was refused, so a
        # file that already contained an overlap — written by towerkit's own
        # editor, by MCP, or by an import — drew a garbled tower and the page
        # said nothing. Grant hit exactly that: two D&O excess layers at the
        # same attach, drawn on top of each other, labels overprinting, and he
        # was reduced to reading the picture to work out why (2026-08-21).
        # The app knew. `line-overlap`, in towerkit's own words.
        diagnostics=_diagnostics(linked),
        moved_from=linked.moved_from,
        last_synced=_last_synced(conn, placement.id) if linked.error else None,
        tower=_tower_for(linked),
        # D6. Only for a linked placement: there is no file to hold either of
        # these otherwise, and a cell that refuses every save is worse than no
        # cell.
        program_notes=(
            _field_display(
                request, ref, placement, "program", "notes", _addr(None, None)
            )
            if placement.program_path
            else None
        ),
        render_cells=(
            {
                shown: _field_display(
                    request, ref, placement, "program", name, _addr(None, None)
                )
                for shown, name in _RENDER_OPTIONS
            }
            if placement.program_path
            else {}
        ),
        has_layers=bool(layers),
        band=_band_stats(layers),
        file_name=Path(placement.program_path).name if placement.program_path else None,
        index=index,
        selected=chosen,
        worksheet=worksheet,
        worksheet_failure=worksheet_failure,
        refocus=refocus,
    )


def _programs(request: Request, org: Any) -> list[str]:
    """Every placement on the account, rendered. One file open apiece — the
    per-request memo in account.linked_for is what makes layers, lines, terms
    and the tower share a single parse of the same bytes."""
    conn = _conn(request)
    return [
        _section_html(request, org.ref, org, placement)
        for placement in placements_repo.for_org(conn, org.id)
    ]


def _programs_panel(
    request: Request, ref: str, org: Any, *, error: str | None = None
) -> HTMLResponse:
    """The whole tab body. Creating a placement and scaffolding a file both
    change the LIST rather than one row of it, so neither can honestly swap a
    single panel."""
    forget_program_reads(request)
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

    forget_program_reads(request)
    return _panel(request, ref, org, placement_id, refocus=f"cell:{key}")


# --- the lines strip (phase 3, D1) ---------------------------------------------
#
# Lines are the axis a scaffolded program could never escape from the browser:
# "Coverage TBD" stayed TBD forever (F4). Rename rides the cell contract; the
# ID FOLLOWS THE NAME and cascades through every appliesTo, so every rename
# success answers with the whole panel — the cell's own action URL is stale
# the moment the write lands.

_LINE_NAME_FIELD = Field("name", "line of coverage", required=True)


def _line_name(conn: sqlite3.Connection, placement_id: str, line_id: str) -> str:
    for lid, name in sync.program_lines(conn, placement_id):
        if lid == line_id:
            return str(name)
    raise HTTPException(status_code=404, detail=f"no line {line_id!r} on this program")


def _line_ends(
    conn: sqlite3.Connection, placement_id: str, line_id: str
) -> tuple[bool, bool]:
    """(is first, is last) in COLUMN order — what decides which of a chip's
    two arrows is dead. The single-chip routes compute it too, or a chip
    swapped back in place would come back with both arrows live at an end the
    strip beside it draws as disabled."""
    ids = [lid for lid, _ in sync.program_lines(conn, placement_id)]
    if line_id not in ids:
        return False, False
    index = ids.index(line_id)
    return index == 0, index == len(ids) - 1


def _lines_base(ref: str, placement_id: str) -> str:
    return f"/accounts/{ref}/program/{placement_id}/lines"


def _line_cell_action(ref: str, placement_id: str, line_id: str) -> str:
    return f"{_lines_base(ref, placement_id)}/{line_id}/cell/name"


def _line_chip_html(
    request: Request,
    ref: str,
    placement_id: str,
    line_id: str,
    name: str,
    *,
    first: bool = False,
    last: bool = False,
) -> str:
    """One line of coverage's controls, wherever a line is worked — the band's
    strip and the structure rail both render this.

    `first`/`last` disable the arrow that would do nothing. towerkit treats a
    move off either end as a no-op, so the guard is about the reader, not the
    write: a live-looking control that changes nothing reads as a broken app.
    """
    cell = render_cell_display(
        request, _LINE_NAME_FIELD, name,
        _line_cell_action(ref, placement_id, line_id),
        tag="span", extra_class="line-name",
    )
    placement = placements_repo.get(_conn(request), placement_id)
    template = TEMPLATES.env.get_template("account/_line_chip.html")
    return template.render(
        base=f"{_lines_base(ref, placement_id)}/{line_id}",
        name=name,
        first=first,
        last=last,
        name_cell=cell,
        # D6, through the derived seam. The NAME is a bespoke cell because
        # renaming a line cascades its id through every appliesTo — bookkit's
        # own rule; the column label is a plain scalar and has none.
        abbr_cell=_field_display(
            request, ref, placement, "line", "abbr", _addr(line_id, None)
        ),
    )


def _line_chips(request: Request, ref: str, placement: Any) -> list[str] | None:
    """None for an unlinked placement — no file, no lines, and the strip
    saying 'no lines' about a file that does not exist would mislead."""
    if not placement.program_path:
        return None
    conn = _conn(request)
    # Through the per-request memo, not a fresh open: the Program tab lists
    # every placement and each panel wants layers, lines, terms and a tower
    # off the SAME file. Reading it once per consumer had the tab opening and
    # re-parsing each program five times per render (caught 2026-08-20 by
    # test_layer_details_is_read_once_per_page, once it was pointed at the
    # function that actually does the I/O).
    program = linked_for(request, conn, placement.id).program
    lines = sync.program_lines_of(program)
    return [
        _line_chip_html(
            request, ref, placement.id, lid, name,
            first=index == 0, last=index == len(lines) - 1,
        )
        for index, (lid, name) in enumerate(lines)
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
    return _panel(request, ref, org, placement_id)


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
    first, last = _line_ends(conn, placement_id, line_id)
    return HTMLResponse(
        _line_chip_html(
            request, ref, placement_id, line_id, name, first=first, last=last
        )
    )


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
        first, last = _line_ends(conn, placement_id, line_id)
        return HTMLResponse(
            _line_chip_html(
                request, ref, placement_id, line_id, current,
                first=first, last=last,
            )
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
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id)


@router.post(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/move",
    response_class=HTMLResponse,
)
async def line_move(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    """Column order in the drawing (phase 4). A move off either end is
    towerkit's documented no-op; either way the panel comes back, so the
    chips and the tower's columns always agree."""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    name = _line_name(conn, placement_id, line_id)
    try:
        delta = int(str((await request.form()).get("delta", "0")))
    except ValueError:
        delta = 0
    try:
        program_files.write(
            conn, placement,
            tool="program_line_edit",
            summary=f"moved line {name}",
            mutate=lambda: sync.move_line(conn, placement_id, line_id, delta),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _panel_refusal(request, ref, org, placement_id, str(exc))
    forget_program_reads(request)
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
    forget_program_reads(request)
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
            "subject": layer["name"],
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

    sync.project(conn, sync.program_file(conn, placement), placement_id=placement.id)


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
    forget_program_reads(request)
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    return _panel(request, ref, org, placement_id, refocus=f"{layer_id}:{key}")


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
    forget_program_reads(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    try:
        _write_layer_field(conn, placement, layer_id, key, value, field, layer)
    except Exception as exc:
        return _layer_editor_cell(request, ref, placement_id, layer, key, str(exc), raw)

    forget_program_reads(request)
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    return _panel(request, ref, org, placement_id, refocus=f"{layer_id}:{key}")


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


@router.get(
    "/accounts/{ref}/program/{placement_id}/worksheet",
    response_class=HTMLResponse,
)
def worksheet_select(
    request: Request, ref: str, placement_id: str
) -> HTMLResponse:
    """Select a layer (and carry the index's collapse state): one GET, the
    whole section back, retargeted — the same swap discipline every write
    uses, so selection and writes cannot disagree about what a section render
    is. `?layer=` and `?closed=` are the state (see `_view_state`); the
    response pushes them onto the browser URL so refresh and every later
    write land back on the same layer."""
    org = _org(request, ref)
    _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    layer, closed = _view_state(request)
    response = _panel(request, ref, org, placement_id, selected=layer)
    query = f"layer={layer}" if layer else ""
    if closed:
        query += ("&" if query else "") + f"closed={_closed_param(closed)}"
    response.headers["HX-Push-Url"] = f"/accounts/{ref}/program" + (
        f"?{query}" if query else ""
    )
    return response


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/move",
    response_class=HTMLResponse,
)
async def layer_move(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """Move the slab one step up or down its own column. Position is the
    attachment, so this is the counterpart of typing one — the whole column
    reseats inside the one mutation (sync.move_layer). Off-the-end comes back
    as a worksheet refusal, not a silent nothing."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    direction = str((await request.form()).get("direction", ""))
    if direction not in ("up", "down"):
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=f"direction is 'up' or 'down', not {direction!r}",
        )
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=f"moved {layer['name']} {direction}",
            mutate=lambda: sync.move_layer(
                conn, placement_id, layer_id, direction=direction
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=str(exc),
        )
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id, selected=layer_id)


def _insert_form(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    position: str, *, line_id: str,
    error: str | None = None, typed: dict[str, str] | None = None,
) -> HTMLResponse:
    """The worksheet's insert-above/below form — name and limit only; the
    position and anchor are implied by the control that opened it and ride as
    hidden fields, because position IS the attachment."""
    return TEMPLATES.TemplateResponse(
        request, "account/_insert_form.html",
        {
            "action": f"/accounts/{ref}/program/{placement_id}/lines/{line_id}/layers",
            "anchor": layer["id"],
            "anchor_name": layer["name"],
            "position": position,
            "line_id": line_id,
            "error": error,
            "typed": typed or {},
        },
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/insert",
    response_class=HTMLResponse,
)
def layer_insert_form(
    request: Request, ref: str, placement_id: str, layer_id: str,
    position: str = "above",
) -> HTMLResponse:
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    if position not in ("above", "below"):
        raise HTTPException(status_code=404, detail="position is above or below")
    return _insert_form(
        request, ref, placement_id, layer, position,
        line_id=str(layer["applies_to"][0]),
    )


# --- recording a policy: nine facts, one act --------------------------------


def _policy_field(request: Request, key: str, seam: str) -> Field:
    """The Field for one slot of the form, from the seam that owns it.

    NEITHER LIST IS RE-DECLARED. A layer field comes out of `LAYER_FIELDS`, a
    derived one out of towerkit through `towerfields.bookkit_field` — the
    same two sources the CELLS are built from, so the label a broker reads in
    this form and the label they read in the cell afterwards cannot differ, and
    a field towerkit renames turns red in one place rather than rendering an
    empty box here.
    """
    if seam == _POLICY_LAYER:
        return _layer_field(key)
    kind, _, name = key.partition(".")
    return towerfields.bookkit_field(_field_entry(kind, name), key)


def _policy_initial(
    request: Request, placement: Any, layer: dict[str, Any]
) -> dict[str, str]:
    """What is already recorded, so the form CORRECTS rather than re-asks.

    NOT a violation of "never pre-fill a figure that comes off a document".
    That rule refuses a GUESS — a figure this surface computed or assumed.
    What is here is what the book already holds, and showing it is the
    difference between a form that completes a record and one that quietly
    blanks the half somebody already typed. Anything unrecorded arrives
    visibly EMPTY, which is the other half of the same rule.
    """
    out: dict[str, str] = {}
    for key, seam in _POLICY_FORM:
        if seam == _POLICY_LAYER:
            value = layer.get(key)
            out[key] = initial_text(_layer_field(key), value)
        else:
            kind, _, name = key.partition(".")
            entry = _field_entry(kind, name)
            out[key] = towerfields.editor_text(
                entry,
                _field_value(request, placement.id, kind, name, layer["id"], None),
            )
    return out


def _policy_form(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    text: dict[str, str], error: str | None = None,
) -> HTMLResponse:
    """The form itself, in the `.ws-host` the other layer sub-forms use.

    COMMIT IN PLACE: a refusal re-renders this with the message and everything
    typed still in it. Nine fields is the most this page asks for at once, and
    losing them to one bad date would be the worst version of the friction
    this form exists to remove.

    `text` IS ALWAYS TEXT, and it always goes through `submitted=` — never
    `initial=`. Those are two different contracts and mixing them corrupts the
    money fields: `FormSpec.initial` holds RAW values and the renderer runs
    `initial_text` over them, so a premium already formatted as "180,000"
    arrives at `int()` and raises. The two sources this form pre-fills from
    both produce TEXT already (`initial_text` for a layer field,
    `towerfields.editor_text` for a derived one), so one convention — the
    exact characters that belong in the box — is the only one that can be
    right for both.
    """
    spec = FormSpec(
        title=f"Record the policy on {layer['name']}",
        fields=[
            _policy_field(request, key, seam) for key, seam in _POLICY_FORM
        ],
    )
    return HTMLResponse(
        render_form(
            request,
            spec,
            f"/accounts/{ref}/program/{placement_id}/layers/{layer['id']}/record",
            error=error,
            submitted=dict(text),
        )
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/record",
    response_class=HTMLResponse,
)
def layer_record_form(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """The nine facts a policy brings, asked for together.

    WRITES NOTHING — the split every form in this app makes."""
    org = _org(request, ref)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    return _policy_form(
        request, ref, placement_id, layer,
        _policy_initial(request, placement, layer),
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/record",
    response_class=HTMLResponse,
)
async def layer_record_save(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """Nine fields, TWO seams, ONE undo unit — and one FILE write.

    Two different properties, and it is worth separating them because only one
    of them is this route's doing (mutation testing, 2026-08-27, said so:
    breaking the composition left the batch count right and the file wrong).

    THE ONE BATCH IS `program_files.write`'s. It opens exactly one batch around
    whatever `mutate` does, so any number of writers inside it are already one
    undo unit. What that buys over nine separate CELL saves is real — nine
    saves are nine batches and nine presses of `u` to take back one policy —
    but it is not what composing the mutation is for.

    THE ONE FILE WRITE IS. `sync.record_layer_policy` runs every change inside
    a single `write_through`, so the file is loaded once, validated once and
    dumped once. Called one writer at a time instead, a value the MODEL refuses
    on the eighth field — `premiumDetail` on a layer that states a premium is
    the real example — leaves the first seven ON DISK with the batch rolled
    back and no snapshot taken. That is a half-recorded policy, and the file
    would then disagree with the event log about what happened.

    Everything is parsed BEFORE anything is written for the same reason, one
    layer up: a bad date is refused without towerkit being asked at all.
    """
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    form = await request.form()
    typed = {key: str(form.get(key, "")) for key, _ in _POLICY_FORM}

    def refused(message: str) -> HTMLResponse:
        # WHAT WAS TYPED, not what is stored: a refused save changed nothing
        # and the reader must not have to retype eight good fields to fix one.
        return _policy_form(request, ref, placement_id, layer, typed, error=message)

    # --- parse everything first ------------------------------------------
    layer_changes: dict[str, Any] = {}
    tower_changes: list[tuple[str, str, Any]] = []
    for key, seam in _POLICY_FORM:
        field = _policy_field(request, key, seam)
        raw = typed[key]
        try:
            if seam == _POLICY_LAYER:
                layer_changes[key] = parse_value(field, raw)
            else:
                kind, _, name = key.partition(".")
                entry = _field_entry(kind, name)
                text = checked_option(field, raw) if (
                    field.kind == "select" and raw.strip()
                ) else raw
                tower_changes.append((kind, name, towerfields.to_wire(entry, text)))
        except (ValueError, towerfields.FieldRefused) as exc:
            # THE FIELD'S OWN LABEL, in front of the sentence. One message
            # above nine boxes has to say WHICH box, or the reader is left
            # comparing what they typed against a refusal that names none of
            # it.
            return refused(f"{field.label}: {exc}")

    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=f"recorded the policy on {layer['name']}",
            # ONE CYCLE, NOT NINE — see the docstring for which property this
            # buys and which one `program_files.write` was already giving.
            mutate=lambda: sync.record_layer_policy(
                conn, placement.id, layer_id,
                layer_fields=layer_changes, tower_fields=tower_changes,
            ),
            open_batch=_open_batch_web,
        )
    except program_files.ProgramWriteRefused as exc:
        return refused(str(exc))
    except Exception as exc:  # a refused write is a message, never a 500
        return refused(str(exc))

    forget_program_reads(request)
    # THE WHOLE SECTION. Nine fields have moved, three of them figures the
    # tower and the header print, so nothing smaller is honest.
    return _panel(request, ref, org, placement_id, selected=layer_id)


def _split_form(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    lines: list[dict[str, Any]],
    error: str | None = None, typed: dict[str, str] | None = None,
) -> HTMLResponse:
    """Split-by-line: the same band twice, the lines divided, the premium
    division typed and totalled. The amounts arrive EMPTY — a figure this
    surface pre-filled is a figure nobody checked (data-entry integrity),
    and towerkit has no apportioning rule to borrow."""
    return TEMPLATES.TemplateResponse(
        request, "account/_split_form.html",
        {
            "action": f"/accounts/{ref}/program/{placement_id}/layers/{layer['id']}/split",
            "layer": layer,
            "premium": (
                format_cents(layer["premium_cents"])
                if layer["premium_cents"] is not None
                else None
            ),
            "lines": lines,
            "error": error,
            "typed": typed or {},
        },
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/split",
    response_class=HTMLResponse,
)
def layer_split_form(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    labels = dict(sync.program_lines(conn, placement_id))
    lines = [
        {"id": lid, "name": labels.get(lid, lid)}
        for lid in layer["applies_to"]
    ]
    return _split_form(request, ref, placement_id, layer, lines)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/split",
    response_class=HTMLResponse,
)
async def layer_split_save(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """Two slabs from one, same band — an add plus a rescope in ONE batch
    (sync.split_layer is one mutation, so the halfway state never touches
    disk). A refusal re-renders the form with everything typed still in it."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    form = await request.form()
    move_lines = [str(value) for value in form.getlist("move_line")]
    new_name = str(form.get("new_name", "")).strip()
    raw_kept = str(form.get("kept_premium", "")).strip()
    raw_moved = str(form.get("moved_premium", "")).strip()
    typed = {
        "new_name": new_name, "kept_premium": raw_kept,
        "moved_premium": raw_moved, "move_line": ",".join(move_lines),
    }
    labels = dict(sync.program_lines(conn, placement_id))
    lines = [
        {"id": lid, "name": labels.get(lid, lid)}
        for lid in layer["applies_to"]
    ]

    def refused(message: str) -> HTMLResponse:
        return _split_form(
            request, ref, placement_id, layer, lines, error=message, typed=typed,
        )

    money = _LAYER_CELLS["premium_cents"]
    try:
        kept = int(parse_value(money, raw_kept)) if raw_kept else None
        moved = int(parse_value(money, raw_moved)) if raw_moved else None
    except ValueError as exc:
        return refused(str(exc))
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=f"split {layer['name']} by line",
            mutate=lambda: sync.split_layer(
                conn, placement_id, layer_id,
                move_line_ids=move_lines, new_name=new_name,
                kept_premium_cents=kept, moved_premium_cents=moved,
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return refused(str(exc))
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id, selected=layer_id)


@router.post(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/layers",
    response_class=HTMLResponse,
)
async def stack_insert(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    """Put a slab into a line's stack. Position decides the attachment.

    NO ATTACHMENT FIELD IS ACCEPTED, and that is the feature: a typed
    attachment is how two slabs come to share one. `anchor` names the slab this
    one goes above or below, and `""` means the bottom of the line.

    The anchor arrives in the BODY, so it is checked against THIS placement's
    own layers — an id in a body is only checked if somebody checks it, which
    is the hole `forms.spec.checked_option` exists to close.

    A REFUSAL KEEPS THE TYPING (spec, section 2): it re-renders the insert
    form fragment with the message and the typed values, in the form host it
    was posted from. Success answers the one placement's section, retargeted
    (`_panel`) — the write changed one program, so rebuilding every placement
    on the account was never honest.
    """
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    anchor = str(form.get("anchor", "")).strip() or None
    position = str(form.get("position", "above"))
    kind = str(form.get("kind", "layer"))
    raw_limit = str(form.get("limit_cents", ""))
    typed = {
        "name": name, "limit_cents": raw_limit, "position": position,
        "anchor": anchor or "", "kind": kind,
    }

    rows = sync.layer_details(conn, placement_id)
    anchor_row = next((row for row in rows if row["id"] == anchor), None)

    def refused(message: str) -> HTMLResponse:
        subject = anchor_row or (rows[0] if rows else {"id": "", "name": ""})
        return _insert_form(
            request, ref, placement_id, subject,
            position if position in ("above", "below") else "above",
            line_id=line_id, error=message, typed=typed,
        )

    if anchor is not None and anchor_row is None:
        return refused(f"no layer {anchor!r} on {placement.ref} — reload the tab")
    if not name:
        return refused("a slab needs a name")
    if kind not in ("layer", "buffer"):
        return refused(f"kind must be 'layer' or 'buffer', not {kind!r}")
    try:
        limit_cents = int(parse_value(_LAYER_CELLS["limit_cents"], raw_limit) or 0)
    except ValueError as exc:
        return refused(str(exc))

    try:
        program_files.write(
            conn, placement,
            tool="program_layer_add",
            summary=(
                f"inserted {name} on {line_id}"
                if kind != "buffer"
                else f"declared a buffer on {line_id}"
            ),
            mutate=lambda: sync.insert_layer(
                conn, placement_id, line_id=line_id, anchor_layer_id=anchor,
                position=position, name=name, limit_cents=limit_cents,
                buffer=kind == "buffer",
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return refused(str(exc))
    forget_program_reads(request)
    return _panel(
        request, ref, org, placement_id,
        selected=anchor if anchor is not None else None,
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}"
    "/markets/{index}/share-preview",
    response_class=HTMLResponse,
)
async def market_share_preview(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int
) -> HTMLResponse:
    """The write preview — a share typed in the worksheet projects before it
    saves: the change, the resulting signed figure, the dollars still open,
    and where it writes. THIS BREAKS BLUR-COMMITS FOR THE SHARE INPUT ON
    PURPOSE (the hand-off records the decision beside the rule): a share
    edit moves the one figure the whole worksheet exists to close, so it is
    shown before it lands. Save commits through the same cell route a
    blur-commit would have used; Discard re-selects the layer and nothing
    was ever written. Cell fields everywhere else keep blur-commit."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    field = _market_field("share_pct")
    form = await request.form()
    raw = str(form.get("share_pct", ""))
    commit = str(form.get("commit", "")) == "1"
    _, closed = _view_state(request)
    select_url = (
        f"/accounts/{ref}/program/{placement_id}/worksheet"
        f"?layer={layer_id}&closed={_closed_param(closed)}"
    )
    context: dict[str, Any] = {
        "carrier": seat["carrier"],
        "was_pct": f"{seat['share_pct']:g}",
        "typed": raw,
        "file_name": (
            Path(placement.program_path).name if placement.program_path else ""
        ),
        "select_url": select_url,
        "preview_action": (
            f"{_market_base(ref, placement_id, layer_id, index)}/share-preview"
        ),
    }
    try:
        share_bps = int(parse_value(field, raw) or 0)
        if not raw.strip():
            raise ValueError(f"{field.label} is required")
    except ValueError as exc:
        return TEMPLATES.TemplateResponse(
            request, "account/_share_preview.html",
            {**context, "preview": {"ok": False, "errors": [str(exc)]}},
        )
    if commit:
        # Save goes through THIS route, not the bare cell route: the cell
        # route's refusal is a <td> editor with no retarget, which inside the
        # preview's own host renders as a garbled fragment in the Save button
        # (review C1). Here a refusal re-renders the preview block — the
        # shape this host holds — with towerkit's words and no Save.
        try:
            program_files.write(
                conn, placement,
                tool="program_layer_edit",
                summary=f"corrected {seat['carrier']} on {layer['name']}",
                mutate=lambda: sync.update_participant(
                    conn, placement_id, layer_id, seat["carrier"],
                    share_bps=share_bps,
                ),
                open_batch=_open_batch_web,
            )
        except Exception as exc:
            return TEMPLATES.TemplateResponse(
                request, "account/_share_preview.html",
                {**context, "preview": {"ok": False, "errors": [str(exc)]}},
            )
        forget_program_reads(request)
        return _panel(
            request, ref, org, placement_id,
            selected=layer_id, refocus=f"{layer_id}:market-{index}-share_pct",
        )
    result = sync.share_preview(conn, placement_id, layer_id, seat["carrier"], share_bps)
    if result.get("ok"):
        result["pct"] = f"{result['share_pct']:g}"
        result["signed"] = f"{result['signed_pct']:g}"
        result["placed"] = result["signed_pct"] >= 100
        result["open_limit"] = (
            format_cents(result["open_limit_cents"])
            if result["open_limit_cents"]
            else None
        )
    return TEMPLATES.TemplateResponse(
        request, "account/_share_preview.html",
        {**context, "preview": result},
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/applies-to/confirm",
    response_class=HTMLResponse,
)
def applies_to_confirm(
    request: Request, ref: str, placement_id: str, layer_id: str, line: str
) -> HTMLResponse:
    """The consequence, stated before it is done (design 3B): turning a line
    off a spanning slab is a decision, not a slip, so the pane shows what
    that line would be left with — a dry run of the SAME set_applies_to call
    the commit makes (sync.rescope_preview), never a second derivation.
    Writes nothing; only the confirm's own POST does."""
    org = _org(request, ref)
    conn = _conn(request)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    current = list(layer["applies_to"])
    if line not in current:
        raise HTTPException(status_code=404, detail=f"{layer['name']} does not cover {line!r}")
    wanted = [lid for lid in current if lid != line]
    result = sync.rescope_preview(conn, placement_id, layer_id, wanted)
    for item in result.get("dropped", []):
        item["left_with"] = format_cents(item["left_with_cents"])
    base = f"/accounts/{ref}/program/{placement_id}/layers/{layer_id}"
    return TEMPLATES.TemplateResponse(
        request, "account/_rescope_confirm.html",
        {
            "layer": layer,
            "line": line,
            "preview": result,
            "premium": (
                format_cents(result["premium_cents"])
                if result.get("premium_cents") is not None
                else None
            ),
            "applies_action": f"{base}/applies-to",
            "split_url": f"{base}/split",
        },
    )


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
    form = await request.form()
    line = str(form.get("line", ""))
    intent = str(form.get("intent", ""))
    current = list(layer["applies_to"])
    if intent == "drop" and line not in current:
        # The confirm was stale — another window already dropped it. A
        # toggle here would silently RE-WIDEN the slab under a button that
        # says Drop (review C26); a refusal says what happened instead.
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=(
                f"{line} is no longer on {layer['name']} — it was already "
                f"dropped; nothing was written"
            ),
        )
    wanted = (
        [lid for lid in current if lid != line]
        if line in current
        else [*current, line]
    )
    if not wanted:
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=(
                f"{layer['name']} must cover at least one line — add another first"
            ),
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
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=str(exc),
        )
    forget_program_reads(request)
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    return _panel(
        request, ref, org, placement_id,
        refocus=f"{layer_id}:applies_to", selected=layer_id,
    )


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
            return _panel(
                request, ref, org, placement_id, selected=layer_id,
                worksheet_error=str(exc),
            )
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
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=str(exc),
        )
    forget_program_reads(request)
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    return _panel(
        request, ref, org, placement_id,
        refocus=f"{layer_id}:statutory", selected=layer_id,
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/policy",
    response_class=HTMLResponse,
)
async def policy_link_save(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """Put this layer on the same POLICY as another, or take it off.

    Workers' compensation is why it exists: Part A is statutory and Part B
    carries a dollar limit, so towerkit cannot make them one layer, and until
    now nothing said they were one policy. The write is towerkit's own
    `link_policy` — which joins rather than assigns and refuses to merge two
    populated policies silently — through the same program-file seam every
    other structure edit uses, so it is validated, snapshotted and revertible.

    AN EMPTY CHOICE IS A REAL ANSWER and unlinks. The select renders a blank
    option for exactly that reason; treating empty as "no change" would make
    unlinking unreachable from the only control that offers it.

    `checked_option` is the guard: the picker's own options are the authority,
    and markup constrains a mouse and nothing else.
    """
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    other_id = str((await request.form()).get("policy_group", "")).strip()
    siblings = [
        (str(row["id"]), str(row["name"]))
        for row in sync.layer_details(conn, placement_id)
        if str(row["id"]) != layer_id
    ]
    if other_id:
        # THE PICKER'S OWN OPTIONS ARE THE AUTHORITY, rebuilt server-side from
        # the same query that built them for the GET — which is also the scope
        # check: a layer id from another placement is simply not in this list.
        try:
            checked_option(
                Field(
                    "policy_group", "same policy as", "select",
                    options=tuple((name, value) for value, name in siblings),
                ),
                other_id,
            )
        except ValueError as exc:
            return _panel(
                request, ref, org, placement_id, selected=layer_id,
                worksheet_error=str(exc),
            )
    partner = dict(siblings).get(other_id, "")
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_edit",
            summary=(
                f"{layer['name']} shares a policy with {partner}"
                if other_id
                else f"{layer['name']} is its own policy"
            ),
            mutate=lambda: (
                sync.link_policy(conn, placement_id, layer_id, other_id)
                if other_id
                else sync.unlink_policy(conn, placement_id, layer_id)
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=str(exc),
        )
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id, selected=layer_id)


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
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=str(exc),
        )
    forget_program_reads(request)
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    return _panel(
        request, ref, org, placement_id,
        refocus=f"{layer_id}:follows", selected=layer_id,
    )


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
    (sync.remove_layer, D2). The layers above stay put: sliding them down to
    close the tower would silently change what the client is covered for, so
    the write leaves an open band and towerkit reports it as a `line-gap`
    WARNING, not a refusal (towerkit/validate.py, 2026-08-21) — the confirm
    says this before the click. A genuine refusal (a different error) still
    re-renders the confirm in place with the message."""
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
    forget_program_reads(request)
    _, fresh = _owned_layer(request, org, placement_id, layer_id)
    if key in _DETAIL_KEYS:
        # A DETAILS-ROW FIELD ANSWERS WITH ITS OWN CELL. Policy number and the
        # two policy dates are not columns in the table above, so nothing in
        # the panel moves when they change — and answering with the panel would
        # replace the section, which CLOSES the details row the user is still
        # working in. Losing the row you are typing in on every save is the
        # same complaint as losing the whole program, one size down.
        return _layer_display_cell(request, ref, placement_id, fresh, key)

    # THE CELL, PLUS THE WHOLE PANEL OUT OF BAND. A layer write can move rows
    # this cell knows nothing about: write_through runs heal_follows, which
    # re-seats the attachment of every follows-underlying layer above the one
    # edited. Swapping back only the edited cell left those rows showing the
    # pre-write attachment — a tower with a gap or an overlap that does not
    # exist in the file, and the next edit made from that row would be made
    # against a number that is already gone (found by review, 2026-08-19).
    return _panel(request, ref, org, placement_id, refocus=f"{layer_id}:{key}")


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
    conn: sqlite3.Connection,
    placement_id: str,
    layers: list[dict[str, Any]] | None = None,
    for_new_line: bool = False,
) -> tuple[Field, ...]:
    """A new layer's facts. `name` and `limit` are required by sync.add_layer;
    premium is optional because a layer is routinely placed before it is
    priced.

    NO ATTACHMENT FIELD, same rule as the stack editor a few lines below this
    one on the same panel (whole-branch review finding 2, 2026-08-21): this
    form still took a typed `attach_cents` after the stack editor shipped,
    which is the exact mechanism ("add a layer" plus an attachment box) that
    drew two D&O excess layers on top of each other in the first place. This
    form is not superseded outright — it is the only web control that can add
    a layer across ALL of a multi-line program's lines in one call, or price
    one at creation — so it stays, minus the one input that made the overlap
    typeable. `sync.add_layer(..., attach_cents=None, ...)` leaves towerkit's
    own suggested-attach (the top of the existing stack for these lines)
    standing, the same "position decides" rule, just for a layer that can span
    more than one line.

    `line` is REQUIRED and asked, never guessed (F5): this form used to pass
    line_ids=[] and towerkit silently defaulted the new layer onto the FIRST
    line — on a multi-line program the web wrote different data than the TUI
    for the same intent, invisibly. Empty options means the program has no
    lines; the caller refuses before rendering a form that cannot succeed.

    A LINE OF COVERAGE CAN BE MADE FROM HERE (Grant, 2026-08-24). The picker
    used to offer only lines that already existed, so a broker starting a
    program — one placeholder line, nothing else — had nowhere to say
    "Employers Liability is its own line of coverage" and typed it into the
    LAYER name instead, aimed at the placeholder. The sentinel is the answer,
    and the write behind it is one mutation (`sync.add_line` with a layer
    spec), so a refused layer leaves no stranded line.

    A STATUTORY LINE IS LABELLED, because it is the dead end that produced
    this whole change: statutory cover owns its whole column, so towerkit
    refuses any other layer on it. The option still exists — the refusal is
    towerkit's to give — but a reader can see where not to aim before they
    aim there.
    """
    lines = sync.program_lines(conn, placement_id)
    statutory = {
        lid
        for row in (layers or [])
        if row["statutory"]
        for lid in row["applies_to"]
    }
    options = tuple(
        (f"{name} — statutory, whole column" if line_id in statutory else name, line_id)
        for line_id, name in lines
    )
    if len(lines) > 1:
        options = (("all lines", "__all__"), *options)
    # The label states the CONSEQUENCE on a program still carrying its
    # scaffold: filling the placeholder is what the broker means there, and a
    # second column beside an unfilled one is never what they meant.
    fill = _scaffold_placeholder_name(conn, placement_id)
    options = (
        (f"fill {fill} — a new line of coverage…" if fill else "new line of coverage…",
         NEW_LINE),
        *options,
    )
    picker = Field("line", "applies to", "select", options, required=True)
    new_line = Field(
        "new_line_name", "line of coverage", placeholder="Employers Liability"
    )
    if for_new_line:
        # THE LEVELS IN THE ORDER THEY NEST when that is what is being made:
        # the line of coverage, then the first layer inside it. The picker
        # stays, last, as the way back out to an existing line — arriving
        # here does not lock the decision in.
        return (
            new_line,
            replace(_LAYER_CELLS["name"], label="first layer"),
            _LAYER_CELLS["limit_cents"],
            _LAYER_CELLS["premium_cents"],
            picker,
        )
    return (
        _LAYER_CELLS["name"],
        picker,
        new_line,
        _LAYER_CELLS["limit_cents"],
        _LAYER_CELLS["premium_cents"],
    )


# The picker value that means "not one of these — a new one". A sentinel, not
# a storable id, so every reader of `values["line"]` has to branch on it;
# checked server-side like any other option, because markup constrains a
# mouse and nothing else.
NEW_LINE = "__new__"


def _scaffold_placeholder_name(
    conn: sqlite3.Connection, placement_id: str
) -> str | None:
    """The name of the placeholder line, when this program is still exactly
    what `scaffold_program` wrote — otherwise None.

    The predicate is `sync.is_untouched_scaffold`, never a second reading of
    it here: the surface's job is to SAY what the write will do, and if the
    two disagreed the form would promise one thing and the file would get
    another."""
    program = sync.linked_program(conn, placement_id).program
    if program is None or not sync.is_untouched_scaffold(program):
        return None
    return str(program.lines[0].name)


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


def _panel(
    request: Request,
    ref: str,
    org: Any,
    placement_id: str,
    *,
    refocus: str | None = None,
    selected: str | None = None,
    worksheet_error: str | None = None,
) -> HTMLResponse:
    """Re-render this placement's whole panel, AND retarget the swap onto it.

    A program write can move more than the cell that caused it — a market's
    share changes the layer's signed percentage, heal_follows re-seats every
    layer above the one edited, and adding a layer changes the table — so no
    single-cell swap is honest here.

    THE PANEL IS THE PRIMARY SWAP, never a sibling riding out of band behind
    a `<td>`. Every write route used to answer with `cell_html + panel_html`,
    the cell targeted in place and the panel marked hx-swap-oob. That shape
    is destroyed by HTML fragment parsing before htmx ever sees it: htmx picks
    its parse context from the response's FIRST tag (`makeFragment`), so a
    response opening with `<td>` is parsed inside
    `<table><tbody><tr>…</tr></tbody></table>` — and a `<section>` is not
    table content, so the parser foster-parents it out of the fragment htmx
    returns. Confirmed in Chrome on 2026-08-20: saving a layer premium left
    `section.program` standing with its `.table-scroll` empty and every one of
    the 14 layer rows gone, the write itself having succeeded. Grant hit this
    as "I changed a limit and the program disappeared; the change saved but I
    had to refresh". The `<tr>`-first responses (the details row) failed the
    other way — the section was dropped silently, so the panel never refreshed
    at all and the next edit was made against stale numbers.

    HX-Retarget/HX-Reswap say the same thing without a second element in the
    response: whatever the trigger's own target was, this answer replaces the
    whole `#program-<id>` section. One element, top level, no table context to
    misparse. forms_render.py already documented the single-element half of
    this rule ("a `<td>` outside a table-row ancestor is silently dropped");
    what was missing was that the rule binds the whole RESPONSE, not just the
    fragment — asserted now by tests/test_conventions.py.

    `refocus` is "<layer_id>:<field_key>" (or "cell:<field_key>" for a header
    cell) and rides back as `data-refocus`; inline-cell.js puts the caret back
    on the cell the user just left, so replacing the section does not cost
    them their place in the table.

    `selected` names the layer whose worksheet the section shows — a write
    made from the worksheet passes the layer it addressed, everything else is
    recovered from the browser URL (`_view_state`), so a save never throws
    the broker back to the first layer.

    `worksheet_error` is a structural refusal's message, rendered at the top
    of the worksheet pane with the file untouched — a refusal answers 200
    with this section alone, retargeted here exactly as a success would be,
    so it is still ONE RESPONSE, ONE TOP-LEVEL ELEMENT.
    """
    forget_program_reads(request)
    conn = _conn(request)
    placement = placements_repo.get(conn, placement_id)
    response = HTMLResponse(
        _section_html(
            request, ref, org, placement,
            refocus=refocus, selected=selected, worksheet_error=worksheet_error,
        )
    )
    response.headers["HX-Retarget"] = f"#program-{placement_id}"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


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
    fields = _layer_add_fields(
        conn, placement_id, layers_for(request, conn, placement_id),
        # A refusal re-renders the form the broker is LOOKING AT, in the order
        # they opened it — reshuffling the fields under a message is its own
        # small betrayal of commit-in-place.
        for_new_line=raw.get("line") == NEW_LINE,
    )
    try:
        values = _parsed(fields, raw)
        if values["line"] == NEW_LINE and not values["new_line_name"]:
            raise ValueError("name the line of coverage")
    except ValueError as exc:
        return _refused_form(request, fields, action, "new layer", str(exc), raw)

    if values["line"] == NEW_LINE:
        # ONE MUTATION: the line and its layer go in together, so a refused
        # layer leaves no stranded line behind (sync.add_line carries the
        # reasoning). On a program still carrying its scaffold this FILLS the
        # placeholder instead of adding beside it — which the picker's own
        # option label said it would before the broker chose it.
        write = lambda: sync.add_line(  # noqa: E731
            conn, placement_id, values["new_line_name"],
            layer_name=values["name"],
            limit_cents=values["limit_cents"],
            premium_cents=values["premium_cents"],
        )
        summary = (
            f"added {values['new_line_name']} with layer {values['name']}"
        )
    else:
        line_ids = (
            [line_id for line_id, _ in all_lines]
            if values["line"] == "__all__"
            else [values["line"]]
        )
        write = lambda: sync.add_layer(  # noqa: E731
            conn, placement_id, values["name"], line_ids,
            attach_cents=None,  # position decides — see _layer_add_fields
            limit_cents=values["limit_cents"],
            premium_cents=values["premium_cents"],
        )
        summary = f"added layer {values['name']}"
    try:
        program_files.write(
            conn, placement,
            tool="program_layer_add",
            summary=summary,
            mutate=write,
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _refused_form(request, fields, action, "new layer", str(exc), raw)
    forget_program_reads(request)
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
    return _panel(request, ref, org, placement_id, selected=layer_id)


def _policy_link_options(
    layers: list[dict[str, Any]], layer_id: str, line_named: dict[str, str]
) -> list[tuple[str, str]]:
    """The layers this one can be told it shares a policy with, as
    (id, label) — self excluded.

    THE LABEL SAYS WHICH LAYER when the name alone does not, and the rule for
    that lives in `sync.qualified_layer_names`, not here: the pipeline's bind
    offer collides the same way and a second copy would be fixed once
    (2026-08-24).
    """
    named = sync.qualified_layer_names(layers, line_named)
    return [
        (str(other["id"]), named[str(other["id"])])
        for other in layers
        if str(other["id"]) != layer_id
    ]


def _market_field(key: str) -> Field:
    field = next((f for f in PARTICIPANT_FIELDS if f.key == key), None)
    if field is None:
        raise HTTPException(status_code=404, detail=f"{key} is not an editable market field")
    return field


def _participant_fields(conn: sqlite3.Connection) -> tuple[Field, ...]:
    """The ADD row's fields: carrier and share, with the carrier completing
    from existing market names — the add form's copy of the rule
    `_market_field_for_editor` applies to the carrier cell.

    `premium_cents` is deliberately NOT here. A market is bound at a share;
    its own premium is a correction made afterwards, if at all — and the
    first one on a layer states every other seat and moves the layer's
    premium too, which is not something to do from an add row while the
    shares are still being typed."""
    return tuple(
        _market_field_for_editor(conn, f.key)
        for f in PARTICIPANT_FIELDS
        if f.key != "premium_cents"
    )


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
    """What the cell SHOWS. The share prints with its % because the cell sits
    beside a Signed column that does too — a bare number would read as money.

    The premium prints the house em dash when there is none to print, which
    on this cell means the LAYER has no premium either: a seat with no stated
    premium of its own still shows its share of the layer's."""
    if key == "carrier":
        return str(seat["carrier"])
    if key == "premium_cents":
        cents = seat["premium_cents"]
        return format_cents(cents) if cents is not None else "—"
    return f"{seat['share_pct']:g}%"


def _market_prefill(key: str, seat: dict[str, Any]) -> str:
    """What the EDITOR pre-fills. The seat carries share_pct as a PERCENT and
    the share parser reads a percent, so the number passes through verbatim.
    The old mini-form fed this percent into initial_text, whose share kind
    formats BPS — a 40% seat pre-filled '0.4', and an unedited save would
    have cut the share 100x. Never route a percent through a bps formatter.

    THE PREMIUM PRE-FILLS ONLY WHAT IS STATED. A derived figure is arithmetic,
    not an answer, and pre-filling it would turn opening the cell to read it
    into a way of accidentally stating every other seat on the layer — the
    "never pre-fill a figure that comes off a document" rule, with teeth."""
    if key == "carrier":
        return str(seat["carrier"])
    if key == "premium_cents":
        # `initial_text`'s own money rule, not a second spelling of it: plain
        # cents with no dollar sign, because that is exactly what the parser
        # accepts back.
        return (
            initial_text(_market_field(key), seat["premium_cents"])
            if seat.get("premium_stated")
            else ""
        )
    return f"{seat['share_pct']:g}"


_MARKET_CELL_CLASS = {
    "carrier": "market-cell",
    "share_pct": "market-cell market-share",
    "premium_cents": "market-cell num mono",
}


def _market_cell_class(key: str, seat: dict[str, Any]) -> str:
    """A DERIVED premium is greyed and a STATED one is not: "this is what the
    market charges" and "this is the layer's premium divided by the share"
    are different claims, and a broker checking a split has to be able to see
    which one a figure is."""
    css = _MARKET_CELL_CLASS[key]
    if key == "premium_cents" and not seat.get("premium_stated"):
        css += " derived"
    return css


def _market_row_html(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    index: int, seat: dict[str, Any],
) -> str:
    """One market as a participation-table row: two inline cells (carrier,
    share — the same editing grammar as the layer cells above), then the
    DERIVED dollar columns computed once in sync.layer_details, and the
    take-off control. A row, not a chip, because the worksheet's table is
    where shares get to 100% (design 1C, 2026-08-24)."""
    def cell(key: str) -> str:
        return render_cell_display(
            request, _market_field(key), _market_display_value(key, seat),
            _market_cell_action(ref, placement_id, layer["id"], index, key),
            tag="td", extra_class=_market_cell_class(key, seat),
        )

    template = TEMPLATES.env.get_template("account/_market_row.html")
    return template.render(
        base=_market_base(ref, placement_id, layer["id"], index),
        seat=seat, layer_name=layer["name"],
        carrier_cell=cell("carrier"),
        # The share input's pre-fill: the percent verbatim, never through the
        # bps formatter (_market_prefill carries the history).
        share=_market_prefill("share_pct", seat),
        host=f"#ws-host-{placement_id}",
        limit=format_cents(seat["limit_cents"]),
        premium_cell=cell("premium_cents"),
        # A carrier the book does not know is a string in a file and nothing
        # more — it misses exposure, hit rate and the market dossier. Said
        # HERE, where the carrier is, rather than left to be noticed on a tab
        # that never mentioned it (Grant, 2026-08-20).
        unlinked=seat["carrier"] not in _known_carriers(request),
    )


def _known_carriers(request: Request) -> set[str]:
    """Memoised per request: the chip asks once per seat, and a busy tower has
    dozens."""
    cached = getattr(request.state, "known_carriers", None)
    if cached is None:
        cached = sync.known_carriers(_conn(request))
        request.state.known_carriers = cached
    return cached


def _market_display_cell(
    request: Request, ref: str, placement_id: str, layer: dict[str, Any],
    index: int, seat: dict[str, Any], key: str,
) -> HTMLResponse:
    return HTMLResponse(
        render_cell_display(
            request, _market_field(key), _market_display_value(key, seat),
            _market_cell_action(ref, placement_id, layer["id"], index, key),
            tag="td", extra_class=_market_cell_class(key, seat),
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
            error=error, tag="td", extra_class=_market_cell_class(key, seat),
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


@router.get(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}",
    response_class=HTMLResponse,
)
def market_chip(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int
) -> HTMLResponse:
    """The whole row — what the remove confirm's [keep] restores."""
    org = _org(request, ref)
    _, layer = _owned_layer(request, org, placement_id, layer_id)
    seat = _seated(layer, index)
    return HTMLResponse(_market_row_html(request, ref, placement_id, layer, index, seat))


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
    form = await request.form()
    raw = str(form.get(key, ""))
    try:
        value = parse_value(field, raw)
        if field.required and value in (None, ""):
            raise ValueError(f"{field.label} is required")
    except ValueError as exc:
        return _market_editor_cell(
            request, conn, ref, placement_id, layer, index, seat, key, str(exc), raw
        )

    if key == "premium_cents":
        return _market_premium_save(
            request, conn, ref, org, placement, layer, index, seat, value, raw,
            commit=str(form.get("commit", "")) == "1",
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
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id, refocus=f"{layer_id}:market-{index}-{key}")


def _market_premium_save(
    request: Request,
    conn: sqlite3.Connection,
    ref: str,
    org: Any,
    placement: Any,
    layer: dict[str, Any],
    index: int,
    seat: dict[str, Any],
    value: Any,
    raw: str,
    commit: bool = False,
) -> HTMLResponse:
    """A market's own premium — the one cell whose write moves numbers the
    broker did not type.

    THE FIRST OVERRIDE ON A LAYER PREVIEWS. towerkit's rule is that stating
    one seat states them all (each at the figure it was already showing) and
    the layer's premium becomes their sum, so the first one changes every row
    of the table and the figure above it. That is shown before it lands — the
    same deliberate exception to blur-commit the share cell already makes,
    and for a stronger reason: the share cell moves one number the broker
    typed, this moves three.

    Afterwards it commits IN PLACE like any other cell: every seat is already
    stated, so only the sum moves and the panel re-renders with it.

    BLANK CLEARS THE WHOLE LAYER, back to a premium split by share. That is
    towerkit's all-or-nothing rule, not a web decision, and it confirms first
    — `premium_clear_preview` names every figure being given up and the share
    each seat lands on. This sentence stood for a day while no such route
    existed and the blank went in on blur (2026-08-24); a docstring that
    promises a guard is worth less than no docstring at all.
    """
    layer_id = layer["id"]
    already = any(part.get("premium_stated") for part in layer["participants"])
    # TWO WRITES ON THIS CELL MOVE FIGURES NOBODY TYPED, and both are shown
    # first. Stating the first premium on a layer states every seat and sums
    # them; BLANKING one clears every seat back to a share of the layer's
    # premium — all-or-nothing is towerkit's rule, not a web decision. The
    # clear was the unannounced one, and it is the easier of the two to
    # trigger: blur commits, so a cell tabbed through empty wrote it
    # (2026-08-24). Both docstrings had promised a confirm for as long as
    # there was none.
    previewing = (
        sync.premium_preview(
            conn, placement.id, layer_id, seat["carrier"], int(value)
        )
        if value is not None and not already
        else sync.premium_clear_preview(conn, placement.id, layer_id, seat["carrier"])
        if value is None and already
        else None
    )
    if previewing is not None and not commit:
        if previewing["ok"]:
            return _premium_preview_response(
                request, ref, placement.id, layer, index, seat, previewing, raw
            )
        # A refused preview is a refused write: say it in the cell the broker
        # typed in, with what they typed still there.
        return _market_editor_cell(
            request, conn, ref, placement.id, layer, index, seat,
            "premium_cents", "; ".join(previewing["errors"]), raw,
        )

    try:
        program_files.write(
            conn, placement,
            tool="program_market_premium",
            summary=(
                f"cleared the stated market premiums on {layer['name']}"
                if value is None
                else f"stated {seat['carrier']}'s premium on {layer['name']}"
            ),
            mutate=lambda: sync.set_participant_premium(
                conn, placement.id, layer_id, seat["carrier"],
                None if value is None else int(value),
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _market_editor_cell(
            request, conn, ref, placement.id, layer, index, seat,
            "premium_cents", str(exc), raw,
        )

    forget_program_reads(request)
    return _panel(
        request, ref, org, placement.id,
        refocus=f"{layer_id}:market-{index}-premium_cents",
    )


def _premium_preview_response(
    request: Request,
    ref: str,
    placement_id: str,
    layer: dict[str, Any],
    index: int,
    seat: dict[str, Any],
    preview: dict[str, Any],
    raw: str,
) -> HTMLResponse:
    """The preview, retargeted over the worksheet host.

    ONE RESPONSE, ONE TOP-LEVEL ELEMENT: this answers with the preview alone
    and says where it goes with HX-Retarget, rather than gluing a cell and a
    panel together — a response opening with `<td>` is parsed inside a table
    and anything else in it is foster-parented away before htmx sees it
    (DECISIONS.md, and the layer-premium bug that emptied a whole section).
    """
    base = _market_base(ref, placement_id, layer["id"], index)
    _, closed = _view_state(request)
    html = TEMPLATES.env.get_template("account/_premium_preview.html").render(
        request=request,
        preview=preview,
        # `typed` is what goes back on Save and must round-trip verbatim;
        # `typed_label` is what the sentence SHOWS. A money cell's keystrokes
        # ("1050000.00") read as a machine value beside three formatted
        # figures — the share cell has no such split because a bare percent
        # reads the same either way.
        typed=raw,
        typed_label=next(
            (
                format_cents(row["premium_cents"])
                for row in preview["seats"]
                if row["typed"] and row["premium_cents"] is not None
            ),
            raw,
        ),
        seat=seat,
        commit_action=f"{base}/cell/premium_cents",
        select_url=(
            f"/accounts/{ref}/program/{placement_id}/worksheet"
            f"?layer={layer['id']}&closed={_closed_param(closed)}"
        ),
        format_cents=format_cents,
    )
    response = HTMLResponse(html)
    response.headers["HX-Retarget"] = f"#ws-host-{placement_id}"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


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
    return _panel(request, ref, org, placement_id, selected=layer_id)


# --- the two ghost-row forms --------------------------------------------------


def _panel_refusal(
    request: Request, ref: str, org: Any, placement_id: str, message: str
) -> HTMLResponse:
    """A refusal for a form-host control with no form to re-render — said in
    the form host itself, never a status code htmx would drop.

    ESCAPED BY HAND because this is a hand-built response, not a template:
    Jinja's autoescape never sees it, and a refusal message can carry
    user-controlled text (a line id from the URL path, quoted verbatim by
    sync's refusals) — htmx re-executes swapped script tags (fresh-eyes
    review, phase 4)."""
    from markupsafe import escape

    return HTMLResponse(f'<p class="form-error" role="alert">{escape(message)}</p>')


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
        request,
        _layer_add_fields(conn, placement_id, layers_for(request, conn, placement_id)),
        f"/accounts/{ref}/program/{placement_id}/layers", "new layer",
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/lines/new-layer",
    response_class=HTMLResponse,
)
def line_of_coverage_form(
    request: Request, ref: str, placement_id: str
) -> HTMLResponse:
    """The SAME form, opened with its picker already asking for a new line of
    coverage — one form, one write, one refusal path.

    Not a second form: a line of coverage cannot exist without a layer
    (towerkit reports `line-empty` as an error), so "add a line" and "add its
    first layer" are one act whichever control starts it. A separate form
    would be a second spelling of that act, and the two would drift over
    which fields a first layer takes.
    """
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    if not placement.program_path:
        return _panel_refusal(
            request, ref, org, placement_id,
            f"{placement.ref} has no program file linked — scaffold one first",
        )
    fields = _layer_add_fields(
        conn, placement_id, layers_for(request, conn, placement_id),
        for_new_line=True,
    )
    return TEMPLATES.TemplateResponse(
        request, "account/_program_form.html",
        {
            "fields": fields,
            "action": f"/accounts/{ref}/program/{placement_id}/layers",
            "title": "new line of coverage",
            "values": {"line": NEW_LINE},
        },
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


def _new_program_dest(conn: Any, org: Any, period_from: str) -> Path | None:
    """Where the new file goes — the same rule `_scaffold_destination` uses
    (first configured root, `<two-word-slug>-<period year>.json`), minus the
    placement that does not exist yet."""
    roots = sync.configured_roots(conn)
    if not roots:
        return None
    slug = "-".join(org.name.lower().split()[:2]).strip(",.")
    year = (period_from or "")[:4] or "new"
    return Path(roots[0]) / f"{slug}-{year}.json"


@router.get("/accounts/{ref}/program/new", response_class=HTMLResponse)
@router.post("/accounts/{ref}/program/new", response_class=HTMLResponse)
async def new_program_page(request: Request, ref: str) -> Any:
    """Starting a program — one worksheet, then the file (design 2B).

    Replaces the two-step '+ New program' then 'Create a program file': the
    source cards (copy last year / start empty), the label-rail form, the
    first-layers table where EACH ROW SEATS ON THE LAST (the attachment is
    the running total, never typed), and the what-will-be-written rail whose
    Checks are towerkit's own validation of the composed program — run live
    on every re-render, before anything exists.

    CLASSIC FORM POSTS, deliberately: every act (stack a layer, remove one,
    create) re-renders this page with everything typed still in it, so a
    refusal never costs the typing. towerkit validates before anything is
    saved; a refusal creates NOTHING — no placement, no file.
    """
    org = _org(request, ref)
    conn = _conn(request)
    siblings = [
        p for p in placements_repo.for_org(conn, org.id) if p.program_path
    ]
    latest = max(siblings, key=lambda p: p.period_to, default=None)

    form = await request.form() if request.method == "POST" else {}
    act = str(form.get("act", ""))
    getlist = form.getlist if hasattr(form, "getlist") else lambda _k: []

    source_kind = str(form.get("source", "copy" if latest else "empty"))
    if source_kind == "copy" and latest is None:
        source_kind = "empty"
    next_from = latest.period_to if latest else ""
    name = str(
        form.get("name", latest.program_name if latest else "")
    ).strip()
    period_from = str(form.get("period_from", next_from)).strip()
    period_to = str(form.get("period_to", "")).strip()
    if not period_to and period_from:
        try:
            from datetime import date as _date

            start = _date.fromisoformat(period_from)
            period_to = start.replace(year=start.year + 1).isoformat()
        except ValueError:
            period_to = ""
    status = str(form.get("status", "prospective"))
    lines_text = str(form.get("lines", ""))
    stacked = [
        {"line": line, "name": lname, "limit": raw}
        for line, lname, raw in zip(
            getlist("stk_line"), getlist("stk_name"), getlist("stk_limit"),
            strict=False,
        )
    ]
    error: str | None = None
    typed_row = {"name": "", "limit": "", "line": ""}

    if act == "stack":
        new_name = str(form.get("new_name", "")).strip()
        raw_limit = str(form.get("new_limit", "")).strip()
        new_line = str(form.get("new_line", "")).strip()
        try:
            if not new_name:
                raise ValueError("a layer needs a name")
            if not new_line:
                raise ValueError(
                    "pick the line this layer covers — state the lines above first"
                )
            parse_value(_LAYER_CELLS["limit_cents"], raw_limit)
            stacked.append({"line": new_line, "name": new_name, "limit": raw_limit})
        except ValueError as exc:
            error = str(exc)
            # A REFUSAL KEEPS THE TYPING — the add row's own values ride back
            # (review C10)
            typed_row = {"name": new_name, "limit": raw_limit, "line": new_line}
    elif act.startswith("unstack:"):
        index = int(act.split(":", 1)[1])
        if 0 <= index < len(stacked):
            stacked.pop(index)

    line_names = [part.strip() for part in lines_text.split(",") if part.strip()]

    def parsed_layers() -> list[dict[str, Any]]:
        return [
            {
                "line": row["line"],
                "name": row["name"],
                "limit_cents": int(parse_value(_LAYER_CELLS["limit_cents"], row["limit"]) or 0),
            }
            for row in stacked
        ]

    source_program = None
    if source_kind == "copy" and latest is not None:
        source_program = linked_for(request, conn, latest.id).program

    dest = _new_program_dest(conn, org, period_from)
    composed = None
    checks: Any = None
    if name and period_from and period_to and (
        source_program is not None or line_names
    ):
        try:
            composed, checks = sync.compose_program(
                insured=org.name, program_name=name, status=status,
                period_from=period_from, period_to=period_to,
                line_names=line_names, layers=parsed_layers(),
                source=source_program,
            )
        except ValueError as exc:
            error = error or str(exc)

    if act == "create" and error is None:
        try:
            # the chip row's own options are the authority, re-checked
            # server-side — markup constrains a mouse and nothing else
            checked_option(_PLACEMENT_CELLS["status"], status)
        except ValueError as exc:
            error = str(exc)
    if act == "create" and error is None:
        if dest is None:
            error = "no program roots configured — set one with bookctl roots"
        elif composed is None:
            error = (
                "name the program, its period and at least one line of coverage first"
            )
        else:
            created, diags = sync.create_program(
                conn, org.id, dest,
                program_name=name, status=status,
                period_from=period_from, period_to=period_to,
                line_names=line_names, layers=parsed_layers(),
                source_path=(
                    sync.program_file(conn, latest)
                    if source_program is not None and latest is not None
                    else None
                ),
            )
            if created is not None:
                from fastapi.responses import RedirectResponse

                forget_program_reads(request)
                return RedirectResponse(
                    f"/accounts/{ref}/program", status_code=303
                )
            error = "; ".join(d.message for d in diags.errors) or "refused"

    # The running attachment per line — display only, derived the same way
    # compose_program seats the rows; shown as text, never an input.
    running: dict[str, int] = {}
    stack_rows = []
    for row in stacked:
        try:
            cents = int(parse_value(_LAYER_CELLS["limit_cents"], row["limit"]) or 0)
        except ValueError:
            cents = 0
        floor = running.get(row["line"], 0)
        stack_rows.append({
            **row,
            "limit_text": format_cents_compact(cents),
            "xs": format_cents(floor),
        })
        running[row["line"]] = floor + cents
    # Every line's next floor, zeros included — the add row states the
    # attachment PER LINE, because a single figure pinned to the first line
    # lies the moment another line is picked (review C18/C29).
    next_xs = {
        line: format_cents(running.get(line, 0)) for line in line_names
    }

    context = _context(conn, org, "program", request)
    context.update({
        "action": f"/accounts/{ref}/program/new",
        "error": error,
        "latest": latest,
        "latest_file": (
            Path(latest.program_path).name
            if latest is not None and latest.program_path
            else None
        ),
        "latest_counts": (
            f"{len(source_program.layers)} layers · {len(source_program.lines)} lines"
            if source_program is not None
            else None
        ),
        "source": source_kind,
        "name": name,
        "period_from": period_from,
        "period_to": period_to,
        "status": status,
        # the same controlled tuple the status cell offers — a typed status
        # would silently fall out of every status-filtered view
        "statuses": [value for _, value in _PLACEMENT_CELLS["status"].options],
        "lines_text": lines_text,
        "line_names": line_names,
        "stack_rows": stack_rows,
        "next_xs": next_xs,
        "typed_row": typed_row,
        "dest": str(dest) if dest else None,
        "dest_name": dest.name if dest else None,
        "facts": (
            {
                "insured": composed.insured,
                "program": composed.program,
                "period": f"{period_from} → {period_to}",
                "lines": len(composed.lines),
                "layers": len(composed.layers),
                "currency": composed.currency,
            }
            if composed is not None
            else None
        ),
        "checks": (
            {
                "ok": checks.ok,
                "errors": [d.message for d in checks.errors],
                "warnings": [d.message for d in checks.warnings],
            }
            if checks is not None
            else None
        ),
    })
    return TEMPLATES.TemplateResponse(request, "account/new_program.html", context)


# NO WEB ROUTE CREATES A SUBMISSION WITH NO LINE OF COVERAGE ANY MORE
# (A4, Grant 2026-08-26).
#
# `GET/POST .../submissions` rendered `submission_form` into this section's form
# host and wrote a bare `submission` row — a package addressed to a market, with
# no `market_response` under it and therefore no line of coverage anywhere. That
# is the second of the two controls that both meant "we sent this market a
# submission", and it is the one that manufactured the defect the Marketing
# panel then had to survive: fourteen seeded placements whose panel said "No
# line of coverage on this placement is being marketed yet" over live
# submissions, two of them quoted at $1.4M, and a client workbook with one
# header row in it.
#
# The band's Submission button is an anchor to the Marketing section now, where
# the add-market row records the same approach against the line of coverage it
# is about, through the one home for that write
# (`services.marketing_entry.approach`, shared with MCP's `market_approach`).
# Marketing already recorded with no line of coverage is not stranded: it
# renders in the report's provisional block and each row can be given its line
# there (`marketing_assign_line`).
#
# `forms.entities.submission_form` / `apply_submission` are NOT deleted: the TUI
# is retired but still green and `s` is still bound to them. web/parity.py's
# `new_submission` entry says what the web does instead.


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


# --- removing a program that should not exist ----------------------------------
#
# NOT merge. Merge folds two records of the same thing together and refuses two
# file-backed placements on purpose (two sources of truth). This is the other
# case: a program created by mistake, with its own file, which should never
# have existed. services/program_remove.py owns the rule; these two routes are
# the door, confirm-first, because a file moves and a browser confirm() shows
# no plan — the same objection this file already records against confirm() for
# revert.


@router.get("/accounts/{ref}/program/{placement_id}/remove", response_class=HTMLResponse)
def program_remove_confirm(
    request: Request, ref: str, placement_id: str
) -> HTMLResponse:
    """The confirm step. Writes NOTHING, and shows the DESTINATION — where the
    file lands is the part a person can only check beforehand (same reason
    _scaffold_confirm.html prints its path)."""
    from ...services import program_remove

    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    source = None
    if placement.program_path:
        candidate = sync.program_file(conn, placement)
        source = candidate if candidate.exists() else None
    return TEMPLATES.TemplateResponse(
        request, "account/_program_remove_confirm.html",
        {
            "header": {"org": org},
            "placement": placement,
            "blockers": program_remove.blockers(conn, placement.id),
            "cascade_refusals": program_remove.cascade_refusals(conn, placement.id),
            "carried": len(placements_repo.dependant_rows(conn, placement.id)),
            "notes": program_remove.consequences(conn, placement),
            "file_from": str(source) if source else None,
            "file_to": (
                str(program_remove.retired_path(source, now=db.utc_now()))
                if source
                else None
            ),
            "action": f"/accounts/{ref}/program/{placement_id}/remove",
            "cancel": f"/accounts/{ref}/program",
        },
    )


@router.post("/accounts/{ref}/program/{placement_id}/remove", response_class=HTMLResponse)
async def program_remove_save(
    request: Request, ref: str, placement_id: str
) -> HTMLResponse:
    """The confirmed removal. Answers with the whole TAB, not the section: the
    section it would have swapped is the one that just stopped existing."""
    from ...services import program_remove as removal

    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    # The cascade is a SEPARATE BUTTON, not a checkbox on the same one: it is a
    # different act with a different sentence, and a checkbox somebody leaves
    # ticked from last time is exactly the prefill nobody checks.
    cascade = str((await request.form()).get("cascade", "")) == "1"
    try:
        removal.remove(
            conn, placement, open_batch=_open_batch_web, now=db.utc_now(),
            source="web", cascade=cascade,
        )
    except removal.ProgramRemoveRefused as exc:
        return _programs_panel(request, ref, org, error="; ".join(exc.reasons))
    except OSError as exc:
        # The record is already gone — the database commits first, on purpose.
        # Say exactly that rather than letting a filesystem errno read as "the
        # removal failed", which would send somebody looking for a record that
        # is not coming back.
        return _programs_panel(
            request, ref, org,
            error=(
                f"{placement.ref} was removed, but its file could not be moved "
                f"aside ({exc}) — the file is still where it was and can be "
                f"moved by hand"
            ),
        )
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


# --- compare (spec D8 slice 5, phase 4) ----------------------------------------


@router.get(
    "/accounts/{ref}/program/{placement_id}/compare", response_class=HTMLResponse
)
def compare_page(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """towerkit's compare_programs as the delta table the spec asks for.

    The PAIR resolves by the renewal adjacency rule — same account, linked,
    expiring period_to == this period_from — with a PICKER when that is
    ambiguous or empty, never a guess (the spec's recommended posture,
    mirroring sync.AmbiguousPlacement's). `?with={placement_id}` overrides.
    Read-only; no tower graphic, per the spec's own recommend-against."""
    from towerkit.compare import compare_programs

    org = _org(request, ref)
    conn = _conn(request)
    proposed = _owned(conn, org, "placement", placement_id, placements_repo.get)
    with_id = request.query_params.get("with")
    action = f"/accounts/{ref}/program/{placement_id}/compare"
    context = _context(conn, org, "program", request)

    if not proposed.program_path:
        context.update({"proposed": proposed, "candidates": [], "action": action})
        return TEMPLATES.TemplateResponse(request, "account/_compare_picker.html", context)

    siblings = [
        p for p in placements_repo.for_org(conn, org.id)
        if p.id != proposed.id and p.program_path
    ]
    if with_id:
        expiring = _owned(conn, org, "placement", with_id, placements_repo.get)
        if not expiring.program_path:
            raise HTTPException(status_code=404, detail=f"{expiring.ref} has no program file")
    else:
        adjacent = [p for p in siblings if p.period_to == proposed.period_from]
        # ?pick=1 is the header's "pair with another…" — the picker on
        # demand, not only on ambiguity.
        if request.query_params.get("pick") or len(adjacent) != 1:
            context.update(
                {"proposed": proposed, "candidates": siblings, "action": action}
            )
            return TEMPLATES.TemplateResponse(
                request, "account/_compare_picker.html", context
            )
        expiring = adjacent[0]

    exp_program = _loaded_program(conn, expiring)
    prop_program = _loaded_program(conn, proposed)
    delta = compare_programs(exp_program, prop_program)

    from ...money import dollars_to_cents

    def money(dollars: int | None) -> str:
        # towerkit's delta speaks dollars; the ONE conversion rule applies
        # even for display (CLAUDE.md: conversion only in sync.py / money.py)
        return format_cents_compact(dollars_to_cents(dollars)) if dollars else "—"

    def band(layer: Any) -> str:
        return f"{money(layer.limit)} xs {money(layer.attach) if layer.attach else '$0'}"

    def signed(layer: Any) -> str | None:
        return None if layer.signed_bps >= 10_000 else f"{layer.signed_bps / 100:g}%"

    # LAYER-LEVEL rows (design 2C): the renewal is read layer by layer, with
    # the carrier moves in-line — new rows tinted good, lapsed danger, added
    # participants marked, removed ones struck. Top of tower first.
    exp_layers = {ly.id: ly for ly in exp_program.layers}
    prop_layers = {ly.id: ly for ly in prop_program.layers}
    ordered = sorted(prop_program.layers, key=lambda ly: -ly.attach) + sorted(
        (ly for ly in exp_program.layers if ly.id not in prop_layers),
        key=lambda ly: -ly.attach,
    )
    rows = []
    for layer in ordered:
        old = exp_layers.get(layer.id)
        new = prop_layers.get(layer.id)
        status = "new" if old is None else ("lapsed" if new is None else "")
        old_names = {p.carrier for p in old.participants} if old else set()
        new_names = {p.carrier for p in new.participants} if new else set()
        shown = new if new is not None else old
        markets = [
            {
                # WITH the %, always — beside two money columns a bare
                # number reads as money (the market cell's own recorded
                # rule; review C11).
                "name": f"{p.carrier} {p.share_bps / 100:g}%",
                "added": bool(old is not None and p.carrier not in old_names),
            }
            for p in (shown.participants if shown else [])
        ]
        gone = sorted(old_names - new_names) if new is not None and old else []
        old_premium = old.premium if old else None
        new_premium = new.premium if new else None
        if old_premium is None and new_premium is None:
            premium_delta_text, premium_dir = "—", ""
        elif new_premium is None and new is not None:
            # priced last year, not yet priced this year — a fact, not a
            # −100% cut (review C13)
            premium_delta_text, premium_dir = "not priced yet", ""
        elif old_premium is None and old is not None:
            premium_delta_text, premium_dir = "newly priced", ""
        else:
            moved = (new_premium or 0) - (old_premium or 0)
            if moved == 0:
                premium_delta_text, premium_dir = "no change", ""
            else:
                premium_delta_text = f"{'+' if moved > 0 else '−'}{money(abs(moved))}"
                # cost framing: an increase reads danger, a reduction good
                premium_dir = "up" if moved > 0 else "down"
        if shown is None:  # unreachable: ordered only holds known layers
            continue
        rows.append(
            {
                "name": shown.name,
                "status": status,
                "expiring": band(old) if old else None,
                "expiring_signed": signed(old) if old else None,
                "proposed": band(new) if new else None,
                "proposed_signed": signed(new) if new else None,
                "premium_delta": premium_delta_text,
                "premium_dir": premium_dir,
                "markets": markets,
                "gone": gone,
                "lapsed_markets": (
                    " · ".join(p.carrier for p in old.participants) if new is None and old else None
                ),
            }
        )

    # The plain-English paragraph (design 2C): said in words BEFORE any
    # table, every sentence derived from the same delta the table shows.
    new_rows = [r for r in rows if r["status"] == "new"]
    lapsed_rows = [r for r in rows if r["status"] == "lapsed"]
    off = sorted({c for ly in exp_program.layers for c in (p.carrier for p in ly.participants)}
                 - {c for ly in prop_program.layers for c in (p.carrier for p in ly.participants)})
    on = sorted({c for ly in prop_program.layers for c in (p.carrier for p in ly.participants)}
                - {c for ly in exp_program.layers for c in (p.carrier for p in ly.participants)})
    sentences: list[str] = []
    for r in new_rows:
        written = " · ".join(m["name"] for m in r["markets"]) or "nobody yet"
        sentences.append(f"{r['name']} is new at {r['proposed']} — {written}.")
    if lapsed_rows:
        names = ", ".join(r["name"] for r in lapsed_rows)
        sentences.append(f"{names} lapse{'s' if len(lapsed_rows) == 1 else ''}.")
    if off or on:
        churn = []
        if off:
            churn.append(f"{', '.join(off)} come{'s' if len(off) == 1 else ''} off")
        if on:
            churn.append(f"{', '.join(on)} join{'s' if len(on) == 1 else ''}")
        sentences.append("; ".join(churn) + ".")
    limit_moved = (delta.limit_new or 0) - (delta.limit_old or 0)
    proposed_priced = any(ly.premium is not None for ly in prop_program.layers)
    expiring_priced = any(ly.premium is not None for ly in exp_program.layers)
    if limit_moved:
        sentences.append(
            f"Total limit moves {money(delta.limit_old)} → {money(delta.limit_new)}."
        )
    if expiring_priced and not proposed_priced:
        # a fresh renewal starts unpriced — that is a fact, not a 100% cut
        # (review C13)
        sentences.append("The proposed program is not priced yet.")
    elif (
        proposed_priced
        and delta.premium_delta_pct is not None
        and delta.premium_delta_pct != 0
    ):
        sentences.append(
            f"Premium moves {money(delta.premium_old)} → {money(delta.premium_new)} "
            f"({delta.premium_delta_pct:+.1f}%)."
        )
    if not sentences:
        # never "nothing moved" — share and band edits move without tripping
        # any clause above, and the table may show them (review C14)
        sentences.append(
            "No layers added or lapsed, no carrier moves — the layer rows "
            "below carry any smaller changes."
        )

    context.update(
        {
            "proposed": proposed,
            "expiring": expiring,
            "pair_note": (
                "paired on renewal adjacency" if not with_id else "paired by hand"
            ),
            "action": action,
            "limit_old": money(delta.limit_old),
            "limit_new": money(delta.limit_new),
            "limit_delta": (
                f"{'+' if limit_moved > 0 else '−'}{money(abs(limit_moved))}"
                if limit_moved
                else None
            ),
            "limit_dir": "up" if limit_moved > 0 else "down" if limit_moved else "",
            "premium_old": money(delta.premium_old),
            "premium_new": money(delta.premium_new),
            "premium_delta_pct": (
                delta.premium_delta_pct
                if proposed_priced and expiring_priced
                else None
            ),
            "layers_old": len(exp_program.layers),
            "layers_new": len(prop_program.layers),
            "layers_note": f"{len(new_rows)} new · {len(lapsed_rows)} lapsed",
            "carriers_old": len({p.carrier for ly in exp_program.layers for p in ly.participants}),
            "carriers_new": len({p.carrier for ly in prop_program.layers for p in ly.participants}),
            "carriers_note": (
                " · ".join(
                    part
                    for part in (
                        f"{', '.join(off)} off" if off else "",
                        f"{', '.join(on)} on" if on else "",
                    )
                    if part
                )
                or "unchanged"
            ),
            "summary": " ".join(sentences),
            "rows": rows,
        }
    )
    return TEMPLATES.TemplateResponse(request, "account/compare.html", context)


# --- exports: the artifacts the terminal can make (phase 4) --------------------
#
# DOWNLOADS ARE PLAIN ANCHOR GETs answering Content-Disposition: attachment —
# no htmx: browsers handle a download navigation natively and a swap contract
# adds nothing (DECISIONS.md, 2026-08-19). Artifacts render into a per-request
# temp dir through towerkit's OWN renderers — the agreement rule end to end:
# every word in the SVG, the PDF and the schematic came off the renderer.
# The open-items workbook CALLS services.export_open_items and never touches
# it (Grant has in-flight edits to that module).


def _refusal_page(request: Request, message: str, back: str) -> HTMLResponse:
    """A download link's refusal is a NAVIGATION, not a swap — answer with a
    small readable page and a way back, never a bare status code. Escaped by
    hand: no template, no autoescape (same rule as _panel_refusal)."""
    from markupsafe import escape

    return HTMLResponse(
        f'<p class="form-error" role="alert">{escape(message)}</p>'
        f'<p><a href="{escape(back)}">back to the program tab</a></p>'
    )


def _attachment(content: bytes, filename: str, media_type: str) -> Any:
    from fastapi.responses import Response

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _loaded_program(conn: sqlite3.Connection, placement: Any) -> Any:
    from towerkit.model import load_program

    return load_program(sync.program_file(conn, placement))


@router.get(
    "/accounts/{ref}/program/{placement_id}/export/tower.svg",
    response_class=HTMLResponse,
)
def export_tower_svg(request: Request, ref: str, placement_id: str) -> Any:
    return _export_tower(request, ref, placement_id, "svg", "image/svg+xml")


@router.get(
    "/accounts/{ref}/program/{placement_id}/export/tower.pdf",
    response_class=HTMLResponse,
)
def export_tower_pdf(request: Request, ref: str, placement_id: str) -> Any:
    return _export_tower(request, ref, placement_id, "pdf", "application/pdf")


def _resolve_theme(stored: str | None) -> Path | None:
    """The stored theme as a path that exists, or a refusal naming it.

    Two steps, because a stored theme is portable BY DESIGN: entries under
    ./themes stay relative so a program file carries the same value between
    machines (towerkit.theme.available_themes). A relative path is therefore
    resolved against the themes this machine can actually see, by NAME, rather
    than against whatever directory happens to have launched the server.

    IT REFUSES RATHER THAN FALLING BACK. A missing theme silently replaced by
    the built-in default renders a client-facing chart in the wrong brand and
    says nothing — the export looks like it worked. Refusing names the theme
    and leaves the file untouched, which is the same call the program panel
    makes when a towerkit file will not load.
    """
    if not stored:
        return None
    from towerkit.theme import resolve_theme

    # ONE RESOLUTION RULE, and it is towerkit's. This module had its own copy —
    # try the literal path, else match by stem — written before towerkit had
    # one, and a second answer to "where is this theme" is how the renderer and
    # the validator came to disagree in the first place (towerkit
    # fix/theme-by-name, 2026-08-21). The refusal is re-worded here because
    # this surface can name the control that fixes it; the RULE is not
    # re-implemented.
    try:
        return resolve_theme(stored)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"this program is set to render with the {Path(stored).stem!r} theme, "
            f"and no theme by that name is installed here — pick another on the "
            f"Program tab's chart strip, or put {Path(stored).name} in ./themes"
        ) from None


def _export_tower(
    request: Request, ref: str, placement_id: str, fmt: str, media_type: str
) -> Any:
    import tempfile
    from pathlib import Path as _Path

    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    if not placement.program_path:
        return _refusal_page(
            request,
            f"{placement.ref} has no program file linked — nothing to draw yet",
            f"/accounts/{ref}/program",
        )
    # Agg BEFORE towerkit's renderer imports pyplot: the default macOS
    # backend wants a display a server thread does not have.
    import matplotlib

    matplotlib.use("Agg")
    from towerkit.render.mpl_program import render_program
    from towerkit.theme import load_theme

    program = _loaded_program(conn, placement)
    # THE PROGRAM'S OWN SAVED CHART OPTIONS, which this route ignored until D6:
    # it always rendered with the library defaults, so a broker who had turned
    # premiums off in towerkit's editor got them back on every download bookkit
    # produced — and, with the settings now editable here, the chart strip
    # would have been a set of controls that provably changed nothing.
    # towerkit's own CLI reads them the same way (cli.py `_cmd_render`),
    # including the theme.
    stored = program.render
    try:
        theme_path = _resolve_theme(stored.theme if stored else None)
    except FileNotFoundError as missing:
        return _refusal_page(request, str(missing), f"/accounts/{ref}/program")
    with tempfile.TemporaryDirectory() as tmp:
        paths = render_program(
            program, load_theme(theme_path), _Path(tmp), placement.ref, formats=[fmt],
            show_totals=stored.show_totals if stored else True,
            show_premiums=stored.show_premiums if stored else True,
            cell_premiums=bool(stored and stored.cell_premiums),
            cell_dates=bool(stored and stored.cell_dates),
            # `render.colorBy` is deliberately NOT passed: towerkit's
            # `render/fills.py` reads it off the file when given no override,
            # so a fifth copy of "what does this file want" would live here.
            # The override exists for `towerctl render --color-by`, not for a
            # route that is rendering the file as saved.
        )
        content = paths[0].read_bytes()
    return _attachment(content, f"{placement.ref}-tower.{fmt}", media_type)


@router.get(
    "/accounts/{ref}/program/{placement_id}/export/schematic.xlsx",
    response_class=HTMLResponse,
)
def export_schematic(request: Request, ref: str, placement_id: str) -> Any:
    import tempfile
    from pathlib import Path as _Path

    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    if not placement.program_path:
        return _refusal_page(
            request,
            f"{placement.ref} has no program file linked — nothing to draw yet",
            f"/accounts/{ref}/program",
        )
    # new_workbook, never the spreadsheet library directly: workbook I/O
    # stays behind towerkit's helpers (the conventions suite greps for the
    # library's name outside imports/), the same seam
    # services/export_open_items composes through
    from towerkit.render.schematic_xlsx import add_schematic_sheet
    from towerkit.render.table_xlsx import finalize_workbook, new_workbook
    from towerkit.theme import load_theme

    program = _loaded_program(conn, placement)
    # THE PROGRAM'S OWN SAVED CHART OPTIONS, for the same reason `_export_tower`
    # above reads them — and this route did not, which made the two downloads
    # off one Program tab disagree: the chart honoured the stored theme and
    # premium settings while the worksheet beside it always rendered with the
    # library default theme and premiums forced on. Two pictures of one tower,
    # from two buttons an inch apart (found 2026-08-25 while moving the period
    # out of the cells).
    stored = program.render
    try:
        theme_path = _resolve_theme(stored.theme if stored else None)
    except FileNotFoundError as missing:
        return _refusal_page(request, str(missing), f"/accounts/{ref}/program")
    wb = new_workbook()
    add_schematic_sheet(
        wb, program, load_theme(theme_path),
        show_premiums=stored.show_premiums if stored else True,
        cell_dates=bool(stored and stored.cell_dates),
        # `render.colorBy` reads itself off the file, same as the chart above.
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = _Path(tmp) / "schematic.xlsx"
        finalize_workbook(wb, out)
        content = out.read_bytes()
    return _attachment(
        content,
        f"{placement.ref}-schematic.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/export/marketing.xlsx",
    response_class=HTMLResponse,
)
def export_marketing_report(
    request: Request, ref: str, placement_id: str, audience: str = "client"
) -> Any:
    """The marketing report — which markets we are approaching, what they said,
    at what rate — by line of coverage.

    NOT a second composition path, for the reason `export_work_workbook` gives
    about its own: `services.marketing_report.write` composes through the same
    `compose()` the MCP tool reads, so the file a client is sent and the answer
    the assistant gives cannot disagree about what a market said.

    TWO AUDIENCES, ONE QUERY. `?audience=internal` adds the underwriter's own
    words, the commission, the clearance warnings and our notes. The default is
    the CLIENT sheet, because this is a document that leaves the building and
    the safe rendering is the one you get by not thinking about it. The
    composer withholds per audience; this route only names which.

    It needs NO program file: marketing happens before a tower exists, and
    every figure on this sheet lives in SQLite. That is why it is here rather
    than behind the `program_path` guard the schematic download carries.
    """
    import tempfile
    from datetime import date as _date
    from pathlib import Path as _Path

    from ...services import marketing_report as report_svc

    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    if audience not in (report_svc.CLIENT, report_svc.INTERNAL):
        # A typo'd audience must not silently fall through to the client sheet
        # in one direction or leak in the other. Refuse and say so.
        return _refusal_page(
            request,
            f"unknown audience {audience!r} — it is 'client' or 'internal'",
            f"/accounts/{ref}/program",
        )
    suffix = "marketing" if audience == report_svc.CLIENT else "marketing-internal"
    with tempfile.TemporaryDirectory() as tmp:
        out = report_svc.write(
            conn,
            placement.id,
            _Path(tmp) / f"{placement.ref}-{suffix}.xlsx",
            _date.today(),
            audience=audience,
        )
        content = out.read_bytes()
    return _attachment(
        content,
        f"{placement.ref}-{suffix}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/accounts/{ref}/export/open-items.xlsx", response_class=HTMLResponse)
def export_open_items_workbook(request: Request, ref: str) -> Any:
    """The TUI's `x`, webside — the same services.export_open_items.write the
    terminal calls, so the two surfaces can never produce different books."""
    import tempfile
    from datetime import date as _date
    from pathlib import Path as _Path

    from ...services import export_open_items as export_svc

    org = _org(request, ref)
    conn = _conn(request)
    with tempfile.TemporaryDirectory() as tmp:
        out = export_svc.write(
            conn, org.id, _Path(tmp) / f"{org.ref}-open-items.xlsx", _date.today()
        )
        content = out.read_bytes()
    return _attachment(
        content,
        f"{org.ref}-open-items.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --- the terms strip: retentions and sublimits (phase 4) -----------------------
#
# One route family serves both kinds through a {kind} path parameter,
# REGISTERED LAST ON PURPOSE: Starlette matches /program/{placement_id}/{kind}
# before FastAPI validates the enum, so EVERY literal sibling — today:
# /renew, /merge, /scaffold, /compare, /layers, /lines, /submissions, /cell,
# /worksheet, /remove, /export/..., and routes/marketing.py's whole
# /marketing/... family — must be registered FIRST to win. That last one is
# not hypothetical: marketing.router was included AFTER this one, so
# `POST /program/<id>/marketing/lines` matched {kind}/{index} and answered 422
# rather than adding a line of coverage (2026-08-25). This list is the invariant's
# one home: when you add a /program/{placement_id}/<one-segment> route, add
# it ABOVE this block AND name it here, or a future reorder will shadow it
# into 422s.

from enum import StrEnum as _StrEnum  # noqa: E402


class TermKind(_StrEnum):
    retentions = "retentions"
    sublimits = "sublimits"


_TERM_AMOUNT_FIELD = Field("amount", "amount", "money", required=True)
_TERM_SINGULAR = {"retentions": "retention", "sublimits": "sublimit"}


def _terms_base(ref: str, placement_id: str, kind: str) -> str:
    return f"/accounts/{ref}/program/{placement_id}/{kind}"


def _program_lines(request: Request, placement_id: str) -> list[tuple[str, str]]:
    """The linked program's lines, off the per-request memo.

    `request` rather than `conn` because these three helpers are called once
    PER TERM CHIP, and a nine-chip terms strip opened and re-parsed the same
    towerkit file nine times per render before this (2026-08-20)."""
    return sync.program_lines_of(linked_for(request, _conn(request), placement_id).program)


def _term_lines(request: Request, placement_id: str) -> list[dict[str, str]]:
    return [{"id": lid, "name": str(name)} for lid, name in _program_lines(request, placement_id)]


def _line_names(request: Request, placement_id: str, ids: list[str]) -> str:
    names = dict(_program_lines(request, placement_id))
    return ", ".join(str(names.get(lid, lid)) for lid in ids)


def _term_label(
    request: Request, placement_id: str, kind: str, term: dict[str, Any]
) -> str:
    lines = _line_names(request, placement_id, term["applies_to"])
    amount = format_cents_compact(term["amount_cents"])
    head = term["type"].upper() if kind == "retentions" else term["name"]
    return f"{head} {amount} · {lines}"


def _term_chip_html(
    request: Request, ref: str, placement_id: str, kind: str, term: dict[str, Any]
) -> str:
    template = TEMPLATES.env.get_template("account/_term_chip.html")
    return template.render(
        base=_terms_base(ref, placement_id, kind),
        term=term,
        label=_term_label(request, placement_id, kind, term),
    )


def _term_chips(request: Request, ref: str, placement: Any) -> dict[str, list[str]] | None:
    if not placement.program_path:
        return None
    conn = _conn(request)
    terms = sync.program_terms_of(linked_for(request, conn, placement.id).program)
    return {
        kind: [
            _term_chip_html(request, ref, placement.id, kind, term)
            for term in terms[kind]
        ]
        for kind in ("retentions", "sublimits")
    }


def _term_by_index(
    conn: sqlite3.Connection, placement_id: str, kind: str, index: int
) -> dict[str, Any]:
    terms = sync.program_terms(conn, placement_id)[kind]
    if not 0 <= index < len(terms):
        raise HTTPException(status_code=404, detail=f"no {kind[:-1]} at index {index}")
    return terms[index]


def _term_form(
    request: Request, ref: str, placement_id: str, kind: str,
    action: str, values: dict[str, Any], error: str | None = None,
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_term_form.html",
        {
            "kind": kind, "action": action,
            "cancel_url": f"{_terms_base(ref, placement_id, kind)}/button",
            "lines": _term_lines(request, placement_id),
            # FROM TOWERKIT, NOT FROM A LITERAL. The three retention types were
            # spelled out in the template — a hand-written copy of towerkit's
            # `RetentionType` sitting in Jinja, where no test and no type
            # checker would ever notice it going stale. towerkit publishes the
            # vocabulary on the same derived surface D6 already reads, so it is
            # read from there and cannot drift by a fourth spelling.
            "retention_types": towerfields.resolve("retention", "type").values or (),
            "values": values, "error": error,
        },
    )


def _term_add_button(
    request: Request, ref: str, placement_id: str, kind: str
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_term_add_button.html",
        {"base": _terms_base(ref, placement_id, kind), "singular": _TERM_SINGULAR[kind]},
    )


async def _term_values(request: Request, kind: str) -> dict[str, Any]:
    form = await request.form()
    return {
        "type": str(form.get("type", "")),
        "name": str(form.get("name", "")),
        "amount": str(form.get("amount", "")),
        "line": [str(v) for v in form.getlist("line")],
        "notes": str(form.get("notes", "")),
    }


def _parse_term(kind: str, values: dict[str, Any]) -> tuple[int, list[str]]:
    """(amount_cents, line ids) — refusing in the broker's language."""
    amount = parse_value(_TERM_AMOUNT_FIELD, values["amount"])
    if amount in (None, ""):
        raise ValueError("amount is required")
    if not values["line"]:
        raise ValueError("pick at least one line of coverage")
    if kind == "sublimits" and not values["name"].strip():
        raise ValueError("the sublimit needs a name")
    return int(amount), values["line"]


@router.get(
    "/accounts/{ref}/program/{placement_id}/{kind}/new", response_class=HTMLResponse
)
def term_add_form(
    request: Request, ref: str, placement_id: str, kind: TermKind
) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return _term_form(
        request, ref, placement_id, kind.value,
        _terms_base(ref, placement_id, kind.value),
        {"line": []},
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/{kind}/button", response_class=HTMLResponse
)
def term_add_button(
    request: Request, ref: str, placement_id: str, kind: TermKind
) -> HTMLResponse:
    org = _org(request, ref)
    _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return _term_add_button(request, ref, placement_id, kind.value)


@router.post(
    "/accounts/{ref}/program/{placement_id}/{kind}", response_class=HTMLResponse
)
async def term_add(
    request: Request, ref: str, placement_id: str, kind: TermKind
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    values = await _term_values(request, kind.value)
    action = _terms_base(ref, placement_id, kind.value)
    try:
        amount_cents, line_ids = _parse_term(kind.value, values)
        if kind.value == "retentions":
            mutate = lambda: sync.add_retention(  # noqa: E731
                conn, placement_id, line_ids, values["type"], amount_cents,
                notes=values["notes"].strip() or None,
            )
        else:
            mutate = lambda: sync.add_sublimit(  # noqa: E731
                conn, placement_id, values["name"].strip(), amount_cents, line_ids,
                notes=values["notes"].strip() or None,
            )
        program_files.write(
            conn, placement,
            tool="program_term_add",
            summary=f"added a {_TERM_SINGULAR[kind.value]}",
            mutate=mutate,
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _term_form(request, ref, placement_id, kind.value, action, values, str(exc))
    return _panel(request, ref, org, placement_id)


@router.get(
    "/accounts/{ref}/program/{placement_id}/{kind}/{index}/chip",
    response_class=HTMLResponse,
)
def term_chip(
    request: Request, ref: str, placement_id: str, kind: TermKind, index: int
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    term = _term_by_index(conn, placement_id, kind.value, index)
    return HTMLResponse(_term_chip_html(request, ref, placement_id, kind.value, term))


@router.get(
    "/accounts/{ref}/program/{placement_id}/{kind}/{index}/edit",
    response_class=HTMLResponse,
)
def term_edit_form(
    request: Request, ref: str, placement_id: str, kind: TermKind, index: int
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    term = _term_by_index(conn, placement_id, kind.value, index)
    values = {
        "type": term.get("type", ""),
        "name": term.get("name", ""),
        "amount": initial_text(_TERM_AMOUNT_FIELD, term["amount_cents"]),
        "line": list(term["applies_to"]),
        "notes": term.get("notes") or "",
    }
    return _term_form(
        request, ref, placement_id, kind.value,
        f"{_terms_base(ref, placement_id, kind.value)}/{index}",
        values,
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/{kind}/{index}",
    response_class=HTMLResponse,
)
async def term_edit(
    request: Request, ref: str, placement_id: str, kind: TermKind, index: int
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    _term_by_index(conn, placement_id, kind.value, index)
    values = await _term_values(request, kind.value)
    action = f"{_terms_base(ref, placement_id, kind.value)}/{index}"
    try:
        amount_cents, line_ids = _parse_term(kind.value, values)
        if kind.value == "retentions":
            mutate = lambda: sync.edit_retention(  # noqa: E731
                conn, placement_id, index,
                type=values["type"], amount_cents=amount_cents, applies_to=line_ids,
                # The form always carries the box, so an empty one is a CLEAR
                # and not an omission — which is exactly the distinction
                # `set_notes` exists to make.
                notes=values["notes"].strip() or None, set_notes=True,
            )
        else:
            mutate = lambda: sync.edit_sublimit(  # noqa: E731
                conn, placement_id, index,
                name=values["name"].strip(), amount_cents=amount_cents,
                applies_to=line_ids,
                notes=values["notes"].strip() or None, set_notes=True,
            )
        program_files.write(
            conn, placement,
            tool="program_term_edit",
            summary=f"edited a {_TERM_SINGULAR[kind.value]}",
            mutate=mutate,
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _term_form(request, ref, placement_id, kind.value, action, values, str(exc))
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id)


@router.get(
    "/accounts/{ref}/program/{placement_id}/{kind}/{index}/remove",
    response_class=HTMLResponse,
)
def term_remove_confirm(
    request: Request, ref: str, placement_id: str, kind: TermKind, index: int
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    term = _term_by_index(conn, placement_id, kind.value, index)
    return TEMPLATES.TemplateResponse(
        request, "account/_term_remove_confirm.html",
        {
            "base": _terms_base(ref, placement_id, kind.value),
            "term": term,
            "label": _term_label(request, placement_id, kind.value, term),
        },
    )


@router.post(
    "/accounts/{ref}/program/{placement_id}/{kind}/{index}/remove",
    response_class=HTMLResponse,
)
def term_remove(
    request: Request, ref: str, placement_id: str, kind: TermKind, index: int
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    term = _term_by_index(conn, placement_id, kind.value, index)
    label = _term_label(request, placement_id, kind.value, term)
    try:
        if kind.value == "retentions":
            mutate = lambda: sync.remove_retention(conn, placement_id, index)  # noqa: E731
        else:
            mutate = lambda: sync.remove_sublimit(conn, placement_id, index)  # noqa: E731
        program_files.write(
            conn, placement,
            tool="program_term_remove",
            summary=f"removed {label}",
            mutate=mutate,
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return TEMPLATES.TemplateResponse(
            request, "account/_term_remove_confirm.html",
            {
                "base": _terms_base(ref, placement_id, kind.value),
                "term": term, "label": label, "error": str(exc),
            },
        )
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id)


# --- towerkit's derived field surface, as cells ---------------------------------
#
# Everything above is a route per FIELD: its own path, its own parse, its own
# refusal. That is the right shape where bookkit has a rule of its own (a
# placement cell writes through to a column AND a file; a layer's money is
# cents here and dollars there).
#
# It is the wrong shape for a plain towerkit scalar, and D6 is the proof:
# seventeen fields reachable only from towerkit's own editor, behind the TUI's
# `o`, which a browser does not have — five of which grew while every parity
# test stayed green. Seventeen more hand-written routes would be seventeen
# places to edit the day towerkit grows an eighteenth.
#
# So these three routes serve EVERY field towerkit publishes. What they cannot
# derive is WHERE a field goes on the page — that is a design decision, not a
# property of the model — so `_PLACED` states it, once, and a field that is
# not in it has no cell here and says so. Parsing, refusing, clearing, bounds
# and guards all come from towerkit (`towerfields`, `sync.set_tower_field`);
# only placement is ours.


@dataclass(frozen=True)
class _Placed:
    """Where one derived field is PUT, and what its save answers with.

    `tag` is the cell's element and is NOT cosmetic: a `<td>` swapped back
    inside the details row's colspan cell has no table-row ancestor at the
    swap point and the HTML parser drops it outright, value and all
    (macros/cell.html). Everything in the details row is a span.

    `answers` is "cell" when the write changes only the value the user typed,
    and "panel" when it changes something the section renders elsewhere — a
    line's column label re-letters every layer table header, so answering with
    the cell alone would leave the headers stale until a refresh.
    """

    tag: str
    answers: str = "cell"
    css: str = ""
    # Names a `_CHOICES` provider that turns this field into a PICKER.
    # towerkit publishes `render.theme` as free text — it is a file path, and
    # the model cannot know which paths exist on this machine. A text box for a
    # path is the open field that mistake-proofing literature says to replace
    # with a constrained control: nobody can type a theme they have, and
    # nothing stops them typing one they do not. The list is discovered at
    # request time, so a theme dropped into ./themes appears without a restart.
    choices: str | None = None


# The placement table. Adding a row here is what makes a towerkit field
# editable in the browser; `tests/test_web_parity.py` checks every key against
# `mcpsurface.SURFACE` (so a field towerkit renames turns red rather than
# 404ing at a user) and against the field ledger (so a field that is BUILT
# cannot still be described there as planned — that drift has shipped three
# times).
_PLACED: dict[str, _Placed] = {
    # An administrative fact about the POLICY, so it sits in the policy group
    # beside the number and the dates, not among the coverage facts. towerkit
    # publishes it as a bool, which towerfields renders as a yes/no select —
    # a constrained control, blank option included, with no cell of its own to
    # hand-write here. The seam working as designed.
    "layer.auditable": _Placed(tag="span"),
    # Which tranche of the tower this slab belongs to — layers sharing the
    # token are drawn as one band when the chart is coloured by structure
    # rather than by market. A plain text cell: the vocabulary is the
    # BROKER's (there is no knowable set of tranche names), which is the one
    # case the constrained-input rule leaves open.
    "layer.group": _Placed(tag="span"),
    # The layer's long tail, in the details row the chevron opens.
    "layer.states": _Placed(tag="span"),
    "layer.limitsDetail": _Placed(tag="span"),
    "layer.retentionDetail": _Placed(tag="span"),
    "layer.premiumDetail": _Placed(tag="span"),
    "layer.notes": _Placed(tag="span"),
    # The column label a line prints in the layer table's header. Every header
    # re-letters, so the whole section answers.
    "line.abbr": _Placed(tag="span", answers="panel"),
    # A note about the programme as a whole, on the section header.
    "program.notes": _Placed(tag="span", css="prose"),
    # The several figures a policy states where `limit` states one. They have
    # no ids — towerkit addresses them by the position a read reported, which
    # is why the address carries the layer AND the index.
    "named_limit.name": _Placed(tag="span"),
    "named_limit.amount": _Placed(tag="span", css="num"),
    # The saved chart options. They answer with their own cell and NOT the
    # panel: they change the exported SVG/PDF and the SOI schematic, and the
    # drawing on this page is towerkit's `render/web.py` geometry, which takes
    # no options — re-rendering the section would redraw a picture that cannot
    # have changed and cost the user their place for nothing.
    "program.render.theme": _Placed(tag="span", choices="themes"),
    "program.render.showTotals": _Placed(tag="span"),
    "program.render.showPremiums": _Placed(tag="span"),
    "program.render.cellPremiums": _Placed(tag="span"),
    "program.render.cellDates": _Placed(tag="span"),
    # No `choices=` provider: towerkit publishes colorBy as an ENUM carrying
    # its own two values (`model.ColorBy`), so `towerfields` derives the picker
    # from `entry.values` unaided — that is the seam working. `render.theme`
    # needs one only because it is a file path the model cannot enumerate.
    "program.render.colorBy": _Placed(tag="span"),
    "program.render.soiSchematic": _Placed(tag="span"),
}

# --- the facts that arrive together ----------------------------------------
#
# RECORDING A POLICY IS ONE ACT, AND THE PAGE HAD NO SURFACE FOR IT (Grant,
# 2026-08-27). Correcting one figure is an inline cell and this book does that
# well; a policy that has just been issued brings NINE facts at once, and the
# worksheet offered nine separate cells to click one at a time. Measured on the
# running app: 17 fields in that pane, 9 of them an em-dash — 53% of the
# widest column on the page is things nobody has said yet, and the only way to
# say them was one click each.
#
# THIS IS THE ONE EXCEPTION TO "THE PANEL IS THE REPORT, NO SECOND ENTRY FORM",
# and it is worth saying exactly why it does not generalise. That rule was
# written about the MARKETING GRID, where the panel genuinely is a report a
# client receives, and where a second form would be a second way to state a
# fact the client reads. The layer worksheet is not a report — it is a
# RECORD, and nobody sends it anywhere. This form does not add a second way to
# state one fact; it adds the first way to state nine at once. Every one of
# them still has its cell, and correcting one afterwards still goes through it.
#
# THE FIELDS SPAN TWO SEAMS, which is the whole reason the form has to exist
# rather than being a loop over one list. `policy no`, the two dates and the
# premium are LAYER_FIELDS and go through `sync.update_layer`; auditable, the
# three detail lines and the notes are towerkit-derived fields and go through
# `sync.set_tower_field`. A broker does not know that and must not have to.
_POLICY_LAYER = "layer"      # writes through sync.update_layer
_POLICY_TOWER = "tower"      # writes through sync.set_tower_field


# IN THE ORDER A POLICY IS READ, not the order the seams fall in. The number
# and the dates are on the declarations page; the premium is next to them; the
# rest is what the wording actually says. A form whose order matches the
# document is a form somebody can fill from the top.
_POLICY_FORM: tuple[tuple[str, str], ...] = (
    ("policy_number", _POLICY_LAYER),
    ("period_from", _POLICY_LAYER),
    ("period_to", _POLICY_LAYER),
    ("premium_cents", _POLICY_LAYER),
    ("layer.auditable", _POLICY_TOWER),
    ("layer.limitsDetail", _POLICY_TOWER),
    ("layer.retentionDetail", _POLICY_TOWER),
    ("layer.premiumDetail", _POLICY_TOWER),
    ("layer.states", _POLICY_TOWER),
    ("layer.notes", _POLICY_TOWER),
)


# The order the chart strip prints them in, and the words it prints. Derived
# labels would give "show totals" / "cell premiums", which say what the JSON
# key is rather than what the option does to the thing it governs.
_RENDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("totals", "render.showTotals"),
    ("premiums", "render.showPremiums"),
    ("premium per cell", "render.cellPremiums"),
    # NOT "dates per cell" any more: the period is stated once under its line
    # of coverage now (towerkit render/terms.py, 2026-08-25), and only a layer
    # that disagrees with its column still says so in a cell. The JSON key is
    # still `cellDates` — renaming it would break every file that carries it —
    # which is exactly why this table exists: the words say what the option
    # does, not what the key is called.
    ("policy periods", "render.cellDates"),
    # "color by", not "colour by": the derived editor's aria-label comes from
    # the field name and reads "color by", so a screen reader and a sighted
    # reader would otherwise be given two different words for one control.
    ("color by", "render.colorBy"),
    ("SOI schematic", "render.soiSchematic"),
    ("theme", "render.theme"),
)


def _theme_choices() -> tuple[tuple[str, str], ...]:
    """Every theme this program file may legally NAME, as (label, value).

    Not every theme on the machine: only the ones that are STORABLE.
    towerkit's validator (`_check_render_theme`) refuses an absolute
    `render.theme` outright — program files are portable by contract, and a
    theme path that renders here and breaks on the next machine is worse than
    one that breaks now — and it resolves the value relative to the working
    directory because that is exactly how `towerctl render` will resolve it.

    So the PACKAGED themes, which `available_themes` reports as absolute paths,
    cannot be named by a program file at all. Offering them is offering a
    choice that makes the file fail validation, which is worse than not
    offering them: the write is refused, and because every later write to that
    file re-validates it, the file is wedged until somebody edits the JSON by
    hand. That is not hypothetical — it is what this picker did on its first
    afternoon, and it is why the list is filtered here rather than presented
    whole and policed on the way in.

    The built-in default is the blank option, and it is a real answer: a
    cleared `render.theme` is what "use towerkit's own theme" means.

    UPDATED 2026-08-21, and the update is the whole point of towerkit's theme
    fix. This used to FILTER OUT every absolute path, which meant filtering out
    every PACKAGED theme — because a program file may not name one, and offering
    a choice that fails validation is worse than not offering it. The cost was
    invisible until Grant's folders moved: with no `./themes` beside the running
    process, the packaged set is ALL there is, and the picker came up completely
    empty (he reported exactly that).

    towerkit now resolves a stored theme BY NAME when the literal relative path
    misses, so `themes/marsh.json` finds the packaged marsh anywhere. That makes
    the portable spelling a real, storable answer for every theme this machine
    can see — so the picker offers them all, and offers them under that
    spelling rather than under the absolute path they were found at.
    """
    from towerkit.theme import available_themes

    # THE PORTABLE SPELLING, for every theme, whatever path it was found at.
    # `themes/<stem>.json` is what a program file may legally hold and what
    # towerkit's `resolve_theme` now finds — a packaged theme included. Sorted
    # and de-duplicated by stem because ./themes wins on a name clash upstream
    # (towerkit.theme.available_themes) and two options reading "marsh" would
    # be a picker asking a question with no distinguishable answers.
    seen: dict[str, str] = {}
    for path in available_themes():
        seen.setdefault(path.stem, f"themes/{path.stem}.json")
    return tuple(sorted(seen.items()))


# A picker's options are DATA, discovered per request, so a theme added to
# ./themes is offered without a restart and one that disappears stops being
# offered. Keyed by the name a `_Placed` row asks for.
_CHOICES: dict[str, Callable[[], tuple[tuple[str, str], ...]]] = {
    "themes": _theme_choices,
}


def _as_choice_field(field: Field, spot: _Placed) -> Field:
    """A `_PLACED` row that names a provider becomes a select.

    The blank option is deliberate and load-bearing for the theme: an empty
    value CLEARS the field, and a cleared `render.theme` means towerkit's
    built-in default — which is a real, choosable answer, not an absence.
    """
    if spot.choices is None:
        return field
    options = _CHOICES[spot.choices]()
    return replace(
        field,
        kind="select",
        options=options,
        optional_select=True,
        # An empty list is not a broken picker — it is a machine with no
        # portable themes installed, and the placeholder says what to do about
        # it rather than presenting an empty menu that reads as a bug.
        placeholder=field.placeholder or (
            "" if options else "no portable themes — put one in ./themes"
        ),
    )


def _field_key(kind: str, name: str) -> str:
    """The form-field name a derived cell posts under, and its `data-field`.

    Qualified by kind BECAUSE it has to be unique within one record scope:
    inline-cell.js finds the next Tab target and the post-swap refocus target
    by `data-field` within the enclosing `<tr>`, and two cells sharing the
    name would send the caret to whichever came first.
    """
    return f"{kind}.{name}"


def _field_entry(kind: str, name: str) -> Any:
    try:
        return towerfields.resolve(kind, name)
    except towerfields.FieldRefused as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _field_placed(kind: str, name: str) -> _Placed:
    spot = _PLACED.get(_field_key(kind, name))
    if spot is None:
        raise HTTPException(
            status_code=404,
            detail=f"{kind}.{name} is writable, but this page has no cell for it",
        )
    return spot


_NO_PART = "_"


def _addr(target: str | None, index: int | None) -> str:
    """The address as ONE path segment: always `<target>:<index>`, with `_` for
    a half this kind does not use.

    ONE segment rather than query parameters because macros/cell.html builds
    the editor's URL as `action + "/edit"` — a base carrying `?target=…` would
    produce `…?target=x/edit` and fetch nothing.

    BOTH HALVES ARE ALWAYS PRESENT, which is not tidiness. The first spelling
    of this used a bare id for a target and `i3` for a position, so the two
    were told apart by a leading "i" — and every real book has a line whose id
    IS "im" (inland marine, the line CLAUDE.md names as the reason a tower's
    earliest end is not its programme's end). Its column-label cell parsed as
    "index m", lost its target, and took the whole Program tab down with it.
    There is no safe leading character to pick out of user-supplied ids.
    """
    return f"{target or _NO_PART}:{_NO_PART if index is None else index}"


def _unaddr(addr: str) -> tuple[str | None, int | None]:
    target, _, position = addr.partition(":")
    return (
        None if target in ("", _NO_PART) else target,
        int(position) if position.isdigit() else None,
    )


def _field_action(
    ref: str, placement_id: str, kind: str, name: str, addr: str
) -> str:
    return f"/accounts/{ref}/program/{placement_id}/field/{kind}/{addr}/{name}"


def _field_subject(
    request: Request, placement: Any, kind: str, target: str | None, index: int | None
) -> str:
    """What the change summary and the conflict dialog call this row.

    A summary reading "set notes" tells the changes list nothing about which
    of fourteen layers moved; every other program write in this module names
    its row, so this one does too.
    """
    conn = _conn(request)
    if kind == "layer" and target:
        for layer in layers_for(request, conn, placement.id):
            if layer["id"] == target:
                return str(layer["name"])
    if kind == "line" and target:
        return _line_name(conn, placement.id, target)
    if kind == "program":
        return str(placement.program_name or placement.ref)
    return f"{kind} {target or index}"


def _field_value(request: Request, placement_id: str, kind: str, name: str,
                 target: str | None, index: int | None) -> Any:
    """What the field holds right now, read through the same loader seam every
    other program read uses — so a file that will not load says so rather than
    rendering as an empty cell (2026-08-20)."""
    conn = _conn(request)
    linked = linked_for(request, conn, placement_id)
    if linked.program is None:
        return None
    return sync.tower_field_value(linked.program, kind, name, target, index)


def _field_display_text(entry: Any, spot: _Placed, value: Any) -> str:
    """What the display cell shows.

    A picker shows its LABEL, never the stored value: `render.theme` holds a
    file path, and a cell reading
    `/…/site-packages/towerkit/themes/marsh.json` says nothing a broker wants
    to know and wraps the row while saying it. The editor still round-trips the
    exact stored value — a cell that pre-fills something its own parser would
    store differently is the cents lesson in another costume.
    """
    if spot.choices is not None and value not in (None, ""):
        for label, candidate in _CHOICES[spot.choices]():
            if candidate == str(value):
                return label
        # Stored, but no longer on this machine. Say so rather than printing a
        # dead path or, worse, an em-dash that reads as "no theme set" while
        # every export silently uses a different one.
        return f"{Path(str(value)).stem} (missing)"
    return towerfields.display(entry, value)


def _field_display(
    request: Request, ref: str, placement: Any, kind: str, name: str, addr: str,
) -> str:
    entry = _field_entry(kind, name)
    spot = _field_placed(kind, name)
    target, index = _unaddr(addr)
    value = _field_value(request, placement.id, kind, name, target, index)
    return render_cell_display(
        request,
        _as_choice_field(
            towerfields.bookkit_field(entry, key=_field_key(kind, name)), spot
        ),
        _field_display_text(entry, spot, value),
        _field_action(ref, placement.id, kind, name, addr),
        tag=spot.tag,
        extra_class=spot.css,
    )


def _field_editor(
    request: Request, ref: str, placement: Any, kind: str, name: str, addr: str,
    error: str | None = None, typed: str | None = None,
) -> str:
    entry = _field_entry(kind, name)
    spot = _field_placed(kind, name)
    target, index = _unaddr(addr)
    value = (
        typed
        if typed is not None
        else towerfields.editor_text(
            entry, _field_value(request, placement.id, kind, name, target, index)
        )
    )
    return render_cell(
        request,
        _as_choice_field(
            towerfields.bookkit_field(entry, key=_field_key(kind, name)), spot
        ),
        value,
        _field_action(ref, placement.id, kind, name, addr),
        error=error,
        tag=spot.tag,
        extra_class=spot.css,
    )


@router.get(
    "/accounts/{ref}/program/{placement_id}/field/{kind}/{addr}/{name}",
    response_class=HTMLResponse,
)
def field_cell(
    request: Request, ref: str, placement_id: str, kind: str, addr: str, name: str
) -> HTMLResponse:
    org = _org(request, ref)
    placement = _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return HTMLResponse(_field_display(request, ref, placement, kind, name, addr))


@router.get(
    "/accounts/{ref}/program/{placement_id}/field/{kind}/{addr}/{name}/edit",
    response_class=HTMLResponse,
)
def field_cell_edit(
    request: Request, ref: str, placement_id: str, kind: str, addr: str, name: str
) -> HTMLResponse:
    org = _org(request, ref)
    placement = _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    return HTMLResponse(_field_editor(request, ref, placement, kind, name, addr))


def _field_conflict(
    request: Request, ref: str, placement: Any, kind: str, name: str, addr: str,
    typed: str, message: str,
) -> HTMLResponse:
    entry = _field_entry(kind, name)
    spot = _field_placed(kind, name)
    target, index = _unaddr(addr)
    return TEMPLATES.TemplateResponse(
        request, "account/_layer_conflict.html",
        {
            "action": _field_action(ref, placement.id, kind, name, addr),
            "field": towerfields.bookkit_field(entry, key=_field_key(kind, name)),
            "typed": typed,
            "subject": _field_subject(request, placement, kind, target, index),
            "message": message,
            "tag": spot.tag,
        },
    )


def _field_write(
    request: Request, ref: str, org: Any, placement: Any, kind: str, name: str,
    addr: str, typed: str,
) -> HTMLResponse:
    """The one write, shared by the save and by Overwrite's retry — so the two
    cannot drift into doing different things (the same reason
    `_write_layer_field` exists for the layer cells)."""
    conn = _conn(request)
    entry = _field_entry(kind, name)
    spot = _field_placed(kind, name)
    target, index = _unaddr(addr)
    try:
        # A PICKER IS CHECKED HERE TOO, not only by the <select>. The markup
        # constrains a mouse; it constrains nothing else, and this route is
        # reachable by anything that can POST. `checked_option` is the same
        # rule every other select in bookkit is held to, so a theme that is not
        # on this machine cannot be stored by hand and then silently swallowed
        # by the export.
        field = _as_choice_field(
            towerfields.bookkit_field(entry, key=_field_key(kind, name)), spot
        )
        if field.kind == "select" and typed.strip():
            # NON-EMPTY ONLY, which is the same order `forms.spec.parse_value`
            # uses and not an optimisation. `checked_option` compares against
            # the option VALUES, and the blank option's value is "" — which is
            # not in that set, so checking it unconditionally refused the one
            # choice the markup itself offers. The theme picker's blank option
            # means "towerkit's built-in default", a real answer a broker picks
            # on purpose; refusing it made the field one-way (settable, never
            # unsettable) while the <select> went on advertising the option.
            # A control that offers a choice the server rejects is the
            # dead-control class in its most confusing form: it looks like it
            # worked, because the row re-renders either way.
            typed = checked_option(field, typed)
        wire = towerfields.to_wire(entry, typed)
    except (towerfields.FieldRefused, ValueError) as exc:
        return HTMLResponse(
            _field_editor(request, ref, placement, kind, name, addr, str(exc), typed)
        )

    subject = _field_subject(request, placement, kind, target, index)
    try:
        program_files.write(
            conn, placement,
            tool="program_field_edit",
            summary=f"set {towerfields.label(entry)} on {subject}",
            mutate=lambda: sync.set_tower_field(
                conn, placement.id, kind, name, wire, target, index
            ),
            open_batch=_open_batch_web,
        )
    except program_files.ProgramWriteRefused as refused:
        if _is_conflict(refused):
            return _field_conflict(
                request, ref, placement, kind, name, addr, typed, str(refused)
            )
        return HTMLResponse(
            _field_editor(request, ref, placement, kind, name, addr, str(refused), typed)
        )
    except Exception as exc:
        return HTMLResponse(
            _field_editor(request, ref, placement, kind, name, addr, str(exc), typed)
        )

    forget_program_reads(request)
    if spot.answers == "panel":
        return _panel(
            request, ref, org, placement.id,
            refocus=f"{target}:{_field_key(kind, name)}" if target else None,
            selected=target if kind == "layer" else None,
        )
    return HTMLResponse(_field_display(request, ref, placement, kind, name, addr))


@router.post(
    "/accounts/{ref}/program/{placement_id}/field/{kind}/{addr}/{name}",
    response_class=HTMLResponse,
)
async def field_cell_save(
    request: Request, ref: str, placement_id: str, kind: str, addr: str, name: str
) -> HTMLResponse:
    org = _org(request, ref)
    placement = _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    typed = str((await request.form()).get(_field_key(kind, name), ""))
    return _field_write(request, ref, org, placement, kind, name, addr, typed)


@router.post(
    "/accounts/{ref}/program/{placement_id}/field/{kind}/{addr}/{name}/reload",
    response_class=HTMLResponse,
)
def field_cell_reload(
    request: Request, ref: str, placement_id: str, kind: str, addr: str, name: str
) -> HTMLResponse:
    """THEIRS wins. Re-project, discard the draft, show what the file holds."""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    _field_placed(kind, name)
    _reproject(conn, placement)
    forget_program_reads(request)
    return HTMLResponse(_field_display(request, ref, placement, kind, name, addr))


@router.post(
    "/accounts/{ref}/program/{placement_id}/field/{kind}/{addr}/{name}/overwrite",
    response_class=HTMLResponse,
)
async def field_cell_overwrite(
    request: Request, ref: str, placement_id: str, kind: str, addr: str, name: str
) -> HTMLResponse:
    """MINE lands on top of theirs — a RETRY, not a force: re-project so the
    sha gate passes, then re-apply this ONE field. write_through re-reads the
    file, so whatever else changed while this tab was open survives under it."""
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    typed = str((await request.form()).get(_field_key(kind, name), ""))
    _reproject(conn, placement)
    forget_program_reads(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    return _field_write(request, ref, org, placement, kind, name, addr, typed)


@router.post(
    "/accounts/{ref}/program/{placement_id}/field/{kind}/{addr}/{name}/keep",
    response_class=HTMLResponse,
)
async def field_cell_keep(
    request: Request, ref: str, placement_id: str, kind: str, addr: str, name: str
) -> HTMLResponse:
    """Neither. The editor comes back with what was typed still in it, and the
    message still saying why nothing was written."""
    org = _org(request, ref)
    placement = _owned(_conn(request), org, "placement", placement_id, placements_repo.get)
    typed = str((await request.form()).get(_field_key(kind, name), ""))
    return HTMLResponse(
        _field_editor(
            request, ref, placement, kind, name, addr,
            "the file moved under this edit — nothing has been written", typed,
        )
    )


# --- named limits: the collection half of D6 ------------------------------------
#
# The two FIELDS a named limit carries are ordinary derived cells above. Adding
# and removing a ROW is not a field write and has no `set_field` to derive from,
# so these two routes are hand-written — the same division the lines strip and
# the terms strip already use.
#
# They answer with the PANEL, selected on the layer: the chips live in the
# worksheet pane now, which every section render rebuilds, so a panel swap no
# longer closes anything.


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}/named-limits",
    response_class=HTMLResponse,
)
async def named_limit_add(
    request: Request, ref: str, placement_id: str, layer_id: str
) -> HTMLResponse:
    """One coordinate limit — a name and a figure. Amount is typed in CENTS
    like every other money field in bookkit and lands in the file as whole
    dollars; `sync.add_named_limit` refuses a sub-dollar remainder rather than
    rounding it away."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    raw = str(form.get("amount", "")).strip()
    try:
        amount = parse_value(_NAMED_LIMIT_AMOUNT, raw)
    except ValueError as exc:
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=str(exc),
        )
    if not name:
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error="a named limit needs a name",
        )
    try:
        program_files.write(
            conn, placement,
            tool="program_named_limit_add",
            summary=f"added the {name} limit on {layer['name']}",
            mutate=lambda: sync.add_named_limit(
                conn, placement_id, layer_id, name, int(amount)
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=str(exc),
        )
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id, selected=layer_id)


@router.post(
    "/accounts/{ref}/program/{placement_id}/layers/{layer_id}"
    "/named-limits/{index}/remove",
    response_class=HTMLResponse,
)
def named_limit_remove(
    request: Request, ref: str, placement_id: str, layer_id: str, index: int
) -> HTMLResponse:
    """No confirm: a named limit is a name and a number, both visible, and the
    removal is one undo unit away (`u`, or the panel's Revert). The confirms in
    this module guard writes that CASCADE — removing a line takes layers with
    it — which this does not."""
    org = _org(request, ref)
    conn = _conn(request)
    placement, layer = _owned_layer(request, org, placement_id, layer_id)
    named = sync.named_limits_of(conn, placement_id, layer_id)
    label = next((n["name"] for n in named if n["index"] == index), f"limit {index}")
    try:
        program_files.write(
            conn, placement,
            tool="program_named_limit_remove",
            summary=f"removed the {label} limit from {layer['name']}",
            mutate=lambda: sync.remove_named_limit(conn, placement_id, layer_id, index),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _panel(
            request, ref, org, placement_id, selected=layer_id,
            worksheet_error=str(exc),
        )
    forget_program_reads(request)
    return _panel(request, ref, org, placement_id, selected=layer_id)
