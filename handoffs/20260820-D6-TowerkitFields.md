# D6 — towerkit's seventeen unreachable fields — 2026-08-20

Branch `feat/d6-towerkit-fields`, **committed, not pushed, not merged**.
Full gate green (1824 passed, mypy clean, ruff clean).
Report artifact: https://claude.ai/code/artifact/ac507f53-6b0e-4bc2-8601-41e8d08eebab

## Goal

Grant said "Go" on D6, spec'd in the previous handoff and deliberately derived
from `towerkit.mcpsurface.SURFACE` rather than hand-listed. Seventeen fields on
towerkit's models were writable only from towerkit's own editor behind the
TUI's `o`, which a browser does not have.

He also said "I really don't want to be coding all night." I read that as: do
D6, defer the twelve-item UI polish list entirely, and do not block on
questions. The UI list is untouched and still lives only in the previous
phase's artifact.

## State — all shipped, tested, mutation-checked

| Area | Files |
|---|---|
| Entry↔Field bridge | `src/bookkit/towerfields.py` (new) |
| Write seam | `sync.py` `_addressed` / `tower_field_value` / `set_tower_field` / `add_named_limit` / `remove_named_limit` / `named_limits_of` |
| Term notes | `sync.py` `add_retention` / `edit_retention` / `add_sublimit` / `edit_sublimit` (`set_notes` flag), `program_terms_of` |
| Generic cell routes | `web/routes/program.py` — `_Placed` / `_PLACED` / `_addr` / `_unaddr` / `_field_*` / six routes |
| Named limits | `web/routes/program.py` `named_limit_add` / `named_limit_remove` |
| Chart strip | `web/routes/program.py` `_RENDER_OPTIONS`, `_layers_panel.html` |
| Export fix | `web/routes/program.py` `_export_tower` |
| Templates | `_layer_details.html`, `_line_chip.html`, `_layers_panel.html`, `_term_form.html`, `_layer_conflict.html` |
| Ledgers | `web/parity.py` `TOWERKIT_MODEL_FIELDS` (0 of 55 planned), `SYNC_VERBS` (+3) |

New tests: `tests/test_towerfields.py` (17), plus additions to
`test_web_program.py` and `test_web_parity.py`. Ten mutations run; ten killed
(two only after the tests were rewritten — see below).

## The shape, in one paragraph

`mcpsurface.SURFACE` is derived from the pydantic models at import time and
carries every writable field's type, bounds, guards, clearability and
container; `edit.set_field` is the single choke point. `towerfields.py`
translates an `Entry` into a `forms.spec.Field` and typed text into the wire
value, and owns the money boundary (cents in, whole dollars out, sub-dollar
refused). `sync.set_tower_field` writes any of them through the existing
`write_through`. Three routes serve every field. The ONE thing that cannot be
derived is where a field goes on the page, so `_PLACED` states that, and a
field with no row there has no cell and says so.

## Next step

**Nothing is queued.** D6 closed the field ledger. The open work is:

1. The twelve-item UI polish list from the previous phase (recommendation was
   items 1–4, ~two days). It is in that phase's artifact, not in the repo.
2. The details-row density question below, which overlaps item 1.

## Decisions

- **Derived, not listed** (Grant, "Go"). Seventeen bespoke routes would be
  seventeen places to edit when towerkit grows an eighteenth field.
- **Prose fields are single-line cells.** A textarea needs Enter to insert a
  newline rather than commit, which splits the inline-edit contract between
  `tui/widgets/inline_edit.py` and `web/static/inline-cell.js` — CLAUDE.md
  requires those two to agree. Revisit only as a deliberate change to both.
- **Named-limit amounts are typed in CENTS**, like every other money field
  here, not in towerkit's whole dollars. Consistency within the row beats
  matching the file format; `cents_to_dollars` refuses the remainder.
- **Term notes ride in the term form**, not a cell — one file write, one undo
  unit, saved with the figure they qualify.
- **Chart options answer with their own cell, not the panel.** They change the
  exports; the web drawing is `render/web.py` geometry and takes no options,
  so a panel refresh would redraw an unchanged picture and cost the caret.
- **`set_notes` is a flag, not a sentinel.** None already means "leave alone"
  in those wrappers and a note must be clearable; two explicit parameters beat
  a magic value.
- **REJECTED: a public `addressed()` helper in towerkit.** It is the right home
  for row addressing, but it is a second repo and a second worktree at the end
  of a long build. bookkit's `sync._addressed` is assembled from its own
  existing finders (`_find_layer` / `_find_line` / `_check_index`) and pinned
  against `edit._entity` for every kind by
  `test_bookkit_addresses_a_row_exactly_as_towerkit_does`, so the two go red
  rather than drifting. Promote it to towerkit next time towerkit is open.
- **REJECTED: redesigning the details row tonight.** It is visibly dense now
  and a long value reflows its neighbours. That is Grant's call — see below.

## Two bugs found

1. **`_export_tower` ignored the file's saved `RenderSettings`** and rendered
   with the library defaults, so premiums turned off in towerkit's editor came
   back on in every SVG and PDF bookkit produced. towerkit's own CLI has
   always read them (`cli.py` `_cmd_render`). Live before this phase; fixed.
2. **The address encoding could not survive a line id starting with `i`.**
   The first spelling told a target from a position by a leading `i`, so the
   line id `im` (inland marine — every real book has one) parsed as "index m",
   lost its target and 500'd the whole Program tab. Both halves of the address
   are always present now (`<target>:<index>`, `_` for an unused half).

## What was tried that failed

- **`edit_set_field(program, "retention", "notes", notes, None, index)`.** A
  retention's TARGET is its index — `edit._entity` takes the list position as
  `target` for a retention or a sublimit, because one argument cannot say both
  and a participant's target is its LAYER. Passing None raised "retention needs
  a target", which `_mutate` folded into an ordinary refusal, so the whole term
  save came back as a re-rendered form with the figure unchanged and nothing
  said about why. `mcpsurface.edit_address` states the rule.
- **The first export test.** It spied on `_loaded_program` and asserted the
  FILE's settings, which only proved the save worked — it stayed green with the
  hand-off to `render_program` deleted, i.e. with the exact bug it was written
  for. It watches the renderer's kwargs now.
- **The first address test.** Neither half of the encoding is individually
  wrong, so mutating `_addr` or `_unaddr` alone left it passing. The invariant
  is the ROUND TRIP and it is asserted directly over `im` / `i3` / `i`.
- **`git checkout -- <file>` inside a mutation-test harness.** It discarded
  every uncommitted change in that file, not just the mutation, and wiped
  `sync.py` and `web/routes/program.py`. Rebuilt from the scratchpad patch and
  the edit scripts. **Commit before mutation testing.**

## Gotchas

- **The Bash tool's cwd resets to the main repo between calls.** The first hour
  of this phase edited the MAIN working tree instead of the worktree. Use
  absolute paths or `git -C`; check `git status` in BOTH.
- **`towerfields.FieldRefused` must be re-raised before the `ValueError` arm**
  in `to_wire`, or refusals come back double-labelled ("amount: amount is not
  optional…").
- **`Field.required` tracks `entry.required`, NOT `entry.clearable`.**
  `layer.states` is neither required nor clearable — it is a `list[str]` that
  is legally empty, and marking it required makes clearing the last state
  unreachable from the keyboard while towerkit accepts it happily.
- **`mcpsurface.VALUE_RULES['clearing']` is written for a JSON caller** and
  names a null-vs-`""` distinction a text input cannot express. `towerfields`
  substitutes its own sentence; the rule is unchanged.
- **A container is materialised through `edit.set_container` only.**
  `program.render` is on towerkit's denylist precisely so no surface builds one
  with a bare `setattr`; `container_defaultable` → `create_container` →
  `set_container` is the sequence, mirrored from `mcpserver`.
- **`named_limit` cells only render where a named limit exists.** Correct, but
  it means a `_PLACED` sweep must create one first or it silently skips them.

## Open questions for Grant

1. **Details-row density.** Eight labelled values, a chip strip with an add
   form, three applies-to toggles and four structure controls now share one
   free-flowing row, and a long note pushes everything after it sideways —
   the same failure as the header wrap fixed on 2026-08-20. Convert the row to
   a labelled grid? That redesigns its original three fields too.
2. **`layer.states` is reachable but only usable on a statutory layer**, which
   is correct — but towerkit's own refusal message says the statutory flag
   "moves in the towerkit editor and nowhere else yet", and bookkit's web has
   had a statutory control since 2026-08-19. Tell towerkit its message is out
   of date?
3. **Merge and push?** Committed and green on the branch; nothing pushed.
