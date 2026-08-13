# MCP Batch Undo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one MCP call one undoable unit that stays revertible *specifically*, long after later work has piled on top of it.

**Architecture:** An additive `batch_id` column on `event_log` is stamped by `base.log_event` from a `ContextVar` that `db.transaction` sets. A new `event_batch` table carries the human-facing metadata. `services/batches.py` collapses a batch to its net effect, detects conflicts against current values, and reverts all-or-nothing. Two surfaces consume it: two MCP tools and a Navigator tree section.

**Tech Stack:** Python 3.13, SQLite (autocommit + `db.transaction`), pydantic row models, Textual 8.x TUI, MCP SDK, pytest/mypy/ruff.

**Spec:** `docs/superpowers/specs/2026-08-13-mcp-batch-undo-design.md`

## Global Constraints

- **`repo/` owns every SQL query.** `services/` and `tui/` contain ZERO raw SQL — `tests/test_conventions.py` fails the build otherwise. Test files may use SQL directly.
- **Gates before every commit:** `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. Never pipe pytest output before an `&&` gate — pipes eat exit codes. Redirect to a file, gate on the command, tail the file after.
- **Write gate output to the session scratchpad, not `/tmp`** — concurrent sessions interleave there.
- **Migrations are additive-only.** 011 adds one column and one table. It must not `ALTER` anything existing beyond the `ADD COLUMN`, and must not backfill.
- **Writes go through `base.insert` / `base.update` / `base.soft_delete`** so `event_log` records them. Never raw INSERT/UPDATE on entity tables.
- **The connection is AUTOCOMMIT.** Real transactions need `with db.transaction(conn):`. `conn.commit()` outside it is a silent no-op.
- **`Row` models use `extra="forbid"`.** Adding a column to a table means adding the field to its model, or `from_row` raises.
- **Cap default is 25 entities** (`BLAST_CAP`). Flagged a REVIEW POINT in the spec — implement 25, do not invent a different number.
- **Money is integer cents; dates are ISO strings.** Never `date.today()` inside a service — pass `today`/`now` as a parameter.

---

### Task 1: Migration 011, the `EventBatch` model, and `repo/batches.py`

**Files:**
- Create: `migrations/011_event_batch.sql`
- Modify: `src/bookkit/models.py` (add `EventBatch`; add `batch_id` to `EventLogEntry`)
- Create: `src/bookkit/repo/batches.py`
- Test: `tests/test_repo.py`

**Interfaces:**
- Consumes: `repo.base.alive`, `ids.new_ulid`, `ids.next_ref`, `models.Row`
- Produces:
  - `models.EventBatch` with fields `id, ref, source, tool, summary, org_id, created_at, reverted_at`
  - `models.EventLogEntry.batch_id: str | None = None`
  - `repo.batches.create(conn, *, batch_id: str, source: str, tool: str, summary: str, org_id: str | None) -> EventBatch`
  - `repo.batches.get(conn, batch_id: str) -> EventBatch` (raises `KeyError`)
  - `repo.batches.get_by_ref(conn, ref: str) -> EventBatch` (raises `KeyError`)
  - `repo.batches.new_batch_id() -> str`
  - `repo.batches.recent(conn, since: str, limit: int = 20) -> list[EventBatch]`
  - `repo.batches.events_for(conn, batch_id: str) -> list[EventLogEntry]`
  - `repo.batches.mark_reverted(conn, batch_id: str, at: str) -> None`

- [ ] **Step 1: Write the migration**

Create `migrations/011_event_batch.sql`:

```sql
-- MCP batch undo: one tool call becomes one undoable unit. Additive only —
-- one nullable column on event_log (existing rows read as "unbatched", which
-- is correct) and one new table. No backfill, nothing rewritten.
ALTER TABLE event_log ADD COLUMN batch_id TEXT;
CREATE INDEX idx_event_batch ON event_log (batch_id);

CREATE TABLE event_batch (
    id          TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    tool        TEXT NOT NULL,
    summary     TEXT NOT NULL,
    org_id      TEXT REFERENCES org (id),
    created_at  TEXT NOT NULL,
    reverted_at TEXT
);
CREATE INDEX idx_event_batch_created ON event_batch (created_at);
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_repo.py`:

```python
def test_event_batch_round_trips_and_lists_recent(conn):
    from bookkit.repo import batches

    client = orgs.create(conn, kind="client", name="Acme")
    made = batches.create(
        conn, batch_id="01BATCHONE", source="mcp", tool="log_activity",
        summary="logged a call", org_id=client.id,
    )
    assert made.ref.startswith("MCP-")
    assert made.reverted_at is None

    got = batches.get_by_ref(conn, made.ref)
    assert got.id == "01BATCHONE"
    assert got.tool == "log_activity"

    listed = batches.recent(conn, since="2000-01-01T00:00:00Z")
    assert [b.id for b in listed] == ["01BATCHONE"]


def test_event_batch_get_by_ref_raises_on_unknown(conn):
    from bookkit.repo import batches

    with pytest.raises(KeyError):
        batches.get_by_ref(conn, "MCP-9999")


def test_mark_reverted_stamps_the_batch(conn):
    from bookkit.repo import batches

    made = batches.create(
        conn, batch_id="01BATCHTWO", source="mcp", tool="task_create",
        summary="made a task", org_id=None,
    )
    batches.mark_reverted(conn, made.id, "2026-08-13T18:00:00Z")
    assert batches.get_by_ref(conn, made.ref).reverted_at == "2026-08-13T18:00:00Z"


def test_events_for_returns_only_that_batch_in_order(conn):
    from bookkit.repo import base, batches

    batches.create(conn, batch_id="01BATCHTHREE", source="mcp", tool="t",
                   summary="s", org_id=None)
    conn.execute(
        "INSERT INTO event_log (id, entity_type, entity_id, field, old_value,"
        " new_value, changed_at, note, batch_id)"
        " VALUES ('e1','task','t1','title','a','b','2026-08-13T10:00:00Z',NULL,'01BATCHTHREE')"
    )
    conn.execute(
        "INSERT INTO event_log (id, entity_type, entity_id, field, old_value,"
        " new_value, changed_at, note, batch_id)"
        " VALUES ('e2','task','t1','due_on',NULL,'2026-09-01','2026-08-13T10:00:01Z',NULL,'01BATCHTHREE')"
    )
    conn.execute(
        "INSERT INTO event_log (id, entity_type, entity_id, field, old_value,"
        " new_value, changed_at, note, batch_id)"
        " VALUES ('e3','task','t9','title','x','y','2026-08-13T10:00:02Z',NULL,NULL)"
    )
    got = batches.events_for(conn, "01BATCHTHREE")
    assert [e.id for e in got] == ["e1", "e2"]
    assert got[0].batch_id == "01BATCHTHREE"
    assert base.alive  # module import guard, keeps the import used
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_repo.py -k "event_batch or mark_reverted or events_for"`
Expected: FAIL with `ModuleNotFoundError: No module named 'bookkit.repo.batches'`

- [ ] **Step 4: Add the models**

In `src/bookkit/models.py`, add `batch_id` to `EventLogEntry` (it is `extra="forbid"`, so the new column breaks `from_row` without this):

```python
class EventLogEntry(Row):
    id: str
    entity_type: str
    entity_id: str
    field: str
    old_value: str | None = None
    new_value: str | None = None
    changed_at: str
    note: str | None = None
    batch_id: str | None = None
```

Add `EventBatch` directly below it:

```python
class EventBatch(Row):
    """One writer action grouped for undo — today always an MCP tool call.
    `summary` is the line the TUI shows; `reverted_at` makes a second revert
    inert rather than a double-apply."""

    id: str
    ref: str
    source: str
    tool: str
    summary: str
    org_id: str | None = None
    created_at: str
    reverted_at: str | None = None
```

- [ ] **Step 5: Write `repo/batches.py`**

```python
"""Undo batches — the event_log grouping that makes one writer action one
undoable unit. SQL only; the revert rules live in services/batches.py."""

from __future__ import annotations

import sqlite3

from ..ids import new_ulid, next_ref
from ..models import EventBatch, EventLogEntry
from ..util import utc_now

BATCH_REF = "MCP"


def create(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    source: str,
    tool: str,
    summary: str,
    org_id: str | None,
) -> EventBatch:
    """The caller supplies batch_id because events written inside the same
    transaction must be stamped with it before this row is queried back."""
    conn.execute(
        "INSERT INTO event_batch (id, ref, source, tool, summary, org_id,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (batch_id, next_ref(conn, BATCH_REF), source, tool, summary, org_id,
         utc_now()),
    )
    return get(conn, batch_id)


def get(conn: sqlite3.Connection, batch_id: str) -> EventBatch:
    row = conn.execute(
        "SELECT * FROM event_batch WHERE id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"batch {batch_id} not found")
    return EventBatch.from_row(row)


def get_by_ref(conn: sqlite3.Connection, ref: str) -> EventBatch:
    row = conn.execute(
        "SELECT * FROM event_batch WHERE ref = ?", (ref,)
    ).fetchone()
    if row is None:
        raise KeyError(f"batch {ref} not found")
    return EventBatch.from_row(row)


def recent(
    conn: sqlite3.Connection, since: str, limit: int = 20
) -> list[EventBatch]:
    """Newest first. `since` is an ISO timestamp the caller computes — no wall
    clock in here."""
    rows = conn.execute(
        "SELECT * FROM event_batch WHERE created_at >= ?"
        " ORDER BY created_at DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    return [EventBatch.from_row(r) for r in rows]


def events_for(conn: sqlite3.Connection, batch_id: str) -> list[EventLogEntry]:
    """Oldest first (rowid order) — the collapse in services/batches.py takes
    the first old_value and the last new_value, so order is load-bearing."""
    rows = conn.execute(
        "SELECT * FROM event_log WHERE batch_id = ? ORDER BY rowid", (batch_id,)
    ).fetchall()
    return [EventLogEntry.from_row(r) for r in rows]


def mark_reverted(conn: sqlite3.Connection, batch_id: str, at: str) -> None:
    conn.execute(
        "UPDATE event_batch SET reverted_at = ? WHERE id = ?", (at, batch_id)
    )


def new_batch_id() -> str:
    return new_ulid()
```

Check that `utc_now` and `next_ref` live where this imports them (`src/bookkit/util.py` and `src/bookkit/ids.py` respectively — confirm with `grep -rn "def utc_now\|def next_ref" src/bookkit/`) and fix the import to match.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_repo.py -k "event_batch or mark_reverted or events_for"`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full gates**

```bash
S=<scratchpad>
uv run pytest -q > $S/t1.txt 2>&1; echo "EXIT:$?"
uv run mypy src > $S/t1m.txt 2>&1; echo "EXIT:$?"
uv run ruff check src tests > $S/t1r.txt 2>&1; echo "EXIT:$?"
```
Expected: all three exit 0.

- [ ] **Step 8: Commit**

```bash
git add migrations/011_event_batch.sql src/bookkit/models.py src/bookkit/repo/batches.py tests/test_repo.py
git commit -m "batches: migration 011 — event_log.batch_id and the event_batch table"
```

---

### Task 2: The `ContextVar` carrier and `log_event` stamping

**Files:**
- Modify: `src/bookkit/db.py` (add `BatchState`, `_current_batch`, `current_batch()`, `transaction(batch=)`)
- Modify: `src/bookkit/repo/base.py:42-64` (`log_event` stamps `batch_id`)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `repo.batches` (Task 1) is NOT used here — this task is pure carrier.
- Produces:
  - `db.BatchState` dataclass with `batch_id: str`, `cap: int`, `entities: set[str]`, and method `touch(entity_id: str) -> None`
  - `db.current_batch() -> BatchState | None`
  - `db.transaction(conn, batch: BatchState | None = None)` — unchanged behaviour when `batch` is None
  - `db.BLAST_CAP: int = 25`
  - `db.BlastRadiusExceeded(Exception)`

Note: `touch()` raising is implemented in Task 3. In this task `touch()` only records.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_events_inside_a_batch_share_its_id(tmp_path):
    from bookkit import db
    from bookkit.repo import base, orgs

    conn = db.connect(tmp_path / "b.db")
    state = db.BatchState(batch_id="01BATCH", cap=99)
    with db.transaction(conn, batch=state):
        orgs.create(conn, kind="client", name="Acme")
    rows = conn.execute(
        "SELECT DISTINCT batch_id FROM event_log WHERE batch_id IS NOT NULL"
    ).fetchall()
    assert [r[0] for r in rows] == ["01BATCH"]
    assert base.alive


def test_events_outside_a_batch_are_unstamped(tmp_path):
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    orgs.create(conn, kind="client", name="Acme")          # no transaction
    with db.transaction(conn):                              # transaction, no batch
        orgs.create(conn, kind="client", name="Beta")
    rows = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE batch_id IS NOT NULL"
    ).fetchone()
    assert rows[0] == 0


def test_batch_context_is_cleared_after_the_block(tmp_path):
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    with db.transaction(conn, batch=db.BatchState(batch_id="01B", cap=99)):
        orgs.create(conn, kind="client", name="Acme")
    assert db.current_batch() is None
    orgs.create(conn, kind="client", name="Beta")
    n = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE batch_id = '01B'"
    ).fetchone()[0]
    stamped_after = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE batch_id IS NOT NULL"
    ).fetchone()[0]
    assert n == stamped_after      # nothing leaked into the batch afterwards


async def test_concurrent_batches_do_not_bleed(tmp_path):
    """The spec calls this out as the failure that would be silent and severe:
    MCP tools run under async wrappers, so two batches can be in flight in the
    same process. A ContextVar keeps them apart; a module global would not."""
    import asyncio

    from bookkit import db
    from bookkit.repo import base

    conn = db.connect(tmp_path / "b.db")
    seen: dict[str, str | None] = {}

    async def run(name: str) -> None:
        token = db._current_batch.set(db.BatchState(batch_id=name, cap=99))
        try:
            await asyncio.sleep(0)          # force interleaving
            state = db.current_batch()
            seen[name] = None if state is None else state.batch_id
        finally:
            db._current_batch.reset(token)

    await asyncio.gather(run("01AAA"), run("01BBB"))
    assert seen == {"01AAA": "01AAA", "01BBB": "01BBB"}
    assert db.current_batch() is None
    assert base.alive


def test_batch_context_is_cleared_when_the_block_raises(tmp_path):
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    with pytest.raises(RuntimeError):
        with db.transaction(conn, batch=db.BatchState(batch_id="01B", cap=99)):
            orgs.create(conn, kind="client", name="Acme")
            raise RuntimeError("boom")
    assert db.current_batch() is None
    assert conn.execute("SELECT COUNT(*) FROM org").fetchone()[0] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_db.py -k "batch"`
Expected: FAIL with `AttributeError: module 'bookkit.db' has no attribute 'BatchState'`

- [ ] **Step 3: Add the carrier to `db.py`**

At the top of `src/bookkit/db.py`, alongside the existing imports:

```python
from contextvars import ContextVar
from dataclasses import dataclass, field as dc_field

BLAST_CAP = 25
"""Most entities one batched writer action may touch before it is refused.
A judgement, not a derivation — REVIEW POINT in the design doc."""


class BlastRadiusExceeded(Exception):
    """A batched write tried to touch more entities than its cap allows."""


@dataclass
class BatchState:
    """Ambient state for one batched action: which batch events belong to, and
    how many distinct entities it has touched so far."""

    batch_id: str
    cap: int = BLAST_CAP
    entities: set[str] = dc_field(default_factory=set)

    def touch(self, entity_id: str) -> None:
        self.entities.add(entity_id)


_current_batch: ContextVar[BatchState | None] = ContextVar(
    "bookkit_current_batch", default=None
)


def current_batch() -> BatchState | None:
    """The batch events written right now belong to, or None.

    A ContextVar rather than an attribute on the connection: sqlite3.Connection
    is a C type with no __dict__ and rejects attribute assignment. It is also
    the right scope for the MCP server's async tool wrappers, where a module
    global would bleed between concurrent calls."""
    return _current_batch.get()
```

Replace `transaction` with:

```python
@contextmanager
def transaction(
    conn: sqlite3.Connection, batch: BatchState | None = None
) -> Iterator[None]:
    """A REAL write transaction on the autocommit connection. BEGIN IMMEDIATE
    takes the write lock up front; any exception rolls the whole batch back.
    Without this, conn.commit()/rollback() are silent no-ops.

    `batch` groups every event written inside the block under one id, so one
    writer action becomes one undoable unit. It defaults to None, which is why
    imports/commit.py stays unbatched without needing a special case."""
    token = _current_batch.set(batch)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        _current_batch.reset(token)
```

- [ ] **Step 4: Stamp the event in `base.log_event`**

In `src/bookkit/repo/base.py`, add `from .. import db` to the imports, then replace `log_event`'s body:

```python
def log_event(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    field: str,
    old_value: Any,
    new_value: Any,
    note: str | None = None,
) -> None:
    batch = db.current_batch()
    if batch is not None:
        batch.touch(entity_id)
    conn.execute(
        "INSERT INTO event_log (id, entity_type, entity_id, field, old_value,"
        " new_value, changed_at, note, batch_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_ulid(),
            entity_type,
            entity_id,
            field,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            utc_now(),
            note,
            None if batch is None else batch.batch_id,
        ),
    )
```

If `from .. import db` creates a circular import, import inside the function instead (`from .. import db` on the first line of `log_event`) and note why in a comment.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_db.py -k "batch"`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full gates**

Same three commands as Task 1 Step 7. Expected: all exit 0. Pay attention here — this task changes a function every write in the codebase calls.

- [ ] **Step 7: Commit**

```bash
git add src/bookkit/db.py src/bookkit/repo/base.py tests/test_db.py
git commit -m "batches: ContextVar carrier — db.transaction(batch=) stamps every event it wraps"
```

---

### Task 3: The blast cap

**Files:**
- Modify: `src/bookkit/db.py` (`BatchState.touch` raises past the cap)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `db.BatchState`, `db.BlastRadiusExceeded`, `db.BLAST_CAP` (Task 2)
- Produces: `BatchState.touch` raising `BlastRadiusExceeded` once a *new* entity would exceed `cap`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_blast_cap_rolls_the_whole_batch_back(tmp_path):
    """A cap that raises AFTER writing is worse than no cap — assert the
    database is untouched, not merely that an error came out."""
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    with pytest.raises(db.BlastRadiusExceeded):
        with db.transaction(conn, batch=db.BatchState(batch_id="01B", cap=3)):
            for n in range(5):
                orgs.create(conn, kind="client", name=f"Client {n}")

    assert conn.execute("SELECT COUNT(*) FROM org").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 0


def test_blast_cap_counts_entities_not_events(tmp_path):
    """Three field writes on ONE row is 1 against the cap, not 3."""
    from bookkit import db
    from bookkit.repo import base, orgs

    conn = db.connect(tmp_path / "b.db")
    org = orgs.create(conn, kind="client", name="Acme")
    with db.transaction(conn, batch=db.BatchState(batch_id="01B", cap=1)):
        base.update(conn, "org", org.id, {"website": "https://a.example"})
        base.update(conn, "org", org.id, {"legal_name": "Acme Ltd"})
        base.update(conn, "org", org.id, {"domain": "a.example"})

    assert orgs.get(conn, org.id).domain == "a.example"


def test_blast_cap_defaults_to_25(tmp_path):
    from bookkit import db

    assert db.BLAST_CAP == 25
    assert db.BatchState(batch_id="01B").cap == 25
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_db.py -k "blast_cap"`
Expected: FAIL — `test_blast_cap_rolls_the_whole_batch_back` fails because nothing raises (5 orgs get created).

- [ ] **Step 3: Make `touch` enforce the cap**

Replace `BatchState.touch` in `src/bookkit/db.py`:

```python
    def touch(self, entity_id: str) -> None:
        """Count a distinct entity against the cap. Raising here rides the
        existing ROLLBACK in transaction(), so an over-cap write leaves NOTHING
        behind — the check cannot be forgotten by a future write tool because
        it lives under log_event rather than in any tool."""
        if entity_id in self.entities:
            return
        if len(self.entities) >= self.cap:
            raise BlastRadiusExceeded(
                f"this action would touch more than {self.cap} records; "
                "narrow it and try again"
            )
        self.entities.add(entity_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_db.py -k "blast_cap"`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full gates and commit**

```bash
git add src/bookkit/db.py tests/test_db.py
git commit -m "batches: blast cap — an over-cap batched write rolls back to nothing"
```

---

### Task 4: Net-effect collapse and conflict detection

**Files:**
- Create: `src/bookkit/services/batches.py`
- Test: `tests/test_batches_service.py`

**Interfaces:**
- Consumes: `repo.batches.events_for`, `repo.batches.get_by_ref`, `repo.base.get`, `repo.base.ENTITY_TABLES`, `models.EventBatch`, `models.EventLogEntry`
- Produces:
  - `services.batches.Change` frozen dataclass: `entity_type: str`, `entity_id: str`, `field: str`, `old_value: str | None`, `new_value: str | None`
  - `services.batches.Conflict` frozen dataclass: `change: Change`, `current_value: str | None`
  - `services.batches.RevertPlan` frozen dataclass: `batch: EventBatch`, `creates: list[Change]`, `deletes: list[Change]`, `updates: list[Change]`, `conflicts: list[Conflict]`, plus property `clean: bool` (True when `conflicts` is empty)
  - `services.batches.plan_revert(conn, batch: EventBatch) -> RevertPlan`

- [ ] **Step 1: Write the failing test**

Create `tests/test_batches_service.py`:

```python
"""Batch revert rules — collapse, conflict detection, and the revert itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import base, batches as batches_repo, orgs
from bookkit.services import batches as batches_svc


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "batches.db")
    yield connection
    connection.close()


def _batch(conn, tool="enrich_field", org_id=None):
    return batches_repo.create(
        conn, batch_id=batches_repo.new_batch_id(), source="mcp", tool=tool,
        summary="a test batch", org_id=org_id,
    )


def test_plan_collapses_a_field_written_twice_to_one_net_change(conn):
    """The batch set website a -> b -> c. Reverting must restore a, once, and
    must NOT read the superseded b as a conflict."""
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
        base.update(conn, "org", org.id, {"website": "c"})

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert len(plan.updates) == 1
    assert plan.updates[0].old_value == "a"
    assert plan.updates[0].new_value == "c"


def test_plan_flags_a_field_changed_since_as_a_conflict(conn):
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
    base.update(conn, "org", org.id, {"website": "grant-typed-this"})

    plan = batches_svc.plan_revert(conn, made)
    assert not plan.clean
    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].current_value == "grant-typed-this"
    assert plan.conflicts[0].change.field == "website"


def test_plan_treats_a_created_row_as_a_soft_delete(conn):
    made = _batch(conn)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        org = orgs.create(conn, kind="client", name="Acme")

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert [c.entity_id for c in plan.creates] == [org.id]


def test_a_created_row_dominates_its_own_field_edits(conn):
    """Created THEN edited in the same batch: revert soft-deletes the row and
    must not conflict-check fields on a row it is about to delete."""
    made = _batch(conn)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        org = orgs.create(conn, kind="client", name="Acme")
        base.update(conn, "org", org.id, {"website": "b"})

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert [c.entity_id for c in plan.creates] == [org.id]
    assert plan.updates == []


def test_plan_ignores_source_provenance_events(conn):
    org = orgs.create(conn, kind="client", name="Acme")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.log_event(conn, "org", org.id, "source", None, "mcp")

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert plan.updates == [] and plan.creates == [] and plan.deletes == []


def test_plan_reverts_a_soft_delete_by_undeleting(conn):
    org = orgs.create(conn, kind="client", name="Acme")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.soft_delete(conn, "org", org.id)

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert [c.entity_id for c in plan.deletes] == [org.id]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_batches_service.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'bookkit.services.batches'`

- [ ] **Step 3: Write the collapse and planning half of `services/batches.py`**

```python
"""Batch revert rules. A batch is one writer action; reverting it puts the
book back the way it was — or refuses and says exactly what stops it.

The reverting itself never guesses: if anything in the batch was changed
afterwards by someone else, the whole revert is refused and the conflicts are
reported. That is the house 'surface, don't guess' rule; a half-reverted
record is neither the before nor the after of any single action."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..models import EventBatch
from ..repo import base
from ..repo import batches as batches_repo

# Provenance, not a mutation — the MCP server stamps it after every write.
SKIP_FIELDS = frozenset({"source"})


@dataclass(frozen=True)
class Change:
    entity_type: str
    entity_id: str
    field: str
    old_value: str | None
    new_value: str | None


@dataclass(frozen=True)
class Conflict:
    change: Change
    current_value: str | None


@dataclass(frozen=True)
class RevertPlan:
    batch: EventBatch
    creates: list[Change]
    deletes: list[Change]
    updates: list[Change]
    conflicts: list[Conflict]

    @property
    def clean(self) -> bool:
        return not self.conflicts


def _current_value(
    conn: sqlite3.Connection, entity_type: str, entity_id: str, field: str
) -> str | None:
    table = base.ENTITY_TABLES[entity_type]
    row = conn.execute(
        f"SELECT {field} FROM {table} WHERE id = ?", (entity_id,)  # noqa: S608
    ).fetchone()
    if row is None:
        return None
    return None if row[0] is None else str(row[0])


def plan_revert(conn: sqlite3.Connection, batch: EventBatch) -> RevertPlan:
    """Collapse the batch to its net effect, then check each net change against
    what the record holds now."""
    events = batches_repo.events_for(conn, batch.id)

    created: dict[tuple[str, str], Change] = {}
    deleted: dict[tuple[str, str], Change] = {}
    # (entity_type, entity_id, field) -> [first old_value, last new_value]
    net: dict[tuple[str, str, str], list[str | None]] = {}

    for event in events:
        if event.field in SKIP_FIELDS:
            continue
        entity = (event.entity_type, event.entity_id)
        if event.field == "created":
            created[entity] = Change(
                event.entity_type, event.entity_id, "created", None, None
            )
            continue
        if event.field == "deleted_at":
            deleted[entity] = Change(
                event.entity_type, event.entity_id, "deleted_at",
                event.old_value, event.new_value,
            )
            continue
        key = (event.entity_type, event.entity_id, event.field)
        if key in net:
            net[key][1] = event.new_value          # newest new_value wins
        else:
            net[key] = [event.old_value, event.new_value]

    updates: list[Change] = []
    conflicts: list[Conflict] = []

    for (entity_type, entity_id, field), (old, new) in net.items():
        # A row this batch created is going away wholesale; conflict-checking
        # its fields would refuse reverts that are in fact clean.
        if (entity_type, entity_id) in created:
            continue
        change = Change(entity_type, entity_id, field, old, new)
        current = _current_value(conn, entity_type, entity_id, field)
        if current != new:
            conflicts.append(Conflict(change, current))
        else:
            updates.append(change)

    for entity, change in deleted.items():
        current = _current_value(conn, entity[0], entity[1], "deleted_at")
        if current is None:                        # someone undeleted it since
            conflicts.append(Conflict(change, None))

    return RevertPlan(
        batch=batch,
        creates=list(created.values()),
        deletes=list(deleted.values()),
        updates=updates,
        conflicts=conflicts,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_batches_service.py`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full gates and commit**

```bash
git add src/bookkit/services/batches.py tests/test_batches_service.py
git commit -m "batches: net-effect collapse and conflict detection"
```

---

### Task 5: Applying the revert

**Files:**
- Modify: `src/bookkit/services/batches.py`
- Test: `tests/test_batches_service.py`

**Interfaces:**
- Consumes: `plan_revert`, `RevertPlan`, `Change`, `Conflict` (Task 4); `repo.base.update`, `repo.base.soft_delete`, `repo.base.undelete`; `repo.batches.mark_reverted`, `repo.batches.get_by_ref`
- Produces:
  - `services.batches.RevertResult` frozen dataclass: `batch: EventBatch`, `reverted: list[Change]`, `refused: list[Conflict]`, `applied: bool`
  - `services.batches.revert(conn, ref: str, now: str, force: bool = False) -> RevertResult` — raises `KeyError` on an unknown ref, `AlreadyReverted` on a second revert
  - `services.batches.AlreadyReverted(Exception)`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_batches_service.py`:

```python
NOW = "2026-08-13T18:00:00Z"


def test_revert_restores_field_values_and_stamps_the_batch(conn):
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})

    result = batches_svc.revert(conn, made.ref, now=NOW)
    assert result.applied
    assert orgs.get(conn, org.id).website == "a"
    assert batches_repo.get_by_ref(conn, made.ref).reverted_at == NOW


def test_revert_soft_deletes_what_the_batch_created(conn):
    made = _batch(conn)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        org = orgs.create(conn, kind="client", name="Acme")

    batches_svc.revert(conn, made.ref, now=NOW)
    with pytest.raises(KeyError):
        orgs.get(conn, org.id)


def test_revert_undeletes_what_the_batch_deleted(conn):
    org = orgs.create(conn, kind="client", name="Acme")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.soft_delete(conn, "org", org.id)

    batches_svc.revert(conn, made.ref, now=NOW)
    assert orgs.get(conn, org.id).name == "Acme"


def test_a_conflicted_revert_writes_absolutely_nothing(conn):
    """All-or-nothing: assert the DB is untouched, not merely that it refused."""
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
        base.update(conn, "org", org.id, {"legal_name": "Acme Ltd"})
    base.update(conn, "org", org.id, {"website": "grant-typed-this"})

    result = batches_svc.revert(conn, made.ref, now=NOW)
    assert not result.applied
    assert len(result.refused) == 1
    got = orgs.get(conn, org.id)
    assert got.website == "grant-typed-this"      # untouched
    assert got.legal_name == "Acme Ltd"           # the clean one NOT reverted
    assert batches_repo.get_by_ref(conn, made.ref).reverted_at is None


def test_force_reverts_the_clean_changes_and_reports_the_rest(conn):
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
        base.update(conn, "org", org.id, {"legal_name": "Acme Ltd"})
    base.update(conn, "org", org.id, {"website": "grant-typed-this"})

    result = batches_svc.revert(conn, made.ref, now=NOW, force=True)
    assert result.applied
    assert len(result.reverted) == 1 and len(result.refused) == 1
    got = orgs.get(conn, org.id)
    assert got.website == "grant-typed-this"      # conflicted, left alone
    assert got.legal_name is None                 # clean, reverted


def test_a_second_revert_is_refused(conn):
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})

    batches_svc.revert(conn, made.ref, now=NOW)
    with pytest.raises(batches_svc.AlreadyReverted):
        batches_svc.revert(conn, made.ref, now=NOW)


def test_revert_is_not_itself_undoable_or_batched(conn):
    """The revert's own writes carry note='revert' and no batch_id, so u skips
    them exactly as it skips undo's bookkeeping."""
    from bookkit.repo import events

    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})

    batches_svc.revert(conn, made.ref, now=NOW)
    rows = conn.execute(
        "SELECT batch_id FROM event_log WHERE note = 'revert'"
    ).fetchall()
    assert rows and all(r[0] is None for r in rows)
    last = events.last_mutation(conn)
    assert last is None or last.note != "revert"


def test_revert_raises_on_an_unknown_ref(conn):
    with pytest.raises(KeyError):
        batches_svc.revert(conn, "MCP-9999", now=NOW)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_batches_service.py -k "revert or force or second"`
Expected: FAIL with `AttributeError: module 'bookkit.services.batches' has no attribute 'revert'`

- [ ] **Step 3: Add `revert` to `services/batches.py`**

Append:

```python
class AlreadyReverted(Exception):
    """This batch has been reverted once already."""


@dataclass(frozen=True)
class RevertResult:
    batch: EventBatch
    reverted: list[Change]
    refused: list[Conflict]
    applied: bool


def revert(
    conn: sqlite3.Connection, ref: str, now: str, force: bool = False
) -> RevertResult:
    """Put the book back the way it was before this batch.

    Refuses outright when anything in the batch was changed since, unless
    `force` — then the clean changes revert and the conflicted ones are
    reported untouched. `now` is a parameter, never the wall clock.

    The revert's own writes carry note='revert' and NO batch_id, so a revert
    cannot itself be batch-reverted and `u` skips it the way it skips undo."""
    from .. import db

    batch = batches_repo.get_by_ref(conn, ref)     # KeyError on unknown
    if batch.reverted_at is not None:
        raise AlreadyReverted(f"{ref} was reverted at {batch.reverted_at}")

    plan = plan_revert(conn, batch)
    if plan.conflicts and not force:
        return RevertResult(batch, reverted=[], refused=plan.conflicts,
                            applied=False)

    with db.transaction(conn):                     # deliberately unbatched
        for change in plan.updates:
            base.update(
                conn, change.entity_type, change.entity_id,
                {change.field: change.old_value}, note="revert",
            )
        for change in plan.creates:
            if base.get(conn, change.entity_type, change.entity_id) is not None:
                base.soft_delete(
                    conn, change.entity_type, change.entity_id, note="revert"
                )
        for change in plan.deletes:
            base.undelete(conn, change.entity_type, change.entity_id)
        batches_repo.mark_reverted(conn, batch.id, now)

    return RevertResult(
        batch=batch,
        reverted=[*plan.updates, *plan.creates, *plan.deletes],
        refused=plan.conflicts,
        applied=True,
    )
```

- [ ] **Step 4: Check `base.undelete` accepts a note**

Run: `grep -n "def undelete" -A 10 src/bookkit/repo/base.py`

`undelete` currently hardcodes `note="undelete"`. That is fine and is what `last_mutation` already excludes — leave it. If the test `test_revert_is_not_itself_undoable_or_batched` fails only on the undelete path, that is expected and correct; adjust the assertion to allow `note` in `{"revert", "undelete"}`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_batches_service.py`
Expected: PASS (14 tests)

- [ ] **Step 6: Run the full gates and commit**

```bash
git add src/bookkit/services/batches.py tests/test_batches_service.py
git commit -m "batches: all-or-nothing revert with a force escape"
```

---

### Task 6: Batch every MCP write site

**Files:**
- Modify: `src/bookkit/mcpserver.py` (8 write functions + a new `_open_batch` helper)
- Test: `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `db.BatchState`, `repo.batches.create`, `repo.batches.new_batch_id`
- Produces:
  - `mcpserver._open_batch(conn, *, tool: str, summary: str, org_id: str | None = None)` — a context manager yielding the `EventBatch`
  - Every write tool's return dict gains `"batch": <ref>`

The eight sites (verify line numbers before editing; they move as you go):
`_log_activity`, `_activity_delete`, `_task_create`, `_task_complete`, `_client_create`, `_enrich_field`, `_request_item_received`, `_request_create`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcpserver.py`:

```python
def test_every_write_tool_returns_a_batch_ref(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    logged = mcpserver._log_activity(rw, "Acme", "a note")
    assert logged["batch"].startswith("MCP-")

    made = mcpserver._task_create(rw, "chase the quote", client="Acme")
    assert made["batch"].startswith("MCP-")
    assert made["batch"] != logged["batch"]


def test_one_mcp_call_is_one_batch(server_db):
    """log_activity writes an interaction AND a follow-up task. Both must land
    in the same batch, or reverting it would unwind half."""
    from bookkit.repo import batches as batches_repo

    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._log_activity(rw, "Acme", "spoke to Ann", follow_up="friday")
    batch = batches_repo.get_by_ref(rw, out["batch"])
    touched = {
        (e.entity_type, e.entity_id)
        for e in batches_repo.events_for(rw, batch.id)
    }
    assert {t for t, _ in touched} == {"interaction", "task"}


def test_a_batch_records_the_tool_and_the_account(server_db):
    from bookkit.repo import batches as batches_repo

    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._enrich_field(rw, "Acme", "website", "https://acme.example")
    batch = batches_repo.get_by_ref(rw, out["batch"])
    assert batch.tool == "enrich_field"
    assert batch.org_id == org.id
    assert batch.source == "mcp"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_mcpserver.py -k "batch"`
Expected: FAIL with `KeyError: 'batch'`

- [ ] **Step 3: Add the helper to `mcpserver.py`**

Next to `_provenance`:

```python
@contextmanager
def _open_batch(
    conn: sqlite3.Connection, *, tool: str, summary: str, org_id: str | None = None
) -> Iterator[EventBatch]:
    """One MCP call, one undo unit. Opens the transaction AND the batch, so
    every event written inside is grouped and revertible together."""
    from .repo import batches as batches_repo

    batch_id = batches_repo.new_batch_id()
    with db.transaction(conn, batch=db.BatchState(batch_id=batch_id)):
        yield batches_repo.create(
            conn, batch_id=batch_id, source="mcp", tool=tool,
            summary=summary, org_id=org_id,
        )
```

Add `from contextlib import contextmanager`, `from collections.abc import Iterator`, and `from .models import EventBatch` to the module imports if not present.

- [ ] **Step 4: Convert each write site**

For each of the eight, replace `with db.transaction(conn):` with `_open_batch`, and add the ref to the return. Worked example for `_log_activity`:

```python
    with _open_batch(
        conn, tool="log_activity", org_id=org.id,
        summary=f"logged a note on {org.name}"
             + (" with a follow-up task" if due else ""),
    ) as batch:
        interaction = interactions.log(
            conn, org.id, type="note",
            occurred_on=date.today().isoformat(),
            subject=note[:80], body=note,
        )
        _provenance(conn, "interaction", interaction.id)
        task = None
        if due:
            task = tasks_repo.create(
                conn, f"Follow up: {note[:60]}", org_id=org.id, due_on=due)
            _provenance(conn, "task", task.id)
    return {"org_id": org.id, "interaction_ref": interaction.id,
            "follow_up_task": task.id if task else None,
            "batch": batch.ref}
```

Summaries for the rest — keep them short and human, they are what the TUI shows:
- `_activity_delete`: `f"deleted an activity on {org name or '—'}"` (the interaction's `subject` is available; use `f"deleted activity: {interaction.subject}"`)
- `_task_create`: `f"created task: {title}"`
- `_task_complete`: `f"completed task: {task.title}"`
- `_client_create`: `f"created client {name}"`
- `_enrich_field`: `f"set {field} on {org.name}"`
- `_request_item_received`: `f"received an item on {request.ref}"`
- `_request_create`: `f"created request {request.ref} for {org.name}"`

`_client_create` creates the org inside the batch, so its `org_id` is not known when the batch opens. Pass `org_id=None` and leave a comment saying why.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_mcpserver.py`
Expected: PASS — the whole file, since existing tests assert on these same return dicts.

- [ ] **Step 6: Run the full gates and commit**

```bash
git add src/bookkit/mcpserver.py tests/test_mcpserver.py
git commit -m "mcp: every write is a batch — one call, one undo unit"
```

---

### Task 7: The `list_batches` and `revert_batch` MCP tools

**Files:**
- Modify: `src/bookkit/mcpserver.py`
- Test: `tests/test_mcpserver.py`, `tests/test_mcp_roundtrip.py`

**Interfaces:**
- Consumes: `services.batches.revert`, `services.batches.AlreadyReverted`, `repo.batches.recent`
- Produces:
  - `mcpserver._list_batches(conn, today: date, limit: int = 20, days: int = 14) -> list[dict[str, Any]]`
  - `mcpserver._revert_batch(conn, ref: str, now: str, force: bool = False) -> dict[str, Any]`
  - Registered tools `list_batches(limit: int = 20)` and `revert_batch(ref: str, force: bool = False)`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcpserver.py`:

```python
def test_list_batches_shows_recent_work_newest_first(server_db):
    from datetime import date

    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    first = mcpserver._log_activity(rw, "Acme", "one")
    second = mcpserver._task_create(rw, "two", client="Acme")

    out = mcpserver._list_batches(rw, today=date(2026, 8, 13))
    refs = [row["ref"] for row in out]
    assert refs == [second["batch"], first["batch"]]
    assert out[0]["tool"] == "task_create"
    assert out[0]["reverted"] is False


def test_revert_batch_puts_the_value_back(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._enrich_field(rw, "Acme", "website", "https://acme.example")
    got = mcpserver._revert_batch(rw, out["batch"], now="2026-08-13T18:00:00Z")
    assert got["applied"] is True
    assert orgs.get(rw, org.id).website is None


def test_revert_batch_refuses_and_explains_a_conflict(server_db):
    from bookkit.repo import base

    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._enrich_field(rw, "Acme", "website", "https://acme.example")
    base.update(rw, "org", org.id, {"website": "https://grant-typed.example"})

    got = mcpserver._revert_batch(rw, out["batch"], now="2026-08-13T18:00:00Z")
    assert got["applied"] is False
    assert got["refused"][0]["field"] == "website"
    assert got["refused"][0]["current"] == "https://grant-typed.example"
    assert orgs.get(rw, org.id).website == "https://grant-typed.example"


def test_batch_tools_are_registered(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"list_batches", "revert_batch"} <= names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_mcpserver.py -k "list_batches or revert_batch or batch_tools"`
Expected: FAIL with `AttributeError: module 'bookkit.mcpserver' has no attribute '_list_batches'`

- [ ] **Step 3: Implement the two functions**

```python
def _list_batches(
    conn: sqlite3.Connection, today: date, limit: int = 20, days: int = 14
) -> list[dict[str, Any]]:
    """Recent batched writes, newest first — what this server changed and
    whether it has been put back."""
    from .repo import batches as batches_repo
    from .repo import orgs as orgs_repo

    since = (today - timedelta(days=days)).isoformat()
    out = []
    for batch in batches_repo.recent(conn, since=since, limit=limit):
        account = None
        if batch.org_id:
            try:
                account = orgs_repo.get(conn, batch.org_id).name
            except KeyError:
                account = "(deleted account)"
        out.append({
            "ref": batch.ref, "tool": batch.tool, "summary": batch.summary,
            "account": account, "at": batch.created_at,
            "reverted": batch.reverted_at is not None,
        })
    return out


def _revert_batch(
    conn: sqlite3.Connection, ref: str, now: str, force: bool = False
) -> dict[str, Any]:
    """Undo one batched write. Refuses outright if anything in it was changed
    since, listing what blocks it — never a partial write unless forced."""
    from .services import batches as batches_svc

    result = batches_svc.revert(conn, ref, now=now, force=force)
    return {
        "ref": result.batch.ref,
        "applied": result.applied,
        "reverted": [
            {"entity": c.entity_type, "id": c.entity_id, "field": c.field}
            for c in result.reverted
        ],
        "refused": [
            {"entity": c.change.entity_type, "id": c.change.entity_id,
             "field": c.change.field, "batch_set": c.change.new_value,
             "current": c.current_value}
            for c in result.refused
        ],
    }
```

Add `from datetime import timedelta` if absent.

- [ ] **Step 4: Register the tools**

In `_register_write_tools`:

```python
    @server.tool()
    async def list_batches(limit: int = 20) -> list[dict[str, Any]]:
        """Recent changes THIS server made, newest first, each with the `ref`
        that `revert_batch` takes. Covers the last 14 days. Use it to show the
        user what you changed, or to find a change that needs putting back."""
        return _list_batches(rw, date.today(), limit=limit)

    @server.tool()
    async def revert_batch(ref: str, force: bool = False) -> dict[str, Any]:
        """Undo one batched change by its `ref` (from `list_batches` or from
        any write tool's return). Refuses and lists the blockers if the user
        has changed any of those fields since — pass force=true ONLY if the
        user has said to revert the rest anyway. `applied: false` means nothing
        was written."""
        return _revert_batch(rw, ref, now=utc_now(), force=force)
```

Import `utc_now` at module level if not already present.

- [ ] **Step 5: Add the protocol round-trip test**

In `tests/test_mcp_roundtrip.py`, follow the existing pattern to call `list_batches` over real stdio and assert a list comes back. This harness caught the thread-dispatch bug last phase that unit tests could not see — do not skip it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_mcpserver.py tests/test_mcp_roundtrip.py`
Expected: PASS

- [ ] **Step 7: Run the full gates and commit**

```bash
git add src/bookkit/mcpserver.py tests/test_mcpserver.py tests/test_mcp_roundtrip.py
git commit -m "mcp: list_batches and revert_batch — audit and reverse this server's work"
```

---

### Task 8: The MCP CHANGES tree section

**Files:**
- Modify: `src/bookkit/tui/screens/navigator.py` (tree build ~line 194-208; pane render ~line 470-560)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `repo.batches.recent`, `repo.orgs.get`
- Produces: a tree node `data=("batches", None)` whose pane renders a `ListTable` with id `#batches-table`, rows keyed `batch:{id}`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tui.py`:

```python
async def test_navigator_lists_recent_mcp_batches(seeded_db: Path) -> None:
    """MCP CHANGES is its own tree section, NOT an attention leaf: attention
    means 'act on this' and carries the 120-day window; this is an audit list
    where most rows need no action."""
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website on Acme", org_id=org.id,
    )

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()

        table = app.screen.query_one("#batches-table", ListTable)
        row = [str(c) for c in table.get_row(f"batch:{made.id}")]
        assert made.ref in row
        assert "enrich_field" in row


async def test_reverted_batches_render_as_reverted(seeded_db: Path) -> None:
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="task_create", summary="made a task", org_id=None,
    )
    batches_repo.mark_reverted(app.conn, made.id, "2026-08-13T18:00:00Z")

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()

        table = app.screen.query_one("#batches-table", ListTable)
        row = [str(c) for c in table.get_row(f"batch:{made.id}")]
        assert any("reverted" in cell for cell in row)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_tui.py -k "mcp_batches or reverted_batches"`
Expected: FAIL — no `#batches-table` widget.

- [ ] **Step 3: Add the tree section**

In `refresh_data`, after the ACCOUNTS/MARKETS sections are added, add:

```python
        recent_batches = batches_repo.recent(
            conn, since=(today - timedelta(days=14)).isoformat()
        )
        self._batches = recent_batches
        if recent_batches:
            # Its own section, deliberately NOT an attention leaf: attention
            # means "act on this" and carries the 120-day bucket window plus
            # the overdue-never-falls-off rule. This is an audit list.
            tree.root.add_leaf(
                _section("MCP CHANGES", len(recent_batches)),
                data=("batches", None),
            )
```

Import `batches as batches_repo` from `...repo` and `timedelta` from `datetime`. Initialise `self._batches: list[EventBatch] = []` wherever the other pane state is initialised.

- [ ] **Step 4: Render the pane**

In the pane-rendering branch chain (beside the `elif group == "requests":` block), add a branch for `("batches", None)`:

```python
        elif kind == "batches":
            table.add_columns("ref", "when", "account", "tool", "what", "state")
            for batch in self._batches:
                account = dash()
                if batch.org_id:
                    try:
                        account = orgs.get(conn, batch.org_id).name
                    except KeyError:
                        account = Text("(deleted account)", style=theme.DIM)
                state = (
                    Text("reverted", style=theme.DIM)
                    if batch.reverted_at else Text("live", style=theme.GREEN)
                )
                table.add_row(
                    batch.ref, batch.created_at[11:16], account, batch.tool,
                    Text(batch.summary, style=theme.DIM), state,
                    key=f"batch:{batch.id}",
                )
```

Match the exact dispatch shape the surrounding code uses (`data[0]`, `group`, or `kind` — read it before writing).

- [ ] **Step 5: Add the field-level detail view (`enter`)**

The spec requires `enter` to open the batch's before→after. Write the test first:

```python
async def test_enter_on_a_batch_shows_field_level_before_and_after(seeded_db: Path) -> None:
    from bookkit import db as db_mod
    from bookkit.repo import base, batches as batches_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website", org_id=org.id,
    )
    with db_mod.transaction(app.conn, batch=db_mod.BatchState(batch_id=made.id)):
        base.update(app.conn, "org", org.id, {"website": "https://mcp.example"})

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()
        table = app.screen.query_one("#batches-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"batch:{made.id}"))
        await pilot.press("enter")
        await pilot.pause()

        rendered = " ".join(str(w.renderable) for w in app.screen.query(Static))
        assert "website" in rendered
        assert "https://mcp.example" in rendered
```

Implement it as a `BatchDetail(ModalScreen)` that renders `plan_revert`'s
`updates`, `creates` and `deletes` as `field: old → new` lines, plus any
conflicts marked. Route to it from the existing `on_data_table_row_selected`
handler when `self._current == ("batches", None)` — read that handler before
editing; the Navigator already dispatches row-selected per section.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_tui.py -k "mcp_batches or reverted_batches or before_and_after"`
Expected: PASS

- [ ] **Step 7: Run the full gates and commit**

```bash
git add src/bookkit/tui/screens/navigator.py tests/test_tui.py
git commit -m "tui: MCP CHANGES section — see what the server changed"
```

---

### Task 9: Reverting from the TUI with `R`

**Files:**
- Modify: `src/bookkit/tui/screens/navigator.py` (a `ConfirmRevertBatch` modal, an `R` binding, `action_revert_batch`)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `services.batches.revert`, `services.batches.plan_revert`, `repo.batches.get`
- Produces: `NavigatorScreen.action_revert_batch`, modal `ConfirmRevertBatch`

- [ ] **Step 1: Write the failing test**

```python
async def test_R_reverts_the_highlighted_batch(seeded_db: Path) -> None:
    from bookkit import db as db_mod
    from bookkit.repo import base, batches as batches_repo

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="enrich_field", summary="set website", org_id=org.id,
    )
    with db_mod.transaction(app.conn, batch=db_mod.BatchState(batch_id=made.id)):
        base.update(app.conn, "org", org.id, {"website": "https://mcp.example"})

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()

        table = app.screen.query_one("#batches-table", ListTable)
        table.focus()
        table.move_cursor(row=table.get_row_index(f"batch:{made.id}"))
        await pilot.pause()

        await pilot.press("R")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        await pilot.press("y")
        await pilot.pause()

        assert orgs.get(app.conn, org.id).website is None
        assert batches_repo.get(app.conn, made.id).reverted_at is not None


async def test_R_needs_the_table_focused(seeded_db: Path) -> None:
    from bookkit.repo import batches as batches_repo

    app = BookkitApp(seeded_db)
    made = batches_repo.create(
        app.conn, batch_id=batches_repo.new_batch_id(), source="mcp",
        tool="task_create", summary="s", org_id=None,
    )
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav._current = ("batches", None)
        nav._render_pane()
        await pilot.pause()
        app.screen.set_focus(None)
        await pilot.press("R")
        await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)
        assert batches_repo.get(app.conn, made.id).reverted_at is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_tui.py -k "R_reverts or R_needs"`
Expected: FAIL — no modal appears.

- [ ] **Step 3: Add the modal**

```python
class ConfirmRevertBatch(ModalScreen):
    """One look before putting a batched change back. On a conflict this
    becomes the refusal list instead of a yes/no — force is an explicit second
    choice, never a default."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y,enter", "accept", "Yes"),
        Binding("f", "force", "Force", show=False),
    ]

    def __init__(self, batch, plan) -> None:
        super().__init__()
        self.batch = batch
        self.plan = plan

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("REVERT MCP CHANGE", classes="modal-title")
            yield Static(f"{self.batch.ref}  {self.batch.tool}\n{self.batch.summary}")
            if self.plan.conflicts:
                lines = "\n".join(
                    f"  {c.change.field}: set {c.change.new_value!r}, "
                    f"now {c.current_value!r}"
                    for c in self.plan.conflicts
                )
                yield Static(
                    f"[{theme.AMBER}]BLOCKED[/] — changed since:\n{lines}"
                )
                yield Static(
                    f"{len(self.plan.updates)} other change(s) would revert cleanly",
                    classes="hint",
                )
                yield Static("f force the rest · esc cancel", classes="hint")
            else:
                yield Static("esc cancel · y / enter revert", classes="hint")

    def action_accept(self) -> None:
        self.dismiss(None if self.plan.conflicts else "revert")

    def action_force(self) -> None:
        self.dismiss("force" if self.plan.conflicts else None)

    def action_decline(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Add the binding and the action**

Add to `NavigatorScreen.BINDINGS`:

```python
        # R, not r — r is renew. Destructive/reversing actions take shift,
        # the same call as D on the account screen.
        Binding("R", "revert_batch", "Revert MCP change", show=False),
```

```python
    def action_revert_batch(self) -> None:
        """R on the MCP CHANGES table: put one batched change back."""
        from ...services import batches as batches_svc

        if self._current != ("batches", None):
            return
        table = self.query_one("#batches-table", ListTable)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(
            Coordinate(table.cursor_row, 0)
        ).row_key.value
        if not key:
            return
        try:
            batch = batches_repo.get(self.app.conn, key.removeprefix("batch:"))
        except KeyError:
            return
        if batch.reverted_at is not None:
            self.notify("already reverted")
            return
        plan = batches_svc.plan_revert(self.app.conn, batch)
        self.app.push_screen(
            ConfirmRevertBatch(batch, plan),
            lambda choice, ref=batch.ref: self._revert_batch(ref, choice),
        )

    def _revert_batch(self, ref: str, choice: str | None) -> None:
        from ...services import batches as batches_svc
        from ...util import utc_now

        if choice is None:
            return
        result = batches_svc.revert(
            self.app.conn, ref, now=utc_now(), force=(choice == "force")
        )
        if result.applied:
            self.notify(f"{ref} reverted — {len(result.reverted)} change(s)")
        else:
            self.notify(f"{ref} refused — {len(result.refused)} conflict(s)")
        self.refresh_data()
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest -q tests/test_tui.py -k "R_reverts or R_needs"`
Expected: PASS

- [ ] **Step 6: Run the full gates and commit**

```bash
git add src/bookkit/tui/screens/navigator.py tests/test_tui.py
git commit -m "tui: R reverts a batched MCP change, refusing on conflict"
```

---

### Task 10: Discoverability and documentation

**Files:**
- Modify: `src/bookkit/tui/screens/help.py`
- Modify: `README.md` (the MCP tool list, if one exists — check first)
- Modify: `CLAUDE.md` (a line about batches under the architecture notes)
- Test: `tests/test_tui.py`

- [ ] **Step 1: Add the help entry**

In the `[b]navigator (home)[/b]` block of `src/bookkit/tui/screens/help.py`:

```
  R  revert an MCP change (MCP CHANGES section) — refuses if you have
     edited any of the same fields since; f then forces the rest
```

- [ ] **Step 2: Add the CLAUDE.md architecture line**

Under the architecture bullets:

```markdown
- One MCP call is ONE undo unit: db.transaction(batch=) stamps every event
  with a batch_id and services/batches.py reverts a batch all-or-nothing,
  refusing when a field changed since. `u` is still single-step and
  field-granular for TUI writes — batching is per db.transaction, and
  imports/commit.py stays unbatched on purpose.
```

- [ ] **Step 3: Write a help-screen test**

```python
async def test_help_documents_the_revert_key(seeded_db: Path) -> None:
    app = BookkitApp(seeded_db)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        rendered = " ".join(
            str(w.renderable) for w in app.screen.query(Static)
        )
        assert "revert an MCP change" in rendered
```

Adjust the widget query to match how `help.py` actually composes (read it first — it may use one `Static` or many).

- [ ] **Step 4: Run the full gates**

Expected: all three exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/bookkit/tui/screens/help.py CLAUDE.md README.md tests/test_tui.py
git commit -m "docs: R and MCP batching in help, README and CLAUDE.md"
```

---

## Final phase review

After Task 10, before declaring done:

1. **Run all three gates one more time on the branch tip**, output to the scratchpad, exit codes checked directly.
2. **Verify the migration is genuinely additive** — open a copy of a pre-011 database, run `db.connect` on it, confirm it migrates without error and existing `event_log` rows read back with `batch_id IS NULL`.
3. **Confirm imports stayed unbatched** — run an import through `imports/commit.py` and assert every event it wrote has `batch_id IS NULL`. This is Grant's decision 1 and nothing in the plan tests it end-to-end.
4. **Live-probe the MCP server** over real stdio: `log_activity`, then `list_batches`, then `revert_batch`, and confirm the interaction is gone.
5. Open items for Grant: the **blast cap of 25** (REVIEW POINT), and whether MCP CHANGES should have been an attention leaf after all once he has seen it in place.
