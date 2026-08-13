"""The Information Requests sheet — PURE composition of what a client still
owes us, for the four-tab export (export_open_items.write() is the
assembler; this module never touches the xlsx renderer). One section per
outstanding request, sub-sectioned per category when a request uses them,
with an unlabelled trailing section for its uncategorised items — mirroring
General on the Open Items sheet. Received and waived items, and cancelled
requests, never appear: this is a client-facing "here is what we're still
waiting on" list, not an audit trail (that lives in the RFI tab itself).
Determinism: `today` is a parameter, never the wall clock, matching every
other composer in this package."""

from __future__ import annotations

import sqlite3
from datetime import date
from itertools import groupby

from babel.dates import format_date

from ..models import RfiItem, RfiRequest
from ..repo import rfi as rfi_repo
from . import rfi as rfi_svc
from .export_open_items import SheetSection, _status_label, flatten_markdown


def _fmt_date(iso: str) -> str:
    """ISO date -> the short client-facing form used in section labels
    ("5 Aug"), via Babel per the house parsing/formatting rule."""
    return format_date(date.fromisoformat(iso), format="d MMM", locale="en_US")


def _due_cell(item: RfiItem, request: RfiRequest) -> str:
    """services.rfi.effective_due rendered for a spreadsheet cell — an undated
    ask is a blank cell, not the em dash the TUI uses."""
    return rfi_svc.effective_due(item, request) or ""


def _earliest_due(items: list[RfiItem], request: RfiRequest) -> str | None:
    dues = [d for i in items if (d := rfi_svc.effective_due(i, request))]
    return min(dues) if dues else None


def _item_row(item: RfiItem, request: RfiRequest) -> tuple[str, str, str, str]:
    return (
        item.prompt,
        flatten_markdown(item.detail or ""),
        _status_label(item.kind),
        _due_cell(item, request),
    )


def _request_sections(
    conn: sqlite3.Connection, request: RfiRequest, items: list[RfiItem]
) -> list[SheetSection]:
    """`items` is already outstanding-only, in items_for_request's order
    (category, nulls last, then creation) — exactly the order the spec asks
    for, so no re-sort happens here."""
    prefix = f"{rfi_svc.asker_name(conn, request)} — {request.title}"
    if not any(item.category for item in items):
        # No sub-grouping: one section carries the full request context.
        label = f"{prefix} · asked {_fmt_date(request.requested_on)}"
        if request.due_on:
            label += f" · due {_fmt_date(request.due_on)}"
        return [SheetSection(label, tuple(_item_row(i, request) for i in items))]

    sections: list[SheetSection] = []
    for category, group in groupby(items, key=lambda i: i.category):
        rows = tuple(_item_row(i, request) for i in group)
        # Uncategorised items trail, unlabelled — the request's context was
        # already stated by the category sections above them.
        category_label: str | None = f"{prefix} · {category}" if category else None
        sections.append(SheetSection(category_label, rows))
    return sections


def compose_information_requests(
    conn: sqlite3.Connection, org_id: str, today: date
) -> list[SheetSection]:
    """Sections per outstanding (request, category) pair, ready for
    render_table_sheet's Item | Detail | Type | Needed by columns. Empty
    list means the sheet is omitted entirely (the assembler's job).

    `today` takes no part in the filter — this sheet has no date window,
    unlike the 120-day chase queue — it exists only for signature symmetry
    with the rest of this package's composers and the no-wall-clock rule."""
    del today

    scored: list[tuple[RfiRequest, list[RfiItem], str | None]] = []
    for request in rfi_repo.requests_for_org(conn, org_id):
        if request.cancelled_at:
            continue
        outstanding = [
            item
            for item in rfi_repo.items_for_request(conn, request.id)
            if item.status == "outstanding"
        ]
        if outstanding:
            scored.append((request, outstanding, _earliest_due(outstanding, request)))

    # Requests by earliest outstanding due (undated last), then ref.
    scored.sort(key=lambda t: (t[2] is None, t[2] or "", t[0].ref))

    sections: list[SheetSection] = []
    for request, items, _due in scored:
        sections.extend(_request_sections(conn, request, items))
    return sections
