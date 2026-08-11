# Shared import pipeline — bookkit ingest + towerkit.ingest

**Date:** 2026-08-11
**Status:** Approved design, pre-implementation
**Repos touched:** bookkit (primary), towerkit (new `ingest` module + CLI verbs)

## Decision context

bookkit and towerkit stay **separate repos** (decided 2026-08-11): towerkit remains
independently shareable as a diagram tool. This spec adds bulk data loading to both,
split by knowledge ownership so nothing is written twice:

- Tower/program knowledge (layer grammar, "15M xs 10M", participants, shares,
  what makes a valid program) → **towerkit**.
- CRM knowledge (accounts, contacts, matching, carrier→market aliases, pipeline)
  → **bookkit**.

**The seam rule:** bookkit decides *which* input is a tower and maps messy headers
to canonical field names; towerkit decides *what the tower means*. towerkit never
sees "account", "market alias", or original spreadsheet headers. bookkit never
re-implements layer grammar.

Rejected alternatives: all ingest in towerkit (bloats the shareable tool with CRM
concepts); a third shared package (speculative infrastructure, third wheelhouse
artifact on the locked-down work machine).

## Inputs and workflows

Source data: **pasted/unstructured text** (quote emails, binder text, PDF pastes)
and **Excel/CSV schedules**. No AMS-export formats for now.

Four flows, in build order:

1. **Initial book load** — xlsx/csv → accounts, contacts, placements. Pure bookkit.
   Tracer bullet: proves reader → tablemap → mapper → matcher → staging → commit
   end-to-end. Re-runnable against a half-entered book (matcher defaults matched
   rows to *update*, never duplicate-create).
2. **Program from schedule** — paste or workbook, launched from an account screen.
   Mapper delegates to `towerkit.ingest`; file lands via the existing scaffold
   path; file↔account link stays user-confirmed.
3. **Contact/interaction capture** — pasted signature or email thread → contact +
   interaction. Entirely bookkit, uses `normalize.py` cleaners.
4. **Renewal updates** — paste or schedule against an existing placement. When
   the account has several linked programs (staggered effective dates, multiple
   lines), the target placement is chosen explicitly first; the parsed
   inception date pre-selects the placement whose expiring period it abuts.
   Staging shows a **diff** (old → new premium, limits, carriers). Commit
   updates the placement and writes the new-period program via existing
   renew-at-birth machinery. One towerkit file carries one period, as today —
   staggered lines remain separate files.

Import UX decision: **staged preview** (not direct-write, not strict-or-nothing).
Every import parses into a reviewable staging list; user fixes or skips rows;
commit is a single transaction, enabled only at zero errors (warnings allowed).

## towerkit: new `ingest` module

`src/towerkit/ingest.py`, public API:

- `parse_tower(text: str) -> DraftProgram` — pasted schedule text. Grammar:
  layer lines (`primary`, `<limit> xs <attachment>`), participant lists with
  percent shares, optional premiums. Reuses existing `money.py` / `dates.py`
  parsers. Carrier names kept **verbatim** as strings — alias resolution is the
  caller's job.
- `program_from_rows(rows: list[dict]) -> DraftProgram` — tabular input using
  **canonical field names**: `layer`, `limit`, `attachment`, `carrier`, `share`,
  `premium`, `inception`, `expiry`. Callers map their own headers first.
  Accepts the SOI workbook shape.
- `DraftProgram` — Program-shaped draft plus `Diagnostics` (existing validate
  vocabulary). Drafts may be incomplete; `to_program()` refuses while errors
  remain. Keeps "parse what you can, surface what you couldn't" out of the
  strict `Program` model.

CLI verbs:

- `towerctl import <file-or-paste> -o program.json` — build a program file from
  a schedule, then open the existing TUI editor for touch-up. Standalone value:
  paste a schedule, get a diagram, no CRM involved.
- `towerctl template <out.xlsx>` — emit the SOI workbook layout as a blank,
  populate-and-reimport template (mirror of `towerctl soi` export).

Money stays whole dollars inside towerkit (existing rule). No new towerkit deps.

## bookkit: new `imports/` package

```
source (xlsx / csv / pasted text)
  → reader      → RawTable (headers, rows, provenance) or raw text
  → tablemap    → canonical field names (fuzzy header match, RapidFuzz)
  → mapper      → list[StagedRecord]   (one mapper per flow)
  → matcher     → match flags against existing DB rows
  → staging UI  → review / fix / skip per row
  → committer   → one SQLite transaction; program parts via sync write-through
```

Modules under `src/bookkit/imports/`:

- `readers.py` — xlsx via openpyxl (already in the wheelhouse as a towerkit dep;
  add as a direct bookkit dep, no wheelhouse rebuild), csv via stdlib, paste as
  text. Readers produce `RawTable` only; zero interpretation.
- `tablemap.py` — arbitrary headers → canonical fields ("Eff Date", "Inception",
  "Effective" → `effective_date`) via RapidFuzz. Unmapped columns surfaced,
  never silently dropped; guessed mapping shown and editable in preview.
- `fieldspec.py` — **one registry per flow** of canonical fields (name,
  required?, parser, example). Single source of truth driving both `tablemap`
  matching and template export.
- `mappers/` — `book_load.py`, `contacts.py`, `program.py`, `renewal.py`.
  Field parsing via existing `money.py` / `dates.py` / `normalize.py`; parse
  failures become row issues, not exceptions. `program.py` is thin: hands
  canonical rows or tower text to `towerkit.ingest`, resolves carrier strings
  through existing market aliases.
- `staging.py` — pure core. `StagedRecord`: entity kind, parsed fields,
  `action` (create / update / skip), matched `target_id`, `issues:
  [(severity, field, message)]`. No SQL.
- `matcher.py` — fuzzy match against existing orgs/contacts/placements (same
  RapidFuzz approach as market aliases). Two candidates above threshold →
  *conflict* issue requiring an explicit pick. Clean match → default action
  **update**. Placement matching is **period-aware**: the key is
  (account, line/program, effective period), never account alone — a client
  with staggered effective dates has several live placements, and a schedule
  row must land on the one whose period it belongs to. When the source data's
  dates don't disambiguate, that is a conflict requiring a pick, not a guess.
- `commit.py` — DB-only flows: one transaction, event_log entries (rowid
  ordering as always). Flows touching a towerkit file follow existing
  write-through order: file written and validated **first**, then the DB
  transaction; file failure aborts the entire import. Every commit records
  source provenance (filename + sha256) in the event_log note. Consequence of
  the write-through rule: any import that changes program structure updates
  the `.json` file at commit time, so towerkit always views current data —
  the DB never holds program facts the file lacks.

UI/CLI:

- TUI `ImportScreen`: pick flow → paste or choose file → preview table grouped
  by entity with match/conflict/error flags → inline field edits with existing
  form widgets → commit (zero-errors gate).
- `bookctl import <flow> <file> --dry-run` — staging report for headless checks.
  Committing stays in the TUI.
- `bookctl template <flow> <out.xlsx>` — blank workbook from the flow's field
  spec: canonical headers, one worked example row, required columns marked.
  Round-trips through import with zero unmapped columns.

## Error handling

- Issues, not exceptions: every parse/match problem lands on the row as
  `(severity, field, message)`.
- Ambiguity is explicit: conflicting matches require a pick; unknown carriers
  surface as warnings resolved via market aliases or user choice.
- Zero-errors gate on commit; warnings allowed through.
- Idempotence: re-importing the same file updates/skips, never duplicates.

## Backups

Bulk import is a bulk-write. Before any commit, the committer snapshots the
SQLite file and any towerkit program files it will touch into a `backups/`
directory beside the database, named `<original>.<UTC timestamp>.bak`, in
addition to the transactional commit and event_log undo path. Pruning is
manual. No irreversible write without a copy on disk.

## Testing

- Pure-core, table-driven: readers, tablemap, fieldspec, each mapper, staging —
  no TUI. Golden paste samples for `parse_tower` in towerkit's tests.
- Contract test (towerkit): SOI export → `program_from_rows` → equal program.
  Anchors template shape and import behaviour simultaneously.
- Template test (both): exported template re-imports with zero unmapped columns.
- Convention test: `imports/` contains no SQL (all writes via `repo/`).
- One Textual pilot test: paste → preview → commit through `ImportScreen`.

## Out of scope

- AMS-export column layouts (EPIC/AMS360/Sagitta) — add as fieldspec aliases
  when a real export file exists.
- PDF parsing — pasted text only; a PDF is pasted by the user, not read by us.
- Auto-commit / watch-folder imports — staging review is the point.
- Refactoring `seed.py` to build via `towerkit.ingest` — it works; noted only.
