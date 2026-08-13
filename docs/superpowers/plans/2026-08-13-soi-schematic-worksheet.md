# SOI Schematic Worksheet (towerkit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An optional second worksheet in the SOI export workbook that renders the insurance schematic — the tower — as real cells and merged ranges, themed identically to the graphic schematic (Marsh theme et al.), toggled from the existing render-options flow, persisted with the program, default OFF. With the option OFF the SOI workbook stays byte-identical to today's output.

**Architecture:** One geometry, two renderers. `layout.py::build_layout` stays the single source of tower geometry; the new `render/schematic_xlsx.py` consumes the SAME `TowerLayout` the graphic does and only quantizes its normalized coordinates onto a sheet grid — no tower math in cell space. Label/color decisions currently inline in `mpl_program.py` move to a small pure module (`render/labels.py`) both renderers call. `table_xlsx.py` splits into a per-sheet body plus a single finalize (pin properties + normalize ONCE) so one workbook can carry the SOI sheet and the schematic sheet; `write_table`'s standalone behavior and bytes are unchanged, guarded by `test_refactor_golden_content`. The composition path (`render_table_sheet` + `render_soi_sheet` + `finalize_workbook` + `sanitize_sheet_title`) is PUBLIC, STABLE API with a second downstream consumer already committed: bookkit's open-items export will compose a three-sheet workbook in a later bookkit-side phase — table sheet ("Open Items"), table sheet ("Projects"), SOI sheet ("Schedule of Insurance", all of a client's linked programs' `SoiSection`s concatenated) — then finalize once. So BOTH one-shot writers become thin wrappers: `write_table` over `render_table_sheet`, `write_soi` over `render_soi_sheet`; a caller creates a workbook, adds N styled sheets, optionally adds a schematic sheet, then pins + normalizes exactly once. The toggle rides `RenderSettings` (program JSON) → JSON schema → canonical dump → `RenderOptionsModal` → `TowerkitApp` attrs → `action_export_soi` → `towerctl soi --schematic`.

**Tech Stack:** Python 3.11+, openpyxl, pydantic (program model), Textual TUI, pytest/pytest-asyncio, mypy, ruff, uv.

## Global Constraints

- towerkit-only. All work at `/Users/grantgreeson/Developer/towerkit`, on branch `feat/soi-schematic` (created in Task 1). Nothing in bookkit changes.
- Gates before EVERY commit: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests` — run separately. Never pipe test output before a `&&` gate; redirect to a file, gate on the command, tail the file after. (A standalone `pytest ... 2>&1 | tail` used only to READ output, with no `&&` behind it, is fine.)
- CRITICAL — CONCURRENT CONSUMER: bookkit imports towerkit as an editable path dep and runs its own test workflows CONCURRENTLY with this plan's execution. Every commit must leave `write_table` and the whole public render API backward-compatible. Prefer additive commits. The golden guard (`tests/test_soi_xlsx.py::test_refactor_golden_content`, `GOLDEN_SHA` at test_soi_xlsx.py:152) must be green at EVERY commit and `GOLDEN_SHA` is never edited to make a refactor pass — it hashes zip content EXCLUDING `docProps/core.xml` (provenance embeds git state).
- Determinism: no wall clock anywhere in the writers; two identical runs are byte-identical; `include_schematic=False` output is byte-identical to the current SOI workbook (asserted, not assumed).
- Tests asserting time math never hardcode a calendar date as "today". (Nothing here reads today at all — program periods are data; keep it that way.)
- Data safety: the only persistent-format change is one additive OPTIONAL key (`render.soiSchematic`) in program JSON — no migration, no rewrite of existing files. The canonical dump emits it only when true (the `followsUnderlying` pattern, model.py:298), so an untouched program re-saves byte-identically. Nothing destructive; no backup machinery needed beyond git.
- Money display goes through `towerkit.money` (`format_money_compact`, `format_share`, `premium_share`) — never hand-formatted. Theme colors only — no color literals in the schematic renderer (font SIZES are a typography scale and may be constants, exactly as `mpl_program.py` already does).

**Spec ambiguities resolved during planning** (call out to Grant only if he objects):
1. A `ParticipantBlock` with non-contiguous runs becomes one merged range PER RECT (a single Excel merge cannot be non-contiguous); the label rides the widest rect, mirroring the graphic's label placement.
2. "One worksheet column group per layout Column" is realized by quantizing the x-axis by BOUNDARY: every distinct rect/column edge becomes a worksheet column edge, and each interval's column width is exactly proportional to its x-extent. Share splits land exactly (layout boundaries are shared bit-identical floats), so merges tile with no gaps/overlaps by construction and no x rounding at all.
3. "Ceil, min 1 row" is realized as boundary snapping with strict monotonicity (each boundary at least one row above the previous). Independent per-block ceil cannot coexist with the spec's boundary-alignment requirement; snapping boundaries gives both.
4. Block premium labels: shown whenever the layer has a premium and `show_premiums` is on — the worksheet always mirrors the graphic's `cell_premiums=True` form (the spec's "label = carrier (share) and premium"), not the graphic's per-cell toggle.
5. `soiSchematic` is emitted in canonical JSON only when true, so older towerkit wheels (prod machine) only reject files that actually use the feature.
6. CLI: `towerctl soi` gains `--schematic` and honors stored `render.soiSchematic`. No `--gamma` on `soi` in v1 — the schematic uses `DEFAULT_GAMMA`, same as the editor's `r` render.
7. Not mirrored in v1: the heavy zero line (cell borders can't cleanly overlay merged ranges; retention fills + axis labels mark it), footnote markers, and the sublimit footer (the SOI sheet already carries that prose).
8. Axis column: each y-interval gets a vertically merged cell in column A labeled with its FLOOR attachment ($0 silent, like the chart gutter); the tower top is implicit in the top layer's heading ("$25M xs $27M").
9. Schematic sheet title: `sanitize_sheet_title(f"{program.program} Schematic")`.

---

### Task 1: extract the shared block-label authority (`render/labels.py`)

The graphic's label text decisions (layer headings, carrier+share, unplaced/pending, retention, group roll-ups, premium-per-block, which block carries the heading) are inline in `mpl_program.py`. The worksheet must quote them exactly, so extract them as pure functions; the graphic delegates. Carrier COLOR assignment is already shared (`Theme.carrier_colours`, theme.py:60) — no extraction needed there.

**Files:**
- Create: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/labels.py`
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/mpl_program.py`
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_labels.py`

**Interfaces:**
- Produces (consumed by Task 4 and by mpl_program):
  - `layer_terms(attach: int, limit: int) -> str` (moved verbatim from mpl_program.py:232)
  - `layer_heading(layer: LayerBlock, follows: bool, marker: str = "") -> str`
  - `participant_label(carrier: str, share_bps: int) -> str`
  - `unplaced_label(share_bps: int, pending: bool) -> str`
  - `retention_label(type_: str, amount: int, vehicle: str | None) -> str`
  - `group_label(band: GroupBand) -> str`
  - `block_premium_label(layer_premium: int | None, share_bps: int) -> str | None`
  - `heading_blocks(participants: Sequence[ParticipantBlock]) -> dict[str, int]`

- [ ] **Step 0: Branch**

```bash
cd /Users/grantgreeson/Developer/towerkit && git checkout -b feat/soi-schematic
```

- [ ] **Step 1: Write failing tests**

`tests/test_labels.py`:

```python
"""Shared block-label authority: both renderers must quote these exactly."""

from towerkit.layout import LayerBlock, ParticipantBlock, Rect
from towerkit.render.labels import (
    block_premium_label,
    heading_blocks,
    layer_heading,
    layer_terms,
    participant_label,
    retention_label,
    unplaced_label,
)


def _layer(name: str, attach: int, limit: int) -> LayerBlock:
    return LayerBlock(
        layer_id="x", name=name, attach=attach, limit=limit, premium=None,
        signed_bps=10_000, y0=0.0, y1=1.0, outlines=(),
    )


def test_layer_terms_market_convention() -> None:
    assert layer_terms(0, 5_000_000) == "$5M"          # primary: never "xs $0"
    assert layer_terms(2_000_000, 25_000_000) == "$25M xs $2M"


def test_layer_heading_matches_graphic() -> None:
    excess = _layer("1st Excess", 1_000_000, 4_000_000)
    assert layer_heading(excess, follows=False) == "1st Excess — $4M xs $1M"
    assert layer_heading(excess, follows=True) == "1st Excess — $4M xs underlying"
    assert layer_heading(excess, follows=False, marker="¹") == "1st Excess¹ — $4M xs $1M"
    assert layer_heading(_layer("Primary", 0, 5_000_000), follows=False) == "Primary — $5M"


def test_participant_and_unplaced_labels() -> None:
    assert participant_label("Zenith", 10_000) == "Zenith 100%"
    assert unplaced_label(4_000, pending=False) == "40% open"
    assert unplaced_label(10_000, pending=True) == "To be placed"


def test_retention_label() -> None:
    assert retention_label("sir", 250_000, None) == "SIR $250K"
    assert retention_label("captive", 1_000_000, "Atomic Re") == "CAPTIVE $1M (Atomic Re)"


def test_block_premium_label() -> None:
    assert block_premium_label(None, 5_000) is None
    assert block_premium_label(100_000, 5_000) == "$50K"


def test_heading_rides_the_widest_block() -> None:
    def blk(layer_id: str, width: float) -> ParticipantBlock:
        return ParticipantBlock(
            layer_id=layer_id, carrier="C", share_bps=1,
            rects=(Rect(0.0, 0.0, width, 1.0),),
        )

    blocks = (blk("a", 0.2), blk("a", 0.7), blk("b", 1.0))
    assert heading_blocks(blocks) == {"a": 1, "b": 2}
```

The expected strings quote the CURRENT graphic (mpl_program.py:104-113 headings, :274 carrier+share, :262-266 unplaced, :188-190 retention, :222-224 group roll-up). If any assertion surprises you, check reality first — `uv run python -c "from towerkit.money import format_share, format_money_compact; print(format_share(10_000), format_money_compact(250_000))"` — and fix the TEST to match the graphic's actual output, never the other way.

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/grantgreeson/Developer/towerkit && uv run pytest tests/test_labels.py 2>&1 | tail -3`
Expected: FAIL — `ModuleNotFoundError: towerkit.render.labels`.

- [ ] **Step 3: Create `render/labels.py`**

```python
"""Shared label text for tower blocks — the single authority both renderers
(the matplotlib graphic and the xlsx schematic worksheet) quote, so a block
reads identically on the chart and in the cells. Pure: layout + money only."""

from __future__ import annotations

from collections.abc import Sequence

from ..layout import GroupBand, LayerBlock, ParticipantBlock
from ..money import format_money_compact, format_share, premium_share


def layer_terms(attach: int, limit: int) -> str:
    """Market convention: a primary is quoted by its limit alone — 'xs $0'
    is meaningless and reads as an error on a chart."""
    if attach > 0:
        return f"{format_money_compact(limit)} xs {format_money_compact(attach)}"
    return format_money_compact(limit)


def layer_heading(layer: LayerBlock, follows: bool, marker: str = "") -> str:
    terms = (
        f"{format_money_compact(layer.limit)} xs underlying"
        if follows
        else layer_terms(layer.attach, layer.limit)
    )
    return f"{layer.name}{marker} — {terms}"


def participant_label(carrier: str, share_bps: int) -> str:
    return f"{carrier} {format_share(share_bps)}"


def unplaced_label(share_bps: int, pending: bool) -> str:
    """Pending layer (nothing signed): 'To be placed'. Partially-open
    remainder: the open share."""
    return "To be placed" if pending else f"{format_share(share_bps)} open"


def retention_label(type_: str, amount: int, vehicle: str | None) -> str:
    label = f"{type_.upper()} {format_money_compact(amount)}"
    return f"{label} ({vehicle})" if vehicle else label


def group_label(band: GroupBand) -> str:
    rollup = f"{band.label} — Limit {format_money_compact(band.limit)}"
    if band.premium:
        rollup += f" · Premium {format_money_compact(band.premium)}"
    return rollup


def block_premium_label(layer_premium: int | None, share_bps: int) -> str | None:
    if layer_premium is None:
        return None
    return format_money_compact(premium_share(layer_premium, share_bps))


def heading_blocks(participants: Sequence[ParticipantBlock]) -> dict[str, int]:
    """Which block of each layer carries the layer heading: the WIDEST — a
    narrow lead share must not doom the name (graphic rule, kept verbatim)."""

    def width(block: ParticipantBlock) -> float:
        return max((r.width for r in block.rects), default=0.0)

    best: dict[str, int] = {}
    for index, block in enumerate(participants):
        current = best.get(block.layer_id)
        if current is None or width(block) > width(participants[current]):
            best[block.layer_id] = index
    return best
```

- [ ] **Step 4: Delegate in `mpl_program.py`** (behavior-identical refactor)

Read mpl_program.py:97-330 before editing. Changes, precisely:
- Delete the module's own `layer_terms` (mpl_program.py:232-237) and add `from .labels import layer_terms as layer_terms` (the `as` re-export keeps `tests/test_render.py:148`'s import of `towerkit.render.mpl_program.layer_terms` working and is ruff-clean). Also import `block_premium_label, group_label, heading_blocks, layer_heading, participant_label, retention_label, unplaced_label`.
- `titles` dict (:103-113) becomes:
  ```python
  titles = {
      layer.layer_id: layer_heading(
          layer,
          follows=layer.layer_id in follows,
          marker=(note_markers or {}).get(layer.layer_id, ""),
      )
      for layer in tower.layers
  }
  ```
- The `_width` closure + `heading_block` loop (:122-128) become `heading_block = heading_blocks(tower.participants)`.
- In the participant loop (:147-151): replace the `premium: int | None` computation with a text one — `premium = block_premium_label(owner.premium, block.share_bps)` guarded by the same `cell_premiums and block.carrier is not None` gate — and change `_participant_label`'s parameter to `premium: str | None`, using it directly as `prem_line` (delete `prem_line = format_money_compact(premium) ...` at :288). `premium_share`/`format_money_compact` imports drop out of the top import list if now unused.
- Retention label (:188-190) becomes `label = retention_label(ret.type, ret.amount, ret.vehicle)` (the `_fit_text` fallback list at :198-202 stays).
- In `_participant_label`: `body = unplaced_label(block.share_bps, pending)` replaces the pending/open branch strings (:262-266); `full = participant_label(block.carrier, block.share_bps)` replaces `share = ...; full = f"..."` (:273-274 — `format_share` may still be needed nowhere; remove dead imports).
- Group roll-up (:222-224) becomes `rollup = group_label(band)`.

- [ ] **Step 5: Run the suite** — the render tests are the behavior verdict

Run: `uv run pytest tests/test_labels.py tests/test_render.py -q 2>&1 | tail -3` then `uv run pytest -q 2>&1 | tail -3`
Expected: all pass (test_render's determinism/output suite proves the graphic still renders; the label unit tests pin the strings).

- [ ] **Step 6: Gates and commit**

Run separately: `uv run pytest -q > /tmp/t1.log; tail -3 /tmp/t1.log` · `uv run mypy src` · `uv run ruff check src tests`

```bash
git add src/towerkit/render/labels.py src/towerkit/render/mpl_program.py tests/test_labels.py
git commit -m "render: extract shared block-label authority (labels.py); graphic delegates"
```

---

### Task 2: boundary quantizer — layout space onto the sheet grid

Pure geometry, no openpyxl: map the layout's normalized gamma-scaled y-space onto ~100 uniform thin rows, and the x-space onto exact proportional columns. Boundary snapping (not per-block spans) is what makes adjacent blocks share row edges: layout guarantees neighbours share bit-identical floats (layout.py:9-13), so they share a snapped row by construction.

**Files:**
- Create: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/schematic_xlsx.py` (pure half only)
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_schematic_xlsx.py`

**Interfaces:**
- Produces (consumed by Task 4 and its tests):
  - Constants: `TOTAL_ROWS = 100`, `GRID_ROW_HEIGHT = 4.0`, `X_CHARS_PER_UNIT = 10.0`, `AXIS_COL = 1`, `AXIS_WIDTH = 10.0`, `FIRST_GRID_COL = 2`, `TITLE_ROW = 1`, `GROUP_ROW = 2`, `LINE_ROW = 3`, `FIRST_GRID_ROW = 4`
  - `quantize_boundaries(ys: Sequence[float], total_rows: int = TOTAL_ROWS) -> dict[float, int]`
  - `x_boundaries(layout: TowerLayout) -> tuple[float, ...]`
  - `y_boundaries(layout: TowerLayout) -> tuple[float, ...]`
  - `sheet_rows(rows: dict[float, int], y0: float, y1: float) -> tuple[int, int]` (inclusive top/bottom worksheet rows)

- [ ] **Step 1: Write failing tests**

`tests/test_schematic_xlsx.py`:

```python
"""Schematic worksheet: quantizer first (pure), then cell content (Task 4)."""

from towerkit.layout import build_layout
from towerkit.model import Layer, Line, Participant, Period, Placement, Program
from towerkit.render.schematic_xlsx import (
    FIRST_GRID_COL,
    FIRST_GRID_ROW,
    quantize_boundaries,
    sheet_rows,
    x_boundaries,
    y_boundaries,
)
from datetime import date


def _mini_program() -> Program:
    """One line, a $1M primary under a $25M excess split 60/40."""
    return Program(
        insured="T", program="T", placement=Placement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[Line(id="gl", name="General Liability")],
        layers=[
            Layer(id="p", name="Primary", applies_to=["gl"], attach=0,
                  limit=1_000_000,
                  participants=[Participant(carrier="A", share_bps=10_000)]),
            Layer(id="x", name="Excess", applies_to=["gl"], attach=1_000_000,
                  limit=25_000_000,
                  participants=[Participant(carrier="B", share_bps=6_000),
                                Participant(carrier="C", share_bps=4_000)]),
        ],
    )


class TestQuantizeBoundaries:
    def test_full_coverage_and_proportionality(self) -> None:
        rows = quantize_boundaries([0.0, 0.1, 1.0], total_rows=100)
        assert rows[0.0] == 0 and rows[0.1] == 10 and rows[1.0] == 100

    def test_min_one_row_floor_is_strict_monotonicity(self) -> None:
        rows = quantize_boundaries([0.0, 0.001, 0.002, 1.0], total_rows=100)
        assert rows[0.001] == 1 and rows[0.002] == 2

    def test_negative_retention_band_shares_the_grid(self) -> None:
        rows = quantize_boundaries([-0.18, 0.0, 1.0], total_rows=100)
        assert rows[-0.18] == 0 and rows[1.0] == 100

    def test_adjacent_spans_tile_exactly(self) -> None:
        rows = quantize_boundaries([0.0, 0.3, 1.0])
        below_top, below_bottom = sheet_rows(rows, 0.0, 0.3)
        above_top, above_bottom = sheet_rows(rows, 0.3, 1.0)
        assert above_top == FIRST_GRID_ROW            # y=1.0 is the first grid row
        assert above_bottom + 1 == below_top          # shared boundary, no gap
        assert below_bottom == FIRST_GRID_ROW + rows[1.0] - 1


class TestLayoutBoundaries:
    def test_x_boundaries_include_share_splits(self) -> None:
        xs = x_boundaries(build_layout(_mini_program()))
        assert xs == (0.0, 0.6, 1.0)                  # 60/40 split at exactly 0.6

    def test_y_boundaries_include_every_attachment_and_zero(self) -> None:
        layout = build_layout(_mini_program())
        ys = y_boundaries(layout)
        assert ys[0] == 0.0 and ys[-1] == 1.0
        assert layout.ymap.y(1_000_000) in ys         # the primary/excess boundary
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_schematic_xlsx.py 2>&1 | tail -3`
Expected: FAIL — `ModuleNotFoundError: towerkit.render.schematic_xlsx`.

- [ ] **Step 3: Create the pure half of `render/schematic_xlsx.py`**

```python
"""The tower as a worksheet: merged ranges over a quantized grid.

Geometry comes from layout.py — THE SAME TowerLayout the graphic renderer
consumes — so stacking, spans and proportions match the chart by
construction. This module only quantizes the layout's normalized
coordinates onto sheet rows/columns and paints merged ranges; no tower math
happens in cell space. Colors/fonts come from the Theme, label text from
render/labels.py — both shared with the graphic.

Quantization is per-BOUNDARY, not per-block: every distinct edge float in
the layout snaps to one integer row/column, and blocks look their edges up.
Neighbours share bit-identical edge floats (layout.py's exactness rule), so
merges tile with no gaps and no overlaps by construction."""

from __future__ import annotations

from collections.abc import Sequence

from ..layout import TowerLayout

TOTAL_ROWS = 100         # quantization target across the full y-span
GRID_ROW_HEIGHT = 4.0    # uniform thin rows: ~100 × 4pt ≈ one screen of tower
X_CHARS_PER_UNIT = 10.0  # a 1.0-wide line column ≈ 10 Excel character units
AXIS_COL = 1             # column A: attachment boundaries as money labels
AXIS_WIDTH = 10.0
FIRST_GRID_COL = 2
TITLE_ROW, GROUP_ROW, LINE_ROW = 1, 2, 3
FIRST_GRID_ROW = 4


def quantize_boundaries(
    ys: Sequence[float], total_rows: int = TOTAL_ROWS
) -> dict[float, int]:
    """Each distinct boundary → an integer row index, proportional over the
    full span, strictly increasing (the spec's ceil/min-1 floor: gamma
    compression already keeps small layers visible, so bumps are rare)."""
    distinct = sorted(set(ys))
    if len(distinct) < 2:
        return dict.fromkeys(distinct, 0)
    lo, span = distinct[0], distinct[-1] - distinct[0]
    out: dict[float, int] = {}
    prev = -1
    for y in distinct:
        row = max(round((y - lo) / span * total_rows), prev + 1)
        out[y] = row
        prev = row
    return out


def x_boundaries(layout: TowerLayout) -> tuple[float, ...]:
    """Every distinct vertical edge: column drawing extents plus every
    participant/retention rect edge (share boundaries split a line into
    share-proportional sub-columns — the 'column group' per line). Group
    bands use nominal column edges, which coincide with drawing extents at
    band boundaries (gutters only close WITHIN a group, layout.py:231-254),
    so they are covered too."""
    edges: set[float] = set()
    for column in layout.columns:
        edges.add(column.ex0)
        edges.add(column.ex1)
    for block in layout.participants:
        for rect in block.rects:
            edges.add(rect.x0)
            edges.add(rect.x1)
    for retention in layout.retentions:
        for rect in retention.rects:
            edges.add(rect.x0)
            edges.add(rect.x1)
    return tuple(sorted(edges))


def y_boundaries(layout: TowerLayout) -> tuple[float, ...]:
    """Every distinct horizontal edge, including the zero line, every
    ref-line (= every attachment breakpoint) and the retention band."""
    ys: set[float] = {0.0}
    ys.update(y for _, y in layout.ref_lines)
    for layer in layout.layers:
        for rect in layer.outlines:
            ys.add(rect.y0)
            ys.add(rect.y1)
    for block in layout.participants:
        for rect in block.rects:
            ys.add(rect.y0)
            ys.add(rect.y1)
    for retention in layout.retentions:
        for rect in retention.rects:
            ys.add(rect.y0)
            ys.add(rect.y1)
    return tuple(sorted(ys))


def sheet_rows(rows: dict[float, int], y0: float, y1: float) -> tuple[int, int]:
    """Inclusive (top_row, bottom_row) worksheet span for normalized
    [y0, y1]. Rows grow downward; y grows upward."""
    top = max(rows.values())
    return (FIRST_GRID_ROW + top - rows[y1], FIRST_GRID_ROW + top - rows[y0] - 1)
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_schematic_xlsx.py -q 2>&1 | tail -3` → PASS, then full suite.

- [ ] **Step 5: Gates and commit**

```bash
git add src/towerkit/render/schematic_xlsx.py tests/test_schematic_xlsx.py
git commit -m "schematic: boundary quantizer — layout y/x space onto a worksheet grid"
```

---

### Task 3: split `write_table` into a PUBLIC per-sheet body + single finalize

`write_table` (table_xlsx.py:88-172) owns the whole workbook lifecycle. Extract the sheet body and the pin-properties+normalize tail so a multi-sheet workbook can render N sheets and normalize ONCE. `write_table` keeps its exact behavior as a thin wrapper — the golden guard is the verdict.

The extracted functions are PUBLIC, STABLE API, not private helpers: besides the schematic (Tasks 4-5), bookkit's open-items export will use exactly this path to add a "Projects" worksheet to its workbook in a later bookkit-side phase. Treat their signatures below as a contract — additive evolution only, and document them in the module docstring as the multi-sheet composition entry points.

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/table_xlsx.py`
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_table_xlsx.py` (append)

**Interfaces:**
- Produces (PUBLIC API — consumed by Tasks 4-5 here and by bookkit's future multi-sheet open-items export; `write_table` itself unchanged):
  - `render_table_sheet(ws: Worksheet, columns: Sequence[TableColumn], sections: Sequence[TableSection], *, theme: Theme, row_height: Callable[[tuple[Any, ...]], float] | None = None) -> None` — renders the full write_table styling contract (header row, section bands, zebra, borders, freeze panes, column widths) onto ONE worksheet; it does NOT set the sheet title — naming each sheet is the caller's job, via `sanitize_sheet_title`
  - `finalize_workbook(wb: Workbook, out_path: Path) -> Path` — pin properties + save + `_normalize_zip`; called exactly ONCE per workbook, after every sheet (table or schematic) is rendered
  - `sanitize_sheet_title(title: str) -> str` — `_sanitize_sheet_title` renamed public (today it is only used inside this module, so the rename is safe); the sheet-title authority for every composition caller
- Multi-sheet caller recipe (the contract the test below exercises): `wb = Workbook()` → title + `render_table_sheet` per table sheet (first sheet reuses `wb.active`, later ones `wb.create_sheet`) → optionally `render_soi_sheet(...)` (Task 5) and/or `add_schematic_sheet(wb, ...)` (Task 4) → `finalize_workbook(wb, out_path)` once.

- [ ] **Step 1: Write failing tests** (append to `tests/test_table_xlsx.py`, which already imports `TableColumn`, `TableSection`, `write_table`, `load_theme`, `load_workbook` and defines `COLS`/`SECTIONS`/`theme` — reuse them):

```python
def test_two_sheet_workbook_single_normalize(theme, tmp_path: Path):
    from openpyxl import Workbook

    from towerkit.render.table_xlsx import finalize_workbook, render_table_sheet

    def build(path: Path) -> Path:
        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "First"
        render_table_sheet(ws, COLS, SECTIONS, theme=theme)
        second = wb.create_sheet("Second")
        render_table_sheet(second, COLS, SECTIONS, theme=theme)
        return finalize_workbook(wb, path)

    a = build(tmp_path / "a.xlsx")
    wb = load_workbook(a)
    assert wb.sheetnames == ["First", "Second"]
    assert [c.value for c in wb["Second"][1]] == ["Item", "Amount"]  # styled body on sheet 2
    assert wb["Second"].freeze_panes == "A2"
    # one finalize → still deterministic
    assert a.read_bytes() == build(tmp_path / "b.xlsx").read_bytes()


def test_finalize_pins_modified_timestamp(theme, tmp_path: Path):
    import zipfile

    from openpyxl import Workbook

    from towerkit.render.table_xlsx import finalize_workbook

    path = finalize_workbook(Workbook(), tmp_path / "p.xlsx")
    with zipfile.ZipFile(path) as z:
        core = z.read("docProps/core.xml").decode()
    assert "1980-01-01T00:00:00Z</dcterms:modified>" in core
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_table_xlsx.py 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'render_table_sheet'`.

- [ ] **Step 3: Refactor `table_xlsx.py`**

Add `from openpyxl.worksheet.worksheet import Worksheet` to the imports. Rename `_sanitize_sheet_title` → `sanitize_sheet_title` (docstring unchanged). Extend the MODULE docstring with the composition contract — that `render_table_sheet` + `finalize_workbook` are the public multi-sheet entry points (one finalize per workbook), with bookkit's open-items export named as a consumer. Then restructure `write_table` — the code inside moves VERBATIM, it only changes owners:

```python
def render_table_sheet(
    ws: Worksheet,
    columns: Sequence[TableColumn],
    sections: Sequence[TableSection],
    *,
    theme: Theme,
    row_height: Callable[[tuple[Any, ...]], float] | None = None,
) -> None:
    """The styled sheet body — everything write_table does between naming
    the sheet and pinning workbook properties, verbatim. Title is the
    caller's job so a multi-sheet workbook can name each sheet itself."""
    # ... current write_table body from `soi = theme.soi` (line 97) through
    # the section loop's final `row_ix += 1` (line 161), unchanged ...


def finalize_workbook(wb: Workbook, out_path: Path) -> Path:
    """Pin properties and normalize the archive — call exactly ONCE per
    workbook, after every sheet is rendered."""
    props = wb.properties
    props.creator = provenance()
    props.created = _PINNED
    props.modified = _PINNED
    props.lastModifiedBy = None
    buffer = BytesIO()
    wb.save(buffer)
    _normalize_zip(buffer.getvalue(), out_path)
    return out_path


def write_table(
    columns: Sequence[TableColumn],
    sections: Sequence[TableSection],
    *,
    title: str,
    theme: Theme,
    out_path: Path,
    row_height: Callable[[tuple[Any, ...]], float] | None = None,
) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sanitize_sheet_title(title)
    render_table_sheet(ws, columns, sections, theme=theme, row_height=row_height)
    return finalize_workbook(wb, out_path)
```

- [ ] **Step 4: Run the FULL suite — the golden guard is the verdict**

Run: `uv run pytest -q 2>&1 | tail -3`
Expected: all pass, `test_refactor_golden_content` included. If the golden fails: the extraction changed serialization order somewhere — diff `xl/styles.xml` of old vs new output and fix the refactor, NEVER the hash.

- [ ] **Step 5: Gates and commit**

```bash
git add src/towerkit/render/table_xlsx.py tests/test_table_xlsx.py
git commit -m "render: public per-sheet body + single finalize; write_table wraps it (byte-identical)"
```

---

### Task 4: the schematic sheet builder (`add_schematic_sheet`)

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/schematic_xlsx.py` (append the openpyxl half)
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_schematic_xlsx.py` (append)

**Interfaces:**
- Consumes: Task 1 labels, Task 2 quantizer, Task 3 `finalize_workbook`/`sanitize_sheet_title`/`_argb`, `layout.build_layout`, `Theme.carrier_colours`/`retention_fill`, `theme.contrast_text`.
- Produces (consumed by Task 5): `add_schematic_sheet(wb: Workbook, program: Program, theme: Theme, *, gamma: float = DEFAULT_GAMMA, show_premiums: bool = True) -> None`

- [ ] **Step 1: Write failing content tests** (append to `tests/test_schematic_xlsx.py`; the styles.xml reading technique generalizes `_row2_fill_hexes` from tests/test_soi_xlsx.py:39-66 — non-anchor merged cells don't expose fills through `load_workbook`):

```python
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from test_soi import make_program

from towerkit.render.schematic_xlsx import add_schematic_sheet
from towerkit.render.table_xlsx import finalize_workbook
from towerkit.theme import load_theme

REPO = Path(__file__).parent.parent
_SML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@pytest.fixture()
def marsh():
    return load_theme(REPO / "themes" / "marsh.json")


@pytest.fixture()
def program():
    return make_program()


def _write_schematic(program, theme, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "SOI"  # stand-in first sheet; Task 5 provides the real one
    add_schematic_sheet(wb, program, theme)
    return finalize_workbook(wb, path)


def _grid(program):
    layout = build_layout(program)
    rows = quantize_boundaries(y_boundaries(layout))
    xs = x_boundaries(layout)
    col_of = {x: FIRST_GRID_COL + i for i, x in enumerate(xs)}
    return layout, rows, col_of


def _fill_hex(xlsx_path: Path, sheet_xml: str, cell_ref: str) -> str | None:
    """ARGB fill of one cell, read from the saved XML (test_soi_xlsx.py's
    technique: openpyxl won't report styles on non-anchor merged cells)."""
    with zipfile.ZipFile(xlsx_path) as z:
        styles = ET.fromstring(z.read("xl/styles.xml"))
        sheet = ET.fromstring(z.read(f"xl/worksheets/{sheet_xml}"))
    fills = []
    for fill in styles.find(f"{_SML_NS}fills"):
        pattern = fill.find(f"{_SML_NS}patternFill")
        fg = pattern.find(f"{_SML_NS}fgColor") if pattern is not None else None
        fills.append(fg.get("rgb") if fg is not None else None)
    fill_ids = [int(xf.get("fillId", "0")) for xf in styles.find(f"{_SML_NS}cellXfs")]
    for cell in sheet.iter(f"{_SML_NS}c"):
        if cell.get("r") == cell_ref:
            return fills[fill_ids[int(cell.get("s", "0"))]]
    return None


class TestSchematicSheet:
    def test_sheet_appended_and_named(self, program, marsh, tmp_path):
        wb = load_workbook(_write_schematic(program, marsh, tmp_path / "s.xlsx"))
        assert wb.sheetnames == ["SOI", "Casualty Schematic"]

    def test_merged_ranges_never_overlap(self, program, marsh, tmp_path):
        ws = load_workbook(_write_schematic(program, marsh, tmp_path / "s.xlsx"))[
            "Casualty Schematic"
        ]
        bounds = [r.bounds for r in ws.merged_cells.ranges]  # (c0, r0, c1, r1)
        for i, a in enumerate(bounds):
            for b in bounds[i + 1 :]:
                disjoint = a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]
                assert disjoint, f"overlapping merges: {a} vs {b}"

    def test_block_merge_lands_exactly_where_the_layout_says(
        self, program, marsh, tmp_path
    ):
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        ws = load_workbook(path)["Casualty Schematic"]
        layout, rows, col_of = _grid(program)
        zenith = next(
            b for b in layout.participants
            if b.carrier == "Zenith" and b.layer_id == "gl-primary"
        )
        rect = zenith.rects[0]
        r0, r1 = sheet_rows(rows, rect.y0, rect.y1)
        c0, c1 = col_of[rect.x0], col_of[rect.x1] - 1
        ref = f"{get_column_letter(c0)}{r0}:{get_column_letter(c1)}{r1}"
        assert ref in {str(r) for r in ws.merged_cells.ranges}
        anchor = ws[f"{get_column_letter(c0)}{r0}"]
        assert "Zenith 100%" in anchor.value
        assert "Primary — $1M" in anchor.value      # heading rides the widest block
        assert "$50K" in anchor.value               # premium share, like the graphic
        # first-appearance palette assignment, same order as the graphic:
        assert anchor.fill.fgColor.rgb == "FF000F47"  # marsh carrierPalette[0]

    def test_merged_interior_cells_carry_the_fill(self, program, marsh, tmp_path):
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        layout, rows, col_of = _grid(program)
        gamma_blk = next(b for b in layout.participants if b.carrier == "Gamma")
        rect = gamma_blk.rects[0]
        r0, _ = sheet_rows(rows, rect.y0, rect.y1)
        c0 = col_of[rect.x0]
        interior = f"{get_column_letter(c0)}{r0 + 1}"  # prop-primary spans many rows
        assert _fill_hex(path, "sheet2.xml", interior) == "FF82BAFF"  # Gamma = palette[3]

    def test_proportions_boundaries_and_stacking(self, program, marsh, tmp_path):
        layout, rows, _ = _grid(program)

        def span(layer_id: str) -> tuple[int, int]:
            ly = next(l for l in layout.layers if l.layer_id == layer_id)
            return sheet_rows(rows, ly.y0, ly.y1)

        p_top, p_bottom = span("gl-primary")
        x_top, x_bottom = span("gl-x1")
        assert x_bottom + 1 == p_top                       # stacked flush, no gap
        prop_top, prop_bottom = span("prop-primary")
        assert (prop_bottom - prop_top) > (p_bottom - p_top)  # $10M towers over $1M

    def test_pending_retention_axis_and_lines(self, program, marsh, tmp_path):
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        ws = load_workbook(path)["Casualty Schematic"]
        values = {
            c.value for row in ws.iter_rows() for c in row if c.value is not None
        }
        assert any("To be placed" in v for v in values)     # al-primary is pending
        assert "SIR $250K" in values                        # retention label
        assert any(v == "$1M" for v in values)              # axis boundary label
        assert "General Liability" in values                # line header
        assert any(v.startswith("Casualty — Limit") for v in values)  # group band

    def test_retention_fill_is_the_typed_theme_fill(self, program, marsh, tmp_path):
        path = _write_schematic(program, marsh, tmp_path / "s.xlsx")
        layout, rows, col_of = _grid(program)
        sir = next(r for r in layout.retentions if r.type == "sir")
        rect = sir.rects[0]
        r0, _ = sheet_rows(rows, rect.y0, rect.y1)
        anchor = f"{get_column_letter(col_of[rect.x0])}{r0}"
        assert _fill_hex(path, "sheet2.xml", anchor) == "FFFFF3DA"  # marsh sir fill

    def test_two_writes_byte_identical(self, program, marsh, tmp_path):
        a = _write_schematic(program, marsh, tmp_path / "a.xlsx")
        b = _write_schematic(program, marsh, tmp_path / "b.xlsx")
        assert a.read_bytes() == b.read_bytes()
```

(`build_layout`, `quantize_boundaries`, `y_boundaries`, `x_boundaries`, `sheet_rows`, `FIRST_GRID_COL` are already imported at the top of this test file from Task 2.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_schematic_xlsx.py 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'add_schematic_sheet'`.

- [ ] **Step 3: Implement the sheet builder** (append to `schematic_xlsx.py`)

Add imports at the top: `from openpyxl import Workbook`, `from openpyxl.styles import Alignment, Border, Font, PatternFill, Side`, `from openpyxl.utils import get_column_letter`, `from openpyxl.worksheet.worksheet import Worksheet`, `from ..layout import Rect, build_layout` (extend the existing layout import), `from ..model import Program`, `from ..money import format_money_compact`, `from ..scale import DEFAULT_GAMMA`, `from ..theme import Chrome, Theme, contrast_text`, `from .labels import (block_premium_label, group_label, heading_blocks, layer_heading, participant_label, retention_label, unplaced_label)`, `from .table_xlsx import _argb, sanitize_sheet_title`.

```python
def add_schematic_sheet(
    wb: Workbook,
    program: Program,
    theme: Theme,
    *,
    gamma: float = DEFAULT_GAMMA,
    show_premiums: bool = True,
) -> None:
    """Append the tower as a worksheet of merged ranges. The caller owns the
    workbook lifecycle (finalize_workbook runs ONCE, after every sheet)."""
    layout = build_layout(program, gamma=gamma)
    chrome = theme.chrome
    colours = theme.carrier_colours(program.carriers())
    ws = wb.create_sheet(sanitize_sheet_title(f"{program.program} Schematic"))
    ws.sheet_view.showGridLines = False

    xs = x_boundaries(layout)
    rows = quantize_boundaries(y_boundaries(layout))
    top = max(rows.values())
    if len(xs) < 2 or top == 0:  # draft with no drawable tower: title only
        _title(ws, program, chrome, last_col=AXIS_COL)
        return
    col_of = {x: FIRST_GRID_COL + i for i, x in enumerate(xs)}
    last_col = FIRST_GRID_COL + len(xs) - 2

    ws.column_dimensions[get_column_letter(AXIS_COL)].width = AXIS_WIDTH
    for x_lo, x_hi in zip(xs, xs[1:], strict=False):
        letter = get_column_letter(col_of[x_lo])
        ws.column_dimensions[letter].width = (x_hi - x_lo) * X_CHARS_PER_UNIT
    for r in range(FIRST_GRID_ROW, FIRST_GRID_ROW + top):
        ws.row_dimensions[r].height = GRID_ROW_HEIGHT

    _title(ws, program, chrome, last_col=last_col)
    _headers(ws, layout, chrome, col_of)
    _axis(ws, layout, rows, chrome)

    pending = {ly.layer_id for ly in layout.layers if ly.signed_bps == 0}
    follows = {ly.id for ly in program.layers if ly.follows_underlying}
    headings = heading_blocks(layout.participants)
    layer_by_id = {ly.layer_id: ly for ly in layout.layers}

    for index, block in enumerate(layout.participants):
        lines: list[str] = []
        if headings.get(block.layer_id) == index:
            owner = layer_by_id[block.layer_id]
            lines.append(layer_heading(owner, follows=block.layer_id in follows))
        if block.carrier is None:
            is_pending = block.layer_id in pending
            lines.append(unplaced_label(block.share_bps, pending=is_pending))
            # graphic: pending = empty dashed outline; open remainder = hatch
            fill = (
                None
                if is_pending
                else PatternFill(
                    "lightUp",
                    fgColor=_argb(chrome.unplaced),
                    bgColor=_argb(chrome.background),
                )
            )
            text_colour = chrome.ink if is_pending else chrome.muted
            edge = Side(
                style="dashed" if is_pending else "thin",
                color=_argb(chrome.ink if is_pending else chrome.unplaced),
            )
        else:
            lines.append(participant_label(block.carrier, block.share_bps))
            if show_premiums:
                premium = block_premium_label(
                    layer_by_id[block.layer_id].premium, block.share_bps
                )
                if premium is not None:
                    lines.append(premium)
            face = colours[block.carrier]
            fill = PatternFill("solid", fgColor=_argb(face))
            text_colour = contrast_text(face, chrome.background, chrome.ink)
            edge = Side(style="thin", color=_argb(chrome.background))
        anchor_rect = max(block.rects, key=lambda r: r.width, default=None)
        for rect in block.rects:
            _block(
                ws, rect, rows, col_of,
                text="\n".join(lines) if rect is anchor_rect else "",
                fill=fill,
                border=Border(left=edge, right=edge, top=edge, bottom=edge),
                font=Font(name=chrome.font, size=8, color=_argb(text_colour)),
            )

    for retention in layout.retentions:
        fill = PatternFill("solid", fgColor=_argb(theme.retention_fill(retention.type)))
        edge = Side(style="thin", color=_argb(chrome.ink))
        for i, rect in enumerate(retention.rects):
            _block(
                ws, rect, rows, col_of,
                text=(
                    retention_label(retention.type, retention.amount, retention.vehicle)
                    if i == 0
                    else ""
                ),
                fill=fill,
                border=Border(left=edge, right=edge, top=edge, bottom=edge),
                font=Font(name=chrome.font, size=7, color=_argb(chrome.ink)),
            )


def _block(
    ws: Worksheet,
    rect: Rect,
    rows: dict[float, int],
    col_of: dict[float, int],
    *,
    text: str,
    fill: PatternFill | None,
    border: Border,
    font: Font,
) -> None:
    """One rect → one merged range. Style EVERY cell in the range before
    reading it back as a merge: openpyxl only renders non-anchor styles when
    they are set per-cell (the fills recipe test_soi_xlsx.py leans on)."""
    r0, r1 = sheet_rows(rows, rect.y0, rect.y1)
    c0, c1 = col_of[rect.x0], col_of[rect.x1] - 1
    anchor = ws.cell(row=r0, column=c0, value=text or None)
    anchor.font = font
    anchor.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            cell = ws.cell(row=r, column=c)
            if fill is not None:
                cell.fill = fill
            cell.border = border
    if (r0, c0) != (r1, c1):  # a 1×1 block is already one cell; no merge
        ws.merge_cells(start_row=r0, start_column=c0, end_row=r1, end_column=c1)


def _title(ws: Worksheet, program: Program, chrome: Chrome, *, last_col: int) -> None:
    period = f"{program.period.start.isoformat()} – {program.period.end.isoformat()}"
    cell = ws.cell(
        row=TITLE_ROW, column=AXIS_COL,
        value=f"{program.insured} — {program.program} · {period}",
    )
    cell.font = Font(
        name=chrome.title_font or chrome.font, size=12, color=_argb(chrome.ink)
    )
    cell.alignment = Alignment(horizontal="left", vertical="center")
    if last_col > AXIS_COL:
        ws.merge_cells(
            start_row=TITLE_ROW, start_column=AXIS_COL,
            end_row=TITLE_ROW, end_column=last_col,
        )
    ws.row_dimensions[TITLE_ROW].height = 22.0
    ws.row_dimensions[GROUP_ROW].height = 14.0
    ws.row_dimensions[LINE_ROW].height = 16.0


def _headers(
    ws: Worksheet, layout: TowerLayout, chrome: Chrome, col_of: dict[float, int]
) -> None:
    """Group bands above, line names under them (the spec's header order —
    the graphic puts both below the tower, a chart-only convention)."""
    accent = Side(style="medium", color=_argb(chrome.accent))
    for band in layout.groups:
        c0, c1 = col_of[band.x0], col_of[band.x1] - 1
        cell = ws.cell(row=GROUP_ROW, column=c0, value=group_label(band))
        cell.font = Font(name=chrome.font, size=8, color=_argb(chrome.accent))
        cell.alignment = Alignment(horizontal="center", vertical="center")
        for c in range(c0, c1 + 1):
            ws.cell(row=GROUP_ROW, column=c).border = Border(bottom=accent)
        if c1 > c0:
            ws.merge_cells(
                start_row=GROUP_ROW, start_column=c0, end_row=GROUP_ROW, end_column=c1
            )
    for column in layout.columns:
        c0, c1 = col_of[column.ex0], col_of[column.ex1] - 1
        cell = ws.cell(row=LINE_ROW, column=c0, value=column.name)
        cell.font = Font(name=chrome.font, size=9, bold=True, color=_argb(chrome.ink))
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if c1 > c0:
            ws.merge_cells(
                start_row=LINE_ROW, start_column=c0, end_row=LINE_ROW, end_column=c1
            )


def _axis(
    ws: Worksheet, layout: TowerLayout, rows: dict[float, int], chrome: Chrome
) -> None:
    """Column A: one merged cell per y-interval, labeled with its floor
    attachment, bottom-aligned — the label sits just above its boundary,
    like the chart's gutter labels. $0 stays silent."""
    ref = list(layout.ref_lines)  # (dollars, y), ascending by construction
    for (d_lo, y_lo), (_d_hi, y_hi) in zip(ref, ref[1:], strict=False):
        if d_lo <= 0:
            continue
        r0, r1 = sheet_rows(rows, y_lo, y_hi)
        cell = ws.cell(row=r0, column=AXIS_COL, value=format_money_compact(d_lo))
        cell.font = Font(name=chrome.font, size=7, color=_argb(chrome.muted))
        cell.alignment = Alignment(horizontal="right", vertical="bottom")
        if r1 > r0:
            ws.merge_cells(
                start_row=r0, start_column=AXIS_COL, end_row=r1, end_column=AXIS_COL
            )
```

(`Chrome` import comes from `..theme`. `sheet_rows(rows, y_lo, y_hi)` in `_axis` deliberately reuses the block-span helper: the interval between two boundaries IS a block span.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_schematic_xlsx.py -q 2>&1 | tail -5` → PASS, then the full suite. If a geometry test fails, debug against `_grid()`'s numbers, not by loosening assertions.

- [ ] **Step 5: Gates and commit**

```bash
git add src/towerkit/render/schematic_xlsx.py tests/test_schematic_xlsx.py
git commit -m "schematic: the tower as merged cells — same layout, theme colors"
```

---

### Task 5: public `render_soi_sheet` + `write_soi_workbook` — one normalize

The SOI sheet body becomes public per-sheet API, exactly like the generic table sheet: bookkit's three-sheet open-items workbook (Open Items + Projects + Schedule of Insurance) will call it with ALL of a client's linked programs' `SoiSection`s concatenated into one list (`SoiSection.label` is arbitrary text, so per-program label sections need no new type). `write_soi` becomes a thin wrapper, mirroring `write_table`.

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/soi_xlsx.py`
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_soi_xlsx.py` (append)

**Interfaces:**
- Consumes: Task 3 `render_table_sheet`/`finalize_workbook`/`sanitize_sheet_title`, Task 4 `add_schematic_sheet`, `soi.build_soi`/`sheet_title`.
- Produces (PUBLIC API):
  - `render_soi_sheet(ws: Worksheet, sections: list[SoiSection], *, theme: Theme, show_premiums: bool = True) -> None` — renders SOI columns + sections onto ONE worksheet of an open workbook (consumed here by `write_soi`/`write_soi_workbook`, downstream by bookkit's three-sheet composition). `show_premiums=False` drops the Premium column and section roll-ups, exactly as today; the SOI row-height heuristic (`_row_height` over the Limits/Retention prose columns, soi_xlsx.py:72-79) is applied internally — callers never pass it. Title is the caller's job (`sanitize_sheet_title`), matching `render_table_sheet`.
  - `write_soi_workbook(program: Program, *, theme: Theme, out_path: Path, show_premiums: bool = True, include_schematic: bool = False, gamma: float = DEFAULT_GAMMA) -> Path` (consumed by Tasks 7-8)
- `write_soi(sections, *, title, theme, out_path, show_premiums=True) -> Path` keeps its exact signature and bytes (golden guard).

- [ ] **Step 1: Write failing tests** (append to `tests/test_soi_xlsx.py`; `program`/`theme` fixtures, `_write`, `_content_hash`, `GOLDEN_SHA` already exist there):

```python
from towerkit.render.soi_xlsx import render_soi_sheet, write_soi_workbook


def test_render_soi_sheet_into_an_open_workbook(program, theme, tmp_path):
    """The bookkit composition shape: a table sheet plus an SOI sheet in one
    workbook, one finalize."""
    from openpyxl import Workbook

    from towerkit.render.table_xlsx import finalize_workbook, sanitize_sheet_title
    from towerkit.soi import build_soi

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Open Items"  # stand-in for bookkit's table sheet
    soi_ws = wb.create_sheet(sanitize_sheet_title("Schedule of Insurance"))
    render_soi_sheet(soi_ws, build_soi(program), theme=theme)
    path = finalize_workbook(wb, tmp_path / "multi.xlsx")
    loaded = load_workbook(path)
    assert loaded.sheetnames == ["Open Items", "Schedule of Insurance"]
    sheet = loaded["Schedule of Insurance"]
    assert [c.value for c in sheet[1]][:2] == ["Insured", "Line of Coverage"]
    assert sheet["A2"].value == "Casualty"          # section band renders
    assert sheet.freeze_panes == "A2"
    assert sheet.row_dimensions[3].height >= 36.0   # SOI row-height heuristic applied


class TestWorkbookOrchestration:
    def test_off_is_byte_identical_to_write_soi(self, program, theme, tmp_path):
        legacy = _write(program, theme, tmp_path / "legacy.xlsx")
        combined = write_soi_workbook(
            program, theme=theme, out_path=tmp_path / "combined.xlsx",
            include_schematic=False,
        )
        assert combined.read_bytes() == legacy.read_bytes()

    def test_off_still_matches_the_golden(self, program, theme, tmp_path):
        path = write_soi_workbook(program, theme=theme, out_path=tmp_path / "g.xlsx")
        assert _content_hash(path) == GOLDEN_SHA

    def test_on_appends_the_schematic_sheet(self, program, theme, tmp_path):
        path = write_soi_workbook(
            program, theme=theme, out_path=tmp_path / "s.xlsx", include_schematic=True
        )
        assert load_workbook(path).sheetnames == [
            "Casualty SOI - 26-27", "Casualty Schematic",
        ]

    def test_on_two_runs_byte_identical(self, program, theme, tmp_path):
        a = write_soi_workbook(
            program, theme=theme, out_path=tmp_path / "a.xlsx", include_schematic=True
        )
        b = write_soi_workbook(
            program, theme=theme, out_path=tmp_path / "b.xlsx", include_schematic=True
        )
        assert a.read_bytes() == b.read_bytes()


# Content golden WITH the schematic sheet (same core.xml-exclusion mechanism
# as GOLDEN_SHA above; same regeneration rule — deliberate change or
# openpyxl bump only, never to make a refactor pass).
SCHEMATIC_GOLDEN_SHA = "FILL_ME"


def test_schematic_golden_content(program, theme, tmp_path):
    path = write_soi_workbook(
        program, theme=theme, out_path=tmp_path / "sg.xlsx", include_schematic=True
    )
    assert _content_hash(path) == SCHEMATIC_GOLDEN_SHA
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_soi_xlsx.py -k "Orchestration or open_workbook" 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'render_soi_sheet'`.

- [ ] **Step 3: Implement in `soi_xlsx.py`**

Extract the body of `write_soi` (soi_xlsx.py:35-64: the column building and section mapping, verbatim) into a private helper, name the row-height lambda, add the public per-sheet function, and make both writers wrap it:

```python
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from ..model import Program
from ..scale import DEFAULT_GAMMA
from ..soi import SoiSection, build_soi, sheet_title
from .schematic_xlsx import add_schematic_sheet
from .table_xlsx import (
    TableColumn,
    TableSection,
    finalize_workbook,
    render_table_sheet,
    sanitize_sheet_title,
)


def _table_parts(
    sections: list[SoiSection], show_premiums: bool
) -> tuple[list[TableColumn], list[TableSection]]:
    """The SOI sheet as table-writer inputs — one mapping for every caller,
    so every SOI sheet body is IDENTICAL (the golden guard's premise)."""
    # ... lines 35-64 of the current write_soi, verbatim, ending
    # `return columns, table_sections` ...


def _soi_row_height(values: tuple[object, ...]) -> float:
    return _row_height(str(values[6]), str(values[7]))


def render_soi_sheet(
    ws: Worksheet,
    sections: list[SoiSection],
    *,
    theme: Theme,
    show_premiums: bool = True,
) -> None:
    """PUBLIC per-sheet API: the SOI body onto one worksheet of an open
    workbook (bookkit composes it beside its own table sheets and finalizes
    once). The prose-column row-height heuristic rides along; titling the
    sheet is the caller's job, like render_table_sheet."""
    columns, table_sections = _table_parts(sections, show_premiums)
    render_table_sheet(
        ws, columns, table_sections, theme=theme, row_height=_soi_row_height
    )


def write_soi(
    sections: list[SoiSection],
    *,
    title: str,
    theme: Theme,
    out_path: Path,
    show_premiums: bool = True,
) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sanitize_sheet_title(title)
    render_soi_sheet(ws, sections, theme=theme, show_premiums=show_premiums)
    return finalize_workbook(wb, out_path)


def write_soi_workbook(
    program: Program,
    *,
    theme: Theme,
    out_path: Path,
    show_premiums: bool = True,
    include_schematic: bool = False,
    gamma: float = DEFAULT_GAMMA,
) -> Path:
    """One workbook, normalized ONCE: the SOI sheet plus the schematic sheet
    when asked. include_schematic=False is byte-identical to write_soi by
    construction — same sheet body, same finalize."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sanitize_sheet_title(sheet_title(program))
    render_soi_sheet(ws, build_soi(program), theme=theme, show_premiums=show_premiums)
    if include_schematic:
        add_schematic_sheet(wb, program, theme, gamma=gamma, show_premiums=show_premiums)
    return finalize_workbook(wb, out_path)
```

(`write_soi` no longer imports `write_table` — drop the dead import. Its wrapper body is exactly what `write_table` did, so bytes are unchanged; the golden guard proves it. `write_soi_workbook` computing the same `sheet_title(program)` the callers pass today is what makes the OFF path byte-identical.)

- [ ] **Step 4: Fill the schematic golden**

Run: `uv run pytest tests/test_soi_xlsx.py::test_schematic_golden_content -v 2>&1 | tail -5`
Expected: FAIL with the actual sha256 in the assertion message. Copy it into `SCHEMATIC_GOLDEN_SHA`, rerun to green. Never touch `GOLDEN_SHA`.

- [ ] **Step 5: Run the full suite** — `uv run pytest -q 2>&1 | tail -3` → all pass, both goldens included.

- [ ] **Step 6: Gates and commit**

```bash
git add src/towerkit/render/soi_xlsx.py tests/test_soi_xlsx.py
git commit -m "soi: public render_soi_sheet + write_soi_workbook — N sheets, one normalize"
```

---

### Task 6: the `soiSchematic` render setting — model, schema, canonical dump, modal, app

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/model.py` (`RenderSettings` at :130, `_RENDER_KEYS` at :238, `program_to_jsonable` render block at :260)
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/schema/program.schema.json` (render.properties, :46-63)
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/tui/widgets/modals.py` (`RenderOptions` :92, `RenderOptionsModal` :102)
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/tui/app.py` (`TowerkitApp.__init__`, :30-33)
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_canonical.py` (append)

**Interfaces:**
- Produces (consumed by Task 7-8):
  - `RenderSettings.soi_schematic: bool` (alias `soiSchematic`, default `False`)
  - `RenderOptions.soi_schematic: bool = False` (frozen dataclass, new last field)
  - `RenderOptionsModal(current_theme, show_totals, show_premiums, cell_premiums=False, cell_dates=False, soi_schematic=False, themes_dir=None)` with Checkbox id `#opt-soi-schematic`
  - `TowerkitApp.soi_schematic: bool` (init `False`)

- [ ] **Step 1: Failing test** (append to `tests/test_canonical.py`, next to `test_render_settings_round_trip` at :74 — same imports and `SAMPLE`):

```python
def test_soi_schematic_round_trips_and_is_omitted_when_off(tmp_path) -> None:
    from towerkit.model import RenderSettings, dump_program
    from towerkit.validate import validate_file

    program = load_program(SAMPLE)
    program.render = RenderSettings(soi_schematic=True)
    target = tmp_path / "p.json"
    dump_program(program, target)
    assert '"soiSchematic": true' in target.read_text()
    reloaded = load_program(target)
    assert reloaded.render is not None and reloaded.render.soi_schematic is True
    _, diags = validate_file(target)
    assert diags.ok  # the schema accepts the new key

    # OFF is the default and is NOT written — existing files re-save unchanged
    program.render = RenderSettings()
    dump_program(program, target)
    assert "soiSchematic" not in target.read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_canonical.py -k soi_schematic 2>&1 | tail -3`
Expected: FAIL — unexpected keyword `soi_schematic` (pydantic).

- [ ] **Step 3: Implement**

`model.py` — `RenderSettings`, after `cell_dates`:

```python
    soi_schematic: bool = Field(alias="soiSchematic", default=False)
```

`_RENDER_KEYS` (:238) becomes `("theme", "showTotals", "showPremiums", "cellPremiums", "cellDates", "soiSchematic")`. In `program_to_jsonable`'s render dict (:262-268), after `"cellDates": ...` add:

```python
                    # emitted only when true (the followsUnderlying pattern):
                    # untouched programs re-save byte-identically, and older
                    # towerkit wheels only reject files that USE the feature
                    "soiSchematic": program.render.soi_schematic or None,
```

`program.schema.json` — inside `render.properties`, after `"cellDates"`:

```json
        "soiSchematic": {
          "type": "boolean"
        }
```

`modals.py` — `RenderOptions` gains `soi_schematic: bool = False` as the last field. `RenderOptionsModal.__init__` gains `soi_schematic: bool = False` between `cell_dates` and `themes_dir` (store `self.soi_schematic`). In `compose`, after the cell-dates Checkbox (:162-166):

```python
            yield Checkbox(
                "Include schematic worksheet in SOI export",
                self.soi_schematic,
                id="opt-soi-schematic",
            )
```

In `_result` (:189-195): `soi_schematic=self.query_one("#opt-soi-schematic", Checkbox).value`.

`app.py` — after `self.cell_dates = False` (:33): `self.soi_schematic = False`.

- [ ] **Step 4: Run** — `uv run pytest tests/test_canonical.py tests/test_tui.py -q 2>&1 | tail -3` → PASS (existing modal tests pass positional args before `themes_dir`, so the new keyword-defaulted parameter is compatible), then the full suite.

- [ ] **Step 5: Gates and commit**

```bash
git add src/towerkit/model.py src/towerkit/schema/program.schema.json src/towerkit/tui/widgets/modals.py src/towerkit/tui/app.py tests/test_canonical.py
git commit -m "model: soiSchematic render setting (schema, canonical dump, options modal)"
```

---

### Task 7: editor wiring — persisted toggle, export honors it

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/tui/screens/editor.py` (stored-options restore at :274-279, `action_render_options` at :1699, `action_export_soi` at :1729, `EDITOR_HELP` at :127-131)
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_tui.py` (append)

**Interfaces:**
- Consumes: Task 5 `write_soi_workbook`, Task 6 option plumbing.

- [ ] **Step 1: Failing pilot test** (append to `tests/test_tui.py`, mirroring `TestPersistedRenderSettings` at :396 — same `sample_copy`/`monkeypatch` pattern):

```python
class TestSchematicToggle:
    @pytest.mark.asyncio
    async def test_toggle_persists_and_export_honors_it(
        self, sample_copy, monkeypatch
    ) -> None:
        from openpyxl import load_workbook

        monkeypatch.chdir(sample_copy.parent.parent)
        app = TowerkitApp(path=sample_copy)
        async with app.run_test(size=(140, 45)) as pilot:
            editor = app.screen
            await pilot.press("t")
            await pilot.pause()
            modal = app.screen
            modal.query_one("#opt-soi-schematic").value = True
            modal.query_one("#apply").press()
            await pilot.pause()
            stored = editor.session.program.render
            assert stored is not None and stored.soi_schematic is True
            assert app.soi_schematic is True
            editor.session.save()
            await pilot.press("x")  # export honors the toggle
            await pilot.pause()
            from towerkit.soi import default_filename

            out = Path("dist") / default_filename(editor.session.program)
            assert out.exists()
            assert len(load_workbook(out).sheetnames) == 2
        # a fresh session reopens with the toggle set
        app2 = TowerkitApp(path=sample_copy)
        async with app2.run_test(size=(140, 45)):
            assert app2.soi_schematic is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tui.py -k Schematic 2>&1 | tail -5`
Expected: FAIL — `stored.soi_schematic is False` (nothing persists it yet) or a query error before that.

- [ ] **Step 3: Implement in `editor.py`**

Read editor.py:270-282 first. In the stored-settings restore block (after `_opts(self).cell_dates = stored.cell_dates`):

```python
            _opts(self).soi_schematic = stored.soi_schematic
```

`action_render_options` (:1699-1727): in `on_choice`, add `_opts(self).soi_schematic = options.soi_schematic` alongside the other four, and `soi_schematic=options.soi_schematic` to the `RenderSettings(...)` construction. In the `RenderOptionsModal(...)` call, pass `getattr(_opts(self), "soi_schematic", False)` as the argument after `cell_dates` (same `getattr` guard style the call already uses at :1724).

`action_export_soi` (:1729-1756): replace the import + call:

```python
        from ...render.soi_xlsx import write_soi_workbook
        from ...soi import default_filename

        program = self.session.program
        try:
            written = write_soi_workbook(
                program,
                theme=self.tower_theme,
                out_path=Path("dist") / default_filename(program),
                show_premiums=_opts(self).show_premiums,
                include_schematic=getattr(_opts(self), "soi_schematic", False),
            )
```

(`build_soi`/`sheet_title` imports drop; everything else in the method — the diagnostics gate, notify, OPEN_CMD — stays.)

`EDITOR_HELP` Output block (:127-131) becomes:

```
Output
  r          render (SVG+PNG to dist/)
  t          render options: theme, totals, premiums, per-cell
             premium and policy term, SOI schematic sheet
             (saved with the file)
  x          export SOI workbook (.xlsx to dist/; adds the
             schematic worksheet when enabled under t)
```

(`NODE_HINTS["program"]` already advertises `t`/`x` — unchanged.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui.py -q 2>&1 | tail -3` → PASS, then the full suite.

- [ ] **Step 5: Gates and commit**

```bash
git add src/towerkit/tui/screens/editor.py tests/test_tui.py
git commit -m "editor: SOI export honors the persisted schematic toggle"
```

---

### Task 8: CLI `--schematic`, final gates, shipping note

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/cli.py` (soi parser at :83-94, `_cmd_soi` at :202-226)
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_cli.py` (append)

**Interfaces:**
- Consumes: Task 5 `write_soi_workbook`, Task 6 `RenderSettings.soi_schematic`.

- [ ] **Step 1: Failing tests** (append to `tests/test_cli.py`; `OLD` and `main` are defined at the top):

```python
def test_soi_schematic_flag_adds_second_sheet(tmp_path) -> None:
    from openpyxl import load_workbook

    out = tmp_path / "soi.xlsx"
    assert main(["soi", str(OLD), "-o", str(out), "--schematic"]) == 0
    assert len(load_workbook(out).sheetnames) == 2


def test_soi_without_flag_stays_single_sheet(tmp_path) -> None:
    from openpyxl import load_workbook

    out = tmp_path / "soi.xlsx"
    assert main(["soi", str(OLD), "-o", str(out)]) == 0
    assert len(load_workbook(out).sheetnames) == 1


def test_soi_honors_stored_render_setting(tmp_path) -> None:
    import json

    from openpyxl import load_workbook

    data = json.loads(OLD.read_text())
    data.setdefault("render", {})["soiSchematic"] = True
    sample = tmp_path / "stored.json"
    sample.write_text(json.dumps(data))
    out = tmp_path / "soi.xlsx"
    assert main(["soi", str(sample), "-o", str(out)]) == 0
    assert len(load_workbook(out).sheetnames) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k schematic_flag 2>&1 | tail -3`
Expected: FAIL — argparse: unrecognized arguments `--schematic`.

- [ ] **Step 3: Implement**

Parser (after `--no-premiums`, :90-93):

```python
    p_soi.add_argument(
        "--schematic", action="store_true",
        help="include the tower schematic as a second worksheet",
    )
```

`_cmd_soi`: replace the `write_soi` import + call with (the CLI flag ORs the stored setting, exactly like `--cell-premiums` in `_cmd_render` at :169):

```python
    from .render.soi_xlsx import write_soi_workbook

    ...
    written = write_soi_workbook(
        program,
        theme=theme,
        out_path=out_path,
        show_premiums=not args.no_premiums and (stored.show_premiums if stored else True),
        include_schematic=args.schematic or bool(stored and stored.soi_schematic),
    )
```

(`build_soi`/`sheet_title` imports drop from `_cmd_soi`; keep `default_filename`.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_cli.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Full final gates**

```bash
cd /Users/grantgreeson/Developer/towerkit
uv run pytest -q > /tmp/final-gate.log; tail -5 /tmp/final-gate.log
uv run mypy src
uv run ruff check src tests
```

All three green before the commit.

- [ ] **Step 6: Commit**

```bash
git add src/towerkit/cli.py tests/test_cli.py
git commit -m "cli: towerctl soi --schematic"
```

- [ ] **Step 7: Manual look + shipping notes (do not skip)**

- Eyeball once in a real spreadsheet app: `uv run towerctl soi programs/atomic-2026.json --theme themes/marsh.json --schematic -o /tmp/atomic-soi.xlsx && open /tmp/atomic-soi.xlsx` — the tower should read like the graphic: proportional stacking, marsh colors, retention band below, pending layer dashed. Cosmetic tuning (row height, font sizes) is fair game AFTER the golden tests are regenerated deliberately, in a follow-up commit — never silently.
- Merging `feat/soi-schematic` to main is Grant's call (superpowers:finishing-a-development-branch). Fresh-eyes review before declaring done.
- Prod machine needs a towerkit RELEASE to see this (wheel flow); follow the release drill in towerkit's CLAUDE.md. No new dependencies, so no bookkit wheelhouse refresh is triggered.

---

### Task 9: visual parity polish (Grant's Excel review, 2026-08-13)

Grant rendered the sheet in Excel; four defects to fix, all visible in his screenshot: wrong theme colours, clipped line headers, one-character-per-line wrapping in narrow share-split merges, clipped text in thin primary layers.

**Files:**
- Modify: `src/towerkit/render/schematic_xlsx.py`, `src/towerkit/render/labels.py` (compact variants if not already present)
- Test: `tests/test_schematic_xlsx.py` (append)

**Requirements (each gets a test):**

1. **Theme parity with the graphic.** For a fixture program rendered with `load_theme` on marsh.json: every participant block's fill in the sheet equals the colour `mpl_program`'s assignment gives that carrier under the same theme (compare via the styles.xml reading pattern against the labels/colour authority, not hardcoded hexes). If `add_schematic_sheet` currently defaults or re-derives colours anywhere instead of taking them from the shared authority, that's the bug.
2. **Line headers never clip.** Header cells get `wrap_text=True` and a computed row height sufficient for the wrapped label at the column's width (two-line allowance); assert the header row height ≥ the two-line height whenever any header wraps.
3. **Narrow merges stay legible.** For a merge narrower than a threshold (pick ~2.5 Excel width units per text line — derive and document), the cell uses `Alignment(shrink_to_fit=True)` (no wrap) AND the label falls back to compact forms from the shared label authority: full → "Carrier share%" → carrier only → empty (colour still speaks). Test with a program containing a 3-way 33.33% split on a narrow line column (Grant's Property tower case) — assert no label longer than the compact form is used below the threshold.
4. **Thin layers get a row floor.** Quantization gains a minimum span of 2 rows per block that carries a label (boundary-snapping preserved; document the deviation from pure proportionality in the module docstring — this is the spec's "hybrid" fallback, activated by Grant's review). Assert a $2M primary under a $100M tower still spans ≥2 rows and its label is not wrapped beyond the merge height.

**Steps:** TDD as always — the four tests first (failing), implement, gates, commit `"schematic: theme parity, header wrap, narrow-merge fitting, row floor"`. Re-run the schematic golden (`SCHEMATIC_GOLDEN_SHA` will change — this is a DELIBERATE content change; regenerate it once, note it in the commit body) and confirm `test_refactor_golden_content` (SOI sheet) is untouched.
