# bookkit imports core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The import pipeline's pure core plus the initial-book-load flow: fieldspec registry, readers, fuzzy tablemap, staging, matcher, book mapper, backup-first committer, and `bookctl template` / `bookctl import --dry-run`.

**Architecture:** `src/bookkit/imports/` per the spec (2026-08-11-shared-import-pipeline-design.md). Everything below the TUI: readers produce `RawTable`, tablemap fuzzy-assigns headers to a per-flow `FieldSpec` registry, the book mapper stages account/contact/placement records with parsed fields and issues, the matcher flags update-vs-create against the live DB, and the committer snapshots then applies in one transaction. The ImportScreen TUI and the program/renewal/contact flows are the follow-up plan; `commit_book` is fully exercised by tests against a temp DB now so the screen only has to call it.

**Tech Stack:** Python 3.11+, sqlite3, RapidFuzz, openpyxl (already in wheelhouse via towerkit), towerkit.ingest (implemented today), pytest via `uv run pytest`.

## Global Constraints

- Repo: `/Users/grantgreeson/Developer/bookkit`, branch `imports`. Money is integer **cents** in bookkit; conversion only via `money.py` helpers. towerkit files carry whole dollars.
- `imports/` contains **zero SQL** — all reads/writes via `repo/` (convention test enforces it, Task 8).
- mypy strict applies (`imports/` is not under the `tui.*` relaxation). `uv run mypy src` + `uv run ruff check src tests` clean before every commit.
- Field parsing only via existing parsers: `money.parse_money_cents/parse_share_bps`, `dates.parse_human_date`, `normalize.clean_*`. Parse failures become staged issues, never exceptions.
- Backups before commit: `db.backup(conn, dest)` into `backups/` beside the DB, named `<dbname>.<UTC ts>.bak`.
- Verify each task: `uv run pytest tests/test_imports.py -v`.

---

### Task 1: `parse_share_bps` delegates to towerkit (DRY)

`bookkit.money.parse_share_bps` re-implements percent→bps; towerkit now owns `parse_share` (added today). Replace the body with a delegation; behaviour is identical (same error type — bookkit re-exports towerkit's `MoneyParseError`).

**Files:** Modify `src/bookkit/money.py:59-77`. Existing tests cover behaviour; run the money tests only.

- [ ] Replace the body of `parse_share_bps` with:

```python
def parse_share_bps(text: str) -> int:
    """Share entry: '25%', '25', '12.5' → basis points. Percent semantics —
    delegated to towerkit, which owns the tower-grammar parsers."""
    from towerkit.money import parse_share

    return parse_share(text)
```

- [ ] Run: `uv run pytest -q -k money` → pass; `uv run mypy src` clean; commit `money: parse_share_bps delegates to towerkit.parse_share`.

### Task 2: `imports/fieldspec.py` — registry + template writer

**Produces:** `FieldSpec(key, required, example, aliases: tuple[str, ...])` frozen dataclass; `BOOK_FIELDS: tuple[FieldSpec, ...]`; `write_template(specs, path) -> Path`.

`BOOK_FIELDS` (keys, required, example, aliases):
- `account` req "Atomic Industries" ("insured","client","named insured","account name","company")
- `industry` "" ("sector"); `naics`; `domain`; `website` ("url"); `owner` ("producer","account exec")
- `status` "prospect" — must be an `OrgStatus` value
- `contact_name` "Rosa Silva" ("contact","primary contact"); `contact_email` ("email"); `contact_phone` ("phone"); `contact_title` ("title")
- `program` "Property" ("program name","line of business","lob")
- `inception` "2026-10-01" ("eff date","effective","effective date","inception date")
- `expiry` "2027-10-01" ("exp date","expiration","expiry date")
- `premium` "250,000" ("annual premium","total premium") → cents
- `limit` "10M" ("total limit") → cents
- `commission` "15%" ("comm","commission %") → bps
- `placement_status` "bound" — must be a `PlacementStatus` value

`write_template`: same shape as towerkit's (bold canonical headers, light-yellow `F9E6A0` fill on required, one example row from the specs' examples, frozen header row, sheet "Import").

- [ ] Test first in new `tests/test_imports.py` (module docstring "Import pipeline pure core"): template writes and re-reads with zero unmapped columns (uses Task 3's reader + Task 4's mapper — write the test now marked with the later imports; it goes green at Task 4). Also: `test_book_fields_have_unique_keys_and_aliases`.
- [ ] Implement, lint, commit `imports: fieldspec — one registry per flow drives mapping and templates`.

### Task 3: `imports/readers.py`

**Produces:** `RawTable(source: str, sha256: str, headers: list[str], rows: list[dict[str, object]])` (rows keyed by ORIGINAL header, empty cells absent); `read_table(path) -> RawTable` dispatching `.xlsx` (openpyxl read_only/data_only, first sheet, try/finally close) / `.csv` (csv.DictReader); anything else raises `ValueError`. sha256 via `sync.file_sha256`.

- [ ] Tests: csv and xlsx fixtures built in tmp_path round through with identical headers/rows; unknown suffix raises.
- [ ] Implement, lint, commit `imports: readers — files to RawTable, zero interpretation`.

### Task 4: `imports/tablemap.py`

**Produces:** `Mapping(assigned: dict[str, str], unmapped: list[str])`; `map_headers(headers, specs) -> Mapping`; `apply(table, mapping) -> list[dict[str, object]]` (canonical-keyed rows, row order preserved).

Matching per header: normalize (lower, non-alnum→single space, strip); exact match against key or aliases wins; else `rapidfuzz.fuzz.token_set_ratio` vs every key+alias, best ≥ 85 wins; ties or below → unmapped. A canonical key claimed twice → second header goes unmapped (first wins, deterministic left-to-right).

- [ ] Tests: exact, alias ("Eff Date"→inception), fuzzy ("Efective Date" typo→inception), garbage → unmapped, duplicate claim.
- [ ] Implement, lint, commit `imports: tablemap — fuzzy headers to canonical fields, nothing dropped silently`.

### Task 5: `imports/staging.py`

**Produces:** `Severity` StrEnum (`ERROR`/`WARNING`); `Issue(severity, field, message)`; `StagedRecord(kind: str, key: str, fields: dict[str, object], source_row: int, action: str = "create", target_id: str | None = None, issues: list[Issue])`; `StagedImport(source, sha256, records, unmapped)` with `errors` property (all ERROR issues incl. a synthetic one per unmapped header? No — unmapped is a warning; report lists it), `ok` (no ERROR issues), and `report() -> str` — the CLI dry-run text: per-kind counts by action, then each record line `<kind> <key> [<action>]` with indented issues, then unmapped headers.

- [ ] Tests: ok/errors gating; report contains counts, issue lines, unmapped section.
- [ ] Implement (pure dataclasses, no imports from repo/), lint, commit `imports: staging — the reviewable middle of every import`.

### Task 6: `imports/matcher.py`

**Produces:**
- `match_org(conn, name) -> tuple[str | None, list[str]]` — (org_id, []) on exact `repo.orgs.find_by_name` or single fuzzy hit ≥ 90 (`rapidfuzz.fuzz.WRatio` over `repo.orgs.list_orgs(conn)` client names — check its real signature at `src/bookkit/repo/orgs.py:41` and filter to kind=client); (None, [names...]) when several ≥ 90 (conflict); (None, []) when no match.
- `match_contact(conn, org_id, email) -> str | None` — case-insensitive email equality over `repo.contacts.for_org`.
- `match_placement(conn, org_id, program_name, period_from, period_to) -> str | None` — over `repo.placements.for_org`: same program_name (case-insensitive) AND period overlap (`from_a <= to_b and from_b <= to_a` on ISO strings). Period-aware per spec: never match on account alone.

- [ ] Tests against a temp DB (`db.connect(tmp_path / "t.db")`, create orgs/contacts/placements via repo): exact, fuzzy single, conflict, no-match; contact email hit/miss; placement overlap in/out, staggered periods pick the right one.
- [ ] Implement, lint, commit `imports: matcher — period-aware, update-not-duplicate`.

### Task 7: `imports/mappers/book.py`

**Produces:** `stage_book(conn, table: RawTable, mapping: Mapping) -> StagedImport`.

Logic: `rows = tablemap.apply(table, mapping)`. Group by cleaned account name (`normalize.clean_text`); missing account → ERROR issue on a rowless record. Per account, first occurrence stages an `account` record: normalized fields (domain/website/naics via clean_*, each failure an ERROR Issue on that field), `status` must be an `OrgStatus` value else ERROR; `match_org` → action `update` + target_id, conflict → ERROR issue listing candidates. Rows with `contact_name` or `contact_email` stage a `contact` record (kind "contact", key `<account>/<name-or-email>`; name split first/last on last space; email via clean_email; `fields["org_key"] = account key`); `match_contact` when the org matched → update. Rows with all of `program`+`inception`+`expiry` stage a `placement` (dates via `parse_human_date` → ISO, expiry ≤ inception → ERROR; premium/limit via `parse_money_cents`; commission via `parse_share_bps`; `placement_status` must be a `PlacementStatus` value; `fields["org_key"]` links it); `match_placement` (when org matched) → update.

- [ ] Tests: a 4-row fixture (two accounts, one with contact+placement, one bad money cell, one conflicting status) asserting record kinds/actions/issues; re-staging against a DB where the account exists yields `update` actions.
- [ ] Implement, lint, commit `imports: book mapper — rows to staged accounts, contacts, placements`.

### Task 8: `imports/commit.py` + convention test

**Produces:** `CommitResult(created: dict[str, int], updated: dict[str, int], backup: Path)`; `commit_book(conn, staged: StagedImport, db_path: Path) -> CommitResult`.

- Raises `ValueError` if `staged.ok` is False (zero-errors gate) — the caller shows issues, never this exception.
- Backup FIRST: `backups = db_path.parent / "backups"`, mkdir ok, `db.backup(conn, backups / f"{db_path.name}.{utc_now-with-colons-stripped}.bak")`.
- Then one transaction (`try:` … `conn.commit()` / `except: conn.rollback(); raise`): accounts in staging order — `create` → `repo.orgs.create(conn, kind=OrgKind.CLIENT, name=..., **clean fields)`, `update` → `repo.orgs.update(conn, target_id, **changed fields)`; remember `org_key → org_id`. Contacts and placements resolve `org_key` through that map (or their matched target). Placements via `repo.placements.create(conn, org_id, program_name, period_from, period_to, status=..., total_premium=..., total_limit=..., commission_bps=...)` / `.update`. Every record gets `repo.base.log_event(conn, <kind-table>, <id>, "import", None, staged.source, note=f"import sha256={staged.sha256}")` — provenance per spec.
- Convention test in the same task: walk `src/bookkit/imports/**/*.py`, assert no `SELECT|INSERT INTO|UPDATE .* SET|DELETE FROM` (mirror the style of the existing convention test).

- [ ] Tests: full pipeline on a temp DB — template → read → map → stage → commit; assert org/contact/placement rows exist, event_log carries the sha note, backup file exists; second identical import stages as updates and creates nothing new; a staged ERROR refuses commit and writes nothing.
- [ ] Implement, lint, commit `imports: committer — backup first, one transaction, provenance in event_log`.

### Task 9: CLI — `bookctl template` / `bookctl import --dry-run`

Read `src/bookkit/cli.py` for its handler dispatch style and follow it.

- `bookctl template <book|program> <out.xlsx>` — book → `write_template(BOOK_FIELDS, out)`; program → towerkit's `ingest_template.write_template(out)` (one registry each side, no duplication).
- `bookctl import book <file> [--dry-run]` — read → map → stage against the real DB → print `staged.report()`; exit 1 when not `ok`. Without `--dry-run` print the report plus `commit happens in the TUI import screen (next plan); use --dry-run for now` and exit 2 — committing stays in the TUI per spec, and the flag's absence must not silently write.

- [ ] Tests in tests/test_imports.py using the cli `main([...])` style found in the repo's existing CLI tests: template writes; dry-run on a template-shaped file prints counts and exits 0; bad file exits 1.
- [ ] Implement, lint, run **full suite**, commit `cli: bookctl template + import --dry-run — staging report from the shell`.

## Deviation notes

- Commit-from-TUI is deferred to the follow-up plan (ImportScreen + program/renewal/contact flows); `commit_book` is still fully implemented and tested now so the screen becomes a thin caller.
- `bookctl import` without `--dry-run` exits 2 with a pointer instead of committing — safer than a CLI commit the spec said not to build.
