"""bookctl mcp — stdio MCP server for the work-machine cowork assistant.

Read tools run on a read-only connection (mode=ro — enforced by the
database). Exactly five write tools exist, all additive, all inside
db.transaction, all event-logged with source=mcp. stdout is protocol;
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
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import db
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
        window as today_brief)."""
        return _open_items(ro, client=client)

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
    """Shared by every client-scoped read tool and all five write tools —
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
    with db.transaction(conn):
        task = tasks_repo.create(conn, title, **fields)
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "title": task.title, "due": task.due_on}


def _task_complete(conn: sqlite3.Connection, task_ref: str) -> dict[str, Any]:
    """Status flip only. task_ref must be an exact id the model READ from
    open_items/today_brief — no fuzzy title matching, by design."""
    from .repo import tasks as tasks_repo

    with db.transaction(conn):
        task = tasks_repo.complete(conn, task_ref)  # KeyError on unknown → tool error
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "status": task.status, "completed_at": task.completed_at}


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
    with db.transaction(conn):
        if kind == "org":
            orgs.update(conn, target.id, note="mcp enrich", **{field: cleaned})
        else:
            contacts_repo.update(conn, target.id, note="mcp enrich", **{field: cleaned})
        _provenance(conn, kind, target.id)
    return {"set": True, "entity": kind, "field": field, "value": cleaned}


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
