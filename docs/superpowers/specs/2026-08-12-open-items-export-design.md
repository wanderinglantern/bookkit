# Open-items export (.xlsx, SOI formatting) — design

Date: 2026-08-12
Status: approved in conversation; pending spec review

## Goal

Export a client-facing open-items list as .xlsx in the same visual
formatting as towerkit's Schedule of Insurance workbooks, driven by
bookkit's open items (tasks with long-form detail, unmet project needs,
pending submissions).

"SOI formatting" = towerkit's existing setup: `Theme.soi` styling
(font, header/band fills, borders), merged section-label rows, and the
deterministic-output rule (pinned workbook properties + epoch-rewritten
archive → byte-identical repeat runs) in `render/soi_xlsx.py`.

## Architecture — formatting authority stays in towerkit

Two-repo change, mirroring the `money.parse_share` delegation pattern:

**towerkit:** extract the generic styled-table writer out of
`render/soi_xlsx.py` into a reusable renderer (e.g.
`render/table_xlsx.py`): takes headers, column widths, sections
(optional label + premium-style total slot + rows), a `Theme`, and an
output path; produces the styled deterministic workbook. `write_soi`
is refactored to call it — SOI output stays byte-identical (regression-
tested against a fixture workbook). The pure-modules-never-import-
rendering-libraries rule is untouched.

**bookkit:** `services/export_open_items.py` composes sections PURELY
(no openpyxl import — bookkit gains no xlsx dependency; rendering is
towerkit's job):

- One section per program/project, plus a "General" section for
  org-level items; section label carries the program name and lines of
  cover.
- Row sources: open/overdue tasks (title + `detail`), unmet project
  needs, pending submissions.
- Markdown in `Task.detail` is flattened to clean plain text for cells
  (bullets survive as text lines; emphasis markers stripped).
- Proposed columns (REVIEW POINT — adjust to what clients should see):
  Item | Details | Type (Task / Need / Submission) | Due / Needed by |
  Status | Days open.
- Task categories (added 2026-08-12): Task gains a freeform `category`
  column — vocabulary-completed like lines (`vocab.task_categories` +
  `Field.suggestions`), never an enum. Org-level tasks in the export are
  sectioned BY CATEGORY (SOV-style; uncategorized falls into "General");
  placement and project sections are unchanged. REVIEW POINT: whether
  category sections should also subdivide placement sections.
- Money (need limits/indications) formatted in whole dollars via the
  existing cents→dollars boundary; dates client-readable.

## Additional worksheets (added 2026-08-13): a three-tab workbook

The export becomes a three-sheet client deliverable, in this order:
**Open Items · Projects · Schedule of Insurance.**

**Sheet 2 — "Projects"**, same styled-table formatting — the client's
projects data in full, not just the unmet-need slice sheet 1 shows:

- One section per non-completed project; section label carries the
  project name, status, and period (start → end).
- Rows: every need on the project regardless of status — line, notes,
  needed-by, prettified status, limit (formatted dollars), days open.
- Sheet included automatically whenever the client has any non-completed
  project; omitted (not blank) otherwise. No toggle.
- The Open Items sheet keeps its unmet-need rows (open-items focus);
  the Projects sheet is the fuller report. REVIEW POINT: drop the
  "Project —" sections from sheet 1 once sheet 2 exists?
**Sheet 3 — "Schedule of Insurance"** — the policy list, pulled straight
from towerkit's SOI machinery:

- towerkit's SOI is per-PROGRAM; this workbook is per-CLIENT. The sheet
  concatenates `build_soi(program)` sections for EVERY linked placement
  (bookkit loads each `placement.program_path`), each program's sections
  under a program-name label, standard SOI columns, `show_premiums=True`
  (it is the client's own program).
- UNLINKED placements (no towerkit file) still appear: a minimal section
  from book data — program name, period, status, premium — so the policy
  list is complete, never silently partial.
- Sheet included whenever the client has any placement.

Both additions DEPEND ON towerkit's multi-sheet workbook composition API
(built by the SOI-schematic phase — public sheet-level writers for both
the generic table sheet AND the SOI sheet, plus single finalize; the
plan-writer has been instructed accordingly). They execute as one small
bookkit follow-up plan once that API lands and merges.

## Entry points

- `bookctl export open-items <org-ref> [--out FILE]` — default filename
  `<org>-open-items-<date>.xlsx` (date passed in, not wall-clocked, per
  the determinism rule).
- TUI: export action on an org/program from the Navigator, reusing the
  shared entity-actions wiring.
- NOT exposed over MCP in v1 (the connector returns data, not files);
  revisit if the work assistant needs to hand the file to clients
  directly.

## Error handling

- Org with zero open items: still writes a valid workbook with an
  explicit "No open items" row — an empty-looking client deliverable
  must say so, not render blank.
- Unknown org ref: error with near-match client-name candidates (rapidfuzz).
- Out-path not writable: plain error, nothing partial left behind.

## Testing

- towerkit: fixture regression test proving refactored `write_soi`
  output is byte-identical to pre-refactor; unit tests for the generic
  writer.
- bookkit: pure-composition tests over seeded fixtures (sections,
  markdown flattening, money/date formatting); one end-to-end test
  writing a real workbook and re-reading it with openpyxl (dev
  dependency only); determinism test (two runs, same bytes).
- Convention test: no openpyxl outside the imports/ package (readers/templates already use it).
- Gates both repos: pytest, mypy, ruff.

## Dependencies / deployment

- No new bookkit runtime dependency (openpyxl remains towerkit's).
- towerkit change lands first; bookkit pins/uses it via the editable
  path dep locally and the wheelhouse/PyPI flow for the work machine —
  a towerkit release is part of shipping this.

## Out of scope (v1)

MCP file export, PDF output, per-client formatting overrides beyond
towerkit themes, responsible-party tracking on tasks (no such field
today; add to the model first if client-facing ownership columns are
wanted — flagged, not designed).
