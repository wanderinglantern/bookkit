"""The Towers page: the whole book's programs as a QUEUE, not a gallery
(design 2D, 2026-08-24).

A card states the ONE fact that would make you open it — an error in
towerkit's own words, the unplaced dollars, the renewal countdown — and
opening it lands on the layer that fact is about, not the top of the
program. The validator decides the order: errors, then unplaced capacity
descending, then the renewal date.

READ-ONLY by design — each card links to its account's Program tab, where
the editing grammar lives; a second editing surface would fork the contract
phases 1–4 settled.

A file that fails validation renders its badge and its reason instead of a
drawing — a page that 500s because one program is mid-edit in towerkit would
hide every other tower in the book. The badge colours carry words, per the
colour-is-signal rule.

THE RENEWAL DATE IS THE ONE YOU COUNT TO (the standing rule): the countdown
runs to the earliest LINE end (`sync.line_ends_of`), never period_to — an IM
layer three months early is exactly the case the rule exists for.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ... import sync
from ...money import format_cents_compact
from ...repo import orgs, placements
from ...services import renewals
from ..app import TEMPLATES
from ..tower import panel

router = APIRouter()

_FILTERS = ("needs-work", "open", "renewing", "all")
RENEWING_WINDOW_DAYS = 90


def _entries(request: Request) -> list[dict[str, Any]]:
    from towerkit.validate import validate_file

    from .account import _conn

    conn = _conn(request)
    linked = placements.all_linked(conn)
    names = orgs.names_for(conn, {p.org_id for p in linked})
    # One guarded fetch per unique org, not one per placement — and GUARDED:
    # org deletion is a soft delete with no cascade to placements, so a
    # linked placement can outlive its account (a missing key renders
    # "(deleted account)" and no link).
    refs: dict[str, str | None] = {}
    for org_id in {p.org_id for p in linked}:
        try:
            refs[org_id] = str(orgs.get(conn, org_id).ref)
        except KeyError:
            refs[org_id] = None
    today = date.today()
    entries: list[dict[str, Any]] = []
    for placement in linked:
        # GUARDED per placement: sync.program_file RAISES when the stored
        # path cannot be resolved, and one moved file must not 500 the whole
        # queue — the module's own promise (review C3). The card carries the
        # reader's sentence instead; recovery stays read-only (bookctl
        # relink is the writer).
        path = sync.program_file_or_none(conn, placement)
        if path is None:
            entries.append({
                "placement": placement,
                "account": names.get(placement.org_id, "(deleted account)"),
                "org_ref": refs.get(placement.org_id),
                "kind": "error",
                "badge": "error",
                "badge_kind": "error",
                "reason": sync.linked_program(conn, placement.id).error
                or "this program file will not open",
                "jump_layer": None,
                "unplaced_cents": 0,
                "renewal_on": placement.period_to,
                "days": (
                    date.fromisoformat(placement.period_to) - today
                ).days,
                "tower": None,
            })
            continue
        program, diags = validate_file(path)
        rows = sync.layer_details_of(program)
        placeable = [
            row for row in rows if not row["buffer"] and not row["statutory"]
        ]
        unplaced_cents = sum(row["open_limit_cents"] for row in placeable)
        # The one-line reason and the layer it lands on are the VALIDATOR's:
        # the first error, then the first layer-unplaced warning on a layer
        # that can actually be placed — a buffer's own unplaced warning
        # describes a CHOSEN uninsured band and must not read as work
        # (review C5) — then any OTHER warning (a gap is a warning by
        # design and belongs in this queue, review C8).
        first_error = next(iter(diags.errors), None)
        special = {
            row["id"] for row in rows if row["buffer"] or row["statutory"]
        }
        unplaced_warning = next(
            (
                d for d in diags.warnings
                if d.code == "layer-unplaced"
                and not (d.ref and d.ref[0] == "layer" and d.ref[1] in special)
            ),
            None,
        )
        other_warning = next(
            (d for d in diags.warnings if d.code != "layer-unplaced"), None
        )
        # THE SERVICE'S RULE, not a second copy of it. This was
        # `min(ends)` — uncapped — while `services.renewals.renewal_on` caps
        # the earliest line end by the program period end, so a program whose
        # layers are written past their own period had this page saying 281
        # days where the service said 20, and the `renewing` filter below
        # measuring off the wrong one (2026-08-24).
        renewal_on = renewals.renewal_on(
            placement, [end for _, end in sync.line_ends_of(program)]
        )
        days = (renewal_on - today).days

        if first_error is not None:
            kind, reason = "error", first_error.message
            said = first_error
        elif unplaced_warning is not None:
            kind, reason = "open", unplaced_warning.message
            said = unplaced_warning
        elif other_warning is not None:
            kind, reason = "warn", other_warning.message
            said = other_warning
        elif days <= RENEWING_WINDOW_DAYS:
            kind = "renewing"
            reason = f"renews {renewal_on.isoformat()}"
            said = None
        else:
            kind = "ok"
            total = sum(row["limit_cents"] for row in placeable)
            reason = f"{format_cents_compact(total)} · fully signed"
            said = None
        jump = (
            said.ref[1]
            if said is not None and said.ref and said.ref[0] == "layer" and said.ref[1]
            else None
        )
        badge = (
            "error"
            if kind == "error"
            else "open"
            if kind == "open"
            else "warn"
            if kind == "warn"
            else (f"{days}d" if days >= 0 else f"{-days}d over")
            if kind == "renewing"
            else "ok"
        )
        entries.append(
            {
                "placement": placement,
                "account": names.get(placement.org_id, "(deleted account)"),
                "org_ref": refs.get(placement.org_id),
                "kind": kind,
                "badge": badge,
                "badge_kind": (
                    "error" if kind == "error"
                    else "warn" if kind in ("open", "renewing", "warn")
                    else "ok"
                ),
                "reason": reason,
                "jump_layer": jump,
                "unplaced_cents": unplaced_cents,
                "renewal_on": renewal_on.isoformat(),
                "days": days,
                "tower": panel(program) if program is not None and diags.ok else None,
            }
        )
    # THE VALIDATOR DECIDES THIS ORDER: errors, then unplaced dollars
    # descending, then the renewal date — the queue's whole point.
    entries.sort(
        key=lambda e: (
            0 if e["kind"] == "error" else 1,
            -e["unplaced_cents"],
            e["renewal_on"],
        )
    )
    return entries


@router.get("/towers", response_class=HTMLResponse)
def towers_page(request: Request, show: str = "needs-work") -> HTMLResponse:
    entries = _entries(request)
    if show not in _FILTERS:
        show = "needs-work"
    subsets: dict[str, list[dict[str, Any]]] = {
        # a warning IS work — a gap program must not hide under "ok" (C8)
        "needs-work": [
            e for e in entries
            if e["kind"] in ("error", "warn") or e["unplaced_cents"] > 0
        ],
        "open": [e for e in entries if e["unplaced_cents"] > 0],
        "renewing": [e for e in entries if e["days"] <= RENEWING_WINDOW_DAYS],
        "all": entries,
    }
    return TEMPLATES.TemplateResponse(
        request, "towers.html",
        {
            "entries": subsets[show],
            "show": show,
            "counts": {name: len(rows) for name, rows in subsets.items()},
            "window_days": RENEWING_WINDOW_DAYS,
            "total": len(entries),
        },
    )
