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
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from babel.dates import format_date

from ..models import LineOfCoverage, MarketResponse, PlacementLine, rating_basis
from ..money import format_cents
from ..repo import lines as lines_repo
from ..repo import marketing, orgs, placements, submissions
from .export_open_items import SheetSection

CLIENT = "client"
INTERNAL = "internal"

# The order a client should read them in: what is live first, what is closed
# last. The eye lands on the top of a block and must find the options, not a
# declination that happens to sort first alphabetically.
_STATUS_ORDER = {
    "bound": 0,
    "quoted": 1,
    "indicated": 2,
    "pending": 3,
    "declined": 4,
    "non_response": 5,
}

_STATUS_LABEL = {
    "bound": "Bound",
    "quoted": "Quoted",
    "indicated": "Indicated",
    "pending": "Pending",
    "declined": "Declined",
    "non_response": "Non-response",
}

_PUBLIC_REASON_LABEL = {
    "class_appetite": "Class / appetite",
    "loss_history": "Loss history",
    "capacity": "Capacity",
    "pricing": "Pricing",
    "incumbent_relationship": "Incumbent relationship",
    "no_reason_given": "No reason given",
}

_MICROS = 1_000_000


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    return format_date(date.fromisoformat(iso), format="d MMM", locale="en_US")


def _fmt_rate(rate_micros: int | None) -> str:
    """A rate is not money: two decimals, no currency symbol, no thousands
    separator that would make 1,010.05 look like a premium."""
    if rate_micros is None:
        return ""
    return f"{rate_micros / _MICROS:.2f}"


def _fmt_exposure(amount: int | None, basis_key: str | None) -> str:
    """Cents when the basis is monetary, a whole count when it is not — the
    one decision models.RatingBasis.monetary exists to make, read here rather
    than judged."""
    if amount is None:
        return ""
    if basis_key is None:
        return format_cents(amount)
    basis = rating_basis(basis_key)
    if basis.monetary:
        return format_cents(amount)
    unit = f" {basis.unit_label}" if basis.unit_label else ""
    return f"{amount:,}{unit}"


def _premium_from(rate_micros: int, exposure: int, rate_per: int) -> int:
    """Cents, from a rate and an exposure. Used ONLY for the bridge, never to
    replace a stated premium: carriers round, apply minimum premiums and
    expense constants, and a computed figure will disagree with the one on
    their quote letter."""
    return round(rate_micros * exposure / (_MICROS * rate_per))


@dataclass(frozen=True)
class RateMove:
    """The rate change against expiring, or the reason there isn't one."""

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
    client's own growth. Reconciles exactly — expiring + rate + exposure =
    quoted — which is the whole reason it is worth printing."""

    expiring_premium: int
    rate_effect: int
    exposure_effect: int
    quoted_premium: int
    market: str


@dataclass(frozen=True)
class ReportRow:
    market: str
    via: str | None
    best: str
    layer: str
    status: str
    responded_on: str | None
    submitted_on: str
    rate_micros: int | None
    rate_move: RateMove
    premium: int | None
    tria: int | None
    total_cost: int | None
    open_subjectivities: int
    public_reason: str
    basis_override: str
    exposure_override: str
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
        return self.market


@dataclass(frozen=True)
class ReportBlock:
    line_name: str
    line_abbr: str | None
    submitted_on: str
    # BOTH the label and the KEY. The label is what a person reads; the key is
    # what decides whether the exposure beside it is cents or a count, and a
    # block that carried only the label had nothing to pass to _fmt_exposure —
    # so 350 power units printed to a client as "$3.50" (found 2026-08-25 by
    # an adversarial check, on the client sheet AND the client xlsx).
    basis_key: str | None
    basis_label: str
    rate_per: int | None
    exposure: int | None
    exposure_change_pct: float | None
    expiring_premium: int | None
    expiring_rate_micros: int | None
    limit_sought: int | None
    attach_sought: int | None
    bridge: Bridge | None
    rows: tuple[ReportRow, ...]

    @property
    def is_empty(self) -> bool:
        return not self.rows


@dataclass(frozen=True)
class MarketingReport:
    account: str
    program: str
    period: str
    as_of: str
    audience: str
    blocks: tuple[ReportBlock, ...]


# --- composition -----------------------------------------------------------


def _rate_move(response: MarketResponse, line: PlacementLine | None) -> RateMove:
    """Rate change against expiring — or a sentence saying why there is none.

    NEVER assumes exposure was flat. An unlabelled flat-exposure assumption
    puts a figure in front of a client that looks like rate change and is
    only premium change wearing a rate's clothes; it is indistinguishable
    from a real one on the page (Grant, 2026-08-25)."""
    if response.rate_micros is None:
        return RateMove(None)
    if line is None or line.expiring_rate_micros is None:
        return RateMove(None, "no expiring rate recorded")
    quoted_basis = response.rating_basis or (line.rating_basis if line else None)
    if line.expiring_basis and quoted_basis and line.expiring_basis != quoted_basis:
        return RateMove(None, "basis changed")
    if not line.expiring_rate_micros:
        return RateMove(None, "no expiring rate recorded")
    pct = (response.rate_micros / line.expiring_rate_micros - 1) * 100
    return RateMove(pct)


def _bridge(response: MarketResponse, line: PlacementLine | None, market: str) -> Bridge | None:
    """Split the premium change into the part that is rate and the part that
    is the client's own growth. Needs both sides of both facts; returns None
    rather than half a story."""
    if line is None or response.rate_micros is None or response.premium is None:
        return None
    rate_per = response.rate_per or line.rate_per
    exposure = response.exposure_amount or line.expected_exposure
    if not rate_per or exposure is None:
        return None
    if line.expiring_premium is None or line.expiring_exposure is None:
        return None
    if line.expiring_rate_micros is None:
        return None
    if line.expiring_basis and line.rating_basis and line.expiring_basis != line.rating_basis:
        return None
    rate_effect = _premium_from(
        response.rate_micros - line.expiring_rate_micros, line.expiring_exposure, rate_per
    )
    exposure_effect = _premium_from(
        response.rate_micros, exposure - line.expiring_exposure, rate_per
    )
    return Bridge(
        expiring_premium=line.expiring_premium,
        rate_effect=rate_effect,
        exposure_effect=exposure_effect,
        quoted_premium=response.premium,
        market=market,
    )


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
    # One bulk read each, not a query per printed cell.
    org_ids = {placement.org_id}
    for response in responses:
        org_ids |= {response.market_org_id or "", response.via_org_id or ""}
    org_ids.discard("")
    names = orgs.names_for(conn, org_ids)
    best = orgs.best_ratings_for(conn, org_ids)

    by_line: dict[str, list[MarketResponse]] = {}
    for response in responses:
        by_line.setdefault(response.line_id, []).append(response)

    blocks: list[ReportBlock] = []
    # Every line the placement EXPECTS, plus any a response named anyway — a
    # line marketed without an expectation row is still a line being marketed.
    for line_id in list(expectations) + [k for k in by_line if k not in expectations]:
        line = vocabulary.get(line_id)
        if line is None:
            continue
        expectation = expectations.get(line_id)
        rows = tuple(
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
            for response in sorted(
                by_line.get(line_id, []),
                key=lambda r: (
                    _STATUS_ORDER.get(r.status, 9),
                    r.premium if r.premium is not None else 1 << 62,
                    r.responded_on or "",
                ),
            )
        )
        blocks.append(_block(line, expectation, rows, submitted, by_line.get(line_id, [])))

    return MarketingReport(
        account=names.get(placement.org_id, ""),
        program=placement.program_name,
        period=f"{placement.period_from} to {placement.period_to}",
        as_of=today.isoformat(),
        audience=audience,
        blocks=tuple(blocks),
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
        market=market,
        via=via,
        best=best.get(response.market_org_id or "", ""),
        layer=_layer_label(response),
        status=_STATUS_LABEL.get(response.status, response.status),
        responded_on=response.responded_on,
        submitted_on=submitted.get(response.submission_id, ""),
        rate_micros=response.rate_micros,
        rate_move=_rate_move(response, expectation),
        premium=response.premium,
        tria=response.tria_premium,
        total_cost=response.total_cost,
        open_subjectivities=subjectivities.get(response.submission_id, 0),
        public_reason=_PUBLIC_REASON_LABEL.get(response.decline_reason_public or "", ""),
        basis_override=(
            rating_basis(response.rating_basis).label if response.rating_basis else ""
        ),
        exposure_override=_fmt_exposure(response.exposure_amount, basis_key),
        internal_reason=(response.decline_reason or "") if internal else "",
        commission_bps=response.commission_bps if internal else None,
        notes=(response.notes or "") if internal else "",
        clearance=clearance,
    )


def _block(
    line: LineOfCoverage,
    expectation: PlacementLine | None,
    rows: tuple[ReportRow, ...],
    submitted: dict[str, str],
    responses: list[MarketResponse],
) -> ReportBlock:
    basis_key = expectation.rating_basis if expectation else None
    exposure = expectation.expected_exposure if expectation else None
    change = None
    if (
        expectation
        and expectation.expiring_exposure
        and exposure is not None
    ):
        change = (exposure / expectation.expiring_exposure - 1) * 100
    # The submission date belongs in the header: one submission goes out, and
    # repeating its date down a column is the duplication the DRY rule names.
    dates = {submitted.get(r.submission_id, "") for r in responses}
    header_date = dates.pop() if len(dates) == 1 else ""
    leading = next((r for r in rows if r.status in ("Quoted", "Bound")), None)
    bridge = None
    if leading is not None:
        source = next(
            (r for r in responses if _layer_label(r) == leading.layer
             and r.premium == leading.premium),
            None,
        )
        if source is not None:
            bridge = _bridge(source, expectation, leading.market_cell)
    return ReportBlock(
        line_name=line.name,
        line_abbr=line.abbr,
        submitted_on=header_date,
        basis_key=basis_key,
        basis_label=rating_basis(basis_key).label if basis_key else "",
        rate_per=expectation.rate_per if expectation else None,
        exposure=exposure,
        exposure_change_pct=change,
        expiring_premium=expectation.expiring_premium if expectation else None,
        expiring_rate_micros=expectation.expiring_rate_micros if expectation else None,
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
    ("Replied", 10.0, False),
    ("Rate", 9.0, True),
    ("Rate Δ", 11.0, True),
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
    if block.submitted_on:
        parts.append(f"submitted {_fmt_date(block.submitted_on)}")
    if block.basis_label:
        per = ""
        if block.rate_per and block.rate_per > 1:
            per = f", per {format_cents(block.rate_per * 100)}"
        parts.append(f"basis {block.basis_label}{per}")
    if block.exposure is not None:
        exposure = _fmt_exposure(block.exposure, block.basis_key)
        if block.exposure_change_pct is not None:
            exposure += f" ({block.exposure_change_pct:+.1f}%)"
        parts.append(f"exposure {exposure}")
    if block.expiring_premium is not None:
        expiring = f"expiring {format_cents(block.expiring_premium)}"
        if block.expiring_rate_micros:
            expiring += f" at {_fmt_rate(block.expiring_rate_micros)}"
        parts.append(expiring)
    return " · ".join(parts)


def _row_cells(row: ReportRow, internal: bool) -> tuple[str, ...]:
    cells = (
        row.market_cell,
        row.best,
        row.layer,
        row.status,
        _fmt_date(row.responded_on),
        _fmt_rate(row.rate_micros),
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
        rows = [_row_cells(row, internal) for row in block.rows]
        if block.bridge is not None:
            bridge = block.bridge
            blank = ("",) * (width - 3)
            rows.append(("",) * width)
            rows.append(("Expiring premium", "", format_cents(bridge.expiring_premium)) + blank)
            rows.append(("Rate effect", "", format_cents(bridge.rate_effect)) + blank)
            rows.append(("Exposure effect", "", format_cents(bridge.exposure_effect)) + blank)
            rows.append((f"{bridge.market}", "", format_cents(bridge.quoted_premium)) + blank)
        sections.append(SheetSection(_block_label(block), tuple(rows)))
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
