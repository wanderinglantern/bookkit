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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...forms.inline import market_approach_fields
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

router = APIRouter()


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


def _field(key: str) -> Field:
    """The editable set, checked SERVER-SIDE. The markup constrains a mouse and
    nothing else — every route is reachable by anything that can POST, and the
    keys deliberately left out of MARKET_RESPONSE_FIELDS (the carrier, the
    total, the rate movement) are left out for reasons a URL must not be able
    to talk its way past."""
    field = marketing_grid.CELL_FIELDS.get(key)
    if field is None:
        raise HTTPException(
            status_code=404, detail=f"{key!r} is not editable on a market response"
        )
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
    return HTMLResponse(
        render_cell_display(
            request,
            _field(key),
            _plain(marketing_grid.display_value(response, key, window)),
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
    value = typed if typed is not None else marketing_grid.editor_value(response, key)
    return HTMLResponse(
        render_cell(
            request,
            _field(key),
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
        request, conn, placement_id, today=date.today(), ref=ref
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
_BLOCK_CELLS = frozenset({"premium", "rate_micros", "status"})


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

    `repo.marketing.edit_response` is the writer for every surface, and it
    rolls the submission's status up afterwards — inside this batch, so the
    parent row moving is part of the same undo unit rather than a change
    nobody can take back."""
    org, _ = _placement(request, ref, placement_id)
    conn = _conn(request)
    response = _owned_response(conn, org, placement_id, response_id)
    field = _field(key)
    raw = str((await request.form()).get(key, ""))
    try:
        value = parse_value(field, raw)
    except ValueError as exc:
        return _editor_cell(
            request, ref, placement_id, response, key, error=str(exc), typed=raw
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
            marketing_repo.edit_response(conn, response_id, {key: value})
    except Exception as exc:  # a refused save is a message, never a 500
        return _editor_cell(
            request, ref, placement_id, response, key, error=_house(exc), typed=raw
        )
    if key in _BLOCK_CELLS:
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
) -> HTMLResponse:
    """The whole Marketing section, retargeted onto itself.

    ONE TOP-LEVEL ELEMENT that says where it goes, exactly as
    routes/program.py `_panel` does for the program section — and this is the
    smaller of the two on purpose. A marketing write moves nothing in the
    tower above it and re-rendering the program section would re-open and
    re-parse the towerkit file for a write that never touched it. Every write
    in this module answers with the section, one block of it, or one row of
    it: whichever is the smallest thing the write can actually change.
    """
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    context = marketing_grid.panel(
        request, conn, placement_id, today=date.today(), ref=ref,
        error=error, pending=pending, refocus=refocus,
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
        request, _conn(request), placement_id, today=date.today(), ref=ref
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
            keep_add_row,
        )
    )
    response = HTMLResponse(html)
    response.headers["HX-Retarget"] = f"#{block['id']}"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


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

    def refuse(message: str) -> HTMLResponse:
        return _section(request, ref, org, placement_id, error=message)

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
    return _section(request, ref, org, placement_id)


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
    return _section(request, ref, org, placement_id)


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

    carrier = _market_named(conn, values.get("market"))
    intermediary = _market_named(conn, values.get("via"))
    if isinstance(carrier, str):
        return refused(carrier)
    if isinstance(intermediary, str):
        return refused(intermediary)
    if carrier is None and intermediary is None:
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
    who = (carrier or intermediary)
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="market_approach",
            summary=f"approached {who.name}", org_id=org.id,
        ):
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


def _market_named(conn: sqlite3.Connection, name: str | None) -> Any:
    """The market a typed name refers to — or the REFUSAL, as a string.

    A name is resolved, never created: a submission recorded against a market
    the book has never heard of would be a new org minted by a typo, and every
    later lookup on the real one would miss it. The refusal names the nearest,
    which is the rule repo/lines.py sets for a vocabulary miss (advisory when
    reading, authoritative when writing) and the one `_resolve_market` follows
    on the MCP side.

    AND IT NAMES WHERE TO PUT A MARKET THAT IS GENUINELY NEW. A carrier this
    book has never carried is ORDINARY in placement — it is most of what
    growing a market list looks like — and `nearest: none close` stated the
    objection and stopped, which is the one refusal on this panel that fell
    short of the standard `date_refusal` sets (found 2026-08-26). It cannot be
    created from here for the reason above, so the sentence says the page that
    can. Named on BOTH branches: a near miss that is not the market being typed
    leaves a broker just as stuck as no match at all.

    Returns None for "nothing typed", an Org for a hit, and a `str` for a miss
    — the caller distinguishes them, because a miss is not an exception here:
    it is a message that belongs in the add row beside the input.
    """
    from rapidfuzz import process

    typed = (name or "").strip()
    if not typed:
        return None
    org = orgs_repo.find(conn, typed) or orgs_repo.find_by_name(conn, typed)
    if org is not None and org.kind == "market":
        return org
    names = [o.name for o in orgs_repo.list_orgs(conn, kind="market")]
    close = process.extract(typed, names, limit=3, score_cutoff=60)
    hint = ", ".join(m[0] for m in close) if close else "none close"
    return (
        f"no market matching {typed!r} — nearest: {hint}. If {typed!r} is new "
        f"to the book, add it at /markets/new first, then record the approach."
    )


def _add_row(
    request: Request,
    ref: str,
    placement_id: str,
    line_id: str,
    fields: tuple[Field, ...],
    values: dict[str, str],
    error: str,
) -> HTMLResponse:
    """The add row again, with the message and everything typed still in it.

    COMMIT IN PLACE, the platform default: a refused save keeps the form open
    with its input intact. One `<tr>`, targeted where it already is, so nothing
    else on the page moves under a broker mid-correction."""
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
            block, fields, values, marketing_grid.COLUMNS, error
        )
    )
    return HTMLResponse(html)
