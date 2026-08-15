# Changelog

All notable changes to bookkit, newest date first.

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
