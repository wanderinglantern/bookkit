# 2026-08-24 — The Program Worksheet (design 1C, built)

## Goal

Implement the program-worksheet redesign from the Claude Design project
(`Program Editing Directions.dc.html`, chosen direction **1C**, plus the
turn-2/turn-3 screens and the `design_handoff_program_worksheet/README.md`
hand-off). One grammar everywhere: structure index left, one layer's
worksheet right, figures in serif, every number typed, every write shown
before it lands.

## State — COMPLETE, merged to main

All phases built, gated (full suite + mypy + ruff green), reviewed
(adversarial workflow), screenshotted, and reported in the artifact
"Program Worksheet — build report". Branch `feat/program-worksheet`.

Phases as commits:
- `feat(sync)` — preview / move_layer / split_layer / derived capacity
- `feat(web)` — index + worksheet + band + rail (the big one)
- `feat(web)` — write preview + rescope consequence
- `feat(web)` — Towers queue
- `feat(web)` — Compare as a renewal report
- `feat(web)` — new-program worksheet

## The load-bearing decisions

- **Selection is a section render.** `GET .../worksheet?layer=&closed=`
  answers the whole section, retargeted — the same swap discipline every
  write uses. State recovers from `HX-Current-URL` on writes
  (`_view_state`), so a save never throws the broker to the first layer.
  The hand-off suggested a pane-only GET; the section swap is one swap
  discipline instead of two and honours one-response-one-element. Recorded
  as a deviation in the report.
- **`_worksheet_ctx` is the pane's one context builder** (absorbed
  `_details_row`'s one-renderer rule). A pane that fails to build logs and
  prints why; the index and band survive.
- **Structural refusals** answer `_panel(selected=…, worksheet_error=…)` —
  200, retargeted, file untouched. Form-shaped refusals (insert, split,
  market add row, named-limit add) re-render their own fragment with the
  typing kept.
- **The share input is the ONE blur-commit exception**, recorded beside the
  rule in `inline-cell.js`. It is a plain input, not a `.cell`; Save posts
  the ordinary cell route. Previews report only warnings the change
  *introduces* (`sync._new_warnings`) — repeating standing warnings claims
  the edit caused them.
- **`sync.preview`** shares the sha guard with `write_through`
  (`_refuse_stale`) so the dry run and the commit can never disagree.
- **attach_cents is not an editable web field** (dropped from
  `_LAYER_CELLS`; parity `Layer.attach` row names the new home).
  `sync.update_layer(attach_cents=)` stays for MCP.
- **`_reseat_column`** is the one home of the reseat loop (insert + move);
  the follows-underlying threshold-seed comments live on `insert_layer`.
- **Towers order** counts to the earliest LINE end (`sync.line_ends_of`),
  never `period_to` — the standing renewal rule.
- **The market row class is `market-row`** — `.market` is the old chip's
  inline-flex CSS and destroys a `<tr>`'s table display (found by
  screenshot, not by tests).

## Deviations from the mock, deliberate

- Money DISPLAY stays compact (D5) in stat cells; the mock showed exact.
  Editors still pre-fill exact. Derived (non-editable) $ columns show exact.
- "Point at the file…" (broken state) not built — recovery is read-only;
  `bookctl relink` is the writer (constraint 9).
- "Copy another account" source card deferred; "Link an existing one…"
  deferred (no unlink/link verb on the web yet).
- Status chips use the real controlled tuple (prospective/submitted/…), not
  the mock's words.
- Keyboard extras (↑/↓ through the stack, ⇧⏎/⌥⏎/⌘⏎) not wired yet — the
  index rows and all controls are real buttons, so Tab reaches everything.
- Recent-changes stays in the page rail (not duplicated into the program
  rail).

## Gotchas for the next session

- A fresh worktree: `uv sync --group dev`, gates as
  `uv run --no-sync python -m pytest`.
- The web server does NOT reload — kill and restart `bookctl web` after
  edits (screenshots against a stale server cost an hour once).
- tests/test_conventions.py: `_layers_panel.html` may appear exactly once in
  routes/program.py and be included by no template — it is the SECTION
  template now (band + workbench + states), name kept.
- The one-file-open invariant: `_worksheet_ctx` reads everything off the
  memoised `linked_for` program via the `_of` readers
  (`layer_named_limits`, `policy_partners_of`, `program_lines_of`,
  `layer_details_of`). Adding a `conn`-taking sync reader to a render
  builder breaks `test_the_program_tab_opens_each_file_once`.
- `/program/{placement_id}/{kind}` still registers LAST; `/worksheet` and
  `/remove` are named in its comment. `/program/new` is a sibling of the
  `{placement_id}` family — fine because all its routes have ≥2 trailing
  segments.
- The new-program page is CLASSIC form posts (no htmx) on purpose — every
  act re-renders with typing kept; `act=stack` requires a line, and the
  select fills from the lines typed on the PREVIOUS render (type lines →
  any submit → select populates).

## Open questions / next

- Grant's queued follow-up: `Web UI System Pass.dc.html` from the same
  design project (import via DesignSync, project
  609b7dd6-c956-46ee-bf3d-5485c2bdfafe).
- Keyboard pass (↑/↓ selection, ⌘⏎ save-preview) if Grant wants it.
- Towers "renewing" badge uses warn for overdue too — danger variant is a
  one-line polish if wanted.
