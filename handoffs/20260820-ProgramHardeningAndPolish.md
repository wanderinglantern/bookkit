# Program hardening and polish — 2026-08-20

Branch `feat/program-hardening-and-polish`, **uncommitted**. Full gate green.
Report artifact: https://claude.ai/code/artifact/48e2b031-9f7d-490d-9b1d-d43421af812f

## Goal

Grant reported three bugs across one afternoon and asked for hardening plus a
UI pass:

1. The Program tab said "the linked file has no layers yet" for five programs
   that render correctly in the TUI.
2. Editing a layer limit made the whole program section disappear; the change
   saved and a refresh brought it back.
3. A market bound on a program never reached the Markets tab.

Plus: apply blur-commits/Escape-discards system wide, surface the towerkit
fields the browser cannot reach, and polish the Program tab.

## What the bugs actually were

**(1) is two bugs.** The trigger: Grant moved his towerkit checkout out of
OneDrive, and `placement.program_path` held absolute paths. The *bug*:
`sync.layer_details` and four sibling readers returned `[]` from a bare
`except Exception`, so "this file will not load" and "this program is empty"
arrived at the panel as the same value and the panel printed the second. The
TUI's `tower_preview` is the one reader that prints its exception, which is
why the file looked fine there.

**(2)** Every write answered with `cell_html + panel_html`, the panel marked
`hx-swap-oob`. htmx picks its parse context from the response's FIRST tag, so
a response opening with `<td>` is parsed inside `<table><tbody><tr>` and the
`<section>` — not table content — is foster-parented out of the fragment
before htmx sees it. Reproduced in Chrome: section standing, `.table-scroll`
standing, 0 of 14 rows. Five string-matching tests asserted the broken shape
was correct.

**(3)** A carrier is a string in a towerkit file. It joins the book only when
a market org carries that name or an alias points at one. `market_add` wrote
the string and stopped; the web had no equivalent of the TUI's `y` review
queue.

## State — all shipped and tested

| Area | Files |
|---|---|
| Path storage + recovery | `src/bookkit/programpath.py` (new), `repo/placements.py:94`, `repo/links.py:27` |
| One loader seam | `sync.py:1271` `LinkedProgram` / `linked_program` / `program_load_error` / `program_file` |
| `bookctl relink` | `services/relink.py` (new), `cli.py:68` (parser), `cli.py:~370` (handler) |
| One-element responses | `web/routes/program.py` `_panel` / `_section_html` |
| Blur commits | `web/static/inline-cell.js`, `tui/widgets/inline_edit.py:269` |
| Carriers → markets | `web/routes/markets.py` `_unlinked_panel` + 2 routes, `templates/markets/_unlinked_panel.html` |
| Program tab polish | `templates/account/_layers_panel.html`, `app.css` (§ "The program section") |
| Field parity ledger | `web/parity.py` `TOWERKIT_MODEL_FIELDS` |

New tests: `tests/test_programpath.py` (18), plus additions to
`test_web_program.py`, `test_web_markets.py`, `test_web_shell.py`,
`test_conventions.py`. Each was mutation-checked — the fix reverted, the test
watched go red.

## Next step

**D6, spec'd and NOT built.** `web/parity.py:TOWERKIT_MODEL_FIELDS` names
eleven towerkit fields as `PLANNED (D6) — NOT BUILT YET`: `Layer.named_limits`,
`Layer.states`, `limits_detail`, `retention_detail`, `premium_detail`,
`Layer.notes`, `Line.abbr`, `Program.notes`, `Program.render`,
`Retention.notes`, `Sublimit.notes`.

**Build it derived, not hand-listed.** towerkit's `mcpsurface.SURFACE`
publishes every field's type, guards, bounds and clearability, and
`edit.set_field` is the single choke point for scalar writes. One generic
bookkit route over that surface beats eleven bespoke ones, and it cannot drift
when towerkit grows a twelfth field. Start at `web/parity.py:389` (the op
ledger, where `set_field` is now marked MERGED) and
`sync.py` `_mutate` / `write_through` for the write seam.

## Decisions

- **Blur commits, Escape discards** (Grant, 2026-08-20). In CLAUDE.md. Applies
  to in-place CELL editing on both surfaces, NOT to whole forms — a multi-field
  modal blurs every time you tab between its own fields.
- **One response, one top-level element.** In CLAUDE.md, asserted by
  `test_conventions.py::test_no_route_answers_with_two_concatenated_fragments`.
- **Recovery is read-only.** `programpath.resolve` never writes the repaired
  value back; `bookctl relink` is the writer. A read path that quietly migrates
  rows turns every render into a migration and makes a wrong guess permanent.
- **Ambiguity is refused, never guessed** — two roots holding the same tail, or
  two files with the same content hash. Same rule as sync's link review.
- **REJECTED: moving the program JSON into the database.** Files were not what
  broke; an absolute path per row was. Moving the authority means towerkit
  grows a DB backend or bookkit becomes a second implementation of its guarded
  write path, and you lose `towerctl edit`, git diffs and handing over a file.
  Revisit only with evidence of OneDrive *eviction* (cloud-only stubs), which
  is a different problem than paths.
- **REJECTED: adopting shadcn/ui literally.** It is a React CLI-installed
  component library; bookkit's web is Jinja + htmx with no build step. Took the
  discipline (one button component, one focus ring, component empty states)
  into the UI plan; left the dependency.

## What was tried that failed

- **Marking the details-row cells' saves with a panel refresh.** Correct for
  table columns, wrong for `policy_number` / `period_from` / `period_to` —
  none of which are columns, so the panel refresh only served to close the row
  the user was typing in. Those three answer with their own cell
  (`_DETAIL_KEYS` branch in `layer_cell_save`).
- **`visibility: hidden` for the hover-revealed chip controls.** Removes the
  element from the focus order, so `:focus-within` can never fire and every
  chip control is unreachable by keyboard. `opacity: 0` instead; pinned by
  `test_web_shell.py::test_no_hover_revealed_control_is_hidden_from_the_keyboard`.
- **Hand-writing the towerkit fixture JSON** in `test_programpath.py` — failed
  the schema on a key the model fills in for free (`retentions`). Build fixtures
  through `towerkit.model.dump_program`.

## Gotchas

- **`request.state.layer_details = {}` is gone.** Use
  `account.forget_program_reads(request)` — there are two memos now
  (`layer_details` and `linked_program`) and sixteen call sites.
- **`Path(placement.program_path)` is banned.** Use
  `sync.program_file(conn, placement)` (raises `ProgramFileMissing` with a
  usable message) or `program_file_or_none`. The stored value is relative to a
  root and may need recovering.
- **Test stubs move with the seam.** `layer_details` is now a thin wrapper;
  the I/O is `linked_program`. Patching the wrapper leaves a stub attached to a
  function nobody calls — five `test_web_account.py` tests went green against
  the real seeded tower before this was caught.
- **`programpath.roots` is the ONE definition of the roots**, env fallback
  included; `sync.configured_roots` delegates to it.
- **The server does not reload.** Two of the afternoon's confusions were a
  stale `bookctl web`. `portguard` refuses to kill a process it does not
  recognise as bookkit's — kill it by pid.

## Open questions for Grant

1. Go/no-go on D6, and on deriving it from `mcpsurface.SURFACE`.
2. Which of the twelve UI items to take (recommendation: 1–4, two days).
3. Commit and push this branch?
