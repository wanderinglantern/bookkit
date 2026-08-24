# Changelog

All notable changes to bookkit, newest date first.

## 2026-08-24

### Added
- **Every screen opens the same way now.** The system pass (design turn 4):
  an ink band carrying the screen's name in serif and its one-line standing,
  section headers whose counts are serif figures sized to matter, the stat
  strip on Book and Today, filter chips with real counts everywhere a list
  can narrow, five state badges that always carry a word, four empty-state
  voices, hover-held row actions, and one new structural accent
  (`WEB_SLATE`) spent on group headers — never on a state, never clickable.
- **Today is one morning, not ten lists.** *Needs you today* merges overdue
  renewals, overdue items and past-SLA submissions into one worst-first
  list — each row printing the date its countdown counts to — then
  *Renewals coming*; the five context lists demote to counts in the rail
  with their tables one disclosure away. done/drop live on the merged list;
  the change list's Revert is untouched.
- **Open items unifies tasks and requests under filter chips**
  (Everything · Overdue · This week · No date · Requests), each with its
  count; done and drop swap the section instead of reloading the page, and
  the view survives every write.
- **The book gained its figures and its filters**: bound premium, clients,
  renewing-90d and overdue as headline stats, chips to narrow by them, and
  the overdue edge on the rows that need work.
- **The exports drawer** (`/exports`): the scattered download links in one
  place — every artifact per account, from the same routes their own tabs
  serve.
- **The Program tab is one worksheet now.** The stack editor, the layers table
  and the details row became one grammar: a grouped, collapsible **structure
  index** on the left, one layer's **worksheet** on the right, and the rail
  (towerkit's diagnostics, the drawing with the selected layer outlined)
  beside it. Selection and collapse state live in the URL, so a refresh — and
  every save — lands back on the same layer. Clicking a drawn block selects
  its layer.
- **Attachment is never typed.** The worksheet states it as a sentence ("Sits
  on 2nd Excess → attaches at $102,000,000, tops out at $202,000,000") and
  changes it with *move up / move down* (a whole-column reseat in one
  mutation), *insert above / below*, and **split by line** — two slabs in the
  same band, the premium division typed and totalled, the new slab unplaced
  until something is bound on it.
- **Participation is a table with the money derived.** Carrier and share per
  seat, the carrier's slice of the limit and premium computed once in sync,
  and **open capacity as a first-class row** — the shortfall in percent and
  dollars, with *place it* focusing the always-present bind row.
- **A share edit previews before it saves.** The one deliberate exception to
  blur-commits: typing a share shows the change, the resulting signed figure,
  the dollars still open and where it writes — Save commits, Discard leaves
  the file untouched. A preview towerkit would refuse shows the refusal and
  offers no Save.
- **Dropping a line off a spanning slab states the consequence first.** What
  that line would be left with, in dollars, whether anything remains above
  it, and that premium is *not* re-rated — Drop / Keep it / Split instead. A
  dry run of the same call the commit makes (`sync.preview`), so the two can
  never disagree.
- **Towers is a queue.** The validator decides the order — errors, then
  unplaced dollars, then the renewal date (counted to the earliest line end,
  never `period_to`) — each card states the one fact that would make you open
  it, in towerkit's own words, and opening it lands on the layer that fact is
  about. Filters with counts: needs work, open capacity, renewing, all.
- **Compare reads like a renewal report.** Four delta stats, a plain-English
  paragraph derived from the same delta the table shows, and a layer-level
  table — new rows tinted, lapsed struck, carrier moves in-line.
- **Starting a program is one worksheet, then the file.** Copy last year
  (structure, lines and terms come across; premiums and bound shares do not)
  or start empty (each layer seats on the last; the attachment is the running
  total). The rail shows the file that will be written and towerkit's checks,
  live, before anything exists — a refusal creates nothing and keeps the
  typing.

### Changed
- Lines of cover moved to the program band; retentions and sublimits to the
  index's foot, where the tower keeps them. The band gained premium and
  open-capacity stats summed from the same per-layer derivations the
  worksheet reads.
- Inserted layers take their id from their name, the same rule `add_layer`
  follows — a slab named "1st Excess" no longer ships as id `layer`.
- The empty and broken program states keep all four facts distinct and now
  say what to do in each; the band withholds its line controls while the
  file will not open.

### Fixed
- Past-SLA rows on Today count to the deadline they print (sent + the SLA,
  now one named constant), wear the overdue tone, and name the underwriter
  to chase; the requests-to-chase list returned as a disclosure the rail's
  count actually opens. Row actions stay visible on touch screens, where
  hover cannot reveal them.

## 2026-08-21

### Added
- **The launcher checks before it serves.** `bookctl web` refuses to start when
  bookkit needs towerkit features the installed towerkit does not have, and
  prints the command that fixes it. That skew spent an afternoon presenting as
  "the toggle arrow does not work"; it now says so at the door. It repairs
  nothing on its own — the fixes are a `git pull` and, only when dependencies
  moved, `./install.sh`, and neither should happen unasked under a launching
  app.
- **The Program tab prints what towerkit says about the file.** Every error and
  warning, in towerkit's own words, above the tower they describe. Until now a
  diagnostic reached the browser only when a *write* was refused, so a file that
  already held a problem — written in towerkit's editor, by the assistant, or by
  an import — drew a wrong picture and the page said nothing. Two layers at the
  same attachment now say `OVERLAP` instead of quietly drawing on top of each
  other.
- **The Projects tab**, the last of the terminal's account tabs the browser did
  not have: a client's jobs and the cover each one still needs, editable where
  it is read, with `need → opportunity` as a row action.
- **A state list can be pasted the way a policy prints it.** Commas were the
  whole entry syntax, so a workers-compensation schedule copied off a policy
  — bare codes, no punctuation — became one long "state". Commas,
  semicolons, slashes, pipes, newlines, tabs and plain spaces all split now,
  and what is recognised is stored as its USPS code: `il`, `Illinois` and
  `IL` are one value, and "New York NJ Rhode Island CT" is four states.
  Nothing is guessed — an unrecognised piece is stored as typed so the
  validator can say it is not a US code, and a near-miss is never quietly
  corrected.
- **Say that two layers are one policy.** Workers' compensation Part A
  (statutory benefits, no dollar limit) and Part B (employers liability, a
  real limit) come on one policy from one carrier, and a layer cannot be
  both — so the schematic draws them apart and nothing said they belonged
  together. The layer details row gains a **same policy as** picker of the
  program's other layers; blank unlinks. It carries a rule rather than being
  a note: two parts stating different policy numbers is refused, and parts
  running on different periods warn.
- **Renewals list the policy, not the program.** A row was one placement,
  carrying the program's whole cover label beside one countdown — so an
  Inland Marine layer expiring three months early made "GL, AL, IM · 70d
  over" claim all three were late. A row is now one date something runs out:
  lines sharing a date share a row, lines expiring apart get one each, and
  each says what expires on it. Cover and Program are separate columns, and
  a placement with no program file linked prints an em dash under Cover
  instead of its own name — the book cannot know its lines, and says so.
- **Remove a program *and* the work filed against it.** The removal refused
  while any submission, task, request, document, team assignment or project
  need pointed at the program, which is right for one created by mistake and
  wrong for one that picked up a stray task on its way. The confirm now offers
  a second button naming exactly how much goes with it, and the whole thing is
  one batch, so one Undo puts every record back. Each row goes through its own
  kind's verb — an information request takes its items with it — and one
  refusal survives the cascade: a request somebody has **answered**, because
  deleting the question deletes the client's answer with it.
- **Remove a program that should not exist.** Merge folds two records of the
  same program together and refuses two file-backed placements on purpose;
  there was no way to say "this one was a mistake". Remove sits beside Merge
  on the program header, confirm-first, and the confirm shows exactly where
  the file goes before anything happens: the towerkit file is **moved** to a
  `.removed/` directory beside it, never deleted. It refuses while any
  submission, task, request, document, team assignment or project need still
  points at the program, naming each. Undo brings the record back; the file
  stays where it was put, and the confirm says so.
- **Auditable, on a policy.** A yes/no fact recorded per layer: does the
  carrier true the premium up at expiry? Workers' compensation and general
  liability normally do; property does not, and two layers of one program
  legitimately differ. Editable in towerkit's own editor and in the layer
  details row in the browser, beside the policy number and the policy dates.
- **Drop a task, as against completing one**, on the web — on the account's
  Work tab, on Open items and on Today. `Done` stamps a completion date;
  `Drop` does not, because a task filed in error or overtaken by events was
  never work that got finished and must not be counted as any. It is the
  TUI's `D` key, one field write, revertible with Undo, and it asks which
  task before it writes. This closes the last of the four deletable row
  kinds the parity ledger was holding `delete_row` open for.
- **Build a tower in the browser.** Layers are inserted above or below what is
  already there and the attachment is worked out from the position — there is
  no attachment to type, which is what made two carriers sharing one slab turn
  into two layers drawn on top of each other. Carriers are added on the slab
  they share. A deliberate uninsured band is a **buffer**: a real slab that
  carries nobody and draws hatched, so the band is stated rather than left as a
  hole nobody explained. Removing a layer from the middle of a tower leaves the
  gap open and says so, rather than sliding everything above it down.
- **Export the Work tab.** `Export .xlsx` above the open-tasks and
  information-requests panels writes just those two tables, through the same
  sheet builders as the client deliverable — the two files cannot disagree
  about what an open item is, and a test compares them cell by cell to keep it
  that way. Two sheets rather than one tab: the two tables have different
  columns, and stacking them would leave half of one blank by construction.

### Fixed
- **Completing or dropping a task on a filtered Open items view no longer
  throws the filter away.** The filters live in the query string so a view is
  a link you can keep; the write answered with the unfiltered page, so a
  broker looking at one client's open items was handed the whole book back.
  The task was recorded correctly — what was lost was where you were standing.
- **Blur saves again, every time.** Saving a cell on the Program tab wedged a
  guard flag on, and from then on clicking away from ANY cell silently kept it
  open with unsaved text — while Enter kept working, which is why it read as
  "sometimes I need to hit enter, other times not". A committed cell also
  flashes briefly now, so a save that changes nothing visible still says it
  happened; the flash knows a write from a walk-away, so Escape is never
  congratulated.
- **Escape no longer commits the value it discards.** The discard guard reset
  on a zero-timer while its revert made a network round trip, so pressing
  Escape after typing could write the very text being thrown away — it reached
  the database with an event-log row. Live since blur-commit shipped; found by
  watching the database after a discard, which nothing had done.
- A service that opens an undo batch and is called from *inside* another one
  no longer leaves an empty batch behind — a line in the changes list
  describing an action, offering a Revert, and reverting nothing. The events
  always landed on the outer batch; only the phantom row was new.

### Changed
- Today completes a task through the same shared write the other two
  surfaces use, instead of its own hand-rolled copy of the batch and its
  sentence.
- **The client's workbook stops explaining itself and starts naming people.**
  The standing scope sentence, the Information Requests banner, the
  "asked [date]" labels and the Type column are gone — the withholding rules
  they narrated are unchanged. The Owner column names the individual on each
  item instead of answering "You" or "Us"; unassigned work still reads "Us",
  and a contact at another client is never named on this one's copy.
- **The Schedule of Insurance is bound cover.** Each programme lists what is
  actually bound under its own name, and anything not bound — quoted,
  submitted, or run off — sits in its own "not bound" block below with its own
  subtotal. Bound and unbound premium were never added together; now they are
  not interleaved either.
- The assignee cell on open items shows the person's name alone; the
  "— our team" qualifier survives only inside the editor, where it is what
  keeps a saved assignee resolving to the right person when two people share
  a name.

## 2026-08-20

### Added
- **Open items across the whole book** (`/items`): every open task, overdue
  first, editable where you read it, with filters that are URLs and capture
  that asks which account. It owns no writes — each cell posts to the
  account's own edit route, so editing from the book-wide list and from the
  account's Work tab are the same write, the same guard and the same undo
  unit.
- Cross-field consistency rules, in one module both surfaces and the assistant
  pass through: a placement can no longer end before it starts, a subjectivity
  can no longer be "met" with no date or "outstanding" while carrying one, a
  quote can no longer respond before it was sent, and a request can no longer
  fall due before it was asked. Every refusal names both remedies, because
  which of two dates is the typo is your knowledge and not the software's.
- The researched rules for data entry, with their sources, as a skill the next
  change reads before adding a field — plus DRY as a standing rule.

### Changed
- **The layer details row is grouped, not thinned.** The complaint was density;
  the cause was proximity — administrative facts, coverage prose, named limits,
  scope and structure were one undifferentiated run. Same eight values, nothing
  hidden, now behind a label rail with exactly one column allowed to wrap, so a
  long note stops shoving its neighbours sideways.
- The chart theme is a picker offering only themes a program file may legally
  name, and the export refuses a theme that has gone missing rather than
  quietly rendering a client's chart in the wrong brand.
- Contact roles and RFI categories complete from your own book on the inline
  cell, which is the path you actually use — the modal always had them.

### Fixed
- **A market response left untouched filed itself as "quoted."** A required
  dropdown with no blank option lets the browser pick its first entry and call
  the field answered; five more did the same, including which colleague an
  assignment went to and which line a new layer covered.
- **Negative amounts are refused.** `-1,000.00` was storable while `-1000` was
  not, so which spelling you typed decided whether a negative premium got in.
- **The import template shipped a fake account as a real data row** — fill your
  rows underneath it, re-import, and you created Atomic Industries carrying a
  bound $250k placement.
- **A percent-formatted commission column imported at one hundredth of its
  value** (15% became 0.15%). It refuses now and names both readings rather
  than guessing.
- A re-import that corrected a renewal date reported "updated" and changed
  nothing. An empty spreadsheet reported "OK to commit", took a backup and did
  nothing. A failed re-stage in the paste window left the previous parse live
  under a green light.
- A refusal no longer survives the correction that fixes it.
- Today printed an account's reference where every other section printed its
  name.
- Out-of-range figures say what range is accepted instead of surfacing a raw
  database constraint error.

- **Seventeen towerkit fields the browser could not reach.** A layer's states
  and its four prose fields (limits, retention and premium detail, notes) and
  its named limits, in the details row; a line's column label on the lines
  strip; a note on the programme itself; a note on the retention and sublimit
  forms, saved in the same write as the figure it qualifies; and the six saved
  chart options beside export. They were writable only from towerkit's own
  editor, behind the TUI's `o`, which a browser does not have.
  None of it is hand-listed. towerkit publishes every writable field as data —
  type, bounds, guards, whether it clears — and funnels every scalar write
  through one choke point; bookkit reads that surface, so three routes serve
  the lot and a field towerkit grows arrives already parsed and already
  refusing correctly. What cannot be derived is where a field goes on the
  page, and that is stated once, in one table, checked against the rendered
  page by the suite.
- A field ledger with nothing left in it: 0 of 55 towerkit model fields are
  marked planned. The ledger was the ticket queue for this work.

### Fixed
- **Every tower download ignored the program's own saved chart settings** and
  rendered with the library defaults, so premiums turned off in towerkit's
  editor came back on in every SVG and PDF bookkit produced. towerkit's own
  `towerctl render` has always read them.
- A cell's address could not survive a line whose id starts with `i` — inland
  marine, which every real book has. It parsed as a position rather than a
  name, lost the row it pointed at, and took the whole Program tab down with
  it. Caught before release by the seeded fixture.
- **The Program tab claimed five real programs were empty.** Moving a towerkit
  checkout out of OneDrive made every `program_path` a dead absolute path, and
  five readers returned `[]` from a bare `except Exception` — so "this file
  will not load" and "this program has nothing in it" reached the panel as the
  same value and it printed the second. The panel now names the file, the path
  it tried and the command that fixes it, and falls back to what the last sync
  recorded (greyed, dated, read-only). The layers were in `proj_layer` the
  whole time.
- **A saved layer edit deleted the layer table.** Every write answered with a
  `<td>` and an out-of-band `<section>` glued together; htmx picks its parse
  context from the response's first tag, so the section was foster-parented out
  of the fragment before htmx saw it — section standing, table emptied, write
  succeeded. One element per response now, with `HX-Retarget`. Five
  string-matching tests had asserted the broken shape was correct.
- **Every write silently erased the tower drawing**, which was rendered only by
  the full-page builder. Found while fixing the above; the section now has one
  renderer, and a convention test fails if a second appears.
- **The layer details row closed itself** on the statutory, follows-underlying
  and applies-to writes made from inside it.
- **A market bound on a program never reached the Markets tab.** A carrier is a
  string in a towerkit file until a market org carries that name or an alias
  points at one, and the web could only do the first half.
- The Program tab opened and re-parsed each program file five to ten times per
  render; one read apiece now.

### Added
- `bookctl relink` — repairs placement↔file links after a towerkit tree moves.
  Reports by default, snapshots the database before writing, and sorts every
  broken link into moved / renamed (byte-identical content) / lost / ambiguous.
  Only the first two are repaired; ambiguity is named and refused.
- Program paths are stored relative to a program root, so moving the tree is
  one `bookctl roots` call rather than a per-row repair. Reads resolve and,
  when the stored location is empty, recover by matching the longest tail that
  exists under exactly one root.
- Carriers on your towers that the book does not know are listed on the Markets
  page, with the same two answers the terminal's `y` queue offers — link to an
  existing market, or add it — and fuzzy candidates shown with their scores.
  An unresolved seat carries a `NEW` badge on its layer row.
- A parity ledger over towerkit's model FIELDS, introspected at runtime. The op
  ledger covered towerkit's verbs and said nothing about its nouns, so five
  Layer fields grew with every parity test green.

### Changed
- **Blur commits, Escape discards**, everywhere a value is edited in place, on
  both surfaces. Blur used to cancel; losing typing to a stray click is the
  failure people actually hit, and a written value is visible, editable again
  and revertible. An unchanged cell still closes without writing.
- The Program tab: a two-tier section header (identity, then labelled facts,
  then right-aligned actions), lines/retentions/sublimits as separate labelled
  rows of bounded chips, per-chip controls revealed on hover **and focus**, and
  export moved to the end of the section as the output it is.

### Added
- Today is the front door: `/` redirects to `/today`, the TUI brief's
  sections in severity order — overdue renewals (never fall off), renewals
  within 120 days, tasks due (tick done in place, one revertible batch),
  project needs, past-SLA requests, quotes expiring, onboarding, requests
  to chase, going stale — plus cross-account recent changes with Revert.
- The renewal Calendar on the web: bucket-aligned months, lines of cover on
  every entry, counting to the earliest line end, overdue pinned on top.
- Quick capture on the web (`/capture`): log an interaction from any page,
  attendees resolved through the same shared rule as the TUI, a follow-up
  phrase offering a task on its own page; + Log on the topbar and every
  account header.
- Global search (`/search`): the TUI's `/` as a page — accounts, contacts
  and interactions grouped, every hit landing on the owning account; the
  topbar carries a live search form everywhere.
- Pipeline outcomes are recordable from the browser: the market response
  form (quote/decline with the bind offer), opportunity create/edit and
  stage moves, close won/lost, and subjectivities.
- Account create and edit on the web, both behind the one duplicate guard
  the TUI uses.
- The markets surface (`/markets`): family outline, hit rates, appetite,
  underwriters, aliases; create, merge-with-confirm (the duplicate's name
  becomes an alias so towers keep resolving), nest and unnest.
- Team management (`/team`): the roster with the specialist filter, member
  create/edit behind the duplicate-name guard, retire behind a confirm
  naming every live assignment (cascade as one revertible batch),
  reactivate; the account rail's Team section went live with assign,
  edit-in-place and remove.

### Fixed
- Merging a master market no longer orphans its nested children: they fold
  into the survivor (merging a master into its own child unnests the
  child), and a market whose parent died before this fix renders instead
  of crashing its own detail page.
- The dead-control sweep verifies POST form actions again — a loop-variable
  rebind had been silently checking them as GET, so a deleted write route
  could not turn the suite red; a sentinel now proves the sweep sees POSTs.
- The parity ledger's pipeline entry caught up with the code it sat beside:
  the tab's writes shipped; the one honest absence left is the global
  kanban.
- Retiring a member whose cascade would exceed the blast cap refuses in the
  page instead of answering a 500.
- The search page stops highlighting Book as the current nav section.
- The TUI's quick capture refuses an unparseable date with the shared
  refusal sentence instead of silently stamping today, and the accepted
  follow-up task is created inside a batch so undo can reach it — its two
  last divergences from the web capture form.

## 2026-08-19

### Fixed
- The open-items workbook link moved to the account-level programs head —
  it was gated inside a linked placement's export strip, unreachable for
  accounts without one (parity audit).
- The account rail's Documents section renders the account's real documents
  instead of a hard-coded "No documents yet"; the unhandled drop-target
  promise is gone, and the team rail's empty state stops implying an add
  the page does not offer.
- Three parity-ledger entries corrected (new_submission and renew_placement
  were built but still marked pending; the delete_row entry denied the
  web's request-item removal).

### Added
- A screen-level parity ledger (web/parity.SCREENS), discovered from the
  TUI screens package, so a whole screen with no web answer finally turns
  the suite red — the audit's structural finding.
- Web downloads: tower SVG and PDF, the schematic workbook, and the client
  open-items workbook — plain links, rendered through towerkit's own
  renderers, closing the file-download gap the web spec deferred.
- The Towers page: every drawn tower across the book with validation
  badges, linking into each account's Program tab; the nav item goes live.
- Compare: the renewal delta table (NEW/RENEWED/LAPSED per seat, share,
  line and premium old→new), pair auto-detected by renewal adjacency with
  a picker fallback.
- The terms strip: retentions and sublimits editable where they are read
  (in-row forms, applies-to as checkboxes, confirm-first removes), and
  line reorder arrows on the lines strip.
- restack is deliberately NOT surfaced: through bookkit's guarded write
  seam it is a provable no-op (valid towers have nothing to heal), and a
  control that provably does nothing would be dead chrome.
- Lines of cover are editable from the browser (D1): rename in place (ids
  cascade), remove behind a confirm naming what dies vs what narrows, add
  in row (arriving with a pending layer — towerkit refuses an empty line).
  A scaffolded program's "Coverage TBD" is one rename away from real.
- The layer details row grew structure: applies-to toggle chips (the first
  caller for sync.set_applies_to), statutory on/off (confirm-first; leaving
  asks for the replacing limit), follows-underlying one-click toggle.
- The tower drawing is clickable: a block scrolls to and flashes the table
  row that edits it.
- Merge a duplicate program from the web — same service and undo unit as
  the TUI's x.
- web/parity.TOWERKIT_EDIT_OPS: every towerkit editor op, introspected at
  runtime, must be covered or deferred by name — closing the "fully built
  but not accessible" class Grant caught on statutory.
- Web placement editing: the program section header's name, period, status
  and commission are inline cells; the file-vs-row ownership rule lives in
  one service both surfaces call.
- Renew on the web, placement-scoped and confirm-first; the confirm states
  exactly what renewing does before anything is written.
- Layer delete on both surfaces (D2): the web details row and the TUI's D
  on a placeholder carriers row, confirm naming the seats that go with it.
- TUI market-seat corrections: e on a carriers row edits the seat, D takes
  it off (confirm first) — previously a wrong share typed in the terminal
  could only be corrected in the browser.
- Web layer add asks which lines the layer covers (previously it silently
  landed on the first line); the scaffold destination is editable; and
  submissions send from the program section, landing on the Pipeline tab.
- A program-verb ledger (web/parity.SYNC_VERBS) discovered from sync.py's
  own source: a new program mutator turns the suite red until every surface
  covers or consciously defers it.

### Changed
- Rework the web Program tab's editing grammar: a market's carrier and share
  are inline cells on the chip (same contract as layer cells, with tab-hop
  and blur-cancel), + market is an in-row form with carrier completion, and
  removing a market asks first, in place.
- Money cells on the web display compact ("$25M") while their editors keep
  pre-filling the exact figure (D5).
- One user-facing word: "program" — the TUI tab, form titles and messages
  stop alternating with "placement" (D3).
- Unbuilt web controls are no longer drawn as disabled placeholders: the
  account header's + Task is a real link (Work tab, form open), and Renew,
  Log interaction, Assign, the six unrouted nav items, the Search pill and
  the book-page filter/New account/Export pills unrender until their routes
  land (D4).

### Added
- A details row per layer on the web Program tab making policy number and
  policy dates reachable and editable.
- tests/test_web_dead_controls.py: no admitted-dead chrome, every rendered
  action resolves to a real route, every editable layer field is reachable.
- Opening a web form now closes other open forms that hold no typed input.

### Fixed
- The web market share editor pre-filled percent through a bps formatter, so
  a 40% seat pre-filled "0.4" and an unedited save would have written 0.4%.

## 2026-08-14

### Added
- Add `bookctl mcp --connector-info` and `--check`, so the Cowork connector
  panel can be filled in without hand-typing paths.
- Add `bookctl roots --json` — the configured program roots, machine-readable
  for towerctl.
- Add `bookctl sync --path` to project a single file, for towerkit's
  post-write hook.
- Add MCP team edits: correct an assignment in place (role, lines, notes),
  rename a member behind a duplicate guard, and `member_deactivate` /
  `member_reactivate` for retiring a colleague. Deactivation refuses while
  assignments are live; `cascade=True` unassigns everywhere as one undo unit.
- Add MCP policy records — the guarded towerkit program cycle exposed, with an
  honest file-revert story rather than a pretend one.
- Add market appetite and underwriter editing, closing the last CRUD gap.
- Add command palette entries, clipboard copy, deep-linking, and a persisted
  filter to the TUI.

### Changed
- Raise the blast cap from 50 to 250 entities per batch.
- Make the TUI act only on what the user can see, and work at 80 columns
  rather than only at 140.
- Point `edit_field`'s `active` handling at the transition tools instead of
  allowing a direct edit.

### Fixed
- Create the database in `build_server` before opening it read-only, so a
  first MCP connection no longer fails on a missing file.
- Stamp the owning org on a deal-level `team_unassign`.
- Tag provenance on each `team_assignment` row touched by a cascade unassign.

### Infrastructure
- Read the snapshot baselines instead of only writing them, so layout
  regressions fail the suite (F14).
- Pin the snapshot plugin the baselines were rendered with.
- Rebuild the wheelhouse so it can actually satisfy the MCP dependency, and
  correct the published SHA256.

## 2026-08-13

### Added
- Add RFI tracking end to end: two-level requests and items, a 120-day chase
  queue, the account requests tab, paste-to-create, and `bookctl today`
  listing the requests to chase.
- Add the MCP server (`bookctl mcp`): read tools for the daily brief,
  renewals, search, programs, staleness, open items and pipeline status; write
  tools for activity logging, tasks, client creation with a rapidfuzz
  duplicate guard, and fill-blanks-only enrichment.
- Add MCP batch undo — one call is one undoable unit, revertible out of order,
  with `list_batches` and `revert_batch`, plus the MCP CHANGES section in the
  navigator and `R` to revert.
- Add MCP write expansion: compare-and-set `edit_field` across nine entity
  kinds, book-side creates, team management, and staged transitions.
- Add the onboarding wizard — a completeness service driven by the data
  itself, an attention feed, and `o` to start or resume.
- Add the client open-items tab, and grow the export to a four-tab workbook:
  Open Items, Projects, Schedule of Insurance, and Information Requests.
- Add optional form drafts (`draft_key`) so a half-typed modal survives `esc`.
- Add inline creation of a team member from the assignment form — create and
  assign in one transaction.

### Changed
- Split export columns into Description and Detail, and remove days-open.
- Order RFI items by rowid rather than a random-tailed ULID.
- Apply one effective-due rule across all three RFI surfaces.

### Fixed
- Stop renamed towerkit lines from duplicating their opportunities.
- Stop a merged-away market from killing the TUI.
- Stop dead placements leaking into the RFI surfaces.
- Make `u` work after any MCP write, and allow deleting a wrongly logged
  activity.
- Make request scope reachable, enforced, visible, and clearable.
- Derive the compose reference date from today — a fixed date broke at
  midnight.
- Keep your place in the navigator across a refresh, and let an inline edit
  survive one.

## 2026-08-12

### Added
- Add commit-in-place as the default for every form: a form never closes on a
  failed save, so a refusal is corrected in place instead of retyped.
- Add the navigator — an attention-first tree home with working data tables —
  as the screen the app opens on.
- Add client projects with insurance needs, including needed-by dates that
  never fall off the radar and a need-to-opportunity link.
- Add the open-items export: pure composition into an SOI-formatted workbook,
  `bookctl export open-items`, and `x` in the navigator.
- Add market families, nesting issuing companies under masters.
- Add task description and category, with category grouping across every task
  table and SOV-style sectioning in the export.
- Add `ctrl+t` to create a task from anywhere, with the client attached by
  default.
- Add inline completion from existing records across every vocabulary field.
- Add inline cell editing (`i`) in the navigator.
- Add `w` on the Team screen to assign the selected member to an account, and
  `i` to paste an underwriter's signature into a market.

### Changed
- Widen attention windows to 120 days so renewals at 91–120 days are no longer
  invisible.
- Give every line of cover its own renewal clock.
- Keep overdue unrenewed programs on the radar rather than dropping them.
- Make `l` edit the layer under the cursor, skipping the picker for
  single-layer programs.
- Carry the bookkit theme and edit-loop conventions across the whole app, and
  show lines of cover on attention tables.
- Extract shared entity action flows so screens stop forking form wiring.

### Fixed
- Require table focus for navigator row actions, and guard the home path
  against stale keys and deleted orgs.

### Infrastructure
- Make `install.sh` PyPI-first with a wheelhouse fallback, surface the PyPI
  failure reason, and pin the wheelhouse SHA256 after a proxy was caught
  altering pip downloads.
- Prove in CI that the published wheelhouse still satisfies an offline install.
- Add convention tests: no raw SQL in `tui/` or `imports/`, and openpyxl stays
  contained.
- Add CLAUDE.md with project conventions and working rules.

## 2026-08-11

### Added
- Initial bookkit: SQLite schema and migrations, models, repo layer, derived
  services, and a seed fixture.
- Add the `bookctl` CLI — `init`, `migrate`, `seed`, `today`, `renewals`,
  `search`, `backup`.
- Add the Textual TUI, towerkit projection and write-through, and creation
  paths for every entity via contextual add/edit forms.
- Add unified placement records: adoption, standing confirmation,
  renew-at-birth, and merges, with platform-wide input normalisation.
- Add carrier alias mapping so every towerkit spelling finds the one market.
- Add layer and program editing via write-through, jump to towerkit, market
  merge and aliases, and the internal team.
- Add the shared import pipeline: registry-driven field mapping, period-aware
  staging that updates rather than duplicates, and a committer that backs up
  first and writes in one transaction with provenance in `event_log`.
- Add import flows for the initial book load, contact signature paste, program
  schedule paste, and renewal paste (diffed by layer name).
- Add the TUI import surfaces — the book import screen on `i`, and the account
  paste chooser.
- Add `bookctl template` and `bookctl import --dry-run`.
- Add the setup dialogue for program roots, and scaffolding a towerkit file
  from a placement.

### Fixed
- Use real transactions, match renewals by attachment, and stop silent renames
  and drops.

### Infrastructure
- Add offline install and launcher mirroring towerkit: `install.sh`,
  `./bookctl`, `Makefile`, `uv.toml`.

---

## Prompt: updating this changelog

Update `changelog.md` with every commit landed on `main` since the most recent
date already listed in the changelog. Follow these rules:

1. Run `git log --pretty=format:"%h|%ad|%s|%an" --date=short --reverse
   <last-listed-date>..origin/main` to see what's new. Also include any
   unmerged commits on the current branch if asked.
2. Group entries by commit author date (`YYYY-MM-DD`), newest date on top. If a
   date already has a section, merge new entries into it rather than
   duplicating the heading.
3. Within each date, bucket bullets under `Added`, `Changed`, `Fixed`,
   `Removed`, `Deprecated`, `Security`, or `Infrastructure`. Omit buckets with
   no entries.
4. One bullet per user-visible change. Collapse trivial follow-ups ("fix
   typo", "address review feedback", formatting-only commits) into the parent
   change instead of listing them separately. Skip pure merge commits, but
   capture the PR they closed.
5. If a change came in via a PR, append `(#<number>)` to the bullet. Find the
   PR by looking for a `Merge pull request #N` commit that references the same
   branch, or via `gh pr list --state merged --search <sha>`.
6. Phrase bullets in the imperative past ("Add X", "Fix Y"). Reference
   user-facing features by name; avoid internal SHAs.
7. Keep the "Prompt: updating this changelog" section at the bottom untouched.
8. After editing, commit the log and push.
