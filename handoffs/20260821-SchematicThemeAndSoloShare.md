# Two schematic render defects, and a Work-page export — Grant, 2026-08-21

Logged from the queue, not yet built.

## 1. The xlsx schematic ignores the theme

> "The xlsx Schematic render does not respect the theme being set - i.e.
> marsh.css"

`render.theme` is set on the program and the SVG/PNG path honours it. The xlsx
schematic (`towerkit/render/schematic_xlsx.py`) appears not to — it draws in
whatever its own defaults are, so a program themed marsh renders unthemed in the
one format that goes to a client in a workbook.

Note the extension in the report: he wrote `marsh.css`, and the theme files are
`themes/marsh.json`. That is almost certainly just how he typed it, but confirm
which artefact he means before assuming — if there IS a css path in play, that
is a different bug.

Where to look: `schematic_xlsx.py` imports `_argb` and `sanitize_sheet_title`
from `table_xlsx.py` and builds its own fills. Whether it ever loads the
resolved theme at all is the first question. `theme.resolve_theme` is the
by-name resolver added 2026-08-21 — the xlsx path should be going through it
like every other renderer.

Related and already fixed: the theme WEDGE (a `render.theme` that resolved
against the working directory) — see handoffs/20260821-ThemeResolutionWedge.md.
This is a different bug: the theme resolves fine and is then not applied.

## 2. A 100% share is noise

> "If a layer is 100% one carrier, no need to show 100% in the cell as it is
> redundant."

He is right, and it is the same rule as the house em dash: print the fact, not
the arithmetic that produced it. A slab with one participant at 100% should say
the carrier's name alone; the percentage only earns its place when there is
something to divide.

Where: every surface that renders participants, and they must agree —
- towerkit's schematic (svg/png and xlsx),
- towerkit's SOI (`Carrier` column already prints `Swiss Re (60%), Chubb (40%)`),
- bookkit's stack editor slab carrier list (`_stack_editor.html`),
- bookkit's program tab layer rows.

DRY says this is ONE predicate, owned by towerkit beside the participants, not
four independent `if share == 10000` checks. Find or add the single helper that
formats a participant list and fix it there. A fifth copy is how the tenth
Today account-name anchor came to differ from the other nine.

Careful with the boundary: 100% is `share_bps == 10000`. A single participant
who is NOT at 100% (a 60% line with 40% unplaced) must still print its share —
that is exactly the case where the number is load-bearing, and suppressing it
would claim cover that was never bought.

## 3. Export open tasks and information requests from the Work page

> "Need to surface an export to .xlsx on the Work page for open tasks and
> information requests to export just these tables into a tab."

The machinery exists: `services/export_open_items.py` composes the whole
four-sheet workbook and `services/export_rfi.py` composes the Information
Requests sheet. What is missing is a narrower entry point — the Work page's
CURRENT VIEW, as its own workbook, without the SOI and Projects sheets.

Two things to decide before building:

- **Does it export the filtered view or the whole account?** The Work page
  carries filters (overdue-only, account scope). An export button that
  silently ignores the filter the user is looking at is the `/items` bug in a
  new place — that one lost the filter on `done` and he reported it. The export
  must carry the same query the page is rendering.
- **One tab or two?** He wrote "into a tab", singular, for two tables. Open
  tasks and information requests have different columns, so one sheet means one
  of them gets a shape that is not its own. Recommend two sheets in one
  workbook and confirm.

`compose()` already takes the pieces separately, so this should be a new
`write_work(...)` beside `write(...)` reusing the same composers — NOT a second
composition path. The columns the client-facing workbook withholds (`ref`,
`internal`) are withheld by `write()`'s explicit column tuple, so any new writer
must make that same choice deliberately rather than inheriting it by accident.
