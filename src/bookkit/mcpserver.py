"""bookctl mcp — stdio MCP server for the work-machine cowork assistant.

Read tools run on a read-only connection (mode=ro — enforced by the
database). Exactly seven write tools exist — log_activity, task_create,
task_complete, client_create, enrich_field, request_item_received,
request_create — all additive, all inside db.transaction, all event-logged
with source=mcp. Keep this list in step with _register_write_tools; a
contract statement that has drifted is worse than none. stdout is protocol;
anything human goes to stderr (never print here).

NOTE ON SDK NAMING: the brief this module was built from named the
FastMCP-era API (`mcp.server.fastmcp.FastMCP`). The installed SDK (now
mcp==2.0.0; this module was originally built against 1.28.1, which had
already renamed the class) exposes it as `MCPServer`, at
`mcp.server.mcpserver.MCPServer` (also re-exported as `mcp.server.MCPServer`).
The constructor kwargs used here (`name`, `instructions`), the `.tool()`
decorator, `._tool_manager.list_tools()`, and `.run()` (stdio by default)
are unchanged in shape — only the class name and import path moved, so this
module follows the installed SDK rather than the brief's example.

NOTE ON `async def` TOOL WRAPPERS: every `@server.tool()`-registered
closure below is declared `async def`, even though its body is a plain
synchronous call into a `_verb(conn, ...)` helper with no `await`. This
is required, not stylistic. The SDK's tool dispatcher (see
`mcp/server/mcpserver/tools/base.py` + `utilities/func_metadata.py` in
the installed package) runs a *sync* tool callable via
`anyio.to_thread.run_sync` — off the event-loop thread — to avoid
blocking it; an `async def` callable is instead awaited directly on the
event-loop thread. Both `ro` and `rw` are opened once, in
`build_server()`, on whatever thread calls it (the event loop's thread
under `serve()`/stdio). `sqlite3` connections are single-thread-bound by
default (`check_same_thread=True`, unset here), so a sync tool handed to
`to_thread.run_sync` raises "SQLite objects created in a thread can only
be used in that same thread" on its very first read or write — this
reproduced on every call once exercised through a real protocol
round-trip (`tests/test_mcp_roundtrip.py`), not through the
call-the-`_verb`-function-directly unit tests in `test_mcpserver.py`,
which never go through the SDK's dispatcher. Keeping the wrappers
`async def` (with sync bodies) is the minimal fix that stays inside this
module: it does not touch `db.py`'s `check_same_thread` default (shared
by tui/repo/imports, out of this module's scope) and does not introduce
real concurrency — SQLite calls still run synchronously, just on the
loop thread that owns the connections, matching this server's
single-client stdio usage.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import db
from .models import EventBatch, RfiItem, RfiRequest
from .services.renewals import RenewalItem

# score_cutoff for the client_create duplicate guard (rapidfuzz WRatio,
# 0-100). 'Henderson Grp' vs 'Henderson Group' scores ~95; this is
# deliberately loose enough to catch near-misses while staying below scores
# for genuinely distinct short names.
_DUP_CUTOFF = 87

_ENRICHABLE_ORG = {"owner", "industry", "naics", "hq_city", "hq_country",
                    "website", "domain", "legal_name", "notes"}
_ENRICHABLE_CONTACT = {"email", "phone", "mobile", "title", "linkedin", "notes"}


def build_server(db_path: Path | str | None = None) -> MCPServer:
    server = MCPServer(
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


def _register_read_tools(server: MCPServer, ro: sqlite3.Connection) -> None:
    @server.tool()
    async def today_brief() -> dict[str, Any]:
        """Today's working brief: tasks due or overdue today, renewals in the
        120-day window, project needs, stale accounts, submissions past SLA.
        tasks_due here is due-or-overdue-today only; use open_items for the
        full task list (including undated and future-due tasks)."""
        return _today_brief(ro)

    @server.tool()
    async def renewals_due(days: int = 120) -> list[dict[str, Any]]:
        """Placements needing renewal attention within `days` (default 120),
        soonest first, plus anything already overdue and not yet renewed.
        Each entry names its lines of cover — a program name alone is never
        enough context to act on. Use this to answer "what's coming up" or
        "what's overdue" instead of guessing from program names."""
        return _renewals_due(ro, days=days)

    @server.tool()
    async def search(query: str) -> list[dict[str, Any]]:
        """Full-text search across orgs, contacts, and interactions, ranked
        and grouped by kind ('org' | 'contact' | 'interaction'). Use this
        FIRST to resolve a client/market/person name or a fuzzy topic into
        real records before calling any other tool that takes an id or ref —
        never guess one."""
        return _search(ro, query)

    @server.tool()
    async def list_programs() -> list[dict[str, Any]]:
        """Every client program (placement) on the book: ref, account,
        program name, period end, and status. Use this to get an overview of
        the whole book or to find a program's ref for program_summary when
        search doesn't already have it."""
        return _list_programs(ro)

    @server.tool()
    async def program_summary(ref: str) -> dict[str, Any]:
        """Posture on ONE program: account, period, status, lines of cover,
        premium, plus counts of its open tasks and outstanding submissions.
        Deliberately slim — it does NOT dump the full program structure or
        market shares. `ref` matches the placement ref or the exact program
        name; an unmatched ref raises with candidate refs/names to retry
        with."""
        return _program_summary(ro, ref)

    @server.tool()
    async def staleness_report() -> list[dict[str, Any]]:
        """Active client accounts that have gone quiet: no logged interaction
        in over 60 days, sorted with the most neglected (days stale × premium)
        first. Use this to find who needs a check-in call before they lapse
        or shop the account elsewhere."""
        return _staleness_report(ro)

    @server.tool()
    async def open_items(client: str | None = None) -> dict[str, Any]:
        """Open items for ONE client (`client` = exact client name or ref; on
        a miss the error lists the nearest candidates) — the same
        composition used for the client export deliverable, so this matches
        what a client would be handed. Omit `client` for the book-wide view:
        ALL open tasks (undated and future-due included, not just due-today),
        unmet project needs, submissions past SLA, and incomplete onboarding,
        across the whole book (project needs use the same 120-day attention
        window as today_brief). Also carries "information_requests": the
        outstanding questions and documents clients still owe us — ALL of them,
        regardless of due date (undated and far-future asks included), in both
        the per-client and the book-wide view. requests_to_chase is the
        narrower 120-day attention view of the same requests."""
        return _open_items(ro, client=client)

    @server.tool()
    async def requests_to_chase(days: int = 120) -> list[dict[str, Any]]:
        """Information requests whose answers are still OUTSTANDING within
        `days` (default 120), soonest first, plus anything already overdue.
        These are things the CLIENT owes US — questions and documents an
        underwriter asked for — NOT our own tasks: nothing here gets done at
        this desk, it gets chased. One entry is one REQUEST, which is what you
        chase with one email; `open_count`/`total_count` say how much of it is
        still outstanding and `days` goes negative once it is overdue. Call
        request_items with a request_ref to see the individual asks inside
        one."""
        return _requests_to_chase(ro, days=days)

    @server.tool()
    async def request_items(request_ref: str) -> dict[str, Any]:
        """Everything on ONE information request — the header plus every item
        (question or document), answered or not — so you can say exactly what
        a client is still owing. `request_ref` MUST be a ref read from
        requests_to_chase or open_items ("RFI-0001", case-insensitive); an
        unknown ref raises with real refs to retry with, never a guess. Each
        item's `item_ref` is what request_item_received takes; `needed_by` is
        the effective due (the item's own, else the request's)."""
        return _request_items(ro, request_ref)

    @server.tool()
    async def pipeline_status() -> dict[str, Any]:
        """Opportunity pipeline health: count, total and probability-weighted
        premium, and average days-in-stage per stage (identified through
        won/lost); win rate and per-gate advance rates; count of submissions
        past SLA. Money is formatted dollars."""
        return _pipeline_status(ro)


def _register_write_tools(server: MCPServer, rw: sqlite3.Connection) -> None:
    @server.tool()
    async def log_activity(
        client: str, note: str, follow_up: str | None = None
    ) -> dict[str, Any]:
        """Log a client interaction (call, email, meeting, site note) — additive
        and event-logged; nothing existing is touched. `client` resolves the same
        way as every other client-scoped tool (exact client name or ref; on a
        miss the error lists the nearest candidates — never guess an id). Pass
        `follow_up` as any human date ("friday", "+2w", "2026-09-01") to also
        create a follow-up task in the same transaction; omit it to just log
        the note."""
        return _log_activity(rw, client, note, follow_up=follow_up)

    @server.tool()
    async def recent_activity(client: str, limit: int = 20) -> list[dict[str, Any]]:
        """A client's logged activity, newest first, each with the
        `interaction_ref` that `activity_delete` takes. Use this to FIND an
        activity you need to correct — `search` returns no refs, so it cannot
        name one for you. `client` resolves like every other client-scoped
        tool (exact client name or ref; on a miss the error lists the nearest
        candidates — never guess an id)."""
        return _recent_activity(rw, client, limit=limit)

    @server.tool()
    async def activity_delete(interaction_ref: str) -> dict[str, Any]:
        """Remove a logged activity that should not be there — typically one
        THIS server logged in error. `interaction_ref` must be an exact ref
        read from `recent_activity` or returned by `log_activity`; an unknown
        or already-deleted ref is an error, never a silent no-op. The delete
        is soft and event-logged, so `u` in the TUI restores it."""
        return _activity_delete(rw, interaction_ref)

    @server.tool()
    async def task_create(
        title: str,
        client: str | None = None,
        description: str | None = None,
        detail: str | None = None,
        category: str | None = None,
        due: str | None = None,
    ) -> dict[str, Any]:
        """Create a task — additive and event-logged. `client` links it to an
        account (exact client name or ref; on a miss the error lists the
        nearest candidates; omit for a book-wide task). `description` is a
        short one-line summary; `detail` holds longer markdown notes. `category`
        is a freeform grouping label — prefer an existing one over inventing a
        new one; call open_items first to see what's already in use. `due`
        accepts any human date ("friday", "+2w", "2026-09-01")."""
        return _task_create(
            rw, title, client=client, description=description, detail=detail,
            category=category, due=due,
        )

    @server.tool()
    async def task_complete(task_ref: str) -> dict[str, Any]:
        """Mark a task done — a status flip only, event-logged. `task_ref` MUST
        be the exact id read from open_items or today_brief; this tool never
        fuzzy-matches a title — guessing a ref risks completing the wrong task."""
        return _task_complete(rw, task_ref)

    @server.tool()
    async def client_create(
        name: str,
        contacts: list[dict[str, Any]] | None = None,
        note: str | None = None,
        tasks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a new client — additive and event-logged. First checks for a
        near-duplicate existing client name (fuzzy match); refuses and names
        the match rather than risk a second record for the same account. On a
        clear name, creates the org plus any bundled `contacts` (list of
        {first_name, last_name, email, ...}), an opening `note`, and any
        `tasks` (list of {title, due, description, detail} — `due` accepts any
        human date) — ALL inside one transaction, so the whole bundle is
        atomic: a bad task date rolls back the org too, nothing partial is
        left behind. Call search first to be sure this client isn't already
        on the book under a different spelling."""
        return _client_create(rw, name, contacts_in=contacts, note=note, tasks_in=tasks)

    @server.tool()
    async def enrich_field(
        client: str, field: str, value: str, contact: str | None = None
    ) -> dict[str, Any]:
        """Fill ONE blank field on a client org (or, with `contact`, on one of
        its contacts) — additive and event-logged. Fill-blanks-only: refuses
        if the field is already set, naming the current value — edits to an
        already-populated field happen in the TUI, not here. `client`
        resolves the same way as every other client-scoped tool. `value` is
        normalized through the same cleaner the forms use for that field
        (email/phone/url/domain/naics) before being stored. Enrichable org
        fields: owner, industry, naics, hq_city, hq_country, website, domain,
        legal_name, notes. Enrichable contact fields: email, phone, mobile,
        title, linkedin, notes."""
        return _enrich_field(rw, client, field, value, contact=contact)

    @server.tool()
    async def request_item_received(
        item_ref: str, response: str | None = None
    ) -> dict[str, Any]:
        """Record that ONE item on an information request came back: marks it
        received and dates it today, event-logged. `item_ref` MUST be the
        exact id read from request_items or open_items; this tool never
        fuzzy-matches a prompt — guessing risks closing out the wrong ask.
        Pass `response` to store the client's actual answer alongside it (a
        document just arriving needs no response). Returns the parent
        request's remaining open_count/total_count, so you can report "3 of 12
        still outstanding" without a second call."""
        return _request_item_received(rw, item_ref, response=response)

    @server.tool()
    async def request_create(
        client: str,
        title: str,
        items: list[str],
        market: str | None = None,
        due_on: str | None = None,
        placement_ref: str | None = None,
        project_ref: str | None = None,
    ) -> dict[str, Any]:
        """File a new information request against a client — additive and
        event-logged. This is what an underwriter's list of questions becomes:
        `items` is that list, one ask per string, and a single string holding a
        pasted numbered or bulleted block is split into one item per line with
        its "1." / "-" markers stripped. `client` resolves the same way as
        every other client-scoped tool. `market` names the market that asked
        (on a miss the error lists the nearest markets — never guess).
        `due_on` accepts any human date ("friday", "+2w", "2026-09-01") and
        becomes the whole request's deadline. `placement_ref` and
        `project_ref` scope the ask to one program or one project and are
        mutually exclusive; each must belong to that client. Everything lands
        in ONE transaction, so a bad date leaves nothing behind."""
        return _request_create(
            rw, client, title, items, market=market, due_on=due_on,
            placement_ref=placement_ref, project_ref=project_ref,
        )


def _today_brief(conn: sqlite3.Connection) -> dict[str, Any]:
    from .dates import days_until
    from .money import format_cents_compact
    from .repo import projects as projects_repo
    from .repo import tasks as tasks_repo
    from .services import renewals, sla, staleness

    today = date.today()
    iso = today.isoformat()
    return {
        "date": iso,
        "tasks_due": [
            {
                "ref": t.id, "title": t.title, "description": t.description,
                "due": t.due_on,
                "days_overdue": max(0, -days_until(t.due_on, today)) if t.due_on else 0,
            }
            for t in tasks_repo.open_tasks(conn, due_by=iso)
        ],
        "renewals_120d": [_renewal(item) for item in renewals.upcoming(conn, today, days=120)],
        "project_needs": [
            _project_need(need, today) for need in projects_repo.needs_due(conn, today, days=120)
        ],
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


def _renewal(item: RenewalItem) -> dict[str, Any]:
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


def _project_need(row: sqlite3.Row, today: date) -> dict[str, Any]:
    from .dates import days_until
    from .money import format_cents_compact

    needed_by = row["needed_by"]
    d = days_until(needed_by, today) if needed_by else 0
    return {
        "needed_by": needed_by,
        "days_overdue": max(0, -d),
        "account": row["org_name"],
        "project": row["project_name"],
        "line": row["line"],
        "status": row["status"],
        "premium_indication": format_cents_compact(row["premium_indication_cents"])
        if row["premium_indication_cents"] else None,
    }


def _renewals_due(conn: sqlite3.Connection, days: int = 120) -> list[dict[str, Any]]:
    from .services import renewals

    return [_renewal(item) for item in renewals.upcoming(conn, date.today(), days=days)]


def _search(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    from .repo import search as search_repo

    return [
        {"kind": hit.kind, "title": hit.title, "snippet": hit.snippet}
        for hit in search_repo.search(conn, query)
    ]


def _list_programs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from .repo import orgs, placements

    out = []
    for org in orgs.list_orgs(conn, kind="client"):
        for p in placements.for_org(conn, org.id):
            out.append({"ref": p.ref, "account": org.name,
                        "program": p.program_name, "period_to": p.period_to,
                        "status": p.status})
    return out


def _program_summary(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
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
        "open_tasks": len([t for t in tasks_repo.open_tasks_for_client(conn, org.id)
                           if t.placement_id == placement.id]),
        "outstanding_submissions": len([
            s for s in submissions.outstanding_for_org(conn, org.id)
            if s["about_placement_id"] == placement.id]),
    }


def _staleness_report(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from .money import format_cents
    from .services import staleness

    return [
        {"account": s.org.name, "last_touch": s.last_interaction_on,
         "days_stale": s.days_stale,
         "premium": format_cents(s.premium) if s.premium else None}
        for s in staleness.stale_accounts(conn, date.today())
    ]


def _resolve_client(conn: sqlite3.Connection, ref_or_name: str) -> Any:
    """Shared by every client-scoped read tool and every client-scoped write
    tool (all but task_complete and request_item_received, which take refs) —
    ONE resolution path so a bad name always fails the same way, with
    close-match hints instead of a raw KeyError."""
    from rapidfuzz import process

    from .repo import orgs

    org = orgs.find(conn, ref_or_name) or orgs.find_by_name(conn, ref_or_name)
    if org is not None:
        return org
    names = [o.name for o in orgs.list_orgs(conn, kind="client")]
    close = process.extract(ref_or_name, names, limit=3, score_cutoff=60)
    hint = ", ".join(m[0] for m in close) if close else "none close"
    raise ValueError(f"no client matching {ref_or_name!r} — nearest: {hint}")


@contextmanager
def _open_batch(
    conn: sqlite3.Connection, *, tool: str, summary: str, org_id: str | None = None
) -> Iterator[EventBatch]:
    """One MCP call, one undo unit. Opens the transaction AND the batch, so
    every event written inside is grouped and revertible together (and counted
    against the blast cap)."""
    from .repo import batches as batches_repo

    batch_id = batches_repo.new_batch_id()
    with db.transaction(conn, batch=db.BatchState(batch_id=batch_id)):
        yield batches_repo.create(
            conn, batch_id=batch_id, source="mcp", tool=tool,
            summary=summary, org_id=org_id,
        )


def _provenance(conn: sqlite3.Connection, entity: str, entity_id: str) -> None:
    from .repo import base

    base.log_event(conn, entity, entity_id, "source", None, "mcp")


def _log_activity(
    conn: sqlite3.Connection, client: str, note: str, follow_up: str | None = None
) -> dict[str, Any]:
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


def _recent_activity(
    conn: sqlite3.Connection, client: str, limit: int = 20
) -> list[dict[str, Any]]:
    """A client's logged activity, newest first, WITH refs. search() returns
    no ids by design, so without this a model could only ever delete an
    activity it had just created itself — a mistake found later was
    unnameable."""
    from .repo import interactions

    org = _resolve_client(conn, client)
    return [
        {"interaction_ref": i.id, "occurred_on": i.occurred_on, "type": i.type,
         "subject": i.subject, "body": i.body}
        for i in interactions.for_org(conn, org.id, limit=limit)
    ]


def _activity_delete(conn: sqlite3.Connection, interaction_ref: str) -> dict[str, Any]:
    """Remove a logged activity — the correction path when this server wrote
    something wrong. Soft delete, so `u` in the TUI restores it and nothing
    is destroyed.

    The get() first is not redundant: base.soft_delete issues an UPDATE that
    matches zero rows for an unknown or already-deleted id and still logs an
    event, so it would report success for a delete that deleted nothing.
    interactions.get filters on aliveness and raises KeyError instead."""
    from .repo import interactions

    interaction = interactions.get(conn, interaction_ref)  # KeyError → tool error
    with _open_batch(
        conn, tool="activity_delete", org_id=interaction.org_id,
        summary=f"deleted activity: {interaction.subject}",
    ) as batch:
        interactions.delete(conn, interaction.id)
        _provenance(conn, "interaction", interaction.id)
    return {"interaction_ref": interaction.id, "deleted": True,
            "subject": interaction.subject, "undo": "u in the TUI restores it",
            "batch": batch.ref}


def _task_create(
    conn: sqlite3.Connection, title: str, client: str | None = None,
    description: str | None = None, detail: str | None = None,
    category: str | None = None, due: str | None = None,
) -> dict[str, Any]:
    from .dates import parse_human_date
    from .repo import tasks as tasks_repo

    fields: dict[str, Any] = {}
    if client:
        fields["org_id"] = _resolve_client(conn, client).id
    if description:
        fields["description"] = description
    if detail:
        fields["detail"] = detail  # markdown stored as-is
    if category:
        fields["category"] = category  # freeform grouping label; suggest existing
        # values to the model: the tool docstring says "prefer an existing
        # category — call open_items first to see what's in use"
    if due:
        parsed = parse_human_date(due)
        if parsed is None:
            raise ValueError(f"cannot read a date from {due!r}")
        fields["due_on"] = parsed.isoformat()
    with _open_batch(
        conn, tool="task_create", org_id=fields.get("org_id"),
        summary=f"created task: {title}",
    ) as batch:
        task = tasks_repo.create(conn, title, **fields)
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "title": task.title, "due": task.due_on,
            "batch": batch.ref}


def _task_complete(conn: sqlite3.Connection, task_ref: str) -> dict[str, Any]:
    """Status flip only. task_ref must be an exact id the model READ from
    open_items/today_brief — no fuzzy title matching, by design."""
    from .repo import tasks as tasks_repo

    task = tasks_repo.get(conn, task_ref)  # KeyError on unknown → tool error
    with _open_batch(
        conn, tool="task_complete", org_id=task.org_id,
        summary=f"completed task: {task.title}",
    ) as batch:
        task = tasks_repo.complete(conn, task_ref)
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "status": task.status,
            "completed_at": task.completed_at, "batch": batch.ref}


def _client_create(
    conn: sqlite3.Connection, name: str,
    contacts_in: list[dict[str, Any]] | None = None, note: str | None = None,
    tasks_in: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The whole bundle (org, contacts, opening note, tasks) is ONE
    transaction — a bad task date (or any other failure partway through)
    rolls the org itself back too, never leaving a half-created client."""
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

    # org_id=None: the org is created INSIDE the batch, so its id is not
    # known when the batch row is written.
    with _open_batch(
        conn, tool="client_create", summary=f"created client {name}",
    ) as batch:
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
            "contacts": len(contacts_in or []), "tasks": len(tasks_in or []),
            "batch": batch.ref}


# Route enrich_field values through the SAME cleaners the forms use
# (tui/widgets/forms.py `_CLEANERS`) so an MCP-entered email/phone/url/domain
# ends up identical to one typed through the TUI. `normalize` is imported
# directly here (never the tui module — mcpserver has no TUI dependency).
_FIELD_CLEANERS = {
    "email": "clean_email",
    "phone": "clean_phone",
    "mobile": "clean_phone",
    "website": "clean_url",
    "domain": "clean_domain",
    "naics": "clean_naics",
    "linkedin": "clean_linkedin",
    "notes": None,  # textarea: stored as-is, same as the forms' passthrough
}


def _clean_field_value(field: str, value: str) -> str:
    from . import normalize

    cleaner_name = _FIELD_CLEANERS.get(field, "clean_text")
    if cleaner_name is None:
        return value
    cleaner = getattr(normalize, cleaner_name)
    return cleaner(value)  # type: ignore[no-any-return]


def _enrich_field(
    conn: sqlite3.Connection, client: str, field: str, value: str,
    contact: str | None = None,
) -> dict[str, Any]:
    """Fill-blanks-only: refuses to touch a field that already has a value
    (edits happen in the TUI). Additive, single-field, event-logged."""
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
    cleaned = _clean_field_value(field, value)
    with _open_batch(
        conn, tool="enrich_field", org_id=org.id,
        summary=f"set {field} on {org.name}"
        + (f" / {target.name}" if contact else ""),
    ) as batch:
        if kind == "org":
            orgs.update(conn, target.id, note="mcp enrich", **{field: cleaned})
        else:
            contacts_repo.update(conn, target.id, note="mcp enrich", **{field: cleaned})
        _provenance(conn, kind, target.id)
    return {"set": True, "entity": kind, "field": field, "value": cleaned,
            "batch": batch.ref}


def _request_item_received(
    conn: sqlite3.Connection, item_ref: str, response: str | None = None
) -> dict[str, Any]:
    """Status flip (plus an optional answer) on ONE item. item_ref must be an
    exact id the model READ from request_items/open_items — no fuzzy prompt
    matching, same contract as _task_complete."""
    from .repo import rfi as rfi_repo
    from .services import rfi as rfi_svc

    # KeyError on an unknown id → tool error, never a silently-created row
    found = rfi_repo.get_item(conn, item_ref)
    with _open_batch(
        conn, tool="request_item_received",
        org_id=rfi_repo.get_request(conn, found.request_id).org_id,
        summary=f"received an item: {found.prompt[:60]}",
    ) as batch:
        item = rfi_svc.mark_received(conn, item_ref, date.today().isoformat())
        if response:
            item = rfi_repo.update_item(conn, item.id, response=response)
        _provenance(conn, "rfi_item", item.id)
        request = rfi_repo.get_request(conn, item.request_id)
        open_count = rfi_repo.open_item_count(conn, request.id)
        total_count = rfi_repo.item_count(conn, request.id)
    return {
        "item_ref": item.id,
        "status": item.status,
        "received_on": item.received_on,
        "response": item.response,
        "request_ref": request.ref,
        "open_count": open_count,
        "total_count": total_count,
        "batch": batch.ref,
    }


def _resolve_market(conn: sqlite3.Connection, ref_or_name: str) -> Any:
    """_resolve_client's twin for the other side of the book: a market is
    named, never guessed, and a miss comes back with the nearest markets."""
    from rapidfuzz import process

    from .repo import orgs

    org = orgs.find(conn, ref_or_name) or orgs.find_by_name(conn, ref_or_name)
    if org is not None and org.kind == "market":
        return org
    names = [o.name for o in orgs.list_orgs(conn, kind="market")]
    close = process.extract(ref_or_name, names, limit=3, score_cutoff=60)
    hint = ", ".join(m[0] for m in close) if close else "none close"
    raise ValueError(f"no market matching {ref_or_name!r} — nearest: {hint}")


def _request_create(
    conn: sqlite3.Connection, client: str, title: str, items: list[str],
    market: str | None = None, due_on: str | None = None,
    placement_ref: str | None = None, project_ref: str | None = None,
) -> dict[str, Any]:
    """An underwriter's emailed list, filed. Request and every item land in
    ONE transaction, so a bad date or an unknown market never leaves a headless
    request behind."""
    from .dates import parse_human_date
    from .repo import placements
    from .repo import projects as projects_repo
    from .repo import rfi as rfi_repo

    # A pasted numbered list must be cleaned identically here and in the TUI's
    # paste box, so this shares that splitter rather than re-deriving the
    # regex. rfi_paste is pure `re` — importing it pulls in no TUI machinery.
    from .tui.widgets.rfi_paste import split_items

    if placement_ref and project_ref:
        raise ValueError(
            "a request is scoped to a placement OR a project, never both")
    org = _resolve_client(conn, client)
    fields: dict[str, Any] = {}
    if market:
        fields["market_org_id"] = _resolve_market(conn, market).id
    if due_on:
        parsed = parse_human_date(due_on)
        if parsed is None:
            raise ValueError(f"cannot read a date from {due_on!r}")
        fields["due_on"] = parsed.isoformat()
    if placement_ref:
        placement = placements.find(conn, placement_ref)
        if placement is None or placement.org_id != org.id:
            have = [p.ref for p in placements.for_org(conn, org.id)]
            raise ValueError(
                f"no placement {placement_ref!r} on {org.name} — it has: {have}")
        fields["placement_id"] = placement.id
    if project_ref:
        # Scanning the client's own projects IS the ownership check: another
        # client's ref simply is not in this list.
        wanted = project_ref.lower()
        project = next(
            (p for p in projects_repo.projects_for_org(conn, org.id)
             if p.ref.lower() == wanted or p.name.lower() == wanted),
            None,
        )
        if project is None:
            have = [f"{p.ref} {p.name}" for p in projects_repo.projects_for_org(conn, org.id)]
            raise ValueError(
                f"no project {project_ref!r} on {org.name} — it has: {have}")
        fields["project_id"] = project.id

    prompts = [prompt for text in items for prompt in split_items(text)]
    with _open_batch(
        conn, tool="request_create", org_id=org.id,
        summary=f"created request {title!r} for {org.name}",
    ) as batch:
        request = rfi_repo.create_request(
            conn, org.id, title, date.today().isoformat(), **fields)
        _provenance(conn, "rfi_request", request.id)
        for prompt in prompts:
            # insertion order is paste order: items_for_request breaks the
            # created_at tie on rowid, so a whole list pasted in one second
            # still reads back in the order the underwriter wrote it
            item = rfi_repo.add_item(conn, request.id, prompt)
            _provenance(conn, "rfi_item", item.id)
    return {"request_ref": request.ref, "account": org.name,
            "item_count": len(prompts), "batch": batch.ref}


def _requests_to_chase(conn: sqlite3.Connection, days: int = 120) -> list[dict[str, Any]]:
    """One entry per REQUEST — the unit you chase with one email — not per
    item. Order is the service's (earliest effective due, then ref)."""
    from .services import rfi as rfi_svc

    return [
        {
            "request_ref": chase.request.ref,
            "account": chase.org_name,
            "title": chase.request.title,
            "asked_by": chase.market_name,
            "scope": rfi_svc.scope_label(conn, chase.request),
            "needed_by": chase.earliest_due,
            "days": chase.days_remaining,
            "open_count": chase.open_count,
            "total_count": chase.total_count,
        }
        for chase in rfi_svc.outstanding_requests(conn, date.today(), days=days)
    ]


def _resolve_request(conn: sqlite3.Connection, request_ref: str) -> RfiRequest:
    """Refs only, case-insensitively — the same never-guess discipline as
    _resolve_client, with real refs named on a miss."""
    from .repo import rfi as rfi_repo

    request = rfi_repo.find_request(conn, request_ref)
    if request is None:
        known = rfi_repo.known_refs(conn)
        raise ValueError(
            f"no information request matching {request_ref!r} — "
            f"known: {known if known else 'none on the book'}")
    return request


def _rfi_open_item(
    request: RfiRequest, item: RfiItem, market_name: str | None,
    account: str | None = None,
) -> dict[str, Any]:
    """One outstanding ask, as open_items lists it. `account` is set only on
    the book-wide branch (the per-client branch already names the account)."""
    row: dict[str, Any] = {"request_ref": request.ref}
    if account is not None:
        row["account"] = account
    row.update({
        "title": request.title,
        "item_ref": item.id,
        "prompt": item.prompt,
        "kind": item.kind,
        "needed_by": item.due_on or request.due_on,  # effective due
        "asked_by": market_name,
    })
    return row


def _client_information_requests(
    conn: sqlite3.Connection, org_id: str
) -> list[dict[str, Any]]:
    """EVERY outstanding ask this client owes, undated ones included — this
    branch is the client-facing composition, and an undated question is still
    something they owe. The book-wide branch below answers the same question
    the same way (unwindowed); only requests_to_chase is windowed."""
    from .repo import orgs
    from .repo import rfi as rfi_repo

    out: list[dict[str, Any]] = []
    for request in rfi_repo.requests_for_org(conn, org_id):
        if request.cancelled_at:
            continue
        market = (orgs.find(conn, request.market_org_id)
                  if request.market_org_id else None)
        out.extend(
            _rfi_open_item(request, item, market.name if market else None)
            for item in rfi_repo.items_for_request(conn, request.id)
            if item.status == "outstanding"
        )
    return out


def _book_information_requests(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The per-client branch's shape, book-wide, plus the account name — and
    like that branch it is UNWINDOWED: every outstanding ask on every live
    uncancelled request, undated and far-future included. open_items is the
    "everything outstanding" tool by contract; the 120-day view is
    requests_to_chase, which keeps using services.rfi.outstanding_requests."""
    from .repo import rfi as rfi_repo

    out: list[dict[str, Any]] = []
    for row in rfi_repo.outstanding_request_rows(conn):
        request = RfiRequest.model_validate(
            {k: row[k] for k in row.keys() if k in RfiRequest.model_fields})
        out.extend(
            _rfi_open_item(request, item, row["market_name"],
                           account=row["org_name"])
            for item in rfi_repo.items_for_request(conn, request.id)
            if item.status == "outstanding"
        )
    return out


def _request_items(conn: sqlite3.Connection, request_ref: str) -> dict[str, Any]:
    """One request, whole: header plus every item, answered or not."""
    from .repo import orgs
    from .repo import rfi as rfi_repo
    from .services import rfi as rfi_svc

    request = _resolve_request(conn, request_ref)
    org = orgs.get(conn, request.org_id)
    # find, not get: a merged-away market blanks the name rather than
    # exploding the whole read (repo/rfi.outstanding_rows makes the same call)
    market = orgs.find(conn, request.market_org_id) if request.market_org_id else None
    return {
        "request_ref": request.ref,
        "account": org.name,
        "title": request.title,
        "asked_by": market.name if market else None,
        "scope": rfi_svc.scope_label(conn, request),
        "requested_on": request.requested_on,
        "due_on": request.due_on,
        "cancelled": request.cancelled_at is not None,
        "items": [
            {
                "item_ref": item.id,
                "kind": item.kind,
                "prompt": item.prompt,
                "detail": item.detail,
                "category": item.category,
                "needed_by": item.due_on or request.due_on,  # effective due
                "status": item.status,
                "received_on": item.received_on,
                "response": item.response,
            }
            for item in rfi_repo.items_for_request(conn, request.id)
        ],
    }


def _open_items(conn: sqlite3.Connection, client: str | None = None) -> dict[str, Any]:
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
            "information_requests": _client_information_requests(conn, org.id),
        }

    from .dates import days_until
    from .repo import projects as projects_repo
    from .repo import tasks as tasks_repo
    from .services import sla

    return {
        # ALL open tasks — undated and future-due included, not filtered to
        # due-by-today (that narrower view is today_brief's job). due may be
        # null; repo ordering (due_on IS NULL, due_on, priority) already
        # puts undated tasks last.
        "tasks_due": [
            {"ref": t.id, "title": t.title, "description": t.description,
             "category": t.category, "due": t.due_on}
            for t in tasks_repo.open_tasks(conn)
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
        # what clients owe US, not what we owe them — and unlike project_needs
        # above this is NOT windowed: every outstanding ask, undated and
        # far-future included, exactly as the per-client branch lists them
        "information_requests": _book_information_requests(conn),
    }


def _pipeline_status(conn: sqlite3.Connection) -> dict[str, Any]:
    from .money import format_cents
    from .services import pipeline, sla

    # StageMetrics carries total_cents/weighted_cents in cents (see
    # services/pipeline.py:63) — cents never leave a tool raw, so format
    # here and rename the keys to drop the now-misleading "_cents" suffix.
    stages = [
        {
            "stage": m.stage,
            "count": m.count,
            "total": format_cents(m.total_cents),
            "weighted": format_cents(m.weighted_cents),
            "avg_days_in_stage": m.avg_days_in_stage,
        }
        for m in pipeline.metrics(conn)
    ]
    return {
        "stages": stages,
        "conversion": pipeline.conversion(conn),
        "submissions_past_sla": len(sla.past_sla(conn, date.today())),
    }


def serve(db_path: Path | str | None = None) -> None:
    build_server(db_path).run()  # stdio transport is the SDK's default
