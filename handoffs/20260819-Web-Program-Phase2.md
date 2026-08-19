# Handoff — web program phase 2: verb parity, both directions

Written 2026-08-19, after `20260819-Web-Program-Phase1.md`. Assumes `CLAUDE.md`.

## What phase 2 changed

- **`services/placement_edit.py`** owns the dual-owner split (F12). `split`
  decides file-vs-row per key; `apply` is the whole-form writer and REFUSES
  to run unbatched. Batching stays with the caller on purpose: a nested
  `open_batch` inserts a phantom empty batch row into the changes rail — the
  reason the first draft of this service was rewritten.
- **Web placement edit**: header name/period/status/commission are cells
  (`POST /program/{placement_id}/cell/{key}`); file fields ride
  `program_files.write`, row fields a plain batch; unchanged values write
  nothing. Commission is on the web for the first time.
- **Renew on the web**: placement-scoped control; confirm states what
  `sync.renew` does; POST answers with the WHOLE panel either way (a
  panel-targeted POST must never answer with a fragment — scaffold's lesson).
  `sync.renewal_period` exposes the would-be dates for the confirm.
- **Applies-to on web layer add** (F5 dead): required select, `all lines`
  when >1; a made-up line refuses; an unlinked placement refuses BEFORE the
  no-lines guard can send someone to towerkit to edit a file that isn't there.
- **Layer delete (D2)**: `sync.remove_layer` (towerkit `edit.remove_layer`
  behind `_mutate`; `_find_layer` first so an unknown id refuses with the
  re-sync hint instead of a KeyError `_mutate` can't catch). Web: details-row
  remove with a confirm naming the seats. TUI: `D` on a placeholder carriers
  row. towerkit refuses removals that strand a gap or empty a line — a lone
  primary on a line is therefore not removable, which is correct and tested.
- **TUI seat backfill**: `_carrier_seats` maps carriers-table row keys to
  (layer_id, carrier); `e` edits the seat (carrier + share, share pre-filled
  as PERCENT→bps — the exact 100x trap the web's editor shipped with), `D`
  removes it, confirm-first, refusals rolling the batch row back with the
  write.
- **Scaffold path is editable** on the web (parity with `t`).
- **Submissions from the program section**: shared `submission_form`, success
  = 204 + HX-Redirect to the Pipeline tab where the record is visible.
- **`web/parity.SYNC_VERBS`**: all ten program verbs per surface, verb set
  discovered from sync.py source (`_mutate` callers + scaffold/renew) — red
  in both directions on drift.

## Deferred, with reasons (do not silently resurrect)

- **unlink** — `project_all` over configured roots would re-adopt the file
  and silently undo it; needs a "forget this file" design that survives sync.
- **Market-cell / placement-cell conflicts** stay one-line refusals; the
  three-way is layer-cell-shaped.
- **TUI program writes onto the snapshot seam** (`program_files.write`) —
  TUI file writes batch but do not snapshot; unifying is its own change.
- **MCP verbs for remove/update participant + remove_layer** — an mcpparity
  decision nobody has made; the ledger names it.
- **TUI direct market-bind key** — binding still rides the submission flow.

## Traps

1. **`git checkout -- <file>` after a mutation test** reverts UNCOMMITTED
   task work with it — it bit twice this build (phase 1 topbar, phase 2
   account.py). Commit first, mutate second.
2. Ruff/gate exit codes eaten by a `| tail` pipe — the CLAUDE.md rule
   applies to LINTS too, not just pytest.
3. The carriers-table seat map is rebuilt per refresh; anything acting on a
   row key must go through `_carrier_seats`, never parse the key.
4. `_program_form.html` now renders selects; `checked_option` refuses values
   off the option list — a select field whose options are request-scoped must
   be rebuilt from the same data on POST.
5. towerkit's checkout (feat/mcp-hardening) now carries line ops + stable
   `Refusal` codes — phase 3's lines CRUD should use them; still not merged
   to towerkit main, coordinate before depending on it.

## What is next (phase 3, gated on nothing — D1 approved)

Lines CRUD on the web (rename "Coverage TBD" to real lines, add/remove via
sync wrappers over towerkit's edit ops), applies-to chips writing through the
currently-deferred `set_applies_to`, the clickable tower (hit-targets over
renderer output, never a second renderer), and merge-placement on the web.
Then phase 4: exports (file downloads), the Towers page, Compare.
