# MCP batch undo — design

Date: 2026-08-13
Status: approved in conversation (brainstormed with Grant); prerequisite for
any MCP edit tooling

## Goal

One MCP call becomes one undoable unit, and that unit stays reversible
*specifically* — long after later work has piled on top of it. This is the
foundation MCP edit tools are built on: today the server can only create,
and the correction path for a mistake is thin.

Two concrete failures motivate this:

1. **`u` is chronological and global.** Undoing an MCP edit made an hour ago
   means walking back through every TUI change made since. Recovery gets
   harder the longer a mistake goes unnoticed — exactly backwards.
2. **`u` is field-granular.** `log_activity` writes an interaction *and* a
   follow-up task in one call; one `u` unwinds half of it. `services/rfi.py`
   `mark_received` already documents the same hazard for its two-field write.

## Decisions (Grant, 2026-08-13)

1. **Batching is per `db.transaction`, and imports opt out.** A batch means
   "one user-visible action". A 500-row import is not that, and it already
   snapshots the DB to `backups/` before the first row — restore-from-snapshot
   stays its rollback story. `u` must never become a 500-row weapon.
   (Rejected: batching every transaction uniformly; the blast radius of one
   mistyped key would be hours of work.)
2. **The problem is reversibility, not prevention.** Build targeted batch
   revert, *not* preview-then-commit. (Noted and rejected: a two-phase
   preview token is satisfied by the model itself, so it surfaces intent
   rather than gating it. Dropped as YAGNI — if it still feels needed once
   revert exists, it can be added independently.)
3. **Revert is all-or-nothing by default.** If any event in the batch
   conflicts with a later change, revert nothing and report exactly what
   blocks it. The house "surface, don't guess" rule; a half-reverted record
   is neither the before nor the after of any action. `force=true` is the
   explicit escape that reverts the clean events and skips the rest.
4. **Two surfaces: the TUI and MCP.** Grant must be able to audit and reverse
   MCP's work *without the model in the loop* — trusting a model to report its
   own mistakes is not a control. (Rejected for now: a third `bookctl`
   surface; additive later if the production machine wants it.)
5. **Approach A — a `batch_id` column stamped ambiently by `db.transaction`.**
   (Rejected: threading `batch_id=` through every `repo/` signature — a large
   unrelated refactor that fails silently by omission. Rejected: deriving
   batches post-hoc by grouping `event_log` on timestamp — guessing; two calls
   in the same second would merge.)

## Schema — migration 011 (additive only)

```sql
ALTER TABLE event_log ADD COLUMN batch_id TEXT;
CREATE INDEX idx_event_batch ON event_log (batch_id);

CREATE TABLE event_batch (
    id          TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,      -- MCP-0001, via ids.next_ref
    source      TEXT NOT NULL,             -- 'mcp' today; the column is the seam
    tool        TEXT NOT NULL,             -- log_activity, policy_edit, …
    summary     TEXT NOT NULL,             -- the human line the TUI shows
    org_id      TEXT REFERENCES org (id),  -- the account touched, when it is one
    created_at  TEXT NOT NULL,
    reverted_at TEXT
);
```

`event_batch` earns its place over reconstructing batches from `event_log`:
the TUI needs tool, time, account and reverted-state without a scan, and
`reverted_at` makes a second revert inert rather than a double-apply.

Existing `event_log` rows get `batch_id NULL`, which reads correctly as
"unbatched". No backfill, no rewrite, nothing destructive.

## The carrier

`db.transaction` gains a `batch` parameter backed by a module-level
`ContextVar`. `base.log_event` reads it and stamps `batch_id` on every event
written inside the block.

```python
_current_batch: ContextVar[BatchState | None] = ContextVar(
    "bookkit_batch", default=None
)
```

A `ContextVar` rather than an attribute on the connection: `sqlite3.Connection`
is a C type with no `__dict__` and rejects attribute assignment (verified).
A `ContextVar` is also the correct carrier across the MCP server's async tool
wrappers, where a module global would bleed between concurrent calls.

`batch=None` is the default, so **`imports/commit.py` needs no change at all**
to stay unbatched — decision 1 is satisfied by the default rather than by a
special case.

Each MCP write site changes one line:

```python
with batches.open(conn, tool="log_activity", org_id=org.id, summary=...) as b:
    ...                     # every base.insert/update inside is stamped with b
```

`batches.open` creates the `event_batch` row, allocates its ref, and delegates
to `db.transaction`. Eight call sites change; none move.

## Revert semantics — `services/batches.py`

First **collapse the batch to its net effect** per `(entity, field)`: the
oldest event's `old_value` and the newest event's `new_value`. A batch that
wrote the same field twice must be checked and reverted once, against its net
before/after — comparing each raw event against the current value would report
a false conflict on every superseded write.

Then classify each net change:

| Event | Revert action | Conflict when |
|---|---|---|
| field update | restore `old_value` | current value ≠ the batch's `new_value` |
| `created` | soft-delete the row | never — already deleted is a no-op |
| `deleted_at` | undelete | the row is alive again |
| `source` | skip — provenance, not a mutation | — |

**A `created` entity dominates its own field events.** If the batch created a
row and then set fields on it, reverting soft-deletes the row and skips those
field changes entirely — they are neither checked for conflicts nor restored.
Conflict-checking fields on a row that is about to be deleted would refuse
reverts that are in fact clean.

**Two passes: check everything, then apply.** If any event conflicts and
`force` is not set, nothing is written and the caller receives the full
conflict list (field, what the batch set, what it holds now, when it changed).
`force=true` applies the clean events, skips the conflicted ones, and reports
both sets.

The apply pass runs inside one `db.transaction`, is event-logged with
`note='revert'`, and carries **no** `batch_id` — so a revert cannot itself be
batch-reverted, and it is excluded from `last_mutation` exactly as `undo` and
`undelete` already are.

**Known limitation, stated rather than hidden:** a revert is not undoable. It
restores the pre-MCP values; a mistaken revert costs the MCP edit and it would
be redone by hand. `event_log` retains the full history either way, so nothing
is destroyed — but `u` will not bring it back.

## Blast cap

`log_event` increments an entity counter on the batch's `ContextVar` state.
Crossing the cap raises `BlastRadiusExceeded`, which propagates out of
`db.transaction`, hits the existing `ROLLBACK`, and leaves nothing written.
The tool returns an error telling the model to narrow its request.

Enforced at the lowest level, so no future write tool can forget it. The cap
counts **entities, not events** — a three-field edit on one placement is 1.

**REVIEW POINT: the cap defaults to 25 entities.** That number is a judgement,
not a derivation.

## Surfaces

**MCP.** Every write tool's return gains `"batch": "MCP-0007"`. Two new tools:

- `list_batches(limit=20)` — recent batches: ref, tool, account, time, what
  changed, reverted state.
- `revert_batch(ref, force=False)` — the semantics above; returns what was
  reverted and what was refused.

**TUI.** Its own tree section directly below ATTENTION — *not* an attention
leaf. Attention in this app means "act on this" and carries the 120-day
bucket-aligned window plus the overdue-never-falls-off rule; none of that fits
an audit list where most entries need no action. (Deviation from the sketch
Grant first approved; raised and accepted in conversation.)

```
MCP CHANGES (3)
  MCP-0007  14:02  Acme        policy_edit    3 fields
  MCP-0006  13:58  Endeavour   enrich_field   1 field
  MCP-0005  13:12  Acme        log_activity   2 rows      reverted
```

- `R` reverts the highlighted batch — shift, consistent with `D`, keeping
  destructive actions off the unshifted keys. Confirm modal names what is
  going; on conflict the modal becomes the refusal list, with `force` as an
  explicit second choice, never a default.
- `enter` opens the batch's field-level before→after.
- Batches from the last 14 days, reverted ones rendered dim (so a revert is
  visibly confirmed rather than vanishing). The section empties itself as
  batches age out; it is hidden entirely when empty.
- Row actions require table focus, per the house rule.

## Testing

Service-level carries the weight:

- Batch stamping: writes inside `batches.open` share one id; writes outside get
  NULL; `imports/commit.py` stays unbatched with no change.
- **`ContextVar` isolation** — concurrent/nested batches must not bleed. MCP
  tools run under async wrappers; a bleed here would be silent and severe.
- Revert of each event kind (field update, `created`, `deleted_at`), with
  `source` skipped.
- Net-effect collapse: a batch writing one field twice reverts once, to the
  oldest `old_value`, and does **not** report a false conflict.
- A `created` row with later field edits in the same batch reverts by
  soft-delete, with no conflict raised on those fields.
- Conflict detection asserting **the database is genuinely untouched** after a
  refusal — not merely that an error came back.
- `force`: partial apply, both sets reported correctly.
- `reverted_at` set; a second revert is inert.
- A revert does not appear in `last_mutation` and is not batch-revertible.
- **Blast cap rolls back to zero rows.** A cap that raises after writing is
  worse than no cap.

MCP: protocol round-trip for both new tools via `test_mcp_roundtrip.py` — that
harness caught the thread-dispatch bug last phase that unit tests could not
see.

TUI: pilot tests for the section rendering, `R` confirm→revert, the conflict
refusal path, reverted-state rendering, and the focus guard.

Conventions: SQL lives in `repo/batches.py`; rules in `services/batches.py`;
zero raw SQL in `tui/`, per the convention gate. (Standing ledger flag:
`mcpserver.py` is not covered by that grep — the new module lands in `repo/`,
where it is.)

## Out of scope (v1)

Preview-then-commit tokens; a `bookctl` revert surface; batching TUI form
saves (they do not use `db.transaction` today and stay field-granular);
reverting a revert; cross-batch conflict resolution UI; and the MCP edit tools
themselves — this is the mechanism they will be built on, not the tools.
