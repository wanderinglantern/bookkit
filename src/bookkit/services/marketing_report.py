"""The marketing report: which markets we are approaching, what they said,
and at what rate — by LINE OF COVERAGE.

PURE COMPOSITION, like every other composer in this package: `compose` reads
and returns data, `to_sections` flattens it into rows, and this module never
touches the xlsx renderer. `today` is a parameter and never the wall clock.
Every query lives in repo/ — services own business rules, not SQL.

The two rules that decide most of this module are about what may NOT be
printed. A rate is comparable only within a rating basis, so a comparison
across two bases is refused and says why rather than showing a number. And a
total cost is blank unless every component is known, because a client
choosing between an admitted quote and an E&S one is choosing on the fees and
the surplus lines tax, and a total that treats an unquoted tax as zero
recommends the wrong one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from babel.dates import format_date

from ..models import (
    MARKET_RESPONSE_STATUS_LABELS,
    PUBLIC_DECLINE_REASON_LABELS,
    RATE_PER_LABELS,
    SUBMISSION_STATUS_LABELS,
    LineOfCoverage,
    MarketResponse,
    PlacementLine,
    Submission,
    rating_basis,
)
from ..money import RATE_SCALE, format_cents, format_rate_micros
from ..repo import lines as lines_repo
from ..repo import marketing, orgs, placements, submissions
from .export_open_items import SheetSection

CLIENT = "client"
INTERNAL = "internal"

# The order a client should read them in: what is live first, what is closed
# last. The eye lands on the top of a block and must find the options, not a
# declination that happens to sort first alphabetically.
# `declined_open_elsewhere` sorts ABOVE `declined` and below everything still
# being chased: the market is live on the line and dead on this band, and a
# reader scanning for who is left has to find it above the markets that walked.
_STATUS_ORDER = {
    "bound": 0,
    "quoted": 1,
    "indicated": 2,
    "pending": 3,
    "declined_open_elsewhere": 4,
    "declined": 5,
    # LAST BUT ONE, above `non_response` only. A market we ruled out on price
    # is the least useful row on the block to a reader choosing between
    # options — but it is still evidence of effort, and above a market that
    # never answered at all.
    "not_viable": 6,
    "non_response": 7,
}

# THE UNKNOWN, so a sort key can say "nobody has told us" and mean it. Every
# key below returns this rather than a number for a NULL, and `order_rows`
# parks those rows at the END in BOTH directions — see its docstring.
UNKNOWN = object()


def _known(value: Any) -> Any:
    return UNKNOWN if value is None else value


# WHAT A COLUMN OF THIS REPORT CAN BE ORDERED BY, and how — ONE HOME.
#
# Keyed by the GRID's column key (web/marketing_grid.COLUMNS), because that is
# what a reader clicks, and read from there rather than declared twice: a
# column is sortable exactly when it appears here, and `Column.sortable` says
# so by looking (tests/test_marketing_gates.py holds the two together).
#
# Every key reads a TYPED value off the row. That is the whole reason the sort
# is here and not in the browser: the cells print "$5,000,000" and "7 Jul",
# and ordering them client-side would mean parsing display strings back into
# figures — the one thing this codebase refuses everywhere (CLAUDE.md: a
# surface colours and labels off the KEY and never reverse-maps a label back).
#
# WHAT IS DELIBERATELY ABSENT:
#
# * `best` — A.M. Best ratings do not sort lexically. "A++" is the strongest
#   and sorts AFTER "A+" and "A" as text, so an alphabetical Best column would
#   put the weakest paper on top while looking like it had answered the
#   question. It needs a declared rank, and that is a piece of domain
#   vocabulary nobody has written down yet.
# * `rate_move`, `basis_override`, `exposure_override`, `rate_per` — each is a
#   composed STRING that is sometimes a figure and sometimes a refusal
#   ("basis not stated"), and an order over a mixed column is meaningless.
# * `reason` / `internal_reason` — prose.
SORT_KEYS: dict[str, Callable[[ReportRow], Any]] = {
    # The carrier, or the intermediary where the paper is not named yet —
    # which is exactly what the Market cell prints.
    "market": lambda r: (r.market or r.via or "").casefold(),
    # Direct approaches carry no intermediary and group together at one end.
    "access": lambda r: (r.via or "").casefold(),
    # NULL ATTACH IS NOT UNKNOWN. It reads as primary / the whole line, which
    # is the BOTTOM of a tower — a known position, and the ordinary one — so
    # it sorts as zero rather than being parked with the blanks.
    "attach": lambda r: r.attach if r.attach is not None else 0,
    "lim": lambda r: _known(r.lim),
    # THE SAME RANK THE DEFAULT ORDER USES, never alphabetical. Sorting a
    # status column A-Z puts "Bound" above "Quoted" by accident and
    # "Non-response" above "Pending" against every reading of the word; what a
    # broker means by sorting on status is live first, closed last.
    "status": lambda r: _STATUS_ORDER.get(r.status_key, 9),
    "sent_on": lambda r: _known(r.submitted_on),
    "responded_on": lambda r: _known(r.responded_on),
    "quote_expires_on": lambda r: _known(r.quote_expires_on),
    "rate": lambda r: _known(r.rate_micros),
    "premium": lambda r: _known(r.premium),
    "tria": lambda r: _known(r.tria),
    "fees": lambda r: _known(r.fees),
    "sl_tax": lambda r: _known(r.sl_tax),
    "total_cost": lambda r: _known(r.total_cost),
    # A COUNT, where zero is an answer. "No open subjectivities" is a fact
    # about a quote, not a gap, so it sorts as zero.
    "subjectivities": lambda r: r.open_subjectivities,
}


def _default_key(row: ReportRow) -> tuple[Any, ...]:
    """LIVE OPTIONS FIRST, then cheapest, then whoever answered first.

    The order a client should read a block in, and the order every block is
    composed in before any reader asks for another. It is a FUNCTION rather
    than a lambda inside `compose` because `order_rows` returns to it whenever
    a sort is cleared — the default is a real order with a reason, not the
    absence of one.
    """
    return (
        _STATUS_ORDER.get(row.status_key, 9),
        row.premium if row.premium is not None else 1 << 62,
        row.responded_on or "",
    )


def order_rows(
    rows: tuple[ReportRow, ...], column: str = "", descending: bool = False
) -> tuple[ReportRow, ...]:
    """One block's rows in the order a reader asked for, or the default.

    AN UNKNOWN FIGURE IS LAST IN BOTH DIRECTIONS. NULL is "nobody has told us"
    (models.MarketResponse.total_cost says it once for the whole book), so it
    is neither the smallest premium nor the largest — and a plain
    `reverse=True` would flip it from the bottom of the ascending sort to the
    TOP of the descending one, putting the rows carrying no answer above every
    quote in hand. The two lists are sorted and rejoined instead.

    THE SORT IS STABLE, so ties keep the order they arrived in — which is the
    default order, and therefore still live-first. Sorting by a column two
    markets agree on does not shuffle them.

    An unknown column name returns the default rather than raising: this is
    reachable from a URL, and a view parameter nobody recognises is not worth
    a 500 (the grid re-renders its own header from what it actually applied,
    so nothing then claims to be sorted when it is not).
    """
    key = SORT_KEYS.get(column)
    if key is None:
        return tuple(sorted(rows, key=_default_key))
    known = [r for r in rows if key(r) is not UNKNOWN]
    unknown = [r for r in rows if key(r) is UNKNOWN]
    known.sort(key=key, reverse=descending)
    return tuple(known + unknown)

# READ, not declared: models.py owns both vocabularies now, because the
# Program tab's status picker and decline-reason picker have to offer the very
# words this report prints (CLAUDE.md, DRY — one rule, one home).
_STATUS_LABEL = MARKET_RESPONSE_STATUS_LABELS
_PUBLIC_REASON_LABEL = PUBLIC_DECLINE_REASON_LABELS
# The PACKAGE's own vocabulary, which is not the response's — see the docstring
# on models.SUBMISSION_STATUS_LABELS. Read, never declared here.
_SUBMISSION_STATUS_LABEL = SUBMISSION_STATUS_LABELS

# WHAT THE PROVISIONAL BLOCK IS CALLED, in words, on the panel AND on the
# client's workbook — one string, because a client reading the sheet and a
# broker reading the screen must be told the same thing.
#
# A SUBMISSION WITH NO RESPONSE ROWS IS REAL MARKETING THAT HAPPENED. It went
# to a market on a day, it may carry a quote, and the one thing nobody has
# recorded is WHICH LINE OF COVERAGE the answer is about — which is not
# knowable from the data and must never be guessed. Fourteen placements on the
# seeded book were in exactly that state and the panel printed "No line of
# coverage on this placement is being marketed yet" over four live submissions,
# two of them quoted at $1.4M, while `/export/marketing.xlsx` downloaded a
# workbook with one header row and nothing under it (Grant, 2026-08-26).
#
# It is DELIBERATELY NOT PHRASED AS A LINE OF COVERAGE. There is no rating
# basis here, no expiring side, no bridge and no add-a-market row — not because
# they were left out but because not one of them is knowable, and a header full
# of "not set" beside a real quote invites somebody to fill figures in against a
# line nobody chose.
PROVISIONAL_LABEL = (
    "Line of coverage not recorded — these markets were approached on this "
    "placement and which line of coverage each answer covers has not been "
    "recorded"
)

_MICROS = RATE_SCALE

# THE WORDS A COMPARISON IS REFUSED IN, stated once. Every one of them is
# printed in the Rate Δ column where a percentage would have been, and
# `_exposure_move` deliberately borrows the first: it is the same refusal
# about the same two columns, and a reader who learns what it means in one
# place has learned it everywhere on this report. They are constants because
# the sheet's column has to be WIDE enough for them (see `_CLIENT_COLUMNS`),
# and a width measured against a literal somewhere else is the copy that
# quietly stops matching.
BASIS_CHANGED = "basis changed"
DENOMINATOR_CHANGED = "denominator changed"
# SILENCE IS NOT AGREEMENT. "changed" is what a comparison says when it can SEE
# two axes disagree; these two are what it says when one side never stated the
# axis at all, which is the failure that is invisible without them — the
# comparison reads the gap as "the same as the other side" and divides. A rate
# typed while the line carried no denominator, the line then given one, is the
# ordinary two-click way to reach it (D4, 2026-08-26), and the block header a
# client reads then states a denominator the quote was never stated against.
# Separate words from "changed" because they are a different fact and a
# different fix: one is a correction, the other is a figure nobody has recorded.
BASIS_UNKNOWN = "basis not stated"
DENOMINATOR_UNKNOWN = "denominator not stated"
NO_EXPIRING_RATE = "no expiring rate recorded"
REFUSAL_NOTES: tuple[str, ...] = (
    BASIS_CHANGED,
    DENOMINATOR_CHANGED,
    BASIS_UNKNOWN,
    DENOMINATOR_UNKNOWN,
    NO_EXPIRING_RATE,
)


@dataclass(frozen=True)
class DateWindow:
    """The span a date recorded against this placement can honestly fall in.

    Marketing runs AHEAD of inception — a submission goes out weeks or months
    before cover starts — so the window is not the policy period: it opens a
    year before the period does and closes when cover does. Inside it a date
    is ordinary and prints short; outside it, it prints its year (fmt_date).
    """

    start: str
    end: str

    def holds(self, iso: str) -> bool:
        return self.start <= iso <= self.end


def window_for(period_from: str, period_to: str) -> DateWindow:
    """The window for one placement. A year of lead-in, in days rather than
    calendar years so a 29 February start needs no special case."""
    start = date.fromisoformat(str(period_from)) - timedelta(days=365)
    return DateWindow(start.isoformat(), str(period_to))


def fmt_date(iso: str | None, window: DateWindow | None = None) -> str:
    """A DATE PRINTS ITS YEAR UNLESS IT IS ORDINARY.

    "12 Aug" was every date this report printed, on the grid and in the client
    workbook alike, so 2001, 2026 and 2099 rendered identically and a mistyped
    year was invisible on the one surface that accepts it (found 2026-08-25).
    Printing the year on every date would make the wrong one no louder than
    the right one, so the year is printed exactly where it is news: a date
    outside the placement's own marketing window, or one on a report that
    could not say what that window is.
    """
    if not iso:
        return ""
    when = date.fromisoformat(iso)
    if window is not None and window.holds(iso):
        return format_date(when, format="d MMM", locale="en_US")
    return format_date(when, format="d MMM y", locale="en_US")


def fmt_rate(rate_micros: int | None) -> str:
    """A rate is not money: two decimals, no currency symbol, no thousands
    separator that would make 1,010.05 look like a premium."""
    if rate_micros is None:
        return ""
    return format_rate_micros(rate_micros)


def fmt_exposure(amount: int | None, basis_key: str | None) -> str:
    """Cents when the basis is monetary, a whole count when it is not — the
    one decision models.RatingBasis.monetary exists to make, read here rather
    than judged.

    AND NEITHER WHEN NOBODY HAS SAID. With no basis this defaulted to
    `format_cents` — a guess, one branch away from the failure that shipped
    beside it: 350 power units printed to a client as "$3.50". Nothing inside
    the integer says which it is, so the fallback states the digits and states
    that the unit is unknown, rather than dressing them as either one. No
    surface can currently store an exposure with no basis (the cell refuses
    while the basis is unknown, and `_basis_guard` refuses clearing one out
    from under a stored figure), so this costs nothing today and is here for
    the next surface that composes one.
    """
    if amount is None:
        return ""
    if basis_key is None:
        return f"{amount:,} (basis not set)"
    basis = rating_basis(basis_key)
    if basis.monetary:
        return format_cents(amount)
    unit = f" {basis.unit_label}" if basis.unit_label else ""
    return f"{amount:,}{unit}"


def _per_label(rate_per: int) -> str:
    """How a denominator READS, out of models.RATE_PER_LABELS — the same list
    the picker offers from and the block header prints.

    It was `format_cents(rate_per * 100)` at both sites, which is a second
    copy of that vocabulary and gets `rate_per = 1` wrong in two different
    ways: the header dropped the clause entirely (`> 1`) and a row would have
    printed "per $1.00" for a rate quoted per UNIT.
    """
    return RATE_PER_LABELS.get(rate_per, str(rate_per))


def _rate_per_override(
    response: MarketResponse, expectation: PlacementLine | None
) -> str:
    """What this row's rate is stated per, when the block heading does not
    already say it.

    Blank in the ordinary case: the heading states the line's denominator and
    every rate on it inherited that denominator on the write, so repeating it
    on each row is noise. It speaks in exactly the two states where the
    heading is not the answer — the row carries its own denominator and it is
    a different one, and the row carries a rate with no denominator at all
    (which `repo.marketing._stamp_rate_per` now refuses to create, and which
    rows written before it could not).
    """
    if response.rate_micros is None:
        return ""
    line_per = expectation.rate_per if expectation is not None else None
    if response.rate_per is None:
        # The words `Move` uses one column over, so the two cells agree about
        # what happened rather than each inventing a phrasing.
        return DENOMINATOR_UNKNOWN
    if response.rate_per == line_per:
        return ""
    return f"per {_per_label(response.rate_per)}"


def _premium_from(
    rate_micros: int, exposure: int, rate_per: int, *, monetary: bool
) -> int:
    """Cents, from a rate and an exposure. Used ONLY for the bridge, never to
    replace a stated premium: carriers round, apply minimum premiums and
    expense constants, and a computed figure will disagree with the one on
    their quote letter.

    `monetary` IS NOT OPTIONAL, because the integer it multiplies means two
    different things and nothing inside it says which — the same fact
    `fmt_exposure` exists to read and `_basis_guard` exists to protect. On a
    monetary basis the exposure is CENTS and `rate_per` is DOLLARS, so the
    hundred cancels: 10.00 per $100 over $41,000,000 is $410,000 and the
    arithmetic lands in cents on its own. On a COUNT basis the exposure is a
    number of things, so rate x count is DOLLARS and has to be taken up to
    cents — without that the bridge came out a hundredfold LOW (320 power
    units at 1287.50 per unit read as $4,120 for $412,000), `_reconciles`
    dropped every walk, and the premium bridge was silently dead on every
    non-monetary basis in the book (D7, found 2026-08-26). Failing safe is not
    the same as working.
    """
    cents = rate_micros * exposure / (_MICROS * rate_per)
    return round(cents if monetary else cents * 100)


@dataclass(frozen=True)
class Move:
    """A change against expiring — the percentage, or the reason there isn't
    one, in words.

    ONE SHAPE FOR EVERY COMPARISON ON THIS REPORT (rate, exposure), because
    every one of them can be refused for the same reason: two figures rated on
    different bases are not comparable, and the honest cell says so instead of
    printing a number. It was `RateMove` and the rate was the only comparison
    that carried a note — which is exactly how the exposure comparison came to
    print -100.0% across two bases (2026-08-25).
    """

    pct: float | None
    note: str = ""

    @property
    def cell(self) -> str:
        if self.pct is None:
            return self.note
        return f"{self.pct:+.1f}%"


@dataclass(frozen=True)
class Bridge:
    """Why the premium moved: the part that is rate and the part that is the
    client's own growth. Reconciles — expiring + rate + exposure = quoted —
    which is the whole reason it is worth printing, and `_reconciles` checks
    it before this is handed out rather than trusting this sentence."""

    expiring_premium: int
    rate_effect: int
    exposure_effect: int
    quoted_premium: int
    market: str


@dataclass(frozen=True)
class ReportRow:
    # THE ROW'S OWN ID, so a surface that renders this report can also WRITE
    # to it. The Program tab edits every figure below where it sits and needs
    # a `market_response` id to post to; without it the panel would have to
    # re-query the responses and pair them back up by name and layer, which is
    # a second composition of the same rows and would differ the first time two
    # markets shared a name (Grant, 2026-08-25).
    response_id: str
    line_id: str
    market: str
    via: str | None
    best: str
    layer: str
    # THE TWO FIGURES `layer` IS BUILT FROM. The sheet prints the sentence
    # ("$5M xs $5M") because a client reads it; the Program tab EDITS the band
    # a market answered on, and nobody can type a sentence — so both halves
    # travel beside it. NULL attach is primary / the whole line.
    attach: int | None
    lim: int | None
    status: str
    # The RAW status beside the label. The label is what a person reads; the
    # key is what a surface keys its colour off, and a panel that reverse-maps
    # "Non-response" back to a key is a second copy of _STATUS_LABEL that will
    # differ the first time a word is reworded.
    status_key: str
    responded_on: str | None
    # WHEN THESE TERMS DIE. Composed, and on the panel it is a CELL: a quote
    # recorded on the Marketing panel could not reach `services.quotes` at all
    # until this column existed, because the queue is keyed on a date the
    # panel had nowhere to put — the money-losing half of the second-home
    # defect (Grant, 2026-08-26). NULL is "nobody has asked the underwriter
    # yet", which is its own piece of work, never "no expiry".
    quote_expires_on: str | None
    submitted_on: str
    rate_micros: int | None
    rate_move: Move
    premium: int | None
    tria: int | None
    # FEES AND TAX AS THEMSELVES, not only inside the total. The sheet prints
    # the total because a reader only reads it; the Program tab's grid EDITS
    # these where they sit, and you cannot type a total (Grant, 2026-08-25) —
    # `total_cost` is derived from all four and stays blank while any is
    # unknown. NULL is "nobody has told us", 0 is "we asked and there is
    # none": both must be able to reach a cell, so neither is defaulted here.
    fees: int | None
    sl_tax: int | None
    total_cost: int | None
    open_subjectivities: int
    public_reason: str
    basis_override: str
    exposure_override: str
    # THE DENOMINATOR THIS ROW'S RATE WAS TYPED AGAINST, where it is not the
    # one the block heading states — the `basis_override` rule on the other
    # axis, and the column D3 was missing. The heading said "per $1,000" while
    # a stored response carried `rate_per = 100`, and the Rate cell printed
    # "9.60" bare underneath it: ten times out on the CLIENT's workbook, with
    # "denominator changed" in the next column the only hint and it never said
    # WHICH. Empty when the row agrees with its block, exactly as
    # `basis_override` is — a column that repeats the heading on every row is
    # the duplication the DRY rule names.
    rate_per_override: str
    # internal only, never composed into a client sheet
    internal_reason: str = ""
    commission_bps: int | None = None
    notes: str = ""
    clearance: str = ""

    @property
    def market_cell(self) -> str:
        if self.market and self.via:
            return f"{self.market} (via {self.via})"
        if self.via:
            return f"{self.via} — carrier TBD"
        if self.market:
            return self.market
        # NEVER BLANK. This is the column that says WHOSE answer the row is,
        # and a row of figures beside an empty first cell reads as a rendering
        # fault — the same reason the block header prints "not set" in words
        # rather than leaving a gap. A response cannot be written without a
        # carrier or an intermediary (the DB CHECK and repo.create_response
        # both hold it), so reaching here means the org behind it is not in
        # the org table at all. Say that, rather than nothing.
        return "market not on file"


@dataclass(frozen=True)
class ReportBlock:
    # The vocabulary id, beside the name. A surface that lets a market be
    # ADDED to this block has to name the line of coverage to the write, and
    # resolving the printed name back to a row is a lookup that can miss.
    line_id: str
    line_name: str
    line_abbr: str | None
    # THE LINE OF COVERAGE IS RETIRED, and the block is here anyway. A
    # soft-deleted vocabulary row is not a licence to delete a client's
    # marketing history from the surface that reports it — the responses and
    # the expectations are all still in SQLite, and the block was being
    # dropped in silence. The PANEL says so beside the name and stops offering
    # to add a market to it: you may correct what a market already said, and
    # may not start marketing a line the book no longer carries.
    #
    # THE CLIENT WORKBOOK DELIBERATELY DOES NOT SAY IT. "Property (retired)"
    # on a sheet a client reads is a sentence about their COVER, which would
    # be a lie — the retirement is a fact about this book's own vocabulary and
    # is the broker's business. What the client sheet needed was the rows,
    # which is what it lost.
    line_retired: bool
    # BOTH the label and the KEY. The label is what a person reads; the key is
    # what decides whether the exposure beside it is cents or a count, and a
    # block that carried only the label had nothing to pass to fmt_exposure —
    # so 350 power units printed to a client as "$3.50" (found 2026-08-25 by
    # an adversarial check, on the client sheet AND the client xlsx).
    basis_key: str | None
    basis_label: str
    rate_per: int | None
    exposure: int | None
    # THE COMPARISON OR THE REASON THERE ISN'T ONE, never a bare float. It was
    # `exposure_change_pct: float | None`, and a bare float has nowhere to put
    # "these two figures are not comparable" — so it printed a percentage
    # across two rating bases (see `_exposure_move`).
    exposure_move: Move
    expiring_premium: int | None
    expiring_rate_micros: int | None
    # THE EXPIRING SIDE'S OWN TWO FACTS, beside the current side's. Both are
    # on the block because the Program tab EDITS this header where it prints
    # it, and a surface that had only the current basis could not tell whether
    # the expiring exposure beside it was cents or a count — the same
    # hundredfold mistake `basis_key` was added to stop, one column over. The
    # sheet does not print either, and does not have to: a block carrying a
    # fact no renderer uses costs nothing, while a renderer re-querying a fact
    # the composer already read is the second read that drifts.
    expiring_exposure: int | None
    expiring_basis: str | None
    limit_sought: int | None
    attach_sought: int | None
    bridge: Bridge | None
    rows: tuple[ReportRow, ...]

    @property
    def is_empty(self) -> bool:
        return not self.rows


@dataclass(frozen=True)
class ProvisionalRow:
    """ONE SUBMISSION NOBODY HAS ASSIGNED A LINE OF COVERAGE TO.

    A SEPARATE SHAPE FROM `ReportRow`, and that is the design rather than an
    accident of typing. A ReportRow carries a `line_id`, a rate, a rating
    basis, a comparison against expiring and a `response_id` every cell on the
    panel posts to; not one of those exists here, and a row that carried them
    as empty strings would render as a line of coverage with everything
    missing — which is the reading this block must never invite. What cannot
    be said is absent from the type, so no renderer can print it.

    The figures are the SUBMISSION's own columns. They are a cache of the
    response rows everywhere else (`repo.marketing.roll_up_submission`), and
    when there are no rows the cache is the only record there is — which is
    exactly why that function refuses to blank them.

    `decline_reason` is INTERNAL. `submission.decline_reason` is free text with
    no public counterpart, unlike `market_response`, which carries the
    broker's private note and the client-safe wording in two separate fields
    for the reason models.PUBLIC_DECLINE_REASONS gives: a single field guarded
    by nothing at all cannot be shown to a client. So it prints in the
    internal sheet's own Decline reason column and the client workbook's
    Reason column stays empty on these rows.
    """

    submission_id: str
    market: str
    best: str
    # The PACKAGE's status vocabulary, not the response's (models
    # .SUBMISSION_STATUS_LABELS says why they are two). `status_key` is what a
    # surface colours off; the label is what a person reads.
    status: str
    status_key: str
    submitted_on: str
    responded_on: str | None
    quote_expires_on: str | None
    premium: int | None
    # THE LIMIT QUOTED, and NOT a layer. `_layer_label` would print
    # "$5,000,000 primary" from a lim with no attach, and primary is a claim
    # about where in a tower this sits that nobody has made about this
    # package. The figure alone states the limit and asserts nothing else.
    lim: int | None
    open_subjectivities: int
    # internal only, never composed into a client sheet
    internal_reason: str = ""


@dataclass(frozen=True)
class MarketingReport:
    account: str
    program: str
    period: str
    as_of: str
    audience: str
    blocks: tuple[ReportBlock, ...]
    # THE SPAN THE DATES ON THIS REPORT BELONG TO, so every renderer of these
    # rows decides one way whether a date needs its year — the sheet, the
    # Program tab's grid and the cell a broker opens to correct one. A window
    # rebuilt at each renderer is the second copy that differs, and the way it
    # differs is a year silently dropped off a client-facing date.
    window: DateWindow
    # THE MARKETING THAT HAS NO LINE OF COVERAGE YET. Its own field rather than
    # a thirteenth `ReportBlock`, so that every renderer of blocks — the sheet,
    # the panel, the header cells, the bridge — carries on describing lines of
    # coverage and nothing else, and reaching these rows is a deliberate act.
    # Empty on a placement whose every submission has been answered by line,
    # which is the state the whole feature is trying to reach.
    provisional: tuple[ProvisionalRow, ...] = ()


# --- composition -----------------------------------------------------------


def _rate_move(response: MarketResponse, line: PlacementLine | None) -> Move:
    """Rate change against expiring — or a sentence saying why there is none.

    NEVER assumes exposure was flat. An unlabelled flat-exposure assumption
    puts a figure in front of a client that looks like rate change and is
    only premium change wearing a rate's clothes; it is indistinguishable
    from a real one on the page (Grant, 2026-08-25)."""
    if response.rate_micros is None:
        return Move(None)
    if line is None or line.expiring_rate_micros is None:
        return Move(None, NO_EXPIRING_RATE)
    quoted_basis = response.rating_basis or (line.rating_basis if line else None)
    # AN AXIS NOBODY STATED IS NOT AN AXIS THAT AGREES. Both halves used to be
    # guarded by `and`, so a missing basis on either side skipped the check
    # entirely and the two rates were divided as though they had been measured
    # on the same thing. An expiring rate can be recorded with no expiring
    # basis in two clicks (the header cells are independent), which is how this
    # reaches a client's workbook.
    if line.expiring_basis is None or quoted_basis is None:
        return Move(None, BASIS_UNKNOWN)
    if line.expiring_basis != quoted_basis:
        return Move(None, BASIS_CHANGED)
    # A RATE COMPARES ONLY WITHIN ONE BASIS **AND** ONE DENOMINATOR. The basis
    # says what is being measured; `rate_per` says how much of it one unit of
    # rate buys, and 1.42 per $100 is ten times 1.42 per $1,000. This checked
    # the basis and not the denominator, and the door repo's `_rate_per_guard`
    # deliberately leaves open — clear the expiring rate, move the picker,
    # re-enter — walked a stored response rate of 11.88 straight across it:
    # "+18.2%" became "-88.2%" on the CLIENT's workbook, an 88% reduction
    # nobody achieved (found 2026-08-26). The response's stamped `rate_per` is
    # the denominator its rate was TYPED against (repo.marketing stamps it on
    # the write); where it is absent the rate simply inherits the line's, and
    # the two agree by definition.
    # `response.rate_per or line.rate_per` READ SILENCE AS AGREEMENT: an
    # unstamped rate inherited the line's denominator and then compared equal
    # to it by construction, so the one state this check exists for — a rate
    # typed before the line had a denominator — walked straight through it.
    # The response's own stamp is the ONLY thing that says what its rate was
    # typed against (repo.marketing._stamp_rate_per writes it, and now refuses
    # a rate it cannot stamp), so its absence is a fact and not a default.
    if response.rate_per is None or line.rate_per is None:
        return Move(None, DENOMINATOR_UNKNOWN)
    if response.rate_per != line.rate_per:
        # The same shape as "basis changed", one word apart: it is the same
        # refusal about the same column, and a reader who has met one has met
        # both.
        return Move(None, DENOMINATOR_CHANGED)
    if not line.expiring_rate_micros:
        return Move(None, NO_EXPIRING_RATE)
    pct = (response.rate_micros / line.expiring_rate_micros - 1) * 100
    return Move(pct)


def _exposure_move(expectation: PlacementLine | None) -> Move:
    """Exposure against expiring — or a sentence saying why there is none.

    THE THIRD COMPARISON ACROSS TWO BASES, and the one that shipped without
    the guard the other two carry. `_rate_move` refuses in words and `_bridge`
    returns None, and this one divided 350 power units by 4,100,000,000 cents
    of gross sales and printed "exposure down 100%" on the CLIENT's own
    workbook — every digit of it real, and the sentence a lie (found
    2026-08-25). A line rated on sales last term and marketed on power units
    this term is the ordinary way to reach it, two pickers apart.

    An absent figure is not a refusal and carries no words: the two exposures
    each have a cell of their own beside this, and "not set" there is already
    the message.
    """
    if expectation is None or expectation.expected_exposure is None:
        return Move(None)
    if not expectation.expiring_exposure:
        return Move(None)
    if expectation.expiring_basis is None or expectation.rating_basis is None:
        # The same reading `_rate_move` makes one function up: two figures
        # nobody has said the units of are not two figures known to share
        # them. No surface can store an exposure with no basis today (the cell
        # refuses and `_basis_guard` refuses clearing one), so this costs
        # nothing now and is the door the next surface would come through —
        # the same reason `fmt_exposure` carries its "basis not set" branch.
        return Move(None, BASIS_UNKNOWN)
    if expectation.expiring_basis != expectation.rating_basis:
        # The SAME WORDS `_rate_move` uses, because it is the same refusal
        # about the same two columns — a reader who learns what it means in
        # one place has learned it everywhere on this report.
        return Move(None, BASIS_CHANGED)
    pct = (expectation.expected_exposure / expectation.expiring_exposure - 1) * 100
    return Move(pct)


# One percent of the premium being explained, and never less than a dollar.
# THE BRIDGE DECOMPOSES ROUNDED INPUTS — the expiring rate is the figure a
# broker wrote down (412,000 / 41,000 = 10.0488, entered as 10.05), so the
# walk can never be required to land to the cent and a hard equality would
# drop the bridge on ordinary business. What this catches is the other
# magnitude entirely: a rate read against the wrong denominator or the wrong
# basis moves an effect by a factor of ten, which is $9,000 adrift on a
# $110,000 quote.
_BRIDGE_SLACK_BPS = 100


def _reconciles(bridge: Bridge) -> bool:
    """Does the walk actually add up to the quote it sits under?

    NOTHING CHECKED THIS. `Bridge`'s own docstring promised it, and four lines
    that do not add up are worse than no bridge at all: the client reads them
    as an explanation of the number above them.
    """
    walked = bridge.expiring_premium + bridge.rate_effect + bridge.exposure_effect
    slack = max(100, abs(bridge.quoted_premium) * _BRIDGE_SLACK_BPS // 10_000)
    return abs(walked - bridge.quoted_premium) <= slack


def _bridge(response: MarketResponse, line: PlacementLine | None, market: str) -> Bridge | None:
    """Split the premium change into the part that is rate and the part that
    is the client's own growth. Needs both sides of both facts; returns None
    rather than half a story."""
    if line is None or response.rate_micros is None or response.premium is None:
        return None
    # THE RESPONSE'S OWN STAMP, never the line's read in as a default — the
    # same correction `_rate_move` carries, and for the same reason: falling
    # back made an unstamped rate equal to the line's denominator by
    # construction, so the check below could not fail.
    rate_per = response.rate_per
    exposure = response.exposure_amount or line.expected_exposure
    if not rate_per or exposure is None:
        return None
    if line.expiring_premium is None or line.expiring_exposure is None:
        return None
    if line.expiring_rate_micros is None:
        return None
    # BOTH AXES KNOWN, NOT MERELY NOT-CONTRADICTORY. `and`-guarded equality
    # read an unstated basis as agreement and walked a bridge across two
    # figures nobody had said the units of.
    if line.expiring_basis is None or line.rating_basis is None:
        return None
    if line.expiring_basis != line.rating_basis:
        return None
    # AND ONE DENOMINATOR — `rate_effect` SUBTRACTS the expiring rate from the
    # quoted one, which is arithmetic only while both are stated per the same
    # unit. `_reconciles` catches most of the damage after the fact; refusing
    # here says why, and does not rely on the walk happening to miss by more
    # than a percent.
    if line.rate_per is None or rate_per != line.rate_per:
        return None
    # WHICH KIND OF INTEGER THE EXPOSURE IS. Both bases are equal by the check
    # above, so one lookup answers for both sides of the walk.
    monetary = rating_basis(line.rating_basis).monetary
    rate_effect = _premium_from(
        response.rate_micros - line.expiring_rate_micros,
        line.expiring_exposure,
        rate_per,
        monetary=monetary,
    )
    exposure_effect = _premium_from(
        response.rate_micros,
        exposure - line.expiring_exposure,
        rate_per,
        monetary=monetary,
    )
    bridge = Bridge(
        expiring_premium=line.expiring_premium,
        rate_effect=rate_effect,
        exposure_effect=exposure_effect,
        quoted_premium=response.premium,
        market=market,
    )
    # DROPPED RATHER THAN PRINTED WHEN IT DOES NOT ADD UP. A denominator or a
    # basis that has been relabelled under a stored rate, or a premium typed
    # with a digit missing, all land here — and the honest answer is the same
    # one this function gives for a half-recorded expiring side: nothing.
    return bridge if _reconciles(bridge) else None


def _layer_label(response: MarketResponse) -> str:
    """NULL attach reads as primary / the whole line, which is the ordinary
    case and the one a client should not have to decode."""
    if response.attach is None and response.lim is None:
        return "Primary"
    if response.attach is None:
        return f"{format_cents(response.lim or 0)} primary"
    return f"{format_cents(response.lim or 0)} xs {format_cents(response.attach)}"


def compose(
    conn: sqlite3.Connection,
    placement_id: str,
    today: date,
    audience: str = CLIENT,
) -> MarketingReport:
    """One block per line of coverage, markets beneath, live options first."""
    placement = placements.get(conn, placement_id)
    subjectivities = submissions.open_subjectivity_counts(conn, placement_id)
    submitted = submissions.sent_dates_for_placement(conn, placement_id)
    expectations = {
        pl.line_id: pl for pl in marketing.placement_lines(conn, placement_id)
    }
    vocabulary = {line.id: line for line in lines_repo.all_lines(conn)}

    responses = marketing.responses_for_placement(conn, placement_id)
    # EVERY SUBMISSION ON THE PLACEMENT, not only the answered ones. The ones
    # with no response row are what `_provisional` is built from, and reading
    # them here rather than in a second pass keeps the org lookup below a
    # SINGLE bulk read over every name this report prints.
    packages = submissions.for_placement(conn, placement_id)
    answered = {r.submission_id for r in responses}
    # One bulk read each, not a query per printed cell.
    org_ids = {placement.org_id}
    for response in responses:
        org_ids |= {response.market_org_id or "", response.via_org_id or ""}
    for package in packages:
        org_ids.add(package.market_org_id or "")
    org_ids.discard("")
    # NAMED DEAD OR ALIVE (`names_for_any`). These rows are a record of what
    # happened: a carrier deleted from the book after it quoted is still the
    # carrier that quoted, and reading through the living lookup left the
    # Market column of that row empty on the panel and in the workbook.
    names = orgs.names_for_any(conn, org_ids)
    best = orgs.best_ratings_for(conn, org_ids)

    by_line: dict[str, list[MarketResponse]] = {}
    for response in responses:
        by_line.setdefault(response.line_id, []).append(response)

    blocks: list[ReportBlock] = []
    # Every line the placement EXPECTS, plus any a response named anyway — a
    # line marketed without an expectation row is still a line being marketed.
    for line_id in list(expectations) + [k for k in by_line if k not in expectations]:
        # RETIRED IS NOT GONE. `all_lines` is the LIVING vocabulary — the right
        # answer for a picker and the wrong one here, because retiring a line
        # (or merging it away) does not touch the responses or the expectations
        # recorded against it. Read through it and the whole block disappeared
        # from the Program tab and from the client's workbook, bound quote and
        # all, with the panel then saying nothing was being marketed
        # (2026-08-25). The block is composed off the row as it IS and carries
        # `line_retired` so every renderer can say so.
        line = vocabulary.get(line_id) or lines_repo.get_any(conn, line_id)
        if line is None:
            # No row at all, alive or dead: nothing to name the block with, and
            # a foreign key says this cannot happen.
            continue
        expectation = expectations.get(line_id)
        # BUILT FIRST, ORDERED SECOND. The sort used to run over the raw
        # `MarketResponse` rows, which cannot reach the figures a reader
        # actually sorts by — the total, the open-subjectivity count, the
        # carrier's NAME — because those are composed one line down. Ordering
        # the composed rows through `order_rows` puts every column within
        # reach of one function, and that function is also what a surface
        # calls when a reader asks for a different order (CLAUDE.md, DRY: one
        # rule, one home).
        rows = order_rows(
            tuple(
                _row(
                    response,
                    expectation,
                    names=names,
                    best=best,
                    subjectivities=subjectivities,
                    submitted=submitted,
                    audience=audience,
                    conn=conn,
                )
                for response in by_line.get(line_id, [])
            )
        )
        blocks.append(_block(line, expectation, rows, by_line.get(line_id, [])))

    return MarketingReport(
        account=names.get(placement.org_id, ""),
        program=placement.program_name,
        period=f"{placement.period_from} to {placement.period_to}",
        as_of=today.isoformat(),
        audience=audience,
        blocks=tuple(blocks),
        window=window_for(placement.period_from, placement.period_to),
        provisional=_provisional(
            [p for p in packages if p.id not in answered],
            names=names,
            best=best,
            subjectivities=subjectivities,
            audience=audience,
        ),
    )


def _provisional(
    packages: list[Submission],
    *,
    names: dict[str, str],
    best: dict[str, str],
    subjectivities: dict[str, int],
    audience: str,
) -> tuple[ProvisionalRow, ...]:
    """The submissions on this placement that no response row speaks for.

    THE GATE IS THE ROW SET, THE SAME ONE `roll_up_submission` USES. Once a
    submission has one response the rows are the authority for all six of its
    quote columns and its markets are reported under the lines they answered;
    while it has none, these columns are the only record of that marketing
    there is, and this block is the only place they are shown. Two functions,
    one gate — a package cannot be counted twice or fall between them.

    SORTED OLDEST FIRST, by the day the package went out. The line blocks sort
    by status because a client is choosing between live options; there is
    nothing to choose between here, and what a broker needs is the order the
    approaches happened in so the one that has been waiting longest is at the
    top. The id breaks a tie so the order cannot depend on SQL.
    """
    internal = audience == INTERNAL
    return tuple(
        ProvisionalRow(
            submission_id=package.id,
            # A submission's market is NOT NULL, so a blank here means the org
            # is not in the org table at all — `market_cell` says the same
            # thing one class up, and for the same reason: a row of figures
            # beside an empty first cell reads as a rendering fault.
            market=names.get(package.market_org_id or "", "") or "market not on file",
            best=best.get(package.market_org_id or "", ""),
            status=_SUBMISSION_STATUS_LABEL.get(
                str(package.status), str(package.status)
            ),
            status_key=str(package.status),
            submitted_on=str(package.sent_on),
            responded_on=package.response_on,
            quote_expires_on=package.quote_expires_on,
            premium=package.quoted_premium,
            lim=package.quoted_limit,
            open_subjectivities=subjectivities.get(package.id, 0),
            internal_reason=(package.decline_reason or "") if internal else "",
        )
        for package in sorted(packages, key=lambda p: (str(p.sent_on), p.id))
    )


def _row(
    response: MarketResponse,
    expectation: PlacementLine | None,
    *,
    names: dict[str, str],
    best: dict[str, str],
    subjectivities: dict[str, int],
    submitted: dict[str, str],
    audience: str,
    conn: sqlite3.Connection,
) -> ReportRow:
    market = names.get(response.market_org_id or "", "")
    via = names.get(response.via_org_id or "") if response.via_org_id else None
    # The line's basis unless this market stated its own — the same fallback
    # _rate_move makes. Overriding the exposure without overriding the basis is
    # the ORDINARY case (a carrier using its own audit figure on the same
    # unit), and reading the basis as None there rendered a count as money.
    line_basis = expectation.rating_basis if expectation else None
    basis_key = response.rating_basis or line_basis
    internal = audience == INTERNAL
    clearance = ""
    if internal:
        conflicts = marketing.clearance_conflicts(conn, response)
        if conflicts:
            others = ", ".join(
                names.get(c.via_org_id or "", "direct") for c in conflicts
            )
            clearance = f"also reached via {others}"
    return ReportRow(
        response_id=response.id,
        line_id=response.line_id,
        market=market,
        via=via,
        best=best.get(response.market_org_id or "", ""),
        layer=_layer_label(response),
        attach=response.attach,
        lim=response.lim,
        status=_STATUS_LABEL.get(response.status, response.status),
        status_key=response.status,
        responded_on=response.responded_on,
        quote_expires_on=response.quote_expires_on,
        submitted_on=submitted.get(response.submission_id, ""),
        rate_micros=response.rate_micros,
        rate_move=_rate_move(response, expectation),
        premium=response.premium,
        tria=response.tria_premium,
        fees=response.policy_fees,
        sl_tax=response.surplus_lines_tax,
        total_cost=response.total_cost,
        open_subjectivities=subjectivities.get(response.submission_id, 0),
        public_reason=_PUBLIC_REASON_LABEL.get(response.decline_reason_public or "", ""),
        basis_override=(
            rating_basis(response.rating_basis).label if response.rating_basis else ""
        ),
        exposure_override=fmt_exposure(response.exposure_amount, basis_key),
        rate_per_override=_rate_per_override(response, expectation),
        internal_reason=(response.decline_reason or "") if internal else "",
        commission_bps=response.commission_bps if internal else None,
        notes=(response.notes or "") if internal else "",
        clearance=clearance,
    )


def _block(
    line: LineOfCoverage,
    expectation: PlacementLine | None,
    rows: tuple[ReportRow, ...],
    responses: list[MarketResponse],
) -> ReportBlock:
    # NO SEND DATE ON THE BLOCK. It used to collapse to a header fact when
    # every package on the line agreed and to nothing at all when they did
    # not, which is a fact of the DATA masquerading as a fact of the block —
    # and on a line marketed over two days it printed nowhere. It is a column
    # on the row now, on both the sheet and the grid (`Sent`).
    basis_key = expectation.rating_basis if expectation else None
    exposure = expectation.expected_exposure if expectation else None
    # A ROW IS IDENTIFIED BY ITS ID, never re-found by matching two of its
    # values. `leading` comes out of `rows` (sorted by status, premium, reply
    # date) and `source` has to be the SAME response out of `responses` (SQL
    # order: attach, id) — and the pair was re-found by `layer == and premium
    # ==`, which two markets quoting the same band at the same figure both
    # satisfy. The bridge then printed one carrier's name over the other
    # carrier's rate, on the CLIENT's workbook, and `_reconciles` could not
    # catch it because the walk it checked was internally consistent and
    # simply belonged to somebody else (found 2026-08-26). `response_id` is on
    # the row for exactly this.
    by_id = {r.id: r for r in responses}
    # THE KEY, NOT THE LABEL. `status` is words a person reads and
    # models.py owns them; matching on "Quoted" makes this a second copy of
    # that vocabulary, which stops finding anything the day a label is
    # reworded — and the failure is a bridge silently missing from the sheet.
    leading = next((r for r in rows if r.status_key in ("quoted", "bound")), None)
    bridge = None
    if leading is not None:
        source = by_id.get(leading.response_id)
        if source is not None:
            bridge = _bridge(source, expectation, leading.market_cell)
    return ReportBlock(
        line_id=line.id,
        line_name=line.name,
        line_abbr=line.abbr,
        line_retired=line.deleted_at is not None,
        basis_key=basis_key,
        basis_label=rating_basis(basis_key).label if basis_key else "",
        rate_per=expectation.rate_per if expectation else None,
        exposure=exposure,
        exposure_move=_exposure_move(expectation),
        expiring_premium=expectation.expiring_premium if expectation else None,
        expiring_rate_micros=expectation.expiring_rate_micros if expectation else None,
        expiring_exposure=expectation.expiring_exposure if expectation else None,
        expiring_basis=expectation.expiring_basis if expectation else None,
        limit_sought=expectation.limit_sought if expectation else None,
        attach_sought=expectation.attach_sought if expectation else None,
        bridge=bridge,
        rows=rows,
    )


# --- flattening for the spreadsheet ---------------------------------------


# (header, width, right-aligned). Width is the renderer's, not a guess: a
# money column that wraps is unreadable and a client will not widen it.
_CLIENT_COLUMNS: tuple[tuple[str, float, bool], ...] = (
    ("Market", 30.0, False),
    ("Best", 9.0, False),
    ("Layer", 20.0, False),
    ("Status", 14.0, False),
    # SENT, THEN REPLIED, in the order they happened.
    #
    # IT WAS COLLAPSED INTO THE BLOCK HEADING and therefore printed only when
    # every package on the line happened to go out the same day — a
    # coincidence of values, not a block fact. On a line marketed over two
    # days the heading fell silent and there was no column to fall back on, so
    # the CLIENT's workbook carried NO send date at all while the broker's own
    # grid had one (D6, found 2026-08-26). "The panel is the report" was false
    # in that column. The heading no longer prints it, so nothing is stated
    # twice — the same un-collapsing, and the same reason, as the grid's.
    ("Sent", 10.0, False),
    ("Replied", 10.0, False),
    ("Rate", 9.0, True),
    # THE DENOMINATOR THIS ROW'S RATE IS STATED PER, where the heading's is
    # not it. Left-aligned and blank on the ordinary row: it is a unit in
    # words, not a figure, and it speaks only where the heading would mislead
    # (`ReportRow.rate_per_override`). The `Basis` column at the end of this
    # tuple is the same rule on the other axis, and the pair is why a reader
    # can trust the heading everywhere else.
    ("Rate per", 11.0, False),
    # WIDE ENOUGH FOR THE REFUSAL, not just for the percentage. This column
    # prints "-19.4%" most of the time and a SENTENCE the rest of it — "basis
    # changed", "denominator changed", "no expiring rate recorded" — and at
    # 11.0 the sentence was clipped by the populated cell beside it, which is
    # the one case where the words are the whole message.
    ("Rate Δ", max(11.0, max(len(n) for n in REFUSAL_NOTES) + 1.0), True),
    ("Est. premium", 15.0, True),
    ("TRIA", 12.0, True),
    ("Total est. cost", 16.0, True),
    ("Subj.", 7.0, True),
    ("Reason", 22.0, False),
    ("Basis", 15.0, False),
    ("Exposure", 17.0, True),
)
_INTERNAL_EXTRA: tuple[tuple[str, float, bool], ...] = (
    ("Decline reason", 36.0, False),
    ("Commission", 12.0, True),
    ("Clearance", 26.0, False),
    ("Notes", 32.0, False),
)


def columns(audience: str) -> tuple[tuple[str, float, bool], ...]:
    """The column spec, so the sheet and any other rendering of these rows
    cannot disagree about how many there are or what they are called."""
    extra = _INTERNAL_EXTRA if audience == INTERNAL else ()
    return _CLIENT_COLUMNS + extra


def _block_label(block: ReportBlock) -> str:
    parts = [block.line_name]
    if block.basis_label:
        per = f", per {_per_label(block.rate_per)}" if block.rate_per else ""
        parts.append(f"basis {block.basis_label}{per}")
    if block.exposure is not None:
        exposure = fmt_exposure(block.exposure, block.basis_key)
        # THE REASON, WHERE THE PERCENTAGE WOULD HAVE BEEN. A blank beside the
        # exposure reads as a comparison that failed to render; "basis changed"
        # says the two terms are not comparable, which is the fact.
        if block.exposure_move.cell:
            exposure += f" ({block.exposure_move.cell})"
        parts.append(f"exposure {exposure}")
    if block.expiring_premium is not None:
        expiring = f"expiring {format_cents(block.expiring_premium)}"
        if block.expiring_rate_micros:
            expiring += f" at {fmt_rate(block.expiring_rate_micros)}"
        parts.append(expiring)
    return " · ".join(parts)


def _row_cells(
    row: ReportRow, internal: bool, window: DateWindow | None = None
) -> tuple[str, ...]:
    cells = (
        row.market_cell,
        row.best,
        row.layer,
        row.status,
        fmt_date(row.submitted_on, window),
        fmt_date(row.responded_on, window),
        fmt_rate(row.rate_micros),
        row.rate_per_override,
        row.rate_move.cell,
        format_cents(row.premium) if row.premium is not None else "",
        format_cents(row.tria) if row.tria is not None else "",
        format_cents(row.total_cost) if row.total_cost is not None else "",
        str(row.open_subjectivities) if row.open_subjectivities else "",
        row.public_reason,
        row.basis_override,
        row.exposure_override,
    )
    if not internal:
        return cells
    return cells + (
        row.internal_reason,
        f"{row.commission_bps / 100:.1f}%" if row.commission_bps else "",
        row.clearance,
        row.notes,
    )


def _provisional_cells(row: ProvisionalRow, internal: bool) -> tuple[str, ...]:
    """One provisional row, in the SAME columns as every other row.

    WHAT IS BLANK IS BLANK BECAUSE IT IS NOT KNOWN, and every one of them is a
    fact that belongs to a LINE OF COVERAGE this package has not been given:
    the rate and its denominator are stated per unit of exposure on a line, the
    rate movement compares against that line's expiring rate, and the basis and
    exposure overrides are overrides OF A BLOCK HEADING that does not exist
    here. A figure invented for any of them would be a claim about cover
    nobody has chosen.

    The Total is blank for the reason it is blank everywhere: it is premium
    plus TRIA plus fees plus tax and no submission column carries the last
    three, so a total printed here would be a premium wearing a total's name
    on the one column a client compares two quotes on.

    THE REASON COLUMN IS EMPTY ON THE CLIENT SHEET even where a reason was
    recorded. `submission.decline_reason` is free text and has no client-safe
    counterpart — models.PUBLIC_DECLINE_REASONS explains why a single
    unguarded field may never reach a client — so it prints in the INTERNAL
    audience's own Decline reason column and nowhere else.
    """
    cells = (
        row.market,
        row.best,
        # THE LIMIT QUOTED, printed as money and as nothing else. `_layer_label`
        # would read a limit with no attachment as "primary", which is a claim
        # about where in a tower this sits that no one has made.
        format_cents(row.lim) if row.lim is not None else "",
        row.status,
        fmt_date(row.submitted_on),
        fmt_date(row.responded_on),
        "",  # rate — stated per unit of exposure on a line
        "",  # rate per — the denominator that rate would be stated against
        "",  # rate Δ — nothing to compare, and no expiring side to compare to
        format_cents(row.premium) if row.premium is not None else "",
        "",  # TRIA — no submission column carries it
        "",  # total est. cost — three of its four parts are unknown
        str(row.open_subjectivities) if row.open_subjectivities else "",
        "",  # reason — see the docstring: internal only on these rows
        "",  # basis — an override of a block heading there isn't one of
        "",  # exposure — the same
    )
    if not internal:
        return cells
    return cells + (row.internal_reason, "", "", "")


def to_sections(report: MarketingReport) -> list[SheetSection]:
    """One section per line of coverage. A line with no approaches says so IN
    WORDS: an empty table is ambiguous, and a client cannot tell a line nobody
    has gone to market on from a rendering bug."""
    internal = report.audience == INTERNAL
    width = len(columns(report.audience))
    sections: list[SheetSection] = []
    for block in report.blocks:
        if block.is_empty:
            sections.append(
                SheetSection(
                    _block_label(block),
                    (("No markets approached on this line yet.",) + ("",) * (width - 1),),
                )
            )
            continue
        rows = [_row_cells(row, internal, report.window) for row in block.rows]
        if block.bridge is not None:
            bridge = block.bridge
            blank = ("",) * (width - 3)
            rows.append(("",) * width)
            rows.append(("Expiring premium", "", format_cents(bridge.expiring_premium)) + blank)
            rows.append(("Rate effect", "", format_cents(bridge.rate_effect)) + blank)
            rows.append(("Exposure effect", "", format_cents(bridge.exposure_effect)) + blank)
            rows.append((f"{bridge.market}", "", format_cents(bridge.quoted_premium)) + blank)
        sections.append(SheetSection(_block_label(block), tuple(rows)))
    if report.provisional:
        # LAST, and it is the one ordering decision this block gets. The line
        # blocks are what the client is choosing between; this is the marketing
        # nobody has finished recording, and putting it above a line of
        # coverage would lead the sheet with its least certain rows.
        #
        # ITS DATES CARRY THEIR YEAR (`fmt_date` with no window): a submission
        # with no line of coverage has nothing tying it to this placement's
        # marketing window, and these are precisely the rows where a mistyped
        # year has had nowhere to show itself.
        sections.append(
            SheetSection(
                PROVISIONAL_LABEL,
                tuple(_provisional_cells(row, internal) for row in report.provisional),
            )
        )
    return sections


def write(
    conn: sqlite3.Connection,
    placement_id: str,
    out_path: Path,
    today: date,
    audience: str = CLIENT,
) -> Path:
    """The marketing report as a workbook, rendered through towerkit so it
    carries the same formatting as every other sheet a client receives (the
    money.parse_share pattern: formatting authority in ONE place).

    One sheet, one section per line of coverage. The towerkit imports are
    function-level like every other writer here: this module composes, and
    nothing above this line knows a spreadsheet exists."""
    from towerkit.render.table_xlsx import (
        TableColumn,
        TableSection,
        finalize_workbook,
        new_workbook,
        render_table_sheet,
        sanitize_sheet_title,
    )
    from towerkit.theme import load_theme

    report = compose(conn, placement_id, today, audience)
    theme = load_theme(None)
    wb = new_workbook()
    title = "Marketing" if audience == CLIENT else "Marketing (internal)"
    # The workbook's FIRST sheet, the way every other writer here does it.
    # `create_sheet` would leave the library's default empty sheet in front of
    # this one, and a reader opening the file would land on a blank page.
    ws = wb.active
    assert ws is not None
    ws.title = sanitize_sheet_title(f"{title} — {report.account}"[:31])
    render_table_sheet(
        ws,
        [
            TableColumn(header, width, align="right" if right else "left")
            for header, width, right in columns(audience)
        ],
        [TableSection(s.label, s.rows) for s in to_sections(report)],
        theme=theme,
    )
    return finalize_workbook(wb, out_path)
