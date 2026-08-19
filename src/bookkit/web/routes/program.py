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

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...money import format_cents_compact
from ...repo import placements as placements_repo
from ..app import TEMPLATES
from .account import _conn, _context, _org, layers_for

router = APIRouter()


def _money(cents: int | None) -> str:
    """An em dash for UNRECORDED, never for zero.

    pipeline.py's _money treats 0 and None alike, which is right for a quote
    premium — a quote with no premium recorded is not a quote for nothing.
    It is WRONG for an attachment point: a primary layer attaches at $0, and
    that is a fact about the tower, not a gap in the record. Rendering it as
    an em dash tells a reader the attachment is unknown when it is known and
    is zero (caught by looking at the rendered page, 2026-08-19).

    So: None is "—", and every real number formats, zero included.
    """
    return "—" if cents is None else format_cents_compact(cents)


def _layer_row(layer: dict[str, Any]) -> dict[str, Any]:
    """One layer, formatted. Money becomes strings here rather than in the
    template: there are no Jinja filters in this app, and arithmetic or
    formatting inside a template is work no test can reach.

    `signed_pct` and the participant shares are DERIVED (towerkit sums the
    participants), which is why they are read-only even once phase 2 makes the
    money and dates editable — a cell that edits a derived value writes
    nothing and reads as broken.
    """
    return {
        "id": layer["id"],
        "name": layer["name"],
        "attach": _money(layer["attach_cents"]),
        # A statutory layer carries benefits and NO dollar limit: towerkit
        # forces limit == 0 and draws it off-scale. Printing "$0" here would
        # render unlimited statutory cover as cover worth nothing — opposite
        # facts that arithmetic alone makes identical.
        "limit": "statutory" if layer["statutory"] else _money(layer["limit_cents"]),
        # premium is the one column where zero and unrecorded really do read
        # alike: towerkit stores None for "not priced yet", and a layer priced
        # at exactly nothing is not a thing. layer_details already gives None.
        "premium": _money(layer["premium_cents"]),
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
                "layers": [_layer_row(layer) for layer in layers],
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
