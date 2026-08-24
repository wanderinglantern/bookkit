# Surface-review fixes — handoff

**Date:** 2026-08-24 · **Repos:** bookkit `main` @ `163690f`, towerkit `main` @ `909a30a` · **Branch to cut:** none yet

## Goal

Fix what the first surface-consequence sweep found. Three agents ran the three
questions in `.claude/skills/surface-consequence-review/SKILL.md` over both
repos; every finding below was then **re-verified by hand** against the running
app or an in-memory repro. Findings that did not survive verification, and
design costs that are not defects, are recorded at the bottom so nobody
re-litigates them.

Everything here is on `main` and shipped. Nothing is in flight.

## State

- Shipped today: the tower export fixes (harbor theme, callouts, carets), the
  stated market premium, lines of coverage + the rail, and the two rail
  affordance fixes. All merged and pushed.
- A test book with every relevant shape is at
  `<scratchpad>/testbook/bookkit.db`, served on `:8931`
  (`BOOKKIT_DB=… uv run --no-sync python -m bookkit.cli web --port 8931
  --no-browser`). It has: a shared Umbrella (Swiss Re / Chubb) for the premium
  work, ACC-0036 with a statutory scaffold, and several `To be placed` layers.
- Gates before this work: bookkit 2224 passed, towerkit 1211 passed (one
  pre-existing environment-dependent `test_connector` failure).

## The fixes, in order

| # | What | Where | Severity |
|---|------|-------|----------|
| 1 | `Layer.premium` goes stale on bind / unbind / split | `sync.py`, towerkit `edit.py` | money, on main |
| 2 | Carrier blocks are white-on-white in the client workbook | `schematic_xlsx.py:488` | client-facing |
| 3 | Pipeline bind picker collides → binds to the wrong line | `routes/pipeline.py:376` | wrong write |
| 4 | `split_layer` writes past `_guard_premium` | `sync.py:1376` | correctness |
| 5 | A layers-empty program hides its own errors and its terms | `_layers_panel.html:108` | silent |
| 6 | The rail cannot rename / relabel / remove a line | `_structure_index.html` | affordance |
| 7 | Advisories discarded → MCP says nothing | `sync.py:1943` | parity |
| 8 | Blank clears every seat with no confirm | `routes/program.py:2969` | data loss |
| 9 | The renewal date is derived twice, one uncapped | `routes/towers.py:118` | house rule |
| 10 | Market page's 90-day window filters `period_to` | `repo/projection.py:122` | house rule |
| 11 | `work.xlsx` missing from `/exports`; `/exports` unlinked | `exports.html:25`, `topbar.html` | reach |
| 12 | The browser tower never draws the statutory chevrons | `web/tower.py:122` | agreement |

### 1. `Layer.premium` goes stale — DO THIS FIRST

`edit.set_participant_premium` establishes "the layer's premium IS the sum of
its markets" and holds it **only while it is the thing writing**. Every other
writer of the participant list leaves the sum stale.

Reproduced on the seeded book: state Chubb at $520,000 on the Umbrella (which
freezes Swiss Re and sets the layer to $1,960,000), then unbind Swiss Re —

```
AFTER UNBIND  layer premium 196000000   seats [('Chubb', 52000000)]
              sum of what seats are paid: 52000000
              placement.total_premium unchanged: 369000000
```

A phantom $1.44M rides `sync.py:285` into `placement.total_premium`, the Book
headline and the account header, while `proj_participant` stays correct — so
the by-market column and the by-program column disagree **from one file**.
Binding is the mirror: a new market takes a share of a base that is already
the other seats' money in full.

**The fix is a heal step in the write path, never a re-sum at each mutation
site.** `heal_follows` is the precedent: bookkit's `sync.write_through`,
towerkit's `mcpserver._write` and towerkit's TUI `EditSession.mutate` all run
it between the mutation and the validation. Add `edit.heal_premiums(program)`
beside it, with one rule:

> If EVERY seat on a layer states a premium, `layer.premium` is their sum.

That makes unbind self-correcting; leaves bind in the warned mixed state until
the broker prices the new market (which the existing verb then resolves, since
`underived` is empty and it simply sets and re-sums); and needs no new
vocabulary. `validate._check_premium_split` already reports the mixed state.

Tests: unbind on a fully-stated layer re-sums; bind leaves the warning and does
NOT invent a figure; the mutation check is removing the heal call.

### 2. Carrier blocks are invisible in the schematic workbook

`towerkit/src/towerkit/render/schematic_xlsx.py:488-498` fills a carrier block
and borders it with `chrome.background` — **white** — on all four sides.
Nothing else outlines it (`_gridlines` skips `occupied`, and unlike
`mpl_program.py:229-235` the workbook draws no per-layer ink rectangle).

Verified on the live export (`GET …/export/schematic.xlsx`):

```
  B8   #CEECFF  vs paper 1.23:1   borders ['FFFFFF']   <-- invisible
  B73  #9CC9EA  vs paper 1.76:1   borders ['FFFFFF']
  B92  #EDEAE4  vs paper 1.20:1   borders ['00205B']   <-- a RETENTION, bounded
```

The retention block is the proof: the same workbook bounds retentions with ink
and leaves carriers unbounded. It only became visible now because harbor's
palette has two entries under 1.25:1 where `default`'s floor was 2.12:1, and
colours are assigned by first appearance — so `#CEECFF` lands on the **fourth
carrier of any tower**.

Fix: border carrier blocks with `chrome.ink` like the retentions. Then add the
missing invariant as a gate — `tests/test_theme_resolution.py` already asserts
every fill LABELS at ≥4.5:1 and that neighbours step in lightness; nothing
asserts a fill is distinguishable from the PAPER it sits on. State it over
every installed theme.

### 3. The pipeline bind picker binds to the wrong line

`routes/pipeline.py:376-383` labels each layer
`"{name}  {limit} xs {attach}  ({signed}% placed)"` — no line of coverage. On
the test book three options are byte-identical (`To be placed  $5M xs $0  (0%
placed)` for Workers Compensation, Crime and Fidelity).

This is **worse than the "same policy as" collision fixed in `866c43c`**:
there the write was addressed by id so nothing was ever linked wrongly; here
the id is correct for whichever option is clicked, so a mis-click writes a
real participation on the wrong line of coverage through
`sync.add_participant`, in a revertible batch nobody knows to revert.

Fix: apply `_policy_link_options`' rule (`routes/program.py:2606-2635`) —
qualify only the ambiguous labels with their line of coverage. Then widen
`test_no_select_offers_the_same_label_twice` (`tests/test_form_selects.py`),
which today scans only `/accounts/{ref}/program` pages and structurally cannot
see this offer: a gate is only as good as where it looks.

### 4. `split_layer` writes past the guard

`sync.py:1376` — `layer.premium = kept`, a bare setattr, so `_guard_premium`
never runs. Its sibling `update_layer` (`sync.py:1057-1067`) routes the
identical write through `edit_set_field` with a comment saying exactly why.
The result: the split form performs the write the inline cell refuses, and the
split form's own copy asks the broker to divide a market-derived sum by hand
(`sync.py:1327-1345`). The worksheet then shows the typed figure under the
"from markets" hint, beside market rows summing to something else — and the
only exit is clearing every stated premium.

Fix: route it through `edit_set_field`. The refusal message already names the
way out.

### 5. A layers-empty program hides its own errors

`_layers_panel.html:108` — `{% if index and (worksheet or worksheet_failure) %}`
wraps the whole workbench, and `_index_groups` returns `None` when `not layers`
(`routes/program.py:284`). So a linked file with lines and no layers renders
neither the diagnostics block (`:123-128`) nor either terms strip
(`_structure_index.html:99,105`).

Built that state and served it: `program-diagnostics` count **0**,
`+ retention` / `+ sublimit` count **0**, while towerkit reports three
`line-empty` **errors**. The one file the app knows is broken is the one it
says nothing about.

**This is a gap between my own approved plan and my own code** — the plan said
"a line with no layers still gets its group, with a count of zero: a rail that
hid the line would hide the thing the diagnostics point at." Fix as the plan
promised: drop the `not layers` early return, render empty groups, and the
gate stops swallowing the rail.

Reachability, checked: a broker **cannot** reach this from the app — removing
the last layer of a line is refused by `line-empty`. It takes a hand edit,
towerkit's editor or MCP. Real, moderate, not urgent.

### 6–12, in brief

- **6.** The rail carries only the two move arrows. `lines/{line_id}/cell/name`
  (+`/edit`) and `lines/{line_id}/remove` are already routed at
  `routes/program.py:1015-1162`; reuse them exactly as the chips do. Same
  shape as the bug Grant reported, left behind by the same reasoning.
- **7.** `sync.py:1943-1960` discards `edit_set_premium`'s return, so
  towerkit's `premium-frozen` / `premium-summed` advisories never leave
  towerkit. The web has a preview so a human is told; MCP has none, while
  `mcpserver.py:509-513` promises "two are ones you did not send". Thread them
  through `_mutate`'s Diagnostics or return them alongside.
- **8.** `routes/program.py:2969` — `if value is not None and not already and
  not commit:` means a **blank** skips the preview and clears every seat's
  stated premium on blur. Two docstrings (`:2965`, `forms/inline.py:183`) say a
  confirm says so first; no confirm route exists. Either build it or correct
  both docstrings — do not leave them lying.
- **9.** `routes/towers.py:118` uses `min(ends)`; `services/renewals.py:152`
  uses `min(ends[0][1], placement.period_to)` — **capped**. Same fact, two
  answers, and Towers' `renewing` filter measures off the wrong one. Call the
  service.
- **10.** `repo/projection.py:122` filters `p.period_to` for "renewing next 90
  days" on the market page, so a layer that runs out early is invisible there.
  `proj_layer` carries no period column — that is why. CLAUDE.md: the renewal
  date is never `placement.period_to`.
- **11.** `exports.html:25` gains the `work.xlsx` anchor (route
  `work.py:862`); `partials/topbar.html` has eight items and no Exports, so
  the drawer is reachable only from Today.
- **12.** `web/tower.py:122` converts `web.chevrons` into CSS rects that
  `_tower_panel.html` never reads and `app.css` has no rule for — dead since
  `356ecc9`, **not** a regression from the geometry move. A statutory layer
  draws closed-topped in the browser and open-topped everywhere else.

## Open questions — Grant's call

1. **Stating a premium on a partially-signed layer collapses the layer
   premium.** $10M layer, premium $1,000,000, Chubb at 50%; correcting Chubb's
   own figure by $20,000 sets `layer.premium` to $520,000 and drops **$480,000**
   from the account's premium. It follows from "the layer is the sum of its
   seats" and `set_participant_premium` allows it deliberately ("a lone seat
   has nothing to freeze"). Options: refuse on an under-signed layer; keep the
   unplaced share's premium; or accept and show the figure being replaced.
2. **A spanning layer is listed once**, so `AUTO LIABILITY · AL 1` while AL
   carries two layers of cover. Grant chose this over repeating it, to keep the
   counts summing. Suggested softener: have the badge name the lines it spans.
3. **The schematic export ignores `program.render.theme`**
   (`routes/program.py:4220` calls `load_theme()` with no argument while the
   PDF/PNG route four lines up resolves the stored theme). Pre-existing;
   amplified now that the default changed. A program pinned to marsh exports a
   chart in marsh and a schematic in harbor.

## Rejected / not defects

- **The panel is NOT stale after a write.** Diagnosed from the code, "fixed",
  and the fix was wrong: `_panel` already drops the per-request memo, and a
  live line-add returns a section containing the new line with the right
  `HX-Retarget` headers. The five routes that never call
  `forget_program_reads` do not need to. Do not re-fix this.
- The rail dropped attach / premium / carriers columns — deliberate
  index-vs-table design; the worksheet has them one click away.
- `insert buffer` only from a selected layer — near-unreachable state.
- **Retired TUI, flagged not fixed** (CLAUDE.md: do not spend effort):
  `widgets/entity_actions.py:265` and `screens/account.py:2670` build the same
  colliding label as #3 (display only — `picker.py:59` keys by layer id, so no
  `DuplicateKey`); `widgets/link_review.py:116` now reads `(To be placed)`;
  and `entity_actions.py:200-234` always passes `premium` to `update_layer`,
  so the guard refuses **every** save of that form on a market-stated layer.

## Gotchas

- Bash `cwd` resets between calls — `cd` in every one.
- Gates: `uv run --no-sync python -m pytest -q` (bookkit),
  `uv run --group dev pytest -q` (towerkit). Never pipe test output before the
  `&&`; redirect to the scratchpad and tail it.
- `git branch --show-current` in the same call as the commit. A commit meant
  for a branch landed on `main` twice today, once after `git checkout -b` had
  already succeeded.
- Verify every finding against the running app before acting on it. That rule
  is in the skill because it was broken today.
