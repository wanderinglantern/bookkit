# Handoff — web program phase 3: structure, the first-class part

Written 2026-08-19, after `20260819-Web-Program-Phase2.md`. Assumes `CLAUDE.md`.

## The reframing this phase carries (read this first)

Mid-phase, Grant caught statutory cover as "fully built but not accessible":
modelled, projected, rendered — and unchangeable from the browser, because
parity had been enumerated over BOOKKIT's surfaces while tower structure hid
behind the TUI's `o` into towerkit's editor, which a browser does not have.
The rule that came out of it is in CLAUDE.md now: **the web's parity universe
is everything the terminal workflow reaches, towerkit's editor included.**
`web/parity.TOWERKIT_EDIT_OPS` enforces it structurally — every public
towerkit.edit op, introspected at runtime, must be covered, deferred by name
with a reason, or marked branch-only pending a decision. When towerkit grows
an op (the feat/mcp-hardening branch will add several on merge), the red
test is the ticket.

## What phase 3 changed

- **Lines CRUD** (`sync.add_line/rename_line/remove_line`, the lines strip in
  `_layers_panel.html`): rename is an inline cell whose SUCCESS answers with
  the whole panel (the id follows the name, so a returned cell would carry a
  dead action URL); remove confirms naming both grades of blast (layers that
  die vs layers that narrow, computed by `_line_blast`); add arrives WITH a
  pending layer because an empty line is a validator ERROR (`line-empty`).
- **The details row grew structure**: applies-to toggle chips —
  `sync.set_applies_to`'s first caller ever — plus statutory (confirm-first
  on, names the limit given up; off requires the replacing figure;
  `sync.set_statutory` is a field write, so it does NOT depend on the
  branch-only towerkit `edit.set_statutory`) and follows-underlying
  (`sync.set_follows_underlying`). All refusals re-render the row in place.
  `sync.layer_details` now carries `follows_underlying` — without it the
  chip rendered permanently off (a write with no visible state).
- **The tower is a surface**: `data-layer-id` on outlines/blocks,
  `data-layer-row` on table rows, a delegated click in form-host.js that
  scrolls and flashes. Attributes only; the agreement rule untouched.
  Reduced motion stills both the flash and the smooth scroll.
- **Merge on the web**: same `merge_placements` call and `merge_placements`
  batch tool as the TUI's `x`; panel-shaped answers both ways; the form
  offers only same-account siblings and never the source itself.

## Traps

1. **A decorator does not follow a renamed function.** Splitting
   `layer_details_row` into `_details_row` left `@router.get(...)` on the
   helper, whose `layer: dict` param became a required BODY on a GET — every
   details fetch 422'd. When extracting a route body, move the decorator by
   hand and re-run the routes' tests before anything else.
2. **A rename invalidates its own cell.** Line ids follow names; any write
   that can change an ID must answer with the panel, never the cell.
3. **Structure writes and the details row**: success re-renders the row AND
   the panel OOB — the OOB section swap discards the returned row, so the
   row closes after each write. Known, accepted; re-seating open rows is a
   future nicety.
4. Ruff/gate exit codes: `echo $?; git commit` commits regardless — bit
   twice this phase. Gate with `&&` from the check itself.

## Deferred, with reasons (the ledger names all of these)

- Retentions/sublimits editors, line groups, move_line — phase 4, with the
  Towers/drawing work where they visually live.
- `restack` — wide blast radius, needs a before/after confirm design.
- Branch-only towerkit ops (set_states, premium detail, named limits,
  set_field/set_container) — re-decide when feat/mcp-hardening merges;
  TOWERKIT_EDIT_OPS will go red on its own.
- unlink, TUI-writes-onto-snapshot-seam, MCP structure verbs — unchanged
  from phase 2.

## What is next (phase 4, from the artifact)

Exports (SVG / schematic / SOI downloads — needs the file-download response
decision), the Towers browse page, Compare (spec D8 slice 5) — and with the
drawing page, the deferred retention/sublimit/line-order editors.
