---
name: surface-consequence-review
description: Use after a change to bookkit or towerkit that adds a data shape, moves where something is read or done, or lets a thing exist more than once — for the bookkit-specific surfaces to walk and the defects that have actually shipped past a full review here.
---

# Surface consequence review — bookkit's evidence

**The METHOD lives in the user-level `grant-coding` skill** — the three
questions, the fix/report split, and the verification discipline. Read that
first; this file is the part that is only true of these two repos, and it is
kept separate so the method has one home rather than two copies that drift.

## Why this exists here

Two defects shipped past a full review, a green suite and a mutation check on
2026-08-24, in one afternoon:

- Lines of coverage each arrive with a layer named "To be placed". A program
  with two of them gave the "same policy as" picker **two identical options**.
  The picker's code was not in the diff — correct before, correct after. What
  changed was the DATA it renders.
- The structure rail moved from a flat pile to groups per line of coverage.
  Reordering a line stayed on the chips in the band above, so the place the
  structure is now read could not reorder it. Nothing was deleted, so nothing
  looked wrong.

Both are the same shape: **the broken code was not in the diff.**

## The surfaces to walk, in these repos

bookkit has THREE surfaces and one of them is an agent. A change that lands on
the web and not on MCP has shipped to two thirds of its users.

| Ask | Where to look |
|---|---|
| Who renders this shape? | `web/templates/`, `web/routes/`, `mcpsurface.SURFACE` |
| Who chooses between instances of it? | every `<select>`, every picker's option list |
| Who totals it? | `sync.py` projections, `placement.total_premium`, the Book headline |
| Who keys or sorts on it? | `RenewalItem.key`, DataTable row keys, queue sort keys |
| Does towerkit say it too? | `web/parity.py` (TOWERKIT_MODEL_FIELDS, TOWERKIT_EDIT_OPS, SYNC_VERBS), `mcpparity.py` |
| Does the export agree with the panel? | `render/mpl_program.py` vs `web/tower.py` + `_tower_panel.html` |

## What the sweeps have found here

Each of these is a check that caught a CONFIRMED defect in this codebase.

- **An invariant maintained by ONE verb is broken by every other writer.**
  `set_participant_premium` established "the layer's premium IS the sum of its
  markets" and held it only while it was writing; bind, unbind and split each
  left the sum stale. Fixed by `edit.heal_premiums` in the write path beside
  `heal_follows` — never a re-sum at each mutation site.
- **A fill must be distinguishable from the PAPER**, not only from its label.
  Two harbor palette entries sit at ~1.2:1 against white and were drawn with a
  white border in the client-facing workbook.
- **Two identical options can cause a wrong WRITE.** The pipeline bind picker's
  collision put a real participation on the wrong line of coverage;
  `sync.qualified_layer_names` is the one home for the rule now.
- **A gate is only as good as where it looks.**
  `test_no_select_offers_the_same_label_twice` scanned `/program` pages and was
  structurally blind to the pipeline's offer.
- **Carried is not drawn.** `web.chevrons` crossed the tower seam correctly and
  nothing inked it, so a statutory layer was closed-topped in the browser and
  open-topped in every export. `test_web_tower.py` now holds every carried key
  to being drawn or named with a reason.
- **The renewal date is the earliest LINE end, never `placement.period_to`** —
  and it has been derived twice, uncapped, on two different pages since.

## Evidence, not suspicion

Reproduce every finding against the RUNNING app before reporting it. On
2026-08-24 a "panel not refreshing" report was diagnosed from the code, fixed,
and the fix was wrong: the panel refreshes correctly, and driving the real
route proved it in one call.

A test book with the relevant shapes is rebuilt by the helper recorded in
`handoffs/20260824-Surface-Review-Fixes-Done.md`; serve it on its own port and
drive the real routes.
