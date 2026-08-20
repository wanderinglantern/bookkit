# Web Program Phase 4 — Outputs, Browse, and the Rest of the Structure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The web produces the artifacts the terminal can (tower SVG/PDF, schematic workbook, open-items workbook — the file-download gap closes), gains the Towers browse page and the Compare delta table (spec D8 slice 5), and finishes the structure surface TOWERKIT_EDIT_OPS defers to this phase by name: retentions, sublimits, line order, restack.

**Architecture:** Downloads are PLAIN ANCHOR GETs answering `Content-Disposition: attachment` — no htmx (browsers handle download navigation natively; a swap contract has nothing to add), artifacts rendered to a per-request temp dir by towerkit's own renderers (render_program, add_schematic_sheet+finalize_workbook) or by services.export_open_items (CALLED, NEVER MODIFIED — Grant has uncommitted edits to that file on main's checkout). Structure editors follow the phase 1–3 grammar; retentions/sublimits are index-addressed like market seats (towerkit's own addressing; every write re-renders the panel so an index is never stale). Compare follows the spec: compare_programs' delta table, auto-detected pair with a picker fallback, no tower graphic.

**Spec:** `docs/superpowers/specs/2026-08-17-towerkit-web-conversion.md` D8 slice 5 + its "recommend against SVG-in-Compare"; the parity artifact Phase 4 section; TOWERKIT_EDIT_OPS deferrals.

## Global Constraints

- Phase 1–3 Global Constraints verbatim (gates, worktree `.claude/worktrees/web-program-phase4`, program_files.write seam for every file write, panel-shaped answers to panel-targeted POSTs, COMMIT BEFORE MUTATION CHECKS, gate exit codes never behind `;` or a pipe).
- **`services/export_open_items.py` and `services/export_rfi.py` are READ-ONLY this phase** — Grant has uncommitted work touching both on the main checkout; modifying them guarantees a conflict with in-flight human work.
- Retention/sublimit AMOUNTS are cents in bookkit, whole dollars in towerkit — `_require_dollars` at the sync wrapper, like every money field.
- New sync mutators must land with SYNC_VERBS entries and TOWERKIT_EDIT_OPS flips in the same commit (the ledger tests force it).
- towerkit APIs used must exist on towerkit MAIN (verified: add/edit/remove retention+sublimit, move_line, restack, render_program, add_schematic_sheet, finalize_workbook, compare_programs, load_theme).

---

### Task 1: sync wrappers — retentions, sublimits, line order, restack

**Files:** `src/bookkit/sync.py`, `src/bookkit/web/parity.py`; test `tests/test_program_edits.py`.

```python
def add_retention(conn, placement_id, applies_to: list[str], type: str,
                  amount_cents: int, aggregate_cents: int | None = None) -> Diagnostics
def edit_retention(conn, placement_id, index: int, *, applies_to=None, type=None,
                   amount_cents=None) -> Diagnostics   # None = leave alone; vehicle/notes preserved
def remove_retention(conn, placement_id, index: int) -> Diagnostics
def add_sublimit(conn, placement_id, name: str, amount_cents: int,
                 applies_to: list[str]) -> Diagnostics
def edit_sublimit(conn, placement_id, index: int, *, name=None, amount_cents=None,
                  applies_to=None) -> Diagnostics
def remove_sublimit(conn, placement_id, index: int) -> Diagnostics
def move_line(conn, placement_id, line_id: str, delta: int) -> Diagnostics
def restack(conn, placement_id) -> Diagnostics
def restack_plan(conn, placement_id) -> list[tuple[str, int, int]]
    # (layer name, attach_cents before, attach_cents after) — the BEFORE/AFTER
    # a confirm shows; loads a copy, simulates edit.restack, writes NOTHING
```
- Index guards via `_at`-equivalent ValueError (towerkit `_at` raises what? verify — wrap so _mutate catches).
- Tests: each op round-trips to the file; bad index refuses with file untouched; amounts refuse sub-dollar cents; move_line off the end is a no-op not an error (towerkit's contract); restack_plan writes nothing and matches what restack then does; SYNC_VERBS discovery test forces the 8 ledger entries (restack_plan is read-only — confirm the discovery regex doesn't catch it, or list it as a read).

### Task 2: web structure editors — retentions & sublimits strip, line order, restack

**Files:** `routes/program.py`, `_layers_panel.html`, new `_retention_chip/_retention_form/_sublimit_*/_restack_confirm.html`, app.css; test `tests/test_web_program.py`.

- A "terms" strip under the lines strip: each retention as a chip (`DEDUCTIBLE $250K · GL`) that opens an in-row edit form (type select over RetentionType, amount money, applies-to line select w/ "all lines"), plus remove behind an in-place confirm; `+ retention` / `+ sublimit` in-row adds (market-add pattern, literal routes before `{index}`). Sublimit chips: `name $amount · lines`.
- Line chips gain ◂ ▸ (POST `/lines/{line_id}/move` delta=-1/+1; success = panel; a no-op move returns the panel unchanged rather than refusing, per towerkit's contract).
- Restack: a control near the strip → GET confirm rendering `restack_plan`'s before/after table ("Umbrella: attaches $2M → $5M …", or "nothing would move") → POST runs `sync.restack` through program_files.write (`tool="program_restack"`). Refusals in place.
- Tests: chips render from the file; edit round-trips; remove confirms first; add refuses bad money in place; reorder swaps tower columns (program_lines order changes); restack confirm shows the plan and writes nothing; restack executes the same plan; dead-controls + reachability suites inherit.

### Task 3: downloads — the file-response decision lands

**Files:** `routes/program.py` (or new `routes/exports.py`), `_layers_panel.html` + account `page.html` (links), DECISIONS.md; test `tests/test_web_exports.py`.

- **Decision (recorded):** downloads are plain `<a href>` GETs returning the bytes with `Content-Disposition: attachment; filename=...` — no htmx, no swap contract; rendered into a per-request `tempfile.TemporaryDirectory`.
- Routes (all `_owned`-guarded, all refuse-as-page when unlinked — an anchor GET can still land on a readable refusal page… NO: a bare GET refusal should be a small HTML page saying why, status 200, since the browser navigated. Render the program tab with the panel error instead? Simplest honest: redirect back to the program tab with the message in the panel error slot is stateful — instead answer a minimal error page with a back link):
  - `GET /accounts/{ref}/program/{placement_id}/export/tower.svg` and `/tower.pdf` — `render_program(program, load_theme(), tmp, stem, formats=[...])`
  - `GET /accounts/{ref}/program/{placement_id}/export/schematic.xlsx` — `Workbook()` (drop default sheet) + `add_schematic_sheet` + `finalize_workbook`
  - `GET /accounts/{ref}/export/open-items.xlsx` — `services.export_open_items.write(conn, org.id, tmp_path, date.today())` — READ-ONLY call
- UI: an export cluster in each linked program section (`tower SVG · tower PDF · schematic XLSX`) as real anchors; the account header gains `Open items XLSX` next to + Task. web/parity's `export_open_items` PENDING entry flips (merge_placement half already lives).
- Tests: each route answers 200 with the right Content-Type + attachment filename; SVG body contains the program name (renderer agreement — the string came off the renderer); xlsx magic bytes (PK); unlinked placement gets the readable refusal; foreign ref 404s; the anchors render (dead-controls resolves them automatically).

### Task 4: the Towers page

**Files:** new `routes/towers.py` + `templates/towers.html`, `partials/topbar.html` (Towers joins the ROUTED nav), app registration; test `tests/test_web_towers.py`.

- `GET /towers`: every linked placement across the book (`placements.all_linked`), grouped by account: account name (link), program name + period + status, the validation badge (`validate_file` → ok / N errors / M warnings, colour + word), and the drawn tower (reuse `web/tower.panel` + `_tower_panel.html`). A file that fails to load renders its error badge instead of killing the page.
- Topbar: `Towers` becomes the second real nav link; dead-controls' no-span rule already enforces honesty.
- Tests: page lists every linked placement and no unlinked one; a corrupted file yields a badge not a 500; nav link present on /book and account pages.

### Task 5: Compare (D8 slice 5, spec-shaped)

**Files:** `routes/program.py` or `routes/compare.py`, `templates/account/compare.html`, section control; test `tests/test_web_compare.py`.

- `GET /accounts/{ref}/program/{placement_id}/compare[?with={other_id}]`: auto-detect the expiring partner — same org, linked, and `period_to == placement.period_from` (the renewal adjacency rule); ambiguity or absence → a picker of the account's other linked placements (the spec's recommended posture). Renders `compare_programs(expiring, proposed)`'s DeltaRows as the delta table: carrier, layer, status word+colour (NEW / RENEWED / LAPSED), share old→new, premium old→new (compact display). READ-ONLY; no tower graphic (spec's "recommend against").
- Control: `Compare` joins the linked-section controls.
- Tests: adjacent pair auto-detects; explicit `with` overrides; no candidate → picker page; delta rows match compare_programs' output for a placement whose renewal changed a share; page marks NEW/LAPSED rows.

### Task 6: close-out

- Screenshots S4-n (terms strip + restack confirm, export cluster, Towers page, Compare table) per the CLAUDE.md rule; ledgers final pass; DECISIONS.md (download pattern; compare auto-detect posture); changelog; handoff `20260819-Web-Program-Phase4.md`; full gates; fresh-eyes review; fix; merge; artifact + memory.

## Self-review notes
- TOWERKIT_EDIT_OPS deferred-to-phase-4 entries all land in T1/T2 (retentions ×3, sublimits ×3, move_line, restack — set_line_group stays deferred: Line.group isn't even projected; note it stays by name).
- export_rfi is not wired this phase (Grant is actively editing it); named in the handoff, not silently dropped.
