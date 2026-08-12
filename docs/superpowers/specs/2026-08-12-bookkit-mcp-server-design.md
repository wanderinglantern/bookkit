# bookkit MCP server — design

Date: 2026-08-12
Status: approved in conversation; pending spec review

## Goal

Expose bookkit's real data to the AI cowork environment on Grant's work
machine (GPT 5.2/5.3, Sonnet 4.6) through a stdio MCP server, so the work
assistant can answer renewal/attention/pipeline questions, surface open
items, and capture tasks and notes — without Grant hand-running `bookctl`
and pasting output.

The server is a working surface, not a book browser: workflow reads plus a
narrow append-style write surface. Full program structure stays in the TUI.

## Decisions made during brainstorming

- Access shape: read + capture (not read-only, not exported files).
- Write surface: notes/activities plus task create and task complete.
  Nothing else. No entity CRUD, no status nudges on renewals/submissions,
  no towerkit JSON access of any kind.
- Read surface: tasks/open items, pipeline & stalled work, staleness,
  renewals/attention, search. Stats (hit rate, exposure) are OUT for v1.
  Program detail is deliberately slim (summary, not structure/shares dump).
- Packaging: `bookctl mcp` subcommand inside bookkit using the official
  Python MCP SDK (`mcp` on PyPI), stdio transport. A separate
  `bookkit-mcp` package and a hand-rolled JSON-RPC server were considered
  and rejected (packaging overhead; protocol-maintenance burden and the
  libraries-over-hand-rolling rule).

## Architecture

New module `src/bookkit/mcpserver.py` (name avoids shadowing the `mcp`
SDK package), launched by `bookctl mcp`. It is a peer of `tui/`: another
consumer of `repo/` and `services/` with ZERO raw SQL — the existing
convention test extends to cover it.

Two SQLite connections:

- **Read connection**: opened with `file:...?mode=ro`. All read tools use
  it. Read-only is enforced by SQLite, not by convention.
- **Write connection**: the normal `db.connect` path. Only the three write
  tools touch it, always inside `with db.transaction(conn):`
  (the connection is autocommit; naked writes are a known foot-gun).

Money is formatted through `money.py` (cents internally, dollars in tool
output). Dates are ISO in output; date *input* (follow-ups, due dates)
parses through `dates.py` so MDY and two-digit-year=20xx rules hold and
dateparser cannot century-bump a past date.

## Tools — read

| Tool | Backing | Notes |
| --- | --- | --- |
| `today_brief()` | today/brief service | same content as `bookctl today`, structured |
| `renewals_due(days=120, program=None)` | `services/renewals.py` | per-line renewal clocks; lines of cover named in every row |
| `open_items(scope=None)` | tasks + project needs + submissions repos | open/overdue tasks, unmet needs, pending submissions; book-wide or scoped to one program/org. The centerpiece. |
| `pipeline_status()` | `services/pipeline.py`, `services/sla.py` | opportunities by stage; SLA breaches; stalled submissions |
| `staleness(days)` | `services/staleness.py` | contacts/orgs/programs untouched for N days |
| `search(query)` | `repo/search.py` | global full-text search |
| `list_programs()` | projects repo | navigation aid |
| `program_summary(ref)` | projects/placements repos | slim: name, org, renewal posture, lines with statuses, open-item counts. No shares/carrier structure dump. |

Attention semantics carry over unchanged: 120-day bucket-aligned windows;
overdue renewals and unmet needs never fall off.

## Tools — write

| Tool | Behavior |
| --- | --- |
| `log_activity(entity_ref, note, follow_up_date=None)` | append an interaction/note via `services/capture.py`; optional follow-up parses via `dates.py` |
| `task_create(title, detail=None, due_date=None, entity_ref=None)` | append a task; append-only. `detail` is long-form text (markdown allowed — stored as-is in the existing `Task.detail` column); `open_items` returns it so the work assistant sees full context |
| `task_complete(task_ref)` | status flip to done, nothing else editable |
| `client_create(name, kind, contacts=[], note=None, tasks=[])` | create org + contacts + opening note + follow-up tasks in ONE additive transaction. Duplicate guard first: name matched against existing orgs via `repo/aliases.py`; a likely duplicate REFUSES with candidates instead of creating. Creates the client only — program structure is TUI-wizard territory (towerkit sync pipeline), never work-model-driven. |
| `enrich_field(entity_ref, field, value)` | fill-blanks-only: sets a field ONLY if currently empty; if a value exists, refuses and returns it ("bookkit already has 555-0142"). Vocab-controlled fields validate against the models.py tuples. Never overwrites anything Grant typed. |

All writes go through the service layer, run in a transaction, and land in
`event_log` with a provenance note identifying the work connector as the
source. `task_complete` requires a concrete task reference the model
obtained from `open_items`/`search` — no fuzzy title matching server-side;
"close out the Henderson task" must resolve to an ID the model read.

Writes are additive (inserts + one status flip); no schema changes and no
destructive path, so no new backup machinery beyond event_log. The
importer-style pre-write DB snapshot is not needed here.

## Error handling

- Tool errors return MCP tool-error results with a plain-English message
  (bad ref, unparseable date, DB locked); the server never crashes the
  session on a bad argument.
- Write conflicts / `SQLITE_BUSY` while the TUI holds a transaction:
  busy_timeout on the write connection; if it still fails, the error says
  "bookkit is busy — try again," and nothing partial commits.
- Unknown `entity_ref`/`task_ref`: reject with candidates from search
  where cheap, never guess.

## Deployment

- New dependency: `mcp` (official Python SDK) → triggers the standing
  wheelhouse refresh drill on the landing commit.
- Work machine connector settings: Name `bookkit`, Command `bookctl`,
  Arguments `mcp`, no env secrets. Mode: both.
- Standing caveat, accepted: every tool result becomes context for the
  cowork models. The write design limits what they can do, not see.

## Testing

- Unit tests: each tool function against the seeded fixture DB (reads
  assert shape + formatting; writes assert row + event_log provenance;
  read connection proven unable to write).
- One stdio round-trip smoke test using the MCP SDK client: list tools,
  call a read tool, call `task_create`, assert the task exists.
- Convention test extended: `mcpserver` module contains no raw SQL.
- Gates as always: pytest, mypy, ruff before commit.

## Out of scope (v1)

Stats tools (hit rate, exposure), full program structure output, renewal/
submission status writes, task editing beyond completion, towerkit JSON
reads or writes, HTTP/SSE transport, auth (stdio inherits the local user).
