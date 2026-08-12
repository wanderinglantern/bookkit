# bookkit MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `bookctl mcp` — a stdio MCP server exposing bookkit's workflow reads and a narrow additive write surface to the AI cowork environment on Grant's work machine.

**Architecture:** One module, `src/bookkit/mcpserver.py` (named to avoid shadowing the `mcp` SDK package), a zero-SQL peer of `tui/` consuming `repo/` and `services/`. Read tools run on a `mode=ro` SQLite connection so read-only is enforced by the database; the five write tools run on the normal connection inside `db.transaction` with `source=mcp` event-log provenance. FastMCP (official Python MCP SDK) handles the protocol; stdout is protocol, logging goes to stderr.

**Tech Stack:** `mcp` PyPI package (FastMCP server, stdio transport), rapidfuzz (already a dep) for duplicate guards, existing repo/services layers.

## Global Constraints

- Gates before EVERY commit: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. Never pipe test output before the `&&` gate.
- `mcpserver.py` contains ZERO raw SQL — repos and services only (convention-tested in Task 6).
- Money out of tools is formatted dollars via `money.format_cents` (cents stay internal). Dates out are ISO; date input parses via `dates.parse_human_date` (MDY, two-digit years are 20xx — never let dateparser century-bump).
- Writes: additive inserts + the task-complete status flip ONLY. No towerkit JSON access of any kind. Every write is wrapped in `with db.transaction(conn):` (the connection is AUTOCOMMIT; naked multi-write sequences are the known foot-gun) and event-logged with `base.log_event(conn, <entity>, <id>, "source", None, "mcp")`.
- NEW DEPENDENCY `mcp` → the wheelhouse refresh drill applies on the landing commit (drill lives in towerkit's CLAUDE.md; see the wheelhouse-on-new-deps memory).
- The SDK's exact import paths are stated as of `mcp>=1.x` (2025/2026 series): `from mcp.server.fastmcp import FastMCP`. If an installed version moved things, follow the SDK's README — do not pin to an old version to avoid reading.

---

### Task 1: dependency, read-only connection, server skeleton, `bookctl mcp`

**Files:**
- Modify: `pyproject.toml` (dependencies), `src/bookkit/db.py`, `src/bookkit/cli.py`
- Create: `src/bookkit/mcpserver.py`
- Test: `tests/test_db.py` (append), `tests/test_mcpserver.py` (create)

**Interfaces:**
- Produces: `db.connect_readonly(path=None) -> sqlite3.Connection`; `mcpserver.build_server(db_path=None) -> FastMCP`; `mcpserver.serve(db_path=None)`; `bookctl mcp` subcommand. Later tasks add tools inside `build_server`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` dependencies, append `"mcp>=1.2"`. Run `uv sync 2>&1 | tail -2` and confirm it resolves.

- [ ] **Step 2: Failing tests**

Append to `tests/test_db.py`:

```python
def test_connect_readonly_refuses_writes(tmp_path):
    path = tmp_path / "ro.db"
    db.connect(path).close()  # create + migrate
    ro = db.connect_readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO setting (key, value) VALUES ('x', 'y')")
    ro.close()
```

Create `tests/test_mcpserver.py`:

```python
"""MCP server: tool functions against a real (temp) database. Tools are
tested as plain functions via the registry — the stdio round-trip lives in
test_mcp_roundtrip.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db
from bookkit.mcpserver import build_server


@pytest.fixture
def server_db(tmp_path: Path) -> Path:
    path = tmp_path / "mcp.db"
    db.connect(path).close()
    return path


def test_build_server_registers_tools(server_db):
    server = build_server(server_db)
    # FastMCP keeps registered tools in its tool manager
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "today_brief" in names
```

(If `_tool_manager` is not the attribute in the installed SDK version, use the public accessor the SDK exposes — `server.list_tools()` is async in some versions; adapt, the assertion is what matters.)

- [ ] **Step 3: Run to verify failure** — `uv run pytest tests/test_mcpserver.py tests/test_db.py -k "readonly or registers" -v 2>&1 | tail -4`

- [ ] **Step 4: Implement**

`db.py` (after `connect`):

```python
def connect_readonly(path: Path | str | None = None) -> sqlite3.Connection:
    """A mode=ro URI connection: read-only enforced by SQLite itself, not by
    convention — the MCP server's read tools use this."""
    target = Path(path) if path else default_db_path()
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn
```

`mcpserver.py` skeleton:

```python
"""bookctl mcp — stdio MCP server for the work-machine cowork assistant.

Read tools run on a read-only connection (mode=ro — enforced by the
database). Exactly five write tools exist, all additive, all inside
db.transaction, all event-logged with source=mcp. stdout is protocol;
anything human goes to stderr (never print here)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import db


def build_server(db_path: Path | str | None = None) -> FastMCP:
    server = FastMCP(
        "bookkit",
        instructions=(
            "Grant's book of business. Money values are formatted dollars; "
            "dates are ISO. Use search/open_items to find refs before "
            "completing or enriching anything — never guess an id."
        ),
    )
    ro = db.connect_readonly(db_path)
    rw = db.connect(db_path)
    _register_read_tools(server, ro)
    _register_write_tools(server, rw)
    return server


def _register_read_tools(server: FastMCP, ro: sqlite3.Connection) -> None:
    @server.tool()
    def today_brief() -> dict:
        """Today's working brief: due tasks, renewals in the 120-day window,
        project needs, stale accounts, submissions past SLA."""
        return _today_brief(ro)


def _register_write_tools(server: FastMCP, rw: sqlite3.Connection) -> None:
    pass  # Task 4


def _today_brief(conn: sqlite3.Connection) -> dict:
    from .dates import days_until
    from .money import format_cents_compact
    from .repo import tasks as tasks_repo
    from .services import renewals, sla, staleness

    today = date.today()
    iso = today.isoformat()
    return {
        "date": iso,
        "tasks_due": [
            {"ref": t.id, "title": t.title, "description": t.description,
             "due": t.due_on, "days_overdue": max(0, -days_until(t.due_on, today)) if t.due_on else 0}
            for t in tasks_repo.open_tasks(conn, due_by=iso)
        ],
        "renewals_120d": [_renewal(item) for item in renewals.upcoming(conn, today, days=120)],
        "stale_accounts": [
            {"account": s.org.name, "last_touch": s.last_interaction_on,
             "days_stale": s.days_stale,
             "premium": format_cents_compact(s.premium) if s.premium else None}
            for s in staleness.stale_accounts(conn, today)
        ],
        "submissions_past_sla": [
            {"market": late.market.name, "account": late.account.name,
             "sent_on": late.submission.sent_on, "days_out": late.days_out}
            for late in sla.past_sla(conn, today)
        ],
    }


def _renewal(item) -> dict:
    from .money import format_cents_compact

    return {
        "renews_on": item.renewal_on,
        "days_remaining": item.days_remaining,
        "bucket": item.bucket,
        "account": item.org.name,
        "program": item.placement.program_name,
        "lines_of_cover": item.lines,          # never a program name alone
        "line_ends": list(item.line_ends),
        "status": item.placement.status,
        "premium": format_cents_compact(item.placement.total_premium)
        if item.placement.total_premium else None,
        "placement_ref": item.placement.ref,
    }


def serve(db_path: Path | str | None = None) -> None:
    build_server(db_path).run()  # stdio transport is FastMCP's default
```

(`Task.description` exists once the export plan's Task 3 has landed — this plan runs after it. If building out of order, drop the description key and add it later.)

`cli.py`: parser — `sub.add_parser("mcp", help="stdio MCP server (work-machine cowork connector)")`; dispatch — **before** `conn = db.connect(args.db)` is fine to leave as-is (the server opens its own two connections), so the branch closes the CLI conn path cleanly:

```python
    if args.command == "mcp":
        from .mcpserver import serve

        serve(args.db)
        return 0
```

Place it inside `_dispatch` like every other branch; the extra CLI connection is harmless (read-only until then) but close it first if `_dispatch` structure allows early return — match the surrounding style.

- [ ] **Step 5: Run** — `uv run pytest tests/test_mcpserver.py tests/test_db.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 6: Commit** — includes `uv.lock`:

```bash
git add pyproject.toml uv.lock src/bookkit/db.py src/bookkit/cli.py src/bookkit/mcpserver.py tests/
git commit -m "mcp: bookctl mcp skeleton — FastMCP stdio server, read-only connection, today_brief"
```

WHEELHOUSE: this commit adds a dependency — run the wheelhouse refresh drill (towerkit CLAUDE.md) before this lands anywhere Grant installs from.

---

### Task 2: read tools — renewals, search, programs, staleness

**Files:**
- Modify: `src/bookkit/mcpserver.py`
- Test: `tests/test_mcpserver.py` (append)

**Interfaces:**
- Produces tools: `renewals_due(days: int = 120)`, `search(query: str)`, `list_programs()`, `program_summary(ref: str)`, `staleness_report()`.

- [ ] **Step 1: Failing tests** — for each tool, seed the temp DB via repos, call the underlying `_fn(ro_conn, ...)` helper directly, assert shape. Example:

```python
def test_renewals_due_names_lines_of_cover(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    placements.create(conn, org_id=org.id, program_name="Acme Property 26-27",
                      period_from="2026-01-01", period_to="2026-10-01", status="bound")
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._renewals_due(ro, days=120)
    assert out[0]["account"] == "Acme"
    assert "lines_of_cover" in out[0]


def test_program_summary_unknown_ref_suggests(server_db):
    ro = db.connect_readonly(server_db)
    with pytest.raises(ValueError, match="no program matching"):
        mcpserver._program_summary(ro, "PLC-9999")
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement** — each tool is a thin `@server.tool()` closure calling a module-level `_fn(conn, ...)` (testable without the protocol). Bodies:

```python
def _renewals_due(conn: sqlite3.Connection, days: int = 120) -> list[dict]:
    from .services import renewals

    return [_renewal(item) for item in renewals.upcoming(conn, date.today(), days=days)]


def _search(conn: sqlite3.Connection, query: str) -> list[dict]:
    from .repo import search as search_repo

    return [
        {"kind": hit.kind, "title": hit.title, "snippet": hit.snippet}
        for hit in search_repo.search(conn, query)
    ]


def _list_programs(conn: sqlite3.Connection) -> list[dict]:
    from .repo import orgs, placements

    out = []
    for org in orgs.list_orgs(conn, kind="client"):
        for p in placements.for_org(conn, org.id):
            out.append({"ref": p.ref, "account": org.name,
                        "program": p.program_name, "period_to": p.period_to,
                        "status": p.status})
    return out


def _program_summary(conn: sqlite3.Connection, ref: str) -> dict:
    """Slim by design (Grant's call): posture and open-item counts, not the
    full structure/shares dump."""
    from . import sync
    from .money import format_cents
    from .repo import orgs, placements, submissions
    from .repo import tasks as tasks_repo

    placement = next(
        (p for org in orgs.list_orgs(conn, kind="client")
         for p in placements.for_org(conn, org.id)
         if p.ref == ref or p.program_name == ref),
        None,
    )
    if placement is None:
        candidates = [p["ref"] + " " + p["program"] for p in _list_programs(conn)[:5]]
        raise ValueError(f"no program matching {ref!r}; try one of: {candidates}")
    org = orgs.get(conn, placement.org_id)
    return {
        "account": org.name, "program": placement.program_name,
        "ref": placement.ref, "period": [placement.period_from, placement.period_to],
        "status": placement.status,
        "lines_of_cover": sync.line_labels(placement.program_path),
        "premium": format_cents(placement.total_premium)
        if placement.total_premium else None,
        "open_tasks": len([t for t in tasks_repo.open_tasks(conn, org_id=org.id)
                           if t.placement_id == placement.id]),
        "outstanding_submissions": len([
            s for s in submissions.outstanding_for_org(conn, org.id)
            if s["about_placement_id"] == placement.id]),
    }


def _staleness_report(conn: sqlite3.Connection) -> list[dict]:
    from .money import format_cents
    from .services import staleness

    return [
        {"account": s.org.name, "last_touch": s.last_interaction_on,
         "days_stale": s.days_stale,
         "premium": format_cents(s.premium) if s.premium else None}
        for s in staleness.stale_accounts(conn, date.today())
    ]
```

Check `staleness.stale_accounts` and `StaleAccount` field names (`services/staleness.py:15`) and mirror exactly. Register each in `_register_read_tools` with a docstring — the docstring IS the prompt the work models see; write it for them (what it returns, when to use it).

- [ ] **Step 4: Run** — `uv run pytest tests/test_mcpserver.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "mcp: read tools — renewals, search, programs, staleness"`

---

### Task 3: read tools — `open_items` and `pipeline_status`

**Files:**
- Modify: `src/bookkit/mcpserver.py`
- Test: `tests/test_mcpserver.py` (append)

**Interfaces:**
- Produces tools: `open_items(client: str | None = None)` — the centerpiece; `pipeline_status()`.
- Consumes: `services.export_open_items.compose` (export plan Task 6) for the per-client case — ONE definition of "open items" across export, TUI, and MCP.

- [ ] **Step 1: Failing tests**

```python
def test_open_items_scoped_reuses_export_composition(server_db):
    # seed a client + org task + need (reuse Task 2's seeding style)
    ro = db.connect_readonly(server_db)
    out = mcpserver._open_items(ro, client="Acme")
    assert out["account"] == "Acme"
    assert out["sections"][0]["rows"][0]["kind"] in ("Task", "Need", "Submission")


def test_open_items_bookwide_matches_attention_windows(server_db):
    ro = db.connect_readonly(server_db)
    out = mcpserver._open_items(ro, client=None)
    assert set(out) == {"tasks_due", "project_needs", "submissions_past_sla",
                        "onboarding_incomplete"}
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
def _open_items(conn: sqlite3.Connection, client: str | None = None) -> dict:
    from dataclasses import asdict

    from .services import export_open_items, onboarding

    today = date.today()
    if client is not None:
        org = _resolve_client(conn, client)
        return {
            "account": org.name,
            "sections": [
                {"label": s.label, "rows": [asdict(r) for r in s.rows]}
                for s in export_open_items.compose(conn, org.id, today)
            ],
        }
    from .dates import days_until
    from .repo import projects as projects_repo
    from .repo import tasks as tasks_repo
    from .services import sla

    return {
        "tasks_due": [
            {"ref": t.id, "title": t.title, "description": t.description,
             "due": t.due_on}
            for t in tasks_repo.open_tasks(conn, due_by=today.isoformat())
        ],
        "project_needs": [
            {"needed_by": n["needed_by"], "days": days_until(n["needed_by"], today),
             "account": n["org_name"], "line": n["line"],
             "project": n["project_name"], "status": n["status"]}
            for n in projects_repo.needs_due(conn, today, days=120)
        ],
        "submissions_past_sla": [
            {"market": late.market.name, "account": late.account.name,
             "days_out": late.days_out}
            for late in sla.past_sla(conn, today)
        ],
        "onboarding_incomplete": [
            {"account": org.name, "missing": missing}
            for org, missing in onboarding.incomplete_clients(conn, today)
        ],
    }


def _pipeline_status(conn: sqlite3.Connection) -> dict:
    from dataclasses import asdict

    from .services import pipeline, sla

    return {
        "stages": [asdict(m) for m in pipeline.metrics(conn)],
        "conversion": pipeline.conversion(conn),
        "submissions_past_sla": len(sla.past_sla(conn, date.today())),
    }
```

`StageMetrics` money fields (check `services/pipeline.py:63`): if any field is cents, format via `format_cents` instead of dumping raw ints — cents must never leave a tool raw.

`_resolve_client` (shared with the write tools, define it now):

```python
def _resolve_client(conn: sqlite3.Connection, ref_or_name: str):
    from rapidfuzz import process

    from .repo import orgs

    org = orgs.find(conn, ref_or_name) or orgs.find_by_name(conn, ref_or_name)
    if org is not None:
        return org
    names = [o.name for o in orgs.list_orgs(conn, kind="client")]
    close = process.extract(ref_or_name, names, limit=3, score_cutoff=60)
    hint = ", ".join(m[0] for m in close) if close else "none close"
    raise ValueError(f"no client matching {ref_or_name!r} — nearest: {hint}")
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git commit -m "mcp: open_items (shared composition with export) and pipeline_status"`

---

### Task 4: write tools — `log_activity`, `task_create`, `task_complete`

**Files:**
- Modify: `src/bookkit/mcpserver.py`
- Test: `tests/test_mcpserver.py` (append)

**Interfaces:**
- Produces tools: `log_activity(client, note, follow_up=None)`, `task_create(title, client=None, description=None, detail=None, due=None)`, `task_complete(task_ref)`.

- [ ] **Step 1: Failing tests**

```python
def test_log_activity_appends_interaction_with_provenance(server_db):
    # seed client "Acme"
    rw = db.connect(server_db)
    out = mcpserver._log_activity(rw, "Acme", "spoke to Ann re GL renewal",
                                  follow_up="friday")
    got = interactions.for_org(rw, out["org_id"])
    assert got[0].body == "spoke to Ann re GL renewal"
    events = rw.execute(  # test-only SQL is fine
        "SELECT * FROM event_log WHERE entity_id = ? AND field = 'source'",
        (got[0].id,)).fetchall()
    assert events and events[0]["new_value"] == "mcp"
    assert out["follow_up_task"] is not None


def test_task_complete_requires_exact_ref(server_db):
    rw = db.connect(server_db)
    with pytest.raises(KeyError):
        mcpserver._task_complete(rw, "not-a-real-id")


def test_write_tools_never_touch_ro_connection(server_db):
    ro = db.connect_readonly(server_db)
    with pytest.raises(sqlite3.OperationalError):
        mcpserver._task_create(ro, "should fail")
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
def _provenance(conn: sqlite3.Connection, entity: str, entity_id: str) -> None:
    from .repo import base

    base.log_event(conn, entity, entity_id, "source", None, "mcp")


def _log_activity(
    conn: sqlite3.Connection, client: str, note: str, follow_up: str | None = None
) -> dict:
    from .dates import parse_human_date
    from .repo import interactions
    from .repo import tasks as tasks_repo

    org = _resolve_client(conn, client)
    due = None
    if follow_up:
        parsed = parse_human_date(follow_up)
        if parsed is None:
            raise ValueError(f"cannot read a date from {follow_up!r}")
        due = parsed.isoformat()
    with db.transaction(conn):
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
            "follow_up_task": task.id if task else None}
```

CHECK `interactions.log`'s real signature at `repo/interactions.py:12` and mirror it exactly (positional/keyword names). The Interaction model needs `org_id`, `type` (use `"note"` — a valid `InteractionType`), `occurred_on`, `subject`, `body`.

```python
def _task_create(
    conn: sqlite3.Connection, title: str, client: str | None = None,
    description: str | None = None, detail: str | None = None,
    due: str | None = None,
) -> dict:
    from .dates import parse_human_date
    from .repo import tasks as tasks_repo

    fields: dict = {}
    if client:
        fields["org_id"] = _resolve_client(conn, client).id
    if description:
        fields["description"] = description
    if detail:
        fields["detail"] = detail  # markdown stored as-is
    if due:
        parsed = parse_human_date(due)
        if parsed is None:
            raise ValueError(f"cannot read a date from {due!r}")
        fields["due_on"] = parsed.isoformat()
    with db.transaction(conn):
        task = tasks_repo.create(conn, title, **fields)
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "title": task.title, "due": task.due_on}


def _task_complete(conn: sqlite3.Connection, task_ref: str) -> dict:
    """Status flip only. task_ref must be an exact id the model READ from
    open_items/today_brief — no fuzzy title matching, by design."""
    from .repo import tasks as tasks_repo

    with db.transaction(conn):
        task = tasks_repo.complete(conn, task_ref)  # KeyError on unknown → tool error
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "status": task.status, "completed_at": task.completed_at}
```

Register all three with docstrings that tell the work models the contract (e.g. task_complete: "requires the exact task ref from open_items — do not guess").

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git commit -m "mcp: write tools — log_activity, task_create, task_complete (additive, event-logged)"`

---

### Task 5: write tools — `client_create` (duplicate guard) and `enrich_field` (fill-blanks-only)

**Files:**
- Modify: `src/bookkit/mcpserver.py`
- Test: `tests/test_mcpserver.py` (append)

**Interfaces:**
- Produces tools: `client_create(name, contacts=[], note=None, tasks=[])`, `enrich_field(client, field, value, contact=None)`.

- [ ] **Step 1: Failing tests**

```python
def test_client_create_refuses_near_duplicate(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Henderson Group", kind="client")
    with pytest.raises(ValueError, match="Henderson Group"):
        mcpserver._client_create(rw, "Henderson Grp")


def test_client_create_bundles_contacts_and_tasks(server_db):
    rw = db.connect(server_db)
    out = mcpserver._client_create(
        rw, "Fresh Co",
        contacts=[{"first_name": "Ann", "last_name": "Lee", "email": "a@fresh.co"}],
        note="met at RIMS", tasks=[{"title": "send intro deck", "due": "9/1"}],
    )
    org = orgs.find(rw, out["org_ref"])
    assert contacts.for_org(rw, org.id)[0].email == "a@fresh.co"
    assert tasks_repo.open_tasks(rw, org_id=org.id)[0].title == "send intro deck"


def test_enrich_field_fills_blank_but_never_overwrites(server_db):
    rw = db.connect(server_db)
    org = orgs.create(rw, name="Acme", kind="client")
    out = mcpserver._enrich_field(rw, "Acme", "industry", "construction")
    assert out["set"] is True
    with pytest.raises(ValueError, match="already has"):
        mcpserver._enrich_field(rw, "Acme", "industry", "manufacturing")


def test_enrich_field_rejects_unknown_field(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Acme", kind="client")
    with pytest.raises(ValueError, match="not enrichable"):
        mcpserver._enrich_field(rw, "Acme", "status", "active")
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
_ENRICHABLE_ORG = {"owner", "industry", "naics", "hq_city", "hq_country",
                   "website", "domain", "legal_name", "notes"}
_ENRICHABLE_CONTACT = {"email", "phone", "mobile", "title", "linkedin", "notes"}
_DUP_CUTOFF = 87  # WRatio; 'Henderson Grp' vs 'Henderson Group' ≈ 95


def _client_create(
    conn: sqlite3.Connection, name: str,
    contacts_in: list[dict] | None = None, note: str | None = None,
    tasks_in: list[dict] | None = None,
) -> dict:
    from rapidfuzz import fuzz, process

    from .dates import parse_human_date
    from .repo import contacts, interactions, orgs
    from .repo import tasks as tasks_repo

    existing = {o.name: o for o in orgs.list_orgs(conn, kind="client")}
    match = process.extractOne(
        name, list(existing), scorer=fuzz.WRatio, score_cutoff=_DUP_CUTOFF)
    if match:
        dup = existing[match[0]]
        raise ValueError(
            f"possible duplicate of {dup.name} ({dup.ref}) — if this is the same "
            f"client use enrich_field/log_activity on it; if genuinely new, "
            f"retry with a more distinct name")

    with db.transaction(conn):
        org = orgs.create(conn, name=name, kind="client")
        _provenance(conn, "org", org.id)
        for c in contacts_in or []:
            contact = contacts.create(conn, org.id, **c)
            _provenance(conn, "contact", contact.id)
        if note:
            interaction = interactions.log(
                conn, org.id, type="note",
                occurred_on=date.today().isoformat(),
                subject=note[:80], body=note)
            _provenance(conn, "interaction", interaction.id)
        for t in tasks_in or []:
            due = None
            if t.get("due"):
                parsed = parse_human_date(t["due"])
                if parsed is None:
                    raise ValueError(f"cannot read a date from {t['due']!r}")
                due = parsed.isoformat()
            task = tasks_repo.create(
                conn, t["title"], org_id=org.id, due_on=due,
                description=t.get("description"), detail=t.get("detail"))
            _provenance(conn, "task", task.id)
    return {"org_ref": org.ref, "name": org.name,
            "contacts": len(contacts_in or []), "tasks": len(tasks_in or [])}
```

(Register the tool with parameter names `contacts`/`tasks` mapping to `contacts_in`/`tasks_in`. The transaction makes the whole bundle atomic: a bad task date rolls back the org too — surface that in the docstring.)

```python
def _enrich_field(
    conn: sqlite3.Connection, client: str, field: str, value: str,
    contact: str | None = None,
) -> dict:
    from .repo import contacts as contacts_repo
    from .repo import orgs

    org = _resolve_client(conn, client)
    if contact is not None:
        allowed, kind = _ENRICHABLE_CONTACT, "contact"
        people = contacts_repo.for_org(conn, org.id, active_only=False)
        target = next((c for c in people if c.name.lower() == contact.lower()), None)
        if target is None:
            names = [c.name for c in people]
            raise ValueError(f"no contact {contact!r} at {org.name}; have: {names}")
    else:
        allowed, kind, target = _ENRICHABLE_ORG, "org", org
    if field not in allowed:
        raise ValueError(f"{field!r} is not enrichable on a {kind}; allowed: {sorted(allowed)}")
    current = getattr(target, field)
    if current:
        raise ValueError(
            f"{org.name}{' / ' + target.name if contact else ''} already has "
            f"{field}={current!r} — fill-blanks-only, edits happen in the TUI")
    with db.transaction(conn):
        if kind == "org":
            orgs.update(conn, target.id, note="mcp enrich", **{field: value})
        else:
            contacts_repo.update(conn, target.id, note="mcp enrich", **{field: value})
        _provenance(conn, kind, target.id)
    return {"set": True, "entity": kind, "field": field, "value": value}
```

Normalization: route `value` through the same cleaner the forms use for that field where one exists (`normalize.clean_email` for email, `clean_phone` for phone/mobile, `clean_domain`, `clean_url`, `clean_naics` — see the `_CLEANERS` map in `tui/widgets/forms.py` and reuse `normalize` directly, NOT the tui module).

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `git commit -m "mcp: client_create with rapidfuzz duplicate guard; enrich_field fill-blanks-only"`

---

### Task 6: stdio round-trip smoke test + convention test

**Files:**
- Create: `tests/test_mcp_roundtrip.py`
- Modify: `tests/test_conventions.py` (created by the export plan's Task 10)

- [ ] **Step 1: Round-trip test**

```python
"""One real protocol round-trip: in-memory client ↔ FastMCP server."""

import pytest

from bookkit import db
from bookkit.mcpserver import build_server


@pytest.mark.anyio
async def test_list_tools_and_call_over_protocol(tmp_path):
    path = tmp_path / "rt.db"
    db.connect(path).close()
    server = build_server(path)

    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(
        server._mcp_server
    ) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert {"today_brief", "open_items", "task_create",
                "task_complete", "client_create", "enrich_field"} <= names
        result = await client.call_tool("task_create", {"title": "from the wire"})
        assert not result.isError
```

If `create_connected_server_and_client_session` or `server._mcp_server` moved in the installed SDK version, use the SDK's current in-memory testing helper (its own test suite demonstrates it); a subprocess-stdio fallback (`mcp.client.stdio` spawning `bookctl --db <path> mcp`) is acceptable but slower. `pytest.mark.anyio` may need the `anyio` pytest plugin — it ships with the `mcp` package's dependency tree; add `pytest-anyio`/`trio` config only if collection fails.

- [ ] **Step 2: Convention test** — append to `tests/test_conventions.py`:

```python
def test_no_raw_sql_in_mcpserver():
    text = (SRC / "mcpserver.py").read_text()
    assert ".execute(" not in text, "mcpserver must consume repo/services only"
```

- [ ] **Step 3: Run everything** — `uv run pytest -q 2>&1 | tail -3` → PASS.

- [ ] **Step 4: Commit** — `git commit -m "mcp: protocol round-trip smoke test + zero-SQL convention guard"`

---

### Task 7: ship it — wheelhouse, docs, connector settings

- [ ] **Step 1: Wheelhouse drill** — the `mcp` dependency (and its transitive deps) must land in the wheelhouse: follow the drill in towerkit's CLAUDE.md (wheel download, re-zip, release --clobber). Verify with the offline-install CI check (the repo already proves the published wheelhouse satisfies an offline install — commit 84d4786 added it; make sure it still passes with the new deps).

- [ ] **Step 2: README/usage note** — add a short section to the repo README (or wherever bookctl subcommands are documented — check what exists) with the work-machine connector settings:

```
Name:      bookkit
Command:   bookctl
Arguments: mcp
Env:       (none; set BOOKKIT_DB only if the DB lives off the default path —
            check db.default_db_path/env handling for the exact variable)
Mode:      both
```

Verify the env-var claim against `db.py`'s `default_db_path`/env fallback (tests/test_setup_and_scaffold.py::test_env_fallback shows the variable name) — document the REAL variable, or the `--db` args form (`Arguments: --db, /path/to/book.db, mcp` — note the CLI's global flag order: `--db` precedes the subcommand).

- [ ] **Step 3: Full gates one last time, both repos if towerkit changed** — then hand Grant the connector settings and a one-line smoke check he can run at work: `bookctl mcp` should start silently and wait on stdin (ctrl+c to exit); anything printed to stdout on startup is a bug.

- [ ] **Step 4: Commit** — `git commit -m "mcp: wheelhouse + connector documentation"`
