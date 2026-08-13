# RFI Tracking Implementation Plan (phases 1–3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track the questions and document requests a client owes — batched into requests, chased from the Navigator, edited from a datasheet tab.

**Architecture:** Two new tables (`rfi_request` → `rfi_item`) behind a new `repo/rfi.py`; business rules (derived open/closed, the 120-day chase feed) in `services/rfi.py`; TUI surfaces reusing the existing Navigator attention pattern, the `InlineTable` datasheet pattern, and `entity_actions` shared flows. The client export sheet is **phase 4 and NOT in this plan** — it is blocked on towerkit's multi-sheet API.

**Tech Stack:** Python 3.13, SQLite (autocommit + `db.transaction`), pydantic row models, Textual 8.x TUI, pytest/mypy/ruff.

**Spec:** `docs/superpowers/specs/2026-08-13-rfi-tracking-design.md`

## Global Constraints

- **`repo/` owns every SQL query.** `services/` and `tui/` contain ZERO raw SQL — `tests/test_conventions.py` fails the build otherwise.
- **Gates before every commit:** `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. When chaining in shell, never pipe pytest output before the `&&` — redirect to a file, gate on the command, tail the file after.
- **Migrations are additive-only.** 010 creates two tables; it must not `ALTER` or backfill anything existing.
- **Writes go through `base.insert` / `base.update` / `base.soft_delete`** so `event_log` records them and `u` undoes them. Never write raw INSERT/UPDATE to these tables.
- **Status vocabularies are tuples in `models.py`** (the `TEAM_ROLES` / `NEED_STATUSES` pattern), rendered via `theme.status_text`. Not enums in pickers, not a lookup table.
- **Dates are ISO strings** in columns; date parsing goes through `Field(kind="date")`, which uses the towerkit MDY fast path. `today` is always a parameter in services, never `date.today()` inside a query function.
- **Attention window is 120 days, bucket-aligned; overdue never falls off.**
- **Effective due date of an item** = `item.due_on` if set, else `request.due_on`. One rule, used everywhere.
- **Request open/closed is DERIVED** from item statuses. Never add a stored status column to `rfi_request`.

---

### Task 1: Schema, models, and ref allocation

**Files:**
- Create: `migrations/010_rfi.sql`
- Modify: `src/bookkit/ids.py:18` (add `RFI_REF`)
- Modify: `src/bookkit/models.py` (add `RfiRequest`, `RfiItem`, status tuples — put them after `ProjectNeed` / `NEED_STATUSES`, around line 240)
- Modify: `src/bookkit/repo/base.py:17-31` (`ENTITY_TABLES`)
- Test: `tests/test_rfi_repo.py`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `rfi_request` / `rfi_item`; `models.RfiRequest`, `models.RfiItem`, `models.RFI_ITEM_STATUSES = ("outstanding", "received", "waived")`, `models.RFI_ITEM_KINDS = ("question", "document")`; `ids.RFI_REF = "RFI"`; `ENTITY_TABLES` keys `"rfi_request"` and `"rfi_item"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rfi_repo.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "rfi.db")
    yield connection
    connection.close()


def test_migration_creates_rfi_tables(conn) -> None:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"rfi_request", "rfi_item"} <= tables


def test_migration_is_idempotent(conn) -> None:
    assert db.pending_migrations(conn) == []


def test_request_scope_is_exclusive(conn) -> None:
    """A request points at a placement OR a project, never both."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rfi_request "
            "(id, ref, org_id, placement_id, project_id, title, requested_on,"
            " created_at, updated_at) "
            "VALUES ('x','RFI-9999','o','p','pr','t','2026-08-13','n','n')"
        )


def test_models_expose_rfi_vocabularies() -> None:
    from bookkit.models import RFI_ITEM_KINDS, RFI_ITEM_STATUSES

    assert RFI_ITEM_STATUSES == ("outstanding", "received", "waived")
    assert RFI_ITEM_KINDS == ("question", "document")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rfi_repo.py -v`
Expected: FAIL — `rfi_request` not in tables; `ImportError` on `RFI_ITEM_STATUSES`.

- [ ] **Step 3: Write the migration**

Create `migrations/010_rfi.sql`:

```sql
-- Information requests (RFIs): batches of questions and document requests
-- a client owes us. Additive only: two new tables, nothing existing touched.
CREATE TABLE rfi_request (
    id             TEXT PRIMARY KEY,
    ref            TEXT NOT NULL UNIQUE,
    org_id         TEXT NOT NULL REFERENCES org (id),
    placement_id   TEXT REFERENCES placement (id),
    project_id     TEXT REFERENCES project (id),
    market_org_id  TEXT REFERENCES org (id),
    title          TEXT NOT NULL,
    requested_on   TEXT NOT NULL,
    due_on         TEXT,
    notes          TEXT,
    cancelled_at   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    deleted_at     TEXT,
    CHECK (placement_id IS NULL OR project_id IS NULL)
);
CREATE INDEX idx_rfi_request_org ON rfi_request (org_id);
CREATE INDEX idx_rfi_request_due ON rfi_request (due_on);

CREATE TABLE rfi_item (
    id           TEXT PRIMARY KEY,
    request_id   TEXT NOT NULL REFERENCES rfi_request (id),
    kind         TEXT NOT NULL DEFAULT 'question',
    prompt       TEXT NOT NULL,
    detail       TEXT,
    category     TEXT,
    due_on       TEXT,
    response     TEXT,
    received_on  TEXT,
    status       TEXT NOT NULL DEFAULT 'outstanding',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    deleted_at   TEXT
);
CREATE INDEX idx_rfi_item_request ON rfi_item (request_id);
CREATE INDEX idx_rfi_item_status ON rfi_item (status);
```

- [ ] **Step 4: Add the models**

In `src/bookkit/models.py`, after `NEED_STATUSES`:

```python
class RfiRequest(Row):
    id: str
    ref: str
    org_id: str
    placement_id: str | None = None
    project_id: str | None = None
    market_org_id: str | None = None
    title: str
    requested_on: str
    due_on: str | None = None
    notes: str | None = None
    cancelled_at: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class RfiItem(Row):
    id: str
    request_id: str
    kind: str = "question"
    prompt: str
    detail: str | None = None
    category: str | None = None
    due_on: str | None = None
    response: str | None = None
    received_on: str | None = None
    status: str = "outstanding"
    created_at: str
    updated_at: str
    deleted_at: str | None = None


RFI_ITEM_STATUSES = ("outstanding", "received", "waived")
RFI_ITEM_KINDS = ("question", "document")
```

- [ ] **Step 5: Register the ref prefix and the entity tables**

In `src/bookkit/ids.py`, after `PROJECT_REF = "PRJ"`:

```python
RFI_REF = "RFI"
```

In `src/bookkit/repo/base.py`, add two entries to `ENTITY_TABLES` after `"project_need": "project_need",`:

```python
    "rfi_request": "rfi_request",
    "rfi_item": "rfi_item",
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_rfi_repo.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add migrations/010_rfi.sql src/bookkit/ids.py src/bookkit/models.py src/bookkit/repo/base.py tests/test_rfi_repo.py
git commit -m "rfi: schema, row models, and ref allocation

Two additive tables — rfi_request holds the scope (one placement OR one
project, enforced by CHECK) and the optional market that asked;
rfi_item holds the questions and document requests. No stored status on
the request: open/closed is derived from its items, so it cannot drift."
```

---

### Task 2: Request CRUD in `repo/rfi.py`

**Files:**
- Create: `src/bookkit/repo/rfi.py`
- Test: `tests/test_rfi_repo.py` (append)

**Interfaces:**
- Consumes: `models.RfiRequest`, `ids.RFI_REF`, `ENTITY_TABLES["rfi_request"]` from Task 1.
- Produces:
  - `create_request(conn: sqlite3.Connection, org_id: str, title: str, requested_on: str, **fields: Any) -> RfiRequest`
  - `get_request(conn: sqlite3.Connection, request_id: str) -> RfiRequest` (raises `KeyError`)
  - `requests_for_org(conn: sqlite3.Connection, org_id: str) -> list[RfiRequest]`
  - `update_request(conn: sqlite3.Connection, request_id: str, note: str | None = None, **changes: Any) -> RfiRequest`
  - `delete_request(conn: sqlite3.Connection, request_id: str) -> None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rfi_repo.py`:

```python
def _org(conn) -> str:
    from bookkit.repo import orgs

    return orgs.create(conn, name="Endeavour Energy", kind="client").id


def test_create_request_allocates_a_ref(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "Sompo — property questions", "2026-08-05")
    assert req.ref.startswith("RFI-")
    assert req.title == "Sompo — property questions"
    assert req.requested_on == "2026-08-05"
    assert rfi.get_request(conn, req.id).id == req.id


def test_requests_for_org_excludes_deleted(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    keep = rfi.create_request(conn, org_id, "keep", "2026-08-05")
    drop = rfi.create_request(conn, org_id, "drop", "2026-08-05")
    rfi.delete_request(conn, drop.id)
    assert [r.id for r in rfi.requests_for_org(conn, org_id)] == [keep.id]


def test_update_request_is_event_logged(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "old", "2026-08-05")
    rfi.update_request(conn, req.id, title="new")
    assert rfi.get_request(conn, req.id).title == "new"
    events = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE entity_id = ?", (req.id,)
    ).fetchone()[0]
    assert events >= 2, "create and update must both land in the event log"


def test_get_request_raises_for_unknown(conn) -> None:
    from bookkit.repo import rfi

    with pytest.raises(KeyError):
        rfi.get_request(conn, "nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rfi_repo.py -v -k request`
Expected: FAIL — `ModuleNotFoundError: No module named 'bookkit.repo.rfi'`

- [ ] **Step 3: Write the implementation**

Create `src/bookkit/repo/rfi.py`:

```python
"""Information requests (RFIs) — batches of questions and document requests
a client owes. A request's open/closed state is DERIVED from its items
(services/rfi.py owns that rule); nothing here stores it."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ids import RFI_REF, next_ref
from ..models import RfiRequest
from . import base


def create_request(
    conn: sqlite3.Connection, org_id: str, title: str, requested_on: str, **fields: Any
) -> RfiRequest:
    fields.setdefault("ref", next_ref(conn, RFI_REF))
    request_id = base.insert(
        conn,
        "rfi_request",
        {"org_id": org_id, "title": title, "requested_on": requested_on, **fields},
    )
    return get_request(conn, request_id)


def get_request(conn: sqlite3.Connection, request_id: str) -> RfiRequest:
    row = base.get(conn, "rfi_request", request_id)
    if row is None:
        raise KeyError(f"rfi request {request_id} not found")
    return RfiRequest.from_row(row)


def requests_for_org(conn: sqlite3.Connection, org_id: str) -> list[RfiRequest]:
    rows = conn.execute(
        f"""SELECT * FROM rfi_request WHERE org_id = ? AND {base.alive()}
            ORDER BY cancelled_at IS NOT NULL, due_on IS NULL, due_on,
                     requested_on DESC""",
        (org_id,),
    ).fetchall()
    return [RfiRequest.from_row(r) for r in rows]


def update_request(
    conn: sqlite3.Connection, request_id: str, note: str | None = None, **changes: Any
) -> RfiRequest:
    base.update(conn, "rfi_request", request_id, changes, note)
    return get_request(conn, request_id)


def delete_request(conn: sqlite3.Connection, request_id: str) -> None:
    base.soft_delete(conn, "rfi_request", request_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rfi_repo.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add src/bookkit/repo/rfi.py tests/test_rfi_repo.py
git commit -m "rfi: request CRUD, refs allocated like projects

Live requests sort by due date with cancelled ones last, so the account
list and the chase queue agree on ordering without a second rule."
```

---

### Task 3: Item CRUD and the category vocabulary

**Files:**
- Modify: `src/bookkit/repo/rfi.py` (append an items section)
- Modify: `src/bookkit/repo/vocab.py` (add `rfi_categories`, after `task_categories` at line 62)
- Test: `tests/test_rfi_repo.py` (append)

**Interfaces:**
- Consumes: `create_request` / `get_request` from Task 2.
- Produces:
  - `add_item(conn: sqlite3.Connection, request_id: str, prompt: str, **fields: Any) -> RfiItem`
  - `get_item(conn: sqlite3.Connection, item_id: str) -> RfiItem` (raises `KeyError`)
  - `items_for_request(conn: sqlite3.Connection, request_id: str) -> list[RfiItem]`
  - `update_item(conn: sqlite3.Connection, item_id: str, note: str | None = None, **changes: Any) -> RfiItem`
  - `delete_item(conn: sqlite3.Connection, item_id: str) -> None`
  - `vocab.rfi_categories(conn: sqlite3.Connection) -> list[str]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rfi_repo.py`:

```python
def test_items_order_by_category_then_creation(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "safety manual", category="Safety")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    rfi.add_item(conn, req.id, "tax return", category="Financials")
    rfi.add_item(conn, req.id, "anything else")  # uncategorised sorts last
    prompts = [i.prompt for i in rfi.items_for_request(conn, req.id)]
    assert prompts == [
        "audited financials", "tax return", "safety manual", "anything else",
    ]


def test_item_defaults_are_question_and_outstanding(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "q", "2026-08-05")
    item = rfi.add_item(conn, req.id, "how many vehicles?")
    assert item.kind == "question"
    assert item.status == "outstanding"
    assert item.received_on is None


def test_update_item_records_a_response(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "q", "2026-08-05")
    item = rfi.add_item(conn, req.id, "how many vehicles?")
    rfi.update_item(
        conn, item.id, response="42, all owned", received_on="2026-08-12",
        status="received",
    )
    got = rfi.get_item(conn, item.id)
    assert got.response == "42, all owned"
    assert got.received_on == "2026-08-12"
    assert got.status == "received"


def test_deleted_items_disappear(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "q", "2026-08-05")
    item = rfi.add_item(conn, req.id, "gone")
    rfi.delete_item(conn, item.id)
    assert rfi.items_for_request(conn, req.id) == []


def test_rfi_categories_vocabulary(conn) -> None:
    from bookkit.repo import rfi, vocab

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "docs", "2026-08-05")
    rfi.add_item(conn, req.id, "a", category="Financials")
    rfi.add_item(conn, req.id, "b", category="Safety")
    rfi.add_item(conn, req.id, "c")
    assert vocab.rfi_categories(conn) == ["Financials", "Safety"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rfi_repo.py -v -k "item or categor"`
Expected: FAIL — `AttributeError: module 'bookkit.repo.rfi' has no attribute 'add_item'`

- [ ] **Step 3: Write the implementation**

Append to `src/bookkit/repo/rfi.py`:

```python
# --- items ---------------------------------------------------------------------


def add_item(
    conn: sqlite3.Connection, request_id: str, prompt: str, **fields: Any
) -> RfiItem:
    item_id = base.insert(
        conn, "rfi_item", {"request_id": request_id, "prompt": prompt, **fields}
    )
    return get_item(conn, item_id)


def get_item(conn: sqlite3.Connection, item_id: str) -> RfiItem:
    row = base.get(conn, "rfi_item", item_id)
    if row is None:
        raise KeyError(f"rfi item {item_id} not found")
    return RfiItem.from_row(row)


def items_for_request(conn: sqlite3.Connection, request_id: str) -> list[RfiItem]:
    """Category groups first (uncategorised last), creation order within —
    the same order the client's sheet renders, so screen and export agree."""
    rows = conn.execute(
        f"""SELECT * FROM rfi_item WHERE request_id = ? AND {base.alive()}
            ORDER BY category IS NULL, category, created_at, id""",
        (request_id,),
    ).fetchall()
    return [RfiItem.from_row(r) for r in rows]


def update_item(
    conn: sqlite3.Connection, item_id: str, note: str | None = None, **changes: Any
) -> RfiItem:
    base.update(conn, "rfi_item", item_id, changes, note)
    return get_item(conn, item_id)


def delete_item(conn: sqlite3.Connection, item_id: str) -> None:
    base.soft_delete(conn, "rfi_item", item_id)
```

Add `RfiItem` to the model import at the top of the file:

```python
from ..models import RfiItem, RfiRequest
```

In `src/bookkit/repo/vocab.py`, after `task_categories`:

```python
def rfi_categories(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "rfi_item", "category"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rfi_repo.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add src/bookkit/repo/rfi.py src/bookkit/repo/vocab.py tests/test_rfi_repo.py
git commit -m "rfi: item CRUD and the category vocabulary

Items sort by category then creation — the same order the client sheet
will render, so the datasheet and the export never disagree."
```

---

### Task 4: The chase feed in `services/rfi.py`

**Files:**
- Create: `src/bookkit/services/rfi.py`
- Modify: `src/bookkit/repo/rfi.py` (append `outstanding_rows`)
- Test: `tests/test_rfi_service.py`

**Interfaces:**
- Consumes: everything from Tasks 2–3.
- Produces:
  - `repo.rfi.outstanding_rows(conn, horizon: str) -> list[sqlite3.Row]` — one row per request with `open_count`, `total_count`, `earliest_due`, `org_name`, `market_name`, plus every `rfi_request` column.
  - `services.rfi.RfiChase` — frozen dataclass: `request: RfiRequest`, `org_name: str`, `market_name: str | None`, `open_count: int`, `total_count: int`, `earliest_due: str | None`, `days_remaining: int`.
  - `services.rfi.is_open(conn, request_id: str) -> bool`
  - `services.rfi.outstanding_requests(conn, today: date, days: int = 120) -> list[RfiChase]`
  - `services.rfi.mark_received(conn, item_id: str, on: str) -> RfiItem`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rfi_service.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import orgs, rfi
from bookkit.services import rfi as rfi_svc

TODAY = date(2026, 8, 13)


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "rfi.db")
    yield connection
    connection.close()


def _request(conn, **fields):
    org = orgs.create(conn, name="Endeavour Energy", kind="client")
    return rfi.create_request(conn, org.id, "Sompo questions", "2026-08-05", **fields)


def test_request_with_an_outstanding_item_is_open(conn) -> None:
    req = _request(conn)
    rfi.add_item(conn, req.id, "how many vehicles?")
    assert rfi_svc.is_open(conn, req.id) is True


def test_all_received_or_waived_closes_the_request(conn) -> None:
    req = _request(conn)
    a = rfi.add_item(conn, req.id, "a")
    b = rfi.add_item(conn, req.id, "b")
    rfi.update_item(conn, a.id, status="received", received_on="2026-08-12")
    rfi.update_item(conn, b.id, status="waived")
    assert rfi_svc.is_open(conn, req.id) is False


def test_a_request_with_no_items_reads_open(conn) -> None:
    """Documented convention: an empty request is still something you owe."""
    req = _request(conn)
    assert rfi_svc.is_open(conn, req.id) is True


def test_overdue_requests_never_fall_off(conn) -> None:
    req = _request(conn, due_on=(TODAY - timedelta(days=400)).isoformat())
    rfi.add_item(conn, req.id, "ancient")
    chases = rfi_svc.outstanding_requests(conn, TODAY, days=120)
    assert [c.request.id for c in chases] == [req.id]
    assert chases[0].days_remaining == -400


def test_requests_beyond_the_window_are_excluded(conn) -> None:
    req = _request(conn, due_on=(TODAY + timedelta(days=200)).isoformat())
    rfi.add_item(conn, req.id, "later")
    assert rfi_svc.outstanding_requests(conn, TODAY, days=120) == []


def test_item_due_pulls_the_request_forward(conn) -> None:
    """The earliest EFFECTIVE due wins: an urgent item surfaces its parent."""
    req = _request(conn, due_on=(TODAY + timedelta(days=200)).isoformat())
    rfi.add_item(conn, req.id, "urgent", due_on=(TODAY + timedelta(days=3)).isoformat())
    chases = rfi_svc.outstanding_requests(conn, TODAY, days=120)
    assert len(chases) == 1
    assert chases[0].days_remaining == 3


def test_counts_are_of_outstanding_items_only(conn) -> None:
    req = _request(conn, due_on=TODAY.isoformat())
    a = rfi.add_item(conn, req.id, "a")
    rfi.add_item(conn, req.id, "b")
    rfi.add_item(conn, req.id, "c")
    rfi.update_item(conn, a.id, status="received", received_on="2026-08-12")
    chase = rfi_svc.outstanding_requests(conn, TODAY, days=120)[0]
    assert (chase.open_count, chase.total_count) == (2, 3)


def test_cancelled_and_closed_requests_are_absent(conn) -> None:
    cancelled = _request(conn, due_on=TODAY.isoformat())
    rfi.add_item(conn, cancelled.id, "x")
    rfi.update_request(conn, cancelled.id, cancelled_at="2026-08-10")

    closed = _request(conn, due_on=TODAY.isoformat())
    done = rfi.add_item(conn, closed.id, "y")
    rfi.update_item(conn, done.id, status="received", received_on="2026-08-12")

    assert rfi_svc.outstanding_requests(conn, TODAY, days=120) == []


def test_mark_received_stamps_status_and_date(conn) -> None:
    req = _request(conn)
    item = rfi.add_item(conn, req.id, "loss runs")
    got = rfi_svc.mark_received(conn, item.id, TODAY.isoformat())
    assert got.status == "received"
    assert got.received_on == "2026-08-13"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rfi_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bookkit.services.rfi'`

- [ ] **Step 3: Add the repo query**

Append to `src/bookkit/repo/rfi.py`:

```python
def outstanding_rows(conn: sqlite3.Connection, horizon: str) -> list[sqlite3.Row]:
    """One row per live, uncancelled request that still has outstanding items
    whose EFFECTIVE due (item's, else the request's) falls on or before the
    horizon — or is already past, so nothing overdue ever falls off.

    NULL effective dues are excluded: an undated request is not yet a chase."""
    return conn.execute(
        f"""
        SELECT r.*, o.name AS org_name, m.name AS market_name,
               COUNT(*)                       AS open_count,
               MIN(COALESCE(i.due_on, r.due_on)) AS earliest_due,
               (SELECT COUNT(*) FROM rfi_item t
                 WHERE t.request_id = r.id AND {base.alive('t')}) AS total_count
        FROM rfi_item i
        JOIN rfi_request r ON r.id = i.request_id
        JOIN org o ON o.id = r.org_id
        LEFT JOIN org m ON m.id = r.market_org_id
        WHERE i.status = 'outstanding'
          AND r.cancelled_at IS NULL
          AND {base.alive('i')} AND {base.alive('r')} AND {base.alive('o')}
        GROUP BY r.id
        HAVING earliest_due IS NOT NULL AND earliest_due <= ?
        ORDER BY earliest_due, r.ref
        """,
        (horizon,),
    ).fetchall()


def open_item_count(conn: sqlite3.Connection, request_id: str) -> int:
    """How many items are still outstanding. Zero means the request is done —
    services/rfi.is_open turns that into the derived open/closed rule."""
    return int(
        conn.execute(
            f"""SELECT COUNT(*) FROM rfi_item
                WHERE request_id = ? AND status = 'outstanding' AND {base.alive()}""",
            (request_id,),
        ).fetchone()[0]
    )


def item_count(conn: sqlite3.Connection, request_id: str) -> int:
    return int(
        conn.execute(
            f"""SELECT COUNT(*) FROM rfi_item
                WHERE request_id = ? AND {base.alive()}""",
            (request_id,),
        ).fetchone()[0]
    )
```

- [ ] **Step 4: Write the service**

Create `src/bookkit/services/rfi.py`:

```python
"""Information-request rules. A request's open/closed state is DERIVED here
and stored nowhere: it is open while any item is outstanding. The chase feed
follows the house attention rule — a 120-day window, and nothing overdue
ever falls off."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..dates import days_until
from ..models import RfiItem, RfiRequest
from ..repo import rfi as rfi_repo


@dataclass(frozen=True)
class RfiChase:
    """One row of the chase queue: a request you would send one email about."""

    request: RfiRequest
    org_name: str
    market_name: str | None
    open_count: int
    total_count: int
    earliest_due: str | None
    days_remaining: int


def is_open(conn: sqlite3.Connection, request_id: str) -> bool:
    """Open while anything is still outstanding. A request with NO items reads
    open by convention — it is an ask you have not yet written down, not a
    finished one."""
    request = rfi_repo.get_request(conn, request_id)
    if request.cancelled_at:
        return False
    if rfi_repo.item_count(conn, request_id) == 0:
        return True
    return rfi_repo.open_item_count(conn, request_id) > 0


def outstanding_requests(
    conn: sqlite3.Connection, today: date, days: int = 120
) -> list[RfiChase]:
    horizon = (today + timedelta(days=days)).isoformat()
    out: list[RfiChase] = []
    for row in rfi_repo.outstanding_rows(conn, horizon):
        earliest = row["earliest_due"]
        out.append(
            RfiChase(
                request=RfiRequest.from_row(row),
                org_name=row["org_name"],
                market_name=row["market_name"],
                open_count=int(row["open_count"]),
                total_count=int(row["total_count"]),
                earliest_due=earliest,
                days_remaining=days_until(earliest, today),
            )
        )
    return out


def mark_received(conn: sqlite3.Connection, item_id: str, on: str) -> RfiItem:
    """d on an item: received, dated. One field write, so u undoes it."""
    return rfi_repo.update_item(conn, item_id, status="received", received_on=on)
```

Note: `RfiRequest.from_row(row)` works on the joined row because pydantic ignores the extra `org_name` / `market_name` / count columns. If your `Row` base is strict, filter first with `{k: row[k] for k in row.keys() if k in RfiRequest.model_fields}`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_rfi_service.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add src/bookkit/repo/rfi.py src/bookkit/services/rfi.py tests/test_rfi_service.py
git commit -m "rfi: derived open/closed and the 120-day chase feed

Rows are requests, not items — you chase a request with one email. An
individually urgent item surfaces by pulling its parent's earliest
effective due forward (MIN of item due, falling back to request due)."
```

---

### Task 5: The `bookctl today` chase line

**Files:**
- Modify: `src/bookkit/cli.py:240-275` (`_print_today`, after the PROJECT NEEDS block)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `services.rfi.outstanding_requests` from Task 4.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_today_lists_requests_to_chase(tmp_path, capsys) -> None:
    from datetime import date

    from bookkit import db
    from bookkit.cli import main
    from bookkit.repo import orgs, rfi

    path = tmp_path / "t.db"
    conn = db.connect(path)
    org = orgs.create(conn, name="Endeavour Energy", kind="client")
    req = rfi.create_request(
        conn, org.id, "Sompo — property questions", "2026-08-05",
        due_on=date.today().isoformat(),
    )
    rfi.add_item(conn, req.id, "how many vehicles?")
    rfi.add_item(conn, req.id, "loss runs 2021-2025", kind="document")
    conn.close()

    assert main(["--db", str(path), "today"]) == 0
    out = capsys.readouterr().out
    assert "REQUESTS TO CHASE (1)" in out
    assert "Sompo — property questions" in out
    assert "2 of 2 open" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v -k chase`
Expected: FAIL — `"REQUESTS TO CHASE (1)" not in out`

- [ ] **Step 3: Write the implementation**

In `src/bookkit/cli.py`, inside `_print_today`, after the PROJECT NEEDS block and before the function ends:

```python
    from .services import rfi as rfi_svc

    chases = rfi_svc.outstanding_requests(conn, today, days=120)
    print(f"\nREQUESTS TO CHASE ({len(chases)})")
    for chase in chases[:15]:
        when = (
            f"{-chase.days_remaining}d overdue"
            if chase.days_remaining < 0
            else f"{chase.days_remaining:>3}d"
        )
        asker = f" ({chase.market_name})" if chase.market_name else ""
        print(
            f"  [{when:>10}] {chase.org_name} — {chase.request.title}{asker} "
            f"· {chase.open_count} of {chase.total_count} open"
        )
    if not chases:
        print("  none")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v -k chase`
Expected: PASS

- [ ] **Step 5: Gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add src/bookkit/cli.py tests/test_cli.py
git commit -m "rfi: bookctl today lists the requests to chase

Same shape as the other brief blocks — overdue marker, client, title,
who asked, and how much of the request is still open."
```

---

### Task 6: Forms for requests and items

**Files:**
- Modify: `src/bookkit/tui/widgets/entity_forms.py` (append after `apply_need`, line ~503)
- Test: `tests/test_rfi_forms.py`

**Interfaces:**
- Consumes: `repo.rfi`, `vocab.rfi_categories`, `models.RFI_ITEM_KINDS`, `models.RFI_ITEM_STATUSES`.
- Produces:
  - `request_form(existing: RfiRequest | None = None, *, conn: sqlite3.Connection | None = None) -> FormSpec`
  - `apply_request(conn, values: dict[str, Any], org_id: str, existing: RfiRequest | None = None) -> RfiRequest`
  - `rfi_item_form(existing: RfiItem | None = None, *, conn: sqlite3.Connection | None = None) -> FormSpec`
  - `apply_rfi_item(conn, values: dict[str, Any], request_id: str, existing: RfiItem | None = None) -> RfiItem`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rfi_forms.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import orgs, rfi
from bookkit.tui.widgets import entity_forms as ef


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "rfi.db")
    yield connection
    connection.close()


def test_request_form_offers_markets_and_defaults_today(conn) -> None:
    orgs.create(conn, name="Sompo", kind="market")
    spec = ef.request_form(conn=conn)
    keys = [f.key for f in spec.fields]
    assert keys == [
        "title", "requested_on", "due_on", "market_org_id", "cancelled_at",
        "notes",
    ]
    market_field = next(f for f in spec.fields if f.key == "market_org_id")
    assert "Sompo" in [label for label, _ in market_field.options]
    assert market_field.optional_select is True
    assert spec.initial["requested_on"]


def test_cancelling_a_request_closes_it(conn) -> None:
    """Withdrawal happens in the form, not on a key — 'cancelled on' set to a
    date is what closes it."""
    from bookkit.services import rfi as rfi_svc

    org = orgs.create(conn, name="Endeavour", kind="client")
    req = rfi.create_request(conn, org.id, "withdrawn", "2026-08-05")
    rfi.add_item(conn, req.id, "never mind")
    assert rfi_svc.is_open(conn, req.id) is True
    ef.apply_request(conn, {"cancelled_at": "2026-08-12"}, org.id, existing=req)
    assert rfi_svc.is_open(conn, req.id) is False


def test_apply_request_creates_then_updates(conn) -> None:
    org = orgs.create(conn, name="Endeavour", kind="client")
    created = ef.apply_request(
        conn,
        {"title": "Sompo questions", "requested_on": "2026-08-05", "due_on": None,
         "market_org_id": None, "notes": None},
        org.id,
    )
    assert created.ref.startswith("RFI-")
    updated = ef.apply_request(
        conn, {"title": "Sompo questions v2"}, org.id, existing=created
    )
    assert updated.id == created.id
    assert updated.title == "Sompo questions v2"


def test_item_form_completes_categories_from_existing_items(conn) -> None:
    org = orgs.create(conn, name="Endeavour", kind="client")
    req = rfi.create_request(conn, org.id, "docs", "2026-08-05")
    rfi.add_item(conn, req.id, "financials", category="Financials")
    spec = ef.rfi_item_form(conn=conn)
    category = next(f for f in spec.fields if f.key == "category")
    assert "Financials" in category.suggestions
    assert spec.initial["kind"] == "question"
    assert spec.initial["status"] == "outstanding"


def test_apply_rfi_item_creates_then_updates(conn) -> None:
    org = orgs.create(conn, name="Endeavour", kind="client")
    req = rfi.create_request(conn, org.id, "docs", "2026-08-05")
    item = ef.apply_rfi_item(
        conn,
        {"prompt": "loss runs", "kind": "document", "category": "Financials",
         "due_on": None, "detail": None, "status": "outstanding",
         "received_on": None, "response": None},
        req.id,
    )
    assert item.kind == "document"
    done = ef.apply_rfi_item(
        conn, {"status": "received", "received_on": "2026-08-12"}, req.id,
        existing=item,
    )
    assert done.status == "received"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rfi_forms.py -v`
Expected: FAIL — `AttributeError: module 'bookkit.tui.widgets.entity_forms' has no attribute 'request_form'`

- [ ] **Step 3: Write the implementation**

Append to `src/bookkit/tui/widgets/entity_forms.py`:

```python
def request_form(
    existing: RfiRequest | None = None, *, conn: sqlite3.Connection | None = None
) -> FormSpec:
    markets: tuple[tuple[str, str], ...] = ()
    if conn is not None:
        markets = tuple(
            (o.name, o.id) for o in orgs.list_orgs(conn, kind="market")
        )
    initial = (
        existing.model_dump()
        if existing
        else {"requested_on": date.today().isoformat()}
    )
    return FormSpec(
        "edit information request" if existing else "new information request",
        [
            Field("title", "request", required=True,
                  placeholder="Sompo — property questions"),
            Field("requested_on", "asked on", "date", required=True),
            Field("due_on", "response due", "date"),
            Field("market_org_id", "asked by", "select", markets,
                  optional_select=True),
            # withdrawal lives here, not on a key: `d` already means "done"
            # app-wide. Blank = live; a date = withdrawn.
            Field("cancelled_at", "cancelled on", "date"),
            Field("notes", "notes", "textarea"),
        ],
        initial=initial,
    )


def apply_request(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    org_id: str,
    existing: RfiRequest | None = None,
) -> RfiRequest:
    core = dropped(values)
    if existing:
        return rfi_repo.update_request(conn, existing.id, **core)
    title = core.pop("title")
    requested_on = core.pop("requested_on")
    return rfi_repo.create_request(conn, org_id, title, requested_on, **core)


def rfi_item_form(
    existing: RfiItem | None = None, *, conn: sqlite3.Connection | None = None
) -> FormSpec:
    category_sugg = tuple(vocab.rfi_categories(conn)) if conn else ()
    initial = (
        existing.model_dump()
        if existing
        else {"kind": "question", "status": "outstanding"}
    )
    return FormSpec(
        "edit item" if existing else "new item",
        [
            Field("prompt", "item", required=True,
                  placeholder="loss runs 2021-2025"),
            Field("kind", "type", "select",
                  tuple((k, k) for k in RFI_ITEM_KINDS)),
            Field("category", "group", suggestions=category_sugg,
                  placeholder="Financials"),
            Field("due_on", "needed by", "date"),
            Field("detail", "detail", "textarea"),
            Field("status", "status", "select",
                  tuple((s, s) for s in RFI_ITEM_STATUSES)),
            Field("received_on", "received on", "date"),
            Field("response", "response", "textarea"),
        ],
        initial=initial,
    )


def apply_rfi_item(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    request_id: str,
    existing: RfiItem | None = None,
) -> RfiItem:
    core = dropped(values)
    if existing:
        return rfi_repo.update_item(conn, existing.id, **core)
    prompt = core.pop("prompt")
    return rfi_repo.add_item(conn, request_id, prompt, **core)
```

Add to the imports at the top of `entity_forms.py`:

```python
from ...models import RFI_ITEM_KINDS, RFI_ITEM_STATUSES, RfiItem, RfiRequest
from ...repo import rfi as rfi_repo
```

(`orgs`, `vocab`, `date`, `dropped`, `Field`, `FormSpec` are already imported in this module — check before adding duplicates.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rfi_forms.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add src/bookkit/tui/widgets/entity_forms.py tests/test_rfi_forms.py
git commit -m "rfi: request and item forms

Markets populate the 'asked by' select from existing market orgs and
stay optional (onboarding asks have no market); item groups complete
from categories already in use, the vocab pattern lines already follow."
```

---

### Task 7: Shared flows in `entity_actions`

**Files:**
- Modify: `src/bookkit/tui/widgets/entity_actions.py` (append after `export_open_items_flow`, line ~211)
- Create: `src/bookkit/tui/widgets/rfi_paste.py`
- Test: `tests/test_rfi_paste.py`

**Interfaces:**
- Consumes: `entity_forms.request_form` / `apply_request` / `rfi_item_form` / `apply_rfi_item` from Task 6; `services.rfi.mark_received` from Task 4.
- Produces:
  - `rfi_paste.split_items(text: str) -> list[str]`
  - `entity_actions.add_request(screen: Screen, org_id: str) -> None`
  - `entity_actions.edit_request(screen: Screen, request: RfiRequest) -> None`
  - `entity_actions.add_rfi_item(screen: Screen, request_id: str) -> None`
  - `entity_actions.edit_rfi_item(screen: Screen, item: RfiItem) -> None`
  - `entity_actions.paste_rfi_items(screen: Screen, request_id: str) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rfi_paste.py`:

```python
from __future__ import annotations

from bookkit.tui.widgets.rfi_paste import split_items


def test_strips_numbering_and_bullets() -> None:
    assert split_items(
        "1. How many vehicles?\n"
        "2) Loss runs 2021-2025\n"
        "- Safety manual\n"
        "• EMR letter\n"
        "* Payroll by class"
    ) == [
        "How many vehicles?",
        "Loss runs 2021-2025",
        "Safety manual",
        "EMR letter",
        "Payroll by class",
    ]


def test_skips_blank_lines_and_trims() -> None:
    assert split_items("  a  \n\n\n   \n b ") == ["a", "b"]


def test_handles_crlf() -> None:
    assert split_items("a\r\nb\r\n") == ["a", "b"]


def test_single_line_and_empty() -> None:
    assert split_items("just one") == ["just one"]
    assert split_items("") == []
    assert split_items("   \n  ") == []


def test_leaves_inner_punctuation_alone() -> None:
    """Only LEADING markers go; a hyphen mid-sentence must survive."""
    assert split_items("1. Loss runs - all years") == ["Loss runs - all years"]


def test_does_not_strip_a_bare_number_that_is_the_question() -> None:
    assert split_items("2026 payroll figures") == ["2026 payroll figures"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rfi_paste.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bookkit.tui.widgets.rfi_paste'`

- [ ] **Step 3: Write the splitter**

Create `src/bookkit/tui/widgets/rfi_paste.py`:

```python
"""Turn a pasted litany into items. Underwriter questions arrive as a
numbered or bulleted block in an email; typing them one form at a time is
the failure mode that kills the feature, so one line becomes one item."""

from __future__ import annotations

import re

# leading "1." / "1)" / "-" / "*" / "•" plus the space after it. The trailing
# \s+ is required: "2026 payroll figures" is a question, not item 2026.
_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")


def split_items(text: str) -> list[str]:
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        cleaned = _MARKER.sub("", line).strip()
        if cleaned:
            out.append(cleaned)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rfi_paste.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Add the shared flows**

Append to `src/bookkit/tui/widgets/entity_actions.py`:

```python
def add_request(screen: Screen, org_id: str) -> None:
    conn = _app(screen).conn
    push_form(
        screen,
        ef.request_form(conn=conn),
        lambda v: screen.notify(f"created {ef.apply_request(conn, v, org_id).ref}"),
    )


def edit_request(screen: Screen, request: RfiRequest) -> None:
    conn = _app(screen).conn
    push_form(
        screen,
        ef.request_form(request, conn=conn),
        lambda v: ef.apply_request(conn, v, request.org_id, existing=request),
    )


def add_rfi_item(screen: Screen, request_id: str) -> None:
    conn = _app(screen).conn
    push_form(
        screen,
        ef.rfi_item_form(conn=conn),
        lambda v: ef.apply_rfi_item(conn, v, request_id),
    )


def edit_rfi_item(screen: Screen, item: RfiItem) -> None:
    conn = _app(screen).conn
    push_form(
        screen,
        ef.rfi_item_form(item, conn=conn),
        lambda v: ef.apply_rfi_item(conn, v, item.request_id, existing=item),
    )


def paste_rfi_items(screen: Screen, request_id: str) -> None:
    """One pasted block → one item per line. Refuses an empty paste in place
    (commit-in-place: the form stays up with the text intact)."""
    conn = _app(screen).conn

    def commit(values: dict) -> str | None:
        prompts = split_items(values.get("pasted") or "")
        if not prompts:
            return "nothing to add — paste one item per line"
        for prompt in prompts:
            rfi_repo.add_item(conn, request_id, prompt)
        screen.notify(f"added {len(prompts)} items — u undoes the last")
        return None

    push_form(
        screen,
        FormSpec(
            "paste items — one per line",
            [Field("pasted", "items", "textarea", required=True)],
        ),
        commit,
    )
```

Add to `entity_actions.py` imports:

```python
from ...models import RfiItem, RfiRequest
from ...repo import rfi as rfi_repo
from .forms import Field, FormSpec
from .rfi_paste import split_items
```

(`ef` — `entity_forms` — is already imported in this module; check before adding.)

- [ ] **Step 6: Run gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add src/bookkit/tui/widgets/rfi_paste.py src/bookkit/tui/widgets/entity_actions.py tests/test_rfi_paste.py
git commit -m "rfi: shared add/edit flows and paste-to-create

The splitter only strips LEADING markers, so '2026 payroll figures'
survives intact while '2. Loss runs' loses its number."
```

---

### Task 8: Navigator — chase bucket and account group

**Files:**
- Modify: `src/bookkit/tui/screens/navigator.py` — `ROW_HINTS` (line ~40), `ADDABLE` (line ~61), `refresh_data` attention dict + tree leaves (lines ~154-176), `_add_account_groups` counts (line ~254), `_fill_attention_table` (line ~402), `_fill_group_table` (line ~476), `action_add_row` / `action_edit_row`
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Consumes: `services.rfi.outstanding_requests`, `repo.rfi.requests_for_org`, `repo.rfi.open_item_count`, `entity_actions.add_request` / `edit_request`.
- Produces: attention key `"rfi"`; tree leaf data `("att", "rfi")` and `("group", ("requests", org_id))`; table row keys `rfi:{request_id}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tui.py`:

```python
async def test_navigator_rfi_chase_bucket_and_group(seeded_db: Path) -> None:
    """A request with an outstanding item shows in the attention feed as ONE
    row carrying its open count, and under its account as a group."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(
        app.conn, org.id, "Sompo — property questions", "2026-08-05",
        due_on=date.today().isoformat(),
    )
    rfi.add_item(app.conn, req.id, "how many vehicles?")
    rfi.add_item(app.conn, req.id, "loss runs", kind="document")

    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav.refresh_data()
        await pilot.pause()

        nav._current = ("att", "rfi")
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        assert table.row_count == 1, "one row per request, not per item"
        row = [str(c) for c in table.get_row(f"rfi:{req.id}")]
        assert any("Sompo — property questions" in c for c in row)
        assert any("2 of 2" in c for c in row)

        nav._current = ("group", ("requests", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        assert table.get_row_index(f"rfi:{req.id}") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui.py -v -k rfi_chase`
Expected: FAIL — table has 0 rows (`"rfi"` is not a known attention key).

- [ ] **Step 3: Wire the attention feed**

In `refresh_data`, alongside the other attention queries:

```python
        from ...services import rfi as rfi_svc

        chases = rfi_svc.outstanding_requests(conn, today, days=120)
```

Add `"rfi": chases,` to the `self._attention` dict, and add a leaf to the ATTENTION tuple after `("onboarding", "onboarding incomplete", len(pending_onboarding))`:

```python
            ("rfi", "requests to chase", len(chases)),
```

- [ ] **Step 4: Fill the attention table**

Add a branch to `_fill_attention_table`:

```python
        elif which == "rfi":
            table.add_columns(
                "response due", right("due in"), "account", "request",
                "asked by", "open",
            )
            for chase in self._attention["rfi"]:
                key = f"rfi:{chase.request.id}"
                self._row_org[key] = chase.request.org_id
                table.add_row(
                    date_text(chase.earliest_due, chase.days_remaining),
                    days_text(chase.days_remaining), chase.org_name,
                    chase.request.title,
                    Text(chase.market_name or "—", style=theme.DIM),
                    f"{chase.open_count} of {chase.total_count}",
                    key=key,
                )
```

- [ ] **Step 5: Wire the account group**

In `_add_account_groups`, add to the `counts` tuple:

```python
            ("requests", len(rfi_repo.requests_for_org(conn, org_id))),
```

Add a branch to `_fill_group_table`:

```python
        elif group == "requests":
            table.add_columns("ref", "request", "asked by", "asked", "due", "open")
            for request in rfi_repo.requests_for_org(conn, org_id):
                key = f"rfi:{request.id}"
                self._row_org[key] = org_id
                asker = (
                    orgs.get(conn, request.market_org_id).name
                    if request.market_org_id else dash()
                )
                open_count = rfi_repo.open_item_count(conn, request.id)
                table.add_row(
                    request.ref, request.title, asker, request.requested_on,
                    date_text(request.due_on, days_until(request.due_on))
                    if request.due_on else dash(),
                    Text(str(open_count), style=theme.AMBER if open_count else theme.DIM),
                    key=key,
                )
```

Add `from ...repo import rfi as rfi_repo` to the imports.

- [ ] **Step 6: Wire the row verbs**

Add `"requests"` to `ADDABLE`. Add to `ROW_HINTS`:

```python
    "requests": "[b]a[/b] add · [b]e[/b] edit · [b]enter[/b] opens account",
    "rfi": "[b]e[/b] edit request · [b]enter[/b] opens account",
```

In `action_add_row`, add a branch:

```python
        elif group == "requests":
            entity_actions.add_request(self, org_id)
```

In `action_edit_row`, add a branch to the row-kind dispatch. `_selected_row()`
returns a `(kind, entity_id)` pair:

```python
        if kind == "rfi":
            try:
                request = rfi_repo.get_request(conn, entity_id)
            except KeyError:
                self.notify("request no longer exists", severity="error")
                return
            entity_actions.edit_request(self, request)
            return
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_tui.py -v -k rfi_chase`
Expected: PASS

- [ ] **Step 8: Gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add src/bookkit/tui/screens/navigator.py tests/test_tui.py
git commit -m "rfi: chase bucket in the attention tree, requests group per account

One attention row per request with its open count — the thing you send
one email about — not one row per outstanding item."
```

---

### Task 9: AccountScreen tab 9 — the requests datasheet

**Files:**
- Modify: `src/bookkit/tui/screens/account.py` — BINDINGS (line ~224), `compose` TabPane block (line ~270), plus the fill/selection handlers
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 6–7; `services.rfi.mark_received`.
- Produces: tab id `tab-requests`, tables `#rfi-requests` (`ListTable`) and `#rfi-items` (`InlineTable`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tui.py`:

```python
async def test_account_requests_tab(seeded_db: Path) -> None:
    """Tab 9 is master/detail: picking a request fills the items datasheet;
    d marks an item received and dates it; paste adds one item per line."""
    from bookkit.repo import rfi
    from bookkit.tui.widgets.inline_edit import InlineTable
    from bookkit.tui.widgets.tables import ListTable

    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    req = rfi.create_request(app.conn, org.id, "Sompo questions", "2026-08-05")
    item = rfi.add_item(app.conn, req.id, "how many vehicles?")

    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()

        requests = app.screen.query_one("#rfi-requests", ListTable)
        assert requests.get_row_index(f"rfi:{req.id}") == 0

        items = app.screen.query_one("#rfi-items", InlineTable)
        assert items.get_row_index(item.id) == 0

        items.focus()
        items.move_cursor(row=0)
        await pilot.press("d")
        await pilot.pause()
        got = rfi.get_item(app.conn, item.id)
        assert got.status == "received"
        assert got.received_on == date.today().isoformat()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui.py -v -k requests_tab`
Expected: FAIL — no widget `#rfi-requests`.

- [ ] **Step 3: Add the tab and register it in the two tab dicts**

In `AccountScreen.BINDINGS`, after the tab-8 binding:

```python
        Binding("9", "show_tab('tab-requests')", "Requests", show=False),
```

In `compose`, after the `tab-open-items` pane:

```python
            with TabPane("9 Requests", id="tab-requests"):
                yield ListTable(id="rfi-requests")
                yield Static(id="rfi-hint")
                yield InlineTable(id="rfi-items")
```

`account.py` drives tabs from two module-level dicts. Add an entry to each.
In `TAB_HINTS` (line ~82), after the `tab-open-items` entry:

```python
    "tab-requests": (
        "[b]i[/b] edit in cell · [b]a[/b] add · [b]p[/b] paste list · "
        "[b]e[/b] edit form · [b]d[/b] received · [b]u[/b] undo"
    ),
```

In `TAB_TABLES` (line ~89), after `"tab-open-items": "open-items-table",` — this
is what lands the cursor in a table when the tab opens, so `j`/`k` and the row
keys work with no extra keypress:

```python
    "tab-requests": "rfi-requests",
```

Also widen the `action_show_tab` docstring from "1–7 jump straight to a tab"
to "1–9", since it is now stale by two.

In `AccountScreen.__init__`, initialise the selected-request tracker beside
the other instance state:

```python
        self._rfi_request_id: str | None = None
```

- [ ] **Step 4: Fill both tables**

Add a fill method, called from the same place the other tabs are filled (follow how `_fill_open_items` / the tab-8 fill is wired):

```python
    def _fill_requests_tab(self) -> None:
        conn = self.app.conn
        table = self.query_one("#rfi-requests", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "request", "asked", "due", "open")
        requests = rfi_repo.requests_for_org(conn, self.org_id)
        for request in requests:
            open_count = rfi_repo.open_item_count(conn, request.id)
            table.add_row(
                request.ref, request.title, request.requested_on,
                request.due_on or dash(),
                Text(str(open_count), style=theme.AMBER if open_count else theme.DIM),
                key=f"rfi:{request.id}",
            )
        self._fill_request_items(requests[0].id if requests else None)

    def _fill_request_items(self, request_id: str | None) -> None:
        conn = self.app.conn
        items = self.query_one("#rfi-items", InlineTable)
        items.clear(columns=True)
        items.add_columns("item", "type", "group", "needed by", "status", "received")
        self._rfi_request_id = request_id
        if request_id is None:
            items.inline_fields = {}
            return
        items.inline_fields = RFI_ITEM_INLINE
        for item in rfi_repo.items_for_request(conn, request_id):
            items.add_row(
                item.prompt, item.kind, item.category or dash(),
                item.due_on or dash(), status_text(item.status),
                item.received_on or dash(), key=item.id,
            )
```

Add at module level, beside the other inline maps:

```python
RFI_ITEM_INLINE = {
    0: Field("prompt", "item", required=True),
    2: Field("category", "group"),
    3: Field("due_on", "needed by", "date"),
}
```

Call `_fill_requests_tab()` from `refresh_data` (line ~307) alongside the
other tab fills, so the tab is populated before it is ever shown.

`account.py` already has a `RowHighlighted` handler for other tables; add the
requests branch to it (or add one if the master/detail table is the first to
need it). `event.data_table.id` distinguishes the source:

```python
    def on_data_table_row_highlighted(self, event: ListTable.RowHighlighted) -> None:
        if event.data_table.id != "rfi-requests":
            return
        key = str(event.row_key.value or "")
        _, _, request_id = key.partition(":")
        self._fill_request_items(request_id or None)
```

Handle the inline commits. This mirrors `navigator.py:673`, including the
`editing` deferral — a refresh landing mid-edit pulls the row out from under
the open editor:

```python
    def on_inline_table_cell_edited(self, event: InlineTable.CellEdited) -> None:
        if event.table.id != "rfi-items":
            return
        rfi_repo.update_item(
            self.app.conn, event.row_key, **{event.field.key: event.value}
        )
        self.notify(f"{event.field.label} saved — u undoes")
        if not event.table.editing:
            self.refresh_data()
```

Wire the editor's prefill so `i` opens with the current value rather than
blank, in `_fill_request_items` before the rows are added:

```python
        items.inline_initial = lambda row_key, field_key: str(
            getattr(rfi_repo.get_item(conn, row_key), field_key) or ""
        )
```

- [ ] **Step 5: Bind the verbs**

`a` / `e` / `d` are existing screen-level actions that dispatch on the active
tab (see `action_add_row` at line ~871 and the `d` handler at ~952). Add a
`tab-requests` branch to each. Which of the two tables has focus decides
whether the verb acts on a request or an item — row actions REQUIRE table
focus, per the house rule:

```python
    def _rfi_focus(self) -> str | None:
        """Which of the tab's two tables is live: 'requests', 'items', or None."""
        if self.query_one("#rfi-requests", ListTable).has_focus:
            return "requests"
        if self.query_one("#rfi-items", InlineTable).has_focus:
            return "items"
        return None
```

In `action_add_row`, for `tab == "tab-requests"`:

```python
            where = self._rfi_focus()
            if where == "requests":
                entity_actions.add_request(self, self.current_org_id)
            elif where == "items" and self._rfi_request_id:
                entity_actions.add_rfi_item(self, self._rfi_request_id)
```

In `action_edit_row`, for `tab == "tab-requests"`:

```python
            conn = self.app.conn
            where = self._rfi_focus()
            try:
                if where == "requests":
                    key = self._selected_key("rfi-requests")
                    _, _, request_id = str(key or "").partition(":")
                    entity_actions.edit_request(
                        self, rfi_repo.get_request(conn, request_id)
                    )
                elif where == "items":
                    item_id = self._selected_key("rfi-items")
                    entity_actions.edit_rfi_item(
                        self, rfi_repo.get_item(conn, str(item_id))
                    )
            except KeyError:
                self.notify("no longer exists", severity="error")
```

In the `d` handler, for `tab == "tab-requests"` — `d` means "received" here,
its one meaning in this feature, consistent with "done" elsewhere:

```python
            if self._rfi_focus() != "items":
                return
            item_id = self._selected_key("rfi-items")
            if not item_id:
                return
            rfi_svc.mark_received(self.app.conn, str(item_id), date.today().isoformat())
            self.notify("received — u undoes")
            self.refresh_data()
```

Add a new `p` binding to `AccountScreen.BINDINGS` and its action:

```python
        Binding("p", "paste_items", "Paste items", show=False),
```

```python
    def action_paste_items(self) -> None:
        if self._active_tab() != "tab-requests" or self._rfi_focus() != "items":
            return
        if not self._rfi_request_id:
            self.notify("pick a request first", severity="warning")
            return
        entity_actions.paste_rfi_items(self, self._rfi_request_id)
```

Add the imports `account.py` needs for all of the above:

```python
from ...repo import rfi as rfi_repo
from ...services import rfi as rfi_svc
from ..widgets.forms import Field
from ..widgets.inline_edit import InlineTable
```

(`Text`, `dash`, `status_text`, `theme`, `date`, `entity_actions` and
`ListTable` are already imported in this module — check before adding.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_tui.py -v -k requests_tab`
Expected: PASS

- [ ] **Step 7: Gates and commit**

```bash
uv run pytest -q > /tmp/p.log 2>&1 && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/p.log
git add src/bookkit/tui/screens/account.py tests/test_tui.py
git commit -m "rfi: AccountScreen tab 9, the requests datasheet

Master/detail because a two-level model needs it: requests on top,
the selected request's items below as an inline-editable datasheet with
p to paste a whole litany and d to mark one received."
```

---

## Phase 4 (NOT in this plan)

The client's "Information Requests" workbook sheet is blocked on towerkit's multi-sheet composition API landing from `feat/soi-schematic`. When it lands, write a follow-up plan covering `services/export_rfi.py` (pure composition — sections per request, sub-sections per (request, category), outstanding only) and the refactor of `export_open_items.py` into a workbook assembler. The spec's "Export tab" section is the source of truth for that work.
