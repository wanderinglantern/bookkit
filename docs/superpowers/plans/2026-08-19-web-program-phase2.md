# Web Program Phase 2 — Verb Parity, Both Directions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After this phase the web can do every program verb the TUI can (edit placement facts, renew, add a layer to the right lines, remove a layer, override the scaffold path, send a submission), the TUI catches up where the web ran ahead (correct/remove a market seat), and a verb-level ledger makes the next gap machine-visible.

**Architecture:** The dual-owner placement-edit split moves out of `tui/widgets/entity_actions.py` into `services/placement_edit.py` so both surfaces call one rule. All file writes stay behind `services.program_files.write` on the web and `sync.*`-in-a-batch on the TUI (unifying the TUI onto the snapshot seam is noted, not done — it is its own reviewed change). New web verbs follow phase 1's grammar: cells edit where facts are read, confirms render in place, list-changing writes answer with the whole panel.

**Tech Stack:** FastAPI + Jinja + htmx, Textual (TUI backfill), pytest with `snapshot_db`.

**Spec:** the "Web Program Parity Review" artifact, Phase 2 section; decisions D1–D5 in DECISIONS.md; handoff `handoffs/20260819-Web-Program-Phase1.md`.

## Global Constraints

- Same gates, worktree (`.claude/worktrees/web-program-phase2`), and htmx/refusal/batch rules as phase 1's plan (Global Constraints there apply verbatim).
- New rule (Grant, 2026-08-19, in CLAUDE.md): UI work shows itself — the artifact gets labeled screenshots (S2-n) of the running UI at phase end.
- **Consciously deferred, with reasons, not silently dropped:**
  - `unlink` — a background `project_all` over the configured roots would re-adopt the file and silently undo it; the verb needs a "forget this file" design that survives sync, which is not this phase's call. The refusal text that promised it was already removed in phase 1.
  - Market-cell conflict three-way — stays a one-line refusal (phase 1 note stands).
  - TUI program writes onto the snapshot seam (`program_files.write`) — worth doing, its own change.

---

### Task 1: `services/placement_edit.py` — the dual-owner split becomes a service

**Files:** Create `src/bookkit/services/placement_edit.py`; modify `src/bookkit/tui/widgets/entity_actions.py` (edit_placement rewires); test `tests/test_placement_edit.py`.

**Interfaces (produces):**
```python
FILE_OWNED = ("program_name", "period_from", "period_to")   # when linked
BOOK_OWNED = ("status", "commission_bps")

def apply(conn, placement, changes: dict[str, Any], *, open_batch) -> None:
    """Split by owner; raises ValueError on any refusal (nothing partial:
    file half runs first via program_files.write when linked — snapshot +
    batch — and the book half joins the same outer batch when both move).
    Unlinked placements route FILE_OWNED keys to the row instead."""
```
- Web cells (Task 2) call it with one-key dicts; TUI's form calls it with the whole diff. Nested `open_batch` joins the outer transaction (db.transaction nests by joining), so a TUI form save stays ONE undo unit.
- Steps: failing tests (linked name-change writes the file; status change writes the row only; unlinked name-change writes the row; refusal propagates with towerkit's words; one batch for a mixed TUI-style edit) → implement → rewire `entity_actions.edit_placement` to build changes and call `apply` → seam test: mutate the service to a no-op and watch the TUI-path test fail (green-suite rule) → gates → commit.

### Task 2: placement header facts become cells on the web

**Files:** Modify `forms/inline.py` (PLACEMENT_FIELDS), `routes/program.py`, `_layers_panel.html`, app.css; test `tests/test_web_program.py`.

- `PLACEMENT_FIELDS`: program_name (required), period_from/period_to (date, required), status (select over the same tuple the TUI form offers), commission_bps (int). Keys are `placement_edit.apply`'s own.
- Cell routes `GET/GET-edit/POST /accounts/{ref}/program/{placement_id}/cell/{key}`, `tag="span"` (header context), display via `_display_text`; save calls `placement_edit.apply(conn, placement, {key: value}, open_batch=_open_batch_web)`; success returns cell + panel OOB (a period write can move the tower); refusal re-renders the editor with the message; a write-through conflict gets the one-line refusal (documented, like market cells).
- Header template: name/period/status become cells; commission appears (it was invisible on the web entirely); status keeps its pill class via extra_class.
- Tests: name saves through to the FILE (sha changes, layer intact); status saves to the row and never touches the file (sha unchanged); commission renders and saves; refusal keeps typed value; reachability: PLACEMENT_FIELDS keys all render as data-field (extend test_web_dead_controls' union or a sibling assertion).

### Task 3: applies-to on web layer add (F5 dies)

**Files:** Modify `routes/program.py` (`_layer_add_fields` grows a line select; `layer_add` expands `__all__`; refuse when the program has no lines), `_program_form.html` (select rendering, mirroring macros/form.html), tests.

- Options from `sync.program_lines(conn, placement_id)`; `("all lines", "__all__")` prepended when >1 line, required always — the silent first-line default dies.
- Tests: the add form renders a select naming the program's lines; a layer added with a chosen line lands on exactly that line (`applies_to` in layer_details); `__all__` lands on all; a no-lines program refuses with "build them in towerkit".

### Task 4: layer remove (D2), web first

**Files:** Modify `src/bookkit/sync.py` (remove_layer), `routes/program.py` + `_layer_details.html` (confirm + POST), tests `test_program_edits.py` + `test_web_program.py`.

```python
def remove_layer(conn, placement_id, layer_id) -> Diagnostics:
    def mutate(program):
        _find_layer(program, layer_id)          # ValueError, not KeyError
        edit_remove_layer(program, layer_id)    # towerkit.edit.remove_layer
    return _mutate(conn, placement_id, mutate)
```
- Web: the details row gains "remove layer" → GET `/layers/{layer_id}/remove` confirm (names the seats going with it and the batch revert; writes nothing) → POST → `program_files.write(tool="program_layer_remove")` → whole panel (a row left the table). Refusal (e.g. towerkit refuses the gap it would leave) comes back in the confirm, in place.
- Tests: sync-level (removed from file; unknown id refuses; a gap-making removal refuses and writes nothing); web-level (GET writes nothing + names participants; POST removes; refused removal keeps panel).

### Task 5: renew on the web

**Files:** Modify `routes/program.py`, `_layers_panel.html` (per-section Renew control), new `_renew_confirm.html`, tests.

- Per-PLACEMENT control (the account-header button stays unrendered — it names no target). GET `/program/{placement_id}/renew` → confirm stating exactly what sync.renew does (next period dates, file cloned + linked at birth when one exists); POST → `open_batch(source="web", tool="renew_placement")` around `sync.renew` → success re-renders the whole programs panel (the new program appears at once); refusal → panel with error slot.
- Tests: confirm GET writes nothing; POST creates the next-period placement + cloned file (assert new placement in panel response and on disk); refusal path (renew a placement that refuses — reuse whatever sync.renew refuses on, e.g. already-renewed/unlinked case per its diags) keeps the panel.

### Task 6: TUI backfill — seats are correctable where they are read

**Files:** Modify `tui/screens/account.py` (carriers table refresh keeps a row→seat map; `e` on the carriers table edits the seat; `D` dispatches to seat-remove or layer-remove with confirms), maybe `widgets/entity_actions.py`; tests `tests/test_tui.py`-style + `test_dead_keys.py` expectations.

- Refresh stores `self._carrier_seats: dict[str, tuple[str, str | None]]` (row key → (layer_id, carrier); placeholder rows carry None).
- `e` with carriers-table focus: seat form (carrier text with `vocab.market_names` suggestions, share) → `sync.update_participant` in a batch; placeholder row falls through to layer edit hint.
- `D` with carriers-table focus: carrier row → ConfirmModal → `sync.remove_participant`; placeholder row → confirm → `sync.remove_layer`. The screen-wide D notify text gains the carriers table. test_dead_keys is the arbiter for hint text.
- Tests: pilot-driven — correct a share from the table; remove a seat; remove an unplaced layer; D on carriers table with no row notifies.

### Task 7: scaffold path override on the web

**Files:** Modify `_scaffold_confirm.html` (the destination becomes an editable input, prefilled), `routes/program.py` scaffold_create (reads `path` from the form, expanduser, falls back to the computed default), tests (a custom path is honored; a taken path refuses in place).

### Task 8: new submission from the program section

**Files:** Modify `routes/program.py` (+ Submission button route pair using `forms.entities.submission_form`/`apply_submission` with `placement_id`), `_layers_panel.html`, tests.

- GET form into the section form host (whole-record spec — selects already render via macros/form.html); POST via the shared `_save` seam; success answers `HX-Redirect` to the pipeline tab, where the submission is actually visible — landing back on a tab that shows no trace of what was just made is the dishonest option.
- Tests: form reachable; POST creates the submission linked to the placement; refusal keeps typed input; success carries HX-Redirect to `/accounts/{ref}/pipeline`.

### Task 9: the verb-level ledger

**Files:** Modify `src/bookkit/web/parity.py` (SYNC_VERBS: every program mutator → per-surface coverage/deferral note), `tests/test_web_parity.py`.

- Discovery is source-truth: the test scans `sync.py` for functions whose bodies call `_mutate(` / `write_through(` plus the named non-mutate writers (`scaffold_program`, `renew`), and asserts that set == SYNC_VERBS' keys, both directions. `set_applies_to` gets an honest DEFERRED-phase-3 entry.

### Task 10: screenshots, docs, gates, review, merge

- Seed a demo DB in the scratchpad, serve, screenshot the program tab (header cells, applies-to add form, details row with remove, renew confirm, seat ops) — label areas S2-1… and embed as data: URIs in the artifact per the new CLAUDE.md rule.
- DECISIONS.md (unlink deferral note), changelog, parity ledger prose, handoff `handoffs/20260819-Web-Program-Phase2.md`.
- Full gates → fresh-eyes review (code-reviewer over the branch diff) → fix → merge → remove worktree → update artifact (phase 2 built + screenshots).

## Self-review notes
- Artifact phase-2 list coverage: placement edit (T1/T2), renew (T5), applies-to (T3), remove_layer (T4), TUI backfill (T6), scaffold override (T7), new submission (T8), ledger (T9); unlink consciously deferred with its reason.
- Type consistency: `placement_edit.apply` is the only new cross-surface signature; both callers built in the same phase.
