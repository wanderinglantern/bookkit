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
