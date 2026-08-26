"""The marketing grid's view model: the report, ready to render — and to edit —
in a browser.

THE PANEL IS THE REPORT (Grant, 2026-08-25). The Program tab renders exactly
what `/export/marketing.xlsx` writes — one block per line of coverage, the
same rows in the same order — because two renderings of one thing is the
second copy that quietly differs, and the copy that differs here is the one a
client is sent. So this module COMPOSES NOTHING: every figure comes from
`services.marketing_report.compose`, and every string it prints comes from
that module's own formatters (`fmt_date`, `fmt_rate`, `fmt_exposure`,
`format_cents`). The one decision made here is presentation — which column,
which alignment, which pill, and which cells are editable where they sit.

It needs NO program file. Marketing happens before a tower exists, and every
figure in this grid lives in SQLite, so the section renders on a placement
whose `program_path` is NULL exactly as it does on a linked one — the same
reason the marketing download sits in the band rather than in the
linked-only export strip.

WHY THE COLUMNS ARE NOT `marketing_report.columns()`. The sheet collapses what
a reader only reads; the grid un-collapses what a broker has to TYPE, and the
rule deciding both is the same one — YOU CANNOT TYPE A DERIVED VALUE.

  * "Total est. cost" becomes TRIA / Fees / SL tax. `MarketResponse.total_cost`
    is the sum of four components and is blank while any is unknown, so the
    total is printed and never edited, and each component is a cell.
  * "Layer" becomes Attach / Limit. `$5M xs $5M` is built from those two by
    `marketing_report._layer_label`; a cell over the sentence would parse
    English.
  * "submitted 20 Aug" comes DOWN OUT OF THE BLOCK HEADING and becomes a Sent
    cell per row. The heading could only print it when every package on the
    line went out the same day — a coincidence of values, not a block fact —
    and on a line marketed over two weeks it printed nothing at all. It is the
    submission's own column and the only one on this grid that is not the
    response's.

Same rows, same order, three columns un-collapsed.

WHY THIS COMPOSES `INTERNAL` WHILE THE BUTTON BESIDE IT DOWNLOADS `CLIENT`.
The workbook is what leaves the building and stays client-composed. This grid
is the broker's own screen, and Grant put two internal facts on it on
2026-08-25: the private decline reason (as a cell, marked at the field), and
the clearance conflicts (as a warning strip). `INTERNAL` is what carries both
out of the composer, so reading them here costs no second composition of the
same rows. Commission and the underwriter's own notes are composed and
deliberately NOT rendered — they are the internal sheet's, and putting them on
a screen read over a shoulder is still a decision nobody has made.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..forms.inline import (
    MARKET_APPROACH_FIELDS,
    MARKET_RESPONSE_FIELDS,
    market_approach_fields,
    placement_line_fields,
)
from ..forms.spec import Field, initial_text
from ..models import (
    MARKET_RESPONSE_STATUS_LABELS,
    PUBLIC_DECLINE_REASON_LABELS,
    RATE_PER_LABELS,
    rating_basis,
)
from ..money import format_cents
from ..repo import lines as lines_repo
from ..repo import marketing as marketing_repo
from ..services import marketing_report
from .forms_render import render_cell_display

# The house mark for "there is no value here", as `_last_synced` and the
# renewal tables print it. Distinct from "not set", which the block header
# uses: a header figure nobody has recorded is a gap somebody can close, and
# a blank there reads as a rendering fault (design step 4).
DASH = "—"
NOT_SET = "not set"

# NULL attach is not a missing figure — it is the ordinary answer, and it has
# a word. Printed in the Attach cell so the reader is never left deciding
# whether an em-dash there means primary or means nobody has said.
PRIMARY = "primary"

# NO INTERMEDIARY IS NOT A MISSING FIGURE EITHER — it is the ordinary answer,
# and it has a word for the same reason `primary` does. An em-dash in this
# column would leave a reader deciding whether it means "we went straight to
# the carrier" or "nobody has recorded how we got there", and those are
# different facts. The EDITOR still pre-fills blank: what a form pre-fills has
# to be something its own parser accepts back unchanged.
DIRECT = "direct"

# The one field on this grid that is stored as an ID and typed as a NAME. It
# is spelled once here so the column, the cell route, the editor's completion
# list and the MCP argument cannot drift apart over which key means it.
VIA = "via_org_id"

# key -> Field, so a URL segment can be checked against the editable set
# server-side — the same guard routes/work.py applies to a task cell. The
# markup constrains a mouse and nothing else.
CELL_FIELDS: dict[str, Field] = {f.key: f for f in MARKET_RESPONSE_FIELDS}

# THE ONE FIELD ON THIS GRID THAT IS NOT THE RESPONSE'S. `sent_on` belongs to
# the SUBMISSION the row hangs off, and it is taken from the add row's own
# tuple rather than declared a second time here: the date a broker types when
# recording the approach and the date they correct afterwards are the same
# fact, and a second Field is a second label, a second parser and a second
# thing to keep in step.
SENT_FIELD: Field = next(f for f in MARKET_APPROACH_FIELDS if f.key == "sent_on")


@dataclass(frozen=True)
class Column:
    """One column of the grid.

    `key` names the fact and `field` names the `market_response` column a cell
    writes to — they are the same string wherever a column is editable, and
    `field` is None for the four the grid PRINTS and cannot take (Best, the
    rate movement, the total, the open-subjectivity count). A row is built by
    walking this tuple, so the header, the cell and the route that saves it
    cannot drift apart.
    """

    key: str
    header: str
    numeric: bool = False
    field: str | None = None
    # WHOSE COLUMN THE CELL WRITES TO. Every column but one is the market
    # response's own; `sent_on` is the SUBMISSION's, and the two take different
    # routes, different guards and different answers (a submission carries
    # every line of coverage, so correcting its date moves rows in other blocks
    # — which is why that save answers with the whole section).
    record: str = "response"
    # A prose column that must be allowed to wrap rather than size its column
    # to an unwrapped sentence — the failure that pushed three columns off the
    # right-hand edge of the RFI panel (routes/work.py `_ITEM_CELL_CLASS`).
    prose: bool = False
    # PINNED TO THE LEFT EDGE WHILE THE REST SCROLLS UNDER IT, 1-based in the
    # order they pin. The grid is 22 columns and 41% of it is off-screen on a
    # 1600px window (measured 2026-08-26), so by the time a reader reaches SL
    # tax there was nothing on screen saying WHOSE row it is — and these two
    # columns are the answer to exactly that question.
    #
    # DECLARED HERE rather than as an `nth-child` in the stylesheet, because
    # the pin belongs to the column and a CSS rule counting positions goes
    # silently wrong the moment a column is inserted before it. The second
    # pin's left OFFSET is the first one's rendered width, which auto table
    # layout decides from content — so it is measured in marketing-grid.js and
    # cannot be a number in either file.
    pin: int = 0

    @property
    def sortable(self) -> bool:
        """Whether a reader can order the grid by this column.

        READ, NOT DECLARED. `marketing_report.SORT_KEYS` is where a column's
        order is defined and it is keyed by these very keys, so a column is
        sortable exactly when there is a key for it — a second list here would
        be the copy that quietly differs, offering a control whose route then
        refuses, or hiding one that works.
        """
        return self.key in marketing_report.SORT_KEYS

    @property
    def th_class(self) -> str:
        """The header cell's class. A pinned column pins its HEADER too, or
        the column slides out from under its own label."""
        parts = []
        if self.numeric:
            parts.append("num")
        if self.pin:
            parts.append(f"pin pin-{self.pin}")
        return " ".join(parts)


COLUMNS: tuple[Column, ...] = (
    Column("market", "Market", prose=True, pin=1),
    # HOW WE REACHED THAT PAPER, its own column and its own cell.
    #
    # THE GRID UN-COLLAPSES WHAT A BROKER HAS TO TYPE — the same rule that
    # splits the workbook's "Layer" into Attach and Limit and its "Total est.
    # cost" into three. The sheet prints "Zurich (via RT Specialty)" in one
    # cell because a client only READS it (`ReportRow.market_cell`, still the
    # sheet's); a broker has to be able to CORRECT it, and until this column
    # existed no surface in the app could — not a cell, not a form, not an MCP
    # argument (Grant, 2026-08-26: "no way to update the access point … to
    # turn back to a direct approach"). Recording a wholesaler and finding out
    # the submission went direct is an ordinary correction with no fix.
    #
    # AND IT IS NOT A SECOND COPY OF THE MARKET COLUMN. The Market cell beside
    # it now prints the CARRIER alone, so the two columns state two facts once
    # each rather than one fact twice (CLAUDE.md, DRY).
    Column("access", "Access", field=VIA, prose=True, pin=2),
    Column("best", "Best"),
    Column("attach", "Attach", numeric=True, field="attach"),
    Column("lim", "Limit", numeric=True, field="lim"),
    Column("status", "Status", field="status"),
    # SENT, THEN REPLIED, in the order they happened. THE GRID UN-COLLAPSES
    # WHAT A BROKER HAS TO TYPE — the same rule that turns the sheet's "Total
    # est. cost" into three cells and its "Layer" into Attach and Limit. The
    # sheet collapses the send date into the block heading because a reader
    # only reads it; a broker has to be able to CORRECT it, and until this
    # column existed no surface in the app could: `submission_form` is
    # create-only, there is no `submission_*` MCP tool, and `_reply_guard`
    # refused every reply dated before a mistyped send while naming that
    # correction as the fix (found 2026-08-26). The heading no longer prints
    # it, so nothing is stated twice.
    Column("sent_on", "Sent", field="sent_on", record="submission"),
    Column("responded_on", "Replied", field="responded_on"),
    # AND WHEN THE TERMS DIE, beside the day they arrived — the two dates are
    # read together and a broker types them from the same quote letter. It is
    # the BROKER's clock rather than the client's (GRID_ONLY in the gates says
    # so): the workbook is a point-in-time comparison, and putting an expiry
    # on what leaves the building is a decision about the client report that
    # nobody has made.
    Column("quote_expires_on", "Expires", field="quote_expires_on"),
    Column("rate", "Rate", numeric=True, field="rate_micros"),
    # WHAT THE ROW'S RATE IS STATED PER, where the block heading's denominator
    # is not it. PRINTED, never a cell: `market_response.rate_per` is stamped
    # by the write from the line's own denominator
    # (repo.marketing._stamp_rate_per) and typing over it would be claiming a
    # rate was quoted against something it was not — the same reason the Total
    # and the rate movement are printed and not editable. Correcting it means
    # correcting the rate, which IS a cell, one column to the left.
    Column("rate_per", "Rate per"),
    Column("rate_move", "Rate Δ", numeric=True),
    Column("premium", "Premium", numeric=True, field="premium"),
    Column("tria", "TRIA", numeric=True, field="tria_premium"),
    Column("fees", "Fees", numeric=True, field="policy_fees"),
    Column("sl_tax", "SL tax", numeric=True, field="surplus_lines_tax"),
    Column("total_cost", "Total", numeric=True),
    Column("subjectivities", "Subj.", numeric=True),
    # THE HEADER IS THE MARKING. An inline cell has no visible label of its
    # own — the column header is it — so the one field on this grid whose
    # words reach a client says so where it is read, and the one that never
    # does says that too. Two fields, never one with a "safe to share" tick:
    # a checkbox fails the first time somebody forgets it, and the cost of
    # that failure is a client reading an underwriter's private opinion.
    Column("reason", "Reason (to client)", field="decline_reason_public", prose=True),
    # THE TWO PER-ROW OVERRIDES OF THE BLOCK HEADING'S FACTS. They are on the
    # CLIENT's workbook and were on no screen the broker has — so a client
    # could read a basis and an exposure the panel did not show, which makes
    # "the panel is the report" false in the direction that matters most
    # (found 2026-08-26 by the column walk). PRINTED, not cells: neither
    # `rating_basis` nor `exposure_amount` is an editable Field on a market
    # response, and an exposure typed with no basis beside it is the one thing
    # `_basis_guard` exists to refuse. Blank on the ordinary row, because a
    # row that agrees with its heading has nothing to add.
    Column("basis_override", "Basis", prose=True),
    Column("exposure_override", "Exposure", numeric=True),
    Column("internal_reason", "Internal (never sent)", field="decline_reason", prose=True),
)

# Colour is signal, never decoration, and every coloured state here still
# prints its own word. The mapping matches routes/book.py `_status_class` where
# the words overlap (declined is danger ink there too), so one vocabulary does
# not read two ways on two tabs.
_STATUS_TONE = {
    "bound": "is-good",
    "quoted": "is-accent",
    "indicated": "is-slate",
    "pending": "is-warn",
    # THE SAME TINT AS `pending`, because it means the same thing to the eye
    # scanning this column: there is something still to do here. It is NOT the
    # decline tint — reading it as danger is exactly the misreading the status
    # exists to stop (models.MARKET_RESPONSE_STATUSES says why), and the pill
    # prints its own words either way.
    "declined_open_elsewhere": "is-warn",
    "declined": "is-danger",
    # OUR judgment, not the market's, so not the decline tint — the market did
    # not refuse us. Muted like `non_response`: it is closed, there is nothing
    # left to chase, and it is not bad news about the placement so much as a
    # market that was never available at this size.
    "not_viable": "is-muted",
    "non_response": "is-muted",
}


def _money(cents: int | None) -> str:
    """NULL prints the house dash; ZERO prints as money.

    They are different answers and the column has to show it — 0 is "we asked,
    there is none", which is what makes a Total possible on admitted domestic
    business, and a blank is "nobody has told us", which is what keeps the
    Total honestly empty."""
    return format_cents(cents) if cents is not None else DASH


def _rate_move_cell(row: marketing_report.ReportRow) -> dict[str, Any]:
    """A number, or the REASON there is no number — never a bare blank.

    "basis changed" and "no expiring rate recorded" are the two sentences the
    composer produces, and they are the whole point of the column: a blank
    where a rate movement should be reads as a figure that failed to render,
    and the reader cannot tell it from a market nobody has heard back from.
    """
    move = row.rate_move
    if move.pct is not None:
        # Down is the broker's win on a rate column, which is why this is not
        # the same good/bad polarity a premium column would take.
        return _cell(move.cell, tone="is-good" if move.pct < 0 else "is-warn")
    if move.note:
        return _cell(move.note, tone="is-note")
    return _cell(DASH)


def _cell(text: str, *, tone: str = "", pill: bool = False) -> dict[str, Any]:
    return {"text": text, "tone": tone, "pill": pill, "html": ""}


def _cells(
    row: marketing_report.ReportRow, window: marketing_report.DateWindow | None
) -> dict[str, dict[str, Any]]:
    return {
        # THE CARRIER ALONE. `market_cell` — which folds the intermediary in —
        # is the SHEET's rendering and stays the sheet's: the Access column
        # beside this one is where the grid says how the paper was reached, and
        # printing it in both places states one fact twice. What survives here
        # is the "carrier TBD" case, because a row addressed only to a
        # wholesaler has no carrier to print and a blank first cell reads as a
        # rendering fault.
        "market": _cell(
            row.market or ("carrier TBD" if row.via else row.market_cell)
        ),
        "access": _cell(row.via or DIRECT),
        "best": _cell(row.best or DASH),
        # PRIMARY IN WORDS, and only in the display. The editor pre-fills from
        # the stored value (blank), because what a form pre-fills has to be
        # something its own parser accepts back unchanged — the same split
        # routes/work.py draws over an assignee's " — our team" suffix.
        "attach": _cell(_money(row.attach) if row.attach is not None else PRIMARY),
        "lim": _cell(_money(row.lim)),
        "status": _cell(
            row.status, tone=_STATUS_TONE.get(row.status_key, ""), pill=True
        ),
        "sent_on": _cell(
            marketing_report.fmt_date(row.submitted_on, window) or DASH
        ),
        "responded_on": _cell(
            marketing_report.fmt_date(row.responded_on, window) or DASH
        ),
        # THE HOUSE DASH, not a countdown. `services.quotes.expiry_word` owns
        # the "5d left" / "expired 3d ago" vocabulary and every surface that
        # prints it reads a submission's rolled-up date; this cell is the ROW's
        # own figure, printed the way the composer prints every other date on
        # the grid. A second countdown here would be a second copy of that
        # judgment, taken off a different object — the "70d over" defect.
        "quote_expires_on": _cell(
            marketing_report.fmt_date(row.quote_expires_on, window) or DASH
        ),
        "rate": _cell(marketing_report.fmt_rate(row.rate_micros) or DASH),
        # THE COMPOSER'S OWN STRING. Deciding here whether this row's
        # denominator differs from its block's would be a second copy of that
        # judgment, and the copy that differs is the one on the screen rather
        # than the one in the file the client is sent.
        "rate_per": _cell(row.rate_per_override or DASH),
        "rate_move": _rate_move_cell(row),
        "premium": _cell(_money(row.premium)),
        "tria": _cell(_money(row.tria)),
        "fees": _cell(_money(row.fees)),
        "sl_tax": _cell(_money(row.sl_tax)),
        "total_cost": _cell(_money(row.total_cost)),
        "subjectivities": _cell(
            str(row.open_subjectivities) if row.open_subjectivities else DASH,
            tone="is-warn" if row.open_subjectivities else "",
        ),
        "reason": _cell(row.public_reason or DASH),
        "basis_override": _cell(row.basis_override or DASH),
        "exposure_override": _cell(row.exposure_override or DASH),
        "internal_reason": _cell(row.internal_reason or DASH),
    }


# THE PACKAGE's STATUS, TINTED. Its vocabulary is not the response's
# (models.SUBMISSION_STATUS_LABELS says why), so it needs its own map — and the
# three words the two share take the SAME tint, because one word must not read
# two ways four inches apart on one screen. `out` takes the warn tint `pending`
# takes: both mean asked, nothing back. `withdrawn` is muted, like
# `non_response`: it is closed and there is nothing left to chase.
_SUBMISSION_TONE = {
    "bound": "is-good",
    "quoted": "is-accent",
    "declined": "is-danger",
    "out": "is-warn",
    "withdrawn": "is-muted",
}

# The one status a package cannot gain a line of coverage under. Withdrawing is
# a decision about the SUBMISSION (we pulled it) and `roll_up_submission` never
# writes it and never writes over it — so a response created under a withdrawn
# package would hang off a parent that can never be recomputed from it, which
# is the same permanently-mis-stated row `marketing_entry.approach` refuses to
# create when it declines to reuse a withdrawn submission. The control is
# WITHDRAWN rather than left to refuse, the way the retired line's add-market
# row is: an affordance that only ever answers no is worse than none, and the
# row says why where the control would have been.
WITHDRAWN = "withdrawn"


def _column_class(column: Column, cell: dict[str, Any]) -> str:
    """The column class, in ONE place. It used to be a literal at each of the
    three sites that build a cell — the panel's first render, the display
    route htmx swaps back after a save, and the editor — which is how a cell
    loses its formatting the moment it is edited and the column silently
    changes shape mid-session (fixed on the RFI items table, 2026-08-19)."""
    parts = []
    if column.numeric:
        parts.append("num mono")
    elif column.prose:
        parts.append("prose")
    if column.pin:
        # THE SAME TWO CLASSES THE HEADER TAKES (`Column.th_class`), because a
        # pinned body cell and an unpinned header slide apart the moment the
        # grid is scrolled sideways.
        parts.append(f"pin pin-{column.pin}")
    if cell["pill"]:
        parts.append("pill-cell")
    if cell["tone"]:
        parts.append(cell["tone"])
    return " ".join(parts)


def cell_action(ref: str, placement_id: str, response_id: str, key: str) -> str:
    return (
        f"/accounts/{ref}/program/{placement_id}"
        f"/marketing/responses/{response_id}/cell/{key}"
    )


def response_base(ref: str, placement_id: str, response_id: str) -> str:
    """One row's URL prefix — what its confirm hangs `/remove` and `/row` off.

    HERE, beside the other URL builders for this grid, and not in the route
    module that uses it. Every path this panel renders is spelled once in this
    file so a route and the markup pointing at it cannot drift; and the G5
    refusal walk reads `routes/marketing.py`'s returned STRINGS as sentences a
    broker is refused in, so a URL returned from there is a "refusal" nobody
    can name a fix for — which is the walk being right about the shape and
    wrong about the string.
    """
    return (
        f"/accounts/{ref}/program/{placement_id}/marketing/responses/{response_id}"
    )


def sent_cell_action(ref: str, placement_id: str, response_id: str) -> str:
    """A ROUTE OF ITS OWN, addressed by the RESPONSE. The submission is what
    gets written, but the row is what the broker is looking at and one response
    hangs off exactly one submission — so the id in the URL is the one the page
    already has, and `_owned_response` is the scope check that already exists
    for it. A URL naming the submission would need a second one."""
    return (
        f"/accounts/{ref}/program/{placement_id}"
        f"/marketing/responses/{response_id}/sent"
    )


def sent_display_value(
    sent_on: str | None, window: marketing_report.DateWindow | None = None
) -> str:
    """What the Sent cell SHOWS — through the composer's own date formatter,
    against the same window the grid behind it used, so a cell re-rendered
    after a save cannot judge the year differently from the row it lands in."""
    return marketing_report.fmt_date(sent_on, window) or DASH


def _via_or_raise(key: str, via_name: str | None) -> str:
    """The access point's name, or a refusal to guess one.

    `via_org_id` is the ONE editable key on this grid whose stored value is an
    id and whose printed value is a NAME, so the two functions below cannot
    read it off the row the way they read every other one — the name is a
    lookup and only the route holds a connection. Raising rather than falling
    through to `str(value)` is the point: the fall-through prints a ULID into
    the cell a broker reads, which looks like data rather than like a bug."""
    if via_name is None:
        raise ValueError(
            f"{key} is stored as an id and printed as a name — pass via_name "
            "(routes/marketing.py resolves it through repo.orgs)"
        )
    return via_name


def display_value(
    response: Any,
    key: str,
    window: marketing_report.DateWindow | None = None,
    *,
    via_name: str | None = None,
) -> str:
    """What an editable cell SHOWS. Read off the stored row rather than off the
    composed report, because the display route re-renders one cell after a save
    and composing the whole report to print one figure would be six queries for
    a number already in hand.

    The one departure from `initial_text` is `attach`: blank means primary and
    says so. The EDITOR still pre-fills blank (see `_cells`)."""
    value = getattr(response, key, None)
    if key == VIA:
        # DIRECT IN WORDS, and only in the display — same split as `primary`
        # one line down: the editor pre-fills blank, because what a form
        # pre-fills has to be something its own parser accepts back unchanged.
        return _via_or_raise(key, via_name) or DIRECT
    if key == "attach":
        return _money(value) if value is not None else PRIMARY
    field = CELL_FIELDS[key]
    if field.kind == "money":
        return _money(value)
    if field.kind == "rate":
        return marketing_report.fmt_rate(value) or DASH
    if field.kind == "date":
        # THE SAME WINDOW THE GRID BEHIND THIS CELL USED. A cell re-rendered
        # after a save that judged the year differently from the row it lands
        # in is the copy that differs, on the same screen, one swap apart.
        return marketing_report.fmt_date(value, window) or DASH
    if key == "status":
        return MARKET_RESPONSE_STATUS_LABELS.get(str(value), str(value))
    if key == "decline_reason_public":
        return PUBLIC_DECLINE_REASON_LABELS.get(str(value or ""), "") or DASH
    return str(value) if value else DASH


def editor_value(response: Any, key: str, *, via_name: str | None = None) -> str:
    """What an editable cell's EDITOR pre-fills with — always the stored value
    in the form its own parser accepts back, never the display string."""
    if key == VIA:
        # THE NAME, NOT THE ID, and BLANK where the display says "direct":
        # blank is what this cell's own parser reads as a direct approach, so
        # clearing the box and typing nothing is the correction Grant asked
        # for rather than a value that has to be spelled.
        return _via_or_raise(key, via_name)
    return initial_text(CELL_FIELDS[key], getattr(response, key, None))


def cell_class(key: str, response: Any) -> str:
    """The class an editable cell carries, for the display route and the editor
    route — the same computation the panel's first render makes, off the same
    Column tuple."""
    column = next(c for c in COLUMNS if c.field == key)
    tone = ""
    pill = key == "status"
    if pill:
        tone = _STATUS_TONE.get(str(getattr(response, "status", "")), "")
    return _column_class(column, {"pill": pill, "tone": tone})


def row_view(
    request: Any,
    row: marketing_report.ReportRow,
    *,
    ref: str,
    placement_id: str,
    window: marketing_report.DateWindow | None = None,
) -> dict[str, Any]:
    """One rendered grid row.

    Built by walking COLUMNS, so a column added above without a cell below
    raises here rather than silently shifting every later cell one place to
    the left — the failure mode a hand-ordered tuple has.
    """
    cells = _cells(row, window)
    built = []
    for column in COLUMNS:
        cell = dict(cells[column.key])
        cell["class"] = _column_class(column, cell)
        if column.record == "submission":
            # The SAME cell contract, pointed at the submission behind the row.
            cell["html"] = render_cell_display(
                request,
                SENT_FIELD,
                "" if cell["text"] == DASH else cell["text"],
                sent_cell_action(ref, placement_id, row.response_id),
                extra_class=cell["class"],
            )
        elif column.field is not None:
            # The SHARED cell contract (macros/cell.html): the display half is
            # the persistent state, the editor is fetched on activation, blur
            # commits and Escape discards. Rendered through the same macro
            # every other inline cell in the app uses — never a copy of it.
            cell["html"] = render_cell_display(
                request,
                CELL_FIELDS[column.field],
                "" if cell["text"] == DASH else cell["text"],
                cell_action(ref, placement_id, row.response_id, column.field),
                extra_class=cell["class"],
            )
        built.append(cell)
    return {
        "id": row.response_id,
        "cells": built,
        # THE "NO FEES OR TAXES APPLY" AFFORDANCE, offered only while it would
        # DO something. NULL is "nobody has told us" and 0 is "we asked, there
        # is none"; only 0 contributes to a total, so without a one-click way
        # to say it the Total column stays blank forever on admitted domestic
        # business — or a broker types three zeros. Once all three are known
        # the control is hidden: an affordance that does nothing is worse than
        # none.
        "charges_unknown": any(
            value is None for value in (row.tria, row.fees, row.sl_tax)
        ),
        "no_charges_url": (
            f"/accounts/{ref}/program/{placement_id}"
            f"/marketing/responses/{row.response_id}/no-charges"
        ),
        # A ROW RECORDED IN ERROR (Grant, 2026-08-26). Always offered, unlike
        # "no charges" — that one is hidden once it would do nothing, and this
        # one can always do something. It fetches the CONFIRM rather than
        # writing: a delete is the one row action whose mistake looks exactly
        # like success, because what it destroys is no longer on the screen to
        # notice missing.
        "remove_url": (
            f"/accounts/{ref}/program/{placement_id}"
            f"/marketing/responses/{row.response_id}/remove"
        ),
    }


# --- the marketing with no line of coverage yet ----------------------------
#
# A SUBMISSION WITH NO RESPONSE ROWS IS REAL MARKETING THAT HAPPENED, and the
# panel used to print "No line of coverage on this placement is being marketed
# yet" straight over it — on fourteen seeded placements, four of them live and
# two quoted at $1.4M (Grant, 2026-08-26). These rows end that sentence.
#
# EVERY CELL IS PRINTED AND NONE IS EDITABLE, and that is the design. The
# figures live on `submission`, which is a CACHE of the response rows
# everywhere else in this app (repo.marketing.roll_up_submission), and a cell
# that wrote to a cache is a second home for the fact — the very defect the
# roll-up exists to close. The one thing that can be done to one of these rows
# is to give it the line of coverage it is missing, after which it is an
# ordinary row in an ordinary block and every cell on it is editable.


def _provisional_cells(
    row: marketing_report.ProvisionalRow,
    window: marketing_report.DateWindow | None,
) -> dict[str, dict[str, Any]]:
    """One provisional row's cells, keyed by the SAME COLUMNS the grid above
    it walks — so the two tables line up column for column, and a column added
    to `COLUMNS` raises here rather than shifting these cells one place left.

    WHAT PRINTS THE HOUSE DASH IS WHAT IS NOT KNOWN; what prints nothing at
    all is what could not be known of a package with no line of coverage. Rate,
    denominator, rate movement, basis and exposure are all facts stated per
    unit of exposure ON A LINE, and a dash there would read as a figure
    somebody could go and fetch. They are left EMPTY, and the block's own
    heading says in words why.
    """
    return {
        "market": _cell(row.market),
        # EMPTY, NOT `direct`. A submission has no intermediary column at all
        # — the access point is a fact about a RESPONSE — so "direct" here
        # would be a claim nobody made about a package that has not been
        # recorded against a line of coverage yet. This is the same reading
        # the rate and basis cells below take: what could not be known of a
        # package like this is left blank, and the block heading says why.
        "access": _cell(""),
        "best": _cell(row.best or DASH),
        # NO ATTACHMENT AND NO "PRIMARY". A submission states a limit, never a
        # band, and `PRIMARY` in this cell would claim the quote sits at the
        # bottom of a tower nobody has drawn.
        "attach": _cell(DASH),
        "lim": _cell(_money(row.lim)),
        "status": _cell(
            row.status, tone=_SUBMISSION_TONE.get(row.status_key, ""), pill=True
        ),
        "sent_on": _cell(marketing_report.fmt_date(row.submitted_on, window) or DASH),
        "responded_on": _cell(
            marketing_report.fmt_date(row.responded_on, window) or DASH
        ),
        "quote_expires_on": _cell(
            marketing_report.fmt_date(row.quote_expires_on, window) or DASH
        ),
        "rate": _cell(""),
        "rate_per": _cell(""),
        "rate_move": _cell(""),
        "premium": _cell(_money(row.premium)),
        "tria": _cell(DASH),
        "fees": _cell(DASH),
        "sl_tax": _cell(DASH),
        "total_cost": _cell(DASH),
        "subjectivities": _cell(
            str(row.open_subjectivities) if row.open_subjectivities else DASH,
            tone="is-warn" if row.open_subjectivities else "",
        ),
        "reason": _cell(DASH),
        "basis_override": _cell(""),
        "exposure_override": _cell(""),
        "internal_reason": _cell(row.internal_reason or DASH),
    }


def assign_action(ref: str, placement_id: str, submission_id: str) -> str:
    return (
        f"/accounts/{ref}/program/{placement_id}"
        f"/marketing/submissions/{submission_id}/line"
    )


def assign_line_options(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """The lines of coverage the ASSIGN control offers, in ONE place — the
    panel renders these as its options and the POST re-queries the same list so
    `checked_option` is authoritative rather than decorative.

    THE BOOK'S LIVING VOCABULARY, and NOT `line_add_options`. That control
    drops every line already on the placement, because it is for opening a
    block that does not exist; this one is for putting a package INTO a block,
    and the ordinary case is the block that is already there. Nothing is
    dropped, for one more reason worth stating: not one `placement_line` row
    exists on the seeded book, so a picker built from what the placement has
    DECLARED would be empty on every placement this feature exists to rescue —
    built and not accessible, which is a bug class here.

    RETIRED LINES ARE NOT OFFERED (`all_lines` is the living list). Assigning
    is starting to market a line, and the book's own rule is that you may
    correct what a market already said on a retired line and may not start
    there.
    """
    return [(line.name, line.id) for line in lines_repo.all_lines(conn)]


def provisional_row_view(
    row: marketing_report.ProvisionalRow,
    *,
    ref: str,
    placement_id: str,
    window: marketing_report.DateWindow | None = None,
) -> dict[str, Any]:
    """One row of the provisional block, built by walking COLUMNS."""
    cells = _provisional_cells(row, window)
    built = []
    for column in COLUMNS:
        cell = dict(cells[column.key])
        cell["class"] = _column_class(column, cell)
        built.append(cell)
    return {
        "id": row.submission_id,
        "cells": built,
        # WITHDRAWN PACKAGES KEEP THEIR ROW AND LOSE THEIR CONTROL. The
        # marketing happened and stays reported; what is withheld is a write
        # that would leave the row permanently mis-stated (see `WITHDRAWN`).
        "can_assign": row.status_key != WITHDRAWN,
        "assign_url": assign_action(ref, placement_id, row.submission_id),
    }


def provisional_view(
    conn: sqlite3.Connection,
    report: marketing_report.MarketingReport,
    *,
    ref: str,
    placement_id: str,
    error: str | None = None,
    error_row: str | None = None,
) -> dict[str, Any] | None:
    """The provisional block, or None when there is nothing in it.

    NOT a `block_view`. It carries no line id, no header facts, no clearance
    strip, no bridge and no add-a-market row — none of them is knowable of a
    package whose line of coverage nobody has recorded, and a header of nine
    "not set" cells beside a $1.4M quote is an invitation to type figures
    against a line nobody chose.
    """
    if not report.provisional:
        return None
    return {
        "id": f"mprov-{placement_id}",
        "label": marketing_report.PROVISIONAL_LABEL,
        "rows": [
            provisional_row_view(
                row, ref=ref, placement_id=placement_id, window=report.window
            )
            for row in report.provisional
        ],
        # THE SAME LIST THE POST CHECKS AGAINST. Empty is a real state — a book
        # with no lines of coverage at all — and the template says so in words
        # and names the control that fixes it, rather than rendering a picker
        # with nothing in it.
        "line_options": assign_line_options(conn),
        # A REFUSAL BESIDE THE CONTROL THAT RAISED IT, and beside the ROW it
        # was raised on. An assign answers with the whole section (the row
        # moves between blocks, so nothing smaller describes the change), and a
        # message dropped at the top of that section would sit inside the
        # add-a-line control three feet from the picker it is about.
        "error": error,
        "error_row": error_row,
    }


# --- the block header: what this line of coverage is expected to do ---------
#
# NINE CELLS, EDITED WHERE THEY PRINT, for the same reason the grid below them
# is: two ways to state one fact is the second copy that quietly differs. They
# are GROUPED rather than run together, because they are two kinds of fact —
# what we are asking the market for, and what the client had last year — and
# proximity is what tells a reader where one kind ends (the layer details row,
# 2026-08-20). Density is not the enemy; undifferentiated density is.

LINE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # The basis comes FIRST on purpose. It decides whether the exposure beside
    # it is money or a count, and an exposure typed before a basis is refused
    # — so the field that unblocks the others is the one the eye lands on.
    ("this term", (
        "rating_basis", "rate_per", "expected_exposure",
        "attach_sought", "limit_sought",
    )),
    ("expiring", (
        "expiring_basis", "expiring_exposure",
        "expiring_premium", "expiring_rate_micros",
    )),
)

LINE_KEYS: tuple[str, ...] = tuple(k for _, keys in LINE_GROUPS for k in keys)

# Which basis says what an exposure MEANS. models.RatingBasis.monetary makes
# the decision; this only says which of the two bases to ask.
EXPOSURE_BASIS: dict[str, str] = {
    "expected_exposure": "rating_basis",
    "expiring_exposure": "expiring_basis",
}

# The composer names two of these facts differently from the column they are
# stored in (`basis_key` and `exposure` read better on a report row). The
# panel renders off a ReportBlock and the cell routes render off the
# PlacementLine itself, so BOTH have to answer to the column name — which is
# what `stored` below is for. Without it the two renderers would each carry
# their own translation table and the copy that differs would be the one
# htmx swaps in after a save.
_BLOCK_ALIAS = {"rating_basis": "basis_key", "expected_exposure": "exposure"}


def stored(source: Any, key: str) -> Any:
    """The value behind a header cell, off a PlacementLine OR a ReportBlock —
    or off NOTHING, which is an ordinary state and not an error.

    A BLOCK CAN EXIST WITH NO `placement_line` ROW BEHIND IT: a response names
    the line, and nobody has stated a single expectation about it yet. All
    nine header cells then read "not set" and all nine are editable, because
    `set_placement_line` creates the row on first write — that is what makes
    them the way the figures get recorded at all. Without this line every one
    of them answered 500 (KeyError on the alias table), with no way out of it
    from the browser: reached by pressing `u` on "started marketing …", and by
    MCP's `market_approach`, which writes a response and no row (2026-08-25).
    """
    if source is None:
        return None
    if hasattr(source, key):
        return getattr(source, key)
    return getattr(source, _BLOCK_ALIAS[key], None)


def line_fields(source: Any) -> dict[str, Field]:
    """The nine header Fields, with the two exposures' KIND decided by the
    bases actually stored on this line. An exposure is cents on a monetary
    basis and a whole count otherwise, and `forms.inline.placement_line_fields`
    reads models.RatingBasis.monetary to say which — never this module."""
    fields = placement_line_fields(
        stored(source, "rating_basis"), stored(source, "expiring_basis")
    )
    return {f.key: f for f in fields}


def line_cell_action(ref: str, placement_id: str, line_id: str, key: str) -> str:
    return (
        f"/accounts/{ref}/program/{placement_id}"
        f"/marketing/lines/{line_id}/cell/{key}"
    )


def line_display_value(source: Any, key: str) -> str:
    """What a header cell SHOWS.

    NOT SET, IN WORDS, for anything unrecorded. A header figure nobody has
    entered is a gap somebody can close, and a blank there reads as a
    rendering fault — the reader cannot tell it from a zero. The class beside
    it drops the mono face so the words can never be misread as a figure."""
    value = stored(source, key)
    if value is None:
        return NOT_SET
    if key == "rate_per":
        # ONE list (models.RATE_PER_CHOICES) behind both the picker's options
        # and this word, so the header cannot print a denominator the picker
        # does not offer.
        return RATE_PER_LABELS.get(int(value), str(value))
    if key in ("rating_basis", "expiring_basis"):
        return rating_basis(str(value)).label
    if key in EXPOSURE_BASIS:
        # 350 power units, never $3.50 — marketing_report.fmt_exposure reads
        # RatingBasis.monetary and this does not re-judge it.
        return marketing_report.fmt_exposure(
            value, stored(source, EXPOSURE_BASIS[key])
        ) or NOT_SET
    field = line_fields(source)[key]
    if field.kind == "rate":
        return marketing_report.fmt_rate(value) or NOT_SET
    return format_cents(value)


def line_editor_value(source: Any, key: str) -> str:
    """What a header cell's EDITOR pre-fills with — the stored value in the
    form its own parser accepts back, never the display string. That equality
    is what makes opening a cell to READ it cost nothing: inline-cell.js
    compares the input against what it opened with, and `base.update` only
    logs what actually changes."""
    return initial_text(line_fields(source)[key], stored(source, key))


def line_cell_class(source: Any, key: str) -> str:
    unset = "" if stored(source, key) is not None else " is-unset"
    return f"marketing-fact-value{unset}"


def line_cell_html(
    request: Any, source: Any, key: str, *, ref: str, placement_id: str, line_id: str
) -> str:
    """One header cell, through the SHARED cell contract (macros/cell.html) —
    the same display → editor → POST the grid below and every other inline
    cell in the app uses, never a second copy of that markup.

    `tag="dd"`, because this cell is the value half of a definition list and
    not a table column. The tag has to track the caller's real container: a
    `<td>` outside a table row is dropped by the HTML parser, attributes and
    all (forms_render.py), which is the failure the parameter exists to stop.
    """
    return render_cell_display(
        request,
        line_fields(source)[key],
        line_display_value(source, key),
        line_cell_action(ref, placement_id, line_id, key),
        tag="dd",
        extra_class=line_cell_class(source, key),
    )


def _header(
    request: Any,
    block: marketing_report.ReportBlock,
    *,
    ref: str,
    placement_id: str,
) -> list[dict[str, Any]]:
    groups = []
    for label, keys in LINE_GROUPS:
        facts = [
            {
                "label": line_fields(block)[key].label,
                "html": line_cell_html(
                    request, block, key,
                    ref=ref, placement_id=placement_id, line_id=block.line_id,
                ),
            }
            for key in keys
        ]
        if label == "this term" and block.exposure_move.cell:
            # DERIVED, so it is PRINTED and never a cell — the same rule that
            # keeps the Total and the rate movement out of the editable set.
            # It is rendered only when the composer could compute it: its
            # absence is not an empty state, it is two figures that have not
            # both been recorded, and each of those has a cell of its own
            # right beside this.
            facts.insert(
                3,
                {
                    "label": "vs expiring",
                    # THE PERCENTAGE, OR THE COMPOSER'S OWN WORDS FOR WHY THERE
                    # ISN'T ONE (`Move.cell`) — never a number this module
                    # computed for itself. Two figures on different rating
                    # bases are not comparable, and the header says so where
                    # the percentage would have been.
                    "value": block.exposure_move.cell,
                },
            )
        groups.append({"label": label, "facts": facts})
    return groups


def _money_or_blank(cents: int | None) -> str:
    return format_cents(cents) if cents is not None else ""


def _bridge(bridge: marketing_report.Bridge | None) -> list[dict[str, str]] | None:
    """Why the premium moved, when the composer could reconcile it. Same four
    lines the sheet appends under the block, in the same order."""
    if bridge is None:
        return None
    return [
        {"label": "expiring premium", "value": format_cents(bridge.expiring_premium)},
        {"label": "rate effect", "value": format_cents(bridge.rate_effect)},
        {"label": "exposure effect", "value": format_cents(bridge.exposure_effect)},
        {"label": bridge.market, "value": format_cents(bridge.quoted_premium)},
    ]


def _clearance(block: marketing_report.ReportBlock) -> list[str]:
    """WARNED, NEVER REFUSED — the `line-gap` rule, on a different fact.

    Two live approaches reaching the same carrier through different
    intermediaries is the collision that gets one of them shut out at the
    underwriter's desk, and the book can only see it because both orgs are
    recorded. It is sometimes deliberate, so a hard block would make a
    legitimate entry impossible; the row is written and the warning stands
    beside it. repo.marketing.clearance_conflicts decides what a conflict is;
    the composer turns it into these words; this only gathers them."""
    return [
        f"{row.market or 'this carrier'} — {row.clearance}"
        for row in block.rows
        if row.clearance
    ]


# --- the order a reader asked for ------------------------------------------
#
# A VIEW, NEVER A STORED FACT. Sorting changes what a broker is looking at and
# nothing about the book, so it lives in the request and is echoed back into
# the markup — no column on `placement_line`, no setting, nothing to migrate.
#
# IT IS PER LINE OF COVERAGE. Sorting General Liability by premium says
# nothing about how Auto should read, and one order across a placement would
# make the two blocks answer a question only one of them was asked.
#
# ONE PARAMETER FOR THE WHOLE SECTION, though, and that is deliberate. The
# Sent cell answers with the ENTIRE section (one submission carries every line
# of coverage), so a sort held per block would be thrown away by a write in a
# different block — a view silently resetting, which reads as broken. The spec
# rides on the section element as one inherited `hx-vals`, so every request
# from anywhere inside it carries every block's order.
ASC = "asc"
DESC = "desc"

# `<line_id>:<column>:<direction>`, comma-joined. Line ids are slugs
# ("general-liability") and column keys are identifiers, so neither can contain
# the separators — asserted in tests/test_marketing_gates.py rather than
# trusted, because a line of coverage is NAMED BY A USER and its id is derived
# from that name.
_SPEC_PAIR = ":"
_SPEC_SEP = ","


def parse_sorts(spec: str) -> dict[str, tuple[str, bool]]:
    """`{line_id: (column, descending)}` off the wire.

    DEFENSIVE, because this arrives in a URL and anything that can make a
    request can send anything. A malformed entry is DROPPED rather than
    refused: an unreadable view parameter is not worth a 500, and the header
    re-renders from what was actually applied — so a spec that names a column
    this grid cannot order leaves the grid saying, correctly, that it is not
    sorted by it.
    """
    out: dict[str, tuple[str, bool]] = {}
    for part in spec.split(_SPEC_SEP):
        bits = part.strip().split(_SPEC_PAIR)
        if len(bits) != 3:
            continue
        line_id, column, direction = (b.strip() for b in bits)
        if not line_id or column not in marketing_report.SORT_KEYS:
            continue
        if direction not in (ASC, DESC):
            continue
        out[line_id] = (column, direction == DESC)
    return out


def format_sorts(sorts: dict[str, tuple[str, bool]]) -> str:
    """The spec, back on the wire. Sorted by line id so the same view produces
    the same string — an attribute that reshuffles on every render is a diff
    nobody can read and a cache nothing can match."""
    return _SPEC_SEP.join(
        f"{line_id}{_SPEC_PAIR}{column}{_SPEC_PAIR}{DESC if desc else ASC}"
        for line_id, (column, desc) in sorted(sorts.items())
    )


def cycled(
    sorts: dict[str, tuple[str, bool]], line_id: str, column: str
) -> dict[str, tuple[str, bool]]:
    """What clicking a header does: ascending, then descending, then OFF.

    THE THIRD CLICK IS NOT A FOURTH STATE, it is the way back. The composer's
    own order — live options first, then cheapest — is what a client should
    read a block in and what the workbook prints, so a reader who sorted to
    answer one question has to be able to put it back without reloading the
    page or guessing which column it started on.
    """
    fresh = dict(sorts)
    current = fresh.get(line_id)
    if current is None or current[0] != column:
        fresh[line_id] = (column, False)
    elif not current[1]:
        fresh[line_id] = (column, True)
    else:
        fresh.pop(line_id, None)
    return fresh


def sort_action(ref: str, placement_id: str) -> str:
    return f"/accounts/{ref}/program/{placement_id}/marketing/sort"


def header_cells(
    block: marketing_report.ReportBlock,
    sorts: dict[str, tuple[str, bool]],
    *,
    ref: str,
    placement_id: str,
) -> list[dict[str, Any]]:
    """One block's column headers, each carrying what it would do if clicked.

    Built here and not in the template so the header and the rows under it are
    walked from the SAME COLUMNS tuple in the same order — the failure a
    hand-ordered header has is silently labelling the wrong column, and it does
    not look like anything.
    """
    column, descending = sorts.get(block.line_id, ("", False))
    cells = []
    for spec in COLUMNS:
        active = spec.sortable and spec.key == column
        cells.append(
            {
                "header": spec.header,
                "class": spec.th_class,
                "sortable": spec.sortable,
                # WHAT A SCREEN READER IS TOLD. `aria-sort` belongs on the
                # header cell and takes "none" while the column is sortable
                # and unsorted — omitting it entirely says the column cannot
                # be sorted, which is a different claim.
                "aria_sort": (
                    ("descending" if descending else "ascending")
                    if active
                    else "none"
                ),
                # THE GLYPH IS BESIDE THE WORD, never instead of it: colour and
                # shape are signal, and the header still reads as its own name
                # (CLAUDE.md — every coloured state carries a glyph or a word).
                "mark": ("▼" if descending else "▲") if active else "",
                "active": active,
                "url": sort_action(ref, placement_id) if spec.sortable else "",
                # THE NEW ORDER AS THE BUTTON'S OWN `hx-vals`, NOT IN ITS URL.
                # The section publishes the CURRENT order as an inherited
                # `hx-vals` so every write inside it round-trips the sort — and
                # htmx builds a GET's query string from that inherited value,
                # which silently overwrote a `?sort=` the button had put in its
                # own href. Clicking Premium a second time re-sent the order it
                # was already in, so the column would sort ascending and then
                # never move again (found in a browser, 2026-08-26). A child's
                # `hx-vals` overrides an ancestor's for the same key, which is
                # exactly the relationship these two have: the section says
                # where the grid IS, the header says where it is GOING.
                "vals": json.dumps(
                    {"sort": format_sorts(cycled(sorts, block.line_id, spec.key))}
                )
                if spec.sortable
                else "",
                # WHAT THE CLICK WILL DO, in words, on hover and for assistive
                # tech — "sort by Premium, highest first" beats an arrow
                # nobody can interpret before they have pressed it once.
                "title": _sort_title(spec, active, descending),
            }
        )
    return cells


def _sort_title(spec: Column, active: bool, descending: bool) -> str:
    if not spec.sortable:
        return ""
    if not active:
        return f"sort by {spec.header}"
    if not descending:
        return f"sort by {spec.header}, the other way"
    return f"stop sorting by {spec.header} — back to live options first"


def block_id(placement_id: str, line_id: str) -> str:
    """The DOM id of one line-of-coverage block, in ONE place.

    Both ids are in it because one Program tab renders every placement on the
    account, and two placements sharing a line of coverage would otherwise
    share an id — a header save would land on whichever came first. It is a
    function rather than an f-string at each site because the ADD ROW builds
    its datalist ids off it too, from a route that re-renders that row alone
    (`routes/marketing._add_row`), and a second copy of the formula there is a
    second copy that can differ.
    """
    return f"mblock-{placement_id}-{line_id}"


def block_view(
    request: Any,
    block: marketing_report.ReportBlock,
    *,
    ref: str,
    placement_id: str,
    base: str,
    window: marketing_report.DateWindow | None = None,
    sorts: dict[str, tuple[str, bool]] | None = None,
) -> dict[str, Any]:
    """One line-of-coverage block, rendered.

    ONE BUILDER, two callers — the section's first render and a header cell
    save, which answers with THIS BLOCK because filling in the expiring rate
    moves the Rate Δ on every row beneath it. Same rule `_row_response` follows
    one level down and `_section_html` follows one level up: a key added to one
    caller's copy and not the other's goes missing on writes.
    """
    return {
        # The DOM id a header cell save retargets onto, and what every id
        # inside the block is scoped by.
        "id": block_id(placement_id, block.line_id),
        "line_id": block.line_id,
        "line_name": block.line_name,
        "line_abbr": block.line_abbr,
        # RETIRED, AND STILL HERE. The block renders with everything recorded
        # against it — that history does not become deniable because the
        # vocabulary row was soft-deleted — and the template says so where the
        # name prints and drops the add-a-market row. The nine header cells
        # stay editable: correcting a figure on a quote already in hand is
        # always right, and starting a NEW approach on a line the book has
        # retired is the only thing being withheld.
        "retired": block.line_retired,
        "groups": _header(request, block, ref=ref, placement_id=placement_id),
        "clearance": _clearance(block),
        # THE COLUMN HEADERS, each carrying what clicking it would do.
        "headers": header_cells(
            block, sorts or {}, ref=ref, placement_id=placement_id
        ),
        # ORDERED HERE, AFTER THE BLOCK WAS COMPOSED, and that ordering is
        # exactly what makes the BRIDGE stay honest. The bridge walks the
        # expiring premium to the LEADING quote (`_block` picks the first
        # bound-or-quoted row out of the composer's own ranking), and the
        # leading quote does not change because a reader asked to see the
        # column of expiry dates in order. Re-composing under the sort would
        # have made it follow whatever landed on top — a different carrier's
        # walk, printed under the same heading.
        "rows": [
            row_view(
                request, row, ref=ref, placement_id=placement_id, window=window
            )
            for row in marketing_report.order_rows(
                block.rows, *(sorts or {}).get(block.line_id, ("", False))
            )
        ],
        "bridge": _bridge(block.bridge),
        "add_url": f"{base}/lines/{block.line_id}/approaches",
    }


def line_add_options(
    conn: sqlite3.Connection, placement_id: str
) -> list[tuple[str, str]]:
    """The lines of coverage the add-a-line control OFFERS, in ONE place.

    Two callers and they must agree: the panel renders these as the picker's
    options, and the POST re-queries the same list so `checked_option` is
    authoritative rather than decorative (markup constrains a mouse). Two
    copies of this rule is a picker whose own refusal does not recognise what
    it just offered — which is what shipped.

    WHAT IS DROPPED IS A LINE THIS PLACEMENT ALREADY HAS A ROW FOR, and
    nothing else. It used to drop any line a RESPONSE named too, and that is
    the state with no way out: a block rendered because a market was
    approached, no `placement_line` row behind it, and the line was then in
    neither half of the control — not in the picker, and refused by name as
    "already a line of coverage in this book". The near-match card's own
    "use <line>" button posted an id this list did not contain and was
    answered with a refusal naming sixteen other lines (2026-08-25). Picking
    it now creates the row, which is exactly the recovery that was missing.
    """
    on_this = {
        row.line_id for row in marketing_repo.placement_lines(conn, placement_id)
    }
    return [
        (line.name, line.id)
        for line in lines_repo.all_lines(conn)
        if line.id not in on_this
    ]


def panel(
    request: Any,
    conn: sqlite3.Connection,
    placement_id: str,
    *,
    today: date,
    ref: str,
    error: str | None = None,
    pending: dict[str, Any] | None = None,
    refocus: str | None = None,
    line_values: dict[str, str] | None = None,
    line_add_preserve: bool = True,
    provisional_error: str | None = None,
    provisional_row: str | None = None,
    sort_spec: str = "",
) -> dict[str, Any]:
    """The whole Marketing section for one placement.

    `today` is a parameter, never the wall clock — the same rule the composer
    states about itself.

    `error` is a refusal from the add-a-line control, which has nowhere of its
    own to put one; `pending` is the near-match question that control asks
    before it creates a line the book has never carried. Both ride the section
    because the control does, and both are None on an ordinary render.

    `line_values` and `line_add_preserve` are the two halves of A CONTROL
    KEEPS WHAT WAS TYPED INTO IT, and they are the add-market row's rule
    reaching the control three inches above it (D5, 2026-08-26).
    `line_values` is what came back on a refusal, so the form re-renders
    holding it; `line_add_preserve` decides whether the markup carries
    `hx-preserve`, and the answer is the same one the add row gives — YES for
    every render that is somebody ELSE'S write (a Sent cell answers with this
    whole section, and it used to rebuild this form from its defaults and wipe
    a half-typed line name with no message), NO for the two answers the
    control's own save produces, because a refusal has to swap its message in
    and a saved line has to clear the form for the next one.
    """
    report = marketing_report.compose(
        conn, placement_id, today, audience=marketing_report.INTERNAL
    )
    base = f"/accounts/{ref}/program/{placement_id}/marketing"
    # RE-PARSED AND RE-FORMATTED, never echoed back as it arrived. What the
    # section publishes for every request inside it has to be what was
    # actually APPLIED: a spec naming a column this grid cannot order is
    # dropped by the parser, and passing the raw string through would leave
    # the page claiming an order it is not in — and carrying that claim into
    # every later write.
    sorts = parse_sorts(sort_spec)
    blocks = [
        block_view(
            request, block, ref=ref, placement_id=placement_id, base=base,
            window=report.window, sorts=sorts,
        )
        for block in report.blocks
    ]
    return {
        "id": f"marketing-{placement_id}",
        # THE MARKETING WITH NO LINE OF COVERAGE YET, in its own block below
        # the lines. None when every package on the placement has been answered
        # by line — which is what the assign control below each row is for.
        "provisional": provisional_view(
            conn, report, ref=ref, placement_id=placement_id,
            error=provisional_error, error_row=provisional_row,
        ),
        # The caret's way home when a write answers with the WHOLE section.
        # One submission carries every line of coverage, so correcting the date
        # it went out moves rows in blocks this one does not contain — and the
        # cell the caret was in no longer exists after that swap.
        "refocus": refocus,
        "columns": COLUMNS,
        # THE ORDER, ON THE SECTION, for every request made from inside it.
        # htmx inherits `hx-vals` down the tree, so one attribute here is what
        # keeps a sort alive through a cell save, an added market, a
        # "no charges" and — the one that mattered — a Sent cell, which
        # answers with this whole section and would otherwise rebuild every
        # block in the composer's default order.
        "sort": format_sorts(sorts),
        "blocks": blocks,
        "add_fields": market_approach_fields(conn),
        # A VISIBLE default, not a browser-chosen one. Both are values a
        # broker would otherwise retype on every approach and both are
        # correctable in front of them before Save — which is the whole test
        # the defaults rule applies. Neither is a figure off a document; those
        # (premium, rate, fees) are not on this form at all.
        "add_values": {"sent_on": today.isoformat(), "status": "pending"},
        "line_url": f"{base}/lines",
        "error": error,
        "pending": pending,
        "line_values": line_values or {},
        "line_add_preserve": line_add_preserve,
        # A line of coverage nobody has started marketing yet. Without this the
        # add-market row is unreachable on a fresh placement — the whole
        # feature would be built and not accessible, which is the bug class
        # this book already named once.
        #
        # `line_add_options` is the ONE definition of what this control
        # offers, shared with the POST that checks what came back — see its
        # docstring for what is dropped and, more importantly, what is not.
        "line_options": line_add_options(conn, placement_id),
    }
