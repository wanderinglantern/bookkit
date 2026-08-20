# bookkit — project context

## Architecture (load-bearing)

- repo/ owns every SQL query; services/ owns business rules; tui/ and
  imports/ contain ZERO raw SQL (convention-tested).
- Money is integer CENTS; towerkit files carry whole dollars; conversion
  only in sync.py / money.py. Shares: one percent→bps rule owned by
  towerkit `money.parse_share`; bookkit delegates.
  ENTRY ACCEPTS CENTS (`1,234.56`), because bookkit stores them and
  format_cents renders them — a form that pre-fills a value its own parser
  refuses makes the whole record unsaveable until the money is manually
  rounded, destroying the cents (2026-08-15). The whole-dollar rule is
  towerkit's and stays enforced where it applies: `cents_to_dollars` still
  refuses sub-dollar amounts on write-through.
- A BARE NUMBER IS NOT A DATE. `parse_human_date` refuses 1-2 digit input:
  dateparser reads "5" as a MONTH and future-biases it, so a follow-up typed
  as "the 5th" saved as 2027-05-01 and fell off every attention window
  silently. Ambiguous entry is refused, never guessed.
- Name uniqueness for team members is enforced in `repo/team.py`, not in a
  caller. It lived only in mcpserver, so the TUI wrote straight past it and
  two colleagues sharing a name made every later MCP lookup land on the
  first match. Guards on identity belong in repo/ where every surface
  inherits them.
- `seed()` is ONE transaction. It writes hundreds of rows and can be refused
  part-way; a half-seeded book is worse than either outcome.
- towerkit JSON files are the sole authority for program structure; proj_*
  tables are a rebuildable cache. Every program write goes load → mutate →
  validate → canonical dump → re-project (sha256-guarded, sync.WriteConflict).
- event_log ordering uses SQLite rowid. base.insert/update event-log
  automatically; bulk imports add provenance notes (source + sha256).
- The connection is AUTOCOMMIT (isolation_level=None): real transactions
  need `with db.transaction(conn):` (BEGIN IMMEDIATE). conn.commit()
  outside it is a no-op — this has bitten before.
- ONE WRITER ACTION IS ONE UNDO UNIT, on both surfaces: db.transaction(batch=)
  stamps every event with a batch_id; services/batches.py owns `open_batch`
  (source='mcp' | 'tui') and reverts a batch all-or-nothing, refusing when a
  field changed since ("surface, don't guess"). Blast cap db.BLAST_CAP=250
  entities per batch (Grant 2026-08-14), enforced under log_event so no tool
  can forget it — it applies to TUI writes too now.
  TUI writes are batched BY DEFAULT: FormModal derives a BatchSpec from the
  form title unless given one, because 33 call sites build a FormModal
  directly and opt-in would leave whichever one you missed unreachable by `R`
  (Grant 2026-08-15, superseding the earlier field-granular `u`). A commit
  callback returning an error rolls the whole save back before the message is
  shown — commit-in-place still keeps the form open with input intact.
  db.transaction NESTS BY JOINING (SQLite has no nested BEGIN): an inner block
  joins the outer one and an inner batch= is ignored, so the outermost action
  owns the undo unit. imports/commit.py is unbatched on purpose (snapshot is
  its rollback), so `u` after an import says "nothing to undo".
- An event_log `field` must be a real column or declared in
  events.NON_MUTATION_FIELDS; base.log_event refuses otherwise. Undo writes
  that field back to that column, so an undeclared name is a landmine that
  only fires when a user presses `u`, days later, as IndexError on an
  unrelated record. That shipped three times ('source', 'import',
  'carrier_alias'/'merged_from') before the guard existed — declare the name,
  don't patch the symptom.
- Team writes: assignments are corrected in place via
  `edit_field(kind="team_assignment")` over role/lines/notes — NEVER
  re-scoped (unassign+assign moves someone between clients). Retiring a
  colleague is `member_deactivate`, not a field edit; it refuses while
  assignments are live and `cascade=True` removes them all in one
  revertible batch. Renames go through edit_field behind a duplicate
  guard, because two members sharing a name makes every lookup ambiguous.
- Backups before bulk writes: importers snapshot the DB (db.backup) into
  backups/ before the first row changes. Migrations are additive-only so
  far; call out anything destructive before writing it.

## UI conventions (Grant's calls, all 2026-08-12)

- FormModal commit-in-place is THE DEFAULT: every form passes `commit=`;
  a refused/failed save keeps the form open with input intact. Dismiss
  callbacks hold success effects only.
- NavigatorScreen is home: attention-first Tree left, working DataTable
  right (a/e/d/r/l act on rows; row actions REQUIRE table.has_focus).
  Today survives on `t`. Shared flows live in widgets/entity_actions.py —
  new screens call those, never fork form wiring.
- Attention windows are 120 DAYS (bucket-aligned). Overdue renewals and
  unmet project needs NEVER fall off. Attention tables show lines of cover
  (RenewalItem.lines, e.g. "GL, AL, EL") — program name alone is not
  enough context.
- THE RENEWAL DATE IS `RenewalItem.renewal_on`, never `placement.period_to`.
  days_remaining counts to the earliest LINE end, because an IM layer runs
  out months before its program period does. Print the same date you count
  to, under a `renews` header: Today, Book, the account header and the
  calendar all printed period_to beside a renewal_on countdown, so a date
  twenty days in the FUTURE rendered red as "70d over" (found independently
  by four reviewers, fixed 2026-08-15). Anything overdue is decided by
  `days_remaining < 0`, never by where a cell lands in a grid.
- Money columns say WHOSE money. The book's per-account column is bound
  premium summed over the account, matching the navigator's headline — it
  used to be whichever placement renewed next, whatever its status, which
  showed revenue that did not exist.
- Vocabulary fields complete from existing records: `Field.suggestions`
  (repo/vocab.py) → textual-autocomplete dropdown + SuggestFromList ghost
  text. Wire both on any new field whose values already exist somewhere.
- One theme: tui/theme.py (palette + status/days/money Text helpers).
  Color is signal, not decoration; every colored state carries a glyph or
  word too.
- BLUR COMMITS, ESCAPE DISCARDS — everywhere a value is edited IN PLACE, on
  both surfaces (Grant, 2026-08-20). Enter commits and closes, Tab commits and
  hops, clicking or tabbing away COMMITS, and Escape is the single discard.
  Blur used to cancel on the reasoning that a surprise write is worse than a
  surprise discard; it is not. A written value is visible, editable again and
  revertible with `u`/the undo toast, while a discarded one is gone with
  nothing left to point at — and losing typing to a stray click is the thing
  people actually hit. Two guards are load-bearing and must survive any
  rewrite: Escape's own close blurs the editor (so it must not then commit
  what Escape discarded), and an UNCHANGED value closes without writing, or
  opening a cell to read it costs a file rewrite, an event-log row and an undo
  batch per glance. Owned by tui/widgets/inline_edit.py and
  web/static/inline-cell.js; they must agree. NOT the rule for whole forms —
  a multi-field modal blurs every time you tab between its own fields, so
  FormModal and the in-row add forms keep their explicit Save.
- ONE RESPONSE, ONE TOP-LEVEL ELEMENT. htmx chooses its HTML parse context
  from the response's FIRST tag (`makeFragment`), so a response opening with
  `<td>` is parsed inside `<table><tbody><tr>`, and anything in it that is not
  table content is FOSTER-PARENTED out of the fragment before htmx ever sees
  it. `cell_html + panel_html` with the panel marked `hx-swap-oob` therefore
  did not refresh the panel — it destroyed it: a saved layer premium left the
  program section standing with its table emptied and all 14 rows gone, the
  write having succeeded, so a refresh made it look like a ghost (Grant,
  2026-08-20). Answer with ONE element and say where it goes with
  `HX-Retarget`/`HX-Reswap` (routes/program.py `_panel`). Asserted by
  tests/test_conventions.py and `_assert_panel_swap` in test_web_program.py.
  The single-element half of this rule was already written down in
  web/forms_render.py; what was missing was that it binds the whole RESPONSE.
- A REFUSAL SAYS SOMETHING. Row actions require table focus, and the gate is
  correct — but returning in SILENCE is its own bug: six of seven navigator
  row actions produced no modal, no message and no change, which reads as a
  broken app, and the hint line names those keys on the same screen. Every
  inapplicable key notifies what to do instead ("press tab or enter to work
  the rows first"), and tests/test_dead_keys.py asserts it.
- EVERY KEY A HINT LINE NAMES MUST BE BOUND, asserted per screen and per
  account tab by tests/test_dead_keys.py — hint text and bindings drifted
  apart twice. When the test fails, check which is wrong before editing:
  two reviewers reported `i paste import` as dead on three tabs and both
  were mistaken (`i` is import_here; the comment they cited is about `D`
  and `P`). The test is the arbiter, not the report.
- THE FOOTER MUST FIT AT 140 COLUMNS. It is one row with hidden overflow, so
  content past the width is cropped silently; before the overflow rule it
  went BLANK on 6 of 9 screens, home included, and the snapshots recorded
  that as correct (2026-08-15). tests/test_layout.py asserts
  `Footer.virtual_size.width <= container_size.width` per screen — when a new
  `show=True` binding fails it, demote one, don't raise the ceiling. Screen
  jumps belong in the palette and `?`; the footer names ROW ACTIONS.
- Modal chrome never scrolls away: the fields scroller is `height: 1fr`
  inside the capped `.modal-box`, so it absorbs the shortfall. A
  viewport-relative cap (`55vh`) on the inner list ADDS to the box cap and
  pushed the Save button outside its own border below 34 rows — invisible,
  while tab still reached it.
- A `Static` with `max-height` clamps its VIRTUAL size: content past the cap
  is unreachable, not scrollable, and Static cannot take focus. Long output
  goes in a `VerticalScroll`, and any go/no-go line is rendered OUTSIDE it
  (`StagedImport.verdict()`) so clipping can never hide the one line that
  decides what happens next.
- Stage/status/type vocabularies are controlled-but-extensible tuples in
  models.py (TEAM_ROLES pattern), rendered via theme.status_text.
- Textual pitfalls (ctrl+p palette, Rich markup in Static, autocomplete
  swallowing Enter, stale DataTable keys, …) are documented in the
  textual-modal-forms skill — read it before TUI work.

## Process

- Gates before every commit: `uv run pytest -q`, `uv run mypy src`,
  `uv run ruff check src tests`. When chaining in shell, never pipe test
  output before the `&&` gate — pipes eat exit codes and red suites get
  committed. Redirect to a file, gate on the command, tail the file after.
- Spec → approval → plan → build, with fresh-eyes review before declaring
  done. Grant approves fast; still present the design first.
- Grant's REAL data is on the production machine (pip access exists there
  now). This Mac's default DB is a stale experiment. Build/verify against
  seeded sample data locally; hand him `bookctl` commands for anything
  data-dependent.
- install.sh is PyPI-first with wheelhouse fallback. The new-dep drill and
  its verification step are documented on the `wheelhouse` target in the
  Makefile. Two rules learned 2026-08-14: take WHEELHOUSE_SHA256 from the
  *uploaded* asset in the same commit as the upload (a stale hash aborts
  with "altered in transit", a tamper warning about an untampered file),
  and the wheelhouse is arm64-only now — cryptography, via mcp's
  pyjwt[crypto], ships no macOS x86_64 wheels.
- Dates: numeric entry is MDY, two-digit years are 20xx (towerkit fast
  path). Never let dateparser century-bump a past date.
- CONCURRENT PHASES GET THEIR OWN WORKTREE. Two sessions sharing one
  working directory bit us on 2026-08-13: a peer session ran `git
  checkout main` mid-edit and another session's commit landed on main
  instead of its feature branch (caught, cherry-picked, reset — see the
  RFI phase-4 report). One session per worktree, `.claude/worktrees/<name>`,
  removed when the branch merges. Also: redirect gate output to the
  scratchpad, not /tmp — concurrent pytest runs interleave there.
- A FRESH WORKTREE NEEDS `uv sync --group dev`, and gates must run as
  `uv run --no-sync python -m pytest`. A bare `uv sync` installs runtime
  deps only, and `uv run pytest` then silently falls through to Anaconda's
  pytest — which cannot import bookkit and reports `ModuleNotFoundError:
  No module named 'bookkit'` from conftest, looking exactly like a broken
  editable install (2026-08-15). Related: `.claude/worktrees/towerkit` is a
  symlink to ../../towerkit and is what makes the `path = "../towerkit"`
  dependency resolve from inside a worktree — don't delete it.
- A GREEN SUITE PROVES NOTHING BROKE, NOT THAT THE NEW PATH IS TAKEN. When
  a change is meant to route behaviour through a new seam, assert the seam
  is actually used, end to end, before believing it. Batching the shared
  `entity_actions.push_form` looked right and went green — while 33 call
  sites built `FormModal` directly and bypassed it entirely (2026-08-15).
- Handoffs go in `./handoffs/YYYYMMDD-Feature.md`, written so a fresh
  Claude can resume cold: goal, state, next step with file:line, decisions
  and what was rejected, what was tried that failed, gotchas, open
  questions. `changelog.md` is maintained per the prompt at its own bottom;
  commit and push it when asked.
- Grant reviews long-form work as published artifacts, not terminal
  scrollback — an audit or a multi-phase build gets a report artifact and,
  for ongoing work, a build log kept updated at the same URL.
- THE WEB IS THE DAILY DRIVER, so its parity universe is EVERYTHING THE
  TERMINAL WORKFLOW REACHES — bookkit TUI keys AND towerkit's editor behind
  `o`, which a browser does not have. "Built but not accessible" is a bug
  class (statutory, 2026-08-19): web/parity.TOWERKIT_EDIT_OPS introspects
  towerkit.edit at runtime and the suite goes red until every op is covered
  or deferred by name with a reason. When towerkit grows a capability, the
  red test IS the ticket.
- UI WORK SHOWS ITSELF (Grant, 2026-08-19): when a phase changes what a
  screen looks like, update the artifact with SCREENSHOTS of the running UI
  (seeded demo data, embedded as data: URIs) and numbered feedback areas
  (S1-1, S1-2, …) beside each shot, so Grant can paste the labels back with
  comments. Screenshots land at each phase's end at minimum, mid-phase when
  a call is being made that a picture would change.
