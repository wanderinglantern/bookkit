# SOI schematic worksheet (towerkit) — design

Date: 2026-08-13
Status: approved in conversation; queued as phase 3 (before MCP server, per Grant 2026-08-13)

## Goal

An optional second worksheet in the SOI export workbook that renders the
insurance schematic — the tower — built from real cells and merged ranges,
themed identically to the current graphic schematic export (Marsh theme
colors et al.). Triggered from the existing export dialogs as an
include-with-SOI option.

## Architecture — one geometry, two renderers

`layout.py::build_layout(program, gamma)` is already the single source of
tower geometry: `Column`s per line of cover, gamma-scaled `LayerBlock`s
with `ParticipantBlock` runs, `RetentionBlock`s, `GroupBand`s. The graphic
renderer (`render/mpl_program.py`) consumes it; the worksheet renderer
consumes THE SAME `TowerLayout`, so stacking, spans, and proportions match
the graphic by construction. No tower math is reimplemented in cell space.

New module `render/schematic_xlsx.py`:

- **Row quantization (Grant's call: proportional, like the graphic):**
  map the layout's normalized gamma-scaled y-space onto a fixed grid of
  thin worksheet rows (~100 rows, uniform small height). Each block's
  row span = its quantized height (ceil, min 1 row — gamma compression
  already keeps small layers visible, so the floor is a formality).
  A $25M layer visibly towers over a $1M primary.
- **Columns:** one worksheet column group per layout `Column` (line of
  cover), plus a left axis column carrying attachment/limit boundaries as
  money labels. `GroupBand`s become merged header cells above their
  columns; line labels sit under them.
- **Blocks as merges:** each `ParticipantBlock` is one merged range
  spanning its runs' columns and its quantized rows — fill color from the
  theme's participant palette (same assignment order as the graphic),
  label = carrier (share) and premium, mirroring the graphic's block
  labels. Retention blocks render below the primary in their dedicated
  style. Unplaced gaps render in the same manner the graphic shows them.
- **Theming:** all colors/fonts from `Theme` (marsh.json et al.) — no
  literals. The sheet must look like the graphic schematic, in cells.

## Workbook composition

`write_table` currently owns the whole workbook lifecycle (create →
style → pin properties → `_normalize_zip`). Restructure so a workbook can
carry multiple sheets and normalize ONCE: extract the per-worksheet body
into a sheet-level function; `write_table` keeps its exact current
behavior and output (golden guard `test_refactor_golden_content` must stay
green); a new orchestration path builds SOI sheet + optional schematic
sheet into one workbook, then pins + normalizes. Determinism contract
unchanged: byte-identical repeat runs, no wall clock.

With the option OFF, output is byte-identical to today's SOI workbook —
guarded by the existing golden test.

## Trigger

The editor's existing export flow (`x` → SOI export; `t` → render
options): render options gain an "include schematic worksheet in SOI
export" toggle, persisted alongside the existing render options, honored
by `action_export_soi`. If a CLI SOI-export path exists, it gains the
matching flag (verify during planning). Default OFF.

## Testing

- Quantizer unit tests: layout→row mapping (proportionality, ceil/min-1,
  full-height coverage, boundary alignment between adjacent layers).
- Sheet content tests reading the saved xlsx: merge ranges present and
  non-overlapping, labels correct, fills match theme palette (read
  styles.xml as test_soi_xlsx.py already does for the SOI sheet).
- Golden guard: option OFF → existing SOI bytes unchanged. New
  content-hash golden for a fixture program WITH the schematic sheet
  (same core.xml-exclusion mechanism).
- Determinism: two identical runs byte-identical with the option on.
- TUI pilot test: toggle persists and the export honors it.
- Gates: pytest, mypy, ruff (towerkit).

## Out of scope (v1)

PDF/print layout tuning, one-schematic-per-line worksheets, editing the
tower from Excel, bookkit's open-items export gaining a schematic (that
workbook has no program context), renewal-comparison schematics.
