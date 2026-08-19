# Web Program Phase 3 — Structure: the First-Class Part — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The browser stops being a viewer with edit boxes and becomes a place a program can be BUILT (D1): lines get CRUD on the web (the "Coverage TBD" dead-end dies), a layer's applies-to becomes editable chips, the tower drawing becomes a surface (click a block, land on its row), and a duplicate placement can merge without the terminal.

**Architecture:** Three new sync wrappers (`add_line`, `rename_line`, `remove_line`) over towerkit's own edit ops — all present on towerkit MAIN, verified; only the branch-only `Refusal` codes are avoided — plus the first caller for the until-now-dead `sync.set_applies_to`. Web routes follow phase 1–2 grammar exactly: cells edit in place, confirms render where asked, list-changing writes answer with the panel. The tower gains hit-targets over renderer output — data attributes and a delegated click handler; zero new text or geometry, so the R66 agreement rule is untouched.

**Tech Stack:** unchanged (FastAPI + Jinja + htmx, Textual untouched this phase except nothing).

**Spec:** the "Web Program Parity Review" artifact, Phase 3 section; D1 (approved); handoff `handoffs/20260819-Web-Program-Phase2.md`.

## Global Constraints

- Phase 1–2 Global Constraints apply verbatim (gates, worktree `.claude/worktrees/web-program-phase3`, program_files.write seam, htmx refusal rules, exact pre-fill).
- SYNC_VERBS discovers new mutators from source: every new sync wrapper needs a ledger entry in the same task, or the suite goes red (by design).
- COMMIT BEFORE ANY MUTATION CHECK — `git checkout --` after a mutation reverted uncommitted work twice already.
- towerkit checkout is someone's in-flight branch: use only APIs verified on towerkit MAIN (add_line/rename_line/remove_line/set_applies_to are); never touch the checkout.

---

### Task 1: sync wrappers — lines become bookkit-editable

**Files:** Modify `src/bookkit/sync.py`, `src/bookkit/web/parity.py` (SYNC_VERBS entries); test `tests/test_program_edits.py`.

**Interfaces (produces):**
```python
def add_line(conn, placement_id, name: str) -> Diagnostics
    # towerkit edit.add_line(program, name); the line starts empty, which
    # towerkit flags as a WARNING-or-error? — VERIFY: a line with no layers is
    # an ERROR per the scaffold notes, so add_line must ALSO seat something or
    # be refused. Resolution: add_line takes the id of an EXISTING layer to
    # extend onto the new line (extend_layer_id), or creates the line together
    # with a pending layer the way scaffold does. Decide by reading
    # towerkit.validate's line rule first; the tests pin whichever holds.
def rename_line(conn, placement_id, line_id, name: str) -> Diagnostics
    # towerkit edit.rename_line — id follows the name, cascading appliesTo
def remove_line(conn, placement_id, line_id) -> Diagnostics
    # towerkit edit.remove_line — cascades: the id leaves every appliesTo and
    # anything left empty goes with it; validator still gates the result
def set_applies_to(conn, placement_id, layer_id, line_ids) -> Diagnostics
    # EXISTS since forever, called by nothing; phase 3 wires it (no change
    # expected, but its tests move from "dead code with tests" to load-bearing)
```
- Steps: read towerkit.validate's empty-line rule → failing tests (rename cascades appliesTo and the projection follows; remove cascades and refuses when the validator says no; add creates a valid program; set_applies_to moves a layer between lines and refuses an unknown line) → implement → SYNC_VERBS entries (web pending until Task 2/3, tui "via `o`", mcp DEFERRED) → gates → commit.

### Task 2: the lines strip on the web

**Files:** Modify `routes/program.py`, `_layers_panel.html`, `app.css`; create `_line_remove_confirm.html`; test `tests/test_web_program.py`.

- A "lines" row under the section header: each line is an inline CELL (rename in place — cell contract, `GET/GET-edit/POST .../program/{placement_id}/lines/{line_id}/cell/name`, tag="span"), plus a remove control (GET confirm naming the layers/retentions that go with it, in place) and a `+ line` in-row add (the market-add pattern: button swaps for a name input; `/lines/new` + `/lines/button` LITERAL ROUTES REGISTERED BEFORE `/lines/{line_id}`).
- Every write through `program_files.write` (tools: `program_line_edit`, `program_line_add`, `program_line_remove`), success = panel OOB (a rename recasts appliesTo everywhere; the tower's columns move).
- Rename pre-fill = current name (exact); a rename that collides slugs is towerkit's to referee.
- Tests: the strip renders every line as a cell; rename writes through and the layers' applies_to follow; remove confirm names the cascade and writes nothing; a remove the validator refuses answers in place; add creates the line (shaped per Task 1's resolution); reachability (dead-controls suite inherits automatically — run it).

### Task 3: applies-to chips in the details row

**Files:** Modify `routes/program.py` (`layer_details_row` gains the chips + a POST), `_layer_details.html`; test `tests/test_web_program.py`.

- The details row shows every program line as a toggle chip (on = layer applies to it). A click POSTs the RESULTING set to `.../layers/{layer_id}/applies-to` (one write, `sync.set_applies_to`, tool `program_layer_edit`); success = panel OOB (columns move); refusal (last-line removal, a gap the move would strand) re-renders the details row with the message.
- The chips are the first UI for a verb that has been dead code with tests since the sync layer was built — SYNC_VERBS entry flips from DEFERRED to live.
- Tests: chips render with the layer's current lines marked; toggling on/off round-trips to the file; emptying the set is refused with the file untouched.

### Task 4: the tower becomes a surface

**Files:** Modify `_tower_panel.html` (data attributes on layer outlines + blocks), `static/form-host.js` (delegated click: scroll the matching row into view + flash), `_layers_panel.html` (rows carry `data-layer-row="{id}"`), `app.css` (flash animation, `prefers-reduced-motion` respected); test `tests/test_web_tower.py` or `test_web_program.py`.

- `data-layer-id` on `.tower-layer` and `.tower-block` divs — attributes only; every string and rect still comes off the renderer, so the agreement rule is untouched.
- Click → find `tr[data-layer-row=id]` → `scrollIntoView({block:"center"})` + a `.row-flash` class removed on animationend.
- Tests (server-side honesty): every drawn layer's `data-layer-id` has a matching `data-layer-row` in the same page; the JS file carries the handler; CSS carries the reduced-motion guard.

### Task 5: merge a duplicate placement on the web

**Files:** Modify `routes/program.py`, `_layers_panel.html` (a "Merge into…" control per section), create `_merge_confirm.html`; test `tests/test_web_program.py`.

- GET `.../program/{placement_id}/merge` → a form in the section's form host: a select of the account's OTHER placements as target, then a confirm sentence naming exactly what moves (submissions/tasks/documents counts are only known after; the confirm states the rule: children move, the source is retired into the target, a file link carries when exactly one side has one, two file-backed refuse).
- POST `.../merge` (target select) → `open_batch(source="web", tool="merge_placements", summary=f"merged {source.ref} into {target.ref}")` around `services.merge.merge_placements` — the same call and tool the TUI's `x` makes, so the changes list reads identically. Success = whole programs panel (the list shrank); MergeError = panel with error slot.
- Tests: form lists only same-account siblings; a merge moves children and retires the source (panel no longer shows it); two file-backed placements refuse with the service's own words; merging into itself is impossible via the form and refused via the route.

### Task 6: ledger, docs, screenshots, gates, review, merge

- SYNC_VERBS final pass (all four new/updated entries), `web/parity.py` prose, DECISIONS.md (the add-line shape decision from Task 1), changelog, handoff `handoffs/20260819-Web-Program-Phase3.md`.
- Screenshots per the CLAUDE.md rule: the lines strip mid-rename, the applies-to chips, the tower click affordance (hover state), the merge confirm — labels S3-n in the artifact.
- Full gates → fresh-eyes review (code-reviewer over branch diff) → fix with tests → merge → remove worktree → publish artifact.

## Self-review notes
- Artifact phase-3 list: lines CRUD (T1+T2), applies_to chips (T3), interactive tower (T4), merge (T5). All covered; nothing new invented.
- The add-line shape (empty line vs line+pending layer) is resolved against towerkit's validator in T1, recorded in DECISIONS.md — not guessed.
