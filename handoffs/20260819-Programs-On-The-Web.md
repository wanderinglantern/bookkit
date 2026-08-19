# Handoff — programs on the web, phases 1–5

Written 2026-08-19. Assumes `CLAUDE.md` and nothing else. Everything below is
verified state at the HEAD named here, not intention.

## Where things stand

| repo | main | suite |
|---|---|---|
| bookkit | **`2490058`** | **1529 passed**, 38 snapshots, mypy clean (122 files), ruff clean |
| towerkit | `feat/mcp-hardening` checked out — **not main** | see the trap below |

`docs/superpowers/plans/2026-08-19-programs-on-the-web.md` is the plan all five
phases came from. Its five phases are **all built**.

## What you can now do in the browser

See every placement with its layers and the markets on each; rename a layer and
correct its attachment, limit, premium and dates; add a layer; bind a market,
correct its share or name, take it off; create a placement; scaffold its
towerkit file; see the tower drawn; and put a program write back from the rail.

## The decisions that must not be silently reversed

- **Money in an editable cell is EXACT, never compact.** A cell is one string
  for both display and the editor's pre-fill, so "$50M" would parse back as
  $50,000,000 and quietly lose the odd dollars of a layer at $50,123,456.
- **Overwrite is a RETRY, not a force.** Re-project, then re-apply the one
  field. `EditSession.save(force=True)` pushes a whole in-memory program, which
  is right in towerkit's TUI and wrong here.
- **Removed is not withdrawn.** An RFI filed in error goes; one you asked for
  and dropped is `cancelled_at` and stays. Both refuse once anyone has answered.
- **Assignment never blocks a task.** Unknown name kept as typed, no name means
  unassigned.
- **The agreement test never compares the two renderers to each other.** Each
  is compared to the shared authority; comparing them would fail on a
  legitimate wrap and teach everyone to weaken it.

## Traps, in the order they will bite

1. **towerkit's checkout is on `feat/mcp-hardening`, not main.** I merged onto
   it by accident, caught it, and reset to `e9c83bc`; it was never pushed. The
   gamma-agreement test sits unmerged on `web-gamma-agreement` (commit
   `e200fd2`) plus a worktree at `.claude/worktrees/web-gamma`. Somebody has
   towerkit work in flight — do not touch that checkout.
2. **The tower's two axes are in different units.** y is `[0, 1]`; x is COLUMN
   units spanning `[0, TowerLayout.width]`. Treating them alike drew a
   four-line program at `left: 237.5%` with two layers off-screen, and every
   test passed. `tests/test_web_tower.py` holds it now.
3. **A retention rect has a NEGATIVE y** — it is drawn below the zero line by
   construction. Do not "fix" it into the box.
4. **htmx swaps 2xx and drops 4xx/5xx.** Every refusal on these surfaces
   returns 200 with the message in the page. A destructive control that
   refuses with a status code produces no swap, no message and no change.
5. **A form must target `closest .form-host`, never the panel.** A refusal is
   not a panel; targeting the panel deletes it, id and all, and no later
   out-of-band swap can restore it.
6. **`_owned_layer` checks BOTH ids.** The cross-account test was a fraud until
   both placements pointed at the same file — it 404'd for the wrong reason and
   passed with the guard deleted.
7. **Gates are `uv run --no-sync python -m pytest -q`.** A bare `uv run pytest`
   hits Anaconda's and fakes a `ModuleNotFoundError`.
8. **`TestClient` needs `base_url="http://127.0.0.1"`** or `web/origin.py`
   refuses it.

## What is left

- `renew_placement` — deliberately out. Renewal is a program-identity question
  with its own open ROADMAP entry; wiring the button would bake in an answer.
- `applies_to` chips, restack, drag-to-resize. `sync.set_applies_to` exists;
  the UI does not.
- SVG/PDF export, the Towers browser page, the Compare screen (spec D8 slice 5).
- The six dead nav items — Today, Navigator, Pipeline, Calendar, Markets,
  Towers — still have no routes. Six screens, not six links.
- Open for Grant: whether a RECEIVED RFI item keeps its answer on the client
  workbook. It currently leaves with its row, which is the sheet's existing
  outstanding-only rule.

## What a review squad found, so the next one does not have to

Five reviewers over phases 1–2 returned 14 findings; 20 more were refuted by
adversarial verify. Six were real, and three were hiding behind my own green
tests: a refusal that deleted the panel, a layer edit that left other rows
stale after `heal_follows` re-seated them, a blank money box that showed a
Python traceback, an unlinked placement that answered with an errno, a route
nothing in the UI could reach, and two tests that could not fail. All fixed;
all now covered by tests that fail under the exact mutations that exposed them.
