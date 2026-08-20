# Changelog

All notable changes to bookkit, newest date first.

## 2026-08-20

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
