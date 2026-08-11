# DECISIONS

One line per choice the brief didn't specify. Newest at the bottom.

- Money is integer **cents** (minor units) in bookkit per §3.1; towerkit files carry whole dollars, so projection multiplies by 100 at the sync boundary and write-through divides — conversion lives only in `sync.py`.
- Reused towerkit's `Diagnostics`/`Diagnostic` classes (`towerkit.validate`) instead of duplicating them; bookkit already depends on towerkit and the semantics are identical.
- ULIDs are generated in-repo (`ids.py`, ~30 lines, Crockford base32 over time+`os.urandom`) rather than adding a `python-ulid` dependency — keeps the corporate wheelhouse story unchanged.
- Human refs only where the brief shows them: `ACC-` orgs, `OPP-` opportunities, `PLC-` placements. Contacts/interactions/tasks/submissions are always reached through their parent and get none.
- `bookctl seed --demo` loads the demo fixture; the fixture generator lives in `src/bookkit/seed.py` so tests and demos share it.
- towerkit dependency is a uv path dependency (`../towerkit`, editable) — the two repos are siblings by construction (§0).
- `program_link` also records `insured_name` at confirm time so a renamed org doesn't silently orphan the link.
- Date input: towerkit's `parse_flexible_date` handles absolute human forms; bookkit adds `+2w`/`+3d`/`+1m` relative shorthand on top (`dates.py`) since renewal work speaks in offsets.
- Undo (`u`) covers the last *field mutation* recorded in `event_log` (single-field revert); creates/deletes are undone by re-delete/un-delete of the row, not full transactional replay.
- Soft-delete filtering is enforced by giving every repo module a single `_rows()`/`fetch` helper that appends `deleted_at IS NULL`; tests grep repo/ for raw `SELECT` against soft-delete tables outside the helpers.
- Sentiment on interactions is a free 'pos'|'neu'|'neg' text column, nullable, no UI ceremony — brief mentions it only in the schema.
- Weighted pipeline value = target_premium × probability_pct / 100, floor-divided, in cents.
- Staleness weighting: ordered by (days_stale × current bound premium), so big neglected accounts sort first; accounts with no premium still appear, premium-weight 1.
- FTS5 uses external-content tables with AFTER INSERT/UPDATE/DELETE triggers; soft-deleted rows are removed from the index by the UPDATE trigger when deleted_at is set.
- `bookctl init` and every CLI/TUI entry apply pending migrations on startup inside a transaction (§3.4); `bookctl migrate` exists for explicitness.
- DB path: `$XDG_DATA_HOME/bookkit/bookkit.db` (default `~/.local/share/bookkit/`), created 0600; `BOOKKIT_DB` env var overrides for tests/dev.
- Deferred (recorded per §5.2): an optional `bookkitRef` field in towerkit's program schema would make file↔account linking exact; needs a towerkit schema change + migration, so the `program_link` table carries the mapping for now.
- Stage moves: forward one gate at a time; won/lost allowed from any open stage (deals die anywhere); closed is closed. Won → probability 100, lost → 0.
- Hit rate counts a bound submission as quoted (it was quoted on the way to binding), so quote_rate = (quoted+bound)/sent and bind_rate = bound/quoted.
- Book summary "by line": placements carry no line column (per the §3.2 schema), so grouping strips a leading year from program_name ("2025 Casualty Program" → "Casualty Program"). A proper line column is a schema change left for when it's needed.
- event_log ordering uses SQLite rowid (insert order); ULIDs within one millisecond are not monotonic.
- Projection creates the placement row when a linked file has no matching (path or org+period) placement — the file *is* a program period, and the cross-book query must see it. towerkit placement bound→bound, proposed→prospective.
- Projection refreshes placement program_name/period/totals from the file (the file is the source of truth for program structure, §5); commission_bps and status stay bookkit-owned.
- TUI sync roots come from `BOOKKIT_PROGRAM_ROOTS` (colon-separated), `y` on Today runs project_all + the link review queue; the CLI takes explicit `--roots`.
- Field-level editing in the TUI covers the high-frequency mutations (log interaction, task done, stage move, contact primary, file links); broad record editing is CLI/DB territory for now — the 5-second capture path is the product.
- Unlinked files are review items, not errors: `bookctl sync` exits 0 with them listed; only validation failures exit non-zero.
- SVG screen snapshots are written by the TUI tests as crash+render cover but not committed as compared baselines (gitignored).
- 10× responsiveness (§8) rests on the schema indexes (expiry, org+date, market+status, FTS) rather than a perf test; every hot query is indexed and the seed is small enough that a dedicated benchmark would test nothing real yet.
- App-level `n` and `/` are ignored while a modal is open — stacked modals from a key leaking through an OptionList were worse than requiring esc first.
- Offline install mirrors towerkit's wheelhouse pattern; bookkit's wheelhouse carries the merged dependency closure of BOTH projects (uv export minus the towerkit package itself) and towerkit installs editable from the required `../towerkit` sibling checkout — a frozen towerkit wheel would defeat the live-file integration.
- `./bookctl` wrapper mirrors towerkit's `./towerctl`: bare invocation is the common case (the TUI — the CLI already does this for no args), everything else passes through.
- install.sh WHEELHOUSE_URL assumes `wanderinglantern/bookkit` v0.1.0 on GitHub (same account as towerkit); the release doesn't exist yet — run `make wheelhouse` and create it, or edit the URL.
- towerkit's new `Line.group` is not projected into proj_* (see NOTES.md) — display-only for bookkit today.
