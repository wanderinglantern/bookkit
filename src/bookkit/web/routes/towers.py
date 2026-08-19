"""The Towers page: every drawn tower across the book, in one place.

The nav item existed as an inert span for two days before D4 unrendered it;
it returns here as a real route. READ-ONLY by design — each tower links to
its account's Program tab, where the editing grammar lives; a second editing
surface would fork the contract phases 1–4 settled.

A file that fails validation renders its badge and its errors instead of a
drawing — a page that 500s because one program is mid-edit in towerkit would
hide every other tower in the book. The badge colours carry words (ok /
errors / warnings), per the colour-is-signal rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...repo import orgs, placements
from ..app import TEMPLATES
from ..tower import panel

router = APIRouter()


def _entries(request: Request) -> list[dict[str, Any]]:
    from towerkit.validate import validate_file

    from .account import _conn

    conn = _conn(request)
    linked = placements.all_linked(conn)
    names = orgs.names_for(conn, {p.org_id for p in linked})
    entries: list[dict[str, Any]] = []
    for placement in sorted(
        linked, key=lambda p: (names.get(p.org_id, ""), p.period_from)
    ):
        program, diags = validate_file(Path(str(placement.program_path)))
        badge = (
            "ok"
            if diags.ok and not diags.warnings
            else (
                f"{len(diags.errors)} error{'s' if len(diags.errors) != 1 else ''}"
                if diags.errors
                else f"{len(diags.warnings)} warning{'s' if len(diags.warnings) != 1 else ''}"
            )
        )
        entries.append(
            {
                "placement": placement,
                "account": names.get(placement.org_id, "(deleted account)"),
                "org_ref": _org_ref(conn, placement.org_id),
                "badge": badge,
                "badge_kind": (
                    "ok" if diags.ok and not diags.warnings
                    else ("error" if diags.errors else "warn")
                ),
                "problems": [str(d) for d in diags.errors],
                "tower": panel(program) if program is not None and diags.ok else None,
            }
        )
    return entries


def _org_ref(conn: Any, org_id: str) -> str:
    return str(orgs.get(conn, org_id).ref)


@router.get("/towers", response_class=HTMLResponse)
def towers_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "towers.html", {"entries": _entries(request)}
    )
