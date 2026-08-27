# 2026-08-27 — The composite rate, and the umbrella that had no door

**Status: MERGED AND PUSHED.** bookkit `main` = `origin/main` = `9fa1bf7`;
towerkit `main` = `origin/main` = `d090d86`. Nothing ahead on any branch, both
feature worktrees removed. Gates green on both repos (bookkit 2,751 tests,
towerkit 1,268; mypy + ruff clean).

Report artifact (screenshots of the running app):
<https://claude.ai/code/artifact/b0cc32b8-aeb8-4806-bfca-05a830655a5e>

These were items 1 and 2 of the four logged at the end of
`20260827-RFI-Subjectivity-Join.md`. Items 3 (program files vs one database)
and 4 (Blocking on Today) are untouched.

---

## Piece 1 — the composite rate (`eac61b4`)

`services.marketing_report.expiring_rate(line) -> ExpiringRate(micros, derived)`
is the ONE definition of what a line expires at. Premium ÷ exposure, scaled by
`rate_per`, monetary-ness off `expiring_basis`. It is a READ — nothing stored.

Readers routed through it: `_rate_move`, `_bridge`, the `ReportBlock`
(`block.expiring_rate` is an `ExpiringRate`, not an int — the stored column is
`None if derived else micros` and deliberately has no second field), the grid
header via `web/marketing_grid.expiring_rate_of`, the cell route in
`routes/marketing.py::_line_cell`, and `mcpserver._set_placement_line`'s reply
(`expiring_rate_derived`).

### Things that will look wrong and are not

- **The derived cell's editor opens EMPTY** while the cell prints a figure.
  Deliberate: blur commits, so a pre-filled derivation is one stray tab from
  being STORED, and a stored rate outranks the division for good. Pinned by
  `test_the_derived_rate_cell_opens_empty_so_a_stray_tab_cannot_store_it`.
- **`_rate_from` lives in marketing_report, not money.py.** It needs
  `monetary`, which is a `models.rating_basis` fact money.py does not know —
  and it is the exact inverse of `_premium_from`, which is right there.
- **G7 allows `repo.marketing._rate_per_guard` to read the column raw.** That
  guard protects the DENOMINATOR under a *stored* rate; the column is genuinely
  its subject. The allowlist is per file AND per enclosing function.

### Gotcha

`Move` carries `.note`, not `.words`. Cost a test run.

---

## Piece 2 — the umbrella (`417eb47` + towerkit `c322ab0`)

**The memory file's diagnosis was WRONG and has been corrected.** towerkit's
`follows_underlying` + `Program.underlying_tops` have always seated a slab at a
different height per line. No remodelling was needed; the refusal was mute.

- towerkit `validate.py`: `line-overlap` appends the verb, only where
  `underlying_tops(above)[line_id] >= below.top` (a slab overlapping a *follows*
  layer is seated onto what sits below THAT, which can be short).
- `sync.set_applies_to(..., follows=True)` — one mutation, one undo unit.
  `sync._rescope` is shared with `rescope_preview` so the dry run cannot drift.
- `sync.follows_would_seat` — `preview()`-based; `write_through` refuses on ANY
  error, so `diags.ok` is the honest "this button will work" predicate.
- `routes/program.py::layer_applies_to_toggle` renders `worksheet_error_action`
  = `{label, url, vals}`; `_worksheet.html` draws it as a `.btn.ws-error-fix`.

### DO NOT force follows on every widen

`sync._reseat_column` does exactly that for INSERTS and copying the rule here is
wrong: widening a GL *primary* onto a line that already has one is
geometrically identical to the umbrella case, and forcing follows would turn a
primary into an excess layer over the other line's primary — silently.
`test_set_applies_to_refuses_a_move_that_overlaps` guards it.

### The defect the build found

`_per_line_seats` (routes/program.py). The position sentence paired the slab
beneath on the layer's FIRST line with the attach off `layer.attach` — which for
a follows layer is the MAX across its lines. The seeded Atomic umbrella printed
"Sits on Primary GL → attaches at $5,000,000" over a $2,000,000 GL primary.
A spanning follows slab now names every seat; a single-line one keeps the
sentence (guarded, and that guard has its own test).

---

## Open, reported not fixed

1. **A typed expiring rate that disagrees with premium ÷ exposure is not
   flagged.** `_reconciles` already drops the bridge when the walk misses by
   >1%, which is where it would reach a client. Grant's call whether the cell
   should say so too.
2. **A spanning slab widened onto a line whose top happens to match keeps a
   PINNED attach** (no follows). Raising a limit underneath then refuses every
   later edit — the wedge shape. Pre-existing; the seeded Atomic umbrella was in
   that state.
3. Six worktrees from earlier sessions sit in `.claude/worktrees/`, all level
   with main, awaiting Grant's go-ahead to remove.

## Verification notes

Sixteen mutations run, each watched red and restored: five on the rate
arithmetic and its readers, four on the web cells, two on towerkit's clause,
four on the follows path, three on the seats. Both features driven in Chrome
against a seeded demo book (`bookctl --db … seed --demo`, then `web --port …`).
