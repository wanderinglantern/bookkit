# Surface-review fixes — done, unmerged

**Date:** 2026-08-24 · **Branches:** bookkit `fix/surface-review-sweep` (11 commits),
towerkit `fix/surface-review-sweep` (2 commits) · **Report:**
https://claude.ai/code/artifact/2eb7a51d-3e62-4fd1-ade4-994e8771a382

## State

All twelve fixes in `20260824-Surface-Review-Fixes.md` are built, each with a
test verified by mutation, on top of a green suite. Neither branch is merged or
pushed. The three Open Questions are untouched — Grant's calls — and nothing
under "Rejected / not defects" was touched.

Gates at the end: bookkit 2244 passed, mypy clean, ruff clean; towerkit 1218
passed with the one pre-existing `test_connector` failure (confirmed on
towerkit `main` too), mypy clean, ruff clean.

## Order to merge

towerkit FIRST — bookkit's #1, #2 and #7 read from it (`edit.heal_premiums`,
the schematic border, the advisories). Then bookkit.

## The twelve, and where each landed

| # | commit | notes |
|---|--------|-------|
| 1 | tk `301885d` + bk `4d3aeaa` | `edit.heal_premiums` in the write path beside `heal_follows` |
| 2 | tk `6f631b2` | carrier blocks bordered in ink; new paper-contrast gate over every theme; `SCHEMATIC_GOLDEN_SHA` moved with its reason |
| 3 | bk `e83b35b` | `sync.qualified_layer_names` — one home; the label gate widened to the pipeline offer |
| 4 | bk `7f00e09` | `split_layer` routes the kept premium through `edit_set_field` |
| 5 | bk `32621f2` | `_index_groups` returns None only with no LINES; workbench gated on the index alone |
| 6 | bk `fbb06f7` | the rail renders `_line_chip.html`; `first`/`last` moved onto the shared chip |
| 7 | bk `e5f13e4` | advisories ride out as warnings, leading, only on a write that happened |
| 8 | bk `fedff04` | `sync.premium_clear_preview` + the clearing variant of the preview template |
| 9 | bk `40d18f0` | `renewals.renewal_on` public, over dates; towers calls it |
| 10 | bk `0b0cc30` | the window moved from the SQL to `services.exposure`; `ExposureRow.renewal_on` |
| 11 | bk `26cb863` | work.xlsx in the drawer; Exports in the top bar; route-walking gate |
| 12 | bk `a2c2e0d` | the caret band drawn from `layout.chevron_points`; `is-statutory` open-topped |

## Gotchas found while doing it

- **`git checkout <file>` on an uncommitted fix reverts the fix.** Mutation
  testing must copy the file aside (`cp x /tmp/x.bak`) and restore from that,
  never from git, until the change is committed. Cost one re-application of
  the whole sync.py edit.
- **Do not run pytest in the foreground while a background suite runs.** Two
  concurrent runs turned a 5.5-minute suite into 20+ minutes and produced
  failures that did not reproduce alone. Run the gate ALONE and wait.
- **`pytest -q` block-buffers when redirected**, so a run that looks stalled at
  8% is usually fine — check the process, not the file.
- The test book was copied to this session's scratchpad and its
  `placement.program_path` / `program_link.path` rewritten to point at the copy;
  `scratchpad/mkbook.sh <dest>` rebuilds a fresh one from the original.
- `sync.project(conn, path)` takes a `Path`, not a `str`.
- A file carrying a standing validation ERROR cannot be re-projected, so the
  "advisories collected but write refused" state is unreachable through
  validation — the `diags.ok` guard in `set_participant_premium` is ordering
  correctness, and its test says so rather than claiming a mutation check.

## Next

1. Merge towerkit, then bookkit; push both.
2. Open question 3 (the schematic export ignoring `program.render.theme`) is
   the one worth taking next: two lines, a clear right answer, and the only one
   of the three that makes two artifacts of the same program disagree.
