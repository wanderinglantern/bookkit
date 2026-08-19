# Handoff — web program tab phase 1: one grammar, no lies

Written 2026-08-19, after the phases-1–5 handoff (`20260819-Programs-On-The-Web.md`).
Assumes `CLAUDE.md` and nothing else.

## Why this phase exists

Grant asked for a parity review of program/tower/schematic editing, web vs
TUI, aiming at first-class program CRUD in the browser. The review (published
artifact "Web Program Parity Review", 2026-08-19) found the plumbing sound and
the surface forked: two editing grammars on one table, dead chrome drawn live,
three editable fields with no UI, a 100x share pre-fill bug, and a web-only
creation journey that dead-ends at "Coverage TBD". Grant decided five things:

- **D1** the browser may edit program structure — lines first (phase 3)
- **D2** layer delete exists — sync.remove_layer, confirm, both surfaces (phase 2)
- **D3** one user-facing word: "program"; placement stays the code/DB term
- **D4** never draw an inert control — wire it or don't render it
- **D5** money cells display compact, pre-fill exact

All five are in `DECISIONS.md`. The plan is
`docs/superpowers/plans/2026-08-19-web-program-phase1.md` (this branch built
all of it); phases 2–4 are specified in the artifact, not yet planned in
detail.

## What phase 1 changed

- **Markets ride the inline-cell contract** (`routes/program.py`): carrier and
  share are cells on the chip (`_market_chip_html`, tag="span", index-addressed
  `.../markets/{index}/cell/{key}`), remove fetches an in-place confirm
  (`_market_confirm.html`), `+ market` is an in-row form (`_market_add.html`)
  with carrier completion from `vocab.market_names`. The old form-host
  mini-form routes are gone.
- **FIXED A LIVE 100x BUG**: the old share editor pre-filled the seat's
  PERCENT through `initial_text`'s BPS formatter — 40% pre-filled "0.4" and an
  unedited save wrote 0.4%. `_market_prefill` carries the rule; the regression
  test is `test_share_editor_prefills_the_actual_percent`.
- **D5**: `_display_text` renders money cells compact; editor routes still
  pre-fill exact via `initial_text`. The old "exact, never compact" comments
  are rewritten to record why the split makes compact safe.
- **Layer details row** (`.../layers/{layer_id}/details`): policy number and
  policy dates, previously routes with no UI, open under each row via a
  chevron column. The cells are SPANS — a `<td>` swapped back inside the
  colspan cell is parser-dropped (`_layer_cell_tag`).
- **D4 sweep**: topbar renders only routed nav (Book); the account header's
  `+ Task` is a real link to `/accounts/{ref}/work?add=task` (work_tab
  pre-opens the task form); Renew / + Log interaction / ··· / Assign /
  Search pill / contacts-panel pending spans / book-page filter+New
  account+Export pills all unrender until their routes land.
  `tests/test_web_dead_controls.py` is the web's `test_dead_keys`: no
  admitted-dead chrome, every rendered hx-get/hx-post/href/action resolves
  against the router, every LAYER_FIELDS key reachable from the page. All
  three verified to fail under mutation.
- **form-host.js**: opening a form closes other form-hosts whose inputs are
  untouched (typed input is never discarded); owns the delegated
  `[data-row-close]` / `[data-row-expand]` handlers (no inline onclick).
- **D3**: TUI tab is "4 Programs" (id stays `tab-placements`), form titles
  "new program"/"edit program", notify strings say "programs tab". 12 TUI
  snapshots re-baselined for exactly that label change. The scaffold refusal
  stops naming "unlink" (no surface has it until phase 2).

## Traps for the next phase

1. **Literal segments before {index}**: `/markets/new` and `/markets/button`
   register BEFORE `/markets/{index}` or the int coercion 422s them.
   Registration order is the resolver across this whole module.
2. **Market cells are index-addressed.** Every write re-renders the whole
   panel so an index is never stale by the time it is used — keep that
   invariant if you change what a save returns.
3. **A market-cell conflict is a one-line refusal**, not the layer cells'
   three-way. Deliberate: the three-way's forms are layer-id-shaped. If phase
   2 extends it, that is its own reviewed change.
4. **`_layer_cell_tag`**: the three detail keys render as spans everywhere
   (display, editor, save response). A td response to a details-row cell is
   silently dropped by the parser.
5. **Snapshot re-baselines**: `uv run --no-sync python -m pytest
   tests/test_snapshots.py --snapshot-update` — read the HTML diff report
   first, per the file's own docstring.
6. **towerkit is mid-flight**: its checkout gained `edit.Refusal` (stable
   codes) and full line ops (add/rename/remove/move, adopt) while this branch
   was built — exactly what phase 3's lines CRUD needs. Do not touch the
   towerkit checkout; coordinate with whoever owns `feat/mcp-hardening`.

## What is next (phase 2, from the artifact)

Move the dual-owner placement-edit split out of `tui/widgets/entity_actions.py`
into `services/`, then: web placement edit (header facts as cells), Renew on
the web (confirm-first, `sync.renew`), applies-to select on web layer add
(today it silently lands on the first line — the known F5 gap this phase did
NOT fix), `sync.remove_layer` (D2), unlink, new submission, TUI market
correct/remove backfill, and the verb-level parity ledger over sync mutators.
