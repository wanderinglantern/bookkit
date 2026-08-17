"""bookctl mcp — stdio MCP server for the work-machine cowork assistant.

Read tools run on a read-only connection (mode=ro — enforced by the database,
not by convention). _register_read_tools and _register_write_tools are the
only registrars, and they are the list: an earlier version of this docstring
enumerated seven write tools by name and called them all additive, which
stopped being true somewhere around thirty tools and several deletes ago. A
contract statement that has drifted is worse than none, so what follows are
the invariants every tool must satisfy rather than a roster that rots:

- Writes go through db.transaction and are event-logged with source=mcp.
- One MCP call is one undo unit: db.transaction(batch=) stamps every event
  with a batch_id, and services/batches.py reverts a batch all-or-nothing,
  refusing when a field changed since. db.BLAST_CAP bounds the entities one
  batch may touch, enforced under log_event so no tool can forget it.
- Program writes take the guarded towerkit cycle — load, mutate, validate,
  canonical dump, re-project — sha256-guarded against a concurrent editor.
- Placements are read-only to the assistant by design.

stdout is protocol; anything human goes to stderr (never print here). That
invariant is checked by `bookctl mcp --check`, which builds a server with
stdout captured and fails if a single byte escapes.

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
from datetime import date, timedelta
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

# Field NAME -> its real KIND, exactly as forms/entities.py declares it for the
# same field. This is per-field, not a name-wide lookup: a name is not
# globally 1:1 with a kind (task.description is a one-line "text"; project's
# is a "textarea") so the kind has to live where the field is declared, not
# be guessed from the name alone.
_ENRICHABLE_ORG = {
    "owner": "text", "industry": "text", "naics": "naics",
    "hq_city": "text", "hq_country": "text", "website": "url",
    "domain": "domain", "legal_name": "text", "notes": "textarea",
}
_ENRICHABLE_CONTACT = {
    "email": "email", "phone": "phone", "mobile": "phone",
    "title": "text", "linkedin": "linkedin", "notes": "textarea",
}


def build_server(db_path: Path | str | None = None) -> MCPServer:
    server = MCPServer(
        "bookkit",
        instructions=(
            "Grant's book of business. Money values are formatted dollars; "
            "dates are ISO. Use search/open_items to find refs before "
            "completing or enriching anything — never guess an id."
        ),
    )
    # Order matters: connect() creates the file and migrates it, and a
    # mode=ro connection cannot create anything. Opening read-only first
    # kills the server at startup on a first run, and a server that never
    # starts looks exactly like a server with no tools.
    rw = db.connect(db_path)
    ro = db.connect_readonly(db_path)
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

    @server.tool()
    async def team_roster() -> dict[str, Any]:
        """Every team member with their assignments — including the exact
        `assignment_id` that team_unassign and edit_field (kind=
        team_assignment) take and the names edit_field and team_assign
        resolve. Read this before any team write."""
        return _team_roster(ro)


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

    @server.tool()
    async def program_layers(placement_ref: str) -> dict[str, Any]:
        """A linked program's tower: lines (with the exact line ids the write
        tools take), every layer with its id, attach/limit/premium in cents,
        and participants. Read this before ANY program write."""
        return _program_layers(rw, placement_ref)

    @server.tool()
    async def program_layer_add(
        placement_ref: str,
        name: str,
        line_ids: list[str],
        attach: str,
        limit: str,
        premium: str | None = None,
    ) -> dict[str, Any]:
        """Add a pending ('to be placed') layer to a linked program — money
        in human dollars ("5m", "250k"). The write goes through towerkit's
        full cycle: a validation failure or a file changed on disk refuses
        and writes NOTHING. Every write snapshots the file first —
        program_revert_file(batch) restores it."""
        return _program_layer_add(rw, placement_ref, name, line_ids,
                                  attach=attach, limit=limit, premium=premium)

    @server.tool()
    async def program_bind(
        placement_ref: str, layer_id: str, carrier: str, share: str
    ) -> dict[str, Any]:
        """Bind a market onto a layer at a share ("25%"). Over-signing the
        layer is refused by towerkit's validator — nothing written. layer_id
        is the exact id program_layers showed."""
        return _program_bind(rw, placement_ref, layer_id, carrier, share)

    @server.tool()
    async def program_layer_edit(
        placement_ref: str,
        layer_id: str,
        name: str | None = None,
        policy_number: str | None = None,
        attach: str | None = None,
        limit: str | None = None,
        premium: str | None = None,
    ) -> dict[str, Any]:
        """Edit the book-facts of one layer (exact id from program_layers):
        name, policy number, attach/limit/premium in human dollars. Tower
        DESIGN (lines, retentions, structure) stays in towerkit's editor."""
        return _program_layer_edit(rw, placement_ref, layer_id, name=name,
                                   policy_number=policy_number, attach=attach,
                                   limit=limit, premium=premium)

    @server.tool()
    async def program_edit(
        placement_ref: str,
        name: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> dict[str, Any]:
        """Edit program-level facts: name and effective dates (any human
        date form)."""
        return _program_edit(rw, placement_ref, name=name,
                             period_from=period_from, period_to=period_to)

    @server.tool()
    async def program_revert_file(batch: str) -> dict[str, Any]:
        """Restore the program file exactly as it was before one program_*
        batch — the file-side revert (revert_batch refuses program batches).
        Refuses if ANYTHING touched the file since that write; revert newer
        changes first."""
        return _program_revert_file(rw, batch)

    @server.tool()
    async def contact_add(
        client: str,
        first_name: str,
        last_name: str,
        email: str | None = None,
        phone: str | None = None,
        title: str | None = None,
        make_primary: bool = False,
    ) -> dict[str, Any]:
        """Add a contact to an EXISTING client (client_create bundles its
        own). Exact-name duplicate on that client refuses — edit the existing
        person instead. Values go through the same cleaners as the forms."""
        return _contact_add(rw, client, first_name, last_name, email=email,
                            phone=phone, title=title, make_primary=make_primary)

    @server.tool()
    async def opportunity_create(
        client: str,
        title: str,
        lines: str | None = None,
        target_premium: str | None = None,
        target_effective: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Track a new deal for a client. A fuzzy title match against that
        client's OPEN opportunities refuses (advance or edit that one);
        closed deals never block. `target_premium` in human dollars ("1.2m"),
        `target_effective` any human date. Opportunities born from project
        needs are the sync service's job — this is for standalone deals."""
        return _opportunity_create(rw, client, title, lines=lines,
                                   target_premium=target_premium,
                                   target_effective=target_effective,
                                   source=source)

    @server.tool()
    async def project_create(
        client: str,
        name: str,
        site: str | None = None,
        start_on: str | None = None,
        end_on: str | None = None,
    ) -> dict[str, Any]:
        """Create a project (construction job, site build-out) on a client.
        Dates accept any human form. Add its lines of cover with need_add."""
        return _project_create(rw, client, name, site=site,
                               start_on=start_on, end_on=end_on)

    @server.tool()
    async def need_add(
        project_ref: str, line: str, needed_by: str, notes: str | None = None
    ) -> dict[str, Any]:
        """Add a line-of-cover need to a project (exact PRJ ref). `needed_by`
        is required — an undated need can't be chased. Unmet needs surface in
        attention and never fall off."""
        return _need_add(rw, project_ref, line, needed_by, notes=notes)

    @server.tool()
    async def member_create(
        name: str,
        title: str | None = None,
        specialty: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Add a team member (colleague on the marsh team). Exact-name
        duplicate refuses — edit the existing person with edit_field."""
        return _member_create(rw, name, title=title, specialty=specialty,
                              email=email, phone=phone)

    @server.tool()
    async def team_assign(
        member: str,
        client: str | None = None,
        placement_ref: str | None = None,
        lines: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Assign a team member (exact name — read team_roster) to exactly
        one of a client (account-level) or a placement (deal-level). `role`
        must be one of the TEAM_ROLES vocabulary; `lines` is freeform
        ("cyber, tech E&O"). Assignments drive attention routing."""
        return _team_assign(rw, member, client=client,
                            placement_ref=placement_ref, lines=lines, role=role)

    @server.tool()
    async def team_unassign(assignment_id: str) -> dict[str, Any]:
        """Remove one assignment by the exact id team_roster showed. Soft and
        batched — revert_batch restores it."""
        return _team_unassign(rw, assignment_id)

    @server.tool()
    async def member_deactivate(
        name: str, cascade: bool = False
    ) -> dict[str, Any]:
        """Retire a colleague (exact name — read team_roster). Refuses while
        they still hold assignments, naming every one; cascade=True removes
        all of them and deactivates as ONE revertible batch. They stay in the
        record and stop appearing in pickers; member_reactivate undoes the
        deactivation, but NOT the cascaded assignments — revert_batch does
        that."""
        return _member_deactivate(rw, name, cascade=cascade)

    @server.tool()
    async def member_reactivate(name: str) -> dict[str, Any]:
        """Bring a retired colleague back (exact name — read team_roster).
        Assignments removed by a cascading deactivate do NOT come back; use
        revert_batch for those."""
        return _member_reactivate(rw, name)

    @server.tool()
    async def opportunity_stage(
        ref: str, to: str, note: str | None = None, loss_reason: str | None = None
    ) -> dict[str, Any]:
        """Move a deal through the pipeline: ONE gate forward at a time, or
        close to won/lost from any open stage. A refused move lists the legal
        next stages. Pass loss_reason when marking lost. This is the ONLY way
        stage changes — edit_field refuses the field."""
        return _opportunity_stage(rw, ref, to, note=note, loss_reason=loss_reason)

    @server.tool()
    async def task_reopen(task_ref: str) -> dict[str, Any]:
        """Reopen a completed task by exact ref — task_complete's undo."""
        return _task_reopen(rw, task_ref)

    @server.tool()
    async def request_item_waive(item_ref: str) -> dict[str, Any]:
        """Waive one RFI item by exact ref — 'we no longer need this from
        you'. The request closes when nothing is left outstanding."""
        return _request_item_waive(rw, item_ref)

    @server.tool()
    async def edit_field(
        kind: str,
        ref: str,
        field: str,
        value: str,
        expecting: str | None = None,
        client: str | None = None,
    ) -> dict[str, Any]:
        """Deliberately OVERWRITE one field — compare-and-set. `expecting`
        must be the value a read just showed you (same human form: money as
        dollars, dates as displayed); a mismatch refuses and names what the
        field really holds, writing nothing. expecting omitted asserts the
        field is BLANK (use enrich_field for routine blank-filling). `kind`
        is org|contact|opportunity|project|project_need|task|team_member|
        team_assignment|rfi_request|rfi_item; `ref` is the exact name
        (org/contact/team_member — contact also needs `client`) or the
        exact ref/id a read returned — for team_assignment, the
        `assignment_id` that team_roster returns. Stage moves are
        opportunity_stage, never this."""
        return _edit_field(rw, kind, ref, field, value,
                           expecting=expecting, client=client)

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
        user has said to revert the rest anyway. `applied: false` means
        nothing was written."""
        from .db import utc_now

        return _revert_batch(rw, ref, now=utc_now(), force=force)


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
    """One MCP call, one undo unit — services.batches.open_batch with this
    surface's source stamp. The TUI opens batches the same way."""
    from .services import batches as batches_svc

    with batches_svc.open_batch(
        conn, source="mcp", tool=tool, summary=summary, org_id=org_id
    ) as batch:
        yield batch


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


def _clean_by_kind(kind: str, value: str) -> str:
    """The one cleaner map (bookkit.forms.spec.CLEANERS), keyed by KIND. A
    field NAME is not globally 1:1 with a kind — `description` is a one-line
    `text` on task and a `textarea` on project — so every caller must resolve
    its own kind (from `_ENRICHABLE_ORG`/`_ENRICHABLE_CONTACT`, or from
    `_EDITABLE`'s per-(entity, field) vtype) before reaching here."""
    from .forms.spec import CLEANERS
    from .normalize import clean_text

    return CLEANERS.get(kind, clean_text)(value)


def _contact_add(
    conn: sqlite3.Connection, client: str, first_name: str, last_name: str,
    email: str | None = None, phone: str | None = None,
    title: str | None = None, make_primary: bool = False,
) -> dict[str, Any]:
    from .repo import contacts as contacts_repo

    org = _resolve_client(conn, client)
    wanted = f"{first_name} {last_name}".strip().lower()
    people = contacts_repo.for_org(conn, org.id, active_only=False)
    dup = next((c for c in people if c.name.lower() == wanted), None)
    if dup is not None:
        raise ValueError(
            f"{org.name} already has a contact {dup.name} — edit them with "
            f"edit_field, or use a distinct name if this is really a new person"
        )
    fields: dict[str, Any] = {}
    if email:
        fields["email"] = _clean_by_kind("email", email)
    if phone:
        fields["phone"] = _clean_by_kind("phone", phone)
    if title:
        fields["title"] = title
    with _open_batch(
        conn, tool="contact_add", org_id=org.id,
        summary=f"added contact {first_name} {last_name} at {org.name}",
    ) as batch:
        contact = contacts_repo.create(
            conn, org.id, first_name=first_name, last_name=last_name, **fields
        )
        if make_primary:
            contacts_repo.set_primary(conn, contact.id)
        _provenance(conn, "contact", contact.id)
    return {"contact_id": contact.id, "name": contact.name,
            "primary": make_primary, "batch": batch.ref}


def _opportunity_create(
    conn: sqlite3.Connection, client: str, title: str,
    lines: str | None = None, target_premium: str | None = None,
    target_effective: str | None = None, source: str | None = None,
) -> dict[str, Any]:
    from rapidfuzz import fuzz, process

    from .repo import opportunities

    org = _resolve_client(conn, client)
    open_opps = [
        o for o in opportunities.for_org(conn, org.id)
        if o.stage not in ("won", "lost")
    ]
    titles = {o.title: o for o in open_opps}
    match = process.extractOne(
        title, list(titles), scorer=fuzz.WRatio, score_cutoff=_DUP_CUTOFF)
    if match:
        dup = titles[match[0]]
        raise ValueError(
            f"possible duplicate of open opportunity {dup.title!r} ({dup.ref}) "
            f"— edit or advance that one, or retry with a more distinct title"
        )
    fields: dict[str, Any] = {}
    if lines:
        fields["lines"] = lines
    if target_premium:
        fields["target_premium"] = _clean_typed("money", "target_premium",
                                                target_premium)
    if target_effective:
        fields["target_effective"] = _clean_typed("date", "target_effective",
                                                  target_effective)
    if source:
        fields["source"] = source
    with _open_batch(
        conn, tool="opportunity_create", org_id=org.id,
        summary=f"created opportunity {title!r} for {org.name}",
    ) as batch:
        opp = opportunities.create(conn, org.id, title, **fields)
        _provenance(conn, "opportunity", opp.id)
    return {"opportunity_ref": opp.ref, "title": opp.title,
            "stage": str(opp.stage.value if hasattr(opp.stage, "value") else opp.stage),
            "batch": batch.ref}


def _project_create(
    conn: sqlite3.Connection, client: str, name: str, site: str | None = None,
    start_on: str | None = None, end_on: str | None = None,
) -> dict[str, Any]:
    from .repo import projects as projects_repo

    org = _resolve_client(conn, client)
    fields: dict[str, Any] = {"site": site} if site else {}
    if start_on:
        fields["start_on"] = _clean_typed("date", "start_on", start_on)
    if end_on:
        fields["end_on"] = _clean_typed("date", "end_on", end_on)
    with _open_batch(
        conn, tool="project_create", org_id=org.id,
        summary=f"created project {name!r} for {org.name}",
    ) as batch:
        project = projects_repo.create_project(conn, org.id, name, **fields)
        _provenance(conn, "project", project.id)
    return {"project_ref": project.ref, "name": project.name, "batch": batch.ref}


def _need_add(
    conn: sqlite3.Connection, project_ref: str, line: str, needed_by: str,
    notes: str | None = None,
) -> dict[str, Any]:
    from .repo import projects as projects_repo

    project = projects_repo.find_project(conn, project_ref)
    if project is None:
        raise ValueError(f"no project {project_ref!r} — use its exact ref (PRJ-…)")
    fields: dict[str, Any] = {"notes": notes} if notes else {}
    when = _clean_typed("date", "needed_by", needed_by)  # required by the model
    with _open_batch(
        conn, tool="need_add", org_id=project.org_id,
        summary=f"added need {line!r} to {project.name}",
    ) as batch:
        need = projects_repo.add_need(conn, project.id, line, when, **fields)
        _provenance(conn, "project_need", need.id)
    return {"need_id": need.id, "line": need.line, "project_ref": project.ref,
            "batch": batch.ref}


# --- policy records: the guarded program-file cycle ---------------------------


def _resolve_linked_placement(conn: sqlite3.Connection, placement_ref: str) -> Any:
    from .repo import placements as placements_repo

    placement = placements_repo.find(conn, placement_ref)
    if placement is None:
        raise ValueError(f"no placement {placement_ref!r} — use its exact PLC ref")
    if not placement.program_path:
        raise ValueError(
            f"{placement.ref} has no program file linked — nothing to edit"
        )
    return placement


def _raise_on_errors(diags: Any) -> list[str]:
    """write_through's Diagnostics → tool contract: errors refuse (nothing was
    written), warnings ride along in the return."""
    if not diags.ok:
        raise ValueError(
            "refused by towerkit's validator — nothing written: "
            + "; ".join(d.message for d in diags.errors)
        )
    return [d.message for d in diags.warnings]


def _program_write(
    conn: sqlite3.Connection,
    placement: Any,
    tool: str,
    summary: str,
    write: Any,
) -> tuple[Any, list[str]]:
    """One batched program-file write: capture the pre-image, run the sync.*
    writer, snapshot on success. sync._mutate already folds WriteConflict
    into the diagnostics ('re-sync and retry'), so _raise_on_errors carries
    the same re-read contract compare-and-set trained the model on."""
    from pathlib import Path as _Path

    from .services import program_files

    path = _Path(placement.program_path)
    pre_image = path.read_bytes()
    with _open_batch(
        conn, tool=tool, org_id=placement.org_id, summary=summary,
    ) as batch:
        diags = write()
        warnings = _raise_on_errors(diags)   # raising rolls the batch back
        program_files.capture(path, batch.ref, pre_image)
    return batch, warnings


def _program_layers(conn: sqlite3.Connection, placement_ref: str) -> dict[str, Any]:
    from pathlib import Path as _Path

    from towerkit.model import load_program

    from . import sync as sync_mod

    placement = _resolve_linked_placement(conn, placement_ref)
    program = load_program(_Path(placement.program_path))
    return {
        "placement_ref": placement.ref,
        "program": program.program,
        "period": {"from": program.period.start.isoformat(),
                   "to": program.period.end.isoformat()},
        "lines": [{"id": ln.id, "name": ln.name} for ln in program.lines],
        "layers": sync_mod.layer_details(conn, placement.id),
    }


def _program_layer_add(
    conn: sqlite3.Connection, placement_ref: str, name: str,
    line_ids: list[str], attach: str, limit: str, premium: str | None = None,
) -> dict[str, Any]:
    from . import sync as sync_mod
    from .money import parse_money_cents

    placement = _resolve_linked_placement(conn, placement_ref)
    batch, warnings = _program_write(
        conn, placement, tool="program_layer_add",
        summary=f"added layer {name!r} to {placement.ref}",
        write=lambda: sync_mod.add_layer(
            conn, placement.id, name, line_ids,
            attach_cents=parse_money_cents(attach),
            limit_cents=parse_money_cents(limit),
            premium_cents=parse_money_cents(premium) if premium else None,
        ),
    )
    return {"added": name, "placement_ref": placement.ref,
            "warnings": warnings, "batch": batch.ref}


def _program_bind(
    conn: sqlite3.Connection, placement_ref: str, layer_id: str,
    carrier: str, share: str,
) -> dict[str, Any]:
    from . import sync as sync_mod
    from .money import parse_share_bps

    placement = _resolve_linked_placement(conn, placement_ref)
    bps = parse_share_bps(share)
    batch, warnings = _program_write(
        conn, placement, tool="program_bind",
        summary=f"bound {carrier} at {bps / 100:g}% on {layer_id}",
        write=lambda: sync_mod.add_participant(
            conn, placement.id, layer_id, carrier, bps
        ),
    )
    return {"bound": carrier, "share_bps": bps, "layer_id": layer_id,
            "warnings": warnings, "batch": batch.ref}


def _program_layer_edit(
    conn: sqlite3.Connection, placement_ref: str, layer_id: str,
    name: str | None = None, policy_number: str | None = None,
    attach: str | None = None, limit: str | None = None,
    premium: str | None = None,
) -> dict[str, Any]:
    from . import sync as sync_mod
    from .money import parse_money_cents

    placement = _resolve_linked_placement(conn, placement_ref)
    batch, warnings = _program_write(
        conn, placement, tool="program_layer_edit",
        summary=f"edited layer {layer_id} on {placement.ref}",
        write=lambda: sync_mod.update_layer(
            conn, placement.id, layer_id, name=name, policy_number=policy_number,
            attach_cents=parse_money_cents(attach) if attach else None,
            limit_cents=parse_money_cents(limit) if limit else None,
            premium_cents=parse_money_cents(premium) if premium else None,
        ),
    )
    return {"edited": layer_id, "warnings": warnings, "batch": batch.ref}


def _program_edit(
    conn: sqlite3.Connection, placement_ref: str, name: str | None = None,
    period_from: str | None = None, period_to: str | None = None,
) -> dict[str, Any]:
    from . import sync as sync_mod

    placement = _resolve_linked_placement(conn, placement_ref)
    from_iso = _clean_typed("date", "period_from", period_from) if period_from else None
    to_iso = _clean_typed("date", "period_to", period_to) if period_to else None
    batch, warnings = _program_write(
        conn, placement, tool="program_edit",
        summary=f"edited program facts on {placement.ref}",
        write=lambda: sync_mod.update_program(
            conn, placement.id, program_name=name,
            period_from=from_iso, period_to=to_iso,
        ),
    )
    return {"edited": placement.ref, "warnings": warnings, "batch": batch.ref}


def _program_revert_file(conn: sqlite3.Connection, batch_ref: str) -> dict[str, Any]:
    """Restore the pre-image of one program write — the file-side revert
    batch undo cannot provide. Refuses if anything touched the file since."""
    from pathlib import Path as _Path

    from . import sync as sync_mod
    from .repo import batches as batches_repo
    from .services import program_files

    batch = batches_repo.get_by_ref(conn, batch_ref)   # KeyError → tool error
    if not batch.tool.startswith("program_"):
        raise ValueError(
            f"{batch_ref} is not a program-file write — use revert_batch"
        )
    if batch.reverted_at is not None:
        raise ValueError(f"{batch_ref} was already reverted at {batch.reverted_at}")
    if batch.org_id is None:
        raise ValueError(f"{batch_ref} names no account — cannot locate its file")
    from .repo import placements as placements_repo

    linked = [
        p for p in placements_repo.for_org(conn, batch.org_id) if p.program_path
    ]
    target = None
    for placement in linked:
        try:
            # the comprehension filtered None paths; mypy cannot see that
            program_files.restore(_Path(str(placement.program_path)), batch_ref)
            target = placement
            break
        except ValueError as exc:
            if "was a write to" not in str(exc) and "no snapshot" not in str(exc):
                raise                          # sha mismatch etc: surface it
    if target is None:
        raise ValueError(f"no snapshot for {batch_ref} on any of this account's files")
    from .db import utc_now

    with db.transaction(conn):                 # unbatched, like revert_batch
        diags = sync_mod.project(conn, _Path(str(target.program_path)),
                                 placement_id=target.id)
        batches_repo.mark_reverted(conn, batch.id, utc_now())
    return {"reverted": True, "batch": batch_ref,
            "file": target.program_path,
            "warnings": [d.message for d in diags.warnings]}


def _find_member(conn: sqlite3.Connection, name: str) -> Any:
    from .repo import team

    members = team.list_members(conn, active_only=False)
    member = next((m for m in members if m.name.lower() == name.lower()), None)
    if member is None:
        raise ValueError(f"no team member {name!r}; have: {[m.name for m in members]}")
    return member


def _guard_member_rename(
    conn: sqlite3.Connection, member_id: str, new_name: str
) -> None:
    """Renaming onto a name someone else holds makes every member lookup
    ambiguous — _find_member and _edit_target both take the first match — so
    this refuses rather than letting a later write land on the wrong row."""
    from .repo import team

    for other in team.list_members(conn, active_only=False):
        if other.id != member_id and other.name.lower() == new_name.lower():
            raise ValueError(
                f"team member {other.name} already holds that name — rename "
                f"or deactivate them first"
            )


def _team_roster(conn: sqlite3.Connection) -> dict[str, Any]:
    from .repo import team

    out = []
    for member in team.list_members(conn, active_only=False):
        assignments = []
        for row in team.for_member(conn, member.id):
            assignments.append({
                "assignment_id": row["id"],
                "account": row["org_name"] if "org_name" in row.keys() else None,
                "placement": row["placement_ref"] if "placement_ref" in row.keys() else None,
                "role": row["role"], "lines": row["lines"],
                "notes": row["notes"],
            })
        out.append({
            "name": member.name, "title": member.title,
            "specialty": member.specialty, "active": member.active,
            "assignments": assignments,
        })
    return {"members": out}


def _member_create(
    conn: sqlite3.Connection, name: str, title: str | None = None,
    specialty: str | None = None, email: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    from .repo import team

    existing = team.list_members(conn, active_only=False)
    dup = next((m for m in existing if m.name.lower() == name.lower()), None)
    if dup is not None:
        raise ValueError(
            f"team member {dup.name} already exists — edit them with edit_field"
        )
    fields: dict[str, Any] = {}
    if title:
        fields["title"] = title
    if specialty:
        fields["specialty"] = specialty
    if email:
        fields["email"] = _clean_by_kind("email", email)
    if phone:
        fields["phone"] = _clean_by_kind("phone", phone)
    with _open_batch(
        conn, tool="member_create", summary=f"added team member {name}",
    ) as batch:
        member = team.create_member(conn, name, **fields)
        _provenance(conn, "team_member", member.id)
    return {"member_id": member.id, "name": member.name, "batch": batch.ref}


def _team_assign(
    conn: sqlite3.Connection, member: str, client: str | None = None,
    placement_ref: str | None = None, lines: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    from .models import TEAM_ROLES
    from .repo import team

    found = _find_member(conn, member)
    if role is not None and role not in TEAM_ROLES:
        raise ValueError(f"role must be one of {list(TEAM_ROLES)}, not {role!r}")
    if (client is None) == (placement_ref is None):
        raise ValueError(
            "scope the assignment with exactly one of `client` (account-level)"
            " or `placement_ref` (deal-level)"
        )
    org_id = None
    placement_id = None
    label = ""
    if client:
        org = _resolve_client(conn, client)
        org_id, label = org.id, org.name
    else:
        from .repo import placements as placements_repo

        placement = placements_repo.find(conn, str(placement_ref))
        if placement is None:
            raise ValueError(f"no placement {placement_ref!r} — use its exact PLC ref")
        placement_id, label = placement.id, placement.ref
        org_id = placement.org_id
    fields: dict[str, Any] = {}
    if lines:
        fields["lines"] = lines
    if role:
        fields["role"] = role
    with _open_batch(
        conn, tool="team_assign", org_id=org_id,
        summary=f"assigned {found.name} to {label}",
    ) as batch:
        assignment = team.assign(
            conn, found.id,
            org_id=org_id if client else None,
            placement_id=placement_id, **fields,
        )
        _provenance(conn, "team_assignment", assignment.id)
    return {"assignment_id": assignment.id, "member": found.name,
            "batch": batch.ref}


def _assignment_org_id(
    conn: sqlite3.Connection, org_id: str | None, placement_id: str | None
) -> str | None:
    """The org a team assignment belongs to. Account-level rows carry it
    directly; deal-level rows carry only the placement, and a batch stamped
    with no org is invisible in that client's history. One rule, so the three
    assignment write paths cannot drift apart again."""
    if org_id is not None:
        return org_id
    if placement_id is None:
        return None
    from .repo import placements as placements_repo

    return placements_repo.get(conn, placement_id).org_id


def _team_unassign(conn: sqlite3.Connection, assignment_id: str) -> dict[str, Any]:
    from .repo import base, team

    row = base.get(conn, "team_assignment", assignment_id)
    if row is None:
        raise ValueError(
            f"no assignment {assignment_id!r} — read team_roster for exact ids"
        )
    with _open_batch(
        conn, tool="team_unassign",
        org_id=_assignment_org_id(conn, row["org_id"], row["placement_id"]),
        summary="removed a team assignment",
    ) as batch:
        team.unassign(conn, assignment_id)
        _provenance(conn, "team_assignment", assignment_id)
    return {"assignment_id": assignment_id, "removed": True, "batch": batch.ref}


def _assignment_label(row: sqlite3.Row) -> str:
    """How one assignment reads in a refusal: the client, plus the placement
    ref when it is deal-level rather than account-level."""
    keys = row.keys()
    account = row["org_name"] if "org_name" in keys else None
    placement = row["placement_ref"] if "placement_ref" in keys else None
    label = account or "unscoped"
    return f"{label} ({placement})" if placement else label


def _member_deactivate(
    conn: sqlite3.Connection, name: str, cascade: bool = False
) -> dict[str, Any]:
    """Retire a colleague. Refuses while they still hold assignments — a
    roster that silently keeps pointing at someone who left is worse than a
    refusal — and names every one so the caller can act. cascade=True removes
    them and deactivates in ONE batch, so revert_batch puts it all back."""
    from .repo import base, team

    member = _find_member(conn, name)
    if not member.active:
        raise ValueError(f"{member.name} is already inactive")
    rows = team.for_member(conn, member.id)
    if rows and not cascade:
        labels = ", ".join(_assignment_label(r) for r in rows)
        raise ValueError(
            f"{member.name} is still on {len(rows)} assignments: {labels} — "
            f"unassign them first, or pass cascade=True to remove all "
            f"{len(rows)} and deactivate as one revertible batch"
        )
    summary = f"deactivated {member.name}"
    if rows:
        summary += f" and removed {len(rows)} assignments"
    # org_id stays None: a cascade spans clients, so no single org owns it.
    with _open_batch(
        conn, tool="member_deactivate", summary=summary,
    ) as batch:
        for row in rows:
            team.unassign(conn, row["id"])
            _provenance(conn, "team_assignment", row["id"])
        base.update(conn, "team_member", member.id, {"active": 0},
                    note="mcp deactivate")
        _provenance(conn, "team_member", member.id)
    return {"name": member.name, "active": False, "unassigned": len(rows),
            "batch": batch.ref}


def _member_reactivate(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """Bring a retired colleague back. Assignments a cascade removed do NOT
    come back — revert_batch is the undo for those."""
    from .repo import base

    member = _find_member(conn, name)
    if member.active:
        raise ValueError(f"{member.name} is already active")
    with _open_batch(
        conn, tool="member_reactivate", summary=f"reactivated {member.name}",
    ) as batch:
        base.update(conn, "team_member", member.id, {"active": 1},
                    note="mcp reactivate")
        _provenance(conn, "team_member", member.id)
    return {"name": member.name, "active": True, "batch": batch.ref}


def _opportunity_stage(
    conn: sqlite3.Connection, ref: str, to: str,
    note: str | None = None, loss_reason: str | None = None,
) -> dict[str, Any]:
    from .repo import opportunities
    from .services import pipeline

    opp = opportunities.find(conn, ref)
    if opp is None:
        raise ValueError(f"no opportunity {ref!r} — use its exact ref (OPP-…)")
    current = str(opp.stage.value if hasattr(opp.stage, "value") else opp.stage)
    allowed = pipeline.allowed_next(current)
    if to not in allowed:
        raise ValueError(
            f"cannot move {opp.ref} from {current!r} to {to!r} — deals advance "
            f"one gate at a time or close; from here: {list(allowed)}"
        )
    with _open_batch(
        conn, tool="opportunity_stage", org_id=opp.org_id,
        summary=f"moved {opp.ref} {current} → {to}",
    ) as batch:
        moved = pipeline.move_stage(conn, opp.id, to, note=note,
                                    loss_reason=loss_reason)
        _provenance(conn, "opportunity", opp.id)
    return {"opportunity_ref": moved.ref,
            "stage": str(moved.stage.value if hasattr(moved.stage, "value") else moved.stage),
            "batch": batch.ref}


def _task_reopen(conn: sqlite3.Connection, task_ref: str) -> dict[str, Any]:
    from .repo import tasks as tasks_repo

    task = tasks_repo.get(conn, task_ref)             # KeyError → tool error
    with _open_batch(
        conn, tool="task_reopen", org_id=task.org_id,
        summary=f"reopened task: {task.title}",
    ) as batch:
        task = tasks_repo.reopen(conn, task_ref)
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "status": task.status, "batch": batch.ref}


def _request_item_waive(conn: sqlite3.Connection, item_ref: str) -> dict[str, Any]:
    from .repo import rfi as rfi_repo

    item = rfi_repo.get_item(conn, item_ref)          # KeyError → tool error
    request = rfi_repo.get_request(conn, item.request_id)
    with _open_batch(
        conn, tool="request_item_waive", org_id=request.org_id,
        summary=f"waived an item: {item.prompt[:60]}",
    ) as batch:
        item = rfi_repo.update_item(conn, item.id, status="waived")
        _provenance(conn, "rfi_item", item.id)
    return {"item_ref": item.id, "status": item.status,
            "request_ref": request.ref, "batch": batch.ref}


# edit_field's allowlists: (kind → field → value type). Each string value
# is the field's real KIND, exactly as forms/entities.py declares it for that
# field on that entity — not a name-wide default. A field name is not
# globally 1:1 with a kind: task.description is a one-line "text" (the
# textarea is `detail`) while project.description IS the textarea; using the
# same vtype for both silently flattened whichever one didn't match. "text"
# routes through the cleaner map (bookkit.forms.spec.CLEANERS) like
# enrich_field; "textarea" is stored verbatim; "money" parses to integer
# cents; "date" through parse_human_date; "int" plain; a tuple is a closed
# vocabulary and refusals list it. `notes` is "textarea" everywhere it
# appears — every forms/entities.py declaration of it agrees, with no
# exceptions. Deliberate absences are the contract: opportunity
# stage/outcome/closed_at belong to opportunity_stage, and
# project_need.status belongs to the queued needs→pipeline reconciler.
def _editable() -> dict[str, dict[str, Any]]:
    from .models import PROJECT_STATUSES, TEAM_ROLES

    return {
        "org": dict(_ENRICHABLE_ORG),
        "contact": {
            **_ENRICHABLE_CONTACT,
            "first_name": "text", "last_name": "text",
        },
        "opportunity": {
            "title": "text", "lines": "text", "target_premium": "money",
            "target_effective": "date", "probability_pct": "int",
            "source": "text", "incumbent_broker": "text",
            "competitor": "text", "notes": "textarea",
        },
        "project": {
            "name": "text", "description": "textarea", "site": "text",
            "status": PROJECT_STATUSES, "start_on": "date", "end_on": "date",
            "notes": "textarea",
        },
        # need STATUS is deliberately absent: the queued needs→pipeline
        # reconciler owns need-status semantics
        "project_need": {
            "line": "text", "needed_by": "date", "limit_cents": "money",
            "premium_indication_cents": "money", "notes": "textarea",
        },
        "task": {
            # description is a one-line summary (forms/entities.py:185); detail
            # is the textarea (:201). Same-named field, different entity,
            # different kind — the whole reason this is per-(entity, field).
            "title": "text", "description": "text", "detail": "textarea",
            "category": "text", "due_on": "date",
        },
        "team_member": {
            "name": "text", "title": "text", "specialty": "text",
            "email": "email", "phone": "phone", "notes": "textarea",
        },
        # role reuses team_assign's vocabulary so the two paths cannot drift.
        # org_id / placement_id are deliberately absent: re-scoping moves two
        # columns at once and single-field compare-and-set cannot do it.
        "team_assignment": {
            "role": TEAM_ROLES, "lines": "text", "notes": "textarea",
        },
        "rfi_request": {"title": "text", "due_on": "date", "notes": "textarea"},
        "rfi_item": {
            "prompt": "text", "category": "text", "due_on": "date",
            "response": "textarea",
        },
    }


_EDITABLE: dict[str, dict[str, Any]] = _editable()

# Fields that exist but are owned by a transition tool. The generic refusal
# only lists what IS editable; these say where the caller should go instead.
_EDIT_REDIRECTS: dict[tuple[str, str], str] = {
    ("team_member", "active"): "member_deactivate / member_reactivate",
    # status and received_on move together (services.rfi.mark_received), so
    # neither is a single-field compare-and-set. Without these the model got
    # the generic "not editable; allowed: [...]" list and no idea where to go.
    ("rfi_item", "status"): "request_item_received / request_item_waive",
    ("rfi_item", "received_on"): "request_item_received",
}


def _clean_typed(vtype: Any, field: str, value: str | None) -> Any:
    """One cleaning rule for value AND expecting, so the model compares in
    the same human forms a read returned."""
    if value is None:
        return None
    if vtype == "money":
        from .money import parse_money_cents

        return parse_money_cents(value)
    if vtype == "date":
        from .dates import parse_human_date

        parsed = parse_human_date(value)
        if parsed is None:
            raise ValueError(
                "enter a date like 2026-10-15, friday, or +2w — a bare number is ambiguous"
            )
        return parsed.isoformat()
    if vtype == "int":
        return int(value)
    if isinstance(vtype, tuple):
        if value not in vtype:
            raise ValueError(f"{field!r} must be one of {list(vtype)}, not {value!r}")
        return value
    return _clean_by_kind(vtype, value)


def _edit_target(
    conn: sqlite3.Connection, kind: str, ref: str, client: str | None
) -> tuple[str, str | None, Any]:
    """(entity_id, org_id-for-the-batch, current-row-model). Exact resolution
    only — a write target is never fuzzy-matched."""
    from .repo import contacts as contacts_repo

    if kind == "org":
        org = _resolve_client(conn, ref)
        return org.id, org.id, org
    if kind == "contact":
        if not client:
            raise ValueError("editing a contact needs `client` to scope the name")
        org = _resolve_client(conn, client)
        people = contacts_repo.for_org(conn, org.id, active_only=False)
        target = next((c for c in people if c.name.lower() == ref.lower()), None)
        if target is None:
            raise ValueError(
                f"no contact {ref!r} at {org.name}; have: {[c.name for c in people]}"
            )
        return target.id, org.id, target
    if kind == "opportunity":
        from .repo import opportunities

        opp = opportunities.find(conn, ref)
        if opp is None:
            raise ValueError(f"no opportunity {ref!r} — use its exact ref (OPP-…)")
        return opp.id, opp.org_id, opp
    if kind == "project":
        from .repo import projects as projects_repo

        project = projects_repo.find_project(conn, ref)
        if project is None:
            raise ValueError(f"no project {ref!r} — use its exact ref (PRJ-…)")
        return project.id, project.org_id, project
    if kind == "project_need":
        from .repo import projects as projects_repo

        need = projects_repo.get_need(conn, ref)          # KeyError → tool error
        project = projects_repo.get_project(conn, need.project_id)
        return need.id, project.org_id, need
    if kind == "task":
        from .repo import tasks as tasks_repo

        task = tasks_repo.get(conn, ref)                  # KeyError → tool error
        return task.id, task.org_id, task
    if kind == "team_member":
        from .repo import team

        members = team.list_members(conn, active_only=False)
        member = next((m for m in members if m.name.lower() == ref.lower()), None)
        if member is None:
            raise ValueError(
                f"no team member {ref!r}; have: {[m.name for m in members]}"
            )
        return member.id, None, member
    if kind == "rfi_request":
        request = _resolve_request(conn, ref)
        return request.id, request.org_id, request
    if kind == "rfi_item":
        from .repo import rfi as rfi_repo

        item = rfi_repo.get_item(conn, ref)               # KeyError → tool error
        request = rfi_repo.get_request(conn, item.request_id)
        return item.id, request.org_id, item
    if kind == "team_assignment":
        from .models import TeamAssignment
        from .repo import base as base_repo

        row = base_repo.get(conn, "team_assignment", ref)
        if row is None:
            raise ValueError(
                f"no assignment {ref!r} — read team_roster for exact ids"
            )
        assignment = TeamAssignment.from_row(row)
        org_id = _assignment_org_id(
            conn, assignment.org_id, assignment.placement_id
        )
        return assignment.id, org_id, assignment
    raise ValueError(f"cannot edit kind {kind!r}; editable: {sorted(_EDITABLE)}")


def _edit_field(
    conn: sqlite3.Connection,
    kind: str,
    ref: str,
    field: str,
    value: str,
    expecting: str | None,
    client: str | None = None,
) -> dict[str, Any]:
    """The deliberate overwrite: compare-and-set. `expecting` must match the
    stored value (both cleaned the same way); a mismatch refuses and names
    what the field actually holds, writing nothing. expecting=None asserts
    the field is blank — enrich semantics made explicit."""
    from .repo import base

    allowed = _EDITABLE.get(kind)
    if allowed is None:
        raise ValueError(f"cannot edit kind {kind!r}; editable: {sorted(_EDITABLE)}")
    vtype = allowed.get(field)
    if vtype is None:
        redirect = _EDIT_REDIRECTS.get((kind, field))
        if redirect is not None:
            raise ValueError(
                f"{field!r} on a {kind} is not a field edit — use {redirect}"
            )
        raise ValueError(
            f"{field!r} is not editable on a {kind}; allowed: {sorted(allowed)}"
        )

    entity_id, org_id, row = _edit_target(conn, kind, ref, client)
    current = getattr(row, field)
    expected = _clean_typed(vtype, field, expecting)
    blank = current in (None, "")
    if expecting is None:
        if not blank:
            raise ValueError(
                f"{kind}.{field} is not blank — it holds {current!r}; pass "
                f"expecting=<that value> to overwrite deliberately"
            )
    elif blank or current != expected:
        raise ValueError(
            f"{kind}.{field} holds {current!r}, not what you expected "
            f"({expected!r}) — re-read the record and retry"
        )

    cleaned = _clean_typed(vtype, field, value)
    if kind == "team_member" and field == "name":
        _guard_member_rename(conn, entity_id, cleaned)
    with _open_batch(
        conn, tool="edit_field", org_id=org_id,
        summary=f"edited {kind}.{field} on {ref}",
    ) as batch:
        base.update(conn, kind, entity_id, {field: cleaned}, note="mcp edit")
        _provenance(conn, kind, entity_id)
    return {"edited": True, "kind": kind, "field": field, "value": cleaned,
            "was": current, "batch": batch.ref}


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
    cleaned = _clean_by_kind(allowed[field], value)
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


def _list_batches(
    conn: sqlite3.Connection, today: date, limit: int = 20, days: int = 14
) -> list[dict[str, Any]]:
    """Recent batched writes, newest first — what this server changed and
    whether it has been put back."""
    from .repo import batches as batches_repo
    from .services import batches as batches_svc

    since = (today - timedelta(days=days)).isoformat()
    recent = batches_repo.recent(conn, since=since, limit=limit)
    labels = batches_svc.account_names(conn, recent)  # one query, not N
    out = []
    for batch in recent:
        account = None
        if batch.org_id:
            account = labels.get(batch.org_id, "(deleted account)")
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
