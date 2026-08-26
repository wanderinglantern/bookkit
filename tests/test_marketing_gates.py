"""Five GATES over the marketing surfaces — one per recurring defect class.

WHY THESE ARE GATES AND NOT TESTS. Three adversarial rounds found 11, then 7,
then 8 defects, and seven of the last eight were ONE RULE NOT APPLIED AT THE
ADJACENT SITE: a guard on the exposure cell and not the rate cell, a
future-date check on `sent_on` and not `responded_on`, typing preserved on the
add-market row and not on the add-a-line control three inches above it, a unit
printed in the grid and not in the workbook. Patching site by site is what
produced that shape. Each test below therefore WALKS every site its rule
applies to, derived from the code rather than listed here, and fails NAMING the
sites that break it — so the next field, cell, comparison, form or refusal is
carried into the rule on the commit that adds it.

A GATE IS ONLY AS GOOD AS WHERE IT LOOKS. Every one of them says, in its own
docstring, which surfaces it reaches and which it cannot. Read that half before
trusting a green tick.

Nothing here is a fix. These describe the rule; where the code does not hold
it, the gate is RED and the red test is the ticket.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bookkit import mcpserver
from bookkit.forms.inline import (
    MARKET_APPROACH_FIELDS,
    MARKET_RESPONSE_FIELDS,
    placement_line_fields,
)
from bookkit.services import marketing_report
from bookkit.web import marketing_grid
from bookkit.web.app import create_app

GL = "general-liability"
AUTO = "auto"

SRC = Path(mcpserver.__file__).parent


# --- the harness ------------------------------------------------------------
#
# The same shape tests/test_web_marketing.py uses. It is restated rather than
# imported because a test module importing another test module binds two files
# that are collected independently; the helpers are four lines each.


@pytest.fixture
def client_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o
        for o in orgs.list_orgs(conn, kind="client")
        if any(p.program_path for p in placements.for_org(conn, o.id))
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _linked(client, org):
    from bookkit.repo import placements

    return next(
        p for p in placements.for_org(client.app.state.conn, org.id) if p.program_path
    )


def _market(conn, name: str):
    """The market this book knows by that name, created only if it does not
    already know one — a second org with the same name makes the name lookup
    the routes do ambiguous."""
    from bookkit.repo import orgs

    org = orgs.find_by_name(conn, name)
    if org is None or org.kind != "market":
        org = orgs.create(conn, kind="market", name=name, status="active")
    return org


def _approach(conn, placement_id: str, market, line_id: str = GL, **fields):
    from bookkit.repo import marketing, submissions

    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2026-08-01", placement_id=placement_id
    )
    return marketing.create_response(
        conn, sub.id, line_id, market_org_id=market.id, **fields
    )


def _mcp_book(conn: sqlite3.Connection):
    from bookkit.repo import orgs, placements

    client = orgs.create(conn, kind="client", name="Gatekeeper Inc", status="active")
    placement = placements.create(
        conn,
        org_id=client.id,
        program_name="2027 casualty",
        period_from="2027-01-01",
        period_to="2028-01-01",
    )
    return client, placement


def _refused(response) -> bool:
    """A refused inline-cell save answers 200 with the EDITOR again, carrying
    the message — that is commit-in-place (macros/cell.html)."""
    return response.status_code == 200 and "cell-error-msg" in response.text


def _named(failures: list[str], rule: str) -> None:
    if failures:
        raise AssertionError(
            f"{rule}\n\n"
            + "\n".join(f"  * {line}" for line in failures)
            + "\n\nEach line names the site. Fix the site or, if the rule "
            "genuinely does not bind there, declare it in this test with the "
            "reason — never delete the walk."
        )


# ===========================================================================
# G1. A FIGURE WHOSE MEANING DEPENDS ON A UNIT IS REFUSED WHILE THE UNIT IS
#     UNSET.
# ===========================================================================
#
# The worked example is the exposure cell: "42 power units and $0.42 are the
# same digits, and nothing in the figure says which one it is". The rate cell
# one column over has no such guard — a rate is accepted while the line has no
# denominator, and setting one later silently claims it (D4, 2026-08-26).

_NUMERIC_KINDS = frozenset({"money", "rate", "count", "int", "share"})

# THE UNIT EACH FIGURE'S MEANING HANGS OFF. The unit is another stored column,
# and a figure typed while it is NULL means nothing that can be read back.
UNIT_OF: dict[tuple[str, str], str] = {
    ("placement_line", "expected_exposure"): "rating_basis",
    ("placement_line", "expiring_exposure"): "expiring_basis",
    # A rate is a numerator over a denominator. `rate_per` IS the denominator,
    # and 1.42 per $100 is ten times 1.42 per $1,000 (models.RATE_PER_CHOICES
    # says so in its own words).
    ("placement_line", "expiring_rate_micros"): "rate_per",
    ("market_response", "rate_micros"): "rate_per",
}

# THE FIGURES WHOSE UNIT CANNOT VARY, each with the reason. A money column is
# integer cents and the currency never changes in this book, so format_cents
# printing "$" is the unit and it is always right.
NO_UNIT: dict[tuple[str, str], str] = {
    ("placement_line", "attach_sought"): "cents; the unit is the currency",
    ("placement_line", "limit_sought"): "cents; the unit is the currency",
    ("placement_line", "expiring_premium"): "cents; the unit is the currency",
    ("market_response", "attach"): "cents; the unit is the currency",
    ("market_response", "lim"): "cents; the unit is the currency",
    ("market_response", "premium"): "cents; the unit is the currency",
    ("market_response", "tria_premium"): "cents; the unit is the currency",
    ("market_response", "policy_fees"): "cents; the unit is the currency",
    ("market_response", "surplus_lines_tax"): "cents; the unit is the currency",
    ("market_approach", "attach"): "cents; the unit is the currency",
    ("market_approach", "lim"): "cents; the unit is the currency",
}

# How each unit-bearing figure is reached on the MCP surface, so the gate can
# put the same question to the assistant's door as to the browser's. A figure
# with no MCP argument is named here as None WITH the reason.
MCP_ARG: dict[tuple[str, str], str | None] = {
    ("placement_line", "expected_exposure"): "expected_exposure",
    ("placement_line", "expiring_exposure"): "expiring_exposure",
    ("placement_line", "expiring_rate_micros"): "expiring_rate",
    ("market_response", "rate_micros"): "rate",
}

# A value of the right KIND, so the only thing left that could refuse it is
# the missing unit.
_SAMPLE = {"money": "1000", "rate": "9.60", "count": "42", "int": "42", "share": "10"}


def _numeric_sites() -> dict[tuple[str, str], str]:
    """Every editable numeric field on the marketing surfaces, off the Field
    tuples the surfaces themselves render — never a list written here.

    `placement_line_fields()` is called with NO bases, which is the state this
    gate is about: with nothing stored, both exposures are `count` fields.
    """
    sites: dict[tuple[str, str], str] = {}
    for record, fields in (
        ("market_response", MARKET_RESPONSE_FIELDS),
        ("placement_line", placement_line_fields()),
        ("market_approach", MARKET_APPROACH_FIELDS),
    ):
        for field in fields:
            if field.kind in _NUMERIC_KINDS:
                sites[(record, field.key)] = field.kind
    return sites


def test_g1_a_figure_is_refused_while_the_unit_that_gives_it_meaning_is_unset(
    client_and_org, conn
) -> None:
    """G1 — every editable numeric cell on the marketing surfaces is either
    declared unit-free WITH a reason, or refuses a figure while its unit is
    NULL, on the web AND on MCP.

    WHERE THIS GATE LOOKS: `MARKET_RESPONSE_FIELDS`, `placement_line_fields()`
    and `MARKET_APPROACH_FIELDS` — the three tuples every marketing input on
    both surfaces is built from — and it drives the real routes
    (`.../marketing/lines/{line}/cell/{key}`, `.../marketing/responses/{id}/
    cell/{key}`) and the real tools (`_set_placement_line`,
    `_market_responded`).

    WHERE IT CANNOT LOOK: (a) an importer, because bookkit has no marketing
    import mapper — a mapper written later would carry figures past every door
    this gate knows; (b) a numeric fact stored on a marketing table but NOT
    exposed as an editable Field (`market_response.exposure_amount` and
    `commission_bps` are both such today), because a value no surface accepts
    cannot be typed without a unit; (c) towerkit's own files, which carry no
    exposure. The first is the real gap and is the one to widen when a
    marketing import lands.
    """
    client, org = client_and_org
    placement = _linked(client, org)
    wconn = client.app.state.conn
    from bookkit.repo import marketing as marketing_repo

    sites = _numeric_sites()

    # (1) THE DECLARATION IS COMPLETE. A numeric field in neither table is a
    #     field nobody has asked the question about.
    undeclared = [
        f"{record}.{key} ({kind}) is neither in UNIT_OF nor in NO_UNIT"
        for (record, key), kind in sorted(sites.items())
        if (record, key) not in UNIT_OF and (record, key) not in NO_UNIT
    ]
    both = [
        f"{record}.{key} is in BOTH UNIT_OF and NO_UNIT"
        for (record, key) in sorted(sites)
        if (record, key) in UNIT_OF and (record, key) in NO_UNIT
    ]
    stale = [
        f"{record}.{key} is declared here and is no longer an editable numeric "
        f"field on any marketing surface"
        for (record, key) in sorted(set(UNIT_OF) | set(NO_UNIT))
        if (record, key) not in sites
    ]
    _named(undeclared + both + stale, "the unit declaration is out of step:")

    missing_arg = [
        f"{record}.{key} has a unit but no MCP_ARG entry — say which tool "
        f"argument reaches it, or None with the reason"
        for (record, key) in sorted(UNIT_OF)
        if (record, key) not in MCP_ARG
    ]
    _named(missing_arg, "the MCP half of the walk is out of step:")

    # (2) THE RULE ITSELF, driven at both doors.
    failures: list[str] = []

    for (record, key), unit in sorted(UNIT_OF.items()):
        kind = sites[(record, key)]
        raw = _SAMPLE[kind]

        # --- the web ---------------------------------------------------
        # A fresh line each time, so no earlier write leaves a unit behind.
        line_id = GL if record == "placement_line" else AUTO
        base = f"/accounts/{org.ref}/program/{placement.id}/marketing"
        if record == "placement_line":
            marketing_repo.set_placement_line(
                wconn, placement.id, line_id, expiring_premium=41_200_000
            )
            assert marketing_grid.stored(
                marketing_repo.placement_line(wconn, placement.id, line_id), unit
            ) is None, f"{unit} should be unset for this leg"
            url = f"{base}/lines/{line_id}/cell/{key}"
        else:
            response = _approach(
                wconn, placement.id, _market(wconn, "Travelers"), line_id=line_id
            )
            url = f"{base}/responses/{response.id}/cell/{key}"

        got = client.post(url, data={key: raw})
        if not _refused(got):
            failures.append(
                f"web {record}.{key}: POST {url} with {raw!r} was ACCEPTED "
                f"(HTTP {got.status_code}) while {unit} is unset — the figure "
                f"is stored with nothing saying what it means"
            )

        # --- MCP -------------------------------------------------------
        arg = MCP_ARG[(record, key)]
        if arg is None:
            continue
        _, mcp_placement = _mcp_book(conn)
        market = _market(conn, "Berkley")
        try:
            if record == "placement_line":
                mcpserver._set_placement_line(
                    conn, mcp_placement.ref, GL, **{arg: raw}
                )
            else:
                approach = mcpserver._market_approach(
                    conn, mcp_placement.ref, GL, market=market.name
                )
                mcpserver._market_responded(
                    conn, approach["response_id"], **{arg: raw}
                )
        except ValueError:
            pass  # refused, which is the rule
        else:
            failures.append(
                f"mcp {record}.{key}: {arg}={raw!r} was ACCEPTED while {unit} "
                f"is unset — the assistant can store a figure that means nothing"
            )

    _named(
        failures,
        "a figure whose meaning depends on a unit was accepted while that "
        "unit was unset (42 power units and $0.42 are the same digits):",
    )


# ===========================================================================
# G2. EVERY COMPARISON BETWEEN TWO FIGURES CHECKS BOTH AXES.
# ===========================================================================
#
# Rate Δ, the exposure move and the premium bridge each compare a stored figure
# to another one. BASIS says what is being measured and DENOMINATOR says how
# much of it one unit of rate buys; each axis has been missed exactly once —
# the exposure move printed "-100%" across two bases (F1), and the rate move
# divided a rate per $100 by a rate per $1,000 (C2).

# The two axes, and the pair of columns each one lives in.
AXES: tuple[str, ...] = ("basis", "denominator")

# Which axes bear on each comparison, and — for an axis that does NOT bear —
# the reason. Keyed by the function's own name so the walk below can report a
# comparison that has no entry.
COMPARISON_AXES: dict[str, dict[str, str | None]] = {
    # None = the axis binds and must be refused across.
    "_rate_move": {"basis": None, "denominator": None},
    "_bridge": {"basis": None, "denominator": None},
    "_exposure_move": {
        "basis": None,
        # An exposure is a quantity, not a ratio: there is no denominator under
        # it to disagree about. `rate_per` marks a RATE and nothing else.
        "denominator": "an exposure is a quantity and has no denominator",
    },
}


def _comparisons() -> dict[str, Any]:
    """Every function in `marketing_report` that COMPARES two stored figures,
    discovered by its return type rather than listed.

    `Move` and `Bridge` are the two shapes this module gives a comparison —
    "the percentage, or the reason there isn't one" and "the walk, or None" —
    and both docstrings say that is what they are for. A fourth comparison
    added with either return annotation is walked here on the commit that adds
    it, which is the whole point.
    """
    found: dict[str, Any] = {}
    for name, obj in vars(marketing_report).items():
        if not inspect.isfunction(obj):
            continue
        if getattr(obj, "__module__", "") != marketing_report.__name__:
            continue
        returns = str(inspect.signature(obj).return_annotation)
        if "Move" in returns or "Bridge" in returns:
            found[name] = obj
    return found


def _line(**over: Any):
    from bookkit.models import PlacementLine

    base = dict(
        id="pl-1",
        created_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-01T00:00:00+00:00",
        placement_id="p-1",
        line_id=GL,
        rating_basis="gross_sales",
        expiring_basis="gross_sales",
        rate_per=100,
        expected_exposure=4_850_000_000,
        expiring_exposure=4_100_000_000,
        # THE BASELINE RECONCILES TO THE CENT, on purpose. `_bridge` drops a
        # walk that does not add up (`_reconciles`), so a baseline built from
        # rounded real-world figures would make every `_bridge` scenario below
        # pass for the wrong reason — refused by the arithmetic rather than by
        # the axis check this gate is asking about. 10.00 per $100 of
        # $41,000,000 IS $4,100,000, exactly.
        expiring_premium=410_000_000,
        expiring_rate_micros=10_000_000,
        limit_sought=None,
        attach_sought=None,
    )
    base.update(over)
    return PlacementLine(**base)  # type: ignore[arg-type]


def _resp(**over: Any):
    from bookkit.models import MarketResponse

    base = dict(
        id="r-1",
        created_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-01T00:00:00+00:00",
        submission_id="s-1",
        line_id=GL,
        market_org_id="o-1",
        status="quoted",
        # 11.00 per $100 of $48,500,000, walked from the expiring side above:
        # $4,100,000 + $410,000 rate effect + $825,000 exposure effect.
        rate_micros=11_000_000,
        premium=533_500_000,
        rate_per=100,
        rating_basis=None,
        exposure_amount=None,
    )
    base.update(over)
    return MarketResponse(**base)  # type: ignore[arg-type]


def _compared(name: str, fn: Any, *, line_over: dict, resp_over: dict) -> bool:
    """Did the comparison go ahead? True means it produced a number."""
    line = _line(**line_over)
    response = _resp(**resp_over)
    if name == "_exposure_move":
        out = fn(line)
        return out.pct is not None
    if name == "_rate_move":
        out = fn(response, line)
        return out.pct is not None
    if name == "_bridge":
        return fn(response, line, "Travelers") is not None
    raise KeyError(name)


# For each comparison and axis, the two ways the axes can fail to agree:
# an explicit MISMATCH, and one side UNKNOWN. Both are "not known to agree",
# and a comparison that goes ahead on either is claiming something nobody said.
_MISMATCH: dict[tuple[str, str], tuple[dict, dict]] = {
    ("_rate_move", "basis"): ({"expiring_basis": "power_units"}, {}),
    ("_rate_move", "denominator"): ({"rate_per": 1000}, {"rate_per": 100}),
    ("_bridge", "basis"): ({"expiring_basis": "power_units"}, {}),
    ("_bridge", "denominator"): ({"rate_per": 1000}, {"rate_per": 100}),
    ("_exposure_move", "basis"): ({"expiring_basis": "power_units"}, {}),
}
_UNKNOWN: dict[tuple[str, str], tuple[dict, dict]] = {
    # The line has a basis; the expiring figure was recorded before anyone said
    # what it was measured on.
    ("_rate_move", "basis"): ({"expiring_basis": None}, {}),
    # THE D4 DOOR. The response's rate was typed while the line had no
    # denominator, so `_stamp_rate_per` could not stamp one; the line acquired
    # `per $100` afterwards, and the comparison then reads the unstamped rate
    # as if it had been quoted per $100 all along.
    ("_rate_move", "denominator"): ({"rate_per": 100}, {"rate_per": None}),
    ("_bridge", "basis"): ({"expiring_basis": None}, {}),
    ("_bridge", "denominator"): ({"rate_per": 100}, {"rate_per": None}),
    ("_exposure_move", "basis"): ({"expiring_basis": None}, {}),
}


def test_g2_a_comparison_is_refused_unless_both_axes_are_known_to_agree() -> None:
    """G2 — every comparison `marketing_report` computes refuses across a
    mismatch on EITHER axis, and refuses when an axis is merely UNKNOWN on one
    side rather than reading silence as agreement.

    WHERE THIS GATE LOOKS: every function in `services.marketing_report` whose
    return annotation is `Move` or `Bridge` — the two shapes this module gives
    a comparison. That is a real walk: a fourth comparison written with either
    annotation is picked up on the commit that adds it, and fails here until
    somebody says which axes bear on it. The scenarios themselves have to be
    supplied per comparison (three figures do not compare the same way), so the
    walk finds the site and the table forces the human.

    WHERE IT CANNOT LOOK: a comparison computed OUTSIDE this module — the
    exposure Δ in the block header and the Total on the grid both come from
    here, but nothing stops a future panel dividing two numbers in a template
    or a route, and this gate would never see it. `web/marketing_grid.py` says
    in its own words that it "COMPOSES NOTHING"; that sentence, not this test,
    is what keeps the comparison count at three.
    """
    found = _comparisons()

    unwalked = [
        f"marketing_report.{name} returns a comparison and has no entry in "
        f"COMPARISON_AXES — say which axes bear on it"
        for name in sorted(found)
        if name not in COMPARISON_AXES
    ]
    gone = [
        f"COMPARISON_AXES names {name}, which is no longer a comparison in "
        f"marketing_report"
        for name in sorted(COMPARISON_AXES)
        if name not in found
    ]
    _named(unwalked + gone, "the comparison walk is out of step:")

    for name in sorted(COMPARISON_AXES):
        for axis in AXES:
            if axis not in COMPARISON_AXES[name]:
                raise AssertionError(
                    f"{name} says nothing about the {axis} axis — every "
                    f"comparison must state, for every axis, whether it binds"
                )

    failures: list[str] = []

    # THE BASELINE MUST COMPARE. Every scenario below is "the baseline, with
    # one axis disturbed", so a baseline that does not produce a number in the
    # first place would make every one of them pass while asking nothing —
    # which is the shape of a gate that looks nowhere.
    for name, fn in sorted(found.items()):
        if not _compared(name, fn, line_over={}, resp_over={}):
            failures.append(
                f"{name} produced NO comparison from a book where both axes "
                f"agree — every scenario below would then pass vacuously"
            )
    _named(failures, "the baseline this gate disturbs is not a comparison:")

    for name in sorted(COMPARISON_AXES):
        fn = found[name]
        for axis, excuse in sorted(COMPARISON_AXES[name].items()):
            if excuse is not None:
                continue
            for label, table in (("mismatched", _MISMATCH), ("unknown", _UNKNOWN)):
                key = (name, axis)
                if key not in table:
                    failures.append(
                        f"{name} / {axis}: no {label} scenario is written for "
                        f"it, so this gate is not actually asking"
                    )
                    continue
                line_over, resp_over = table[key]
                if _compared(name, fn, line_over=line_over, resp_over=resp_over):
                    failures.append(
                        f"{name} compared two figures whose {axis} is {label} "
                        f"(line={line_over}, response={resp_over}) and produced "
                        f"a number — the reader cannot tell it from a real one"
                    )

    _named(
        failures,
        "a comparison went ahead across an axis that is not known to agree:",
    )


# ===========================================================================
# G3. A UNIT IS PRINTED WHEREVER ITS FIGURE IS.
# ===========================================================================
#
# The Rate column prints a bare number under a heading that may not be its
# denominator (D3), and the workbook drops the send date entirely on a line
# marketed over more than one day (D6). "The panel is the report" is the
# design; nothing currently holds it true.

# Every column of the CLIENT workbook, and what carries the unit of the figure
# it prints. A column whose reading depends on a unit must either carry that
# unit IN THE CELL, or be a fact that cannot differ from the block heading
# above it — and the second is a claim this gate checks, not a note.
UNIT_IN_CELL = "in-cell"          # the cell prints its own unit
UNIT_IN_BLOCK = "block-heading"   # the heading states it and no row can differ
UNITLESS = "unitless"             # words, a date, a count of things

CLIENT_COLUMN_UNIT: dict[str, str] = {
    "Market": UNITLESS,
    "Best": UNITLESS,
    "Layer": UNIT_IN_CELL,          # format_cents prints "$"
    "Status": UNITLESS,
    "Sent": UNITLESS,
    "Replied": UNITLESS,
    "Rate": UNIT_IN_BLOCK,          # claims: the block heading's denominator
    # The denominator IS a unit, printed as words — and printed only where the
    # heading is not the answer, which is what keeps the claim above true.
    "Rate per": UNITLESS,
    "Rate Δ": UNITLESS,             # a percentage, or the refusal in words
    "Est. premium": UNIT_IN_CELL,
    "TRIA": UNIT_IN_CELL,
    "Total est. cost": UNIT_IN_CELL,
    "Subj.": UNITLESS,
    "Reason": UNITLESS,
    "Basis": UNITLESS,              # the basis IS a unit, printed as words
    "Exposure": UNIT_IN_CELL,       # fmt_exposure prints "$" or the unit label
}

# THE GRID AND THE WORKBOOK ARE ONE REPORT. Each workbook column names the grid
# column(s) it corresponds to; the grid deliberately UN-COLLAPSES three of them
# (marketing_grid's own docstring says which and why). A workbook column with
# no grid counterpart, or a grid column with no workbook counterpart, is the
# panel and the report saying different things.
WORKBOOK_TO_GRID: dict[str, tuple[str, ...]] = {
    "Market": ("market",),
    "Best": ("best",),
    # "$5M xs $5M" is derived from two figures nobody can type as a sentence.
    "Layer": ("attach", "lim"),
    "Status": ("status",),
    "Sent": ("sent_on",),
    "Replied": ("responded_on",),
    "Rate": ("rate",),
    "Rate per": ("rate_per",),
    "Rate Δ": ("rate_move",),
    "Est. premium": ("premium",),
    "TRIA": ("tria",),
    # The workbook prints one total; the grid edits the three components under
    # it, because you cannot type a total.
    "Total est. cost": ("total_cost", "fees", "sl_tax"),
    "Subj.": ("subjectivities",),
    "Reason": ("reason",),
    # Per-row overrides of the block heading's two facts.
    "Basis": ("basis_override",),
    "Exposure": ("exposure_override",),
}

# Grid columns with no workbook counterpart, each with the reason it is the
# broker's own and not the client's.
GRID_ONLY: dict[str, str] = {
    "internal_reason": (
        "the underwriter's private opinion; INTERNAL only, and the workbook's "
        "own 'Decline reason' column carries it on the internal audience"
    ),
}


def _client_headers() -> tuple[str, ...]:
    return tuple(h for h, _, _ in marketing_report.columns(marketing_report.CLIENT))


def test_g3_the_workbook_and_the_grid_are_one_report_and_state_their_units(
    client_and_org,
) -> None:
    """G3 — every figure the CLIENT workbook prints whose reading depends on a
    unit carries that unit (or a note that says its unit differs), and the web
    grid and the workbook carry the same columns.

    WHERE THIS GATE LOOKS: `marketing_report.columns(CLIENT)` — the one spec
    the .xlsx writer renders from — and `marketing_grid.COLUMNS`, the one tuple
    the panel's rows are built by walking. Both are derived, so a column added
    to either turns this red on the commit that adds it. The unit claims are
    driven against a REAL composed report, not read off the source.

    WHERE IT CANNOT LOOK: (a) the block HEADING, which is prose built by
    `_block_label` rather than a column — it is checked here only for the one
    fact a row can contradict (the denominator), not exhaustively; (b) the
    INTERNAL audience's four extra columns, whose figures are commission (bps,
    printed as a percentage) and free text — no unit-bearing figure among them
    today, and a fifth one would slip past this gate; (c) the rendered .xlsx
    itself — this walks `to_sections`, which is what the writer renders, so a
    unit lost in towerkit's `render_table_sheet` would not be seen here.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing as marketing_repo

    failures: list[str] = []

    # (1) THE DECLARATIONS COVER THE COLUMNS THAT EXIST.
    headers = _client_headers()
    grid_keys = tuple(c.key for c in marketing_grid.COLUMNS)
    for header in headers:
        if header not in CLIENT_COLUMN_UNIT:
            failures.append(
                f"workbook column {header!r} has no entry in "
                f"CLIENT_COLUMN_UNIT — say what carries its unit"
            )
        if header not in WORKBOOK_TO_GRID:
            failures.append(
                f"workbook column {header!r} has no entry in WORKBOOK_TO_GRID "
                f"— name the grid column(s) it corresponds to"
            )
    for header in sorted(set(CLIENT_COLUMN_UNIT) | set(WORKBOOK_TO_GRID)):
        if header not in headers:
            failures.append(
                f"{header!r} is declared here and is no longer a workbook column"
            )
    _named(failures, "the column declarations are out of step:")

    # (2) THE PANEL IS THE REPORT: same columns, both ways.
    for header, keys in sorted(WORKBOOK_TO_GRID.items()):
        if not keys:
            failures.append(
                f"workbook column {header!r} has NO column on the web grid — "
                f"a client reads a fact the broker's own screen does not show"
            )
            continue
        for key in keys:
            if key not in grid_keys:
                failures.append(
                    f"workbook column {header!r} names grid column {key!r}, "
                    f"which does not exist"
                )
    claimed = {k for keys in WORKBOOK_TO_GRID.values() for k in keys}
    for key in grid_keys:
        if key in claimed:
            continue
        if key in GRID_ONLY:
            continue
        failures.append(
            f"grid column {key!r} reaches no workbook column and is not "
            f"declared broker-only in GRID_ONLY — the panel and the report "
            f"disagree about what this report contains"
        )
    for key in sorted(GRID_ONLY):
        if key not in grid_keys:
            failures.append(f"GRID_ONLY names {key!r}, no longer a grid column")

    # (3) THE UNIT CLAIMS, against a composed report.
    #
    # One line of coverage, rated per $1,000, marketed over TWO days, with one
    # market whose rate was quoted per $100. That single book exercises every
    # claim: the block heading states one denominator, a row disagrees with it,
    # and two packages went out on different days.
    marketing_repo.set_placement_line(
        conn, placement.id, GL,
        rating_basis="gross_sales", expiring_basis="gross_sales", rate_per=1000,
        expected_exposure=4_850_000_000, expiring_exposure=4_100_000_000,
        expiring_premium=41_200_000, expiring_rate_micros=10_048_780,
    )
    from bookkit.repo import submissions as submissions_repo

    early = submissions_repo.create(
        conn, market_org_id=_market(conn, "Travelers").id,
        sent_on="2026-08-06", placement_id=placement.id,
    )
    marketing_repo.create_response(
        conn, early.id, GL, market_org_id=_market(conn, "Travelers").id,
        status="quoted", responded_on="2026-08-12",
        rate_micros=9_600_000, rate_per=100, premium=46_560_000,
    )
    late = submissions_repo.create(
        conn, market_org_id=_market(conn, "Berkley").id,
        sent_on="2026-08-13", placement_id=placement.id,
    )
    marketing_repo.create_response(
        conn, late.id, GL, market_org_id=_market(conn, "Berkley").id,
        status="pending",
    )

    report = marketing_report.compose(
        conn, placement.id, date(2026, 8, 14),
        audience=marketing_report.CLIENT,
    )
    sections = marketing_report.to_sections(report)
    section = next(s for s in sections if "General Liability" in s.label)
    index = {h: i for i, h in enumerate(headers)}

    # THE PREMISE OF EACH CHECK BELOW, asserted rather than assumed — a
    # scenario that quietly stopped exercising the thing would make this half
    # of the gate pass while asking nothing.
    assert "per $1,000" in section.label, section.label
    quoting = next(
        (r for r in section.rows if r[index["Market"]].startswith("Travelers")), None
    )
    assert quoting is not None and quoting[index["Rate"]], section.rows

    # A ROW'S DENOMINATOR DIFFERS FROM THE BLOCK HEADING'S. Either the row
    # says so where the figure is, or the claim "the heading states it and no
    # row can differ" (UNIT_IN_BLOCK) is false.
    if CLIENT_COLUMN_UNIT["Rate"] == UNIT_IN_BLOCK and "$100" not in " ".join(quoting):
        failures.append(
            f"workbook Rate cell prints {quoting[index['Rate']]!r} for "
            f"{quoting[index['Market']]!r} under a heading that says "
            f"'per $1,000' — the rate is stored per $100 and nothing in the "
            f"row says which denominator it is stated against"
        )

    # THE SEND DATE. It is a column on the grid (`sent_on`); the workbook
    # collapses it into the block heading — which `_block_label` can only do
    # when every package on the line went out the same day. Two days here, so
    # the heading is silent and there is no column to fall back on.
    assert "2026-08-06" != "2026-08-13"  # the two packages above
    if "submitted" not in section.label and not any("Sent" in h for h in headers):
        failures.append(
            "the workbook prints NO send date for a line marketed over two "
            "days: `_block_label` collapses it into the heading only when "
            "every package agrees, and there is no Sent column to fall back "
            "on — the grid has one (`sent_on`)"
        )

    _named(
        failures,
        "the client workbook and the web grid do not state the same report, "
        "or print a figure whose unit is not on the page:",
    )


# ===========================================================================
# G4. A RE-RENDERED FORM KEEPS WHAT WAS TYPED.
# ===========================================================================
#
# The add-market row keeps its values on a refusal and on a block answer (C4);
# the add-a-line control three inches above it loses them on both (D5).


_VOID = frozenset({"input", "br", "hr", "img", "meta", "link", "source", "col"})
_CONTROL_CLASSES = ("marketing-line-add", "marketing-add-row")
# A CONTROL IS A CONTAINER, NEVER THE SAVE ITSELF. The add row's `hx-post`
# lives on its `<button>`, and letting a button open a frame of its own made
# that button the innermost control — zero inputs, dropped at its own end tag,
# with the row it belongs to left holding no url and therefore never found.
# The walk then reported ONE control on a panel that has two, and passed.
_CONTAINERS = frozenset({"form", "tr", "div", "section", "fieldset", "td"})


class _Forms(HTMLParser):
    """Every multi-field entry control on the marketing panel, discovered from
    the rendered markup.

    A CONTROL, not a `<form>` tag: the add-market row deliberately has no
    `<form>` element (it lives inside a table, where a form is illegal) and
    carries its POST on the save button instead. What identifies one is what
    makes it a form in the sense that matters here — an explicit save reaching
    ONE url, over MORE THAN ONE named input. An inline CELL is excluded by
    that same test: it holds exactly one input, and its commit model is
    blur-commits rather than an explicit save.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # (tag, frame|None) for every element still open. `input` never gets
        # an end tag, so void elements are never pushed — a depth counter that
        # counted them closed every container one element early, and the walk
        # then found nothing at all.
        self.open: list[tuple[str, dict[str, Any] | None]] = []
        self.found: list[dict[str, Any]] = []
        # A <select>'s chosen value lives on an <option selected>, not on a
        # `value` attribute of its own — a check that read the select's own
        # attributes would call every picker "empty" and pass the moment the
        # control it is walking is a picker.
        self.select: tuple[dict[str, Any], str] | None = None

    def _frame(self) -> dict[str, Any] | None:
        for _, frame in reversed(self.open):
            if frame is not None:
                return frame
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag in _VOID:
            self._record(tag, a)
            return
        frame: dict[str, Any] | None = None
        classes = a.get("class", "")
        if tag in _CONTAINERS and (
            "hx-post" in a or any(c in classes for c in _CONTROL_CLASSES)
        ):
            frame = {
                "tag": tag,
                "id": a.get("id", ""),
                "class": classes,
                "post": a.get("hx-post", ""),
                "preserve": "hx-preserve" in a,
                "inputs": {},
            }
        self.open.append((tag, frame))
        self._record(tag, a)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._record(tag, {k: (v or "") for k, v in attrs})

    def _record(self, tag: str, a: dict[str, str]) -> None:
        frame = self._frame()
        if frame is None:
            return
        if tag in ("input", "select", "textarea") and a.get("name"):
            frame["inputs"][a["name"]] = dict(a, value=a.get("value", ""))
            if tag == "select":
                self.select = (frame, a["name"])
        if tag == "option" and self.select is not None and "selected" in a:
            frame_of, name = self.select
            frame_of["inputs"][name]["value"] = a.get("value", "")
        if tag == "button" and "hx-post" in a and not frame["post"]:
            frame["post"] = a["hx-post"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.select = None
        while self.open:
            open_tag, frame = self.open.pop()
            if frame is not None and frame["post"] and len(frame["inputs"]) > 1:
                self.found.append(frame)
            if open_tag == tag:
                return


def _marketing_section(html: str, placement_id: str) -> str:
    """Just this placement's marketing section.

    THE PROGRAM TAB IS NOT THE MARKETING PANEL. It renders the layers table,
    the named-limits row and every other placement's marketing section too, and
    an unscoped walk picked up `market-add-form` from the LAYERS panel — a real
    control, held by its own tests, and noise here. Scoping is what makes the
    reds this gate reports be about the surface it names.
    """
    start = html.index(f'id="marketing-{placement_id}"')
    start = html.rindex("<section", 0, start)
    end = html.index("</section>", start)
    # The section holds nested <section> elements (the fact groups), so walk
    # forward until the tags balance.
    depth = 0
    i = start
    while i < len(html):
        nxt = html.find("<section", i + 1)
        end = html.find("</section>", i + 1)
        if end == -1:
            break
        if nxt != -1 and nxt < end:
            depth += 1
            i = nxt
            continue
        if depth == 0:
            return html[start : end + len("</section>")]
        depth -= 1
        i = end
    return html[start:]


def _entry_controls(html: str) -> list[dict[str, Any]]:
    parser = _Forms()
    parser.feed(html)
    return parser.found


# What to type into each control to be REFUSED, and how to spot the typing
# coming back. Keyed by a substring of the control's class, because the
# controls are discovered rather than named.
REFUSALS: dict[str, dict[str, str]] = {
    # Both halves given at once: "pick from the list or type a new name, not
    # both". A refusal this control cannot answer without retyping.
    #
    # THE PICKED LINE MUST BE ONE THE CONTROL ACTUALLY OFFERS. It was GL,
    # which the fixture below puts a `placement_line` row on — and
    # `line_add_options` drops every line already on the placement, so GL is
    # not among the options. No markup can echo a chosen value back into a
    # picker that does not carry it, and offering it would break the rule that
    # a picker offers ONLY what is storable (`checked_option` re-queries the
    # same list on the POST). The scenario was asking for something no correct
    # implementation could give, and a browser could never have produced it;
    # AUTO is offered here, which is what a person clicking this control would
    # actually have picked (corrected 2026-08-26).
    "marketing-line-add": {"line_id": AUTO, "line_name": "Marine Cargo"},
    # A carrier the book has never heard of.
    "marketing-add-row": {
        "market": "Zzzz Mutual",
        "attach": "500000",
        "lim": "1000000",
        "status": "pending",
        "sent_on": "2026-08-10",
    },
}


def test_g4_every_entry_control_on_the_panel_keeps_what_was_typed(
    client_and_org,
) -> None:
    """G4 — every multi-field entry control on the marketing panel keeps its
    typed values when its OWN save is refused, and when SOMEBODY ELSE'S write
    re-renders it.

    WHERE THIS GATE LOOKS: the rendered marketing section. The controls are
    DISCOVERED from the markup (an explicit save posting to one url, over more
    than one named input), so a third entry control added to this panel is
    walked on the commit that adds it and fails here until it is given a
    refusal scenario. Both halves are driven against the real routes: the
    control's own POST for the refusal, and TWO sibling writes — a `premium`
    cell, which answers with the BLOCK, and a `sent` cell, which answers with
    the whole SECTION. Both are needed: a control that lives in the block is
    never re-rendered by a section-only answer and the reverse, so one sibling
    write asks the question of only half the controls.

    WHERE IT CANNOT LOOK: (a) inline CELLS, deliberately — they hold one input
    and commit on blur, and their re-render path (`_editor_cell(typed=raw)`) is
    a different contract, held by tests/test_web_marketing.py; (b) a control
    with no inputs — the near-match card is buttons carrying `hx-vals`, so it
    is not walked and could not lose typing anyway; (c) anything outside this
    section: the layers panel's own add-forms are a real surface with the same
    rule and are NOT held here, which is the obvious place to widen this next;
    (d) MCP, which has no re-rendered form to lose.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing as marketing_repo

    # A block to hang the add-market row off, and a market on it so a sibling
    # write has a row to land on.
    marketing_repo.set_placement_line(conn, placement.id, GL)
    sibling = _approach(
        conn, placement.id, _market(conn, "Travelers"), line_id=GL, status="quoted"
    )

    def section(html: str) -> str:
        return _marketing_section(html, placement.id)

    controls = _entry_controls(section(client.get(f"/accounts/{org.ref}/program").text))
    assert controls, "no entry control was found on the marketing panel at all"

    failures: list[str] = []
    unscripted = [
        f"entry control {c['class']!r} (posts to {c['post']!r}) has no refusal "
        f"scenario in REFUSALS — this gate is not asking anything about it"
        for c in controls
        if not any(k in c["class"] for k in REFUSALS)
    ]
    _named(unscripted, "the entry-control walk is out of step:")

    cell = f"/accounts/{org.ref}/program/{placement.id}/marketing"
    siblings = (
        # answers with the BLOCK (routes/marketing._BLOCK_CELLS)
        ("premium cell", f"{cell}/responses/{sibling.id}/cell/premium",
         {"premium": "48708000"}),
        # answers with the whole SECTION (one submission carries every line)
        ("sent cell", f"{cell}/responses/{sibling.id}/sent",
         {"sent_on": "2026-08-04"}),
    )

    for control in controls:
        scenario = next(v for k, v in REFUSALS.items() if k in control["class"])
        marker = control["class"].split()[0]
        typed = {k: v for k, v in scenario.items() if k in control["inputs"] and v}
        assert typed, f"scenario for {control['class']!r} names no field it has"

        # --- ITS OWN REFUSAL -------------------------------------------
        refused = client.post(control["post"], data=scenario)
        assert refused.status_code == 200, refused.status_code
        back = next(
            (c for c in _entry_controls(refused.text) if marker in c["class"]), None
        )
        if back is None:
            failures.append(
                f"a refusal from {control['post']!r} did not answer with the "
                f"{marker!r} control at all, so everything typed into it is gone"
            )
        else:
            for key, value in typed.items():
                got = back["inputs"].get(key, {}).get("value", "")
                if got != value:
                    failures.append(
                        f"a refusal from {control['post']!r} lost {key}={value!r} "
                        f"out of the {marker!r} control — it came back holding "
                        f"{got!r}"
                    )

        # --- SOMEBODY ELSE'S WRITE -------------------------------------
        reached = 0
        for what, url, data in siblings:
            answer = client.post(url, data=data)
            assert answer.status_code == 200, (url, answer.status_code)
            mine = next(
                (c for c in _entry_controls(answer.text) if marker in c["class"]),
                None,
            )
            if mine is None:
                continue  # this write does not re-render the control
            reached += 1
            if mine["preserve"]:
                continue  # htmx keeps the element, and everything in it
            kept = all(
                mine["inputs"].get(key, {}).get("value", "") == value
                for key, value in typed.items()
            )
            if not kept:
                failures.append(
                    f"a {what} save re-rendered the {marker!r} control from its "
                    f"defaults and without hx-preserve — anything half-typed in "
                    f"it is gone, with no message ({url})"
                )
        if not reached:
            # SILENCE IS NOT A PASS. A control that no sibling write happens to
            # re-render has the second half of this rule untested, and the gate
            # says so rather than counting it green.
            failures.append(
                f"no sibling write on this panel re-rendered the {marker!r} "
                f"control, so nothing here asks whether it survives one"
            )

    _named(failures, "an entry control lost what was typed into it:")


# ===========================================================================
# G5. A REFUSAL NAMES A FIX REACHABLE FROM THE SURFACE THAT RAISED IT.
# ===========================================================================
#
# `_reply_guard` tells an MCP caller to "correct the date the submission went
# out", and MCP has no way to do it (D8). The carrier refusal named no fix at
# all until this round (C7).

# The modules whose refusals this gate walks, and the surfaces each one's
# refusals reach. A rule in repo/ or services/ is inherited by BOTH doors —
# that is why it is there — so a fix it names has to exist on both.
REFUSAL_MODULES: dict[str, tuple[str, ...]] = {
    "repo/marketing.py": ("web", "mcp"),
    "services/marketing_entry.py": ("web", "mcp"),
    # Reached from marketing through `marketing_entry.approach`'s
    # `check_not_future`. Three of its other refusals are other pairs' (a
    # project's dates, a request's, a settled subjectivity's) and are declared
    # below anyway: the walk is by MODULE, because a refusal's words do not
    # know which caller raised them.
    "services/consistency.py": ("web", "mcp"),
    "web/routes/marketing.py": ("web",),
}

# Every refusal these modules produce, and the FIX its own words name.
#
#   field       — retype a value the caller already holds. Reachable from any
#                 surface by definition, which is why most refusals take it.
#   web / mcp   — a fix somewhere ELSE: a path the app must actually serve,
#                 and a tool the server must actually register.
#   passthrough — this site does not write a sentence; it relabels another
#                 module's. The fix is that module's business, and naming it
#                 here would be a second copy of it.
#
# The key is a distinctive fragment of the sentence. Matching is
# LONGEST-FIRST, so a fragment that is a substring of a longer one cannot
# silently claim it.
NAMED_FIX: dict[str, dict[str, str]] = {
    # --- repo/marketing.py ------------------------------------------------
    "a market cannot answer a package it has not been sent": {
        # "…or correct the date the submission went out if that is the one
        # that is wrong."
        "web": (
            "/accounts/{ref}/program/{placement_id}/marketing"
            "/responses/{response_id}/sent"
        ),
        "mcp": "submission_sent_on",
    },
    "the same digits over a different denominator": {"field": "expiring_rate_micros"},
    "unknown market response status": {"field": "status"},
    "the carrier and the intermediary are the same market": {
        "field": "market_org_id"
    },
    "a market response needs a carrier or an intermediary": {"field": "via_org_id"},
    "one basis measures money and the other counts things": {
        "field": "expected_exposure"
    },
    # --- services/marketing_entry.py --------------------------------------
    "an approach needs a carrier or an intermediary": {"field": "via"},
    # --- services/consistency.py ------------------------------------------
    "has not happened yet": {"field": "sent_on"},
    "or correct the": {"field": "either date"},
    "does not go with status": {"field": "status"},
    # --- web/routes/marketing.py ------------------------------------------
    "add it at /markets/new first": {"web": "/markets/new"},
    "is not editable on a market response": {"field": "key"},
    "is not an expectation a line of coverage carries": {"field": "key"},
    "that could not be saved and nothing was written": {
        "field": "the figure under the caret"
    },
    "no such market response": {"field": "response_id"},
    "no such line on this placement": {"field": "line_id"},
    "no line of coverage": {"field": "line_id"},
    "a submission has a date it went out on": {"field": "sent_on"},
    "42 power units and $0.42 are the same digits": {"field": "rating_basis"},
    "pick a line of coverage from the list or type a new name, not both": {
        "field": "line_name"
    },
    "pick a line of coverage from the list, or type a new name": {
        "field": "line_name"
    },
    "give the intermediary alone": {"field": "via"},
    "is required": {"field": "status"},
    "is already a line of coverage in this book": {"field": "line_id"},
    "looks like a line of coverage this book": {"field": "line_name"},
    "was created while you were looking at this": {"field": "line_id"},
    # `refused(f"{field.label}: {exc}")` — the parser's own sentence, given
    # the label of the field it was typed into.
    ":": {"passthrough": "forms.spec.parse_value / money.py"},
}


# Longest first: "pick a line of coverage from the list" is a prefix of the
# refusal that adds ", or type a new name", and a shorter key matching first
# would report the specific one as undeclared.
_KEYS: tuple[str, ...] = tuple(sorted(NAMED_FIX, key=len, reverse=True))


def _refusal_strings(relative: str) -> list[str]:
    """Every sentence a module refuses in, off its AST.

    Both shapes count: `raise ValueError("…")` — the rule stated where every
    surface inherits it — and the string literals handed to a route's own
    `refuse(...)`/`refused(...)` helper, which is how a web refusal reaches a
    cell rather than a 500. An f-string is joined from its literal parts, which
    is enough to recognise the sentence.
    """
    tree = ast.parse((SRC / relative).read_text())
    out: list[str] = []

    def literal(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
        return None

    def literals(node: ast.AST) -> list[str]:
        """Every sentence one expression can produce. An `IfExp` is two: the
        `_house` fallback ("that could not be saved…") is the else-branch of
        one, and reading only the whole expression would miss it."""
        if isinstance(node, ast.IfExp):
            return [t for t in (literal(node.body), literal(node.orelse)) if t]
        text = literal(node)
        return [text] if text else []

    for node in ast.walk(tree):
        # A REFUSAL RETURNED AS A VALUE, not raised. `_market_named` hands its
        # miss back as a `str` because a miss is not an exception there — it
        # is a message that belongs in the add row beside the input — and
        # `consistency.order_refusal` returns the sentence every ordered-date
        # pair is refused in. A walk that only read `raise` would miss both.
        if isinstance(node, ast.Return) and node.value is not None:
            for text in literals(node.value):
                if len(text.strip()) > 3:
                    out.append(" ".join(text.split()))
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            fn = node.exc.func
            if isinstance(fn, ast.Name) and fn.id == "ValueError" and node.exc.args:
                text = literal(node.exc.args[0])
                if text:
                    out.append(" ".join(text.split()))
            if isinstance(fn, ast.Name) and fn.id == "HTTPException":
                for kw in node.exc.keywords:
                    if kw.arg == "detail":
                        text = literal(kw.value)
                        if text:
                            out.append(" ".join(text.split()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("refuse", "refused") and node.args:
                text = literal(node.args[0])
                if text:
                    out.append(" ".join(text.split()))
        # `_clash(..., head=f"…")` is a refusal wearing a question's clothes.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_clash":
                for kw in node.keywords:
                    if kw.arg == "head":
                        text = literal(kw.value)
                        if text:
                            out.append(" ".join(text.split()))
    return out


def _web_paths(app) -> set[str]:
    """Every path the APP actually serves.

    `app.routes` holds `_IncludedRouter` wrappers rather than the routes
    themselves, so a naive read of `.path` returns four entries and every
    check against it passes by finding nothing — the shape of a gate that
    looks nowhere. Unwrap to the real router.
    """
    found: set[str] = set()

    def walk(routes) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
            sub = getattr(route, "routes", None)
            if sub is not None and inner is None:
                walk(sub)
            path = getattr(route, "path", None)
            if isinstance(path, str) and path:
                found.add(path)

    walk(app.routes)
    return found


def _mcp_tools(tmp_path: Path) -> set[str]:
    """Every tool the server actually registers — off the registrar, so the
    list cannot go stale (the shape tests/test_mcpserver.py settled on)."""
    from mcp.server.mcpserver import MCPServer

    from bookkit import db

    probe = MCPServer("gate-probe")
    mcpserver._register_read_tools(probe, db.connect(tmp_path / "gate-ro.db"))
    mcpserver._register_write_tools(probe, db.connect(tmp_path / "gate-rw.db"))
    return {t.name for t in probe._tool_manager.list_tools()}


def test_g5_every_refusal_names_a_fix_that_exists_on_the_surface_it_reaches(
    client_and_org, tmp_path: Path
) -> None:
    """G5 — every sentence the marketing modules refuse in names an action,
    and that action exists on every surface the refusal can reach.

    WHERE THIS GATE LOOKS: the ASTs of the four modules a marketing write can
    be refused BY (REFUSAL_MODULES) — every `raise ValueError`, every
    `HTTPException(detail=…)`, every string handed to a route's
    `refuse`/`refused`, the near-match card's `head`, and every refusal
    RETURNED as a value rather than raised (`_market_named` hands its miss back
    as a `str`, and `consistency.order_refusal` returns the sentence; a walk
    that read only `raise` would have missed both, and one of them is the C7
    fix this round just landed). A refusal added to any of them is walked on
    the commit that adds it and fails here until somebody says what fix it
    names. The named fixes are then checked against the REAL surfaces: web
    paths off the app's routers, MCP tools off the registrar.

    WHERE IT CANNOT LOOK: (a) a refusal raised in a module not listed in
    REFUSAL_MODULES — `money.py`, `forms/spec.py` and `repo/lines.py` all
    refuse into these cells and are held by their own tests, but a NEW shared
    module refusing into marketing would go unwalked; (b) a sentence assembled
    at runtime, of which only the literal frame is seen — rapidfuzz's
    nearest-match hint and every `{value}` are interpolated away, so a fix
    named ONLY inside an interpolation is invisible here; (c) whether the
    named fix actually HELPS — that a route exists is not proof it is
    reachable from where the reader is standing, which is a judgment no test
    makes.
    """
    client, _ = client_and_org
    paths = _web_paths(client.app)
    tools = _mcp_tools(tmp_path)

    failures: list[str] = []
    seen: set[str] = set()

    for relative, surfaces in sorted(REFUSAL_MODULES.items()):
        for sentence in _refusal_strings(relative):
            key = next((k for k in _KEYS if k in sentence), None)
            if key is None:
                failures.append(
                    f"{relative}: refusal {sentence[:70]!r}… has no entry in "
                    f"NAMED_FIX — say what fix its words name"
                )
                continue
            seen.add(key)
            fix = NAMED_FIX[key]
            if "field" in fix or "passthrough" in fix:
                # Retyping a value the caller already holds is reachable from
                # any surface; a relabelled sentence is another module's fix.
                continue
            for surface in surfaces:
                if surface not in fix:
                    failures.append(
                        f"{relative}: refusal {sentence[:60]!r}… reaches the "
                        f"{surface} surface and NAMED_FIX says nothing about "
                        f"how the fix is reached there"
                    )
                    continue
                target = fix[surface]
                if surface == "web" and target not in paths:
                    failures.append(
                        f"{relative}: refusal {sentence[:60]!r}… names a web "
                        f"fix at {target!r}, which the app does not serve"
                    )
                if surface == "mcp" and target not in tools:
                    failures.append(
                        f"{relative}: refusal {sentence[:60]!r}… names the fix "
                        f"{target!r} on MCP, and no such tool is registered — "
                        f"a refusal must never name a fix that does not exist"
                    )

    for key in sorted(NAMED_FIX):
        if key not in seen:
            failures.append(
                f"NAMED_FIX declares {key!r}, and no refusal in the walked "
                f"modules says it any more"
            )

    _named(
        failures,
        "a refusal names a fix that is not reachable from the surface it was "
        "raised on:",
    )


# ===========================================================================
# G6. A DATE THAT WITNESSES AN ACT IS REFUSED IN THE FUTURE.
# ===========================================================================
#
# `parse_human_date` FUTURE-BIASES: "aug 5" typed on 14 August 2026 is read as
# 5 August 2027, and every surface here accepts a human date. `sent_on` gained
# `consistency.check_not_future` on 2026-08-25; `responded_on`, the cell one
# column to the right of it on the same row, did not, and stored 2027 in
# silence (D2, 2026-08-26). That is G1's shape one field over — a rule applied
# at one site and not the adjacent one — and it is a gate for the same reason.

# Every date on the marketing surfaces that RECORDS SOMETHING AS HAVING
# HAPPENED, with the act it witnesses. A date in this table must be refused in
# the future on every surface that can write it.
WITNESS_DATES: dict[tuple[str, str], str] = {
    ("market_response", "responded_on"): "the day a market answered",
    ("market_approach", "sent_on"): "the day a package went to the market",
}

# Dates that legitimately look FORWARD, each with the reason — a quote expires
# next month, a policy period runs a year out, a task is due next week. None on
# these three tuples today; the table exists so the next one is DECLARED rather
# than silently exempted by nobody noticing it.
FORWARD_LOOKING: dict[tuple[str, str], str] = {}


def _date_sites() -> set[tuple[str, str]]:
    """Every editable date field on the marketing surfaces, off the same three
    Field tuples G1 walks — never a list written here."""
    return {
        (record, field.key)
        for record, fields in (
            ("market_response", MARKET_RESPONSE_FIELDS),
            ("placement_line", placement_line_fields()),
            ("market_approach", MARKET_APPROACH_FIELDS),
        )
        for field in fields
        if field.kind == "date"
    }


def test_g6_a_date_that_witnesses_an_act_is_refused_in_the_future(
    client_and_org, conn
) -> None:
    """G6 — every date on the marketing surfaces that records something as
    having HAPPENED is refused when it has not happened yet, on the web AND on
    MCP, and the one that is checked in a service is checked for BOTH.

    WHERE THIS GATE LOOKS: the same three Field tuples G1 walks, filtered to
    `kind == "date"`, so a date field added to any marketing surface is walked
    on the commit that adds it and fails here until somebody says whether it
    witnesses an act or looks forward. Every declared witness is then driven
    through EVERY door that can write it — for `sent_on` that is three (the
    add-market row, the Sent cell, and MCP's `market_approach`), and the third
    of them, `submission_sent_on`, did not exist until this round. It also
    holds `services.marketing_entry.WITNESS_DATES` against this table, because
    the fix for `responded_on` is a SERVICE both surfaces share and a table
    only one of them reads is the copy that differs.

    WHERE IT CANNOT LOOK: (a) an importer — the same gap G1 names, and the
    same one to widen when a marketing import lands; (b) a date stored on a
    marketing table with no editable Field behind it (`quote_expires_on` is
    reached through the pipeline's own form, is FORWARD-looking, and is held
    by `consistency.check_quote_dates`); (c) the wall clock itself — every
    door here reads `date.today()`, so this drives a date a year out rather
    than trying to move time.
    """
    client, org = client_and_org
    placement = _linked(client, org)
    wconn = client.app.state.conn
    from bookkit.repo import marketing as marketing_repo
    from bookkit.services import marketing_entry

    sites = _date_sites()
    undeclared = [
        f"{record}.{key} is a date on a marketing surface and is in neither "
        f"WITNESS_DATES nor FORWARD_LOOKING — say which it is"
        for (record, key) in sorted(sites)
        if (record, key) not in WITNESS_DATES
        and (record, key) not in FORWARD_LOOKING
    ]
    stale = [
        f"{record}.{key} is declared here and is no longer a date field on any "
        f"marketing surface"
        for (record, key) in sorted(set(WITNESS_DATES) | set(FORWARD_LOOKING))
        if (record, key) not in sites
    ]
    _named(undeclared + stale, "the witness-date declaration is out of step:")

    # THE SERVICE'S OWN TABLE IS THE SAME TABLE. `marketing_entry.responded`
    # walks WITNESS_DATES to decide what to check, so a date declared here and
    # missing there is a rule this gate believes in and the code does not.
    declared_on_response = {
        key for (record, key) in WITNESS_DATES if record == "market_response"
    }
    if set(marketing_entry.WITNESS_DATES) != declared_on_response:
        raise AssertionError(
            f"services.marketing_entry.WITNESS_DATES is "
            f"{sorted(marketing_entry.WITNESS_DATES)} and this gate declares "
            f"{sorted(declared_on_response)} — one home, one table"
        )

    # A YEAR OUT, which is what a future-biased "aug 5" produces in August.
    future = (date.today() + timedelta(days=365)).isoformat()
    base = f"/accounts/{org.ref}/program/{placement.id}/marketing"
    marketing_repo.set_placement_line(wconn, placement.id, GL)
    row = _approach(wconn, placement.id, _market(wconn, "Travelers"), line_id=GL)

    # EVERY DOOR, named, so a red line says which one let it through.
    web: dict[tuple[str, str], list[tuple[str, str, dict[str, str]]]] = {
        ("market_response", "responded_on"): [
            (
                "the Replied cell",
                f"{base}/responses/{row.id}/cell/responded_on",
                {"responded_on": future},
            ),
        ],
        ("market_approach", "sent_on"): [
            (
                "the add-market row",
                f"{base}/lines/{GL}/approaches",
                {"market": "Berkley", "status": "pending", "sent_on": future},
            ),
            ("the Sent cell", f"{base}/responses/{row.id}/sent", {"sent_on": future}),
        ],
    }

    failures: list[str] = []
    for site in sorted(WITNESS_DATES):
        doors = web.get(site)
        if not doors:
            failures.append(
                f"{site[0]}.{site[1]} witnesses an act and this gate drives no "
                f"web door for it — name the route, or the walk is not asking"
            )
            continue
        for what, url, data in doors:
            got = client.post(url, data=data)
            if not _refused(got):
                failures.append(
                    f"web {site[0]}.{site[1]}: {what} accepted {future} "
                    f"(HTTP {got.status_code}) — a date that records what "
                    f"already happened was stored in the future"
                )

    # --- MCP: the same question at the assistant's door --------------------
    _, mcp_placement = _mcp_book(conn)
    market = _market(conn, "Berkley")
    approach = mcpserver._market_approach(
        conn, mcp_placement.ref, GL, market=market.name
    )
    mcp: dict[tuple[str, str], list[tuple[str, Any]]] = {
        ("market_response", "responded_on"): [
            (
                "market_responded",
                lambda: mcpserver._market_responded(
                    conn, approach["response_id"], responded_on=future
                ),
            ),
        ],
        ("market_approach", "sent_on"): [
            (
                "market_approach",
                lambda: mcpserver._market_approach(
                    conn, mcp_placement.ref, AUTO, market=market.name,
                    sent_on=future,
                ),
            ),
            (
                "submission_sent_on",
                lambda: mcpserver._submission_sent_on(
                    conn, approach["response_id"], future
                ),
            ),
        ],
    }
    for site in sorted(WITNESS_DATES):
        doors = mcp.get(site)
        if not doors:
            failures.append(
                f"{site[0]}.{site[1]} witnesses an act and this gate drives no "
                f"MCP door for it — name the tool, or say there is none"
            )
            continue
        for tool, call in doors:
            try:
                call()
            except ValueError:
                continue  # refused, which is the rule
            failures.append(
                f"mcp {site[0]}.{site[1]}: {tool} accepted {future} — the "
                f"assistant can record an act as having happened next year"
            )

    _named(
        failures,
        "a date that records what already happened was accepted in the future "
        "(parse_human_date reads a bare month and day as NEXT year):",
    )
