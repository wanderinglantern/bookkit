# Web Program Tab Phase 1 — One Grammar, No Lies — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the web Program tab's editing grammar uniform and its chrome honest — markets edit where they sit, money reads compact but edits exact, hidden layer fields become reachable, dead controls disappear — without adding any new sync verb.

**Architecture:** Markets move onto the existing inline-cell contract (display/edit/save routes per cell), reusing `macros/cell.html` with `tag="span"`; section-level form hosts remain only for section-level acts (new program, new layer, scaffold). All writes keep flowing through `services.program_files.write`. Two new test files make chrome honesty and field reachability machine-checked, the same way `test_dead_keys.py` does for the TUI.

**Tech Stack:** FastAPI + Jinja + htmx (vendored), pytest with `TestClient(base_url="http://127.0.0.1")`, the `snapshot_db` fixture (seeds a book + real towerkit files).

**Spec:** The published review artifact "Web Program Parity Review" (2026-08-19), Phase 1 section, with Grant's decisions D3 (one vocabulary: "program" user-facing), D4 (never draw an inert control), D5 (compact display / exact pre-fill). D1/D2 are phases 2–3, not this plan.

## Global Constraints

- Gates: `uv run --no-sync python -m pytest -q`, `uv run --no-sync python -m mypy src`, `uv run --no-sync python -m ruff check src tests` — never pipe test output before the `&&` gate; redirect to the scratchpad, gate on the command, tail the file.
- Work in `.claude/worktrees/web-program-phase1`, branch `web-program-phase1`.
- Every program write goes through `services.program_files.write` — no direct `sync.*` calls from routes.
- htmx refusals are 200-with-message in the page, never a bare 4xx/5xx (htmx drops non-2xx swaps).
- Forms target `closest .form-host` / their own anchor, never the panel (a refusal is not a panel).
- Editor pre-fill for money/share stays EXACT (`initial_text`); D5 changes DISPLAY strings only.
- A cell macro's element must be legal where it lands (`td` only under `tr`; use `tag="span"` inside a `td`).
- Money cells: display via `money.format_cents_compact`; empty stays "—".

---

### Task 1: Compact money display, exact pre-fill (D5)

**Files:**
- Modify: `src/bookkit/web/routes/program.py` (`_layer_row`, `_layer_display_cell`, the "MONEY IS SHOWN EXACT" comments)
- Test: `tests/test_web_program.py`

**Interfaces:**
- Produces: `_display_text(field: Field, value: Any) -> str` in `routes/program.py` — money kinds → `format_cents_compact(int(value))`, `None` → `""`, everything else → `initial_text(field, value)`. Tasks 2 and 5 call it for every display cell.

- [ ] **Step 1: Write the failing tests** (replace `test_layer_money_is_formatted_not_raw_cents`'s exact-string expectations; add the split assertion):

```python
def test_money_cells_display_compact(app_and_org):
    """D5 (2026-08-19): display is compact; the editor pre-fill stays exact.
    The old exact-display rule guarded against a compact string being parsed
    back lossily — severed by splitting display from pre-fill, not by
    weakening the parser rule."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    layer = sync.layer_details(conn, placement.id)[0]

    page = client.get(f"/accounts/{org.ref}/program").text

    from bookkit.money import format_cents_compact
    assert format_cents_compact(layer["limit_cents"]) in page

def test_money_editor_prefill_stays_exact(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    layer = sync.layer_details(conn, placement.id)[0]

    editor = client.get(_cell(org, placement, layer, "limit_cents") + "/edit").text

    exact = format_cents(layer["limit_cents"]).lstrip("$")
    assert f'value="{exact}"' in editor
```

- [ ] **Step 2: Run to verify the new ones fail** (`uv run --no-sync python -m pytest -q tests/test_web_program.py -k compact_or_prefill`)
- [ ] **Step 3: Implement `_display_text` and use it in `_layer_row.cell()` and `_layer_display_cell`; rewrite the exact-money comments to record D5.**
- [ ] **Step 4: Full program test file green.**
- [ ] **Step 5: Commit** `feat(web): money cells display compact, edit exact (D5)`

### Task 2: Markets join the inline-cell contract (+ fixes the share pre-fill bug)

**Files:**
- Create: `src/bookkit/web/templates/account/_market_chip.html`
- Modify: `src/bookkit/web/routes/program.py`, `src/bookkit/web/templates/account/_layers_panel.html`, `src/bookkit/web/static/app.css`
- Test: `tests/test_web_program.py`

**Interfaces:**
- Produces: `_market_cell_action(ref, placement_id, layer_id, index, key) -> str` returning `/accounts/{ref}/program/{placement_id}/layers/{layer_id}/markets/{index}/cell/{key}`; `_participant_fields(conn) -> tuple[Field, ...]` (PARTICIPANT_FIELDS with carrier `suggestions=tuple(vocab.market_names(conn))` via `dataclasses.replace`); route `GET .../markets/{index}` returning one chip (Task 3's "keep" uses it); `_market_chip(request, ref, placement_id, layer_id, index, seat) -> str`.
- Routes: `GET .../markets/{index}/cell/{key}` (display), `GET .../markets/{index}/cell/{key}/edit` (editor), `POST .../markets/{index}/cell/{key}` (save — path unchanged from today). DELETE the old `GET .../markets/{index}/edit/{key}` mini-form route.

**Known live bug fixed here:** the old mini-form pre-filled the share by passing PERCENT (`seat["share_pct"]`, e.g. 40.0) into `initial_text`, whose share kind formats BPS — so a 40% seat pre-filled "0.4" and an unedited save would write 0.4%. The regression test is non-negotiable.

- [ ] **Step 1: Failing tests:**

```python
def test_share_editor_prefills_the_actual_percent(app_and_org):
    """40% must pre-fill '40', not '0.4' — the old form fed percent into a
    bps formatter, and an unedited save would have cut the share 100x."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer_id, seat_index, seat = _first_seat(conn, org)

    editor = client.get(_market_cell(org, placement, layer_id, seat_index, "share_pct") + "/edit").text

    assert f'value="{seat["share_pct"]:g}"' in editor

def test_market_cells_render_in_the_row_not_a_form_host(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer_id, seat_index, seat = _first_seat(conn, org)

    page = client.get(f"/accounts/{org.ref}/program").text

    action = _market_cell(org, placement, layer_id, seat_index, "carrier")
    assert f'data-cell-action="{action}"' in page

def test_market_share_saves_through_the_cell_and_updates_signed(app_and_org):
    ...  # POST .../cell/share_pct with "35"; assert file bps == 3500,
         # response carries the display cell AND the OOB panel (heal/signed refresh)

def test_carrier_editor_offers_existing_market_names(app_and_org):
    ...  # GET carrier editor; assert "<datalist" and a seeded market name in it
```

- [ ] **Step 2: Run; fail** (routes/partial absent).
- [ ] **Step 3: Implement:** `_market_chip.html` renders `<span class="market">` holding the two cells (`render_cell_display(..., tag="span")`; carrier display = `seat["carrier"]`, share display = `f"{seat['share_pct']:g}%"`; share editor pre-fill = `f"{seat['share_pct']:g}"` — NOT `initial_text`) plus the remove button (unchanged this task). `_layers_panel.html` includes it per participant. Routes mirror `layer_cell*`: refusal → editor cell with error; conflict handling identical to layer cells (`_is_conflict` → the three-way is layer-cell-specific; for markets a conflict returns the editor with the message this phase); success → display cell + `_panel` OOB. POST body key mapping stays `{"share_bps": parsed}` / `{"new_carrier": parsed}`.
- [ ] **Step 4: Update the two existing tests that used the old edit route (`test_a_market_can_be_reached_for_editing_from_the_page`, `test_a_markets_share_is_corrected_in_place`) to the cell contract; whole file green.**
- [ ] **Step 5: Commit** `feat(web): markets edit in the row via the cell contract; fix 100x share pre-fill`

### Task 3: Market remove gets an in-place confirm

**Files:**
- Create: `src/bookkit/web/templates/account/_market_confirm.html`
- Modify: `src/bookkit/web/routes/program.py`, `_market_chip.html`
- Test: `tests/test_web_program.py`

**Interfaces:**
- Consumes: `GET .../markets/{index}` (chip re-render) from Task 2.
- Produces: `GET .../markets/{index}/remove` → confirm fragment swapped over `closest .market` (writes nothing); `POST .../markets/{index}/remove` unchanged semantics (panel OOB on success).

- [ ] **Step 1: Failing tests:** GET remove returns the carrier name + "the layer stays" and leaves the file untouched (sha unchanged); the chip's remove button is `hx-get` (confirm) not `hx-post`; POSTing after confirm removes the seat (existing removal test keeps passing).
- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement:** confirm span with `[take off]` (`hx-post .../remove`, target `closest .market`, success = empty target + panel OOB) and `[keep]` (`hx-get .../markets/{index}`, swap `outerHTML` on `closest .market`). Message: `take {carrier} off {layer}? the layer stays, and R reverts this`.
- [ ] **Step 4: Green.**
- [ ] **Step 5: Commit** `feat(web): market remove confirms in place`

### Task 4: Market add moves into the row

**Files:**
- Create: `src/bookkit/web/templates/account/_market_add.html`
- Modify: `src/bookkit/web/routes/program.py`, `_layers_panel.html`
- Test: `tests/test_web_program.py`

**Interfaces:**
- Produces: `GET .../layers/{layer_id}/markets/new` now returns the inline add form (swapped over `closest .market-add`, outerHTML); `GET .../layers/{layer_id}/markets/button` returns the collapsed `+ market` control (cancel target); `POST .../layers/{layer_id}/markets` unchanged path — refusal re-renders `_market_add.html` with the error and typed values at the same anchor, success returns empty + panel OOB.
- Deletes: the form-host `market_add_form` flow (`_mini_form` stays — layer add still uses it).

- [ ] **Step 1: Failing tests:** the rendered `+ market` control carries `hx-target="closest .market-add"`; a refused add (blank share) returns the inline form with the typed carrier intact and the page's panel id absent from the fragment (it must not be a panel swap); a good add binds (existing bind test keeps passing, retargeted).
- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement.** Carrier input uses `_participant_fields(conn)` so the datalist rides along.
- [ ] **Step 4: Green** (including `test_a_blank_share_is_refused_in_the_broker_s_language` updated to the new fragment).
- [ ] **Step 5: Commit** `feat(web): market add is an in-row form, with carrier completion`

### Task 5: Layer details expander — policy number and policy dates become reachable

**Files:**
- Modify: `src/bookkit/web/routes/program.py`, `_layers_panel.html`, `static/app.css`
- Test: `tests/test_web_program.py`

**Interfaces:**
- Produces: `GET /accounts/{ref}/program/{placement_id}/layers/{layer_id}/details` → `<tr class="layer-details"><td></td><td colspan="6">` holding three labeled cells (`policy_number`, `period_from`, `period_to`) rendered with `render_cell_display(tag="span")` and `_display_text`, plus a close button (`onclick="this.closest('tr').remove()"`). The table gains a leading chevron column (`<th></th>` + per-row `<td class="row-expand">` button, `hx-get` the details route, `hx-target="closest tr"`, `hx-swap="afterend"`).

- [ ] **Step 1: Failing tests:**

```python
def test_layer_details_row_carries_the_three_hidden_fields(app_and_org):
    ...  # GET details; assert data-field="policy_number|period_from|period_to"

def test_every_layer_row_offers_its_details(app_and_org):
    ...  # program tab contains the details hx-get for each layer
```

- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement.** Note in the template: any panel OOB re-render closes open details rows — accepted; the row is one click away and the alternative (re-seating open expanders server-side) buys little.
- [ ] **Step 4: Green. A policy_number save through the existing cell POST works from the details row unchanged (the routes already accept every `_LAYER_CELLS` key — that was finding F6).**
- [ ] **Step 5: Commit** `feat(web): layer details row makes policy number and dates reachable`

### Task 6: One open editor per section (form-host hygiene)

**Files:**
- Create: `src/bookkit/web/static/form-host.js`
- Modify: `src/bookkit/web/templates/base.html`
- Test: `tests/test_web_shell.py`

**Interfaces:**
- Produces: on `htmx:beforeSwap` whose target is a `.form-host` receiving non-empty content, every OTHER `.form-host` whose inputs/selects all still equal their `defaultValue` is emptied; one with typed input is left alone (commit-in-place: typed work is never discarded).

- [ ] **Step 1: Failing test:** `base.html` references `/static/form-host.js` and the file serves 200 with `application/javascript` (the suite has no JS runtime; the honest server-side assertions are presence + serving + the asset-version glob covering it).
- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement (~20 lines, mirroring inline-cell.js's addEventListener style).**
- [ ] **Step 4: Green.**
- [ ] **Step 5: Commit** `feat(web): opening a form closes other untouched forms`

### Task 7: Dead chrome — wire it or hide it (D4), with the honesty tests

**Files:**
- Modify: `src/bookkit/web/templates/partials/topbar.html`, `src/bookkit/web/templates/account/page.html`, `src/bookkit/web/routes/work.py`
- Create: `tests/test_web_dead_controls.py`
- Test: existing `tests/test_web_shell.py` expectations updated

**Interfaces:**
- Topbar nav renders links for routed sections only (Book); the six inert spans go.
- Header actions: `+ Task` becomes `<a href="/accounts/{ref}/work?add=task">`; `work_tab` accepts `add=task` and renders the new-task form pre-opened in the tab's form host (reusing the existing tasks/new GET fragment). `+ Log interaction` (creation is deliberately not a web flow — quick capture's job), `Renew` (phase 2 wires it), `···`, and the rail's `Assign` are removed, not hidden with CSS.
- Produces: `tests/test_web_dead_controls.py` with three guards later phases inherit:

```python
PAGES = ["/book", "/accounts/{ref}/program", "/accounts/{ref}/relationship",
         "/accounts/{ref}/work", "/accounts/{ref}/pipeline"]

def test_no_control_admits_it_is_not_wired(...):
    # for each page: assert "Not wired yet" not in html
    # and no <span class="topbar-nav-item"> (non-anchor nav) remains

def test_every_rendered_action_resolves(...):
    # regex hx-get/hx-post/href/form-action paths out of each page;
    # skip '#'/'http'; assert app.router matches each (Match.FULL) with the
    # right method — a control pointing nowhere turns the suite red

def test_every_editable_layer_field_is_reachable(...):
    # union of data-field values on the program tab plus each layer's
    # /details fragment == set(_LAYER_CELLS) — kills the invisible-field
    # class (F6) permanently
```

- [ ] **Step 1: Write the three tests; run — `test_no_control_admits_it_is_not_wired` and reachability fail against current templates (reachability passes only after Task 5 — run it now to confirm it PASSES, i.e. it is Task 5 that made it true; then mutate Task 5's template chevron out locally to watch it fail, per verifying-tests-can-fail).**
- [ ] **Step 2: Implement the template/route changes until all three pass.**
- [ ] **Step 3: Update any shell/snapshot tests that asserted the placeholder spans.**
- [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** `feat(web): dead chrome removed or wired; honesty tests added (D4)`

### Task 8: One vocabulary (D3)

**Files:**
- Modify: `src/bookkit/forms/entities.py` (placement_form title → "new program" / "edit program"), `src/bookkit/tui/screens/account.py` (tab label "Placements" → "Programs"; notify strings naming "placements tab"), `src/bookkit/web/routes/program.py` (scaffold refusal drops "or unlink it first" — the verb doesn't exist until phase 2), `src/bookkit/web/parity.py` (entry text where it describes flows this plan moved)
- Test: whatever `test_dead_keys.py` / TUI tests assert about the tab label — the test is the arbiter; update text where the CHANGE is intended, never to silence a real drift.

- [ ] **Step 1: Grep for user-facing "placement"/"Placements" strings in web templates + TUI labels; change per D3 (code identifiers, refs `PLC-`, and DB terms stay).**
- [ ] **Step 2: Full suite; fix intentional-string assertions.**
- [ ] **Step 3: Commit** `chore: one user-facing word — program (D3)`

### Task 9: Decisions, ledger, gates, review, merge, handoff

**Files:**
- Modify: `DECISIONS.md` (five lines D1–D5; D1 noted as superseding the design-boundary line for LINES when phase 3 lands; D5 superseding exact-display: display compact, pre-fill exact), `changelog.md` (per its own bottom prompt), `web/parity.py` final pass
- Create: `handoffs/20260819-Web-Program-Phase1.md`

- [ ] **Step 1: Full gates in the worktree** (`pytest -q` → scratchpad file, `mypy src`, `ruff check src tests`).
- [ ] **Step 2: Fresh-eyes review (code-reviewer agent over the branch diff); fix what's real.**
- [ ] **Step 3: Merge `web-program-phase1` → main, remove the worktree, update the published review artifact (decisions marked decided, phase 1 marked built), write the handoff.**

## Self-review notes

- Spec coverage: F1 (T2/T4), F2 (T6), F3 (T7), F6 (T5+T7 test), F7 (T1), F8 (T3), F9 (T8), F10 (T2/T4 datalist). F4/F5/F11/F12 are phases 2–3 by design. Share pre-fill bug: T2.
- The conflict three-way stays layer-cell-only this phase; a market-cell conflict refuses with the message (documented in T2).
- No new sync verbs anywhere; every write path already exists.
