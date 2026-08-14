# bookkit — project context

## Architecture (load-bearing)

- repo/ owns every SQL query; services/ owns business rules; tui/ and
  imports/ contain ZERO raw SQL (convention-tested).
- Money is integer CENTS; towerkit files carry whole dollars; conversion
  only in sync.py / money.py. Shares: one percent→bps rule owned by
  towerkit `money.parse_share`; bookkit delegates.
- towerkit JSON files are the sole authority for program structure; proj_*
  tables are a rebuildable cache. Every program write goes load → mutate →
  validate → canonical dump → re-project (sha256-guarded, sync.WriteConflict).
- event_log ordering uses SQLite rowid. base.insert/update event-log
  automatically; bulk imports add provenance notes (source + sha256).
- The connection is AUTOCOMMIT (isolation_level=None): real transactions
  need `with db.transaction(conn):` (BEGIN IMMEDIATE). conn.commit()
  outside it is a no-op — this has bitten before.
- One MCP call is ONE undo unit: db.transaction(batch=) stamps every event
  with a batch_id; services/batches.py reverts a batch all-or-nothing,
  refusing when a field changed since ("surface, don't guess"). Blast cap
  db.BLAST_CAP=50 entities per batch (Grant 2026-08-13), enforced under
  log_event so no tool can forget it. `u` stays single-step/field-granular for TUI writes;
  imports/commit.py is unbatched on purpose (snapshot is its rollback).
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
- Vocabulary fields complete from existing records: `Field.suggestions`
  (repo/vocab.py) → textual-autocomplete dropdown + SuggestFromList ghost
  text. Wire both on any new field whose values already exist somewhere.
- One theme: tui/theme.py (palette + status/days/money Text helpers).
  Color is signal, not decoration; every colored state carries a glyph or
  word too.
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
- install.sh is PyPI-first with wheelhouse fallback. New-dep drill lives
  in towerkit's CLAUDE.md (wheel download, re-zip, release --clobber).
- Dates: numeric entry is MDY, two-digit years are 20xx (towerkit fast
  path). Never let dateparser century-bump a past date.
- CONCURRENT PHASES GET THEIR OWN WORKTREE. Two sessions sharing one
  working directory bit us on 2026-08-13: a peer session ran `git
  checkout main` mid-edit and another session's commit landed on main
  instead of its feature branch (caught, cherry-picked, reset — see the
  RFI phase-4 report). One session per worktree, `.claude/worktrees/<name>`,
  removed when the branch merges. Also: redirect gate output to the
  scratchpad, not /tmp — concurrent pytest runs interleave there.
