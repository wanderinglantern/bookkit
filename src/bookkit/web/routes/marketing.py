"""The marketing grid's WRITES: what each market said, edited where it prints.

THE PANEL IS THE REPORT (Grant, 2026-08-25), so there is no second entry form
here and there must never be one — every figure is corrected in the cell that
shows it, through the same `macros/cell.html` contract contacts, tasks and
layer cells use. Only the ADD row is a form, because six fields are filled in
one go and tabbing between them must not commit half an approach (CLAUDE.md:
inline commit is not the rule for whole forms).

WHY A SAVE ANSWERS WITH THE WHOLE ROW rather than the cell alone. Four of the
cells feed a fifth: `MarketResponse.total_cost` is premium + TRIA + fees + tax
and is blank while any of them is unknown, so a response carrying only the
edited `<td>` would leave a stale Total sitting beside a figure that had just
moved — on a grid whose whole purpose is that a client can compare two quotes
on their total cost. The row is still ONE TOP-LEVEL ELEMENT and it says where
it goes with HX-Retarget/HX-Reswap, exactly as routes/program.py `_panel`
does for the section. A REFUSAL answers with the editor cell alone, because a
refused save changed nothing and commit-in-place has to keep the typed value
under the caret.

AND WHY THREE OF THEM ANSWER WITH THE WHOLE BLOCK. The premium bridge and the
clearance strip are BLOCK facts read off the rows beneath them — the bridge
decomposes the leading quote's premium and rate, the strip counts open
statuses — so premium, rate and status cannot be answered honestly with a row
(`_BLOCK_CELLS`, and what each one was showing before). The rule underneath is
the same one all the way up: answer with the smallest thing the write can
actually change, and never with something smaller than that.

This module owns no SQL and no rules. The submission-reuse rule is
services.marketing_entry's (shared with MCP's `market_approach`), the status
roll-up and the clearance report are repo.marketing's, and the vocabulary is
models'. What lives here is the account scope check, the parse, the batch, and
the shape of the answer.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...forms.inline import market_approach_fields, market_response_fields
from ...forms.spec import Field, checked_option, parse_value
from ...models import MarketResponse
from ...repo import lines as lines_repo
from ...repo import marketing as marketing_repo
from ...repo import orgs as orgs_repo
from ...repo import placements as placements_repo
from ...repo import submissions as submissions_repo
from ...services import batches as batches_svc
from ...services import consistency, marketing_entry, marketing_report
from .. import marketing_grid
from ..app import TEMPLATES
from ..forms_render import render_cell, render_cell_display
from .account import _conn, _not_here, _org, _owned


async def _remember_sort(request: Request) -> None:
    """THE ORDER A READER ASKED FOR, read ONCE for every route in this module.

    A ROUTER-LEVEL DEPENDENCY, not a line in each handler. Twenty call sites
    in here compose or re-render a panel, and a read written per route is
    nineteen chances to forget one — and a forgotten one is not a crash, it is
    a view that silently resets to the composer's default the next time a
    broker edits a cell. So it is taken before any handler runs and the three
    answer helpers read it off `request.state`, synchronously, wherever they
    are called from.

    BOTH HALVES OF THE REQUEST. The section publishes the spec as an inherited
    `hx-vals`, which htmx sends as a FORM FIELD on a POST and as a QUERY
    PARAMETER on a GET. Awaiting the form here is free for the handler that
    also awaits it: Starlette parses once and caches on the request.

    NOT VALIDATED HERE. `marketing_grid.parse_sorts` drops what it cannot read
    and `panel` re-formats from what it actually APPLIED, so a spec naming a
    column this grid cannot order simply does not sort — no refusal, and no
    page left claiming an order it is not in.
    """
    spec = str(request.query_params.get("sort", ""))
    if request.method == "POST":
        typed = (await request.form()).get("sort")
        if typed is not None:
            spec = str(typed)
    request.state.sort_spec = spec


def _sorted_by(request: Request) -> str:
    """What `_remember_sort` put there. Defaulted rather than assumed: this
    module's helpers are also called from routes/program.py's first render of
    the tab, which is a different router and never ran that dependency."""
    return str(getattr(request.state, "sort_spec", "") or "")


# EVERY ROUTE IN THIS MODULE READS THE ORDER, whether it thinks it needs to or
# not — see `_remember_sort`. The dependency is on the ROUTER so a route added
# later inherits it without anybody remembering to.
router = APIRouter(dependencies=[Depends(_remember_sort)])


# --- resolution and scope ---------------------------------------------------


def _placement(request: Request, ref: str, placement_id: str) -> tuple[Any, Any]:
    org = _org(request, ref)
    placement = _owned(
        _conn(request), org, "placement", placement_id, placements_repo.get
    )
    return org, placement


def _owned_response(
    conn: sqlite3.Connection, org: Any, placement_id: str, response_id: str
) -> MarketResponse:
    """BOTH ids in the URL are claims, and both are checked — the shape
    `_owned_item` settled for an RFI item. The response has to exist, its
    submission has to be on THIS placement, and the placement has to be this
    account's (checked by `_owned` before this is called). Without the middle
    check a response could be edited under a placement it does not belong to,
    and the row that came back would not be the row the write touched.

    Both refusals are the same 404, deliberately: telling "no such id" from
    "someone else's id" apart is how a guessable id becomes a membership
    oracle."""
    try:
        response = marketing_repo.get_response(conn, response_id)
    except KeyError:
        raise _not_here("market response", response_id, org) from None
    try:
        submission = submissions_repo.get(conn, response.submission_id)
    except KeyError:  # a soft-deleted submission carries nothing onto the page
        raise _not_here("market response", response_id, org) from None
    if submission.placement_id != placement_id:
        raise _not_here("market response", response_id, org)
    return response


def _field(key: str, conn: sqlite3.Connection | None = None) -> Field:
    """The editable set, checked SERVER-SIDE. The markup constrains a mouse and
    nothing else — every route is reachable by anything that can POST, and the
    keys deliberately left out of MARKET_RESPONSE_FIELDS (the CARRIER, the
    total, the rate movement) are left out for reasons a URL must not be able
    to talk its way past.

    `conn` is the access point's completion list and nothing else. Pass it
    wherever a broker TYPES — the editor, and the refusal it comes back as —
    and leave it off where a value is only printed: a display cell that
    queried the book's whole market list once per row would pay for a datalist
    nobody can open."""
    field = marketing_grid.CELL_FIELDS.get(key)
    if field is None:
        raise HTTPException(
            status_code=404, detail=f"{key!r} is not editable on a market response"
        )
    if conn is not None and key == marketing_grid.VIA:
        return next(f for f in market_response_fields(conn) if f.key == key)
    return field


def _house(exc: Exception) -> str:
    """The refusal, IN THIS BOOK'S OWN WORDS, never a library's.

    Every guard under repo/ and services/ raises ValueError carrying a sentence
    written for a broker, and that sentence IS the message — this is the one
    thing that must keep reaching the cell. Anything else arriving here is some
    library speaking about its own internals: a pasted 20-digit premium
    answered "Python int too large to convert to SQLite INTEGER" in the premium
    cell (found 2026-08-25). That names no fix, says nothing about what was
    typed, and reads as a crash the app is pretending to have survived.

    The figure that produced it is now refused by money.py's own ceiling before
    any writer sees it, which is where a bad VALUE belongs. This is the floor
    under that: whatever else breaks, the sentence a broker reads is one this
    book wrote.
    """
    return (
        str(exc)
        if isinstance(exc, ValueError)
        else (
            "that could not be saved and nothing was written — check the "
            "figure and try again, and say so if it keeps happening"
        )
    )


def _via_name(conn: sqlite3.Connection, response: MarketResponse) -> str:
    """The intermediary's NAME, or "" for a direct approach.

    THE ONE LOOKUP. `via_org_id` is stored as an id and typed as a name, and
    both halves of the cell contract need the name — the display prints it, the
    editor pre-fills it — so resolving it twice in two places is the copy that
    quietly differs. An id with no org behind it comes back blank rather than
    as a ULID: a deleted intermediary reads as a direct approach, which is
    wrong in the safe direction (it is correctable in the cell), where printing
    `01J…` reads as data.
    """
    if not response.via_org_id:
        return ""
    return orgs_repo.names_for(conn, {response.via_org_id}).get(
        response.via_org_id, ""
    )


def _access_point(conn: sqlite3.Connection, typed: str) -> str | None:
    """The market a typed access point names, `None` for a direct approach.

    BLANK IS AN ANSWER, and it is the one Grant asked for: an approach recorded
    through a wholesaler that actually went straight to the carrier is cleaned
    up by emptying this cell, not by finding a word that means "nobody". That
    is why the cell prints `direct` and pre-fills empty — the display says what
    the blank MEANS and the editor stays something its own parser accepts back
    unchanged.

    A MISS IS REFUSED, not degraded. `via_org_id` is a foreign key, so unlike
    an assignee there is no freeform half to fall back to (repo/assignees.py
    explains why that field HAS one), and unlike the add row two rows down
    there is no half-typed approach to lose by sending the reader elsewhere —
    this cell holds exactly one value. So the refusal names the nearest markets
    to type instead, says blank means direct, and names the door that makes a
    market the book has never carried.
    """
    typed = typed.strip()
    if not typed:
        return None
    org = orgs_repo.find_market(conn, typed)
    if org is not None:
        return org.id
    # THE SCORES ARE PRINTED, the same reading the add row's card takes:
    # "91% alike" is a fact a broker can judge and "did you mean…" is not.
    near = ", ".join(
        f"{match.name} ({score}% alike)"
        for match, score in orgs_repo.near_markets(conn, typed)
    )
    # ONE f-STRING, not a `+` chain. The G5 walk reads a refusal off the AST
    # and only sees a `Constant` or a `JoinedStr` — a sentence assembled with
    # `+` is a `BinOp` and goes unwalked, which is a refusal nobody is made to
    # declare a fix for.
    nearest = f" — nearest: {near}" if near else ""
    raise ValueError(
        f"no market on this book is called {typed!r}{nearest}. Type one of "
        "those, leave it blank for a direct approach, or add the market on "
        "the Markets page first."
    )


def _who(conn: sqlite3.Connection, response: MarketResponse) -> str:
    """The name for the undo sentence: the carrier, or the intermediary while
    the paper is still unnamed."""
    names = orgs_repo.names_for(
        conn, {response.market_org_id or "", response.via_org_id or ""} - {""}
    )
    return (
        names.get(response.market_org_id or "")
        or names.get(response.via_org_id or "")
        or "a market"
    )


# --- one cell ---------------------------------------------------------------


def _display_cell(
    request: Request,
    ref: str,
    placement_id: str,
    response: MarketResponse,
    key: str,
    *,
    placement: Any = None,
) -> HTMLResponse:
    """One cell, as it reads.

    The PLACEMENT comes with it because a date cell prints its year unless the
    date falls in this placement's own marketing window, and a cell rendered
    without that window would answer with a different string from the grid it
    is swapped back into (marketing_report.fmt_date).
    """
    window = (
        marketing_report.window_for(placement.period_from, placement.period_to)
        if placement is not None
        else None
    )
    conn = _conn(request)
    return HTMLResponse(
        render_cell_display(
            request,
            _field(key),
            _plain(
                marketing_grid.display_value(
                    response, key, window, via_name=_via_name(conn, response)
                )
            ),
            marketing_grid.cell_action(ref, placement_id, response.id, key),
            extra_class=marketing_grid.cell_class(key, response),
        )
    )


def _plain(text: str) -> str:
    """The macro prints its own em-dash for an empty value, so the house dash
    must not be handed to it as content — that renders a literal "—" the user
    can then open and see as typed text."""
    return "" if text == marketing_grid.DASH else text


def _editor_cell(
    request: Request,
    ref: str,
    placement_id: str,
    response: MarketResponse,
    key: str,
    *,
    error: str | None = None,
    typed: str | None = None,
) -> HTMLResponse:
    conn = _conn(request)
    value = (
        typed
        if typed is not None
        else marketing_grid.editor_value(
            response, key, via_name=_via_name(conn, response)
        )
    )
    return HTMLResponse(
        render_cell(
            request,
            # THE COMPLETION LIST RIDES ON THE EDITOR, and on the refusal it
            # comes back as. This is the only half of the contract a broker
            # types into, and a refused access point re-rendered without its
            # datalist would take the suggestions away at the exact moment the
            # name typed was one the book does not carry.
            _field(key, conn),
            value,
            marketing_grid.cell_action(ref, placement_id, response.id, key),
            error=error,
            extra_class=marketing_grid.cell_class(key, response),
        )
    )


def _row_response(
    request: Request,
    ref: str,
    placement_id: str,
    response_id: str,
    *,
    refocus: str | None = None,
) -> HTMLResponse:
    """The whole grid row, re-composed, retargeted onto itself.

    Re-composing the report to answer with one row is six queries for a row
    already on screen — and it is the right price. The alternative is this
    module deriving `total_cost`, the rate movement and the layer sentence
    itself, which is a second composition of the client-facing report living
    in a route, and the copy that differs would be the one the broker reads
    while the client's workbook says something else.

    `refocus` rides back as `data-refocus="cell:<key>"`; inline-cell.js reads
    it off whatever element landed and puts the caret back on the cell the
    save replaced, so a run of edits down one column stays a run.
    """
    conn = _conn(request)
    report = marketing_grid.panel(
        request, conn, placement_id, today=date.today(), ref=ref,
        sort_spec=_sorted_by(request),
    )
    row = next(
        (
            r
            for block in report["blocks"]
            for r in block["rows"]
            if r["id"] == response_id
        ),
        None,
    )
    if row is None:  # deleted under the page; nothing honest to swap in
        raise HTTPException(status_code=404, detail="no such market response")
    row = dict(row, refocus=refocus)
    template = TEMPLATES.env.get_template("macros/marketing.html")
    html = str(template.make_module({}).row(row, report["columns"]))  # type: ignore[attr-defined]
    response = HTMLResponse(html)
    # ONE RESPONSE, ONE TOP-LEVEL ELEMENT, and it says where it goes rather
    # than riding out of band behind another fragment. The id is prefixed
    # because a bare ULID starts with a digit and `#01J…` is not a valid CSS
    # id selector.
    response.headers["HX-Retarget"] = f"#mrow-{response_id}"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


_CELL = "/accounts/{ref}/program/{placement_id}/marketing/responses/{response_id}/cell/{key}"

# THE THREE CELLS THAT MOVE MORE THAN THEIR OWN ROW, so the three that answer
# with the whole BLOCK (found 2026-08-25, in a browser).
#
# * `premium` and `rate_micros` are what the block's premium BRIDGE is
#   decomposed from — it walks the expiring premium to the LEADING quote's
#   premium through that quote's rate (marketing_report._block). Answering with
#   the row left the bridge printing a premium the grid no longer held: two
#   different premiums for one market, four inches apart, on a panel whose
#   whole purpose is comparing quotes. Correcting the rate silently inverted
#   the rate effect the same way.
# * `status` decides WHICH row leads, and whether any does — declining the
#   leader has to take the bridge away with it, not leave a whole walk standing
#   for a quote that is gone. It is also what the CLEARANCE strip counts
#   (repo.marketing.clearance_conflicts looks at open statuses only), and
#   declining the duplicate approach is the very act that resolves the
#   conflict; the warning stood there until the tab was reloaded.
#
# NOT EVERY CELL. A block answer re-composes the report for one line of
# coverage, and the other eight cells cannot move a figure outside their own
# row — a per-keystroke-close re-compose is a real cost and buys nothing there.
# `responded_on` is the near miss and stays a row on purpose: it is only a
# tie-break in the row ORDER between two rows already identical in status and
# premium, and no figure anywhere moves with it.
#
# * `via_org_id` is the fourth, and for the CLEARANCE half of the same reason
#   `status` is: a conflict is the same carrier reached twice on one line
#   through DIFFERENT intermediaries (repo.marketing.clearance_conflicts
#   compares exactly that), so turning a wholesaler approach into a direct one
#   either raises the warning or takes it away — and the strip that prints it
#   lives in the block header, above the row.
_BLOCK_CELLS = frozenset({"premium", "rate_micros", "status", marketing_grid.VIA})


def _orders_the_block(request: Request, line_id: str, key: str) -> bool:
    """Whether this write changes the figure the block is currently SORTED by.

    THE FIFTH BLOCK CELL, AND IT IS NOT A FIXED ONE. The four above answer with
    the block because of what they move above the rows — the bridge, the
    clearance strip. A sorted column moves the ROWS THEMSELVES: with the grid
    ordered by Expires, typing an expiry that belongs three rows up and getting
    a row-sized answer leaves the row sitting exactly where it was, in a column
    that now visibly is not in order. The grid would be lying about the one
    thing the reader asked it to do.

    Keyed on the COLUMN, not the field: `marketing_report.SORT_KEYS` and
    `marketing_grid.COLUMNS` share their keys, and the editable columns share
    them with the `market_response` field they write — the one exception being
    `rate_micros`, whose column is `rate` and which is in `_BLOCK_CELLS`
    already for the bridge.
    """
    column, _ = marketing_grid.parse_sorts(_sorted_by(request)).get(
        line_id, ("", False)
    )
    return bool(column) and column == key


@router.get(_CELL, response_class=HTMLResponse)
def response_cell(
    request: Request, ref: str, placement_id: str, response_id: str, key: str
) -> HTMLResponse:
    org, placement = _placement(request, ref, placement_id)
    response = _owned_response(_conn(request), org, placement_id, response_id)
    _field(key)
    return _display_cell(
        request, ref, placement_id, response, key, placement=placement
    )


@router.get(_CELL + "/edit", response_class=HTMLResponse)
def response_cell_edit(
    request: Request, ref: str, placement_id: str, response_id: str, key: str
) -> HTMLResponse:
    org, _ = _placement(request, ref, placement_id)
    response = _owned_response(_conn(request), org, placement_id, response_id)
    _field(key)
    return _editor_cell(request, ref, placement_id, response, key)


@router.post(_CELL, response_class=HTMLResponse)
async def response_cell_save(
    request: Request, ref: str, placement_id: str, response_id: str, key: str
) -> HTMLResponse:
    """One field, one writer action, one batch.

    `services.marketing_entry.responded` is where this and MCP's
    `market_responded` meet, and `repo.marketing.edit_response` under it is
    the writer for every surface — it rolls the submission's status up
    afterwards, inside this batch, so the parent row moving is part of the
    same undo unit rather than a change nobody can take back. The service is
    in the middle for the one rule repo/ cannot hold: a date that witnesses an
    act needs a TODAY to be judged against."""
    org, _ = _placement(request, ref, placement_id)
    conn = _conn(request)
    response = _owned_response(conn, org, placement_id, response_id)
    field = _field(key, conn)
    raw = str((await request.form()).get(key, ""))
    if key == marketing_grid.VIA:
        # A NAME IN, AN ID OUT. Resolved here rather than in `parse_value`
        # because resolution needs the book, and refused here rather than
        # silently stored because `via_org_id` is a foreign key — there is no
        # freeform half to degrade to, the way repo.assignees has for a person
        # the book has never heard of.
        try:
            value = _access_point(conn, raw)
        except ValueError as exc:
            return _editor_cell(
                request, ref, placement_id, response, key,
                error=str(exc), typed=raw,
            )
    else:
        try:
            value = parse_value(field, raw)
        except ValueError as exc:
            return _editor_cell(
                request, ref, placement_id, response, key,
                error=str(exc), typed=raw,
            )
    if field.required and value in (None, ""):
        return _editor_cell(
            request, ref, placement_id, response, key,
            error=f"{field.label} is required", typed=raw,
        )
    who = _who(conn, response)
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="market_responded",
            summary=f"set {field.label} on {who}", org_id=org.id,
        ):
            marketing_entry.responded(conn, response_id, {key: value})
    except Exception as exc:  # a refused save is a message, never a 500
        return _editor_cell(
            request, ref, placement_id, response, key, error=_house(exc), typed=raw
        )
    if key in _BLOCK_CELLS or _orders_the_block(request, response.line_id, key):
        # The refocus token names THIS ROW, not "cell" — inside a block there
        # are as many `premium` cells as there are markets, and `cell:premium`
        # would put the caret in the first row's.
        return _block_response(
            request, ref, placement_id, response.line_id,
            refocus=f"{response_id}:{key}",
        )
    return _row_response(
        request, ref, placement_id, response_id, refocus=f"cell:{key}"
    )


# --- "no fees or taxes apply" ----------------------------------------------


_ZERO_CHARGES = {"tria_premium": 0, "policy_fees": 0, "surplus_lines_tax": 0}


@router.post(
    "/accounts/{ref}/program/{placement_id}/marketing/responses/{response_id}/no-charges",
    response_class=HTMLResponse,
)
def response_no_charges(
    request: Request, ref: str, placement_id: str, response_id: str
) -> HTMLResponse:
    """"We asked, and there is none" — said once, for all three.

    NULL AND ZERO ARE DIFFERENT ANSWERS (models.MarketResponse.total_cost).
    NULL is "nobody has told us" and keeps the Total honestly blank; 0 is a
    figure a market gave us and is what makes a Total possible at all. On
    ordinary admitted domestic business all three are genuinely zero, so
    without this the Total column stays blank forever — or a broker types
    three zeros and the book records three separate edits for one fact.

    ONE BATCH, so `u` takes all three back together: they were one answer.
    It does NOT overwrite a figure already recorded — writing 0 over a stated
    surplus lines tax would be this control quietly destroying a number
    somebody was given, and `base.update` only logs what actually changes.
    """
    org, _ = _placement(request, ref, placement_id)
    conn = _conn(request)
    response = _owned_response(conn, org, placement_id, response_id)
    changes = {
        key: 0 for key in _ZERO_CHARGES if getattr(response, key) is None
    }
    if not changes:
        # A REFUSAL SAYS SOMETHING — but there is nothing to refuse here: all
        # three are already known, the button is not rendered in that state,
        # and answering with the unchanged row is the honest no-op.
        return _row_response(request, ref, placement_id, response_id)
    with batches_svc.open_batch(
        conn, source="web", tool="market_responded",
        summary=f"no fees or taxes from {_who(conn, response)}", org_id=org.id,
    ):
        marketing_repo.edit_response(conn, response_id, changes)
    return _row_response(request, ref, placement_id, response_id)


# --- the date the package went out ------------------------------------------
#
# THE ONE CELL ON THIS GRID THAT WRITES A SUBMISSION. Everything else in a row
# is the market's answer; `sent_on` is when we asked, and it belongs to the
# package rather than to any one line of coverage on it.
#
# IT HAD TO BECOME EDITABLE SOMEWHERE. `repo.marketing._reply_guard` refuses a
# reply dated before its submission went out and its refusal names correcting
# the send date as the other way out — and on 2026-08-26 no surface in the app
# could: `submission_form` is create-only, `apply_response` says in its own
# words that sent_on "is not on this form and cannot be", there is no
# `submission_*` MCP tool and the panel has no delete. One transposed digit in
# the add row therefore wedged the Replied cell of that row permanently. A
# refusal must never name a fix that does not exist; this is the fix.

_SENT_CELL = (
    "/accounts/{ref}/program/{placement_id}/marketing/responses/{response_id}/sent"
)


def _sent_cell(
    request: Request,
    ref: str,
    placement_id: str,
    response: MarketResponse,
    *,
    editing: bool,
    placement: Any = None,
    error: str | None = None,
    typed: str | None = None,
) -> HTMLResponse:
    """The Sent cell, through the SHARED cell contract — the same display →
    editor → POST every other inline cell in the app uses.

    The PLACEMENT rides along for the same reason `_display_cell` takes it: a
    date prints its year unless it falls in this placement's marketing window,
    and a cell rendered without that window answers with a different string
    from the grid it is swapped back into."""
    conn = _conn(request)
    sent_on = submissions_repo.get(conn, response.submission_id).sent_on
    action = marketing_grid.sent_cell_action(ref, placement_id, response.id)
    field = marketing_grid.SENT_FIELD
    if not editing:
        window = (
            marketing_report.window_for(placement.period_from, placement.period_to)
            if placement is not None
            else None
        )
        return HTMLResponse(
            render_cell_display(
                request,
                field,
                _plain(marketing_grid.sent_display_value(sent_on, window)),
                action,
            )
        )
    # The EDITOR pre-fills the stored value in the form its own parser accepts
    # back — never the display string, or opening a cell to read it would cost
    # a write (inline-cell.js compares against what it opened with).
    value = typed if typed is not None else (sent_on or "")
    return HTMLResponse(render_cell(request, field, value, action, error=error))


@router.get(_SENT_CELL, response_class=HTMLResponse)
def sent_cell(
    request: Request, ref: str, placement_id: str, response_id: str
) -> HTMLResponse:
    org, placement = _placement(request, ref, placement_id)
    response = _owned_response(_conn(request), org, placement_id, response_id)
    return _sent_cell(
        request, ref, placement_id, response, editing=False, placement=placement
    )


@router.get(_SENT_CELL + "/edit", response_class=HTMLResponse)
def sent_cell_edit(
    request: Request, ref: str, placement_id: str, response_id: str
) -> HTMLResponse:
    org, _ = _placement(request, ref, placement_id)
    response = _owned_response(_conn(request), org, placement_id, response_id)
    return _sent_cell(request, ref, placement_id, response, editing=True)


@router.post(_SENT_CELL, response_class=HTMLResponse)
async def sent_cell_save(
    request: Request, ref: str, placement_id: str, response_id: str
) -> HTMLResponse:
    """Correct the date a package went out.

    IT ANSWERS WITH THE WHOLE SECTION, and that is the smallest honest unit
    here: one submission carries every line of coverage it was sent on, so its
    rows sit in blocks this line's block does not contain — answering with the
    block would leave the same package printing two different send dates on
    one screen. `refocus` puts the caret back on the row it was on, the way a
    layer cell save does when the program section comes back.

    `repo.submissions._sent_guard` owns both refusals (a date that has not
    happened yet, and one later than an answer already recorded) so that MCP
    and the pipeline's own form inherit them.
    """
    org, _ = _placement(request, ref, placement_id)
    conn = _conn(request)
    response = _owned_response(conn, org, placement_id, response_id)
    field = marketing_grid.SENT_FIELD
    raw = str((await request.form()).get(field.key, ""))

    def refused(message: str) -> HTMLResponse:
        return _sent_cell(
            request, ref, placement_id, response,
            editing=True, error=message, typed=raw,
        )

    try:
        value = parse_value(field, raw)
    except ValueError as exc:
        return refused(str(exc))
    if value in (None, ""):
        # NOT NULLABLE, and the refusal says why rather than writing a blank
        # the column cannot hold: a submission that went out has a date, and
        # `submission.sent_on` is NOT NULL under this.
        return refused(
            "a submission has a date it went out on — enter the day the "
            "package went to the market"
        )
    try:
        # THE SAME TWO REFUSALS THE ADD ROW MEETS, and neither is stated twice:
        # the future check is `services.consistency`'s (today is a parameter,
        # so it is the same rule `marketing_entry.approach` applies) and the
        # "not after an answer already recorded" check is
        # `repo.submissions._sent_guard`'s, under the write itself.
        consistency.check_not_future(
            str(value), label="a submission sent", today=date.today().isoformat()
        )
        with batches_svc.open_batch(
            conn, source="web", tool="market_approach",
            summary=f"corrected the date we went to {_who(conn, response)}",
            org_id=org.id,
        ):
            submissions_repo.update(conn, response.submission_id, sent_on=value)
    except Exception as exc:  # a refused save is a message, never a 500
        return refused(_house(exc))
    return _section(
        request, ref, org, placement_id, refocus=f"{response_id}:{field.key}"
    )


# --- the section, and one block of it ---------------------------------------


def _section(
    request: Request,
    ref: str,
    org: Any,
    placement_id: str,
    *,
    error: str | None = None,
    pending: dict[str, Any] | None = None,
    refocus: str | None = None,
    line_values: dict[str, str] | None = None,
    its_own: bool = False,
    provisional_error: str | None = None,
    provisional_row: str | None = None,
) -> HTMLResponse:
    """The whole Marketing section, retargeted onto itself.

    ONE TOP-LEVEL ELEMENT that says where it goes, exactly as
    routes/program.py `_panel` does for the program section — and this is the
    smaller of the two on purpose. A marketing write moves nothing in the
    tower above it and re-rendering the program section would re-open and
    re-parse the towerkit file for a write that never touched it. Every write
    in this module answers with the section, one block of it, or one row of
    it: whichever is the smallest thing the write can actually change.

    `its_own` says this answer is the ADD-A-LINE CONTROL's own — its refusal,
    its near-match question, or the section after it wrote a line. Only those
    three may rebuild that control, because only those three have something
    new to put in it; every other answer that passes through here belongs to
    somebody else's write and must leave whatever is half-typed there alone
    (`marketing_grid.panel`, `line_add_preserve`). Defaulting to False is
    deliberate: a new caller is somebody else's write until it says otherwise,
    which is the direction that fails safe.
    """
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    context = marketing_grid.panel(
        request, conn, placement_id, today=date.today(), ref=ref,
        error=error, pending=pending, refocus=refocus,
        line_values=line_values, line_add_preserve=not its_own,
        provisional_error=provisional_error, provisional_row=provisional_row,
        sort_spec=_sorted_by(request),
    )
    html = TEMPLATES.env.get_template("account/_marketing_panel.html").render(
        placement=placement, marketing=context
    )
    response = HTMLResponse(html)
    response.headers["HX-Retarget"] = f"#{context['id']}"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


def _block_response(
    request: Request,
    ref: str,
    placement_id: str,
    line_id: str,
    *,
    refocus: str | None = None,
    keep_add_row: bool = True,
) -> HTMLResponse:
    """ONE line-of-coverage block, re-composed, retargeted onto itself.

    A header cell is not a cell that changes only itself. Recording the
    expiring rate turns every Rate Δ in the rows beneath it from a sentence
    ("no expiring rate recorded") into a number, and that is the entire reason
    the header exists — a cell-only answer would leave nine rows still saying
    the figure had never been given, beside the cell that had just given it.
    The block is the smallest honest unit, the same way the ROW is for a
    premium (four cells feed a Total) and the SECTION is for adding a line.

    `keep_add_row` marks the ADD ROW `hx-preserve`, so the swap keeps the one
    a broker may be half-way through typing instead of rebuilding it from its
    defaults — NEVER LOSE TYPING, and a write to somebody else's cell is not a
    reason to lose it. The add row's own successful save passes False, because
    there the cleared row IS the right answer. The refusal path never comes
    through here at all: `_add_row` re-renders the row itself, carrying the
    posted values and the message.
    """
    context = marketing_grid.panel(
        request, _conn(request), placement_id, today=date.today(), ref=ref,
        sort_spec=_sorted_by(request),
    )
    block = next(
        (b for b in context["blocks"] if b["line_id"] == line_id), None
    )
    if block is None:  # the row went away under the page; nothing honest to swap
        raise HTTPException(status_code=404, detail="no such line on this placement")
    block = dict(block, refocus=refocus)
    template = TEMPLATES.env.get_template("macros/marketing.html")
    html = str(
        template.make_module({}).block(  # type: ignore[attr-defined]
            block, context["columns"], context["add_fields"], context["add_values"],
            keep_add_row, context["id"],
        )
    )
    response = HTMLResponse(html)
    response.headers["HX-Retarget"] = f"#{block['id']}"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


# --- the tab ------------------------------------------------------------------


@router.get("/accounts/{ref}/marketing", response_class=HTMLResponse)
def marketing_tab(request: Request, ref: str) -> HTMLResponse:
    """Every placement on the account, each with its marketing grid.

    ITS OWN TAB SINCE 2026-08-27. The grid asks for 1,811px and had 1,064 in
    the Program tab's middle column, and it was 38% of that page's height —
    but the reason that decided it is not about pixels: marketing happens
    BEFORE a tower exists and every figure on it lives in SQLite, so it was
    never subordinate to the program file it was nested inside.

    IT REGISTERS ITSELF rather than joining `account._PANEL_TEMPLATE`, the way
    `relationship` does and for the same reason: the panel needs a context the
    generic tab renderer does not build. `app.py` includes this router before
    `account.router`, so this specific path wins over that module's `{tab}`
    catch-all.

    EVERY PLACEMENT, LINKED OR NOT. A placement with no program file is
    exactly the state most marketing happens in, and the section renders the
    same either way.
    """
    from .account import _context

    conn = _conn(request)
    org = _org(request, ref)
    context = _context(conn, org, "marketing", request)
    context["sections"] = [
        {
            "placement": placement,
            "marketing": marketing_grid.panel(
                request, conn, placement.id, today=date.today(), ref=ref,
                sort_spec=str(request.query_params.get("sort", "")),
            ),
            # BOTH AUDIENCES, EACH SAYING WHICH. The client sheet withholds the
            # internal decline reason, the commission and the notes; the
            # internal one does not. `audience` was reachable only by
            # hand-typing a query parameter until now — built and not
            # accessible, on the half that must never reach a client by
            # accident (issue #1).
            # THE NAME IN THE MARKUP AS WELL AS THE HEADER. The server sends
            # `Content-Disposition: attachment; filename="PLC-0001-marketing
            # .xlsx"` and a plain browser honours it — verified — but Grant's
            # download landed as a bare UUID while an extension was attached to
            # his Chrome (2026-08-27). The bytes were right and only the name
            # was lost, which is the worst shape for a file somebody is about
            # to send a client: it opens fine and it is unrecognisable in a
            # folder. `download` states the same name a second way, from the
            # page, so an interception has to lose both to lose it.
            "export_name": f"{placement.ref}-marketing.xlsx",
            "export_client": (
                f"/accounts/{ref}/program/{placement.id}"
                f"/export/marketing.xlsx?audience=client"
            ),
            "export_internal": (
                f"/accounts/{ref}/program/{placement.id}"
                f"/export/marketing.xlsx?audience=internal"
            ),
        }
        for placement in placements_repo.for_org(conn, org.id)
    ]
    context["oob"] = False  # a full tab-page render is never an OOB swap
    return TEMPLATES.TemplateResponse(request, "account/marketing.html", context)


# --- a row that records marketing which did not happen ------------------------


_REMOVE = (
    "/accounts/{ref}/program/{placement_id}/marketing/responses/{response_id}/remove"
)


@router.get(
    "/accounts/{ref}/program/{placement_id}/marketing/responses/{response_id}/row",
    response_class=HTMLResponse,
)
def response_row(
    request: Request, ref: str, placement_id: str, response_id: str
) -> HTMLResponse:
    """THE ROW AGAIN, unchanged — what [keep] on the remove confirm fetches.

    A fragment route so backing out of a confirm costs one row rather than a
    whole section, which is the same reason `_market_confirm.html`'s keep
    button re-fetches a participation rather than the panel around it."""
    org, _ = _placement(request, ref, placement_id)
    _owned_response(_conn(request), org, placement_id, response_id)
    return _row_response(request, ref, placement_id, response_id)


@router.get(_REMOVE, response_class=HTMLResponse)
def response_remove_confirm(
    request: Request, ref: str, placement_id: str, response_id: str
) -> HTMLResponse:
    """The question, in the row's own place. WRITES NOTHING — only the POST
    below writes, which is the split every confirm in this app makes."""
    org, _ = _placement(request, ref, placement_id)
    conn = _conn(request)
    response = _owned_response(conn, org, placement_id, response_id)
    return HTMLResponse(_confirm_html(request, conn, ref, placement_id, response))


def _confirm_html(
    request: Request,
    conn: sqlite3.Connection,
    ref: str,
    placement_id: str,
    response: MarketResponse,
    error: str | None = None,
) -> str:
    line = lines_repo.get_any(conn, response.line_id)
    submission = submissions_repo.get(conn, response.submission_id)
    # WHETHER THE APPROACH IS LEFT WITH NOTHING TO SAY. Counted over the LIVE
    # rows only, and the answer changes what the confirm tells the reader will
    # happen — which is the one fact they cannot work out for themselves.
    siblings = [
        r
        for r in marketing_repo.responses_for_submission(conn, response.submission_id)
        if r.id != response.id
    ]
    return TEMPLATES.env.get_template(
        "account/_response_remove_confirm.html"
    ).render(
        response_id=response.id,
        base=marketing_grid.response_base(ref, placement_id, response.id),
        who=_who(conn, response),
        line_name=line.name if line else "this line of coverage",
        last_on_package=not siblings,
        sent_on=submission.sent_on,
        span=len(marketing_grid.COLUMNS) + 1,
        error=error,
    )


@router.post(_REMOVE, response_class=HTMLResponse)
def response_remove(
    request: Request, ref: str, placement_id: str, response_id: str
) -> HTMLResponse:
    """Take the row back, in one revertible act.

    IT ANSWERS WITH THE SECTION, and this is the one write in this module where
    that is the SMALLEST honest unit rather than the largest. Answer with the
    smallest thing the write can actually change — and a removal can change
    three things outside the row's own block, each of which was tried and
    failed as a block-sized answer (all three found by clicking it, 2026-08-26):

      * THE APPROACH MOVES. Take the last answer off a package and the package
        itself renders in the PROVISIONAL block — "line of coverage not
        recorded", because it did go out on its day — which is a sibling
        article the confirm explicitly promises. A block answer left that
        promise unkept until the reader reloaded.
      * THE BLOCK CAN VANISH. Remove the only row on a line the placement has
        no expectations for and the composer stops building that block at all;
        a block-targeted answer then retargets onto an element that is not
        there and comes back 404, which reads as nothing having happened.
      * THE BRIDGE AND THE CLEARANCE STRIP move for the same reasons
        `_BLOCK_CELLS` exists — the bridge is decomposed from the LEADING
        quote and the strip counts LIVE approaches.
    """
    org, _ = _placement(request, ref, placement_id)
    conn = _conn(request)
    response = _owned_response(conn, org, placement_id, response_id)
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="market_response_remove",
            summary=(
                f"removed what {_who(conn, response)} said, recorded in error"
            ),
            org_id=org.id,
        ):
            marketing_repo.remove_response(conn, response_id)
    except Exception as exc:  # a refused delete is a message, never a 500
        # THE CONFIRM AGAIN, CARRYING THE REFUSAL. Answering with the row would
        # put the reader back where they started with no sign anything had
        # happened — the "a refusal says something" rule, on the one control in
        # this module whose failure destroys nothing and therefore looks
        # exactly like success.
        return HTMLResponse(
            _confirm_html(request, conn, ref, placement_id, response, _house(exc))
        )
    return _section(request, ref, org, placement_id)


# --- the order a reader asked for --------------------------------------------


@router.get(
    "/accounts/{ref}/program/{placement_id}/marketing/sort",
    response_class=HTMLResponse,
)
def marketing_sort(request: Request, ref: str, placement_id: str) -> HTMLResponse:
    """Re-render the section in the order a header click asked for.

    A GET, AND IT WRITES NOTHING. Sorting changes what a broker is looking at
    and nothing about the book — no column, no event log, no batch, nothing to
    undo — so it is idempotent, safe to repeat, and the URL says what it shows.

    IT ANSWERS WITH THE SECTION, not the block whose header was clicked. The
    order for every block lives on the section as one inherited `hx-vals`
    (`marketing_grid.panel`), which is what keeps a sort alive through a Sent
    cell — and a block-sized answer could not update the element that carries
    it. The cost is a re-compose of the other blocks, which is what the Sent
    cell already pays and is a deliberate click rather than a keystroke.

    NOTHING IS VALIDATED HERE and nothing is refused: `parse_sorts` drops a
    column this grid cannot order and `panel` re-formats from what it applied,
    so the worst a hand-typed URL can do is render the grid in its own default
    order while saying so.
    """
    org, _ = _placement(request, ref, placement_id)
    return _section(request, ref, org, placement_id)


# --- giving a package the line of coverage nobody recorded -------------------


@router.post(
    "/accounts/{ref}/program/{placement_id}/marketing/submissions"
    "/{submission_id}/line",
    response_class=HTMLResponse,
)
async def marketing_assign_line(
    request: Request, ref: str, placement_id: str, submission_id: str
) -> HTMLResponse:
    """Record which line of coverage this package's answer is about.

    A SUBMISSION WITH NO RESPONSE ROWS IS REAL MARKETING THAT HAPPENED, and
    this is the only thing that can be done to one. The rule is
    `services.marketing_entry.assign_line`'s — nothing is invented, the six
    facts the submission recorded move onto the row that will state them, and
    from that moment `repo.marketing.roll_up_submission` owns the submission's
    columns exactly as it owns every other answered package's.

    ONE BATCH, so `u` takes it back in one act: the response, and the roll-up
    the response triggers, are one writer action.

    IT ANSWERS WITH THE WHOLE SECTION, and that is the smallest honest unit
    here — the only write on this panel of which that is true. The row LEAVES
    the provisional block and appears in a line-of-coverage block that may not
    have existed a moment ago, so neither block alone describes what changed;
    a block answer would swap in one of them and leave the other on the page
    stating what it stated before.
    """
    org, _ = _placement(request, ref, placement_id)
    conn = _conn(request)
    submission = _owned_submission(conn, org, placement_id, submission_id)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    # THE PICKER'S OWN OPTIONS ARE THE AUTHORITY, re-queried on the POST from
    # the one function that also rendered them (`assign_line_options`). Markup
    # constrains a mouse and nothing else, and a line of coverage that has been
    # retired since this page rendered must not be storable from a stale tab.
    field = Field(
        "line_id", "line of coverage", "select",
        tuple(marketing_grid.assign_line_options(conn)), required=True,
    )

    def refused(message: str) -> HTMLResponse:
        """The section again, with the message BESIDE THE ROW that raised it.

        Named `refused` for the reason `marketing_approach_add`'s is:
        tests/test_marketing_gates.py G5 walks every literal handed to a
        `refuse`/`refused` call in this module and fails until somebody says
        what fix its words name. A refusal passed straight into `_section` as a
        keyword would be invisible to that walk — which is the gap the D-round
        already noted about routes/pipeline.py.
        """
        return _section(
            request, ref, org, placement_id,
            provisional_error=message, provisional_row=submission_id,
        )

    try:
        line_id = parse_value(field, raw.get("line_id"))
    except ValueError as exc:
        return refused(f"{field.label}: {exc}")
    if not line_id:
        return refused(
            "pick the line of coverage this market's answer is about — it is "
            "never guessed, because an answer filed against the wrong line is a "
            "quote attributed to cover nobody quoted"
        )
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="market_responded",
            summary=f"line of coverage for {_market_name(conn, submission)}",
            org_id=org.id,
        ):
            marketing_entry.assign_line(conn, submission_id, line_id)
    except Exception as exc:  # a refused save is a message, never a 500
        return refused(_house(exc))
    return _section(request, ref, org, placement_id)


def _owned_submission(
    conn: sqlite3.Connection, org: Any, placement_id: str, submission_id: str
) -> Any:
    """BOTH ids in the URL are claims, and both are checked — the same shape
    `_owned_response` settles for a response id. The same 404 for "no such id"
    and for "someone else's id", deliberately: telling them apart is how a
    guessable id becomes a membership oracle."""
    try:
        submission = submissions_repo.get(conn, submission_id)
    except KeyError:
        raise _not_here("submission", submission_id, org) from None
    if submission.placement_id != placement_id:
        raise _not_here("submission", submission_id, org)
    return submission


def _market_name(conn: sqlite3.Connection, submission: Any) -> str:
    """Who the package went to, for the undo toast. `names_for_any`, because a
    market deleted from the book after we sent it a submission is still the
    market we sent it to — the same reading the composer takes."""
    names = orgs_repo.names_for_any(conn, {submission.market_org_id or ""})
    return names.get(submission.market_org_id or "", "this market")


# --- the line's own expectations, as cells ----------------------------------


_LINE_CELL = "/accounts/{ref}/program/{placement_id}/marketing/lines/{line_id}/cell/{key}"


def _line(
    request: Request, ref: str, placement_id: str, line_id: str
) -> tuple[Any, Any]:
    """The account, and this placement's row for this line of coverage.

    The row may not exist yet and that is not an error: a block can exist
    because a RESPONSE named the line, with nobody having stated a single
    expectation about it. `set_placement_line` creates on first write, so the
    cells are editable before the row is — what has to be checked here is that
    the LINE is real and the placement is this account's, which are the two
    claims the URL makes.

    REAL INCLUDES RETIRED (`get_any`). A retired line of coverage still shows
    its block, because the marketing recorded against it happened; the cells
    in that block therefore have to answer, or the panel renders nine controls
    that quietly do nothing — the same silence `_house` exists to stop, one
    door over. What a retired line does NOT get is a new approach: that
    control is withdrawn in the template, where the header can say why.
    """
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    if lines_repo.get_any(conn, line_id) is None:
        raise _not_here("line of coverage", line_id, org)
    return org, marketing_repo.placement_line(conn, placement_id, line_id)


def _line_field(line: Any, key: str) -> Field:
    """The editable set, CHECKED SERVER-SIDE — the markup constrains a mouse
    and nothing else. The Field is rebuilt from the line's own stored bases on
    every request, which is what makes an exposure parse as cents or as a
    count according to what is stored NOW rather than what the page showed
    when it rendered."""
    fields = marketing_grid.line_fields(line)
    field = fields.get(key)
    if field is None:
        raise HTTPException(
            status_code=404,
            detail=f"{key!r} is not an expectation a line of coverage carries",
        )
    return field


def _line_cell(
    request: Request,
    ref: str,
    placement_id: str,
    line_id: str,
    line: Any,
    key: str,
    *,
    editing: bool,
    error: str | None = None,
    typed: str | None = None,
) -> HTMLResponse:
    action = marketing_grid.line_cell_action(ref, placement_id, line_id, key)
    field = _line_field(line, key)
    css = marketing_grid.line_cell_class(line, key)
    if not editing:
        return HTMLResponse(
            render_cell_display(
                request, field,
                marketing_grid.line_display_value(line, key),
                action, tag="dd", extra_class=css,
            )
        )
    value = (
        typed if typed is not None else marketing_grid.line_editor_value(line, key)
    )
    return HTMLResponse(
        render_cell(
            request, field, value, action, error=error, tag="dd", extra_class=css
        )
    )


@router.get(_LINE_CELL, response_class=HTMLResponse)
def line_cell(
    request: Request, ref: str, placement_id: str, line_id: str, key: str
) -> HTMLResponse:
    _, line = _line(request, ref, placement_id, line_id)
    return _line_cell(
        request, ref, placement_id, line_id, line, key, editing=False
    )


@router.get(_LINE_CELL + "/edit", response_class=HTMLResponse)
def line_cell_edit(
    request: Request, ref: str, placement_id: str, line_id: str, key: str
) -> HTMLResponse:
    _, line = _line(request, ref, placement_id, line_id)
    return _line_cell(
        request, ref, placement_id, line_id, line, key, editing=True
    )


@router.post(_LINE_CELL, response_class=HTMLResponse)
async def line_cell_save(
    request: Request, ref: str, placement_id: str, line_id: str, key: str
) -> HTMLResponse:
    """One expectation, one writer action, one batch.

    AN EXPOSURE IS REFUSED WHILE ITS BASIS IS UNKNOWN rather than guessed.
    `placement_line_fields` builds the exposure as a `count` field when no
    basis is stored, so an amount typed there is refused by money.parse_count
    — but the sentence that parser gives is about decimals and currency
    symbols, and the real remedy is a different field entirely. This says so,
    naming the basis cell as the fix, because a refusal that does not name the
    fix is half a message.
    """
    org, line = _line(request, ref, placement_id, line_id)
    conn = _conn(request)
    field = _line_field(line, key)
    raw = str((await request.form()).get(key, ""))

    def refused(message: str) -> HTMLResponse:
        return _line_cell(
            request, ref, placement_id, line_id, line, key,
            editing=True, error=message, typed=raw,
        )

    basis_key = marketing_grid.EXPOSURE_BASIS.get(key)
    if basis_key and raw.strip() and marketing_grid.stored(line, basis_key) is None:
        return refused(
            f"set the {marketing_grid.line_fields(line)[basis_key].label} first — "
            f"42 power units and $0.42 are the same digits, and nothing in the "
            f"figure says which one it is"
        )
    try:
        value = parse_value(field, raw)
    except ValueError as exc:
        return refused(str(exc))
    if key == "rate_per" and value is not None:
        # The picker's values are strings because a <select> has no other
        # kind; the column is an integer. Converted HERE, once, after
        # `checked_option` has already refused anything the picker does not
        # offer — so this can never be an int() over arbitrary input.
        value = int(value)
    who = lines_repo.get(conn, line_id)
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="set_placement_line",
            summary=f"set {field.label} on {who.name if who else line_id}",
            org_id=org.id,
        ):
            marketing_repo.set_placement_line(
                conn, placement_id, line_id, **{key: value}
            )
    except Exception as exc:  # a refused save is a message, never a 500
        return refused(_house(exc))
    return _block_response(
        request, ref, placement_id, line_id, refocus=f"cell:{key}"
    )


# --- a line of coverage, and a market on it ---------------------------------


def _line_add_picker(conn: sqlite3.Connection, placement_id: str) -> Field:
    """The picker's OWN OPTIONS, re-queried on the POST from the ONE function
    that built them for the GET (`marketing_grid.line_add_options`) — which is
    what makes `checked_option` authoritative rather than decorative, and what
    stops the panel offering a line this refuses. It still refuses a line that
    has gone stale since the page rendered (retired, or started by another
    session), which is right: the row it named is gone."""
    return Field(
        "line_id", "line of coverage", "select",
        tuple(marketing_grid.line_add_options(conn, placement_id)),
    )


def _clash(
    conn: sqlite3.Connection,
    placement_id: str,
    typed: str,
    matches: list[tuple[Any, int]],
    *,
    head: str,
    offer_create: bool,
) -> dict[str, Any]:
    """The near-match question, as data.

    ADVISORY, NEVER A VETO. `Excess Liability` and `Employers Liability` share
    most of their letters and are not the same thing, so both answers are
    offered and neither is chosen here — a warning a user cannot override is a
    feature that makes a correct entry impossible (repo/lines.py's own words).

    `offer_create` is the ONE case where the create half is withheld: an
    EXACT duplicate. That is not a judgment call about spelling, it is the same
    name, and `repo.lines.create` refuses it — so the refusal names the line
    that already exists and offers to use it instead of only saying no.
    """
    here = {
        row.line_id for row in marketing_repo.placement_lines(conn, placement_id)
    }
    return {
        "head": head,
        "name": typed,
        "matches": [
            {
                "id": line.id,
                "name": line.name,
                "score": score,
                "here": line.id in here,
                "use_vals": json.dumps({"line_id": line.id}),
            }
            for line, score in matches
        ],
        "create_vals": (
            json.dumps({"line_name": typed, "create": "yes"})
            if offer_create
            else ""
        ),
    }


@router.post(
    "/accounts/{ref}/program/{placement_id}/marketing/lines",
    response_class=HTMLResponse,
)
async def marketing_line_add(
    request: Request, ref: str, placement_id: str
) -> HTMLResponse:
    """Open a block for a line of coverage on this placement — one the book
    already carries, or one it has never heard of.

    A block exists because a `placement_line` row or a response names the line,
    so on a fresh placement there is nothing to add a market TO. This is the
    way in, and it writes nothing but the row itself — no expiring figures, no
    exposure, no basis. Every one of those comes off a document and none of
    them is guessed here.

    THE VOCABULARY HAS TO BE ABLE TO GROW, or a broker meets a picker that
    cannot say what they are placing. It grows behind the near-match guard,
    which is a QUESTION and not a refusal, and behind an exact-duplicate
    refusal that names the line already there.
    """
    org = _org(request, ref)
    conn = _conn(request)
    _owned(conn, org, "placement", placement_id, placements_repo.get)
    form = await request.form()
    picked = str(form.get("line_id", "")).strip()
    typed = str(form.get("line_name", "")).strip()
    confirmed = bool(str(form.get("create", "")).strip())

    # WHAT WAS TYPED, so every answer below can hand it back. A refusal that
    # empties the control it refuses makes the user retype a value the server
    # is holding in its own hand.
    kept = {"line_id": picked, "line_name": typed}

    def refuse(message: str) -> HTMLResponse:
        return _section(
            request, ref, org, placement_id,
            error=message, line_values=kept, its_own=True,
        )

    if picked and typed:
        # A REFUSAL SAYS SOMETHING, and it must not pick a winner: whichever
        # this chose silently would be wrong half the time, and the user is
        # one click from saying which they meant.
        return refuse(
            "pick a line of coverage from the list or type a new name, not both"
        )
    if not picked and not typed:
        return refuse("pick a line of coverage from the list, or type a new name")

    if picked:
        try:
            line_id = checked_option(_line_add_picker(conn, placement_id), picked)
        except ValueError as exc:
            return refuse(str(exc))
        return _add_line(request, ref, org, placement_id, line_id)

    existing = lines_repo.by_name(conn, typed)
    if existing is not None:
        # AN EXACT DUPLICATE IS REFUSED — and the refusal names it and offers
        # to use it, which is what repo.lines.DuplicateLine carries the row
        # for. Saying only "that already exists" would leave the broker to
        # find it in a picker they have already looked past.
        return _section(
            request, ref, org, placement_id,
            line_values=kept, its_own=True,
            pending=_clash(
                conn, placement_id, typed, [(existing, 100)],
                head=(
                    f"{typed!r} is already a line of coverage in this book — "
                    f"use it rather than making a second one"
                ),
                offer_create=False,
            ),
        )

    if not confirmed:
        matches = lines_repo.near_matches(conn, typed)
        if matches:
            return _section(
                request, ref, org, placement_id,
                line_values=kept, its_own=True,
                pending=_clash(
                    conn, placement_id, typed, matches,
                    head=(
                        f"{typed!r} looks like a line of coverage this book "
                        f"already carries. Use one of these, or create it anyway "
                        f"— they may genuinely be different cover."
                    ),
                    offer_create=True,
                ),
            )

    try:
        with batches_svc.open_batch(
            conn, source="web", tool="line_add",
            summary=f"added the line of coverage {typed}", org_id=org.id,
        ):
            line_id = lines_repo.create(conn, typed)
            marketing_repo.set_placement_line(conn, placement_id, line_id)
    except lines_repo.DuplicateLine as exc:
        # The race: somebody created it between the near-match check and this
        # write. The refusal is the same one the exact-name path gives, out of
        # the exception that carries the row.
        return _section(
            request, ref, org, placement_id,
            line_values=kept, its_own=True,
            pending=_clash(
                conn, placement_id, typed, [(exc.existing, 100)],
                head=(
                    f"{exc.existing.name!r} was created while you were looking "
                    f"at this — use it rather than making a second one"
                ),
                offer_create=False,
            ),
        )
    except Exception as exc:  # a refused save is a message, never a 500
        return refuse(_house(exc))
    # WRITTEN: the control is rebuilt EMPTY and without hx-preserve, ready for
    # the next line — the same thing the add-market row's own successful save
    # does. Preserving here would leave the name that was just created sitting
    # in the box as though it had not been.
    return _section(request, ref, org, placement_id, its_own=True)


def _add_line(
    request: Request, ref: str, org: Any, placement_id: str, line_id: str
) -> HTMLResponse:
    conn = _conn(request)
    line = lines_repo.get(conn, line_id)
    with batches_svc.open_batch(
        conn, source="web", tool="set_placement_line",
        summary=f"started marketing {line.name if line else line_id}",
        org_id=org.id,
    ):
        marketing_repo.set_placement_line(conn, placement_id, line_id)
    return _section(request, ref, org, placement_id, its_own=True)


@router.post(
    "/accounts/{ref}/program/{placement_id}/marketing/lines/{line_id}/approaches",
    response_class=HTMLResponse,
)
async def marketing_approach_add(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    """Record that we went to a market on this line of coverage.

    The submission-reuse rule is services.marketing_entry's, shared with MCP's
    `market_approach`: one submission goes to one market carrying every line,
    and this joins the live one rather than opening a second package.

    CLEARANCE IS NOT CHECKED HERE, and the row is written whatever it would
    have said. A second live approach to the same carrier through a different
    intermediary is sometimes deliberate; the panel warns beside the row it
    just wrote (the `line-gap` rule).
    """
    org, _ = _placement(request, ref, placement_id)
    conn = _conn(request)
    if lines_repo.get(conn, line_id) is None:
        raise HTTPException(status_code=404, detail=f"no line of coverage {line_id!r}")
    raw = {k: str(v) for k, v in (await request.form()).items()}
    fields = market_approach_fields(conn)

    def refused(message: str) -> HTMLResponse:
        return _add_row(request, ref, placement_id, line_id, fields, raw, message)

    values: dict[str, Any] = {}
    for field in fields:
        try:
            values[field.key] = parse_value(field, raw.get(field.key))
        except ValueError as exc:
            return refused(f"{field.label}: {exc}")
        # REQUIRED IS CHECKED HERE OR IT IS NOT CHECKED AT ALL. The markup
        # `required` on the status select cannot fire — the add row is inside a
        # table and has no <form> ancestor, and its Save is a type="button"
        # carrying the POST — so a status cleared back to blank filed itself as
        # "pending" in silence, which is the same shape as the untouched
        # response that filed itself as "quoted" (forms/inline.py says so on
        # this exact field). The cell route one door over has always checked
        # it; a declared guard that does not hold is worse than no guard.
        # Same sentence as the cell route's, and as forms.spec.parse_values'.
        if field.required and values[field.key] in (None, ""):
            return refused(f"{field.label} is required")

    # A MARKET NEW TO THE BOOK IS ADDED FROM HERE, not on another tab.
    # `_resolve_markets` answers with the two orgs, or with the question the
    # add row asks before it mints one — and nothing is created until every
    # name on the row has an answer, so a confirmed carrier beside an
    # unanswered intermediary leaves no market behind with no approach.
    resolution = _resolve_markets(conn, values, raw)
    if isinstance(resolution, str):
        return refused(resolution)
    if isinstance(resolution, dict):
        return _add_row(
            request, ref, placement_id, line_id, fields, raw,
            error=None, pending=resolution, decided=_decided(raw),
        )
    carrier, intermediary, to_create = resolution
    if carrier is None and intermediary is None and not to_create:
        return refused(
            "a carrier or an intermediary — if the wholesaler has not named the "
            "paper yet, give the intermediary alone and fill the carrier in "
            "when they come back with it"
        )

    extra = {
        key: values[key]
        for key in ("attach", "lim", "status")
        if values.get(key) is not None
    }
    # WHO THE BATCH SAYS WAS APPROACHED. `intermediary` may still be the
    # "same as the carrier" sentinel here and a market being created has no
    # row yet, so this reads a name off whatever actually has one and falls
    # back to the one about to be minted.
    named = [o for o in (carrier, intermediary) if getattr(o, "name", None)]
    who: Any = named[0] if named else _Named(to_create[0][1])
    try:
        # ONE WRITER ACTION IS ONE UNDO UNIT: the market this row minted and
        # the approach it was minted for revert together. Creating it in its
        # own batch would leave a market behind after `R` took the approach
        # back — a record nobody asked for, on a book where a duplicate market
        # is the thing every guard here exists to prevent.
        with batches_svc.open_batch(
            conn, source="web", tool="market_approach",
            summary=f"approached {who.name}", org_id=org.id,
        ):
            for key, typed in to_create:
                minted = orgs_repo.create_market(conn, typed)
                if key == "market":
                    carrier = minted
                else:
                    intermediary = minted
            if intermediary is _SAME_AS_MARKET:
                intermediary = carrier
            marketing_entry.approach(
                conn,
                placement_id,
                line_id,
                sent_on=values.get("sent_on") or date.today().isoformat(),
                market_org_id=carrier.id if carrier else None,
                via_org_id=intermediary.id if intermediary else None,
                **extra,
            )
    except Exception as exc:  # a refused save is a message, never a 500
        return refused(_house(exc))
    # THE BLOCK, not the whole section: an approach adds a row to ONE line of
    # coverage, and the clearance strip and the premium bridge that also move
    # with it both live on that block. Answering with the section would
    # re-render every other line's grid for a write that touched none of them.
    #
    # AND THE ADD ROW IS REBUILT HERE, not preserved: the approach was saved,
    # so the form has to come back empty for the next one. Every other answer
    # keeps it (`_block_response`'s default), because no other write has any
    # business clearing what a broker is typing.
    return _block_response(
        request, ref, placement_id, line_id, keep_add_row=False
    )


_SAME_AS_MARKET = object()
"""`via` is the same brand-new market as `market` — see `_resolve_markets`.
Not an Org because the row does not exist yet; the writer resolves it to
whatever the single create returned, which is the point."""


class _Named:
    """A name with nothing behind it yet — only so the batch summary can say
    who was approached when the market is created inside that same batch and
    has no row until the transaction is open."""

    def __init__(self, name: str) -> None:
        self.name = name


# The two market names on the add row, and the label each answers to. Both
# halves of every control below are built from this rather than written twice:
# a miss on `via` asks exactly the question a miss on `market` asks, and it
# used to be that only one of them had a way out at all.
_MARKET_KEYS: tuple[tuple[str, str], ...] = (
    ("market", "carrier"),
    ("via", "intermediary"),
)


def _decided(raw: dict[str, str]) -> dict[str, str]:
    """What the near-match question has already been answered with, as hidden
    inputs for the row that asks the NEXT question.

    Both names on the row can be new to the book, and the question is asked one
    at a time. Without this the answer to the first is lost the moment the
    second is asked, and the two questions ping-pong forever — the broker
    confirms the carrier, is asked about the intermediary, and confirming that
    one re-asks about the carrier.
    """
    keys = [f"use_{k}" for k, _ in _MARKET_KEYS] + [
        f"create_{k}" for k, _ in _MARKET_KEYS
    ]
    return {k: raw[k] for k in keys if raw.get(k, "").strip()}


def _resolve_markets(
    conn: sqlite3.Connection, values: dict[str, Any], raw: dict[str, str]
) -> Any:
    """The carrier and the intermediary, or the question to ask about them.

    Returns `(carrier, intermediary, to_create)` when every name on the row has
    an answer — `to_create` being the ones the broker has confirmed are new,
    written by the caller inside the approach's own batch. Returns a `dict`
    when a name still needs the near-match question, and a `str` for a refusal
    the row shows as it shows any other.

    A NAME IS STILL NEVER CREATED BY ACCIDENT. What changed on 2026-08-26 is
    where the answer is given: a carrier the book has never carried is ORDINARY
    in placement — it is most of what growing a market list looks like — and
    the old refusal named /markets/new and stopped, which sends a broker away
    from the row they are mid-way through typing and loses the rest of it.
    The question is asked HERE, with the nearest markets to use instead beside
    it, and the create is one click that says what it is doing.

    NOTHING IS CREATED WHILE A QUESTION IS STILL OPEN. The confirmations ride
    back as hidden inputs (`_decided`), so both names can be answered before
    the first row is written — otherwise a confirmed carrier would be minted,
    the intermediary question asked, and abandoning it would leave a market on
    the book with no approach behind it.
    """
    resolved: dict[str, Any] = {"market": None, "via": None}
    to_create: list[tuple[str, str]] = []
    for key, label in _MARKET_KEYS:
        picked = raw.get(f"use_{key}", "").strip()
        typed = str(values.get(key) or "").strip()
        if picked:
            # THE ID IS AUTHORITATIVE, the typed name decorative: this is the
            # "use Zurich" button answering, and the misspelling that raised
            # the question is still sitting in the box beside it.
            org = orgs_repo.find(conn, picked)
            if org is None or org.kind != "market":
                return (
                    "that market is no longer on the book — retype the "
                    "carrier and answer the question again"
                )
            resolved[key] = org
            # BOTH COPIES, because the row is re-rendered from `raw` (the
            # strings that were typed) and read from `values` (the parsed
            # ones). Leaving the misspelling in `raw` would put it back in the
            # box under the answer that just replaced it.
            values[key] = raw[key] = org.name
            continue
        if not typed:
            continue
        org = orgs_repo.find_market(conn, typed)
        if org is not None:
            resolved[key] = org
            continue
        if raw.get(f"create_{key}", "").strip():
            to_create.append((key, typed))
            continue
        return _market_clash(conn, key, label, typed)
    if len(to_create) == 2 and (
        to_create[0][1].casefold() == to_create[1][1].casefold()
    ):
        # ONE NAME IS ONE MARKET. Confirming the same new name in both boxes
        # would otherwise mint it twice and hand repo.marketing two DIFFERENT
        # ids, so the guard that refuses paper reached through itself would
        # see two markets and pass — leaving the book with a duplicate the
        # merge tool has to clean up. Creating it once makes the ids equal and
        # that guard says the sentence it exists to say (the whole batch,
        # create included, then rolls back).
        to_create = [to_create[0]]
        resolved["via"] = _SAME_AS_MARKET
    return resolved["market"], resolved["via"], to_create


def _market_clash(
    conn: sqlite3.Connection, key: str, label: str, typed: str
) -> dict[str, Any]:
    """The near-match question the add row asks before it mints a market.

    ADVISORY, NEVER A VETO — the same shape `_clash` gives a line of coverage,
    and for the same reason: 'Zurich' and 'Zurich American' are two real
    markets on most books, so nothing here can decide for the broker. Both
    answers are buttons: use one that exists, or add the one that was typed.
    The scores are printed because "91% like Zurich" is a fact somebody can
    judge and "did you mean…" is not.

    THE CREATE IS ALWAYS OFFERED, near matches or none. A control that shows
    the question and no way through it is the refusal this replaced, wearing a
    question's clothes.
    """
    matches = orgs_repo.near_markets(conn, typed)
    head = (
        f"{typed!r} is not a market this book carries. Use one of these, or "
        f"add it — a carrier new to the book is ordinary."
        if matches
        else f"{typed!r} is not a market this book carries yet — add it."
    )
    return {
        "head": head,
        "name": typed,
        "label": label,
        "matches": [
            {
                "name": org.name,
                "score": score,
                "vals": json.dumps({f"use_{key}": org.id}),
            }
            for org, score in matches
        ],
        "create_vals": json.dumps({f"create_{key}": "yes"}),
    }


def _add_row(
    request: Request,
    ref: str,
    placement_id: str,
    line_id: str,
    fields: tuple[Field, ...],
    values: dict[str, Any],
    error: str | None = None,
    *,
    pending: dict[str, Any] | None = None,
    decided: dict[str, str] | None = None,
) -> HTMLResponse:
    """The add row again, with the message and everything typed still in it.

    COMMIT IN PLACE, the platform default: a refused save keeps the form open
    with its input intact. One `<tr>`, targeted where it already is, so nothing
    else on the page moves under a broker mid-correction.

    `pending` is the near-match question about a market name, and it rides
    INSIDE this same `<tr>` rather than answering as a second element: htmx
    parses a response by its first tag, and anything after a `<tr>` that is not
    table content is foster-parented out of the fragment before htmx sees it
    (CLAUDE.md, ONE RESPONSE ONE TOP-LEVEL ELEMENT). `decided` is what earlier
    questions on this same row were already answered with, carried as hidden
    inputs so the next answer does not throw the last one away."""
    template = TEMPLATES.env.get_template("macros/marketing.html")
    block = {
        # THE SAME id THE REAL BLOCK CARRIES. The add row's datalists are named
        # after it, and this row is re-rendered on its own on a refusal — a
        # different id here would leave the corrected row's carrier input
        # pointing at a datalist that is no longer on the page.
        "id": marketing_grid.block_id(placement_id, line_id),
        "line_id": line_id,
        "add_url": (
            f"/accounts/{ref}/program/{placement_id}"
            f"/marketing/lines/{line_id}/approaches"
        ),
    }
    html = str(
        template.make_module({}).add_row(  # type: ignore[attr-defined]
            block, fields, values, marketing_grid.COLUMNS, error,
            False, pending, decided or {},
        )
    )
    return HTMLResponse(html)
