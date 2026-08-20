# Handoff — web program phase 4: outputs, browse, and the rest of the structure

Written 2026-08-19, after `20260819-Web-Program-Phase3.md`. Assumes `CLAUDE.md`.

## What phase 4 changed

- **Downloads exist** (the file-response decision, DECISIONS.md): plain
  anchor GETs with `Content-Disposition: attachment`. Tower SVG/PDF
  (`render_program`, Agg forced BEFORE pyplot loads — a server thread has no
  display), schematic workbook (`add_schematic_sheet` + `finalize_workbook`),
  and the client open-items workbook — which CALLS
  `services.export_open_items.write` and never modifies it (Grant has
  in-flight edits to that module and export_rfi; both stayed untouched).
  A download refusal is a readable page with a back link.
- **The Towers page** (`routes/towers.py`, `/towers`): every linked
  placement's tower with a validation badge; a file mid-edit in towerkit
  yields its reasons, never a 500. Read-only by design — editing lives on
  the account Program tab. The nav gains its second real link.
- **Compare** (`compare_page` in routes/program.py, spec D8 slice 5):
  `compare_programs`' delta table; pair auto-detected by renewal adjacency
  (expiring `period_to` == proposed `period_from`), picker on ambiguity,
  `?with=` override. No tower graphic, per the spec's recommend-against.
- **The terms strip**: retentions and sublimits editable in row
  (`sync.add/edit/remove_retention`, `add/edit/remove_sublimit`,
  `sync.program_terms` reader); applies-to is CHECKBOXES because a term can
  span a subset of lines. Line chips gained reorder arrows
  (`sync.move_line`).
- **restack is deliberately absent, by proof**: `write_through` only accepts
  files that already projected (validated), and a valid tower has no gaps or
  overlaps to heal — restack is towerkit's DRAFT healer; through bookkit it
  no-ops on every reachable input. TOWERKIT_EDIT_OPS records the proof; a
  button would be dead chrome (D4).

## Traps

1. **The `{kind}` route family registers LAST and the module says so**:
   Starlette matches `/program/{placement_id}/{kind}` before FastAPI
   validates the enum, so every literal sibling (`/renew`, `/merge`,
   `/scaffold`, `/compare`, `/export/...`) must be registered ABOVE it or be
   shadowed into 422s. Any new single-segment route under
   `/program/{placement_id}/` goes above that block.
2. **`**kwargs` in a FastAPI route signature 422s every request** — it reads
   as a required parameter. Compare shipped with one for ten minutes.
3. **matplotlib in a route**: `matplotlib.use("Agg")` BEFORE importing
   `towerkit.render.mpl_program`, or macOS wants a display.
4. **Terms are index-addressed** (towerkit's own rule, no ids); towerkit's
   `_at` raises IndexError, which `_mutate` does not catch — `sync`
   pre-checks with ValueError.

## Deferred / open (named, with reasons)

- `set_line_group` — Line.group isn't projected; revisit with demand.
- Branch-only towerkit ops (set_states, premium detail, named limits,
  set_field/set_container) — TOWERKIT_EDIT_OPS goes red on its own when
  feat/mcp-hardening merges; decide each then.
- export_rfi on the web — Grant is actively editing that service; wire a
  download for it AFTER his change lands, in its own small slice.
- unlink, TUI-writes-onto-snapshot-seam, MCP structure verbs — unchanged.

## Where this leaves the program surface

Every program verb the terminal workflow reaches now has a web answer or a
named deferral, enforced by two runtime ledgers (SYNC_VERBS over sync.py
source, TOWERKIT_EDIT_OPS over towerkit.edit). The Program tab edits
placements, layers, seats, lines, terms and structure in one grammar;
programs renew, merge, scaffold, compare and export; Towers browses the
book. The nav's remaining unbuilt sections (Today, Navigator, Pipeline,
Calendar, Markets) are simply unrendered until someone builds them — the
next candidate slices, in whatever order Grant wants his daily driver to
grow.
