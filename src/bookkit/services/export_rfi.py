"""The Information Requests sheet — PURE composition of what a client still
owes us, for the four-tab export (export_open_items.write() is the
assembler; this module never touches the xlsx renderer). One section per
outstanding request, sub-sectioned per category when a request uses them,
with an unlabelled trailing section for its uncategorised items — mirroring
General on the Open Items sheet. Received and waived items, and cancelled
requests, never appear: this is a client-facing "here is what we're still
waiting on" list, not an audit trail (that lives in the RFI tab itself).
Determinism: `today` is a parameter, never the wall clock, matching every
other composer in this package.

THE INTERNAL RULE APPLIES HERE TOO, both halves of it — see
`_client_safe`. It did not until 2026-08-19: sheet 1's SCOPE_NOTE says
"Internal administrative items are not included" and speaks for the whole
workbook, while this sheet shipped an item categorised `Internal` under a
heading naming it. A scope note that is false about the document it appears
in is worse than either half alone: a reader who checks is told the wrong
thing by the document itself."""

from __future__ import annotations

import sqlite3
from datetime import date
from itertools import groupby

from babel.dates import format_date

from ..models import RfiItem, RfiRequest, is_internal_category, reads_as_internal
from ..repo import rfi as rfi_repo
from . import rfi as rfi_svc
from .export_open_items import SheetSection, flatten_markdown


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
    """Four wide, always. Whether the fourth column is PRINTED is the
    assembler's call (export_open_items: it appears only when something on the
    sheet has actually been answered), but composing it conditionally here
    would make the row shape depend on the account, and every downstream index
    — the row-height estimate reads values[1] — would have to ask which shape
    it got.

    Note what this column can and cannot show: the sheet is outstanding-only,
    so an answer is visible while the item is still open — an interim note,
    "controller is pulling it, expect Friday" — and leaves the sheet with its
    item the moment somebody marks it received."""
    return (
        item.prompt,
        flatten_markdown(item.detail or ""),
        _due_cell(item, request),
        flatten_markdown(item.response or ""),
    )


def _client_safe(items: list[RfiItem]) -> list[RfiItem]:
    """The two-tier internal rule C9 settled for sheet 1, applied to sheet 2.

    ONE RULE, TWO MATCHES, and the asymmetry is models'. `is_internal_category`
    is EXACT equality and decides whether a ROW ships: over-reach there
    silently deletes a real ask from the one list whose whole job is telling
    the client what they still owe us, and a thing never asked for is never
    sent. `reads_as_internal` is a PREFIX and decides only whether a HEADING
    may be printed: the rows underneath survive either way, so over-reach
    costs a heading the client would have found unremarkable and under-reach
    puts "Internal Review" in the client's workbook as a banner.

    A suppressed heading files its rows as UNCATEGORISED, which on this sheet
    is the trailing unlabelled section — the same move sheet 1 makes into
    General, for the same reason: a row whose category cannot be shown is, to
    this reader, uncategorised. The re-sort that follows is a no-op unless
    something was actually suppressed (Python's sort is stable and
    items_for_request already orders category-then-nulls-last), and it is
    needed because `groupby` only groups adjacent equals — a blanked category
    left mid-list would emit an unlabelled section BETWEEN two labelled ones,
    where an unbannered row reads as belonging to whichever section printed
    above it.

    There is no `include_internal` escape hatch here, unlike compose(): this
    module has exactly one caller and it is the client deliverable. A switch
    with no second caller is a way to get the default wrong later."""
    shown = [item for item in items if not is_internal_category(item.category)]
    unbannered = [
        item.model_copy(update={"category": None})
        if reads_as_internal(item.category)
        else item
        for item in shown
    ]
    return sorted(unbannered, key=lambda item: item.category is None)


def _request_sections(
    conn: sqlite3.Connection, request: RfiRequest, items: list[RfiItem]
) -> list[SheetSection]:
    """`items` is already outstanding-only and client-safe, in
    items_for_request's order (category, nulls last, then creation) — exactly
    the order the spec asks for, so no re-sort happens here (`_client_safe`
    restores nulls-last after it blanks a suppressed heading)."""
    prefix = f"{rfi_svc.asker_name(conn, request)} — {request.title}"
    if not any(item.category for item in items):
        # No sub-grouping: one section carries the full request context.
        # NO "asked <date>". It dated the ASKING, which tells the client how
        # long we have been waiting rather than anything they need to act on —
        # and on an ask that has been chased twice it reads as a reproach
        # (Grant, 2026-08-21). The DUE date stays: that one is theirs to act on.
        label = prefix
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


ANSWERED_LABEL = "already sent"
"""How the answered half is headed.

"Items we need from you" is the sheet's own banner and a false statement about
something they have already sent, so the answered sections carry their own
words rather than sitting silently under it."""


def _answered_sections(
    conn: sqlite3.Connection, request: RfiRequest, items: list[RfiItem]
) -> list[SheetSection]:
    """The asks this request has answers for, in one section per request.

    NOT sub-grouped by category the way the outstanding half is: the category
    bands exist to help somebody work down a list of things still to do, and
    nobody works down a list of things already done.
    """
    prefix = f"{rfi_svc.asker_name(conn, request)} — {request.title}"
    return [
        SheetSection(
            f"{prefix} · {ANSWERED_LABEL}",
            tuple(_item_row(item, request) for item in items),
        )
    ]


def compose_information_requests(
    conn: sqlite3.Connection, org_id: str, today: date
) -> list[SheetSection]:
    """Sections per outstanding (request, category) pair, ready for
    render_table_sheet's Item | Detail | Needed by columns. Empty
    list means the sheet is omitted entirely (the assembler's job).

    `today` takes no part in the filter — this sheet has no date window,
    unlike the 120-day chase queue — it exists only for signature symmetry
    with the rest of this package's composers and the no-wall-clock rule."""
    del today

    scored: list[tuple[RfiRequest, list[RfiItem], list[RfiItem], str | None]] = []
    for request in rfi_repo.requests_for_org(conn, org_id):
        if request.cancelled_at:
            continue
        # _client_safe runs BEFORE the emptiness test and before _earliest_due,
        # so a request whose only outstanding item is Internal omits its whole
        # section rather than printing an empty heading, and a withheld item's
        # date takes no part in ordering the sheet.
        every = rfi_repo.items_for_request(conn, request.id)
        outstanding = _client_safe([i for i in every if i.status == "outstanding"])
        # WHAT THEY HAVE ALREADY TOLD US. The sheet was outstanding-only, so an
        # answer left the client's copy the moment the item was marked received
        # — taking the record of what they sent with it (Grant, 2026-08-19).
        #
        # Only items carrying an ANSWER: the point is keeping what they told
        # us, and a received item nobody recorded an answer for says nothing
        # they do not already know. _client_safe runs over these too — the
        # internal rule withholding an outstanding ask and then shipping it the
        # moment it is answered would be the same leak, delayed.
        answered = _client_safe([
            i for i in every if i.status != "outstanding" and i.response
        ])
        if outstanding or answered:
            scored.append(
                (request, outstanding, answered, _earliest_due(outstanding, request))
            )

    # Requests by earliest outstanding due (undated last), then ref. A request
    # with nothing outstanding has no due date to sort on and lands with the
    # undated — which is where a finished ask belongs anyway.
    scored.sort(key=lambda t: (t[3] is None, t[3] or "", t[0].ref))

    # EVERY outstanding section first, then every answered one. Not per
    # request: "what you still owe us" is the list this sheet exists for, and
    # interleaving finished asks through it would bury the live ones.
    sections: list[SheetSection] = []
    for request, items, _answered, _due in scored:
        if items:
            sections.extend(_request_sections(conn, request, items))
    for request, _items, answered, _due in scored:
        if answered:
            sections.extend(_answered_sections(conn, request, answered))
    return sections


def _considered_items(conn: sqlite3.Connection, org_id: str) -> list[RfiItem]:
    """Every item this sheet would consider before the internal rule runs, on a
    request that has not been cancelled.

    THE SAME POPULATION compose_information_requests walks, which is the whole
    point: the counts below report what the workbook actually withheld, and a
    narrower population here would under-report it silently. That is not
    hypothetical — this was outstanding-only until answered items joined the
    sheet on 2026-08-19, at which point an Internal item that had been answered
    was withheld from the client and counted by nobody."""
    items: list[RfiItem] = []
    for request in rfi_repo.requests_for_org(conn, org_id):
        if request.cancelled_at:
            continue
        items += [
            item
            for item in rfi_repo.items_for_request(conn, request.id)
            if item.status == "outstanding" or item.response
        ]
    return items


def withheld_items(conn: sqlite3.Connection, org_id: str) -> list[RfiItem]:
    """Asks kept off the client's copy — exactly what
    `_client_safe` drops. Withholding a ROW from this sheet is the one place
    the internal rule can cost the client something they were meant to send,
    so it is never silent: export_open_items.withheld_note names the count on
    the CLI line and the TUI toast, beside the task count it already gave."""
    return [i for i in _considered_items(conn, org_id) if is_internal_category(i.category)]


def near_miss_items(conn: sqlite3.Connection, org_id: str) -> list[RfiItem]:
    """Outstanding asks whose category READS internal without equalling it —
    "Internal Review", "internal note". These SHIPPED; only their heading was
    suppressed. Same shape, and same reason for saying so out loud, as
    export_open_items.near_miss_internal does for tasks."""
    return [
        i for i in _considered_items(conn, org_id)
        if reads_as_internal(i.category) and not is_internal_category(i.category)
    ]
