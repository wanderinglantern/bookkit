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
- A MARKET CAN STATE ITS OWN PREMIUM, and STATING ONE STATES THEM ALL
  (Grant, 2026-08-24). `Participant.premium` is the figure a market charges
  where it differs from its share — a differential, tax and fees on one paper,
  a non-concurrent quote. towerkit's `edit.set_participant_premium` owns the
  rule: the seat typed, every other seat frozen at what it was already
  showing, and `Layer.premium` set to their SUM, in one act. The field is
  denied to the generic setter and `layer.premium` is guarded while any seat
  states one, because typing over a sum makes one of the two figures a lie.
  `Layer.premium_for(participant)` is the ONE definition of what a carrier is
  paid; a surface that multiplies the layer premium by the share cannot see a
  stated figure.
  THE INVARIANT IS NOT SELF-MAINTAINING. That verb holds it only while it is
  the thing writing: binding a market, unbinding one and splitting a layer all
  change the participant list and leave the sum stale — a layer claiming
  $1,960,000 whose one remaining market is paid $520,000, carried into the
  account premium (found 2026-08-24 by the surface sweep, on main). Whatever
  fixes it belongs in the WRITE PATH beside `heal_follows`, never re-summed at
  each mutation site.
- Backups before bulk writes: importers snapshot the DB (db.backup) into
  backups/ before the first row changes. Migrations are additive-only so
  far; call out anything destructive before writing it.

## A SCHEMA CHANGE IS NOT DONE UNTIL AN AGENT CAN SEE IT (Grant, 2026-08-21)

Adding a field, a table or a verb is half a change. The other half is the MCP
surface: if the assistant cannot READ the new fact or WRITE it, the feature is
"built but not accessible" for the one user who works through tools rather than
screens. Do not close a schema change without walking this chain and saying in
the commit which links you touched.

**towerkit model change** (a field on Layer/Program/Line/...):
1. `src/towerkit/model.py` — the field itself, with a comment saying WHY.
2. `uv run --group dev python tools/sync_schema.py`, then hand-write the
   `description` into BOTH `schema/program.schema.json` and
   `src/towerkit/schema/program.schema.json`; `--check` must say it is in sync.
   The schema is `additionalProperties: false` at nine sites, so a field missing
   here makes the file towerkit's OWN writer produced fail validation.
3. `mcpsurface.SURFACE` is DERIVED and picks the field up unaided — that is the
   seam working. `tests/test_mcp_surface.py`'s reviewed-count gate then goes red
   ON PURPOSE, to force a human to look at what the assistant just gained. Raise
   the count AND write the reason in the docstring.
4. If the change adds a FUNCTION to `edit.py`, place it in `mcpparity.py` —
   MUTATIONS (naming the tool that reaches it), DEFERRED_MUTATIONS (naming the
   verb that would), or NOT_A_MUTATION. `test_mcp_parity` fails until you do.

**bookkit side of a towerkit change:**
5. `web/parity.py::TOWERKIT_MODEL_FIELDS` — red until the field is named, and
   the red test IS the ticket.
6. `web/parity.py::SYNC_VERBS` — a new `sync.*` program mutator needs a row
   saying what reaches it on web / tui / mcp. `insert_layer` was written without
   one on 2026-08-21 and only the existing gate caught it.
7. `routes/program.py::_PLACED` if the field should be editable in the browser.

**bookkit's own schema change** (a migration): the tables MCP reads are
`mcpserver.py`'s business; a column no tool can read or write is a column the
assistant will confidently tell Grant does not exist.

The rule behind all of it: bookkit has three surfaces and one of them is an
agent. A change that lands on the web and not on MCP has shipped to two thirds
of its users.

AND A MISSING VERB IS NOT A REFUSAL — IT IS A WRONG WRITE (Grant, 2026-08-26).
The market half of the book had no create door on MCP at all, so an assistant
asked to add a carrier reached for the only create tool there was and the
carrier landed as a CLIENT: invisible to every market picker, unreachable by
`market_approach`, and a kind `mcpsurface.DENIED` will not let anyone correct.
A model does not stop at a wall the way a person does; it finds the nearest
door and goes through it. So every entity a tool can NAME needs a tool that
can MAKE one, and the refusal for a miss must name that tool — `no market
matching X — nearest: none close` stated the objection and stopped, which is
what sent it looking. `market_create` / `market_edit` / `markets_list` are the
market's three, and `mcpparity.py` is where the next entity's absence shows up
as a red test.

## DRY — the standing rule (Grant, 2026-08-20)

DON'T REPEAT YOURSELF, in code, in what the user has to type, and in how the
platform is used. The second copy is not the risk; the copy that quietly
DIFFERS is.

- One rule, one home. A vocabulary, a query, a parser, a piece of markup: state
  it once and read it from there. towerkit's retention types were spelled out
  as a literal in a Jinja template — a fourth copy of an enum, where no test
  and no type checker would ever see it go stale.
- A fact the user has already given is not asked for twice. The insured lives
  on the account, not a second time in the file (that decision is recorded in
  web/parity.py and is the same rule).
- The derived-field seam (towerfields / sync.set_tower_field / `_PLACED`) is
  this rule at its largest: seventeen fields, one definition.
- When a bug appears in one of N copies, fix the N, not the one. The Today
  account-name bug was ten hand-written copies of the same anchor and the tenth
  one differed; the fix was one macro over one lookup, not one corrected line.

## Data entry and input integrity

READ `.claude/skills/data-entry-integrity/SKILL.md` BEFORE adding a field, a
form, a picker or an import mapping. It carries the researched rules (with
sources) and the insurance-domain facts about limits and sublimits that decide
what a field must be able to SAY.

IT APPLIES EVERYWHERE, not to programs (Grant, 2026-08-21). The research was
done while the Program tab was the thing under the microscope, so every worked
example below is a layer or a placement — and that is an accident of when it
was done, not a statement of scope. Projects, needs, tasks, contacts, requests,
the capture flow, imports and anything built after this are held to the same
rules. A new surface does not get to be the exception because the examples do
not mention it.

The load-bearing ones:

- CONSTRAINED INPUT over an open text field wherever the valid set is knowable,
  and a picker must offer ONLY what is storable. `render.theme` shipped as free
  text and could store an absolute path, which towerkit refuses as
  non-portable — and since every later write re-validates the file, one bad
  value wedged the whole program until the JSON was hand-edited. Check the
  picker server-side too (`forms.spec.checked_option`): markup constrains a
  mouse and nothing else.
- EVERY SELECT RENDERS A BLANK OPTION, required or not. Without one the browser
  pre-selects option 1 and `required` is satisfied by a value nobody chose: a
  market response left untouched filed itself as "quoted", and a layer took
  "all lines" — the field routes/program.py says must be asked, never guessed.
  A value that should arrive set is the form's `initial`, which is a default
  the user can SEE.
- VALIDATE ON BLUR, CLEAR ON KEYSTROKE. Validating while typing measurably
  raises error rates; a message that survives the correction makes a valid
  entry read as broken. The TUI's auto-dismissing toasts are right; the web's
  persistent `.cell-error` is not, and is on the list.
- NEVER PRE-FILL A FIGURE THAT COMES OFF A DOCUMENT. People do not check
  prefills. A template fills NAMES and leaves every amount visibly empty.
- CONSISTENCY IS THE THIN CATEGORY. Conformance and timeliness are well covered
  here; cross-field rules (period_to > period_from on an unlinked placement,
  status paired with its date, response after submission) mostly are not.
  Enforce them at the service layer where both surfaces meet — a DB CHECK is a
  migration and refuses to apply against existing violating rows.
- DENSITY IS NOT THE ENEMY; undifferentiated density is. Do not thin a working
  surface an expert reads — group it. The layer details row is the worked
  example (`_layer_details.html`).

## THE WEB IS THE PRODUCT (Grant, 2026-08-21)

THE TUI IS RETIRED AND WILL BE DELETED. Grant does not use it. Until the
deletion lands it keeps working and the suite keeps it green, but it is no
longer where features land and no longer the yardstick the web is measured
against — and no web decision should be shaped by what the TUI does.

DO NOT SPEND EFFORT ON IT. Every hour keeping a retired surface green is an
hour taxed onto a surface nobody opens; if a change would require TUI work to
keep the suite passing, say so and ask whether to bring the deletion forward
rather than paying it quietly. Design decisions are made FOR THE BROWSER, and
"more modern surfaces to interact with the data layers" is the direction.

What this changes, concretely:

- A new capability is built on the web. It does not need a TUI equivalent, and
  the absence of one is not a gap.
- `web/parity.py`'s TUI half inverts in purpose. It was the guard that turned
  the suite red when the TUI grew something the web lacked — the web catching
  up. With the TUI frozen that list stops growing, so it becomes a FINITE
  checklist to finish and then delete, not a standing invariant. The towerkit
  halves (TOWERKIT_MODEL_FIELDS, TOWERKIT_EDIT_OPS, SYNC_VERBS) are unaffected:
  they measure the web against TOWERKIT, which is very much alive.
- The rules below that are about Textual — the 140-column footer, dead keys,
  the modal-forms skill, NavigatorScreen as home — are now HISTORICAL. Read
  them when touching the TUI to keep it working; do not let them shape a web
  decision.
- "Tower design stays in towerkit's editor; `o` jumps there" is DEAD as a
  reason to leave something out of the web: `o` was a bookkit TUI key, and a
  browser has no jump. The web has to be able to build a tower.

The invariants that were never about the TUI still bind everywhere: one write
per undo unit, repo/ owns the SQL, towerkit files are the authority, the
renewal date is the one you counted to, and the data-entry rules.

## UI conventions (Grant's calls, all 2026-08-12)

- A WORKING SURFACE FILLS THE WINDOW; A READING SURFACE KEEPS ITS MEASURE
  (Grant, 2026-08-24). The system pass gave every page one frame and capped it
  at 68.75rem, which is a reading measure — right for prose, wrong for the five
  screens wearing `.page-body`, all of them working tables. On a 1920px window
  that left 820px of blank paper and wrapped the columns carrying sentences onto
  two lines while the room to print them sat empty beside the table. Today shows
  20 rows where 13 fit. The measure survives ONLY where it belongs and each
  carries its own comment saying why: `.search-page` (a result list is prose),
  `.capture-page` (one form), `.page-form-host` (a form column reads as a form).
  UNCAPPING IS HALF THE CHANGE — the other half is where the slack lands. When
  only `account` was `.book-grid`'s 1fr track it took every one of the spare
  pixels and opened a canyon between the client's name and its owner: the same
  dead space, moved inside the table. Every track flexes, and a right-aligned
  column needs a wider gutter than a left-aligned one because it ends where the
  next begins (`premium` and `last touch` were touching).
- A SLAB'S ATTACHMENT COMES FROM ITS POSITION, never from a field (Grant,
  2026-08-21). The stack editor inserts above/below and recomputes the whole
  column in ONE mutation, so `write_through` never sees a half-shifted tower.
  A typed attachment is how two slabs come to share one: a quota share built as
  two layers at `$5M xs $5M` drew on top of itself with the labels
  overprinting, and towerkit had been reporting `line-overlap` the whole time
  while the web said nothing. CARRIERS ARE ADDED ON THE SLAB (`+ carrier`),
  layers on the stack — sharing and stacking must never look like the same act.
- A BUFFER IS A SLAB, NOT A GAP. A deliberate uninsured band has an attachment
  and a limit and carries no carriers and no premium. It needs NO
  gap-suppression rule and must not have one: a slab with an attachment and a
  limit fills the band, so there is no gap left to report — and a suppression
  branch also hid a real gap above an under-sized buffer, which is the one
  thing the feature exists to make honest (a surviving mutant found it,
  2026-08-21). A buffer must never render as unplaced capacity in words: "to be
  placed" says cover is coming to a band somebody chose to leave uninsured.
- A GAP IS REPORTED, NOT REFUSED. `line-gap` is a WARNING, because an error
  refuses the write and every later write re-validates the whole file — so a
  program that acquired a gap could never be edited again, including by the
  edit that would fill it. That is the `render.theme` wedge in another costume.
  Removing a mid-stack slab therefore LEAVES the gap and the confirm says so
  before it happens: closing the tower up would silently move cover the client
  bought.
- FormModal commit-in-place is THE DEFAULT: every form passes `commit=`;
  a refused/failed save keeps the form open with input intact. Dismiss
  callbacks hold success effects only.
- NavigatorScreen is home: attention-first Tree left, working DataTable
  right (a/e/d/r/l act on rows; row actions REQUIRE table.has_focus).
  Today survives on `t`. Shared flows live in widgets/entity_actions.py —
  new screens call those, never fork form wiring.
- THE TERM IS "LINE OF COVERAGE" (Grant, 2026-08-24), everywhere a person
  reads one. It used to be "line of cover" in eight places and "coverage"
  in Grant's own words, which is the drift this rule exists to stop. A LINE
  OF COVERAGE HOLDS LAYERS, and any surface that shows both says which is
  which: the structure rail groups by line of coverage, and the add-layer
  form can make one (its `applies to` picker carries a `new line of
  coverage…` sentinel). towerkit's own diagnostics still say "line" — that
  is towerkit speaking, and quoting it verbatim is right.
- Attention windows are 120 DAYS (bucket-aligned). Overdue renewals and
  unmet project needs NEVER fall off. Attention tables show lines of coverage
  (RenewalItem.lines, e.g. "GL, AL, EL") — program name alone is not
  enough context.
- A RENEWAL ROW IS ONE DATE SOMETHING RUNS OUT, not one placement (Grant,
  2026-08-21). `renewals.upcoming()` returns one `RenewalItem` per date a
  placement has cover expiring on — lines sharing a date share a row, lines
  expiring apart get a row each — so an IM layer three months early no longer
  drags its whole program's cover label under one red countdown. `item.cover`
  is what expires on THAT row's date and is EMPTY when no file is linked; a
  surface prints the house em dash there and never the program name, which is
  what made the column unreadable on a mostly-unlinked book. The program is
  its own column. `RenewalItem.key` carries the date because a placement is
  now several rows and both TUI tables keyed theirs by placement id — the
  second one raised DuplicateKey mid-build and blanked the whole screen.
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
- AN ACCOUNT IS NAMED, NOT REFERENCED. Every surface that shows an account
  shows its NAME and links on its ref, through `macros/account.html` over
  `repo/orgs.labels_for` (which returns the two together so they cannot be
  fetched separately and drift). Today had ten hand-written copies of that
  anchor and the tasks table — the one whose row carried no name — printed
  `ACC-0004` where the other nine printed the account (Grant, 2026-08-20).
- OPEN ITEMS ACROSS THE BOOK is `/items` (routes/items.py), and it OWNS NO
  WRITES: every cell posts to the account-scoped route in routes/work.py that
  already serves it, because those answer with the cell alone and are therefore
  correct on any page that renders them. One parser, one guard, one batch, one
  refusal path. Only `done` differs, and only in what it re-renders — the write
  is `work.complete_task`, shared.
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
- THE BUG IS USUALLY NOT IN THE DIFF (Grant, 2026-08-24). A review that reads
  the changed lines cannot find the surface a change broke WITHOUT TOUCHING —
  a picker that started offering two identical options because a name became
  repeatable, an affordance left behind at the old home when the place a thing
  is worked moved. Both shipped past a full review, a green suite and a
  mutation check in one afternoon. After any change that adds a data shape,
  moves where something is read or done, or lets a thing exist more than once,
  read `.claude/skills/surface-consequence-review/SKILL.md` and walk the three
  questions it asks. Its own first rule: reproduce a finding against the
  RUNNING app before reporting it — a "panel not refreshing" was diagnosed
  from the code and fixed, and the fix was wrong.
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
- DONE MEANS MERGED AND PUSHED, ACROSS INSTANCES (Grant, 2026-08-24: "ensure
  all completed work across instances is merged and pushed"). Other sessions
  leave finished commits on branches and in `.claude/worktrees/`. Before
  declaring a milestone done, sweep BOTH repos: every local branch, every
  remote branch and every worktree, with `git rev-list --count main..<ref>` —
  anything ahead of main is either finished work nobody merged or in-flight
  work to name. Two commits were found that way, one of them another
  instance's.
- CHECK THE BRANCH IMMEDIATELY BEFORE COMMITTING. `git branch --show-current`,
  in the same call as the commit. A shared checkout moves under you: twice on
  2026-08-24 a commit meant for a feature branch landed on `main`, once after
  `git checkout -b` had already succeeded. The fix when it happens is
  `git branch -f <feature> <sha>` then `git branch -f main origin/main` —
  never a force-push of a rewritten `main`.
- CANVAS WITH PARALLEL AGENTS, THEN VERIFY EVERY FINDING YOURSELF (Grant,
  2026-08-24: "dispatch multiple agents to canvas and report findings"). One
  agent per question, each told to cite file:line, give a concrete failing
  scenario and reproduce it against the running app. Then re-check each
  finding before it reaches Grant: of one sweep's eight, three were real
  defects, one was a decision he had already made, and the rest were design
  costs worth naming but not fixing. An unverified finding costs him more
  than a missing one.
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
