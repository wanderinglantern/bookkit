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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import db, mcpsurface
from .models import EventBatch, RfiItem, RfiRequest, is_internal_category
from .services import consistency

# _DUP_CUTOFF: score_cutoff for the near-duplicate title guards below
# (rapidfuzz WRatio, 0-100). 'Henderson Grp' vs 'Henderson Group' scores ~95;
# this is deliberately loose enough to catch near-misses while staying below
# scores for genuinely distinct short names. The CLIENT-NAME create guard
# moved to services.orgs.find_duplicate (2026-08-20) so the TUI's and the
# web's create forms share it — this alias tracks that one so the two numbers
# cannot drift apart.
from .services.orgs import DUPLICATE_CUTOFF as _DUP_CUTOFF
from .services.renewals import RenewalItem

# Field NAME -> its real KIND, exactly as forms/entities.py declares it for the
# same field. This is per-field, not a name-wide lookup: a name is not
# globally 1:1 with a kind (task.description is a one-line "text"; project's
# is a "textarea") so the kind has to live where the field is declared, not
# be guessed from the name alone — which is why these are now DERIVED from
# those declarations rather than restated here. mcpsurface owns the
# derivation and the denylist; read that module, not this constant.
_ENRICHABLE = mcpsurface.enrichable()
_ENRICHABLE_ORG = _ENRICHABLE["org"]
_ENRICHABLE_CONTACT = _ENRICHABLE["contact"]


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
        Each entry names its lines of coverage — a program name alone is never
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
        """Posture on ONE program: account, period, status, lines of coverage,
        premium, plus counts of its open tasks and outstanding submissions.
        Deliberately slim — it does NOT dump the full program structure or
        market shares. `ref` matches the placement ref or the exact program
        name; an unmatched ref raises with candidate refs/names to retry
        with."""
        return _program_summary(ro, ref)

    @server.tool()
    async def lines_list() -> list[dict[str, Any]]:
        """The book's ONE vocabulary of lines of coverage: id, name,
        abbreviation and ACORD code, in reading order. Every marketing tool
        takes a line by its exact name, abbreviation or id from this list —
        read it BEFORE market_approach or set_placement_line rather than
        inventing a spelling, because a fifth spelling of General Liability
        is a duplicate nobody can merge back without moving its references.
        Use line_add only when the line genuinely is not here."""
        return _lines_list(ro)

    @server.tool()
    async def marketing_report(
        placement_ref: str, audience: str = "client", as_of: str | None = None
    ) -> dict[str, Any]:
        """The marketing report for ONE placement (exact PLC ref — read
        list_programs), as readable text: one block per line of coverage,
        markets beneath it, live options first. `audience` is 'client' or
        'internal' and anything else is refused with the list — 'client'
        WITHHOLDS the internal decline reason, the commission and the internal
        notes, and prints only the controlled public decline wording; go to
        'internal' to see those and the clearance collisions. `as_of` is a
        human date and defaults to today; the composer never reads the clock,
        so the same date always composes the same report. Also returns
        `responses`, an index of ids — that is where market_responded's
        `response_ref` comes from, because a printed row carries no id."""
        return _marketing_report(ro, placement_ref, audience=audience, as_of=as_of)

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
        composition used for the client export deliverable, PLUS the tasks
        that composition withholds: a task filed under the "Internal"
        category never reaches the client's workbook, and its row here is
        marked `internal: true`. So this is what a client would be handed
        plus everything marked internal, deliberately — you are reading
        Grant's book, not the deliverable. Omit `client` for the book-wide
        view:
        ALL open tasks (undated and future-due included, not just due-today),
        unmet project needs, submissions past SLA, and incomplete onboarding,
        across the whole book (project needs use the same 120-day attention
        window as today_brief). Book-wide "tasks_due" rows carry `internal`
        too, on the same rule — so a task no client will ever see is labelled
        in BOTH branches of this tool, not just the per-client one. The match
        is exact: a task categorised "Internal Review" is internal: false and
        DOES reach the client's workbook. Also carries
        "information_requests": the outstanding questions and documents
        clients still owe us — ALL of them, regardless of due date (undated
        and far-future asks included), in both the per-client and the
        book-wide view. requests_to_chase is the
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
        past SLA. Money is formatted dollars. Aggregates only — no deal is
        named here; call `opportunities` for the individual deals and their
        refs."""
        return _pipeline_status(ro)

    @server.tool()
    async def opportunities(
        client: str | None = None, include_closed: bool = False
    ) -> list[dict[str, Any]]:
        """Open deals WITH the `opportunity_ref` that opportunity_stage and
        edit_field(kind="opportunity") take. Use this to FIND a deal you need
        to move or correct: pipeline_status returns per-stage aggregates only
        and `search` returns no ids at all, so nothing else on this surface
        can name a deal the assistant did not create itself. Omit `client` for
        every open deal on the book (each row names its account, so "the Acme
        cyber deal" is findable in one call); pass `client` (exact name or
        ref; on a miss the error lists the nearest candidates) to scope it.
        `include_closed` adds won/lost deals, which are excluded by default."""
        return _opportunities(ro, client=client, include_closed=include_closed)

    @server.tool()
    async def describe(kind: str | None = None) -> dict[str, Any]:
        """What edit_field can write: every kind, its fields, their types and
        the allowed values of any closed vocabulary. Call this BEFORE an edit
        rather than discovering the surface by making a call you expect to
        fail — a refusal costs a round trip and reads as an error in the
        transcript. Omit `kind` for the whole surface, which also lists the
        entities that are deliberately NOT editable and why (placements,
        submissions, documents, appetites, interactions); pass one kind for
        just that entity. `denied_fields` names the fields an entity has that
        edit_field will refuse, with the reason each — several of those are
        owned by a verb tool (opportunity_stage, task_complete,
        request_item_received, member_deactivate) which is where to go
        instead. Derived from the same declarations edit_field enforces, so
        it cannot go stale."""
        return mcpsurface.describe(kind)

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
        client: str, note: str, follow_up: str | None = None,
        type: str = "note", occurred_on: str | None = None,
    ) -> dict[str, Any]:
        """Log a client interaction — additive and event-logged; nothing
        existing is touched. `client` resolves the same way as every other
        client-scoped tool (exact client name or ref; on a miss the error
        lists the nearest candidates — never guess an id). `type` is what
        actually happened: call | meeting | email | note | site_visit | event
        (default note; anything else is refused with the list).
        `occurred_on` is WHEN, as a human date ("yesterday", "2 days ago",
        "2026-08-11" — but NOT "last tuesday", which does not parse) — it
        defaults to today, so pass it for anything you are writing up after
        the fact. A bare 1-2 digit number is refused rather than read as a
        day of the month. Get both right at the time: there is no interaction
        kind in edit_field, so the only correction is activity_delete
        (find the ref with recent_activity) and log it again. Pass
        `follow_up` as any human date to also create a follow-up task in the
        same transaction."""
        return _log_activity(rw, client, note, follow_up=follow_up,
                             type=type, occurred_on=occurred_on)

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
        assignee: str | None = None,
    ) -> dict[str, Any]:
        """Create a task — additive and event-logged. `client` links it to an
        account (exact client name or ref; on a miss the error lists the
        nearest candidates; omit for a book-wide task). `description` is a
        short one-line summary; `detail` holds longer markdown notes. `category`
        is a freeform grouping label — prefer an existing one over inventing a
        new one; call open_items first to see what's already in use. `due`
        accepts any human date ("friday", "+2w", "2026-09-01").

        `assignee` is OPTIONAL AND NEVER BLOCKS THE TASK. Pass a name and it
        resolves to that person when the book knows exactly one of them; a name
        it does not know is kept verbatim as a note; an omitted one leaves the
        task unassigned, which is the normal state of a new task. There is no
        circumstance in which a task fails to be created because of who it is
        for — and if you cannot record something the way you meant to, SAY SO
        rather than writing a different KIND of record instead: an information
        request is a question put to a CLIENT and appears in their workbook,
        which is not what a note-to-self is."""
        return _task_create(
            rw, title, client=client, description=description, detail=detail,
            category=category, due=due, assignee=assignee,
        )

    @server.tool()
    async def task_assign(task_ref: str, assignee: str | None = None) -> dict[str, Any]:
        """Put a task on somebody, or take it off them — one revertible batch.

        `assignee` resolves against your colleagues and the account's own
        contacts: the qualified label a picker would show ("Sam Garcia —
        Atomic Industries") wins outright, a bare name resolves when exactly
        one person answers to it, and anything else is kept as typed. Omit it
        (or pass null) to clear the assignment. `task_ref` MUST be an exact id
        read from open_items or today_brief."""
        return _task_assign(rw, task_ref, assignee)

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
        if the field is already set, naming the current value and the
        `expecting` to pass edit_field instead; overwriting is that tool's
        job, not a thing this surface cannot do. `client` resolves the same
        way as every other client-scoped tool. `value` is normalized through
        the same cleaner the forms use for that field (email/phone/url/
        domain/naics) before being stored, and a closed vocabulary refuses a
        value outside its list. WHICH FIELDS: call `describe("org")` or
        `describe("contact")` — enrich runs over exactly the set edit_field
        writes, derived from the form declarations, so a list retyped here
        would be a second field table in prose and would rot the way the
        first one did (it already had: it named nine org fields and missed
        name and status, and both contact names and role)."""
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
    async def request_remove(request_ref: str) -> dict[str, Any]:
        """Take an information request off the book WITH ITS ITEMS, as one
        revertible batch — for a request filed in ERROR, which is a different
        fact from one withdrawn. A request you asked for and no longer need is
        withdrawn by setting `cancelled_at`, and stays in the book; this one
        was never true and goes. Refused once any item has been answered or
        received, because deleting the question deletes the client's answer
        with it — the refusal names the alternative. `request_ref` MUST be an
        exact id or ref read from requests_to_chase or open_items."""
        return _request_remove(rw, request_ref)

    @server.tool()
    async def request_item_remove(item_ref: str) -> dict[str, Any]:
        """Take ONE ask off a request — a line filed in error. The request
        survives even if this was its last item. Refused once the item has
        been answered; waive it instead if it is simply no longer needed.
        `item_ref` MUST be an exact id read from request_items."""
        return _request_item_remove(rw, item_ref)

    @server.tool()
    async def program_layers(placement_ref: str) -> dict[str, Any]:
        """A linked program's tower: lines (with the exact line ids the write
        tools take), every layer with its id, attach/limit/premium in cents,
        and the carrier panel on each layer — carrier, share as a PERCENT
        (matching the layer's own signed_pct, and the form program_bind
        takes), and that carrier's share of the layer premium in cents. A
        layer nobody is on has an empty panel, which is towerkit's 'To be
        placed'. Read this before ANY program write."""
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
    async def program_market_premium(
        placement_ref: str, layer_id: str, carrier: str,
        premium: str | None = None,
    ) -> dict[str, Any]:
        """State ONE market's premium on a layer its markets share — a
        differential, tax and fees on one paper, a non-concurrent quote.

        STATING ONE STATES THEM ALL: every other market on the layer is
        written at the figure it was already showing, and the layer's premium
        becomes their sum. Three numbers move and two are ones you did not
        send. Leave `premium` off to CLEAR the whole layer back to one premium
        split by share. Money in dollars ('520k', '$520,000');
        program_revert_file(batch) restores the file."""
        return _program_market_premium(
            rw, placement_ref, layer_id, carrier, premium
        )

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
    async def contact_remove(client: str, contact: str) -> dict[str, Any]:
        """Take a contact OFF a client — the inverse of contact_add, for a row
        that should never have been on that account (this tool filed a
        wholesaler as a client contact once, and nothing could undo it).
        Soft and revertible: the interactions they attended keep their record,
        and revert_batch puts the contact back. Removing the primary leaves the
        account with NO primary rather than promoting someone — say who should
        be primary and set them yourself. A contact who genuinely left the
        company is a different thing; this says the row was wrong."""
        return _contact_remove(rw, client, contact)

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
        Dates accept any human form. Add its lines of coverage with need_add."""
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
    async def line_add(
        name: str, abbr: str | None = None, acord_code: str | None = None
    ) -> dict[str, Any]:
        """Add a line of coverage to the book's vocabulary — additive and
        event-logged. READ lines_list FIRST: an exact duplicate is refused and
        names the line that already exists, and near matches come back in
        `near_matches` with their scores WITHOUT blocking the write. That
        warning is the point of this tool having a reply at all — 'Excess
        Liability' and 'Employers Liability' are genuinely different lines, so
        nothing can refuse for you, but a new line that scores 94 against one
        already on the book is almost always the same line spelled twice.
        Check `near_matches` and revert the batch if it is."""
        return _line_add(rw, name, abbr=abbr, acord_code=acord_code)

    @server.tool()
    async def market_approach(
        placement_ref: str,
        line: str,
        market: str | None = None,
        via: str | None = None,
        attach: str | None = None,
        limit: str | None = None,
        sent_on: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Record going to a market on one line of coverage of one placement
        (exact PLC ref — read list_programs). `line` is the exact name,
        abbreviation or id from lines_list; a miss names the nearest lines and
        writes nothing. At least one of `market` (the carrier whose paper it
        is) and `via` (the wholesaler or MGA it went through) is required —
        give `via` alone when the wholesaler has not named the paper yet,
        which is the truth rather than a gap, and fill the carrier in later.
        Both resolve as exact market names or refs. `attach` and `limit` say
        WHICH slab the approach is about, in dollars, for an excess tower;
        omit both for primary or the whole line. `sent_on` is a human date and
        defaults to today. Reuses the submission already out to that market on
        this placement rather than filing a second one. `clearance_warnings`
        comes back non-empty when somebody else is already reaching that same
        carrier on that same line through a different intermediary — a
        WARNING, never a refusal, because the double approach is sometimes
        deliberate; read it and decide."""
        return _market_approach(
            rw, placement_ref, line, market=market, via=via, attach=attach,
            limit=limit, sent_on=sent_on, notes=notes,
        )

    @server.tool()
    async def market_responded(
        response_ref: str,
        status: str | None = None,
        responded_on: str | None = None,
        quote_expires_on: str | None = None,
        rate: str | None = None,
        premium: str | None = None,
        fees: str | None = None,
        decline_reason: str | None = None,
        decline_reason_public: str | None = None,
    ) -> dict[str, Any]:
        """Record what a market said, onto the row market_approach created.
        `response_ref` MUST be an exact id from marketing_report's `responses`
        list — this tool never fuzzy-matches. `status` is the market-response
        vocabulary (anything outside it is refused with the list). `rate` is a
        rate per unit of exposure like '1.42' — NOT money and NOT a percent.
        `premium` and `fees` are dollars, as the carrier stated them; leave one
        out rather than passing zero, because a zero is a claim the carrier did
        not make. `decline_reason` is INTERNAL free text and never reaches a
        client; `decline_reason_public` takes the controlled public wording and
        is what a client-facing report prints — leave it blank to say nothing,
        which is safer than a sentence anyone will wish they had not written.
        `quote_expires_on` is the day THESE terms die — the date the whole
        chase queue is keyed on, so a quote recorded without one is on no
        clock at all; it is refused if it falls before the reply or before the
        package went out. The submission's own status is a roll-up of its
        response rows, and so now are its premium, limit, response date,
        expiry and decline reason: none of them is typed, and the recomputed
        status comes back as `submission_status`."""
        return _market_responded(
            rw, response_ref, status=status, responded_on=responded_on,
            quote_expires_on=quote_expires_on,
            rate=rate, premium=premium, fees=fees,
            decline_reason=decline_reason,
            decline_reason_public=decline_reason_public,
        )

    @server.tool()
    async def market_assign_line(submission_ref: str, line: str) -> dict[str, Any]:
        """Say which line of coverage a package's answer is about, for a
        submission that has none recorded. `submission_ref` MUST be an exact id
        from marketing_report's `submissions_with_no_line` list — those are the
        packages that went to a market with no response row under them, and
        they print in the report's own "Line of coverage not recorded" block.
        `line` is the exact name, abbreviation or id from lines_list; a miss
        names the nearest lines and writes nothing. NOTHING IS INVENTED: the
        package's own status, premium, limit, reply date, expiry and decline
        reason move onto the response row, and from then on the submission's
        columns are recomputed from its rows like every other package's — which
        is why `submission_status` comes back, and why it should read the same
        as it did before. A withdrawn package is refused (going back to that
        market is a new approach), and so is one that already has an answer on
        some line — use market_approach to add another line to that one."""
        return _market_assign_line(rw, submission_ref, line)

    @server.tool()
    async def submission_sent_on(response_ref: str, sent_on: str) -> dict[str, Any]:
        """Correct the date the package behind a market response went out.
        `response_ref` is an exact response id from marketing_report's
        `responses` list, the same id market_responded takes — the submission
        is what gets written and the response is the row you are holding.
        THIS MOVES EVERY LINE OF COVERAGE ON THAT PACKAGE, because one
        submission carries all of them; `responses_affected` names the rows it
        moved. Use it when a reply is refused for being dated before the
        submission went out AND the send date is the one that is wrong — when
        the reply is the typo, correct that with market_responded instead. A
        date that has not happened yet is refused, and so is one later than an
        answer already recorded against the package."""
        return _submission_sent_on(rw, response_ref, sent_on)

    @server.tool()
    async def submission_withdraw(submission_ref: str) -> dict[str, Any]:
        """WE PULLED THIS PACKAGE. `submission_ref` is an exact submission id —
        marketing_report hands them back in `responses[].submission_id` and in
        `submissions_with_no_line[].submission_id`. It writes ONE column and
        touches nothing else: what each market already said stays exactly where
        it is, on its own rows, in the report and in the client's workbook.
        Withdrawing is a decision about the SUBMISSION rather than a summary of
        what a market said, which is why no response status says it and why the
        roll-up never writes it or writes over it. Going back to that market
        afterwards is a NEW approach (market_approach opens a fresh package;
        this one is never reused), and `submission_reinstate` puts a package
        pulled by mistake straight back. Refused on one already withdrawn."""
        return _submission_withdraw(rw, submission_ref)

    @server.tool()
    async def submission_reinstate(submission_ref: str) -> dict[str, Any]:
        """Put a withdrawn package BACK at market, at whatever its response
        rows say it is — a package pulled while two markets were quoting comes
        back `quoted`, not `out`, and its premium, limit, reply date, expiry
        and decline reason are recomputed from those rows in the same act. A
        package with no rows comes back `out`: asked, nothing back yet.
        `submission_ref` is an exact submission id (marketing_report's
        `responses[].submission_id` / `submissions_with_no_line[]`). Refused on
        a package that is not withdrawn — there is nothing to put back, and
        what a market said is corrected with market_responded."""
        return _submission_reinstate(rw, submission_ref)

    @server.tool()
    async def set_placement_line(
        placement_ref: str,
        line: str,
        expiring_premium: str | None = None,
        expiring_exposure: str | None = None,
        expiring_rate: str | None = None,
        expiring_basis: str | None = None,
        expected_exposure: str | None = None,
        rating_basis: str | None = None,
        rate_per: str | None = None,
        limit_sought: str | None = None,
        attach_sought: str | None = None,
    ) -> dict[str, Any]:
        """State what one line of coverage on one placement is expected to do —
        the expiring figures the client's comparison is measured against, and
        the basis every market response on that line inherits. Creates the row
        or corrects it; there is only ever one per placement and line.
        `placement_ref` is an exact PLC ref, `line` an exact entry from
        lines_list. An EXPOSURE is money on a monetary basis and a whole count
        otherwise (a fleet is 42 power units, and 42 cannot be cents), so
        either figure is refused unless its basis is already stored or arrives
        in the same call — pass `rating_basis` (or `expiring_basis` for the
        expiring side) from the rating-basis vocabulary, which refuses with its
        list. `rate_per` is the denominator a rate is quoted against (100,
        1000, or 1 per unit). `expiring_rate` is stored rather than derived:
        deriving it needs the expiring exposure, which is a separate fact
        nobody may have recorded, and the report leaves the comparison blank
        instead of assuming exposure was flat. `attach_sought` is where the
        cover being asked for starts — blank means primary, which is the
        ordinary case, so state it only for an excess layer."""
        return _set_placement_line(
            rw, placement_ref, line,
            expiring_premium=expiring_premium,
            expiring_exposure=expiring_exposure,
            expiring_rate=expiring_rate,
            expiring_basis=expiring_basis,
            expected_exposure=expected_exposure,
            rating_basis=rating_basis,
            rate_per=rate_per,
            limit_sought=limit_sought,
            attach_sought=attach_sought,
        )

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

    @server.tool(description=_EDIT_FIELD_DESCRIPTION)
    async def edit_field(
        kind: str,
        ref: str,
        field: str,
        value: str,
        expecting: str | None = None,
        client: str | None = None,
    ) -> dict[str, Any]:
        """Registered with `description=_EDIT_FIELD_DESCRIPTION` (module
        level), which ends with `mcpsurface.VALUE_RULES` — the same sentence
        `describe` serves as its note. The two used to be hand-written and
        disagreed about whether money was dollars or cents, and describe is
        the one a model is told to call first."""
        return _edit_field(rw, kind, ref, field, value,
                           expecting=expecting, client=client)

    @server.tool()
    async def list_batches(
        limit: int = 20, days: int = 14, client: str | None = None
    ) -> list[dict[str, Any]]:
        """Recent changes to the book, newest first, each with the `ref` that
        `revert_batch` takes. NOT just this server's work: every batched write
        is here, whatever made it — this assistant, the TUI, or the web app —
        and each row's `tool` and `source` say which. So this answers "what
        changed on this account lately", not only "what did I change".
        `days` is the window (default 14). `client` narrows to one account
        (exact name or ref; on a miss the error lists the nearest candidates)
        — note that a batch which names no account, such as the one that
        CREATED that client, cannot be matched by it. Anything reverted
        already is marked `reverted: true`."""
        return _list_batches(rw, date.today(), limit=limit, days=days,
                             client=client)

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
        "lines_of_cover": sync.line_labels(placement.program_path, conn),
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


def _resolve_task(conn: sqlite3.Connection, task_ref: str) -> Any:
    """Exact ref only — a write target is never fuzzy-matched. The refusal
    names WHERE the right ref comes from, the standard the assignment/
    opportunity/project resolvers already set: repo.get raises a bare
    `task TSK-9999 not found`, which tells a model nothing it can act on."""
    from .repo import tasks as tasks_repo

    try:
        return tasks_repo.get(conn, task_ref)
    except KeyError:
        raise ValueError(
            f"no task {task_ref!r} — read open_items or today_brief for exact refs"
        ) from None


def _resolve_need(conn: sqlite3.Connection, need_ref: str) -> Any:
    """Need ids reach a caller through open_items FOR A CLIENT (the per-client
    branch carries each row's `ref`; the book-wide one does not) or as
    need_add's return."""
    from .repo import projects as projects_repo

    try:
        return projects_repo.get_need(conn, need_ref)
    except KeyError:
        raise ValueError(
            f"no project need {need_ref!r} — read open_items for that client "
            f"for exact ids"
        ) from None


def _resolve_rfi_item(conn: sqlite3.Connection, item_ref: str) -> RfiItem:
    from .repo import rfi as rfi_repo

    try:
        return rfi_repo.get_item(conn, item_ref)
    except KeyError:
        raise ValueError(
            f"no request item {item_ref!r} — read request_items for exact refs"
        ) from None


def _resolve_batch(conn: sqlite3.Connection, ref: str) -> EventBatch:
    """Resolved here rather than left to the service so the refusal names a
    recovery path. The extra lookup is deliberate: services.batches.revert
    resolves again, and one cheap SELECT is the price of not having one
    refusal style per tool."""
    from .repo import batches as batches_repo

    try:
        return batches_repo.get_by_ref(conn, ref)
    except KeyError:
        raise ValueError(
            f"no batch {ref!r} — read list_batches for exact refs"
        ) from None


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
    conn: sqlite3.Connection, client: str, note: str, follow_up: str | None = None,
    type: str = "note", occurred_on: str | None = None,
) -> dict[str, Any]:
    """`type` and `occurred_on` both default to what this tool used to
    hardcode, so nothing that called it before changes. They exist because an
    assistant could not record "the call I had with Sarah last Tuesday" — and
    could not correct it afterwards either, there being no interaction kind in
    edit_field. Both parse through machinery that already exists: the
    InteractionType vocabulary (refused with the list, like every other closed
    vocabulary here) and parse_human_date, which refuses a bare 1-2 digit
    number on purpose — dateparser reads "5" as a MONTH and future-biases it,
    which once saved a follow-up as 2027-05-01 and dropped it out of every
    attention window silently. That refusal is passed through, not routed
    around."""
    from .dates import parse_human_date
    from .forms.spec import date_refusal
    from .models import InteractionType
    from .repo import interactions
    from .repo import tasks as tasks_repo

    org = _resolve_client(conn, client)
    kind = _clean_typed(
        tuple(t.value for t in InteractionType), "type", type)
    when = date.today().isoformat()
    if occurred_on:
        happened = parse_human_date(occurred_on)
        if happened is None:
            raise ValueError(date_refusal(occurred_on))
        when = happened.isoformat()
    due = None
    if follow_up:
        parsed = parse_human_date(follow_up)
        if parsed is None:
            raise ValueError(date_refusal(follow_up))
        due = parsed.isoformat()
    with _open_batch(
        conn, tool="log_activity", org_id=org.id,
        summary=f"logged a {kind} on {org.name}"
        + (" with a follow-up task" if due else ""),
    ) as batch:
        interaction = interactions.log(
            conn, org.id, type=kind, occurred_on=when,
            subject=note[:80], body=note,
        )
        _provenance(conn, "interaction", interaction.id)
        task = None
        if due:
            task = tasks_repo.create(
                conn, f"Follow up: {note[:60]}", org_id=org.id, due_on=due)
            _provenance(conn, "task", task.id)
    return {"org_id": org.id, "interaction_ref": interaction.id,
            "type": interaction.type, "occurred_on": interaction.occurred_on,
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
    interactions.get filters on aliveness and raises KeyError, which this
    turns into a refusal naming where a real ref comes from."""
    from .repo import interactions

    try:
        interaction = interactions.get(conn, interaction_ref)
    except KeyError:
        # THE FIFTH BARE KeyError, on the path log_activity's own docstring
        # names as the only way to correct a mis-logged interaction: there is
        # no interaction kind in edit_field, so a model sent here by that
        # sentence and met with `interaction <id> not found` has no next step.
        raise ValueError(
            f"no activity {interaction_ref!r} — read recent_activity for that "
            f"client and use the `interaction_ref` it returns (an activity "
            f"already deleted is gone from it; `u` in the TUI puts one back)"
        ) from None
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
    assignee: str | None = None,
) -> dict[str, Any]:
    from .dates import parse_human_date
    from .forms.spec import date_refusal
    from .repo import assignees as assignees_repo
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
            raise ValueError(date_refusal(due))
        fields["due_on"] = parsed.isoformat()
    with _open_batch(
        conn, tool="task_create", org_id=fields.get("org_id"),
        summary=f"created task: {title}",
    ) as batch:
        task = tasks_repo.create(conn, title, **fields)
        if assignee is not None:
            # AFTER the task exists, inside the same batch. Assignment is never
            # a precondition: repo.assignees.columns resolves what it can and
            # keeps the rest as typed, so an unknown name is a note rather than
            # a refusal. Before this existed, a caller that wanted an assigned
            # task could not make one at all — and an assistant, unable to file
            # the open item, filed an information request instead: a question
            # put to a client, in their workbook (Grant, at work, 2026-08-19).
            assignees_repo.set_on_task(
                conn, task.id, assignee, org_id=fields.get("org_id"), note="mcp"
            )
            task = tasks_repo.get(conn, task.id)
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "title": task.title, "due": task.due_on,
            "assignee": assignees_repo.name_of(conn, task), "batch": batch.ref}


def _task_assign(
    conn: sqlite3.Connection, task_ref: str, assignee: str | None = None
) -> dict[str, Any]:
    """Set or clear one task's assignee. repo.assignees owns the three columns
    and writes them in one statement, so the batch owns them as one undo
    unit."""
    from .repo import assignees as assignees_repo
    from .repo import tasks as tasks_repo

    task = _resolve_task(conn, task_ref)
    with _open_batch(
        conn, tool="task_assign", org_id=task.org_id,
        summary=(
            f"assigned to {assignee}: {task.title}" if assignee
            else f"unassigned: {task.title}"
        ),
    ) as batch:
        assignees_repo.set_on_task(
            conn, task.id, assignee, org_id=task.org_id, note="mcp"
        )
    fresh = tasks_repo.get(conn, task.id)
    return {
        "task_ref": fresh.id, "title": fresh.title,
        "assignee": assignees_repo.name_of(conn, fresh), "batch": batch.ref,
    }


def _request_remove(conn: sqlite3.Connection, request_ref: str) -> dict[str, Any]:
    from .repo import rfi as rfi_repo
    from .services import rfi as rfi_svc

    found = rfi_repo.find_request(conn, request_ref)
    if found is None:
        raise ValueError(
            f"no information request {request_ref!r} — read requests_to_chase "
            f"for exact refs"
        )
    removed = rfi_svc.remove_request(conn, found.id, source="mcp")
    return {
        "removed": True, "request_ref": found.ref, "title": removed.title,
        "items_removed": removed.items, "batch": removed.batch,
    }


def _request_item_remove(conn: sqlite3.Connection, item_ref: str) -> dict[str, Any]:
    from .services import rfi as rfi_svc

    item = _resolve_rfi_item(conn, item_ref)
    removed = rfi_svc.remove_item(conn, item.id, source="mcp")
    return {
        "removed": True, "item_ref": item.id, "prompt": removed.prompt,
        "batch": removed.batch,
    }


def _task_complete(conn: sqlite3.Connection, task_ref: str) -> dict[str, Any]:
    """Status flip only. task_ref must be an exact id the model READ from
    open_items/today_brief — no fuzzy title matching, by design."""
    from .repo import tasks as tasks_repo

    task = _resolve_task(conn, task_ref)
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
    from .dates import parse_human_date
    from .forms.spec import date_refusal
    from .repo import contacts, interactions, orgs
    from .repo import tasks as tasks_repo
    from .services import orgs as orgs_svc

    dup = orgs_svc.find_duplicate(conn, name)
    if dup:
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
                    raise ValueError(date_refusal(t["due"]))
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
    its own kind (from mcpsurface's derived per-(entity, field) value type)
    before reaching here. Tuple value types are a closed vocabulary and go
    through _clean_typed instead."""
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


def _contact_remove(
    conn: sqlite3.Connection, client: str, contact: str
) -> dict[str, Any]:
    """Take a contact off a client account. The rules (clear is_primary first,
    one batch, promote nobody) belong to services.contacts.remove, which the
    TUI and the web call too — this resolves the names and reports."""
    from .repo import contacts as contacts_repo
    from .services import contacts as contacts_svc

    org = _resolve_client(conn, client)
    # exact match, never fuzzy: a write target is resolved the same way
    # _edit_target resolves one, and the refusal names who IS on the account.
    people = contacts_repo.for_org(conn, org.id, active_only=False)
    target = next((c for c in people if c.name.lower() == contact.lower()), None)
    if target is None:
        raise ValueError(
            f"no contact {contact!r} at {org.name}; have: {[c.name for c in people]}"
        )
    removed = contacts_svc.remove(conn, target.id, source="mcp")
    return {"contact_id": removed.contact_id, "name": removed.name,
            "removed": True, "was_primary": removed.was_primary,
            "interactions": removed.interactions, "detail": removed.message,
            "undo": "revert_batch puts them back", "batch": removed.batch}


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
    # Refused before the batch opens: the pair reaches a client through
    # services/export_open_items, so a reversed range must not exist even for
    # the length of a transaction that a later revert would have to clean up.
    consistency.check_project_dates(fields.get("start_on"), fields.get("end_on"))
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
    """services.program_files.raise_on_errors, with this surface's wording.

    The refusal it raises is a ProgramWriteRefused, which IS a ValueError, so
    every MCP caller's contract is unchanged — the web is the caller that
    needs the diagnostics inside it (services/program_files.py)."""
    from .services.program_files import ProgramWriteRefused, raise_on_errors

    try:
        return raise_on_errors(diags)
    except ProgramWriteRefused as refused:
        raise ValueError(
            "refused by towerkit's validator — nothing written: " + str(refused)
        ) from refused


def _program_write(
    conn: sqlite3.Connection,
    placement: Any,
    tool: str,
    summary: str,
    write: Any,
) -> tuple[Any, list[str]]:
    """One batched program-file write — services.program_files.write with this
    surface's batch source and its wording for a refusal.

    The body moved there on 2026-08-19 so the web could be the second caller
    of the same seam rather than a hand-copied twin of it. Nothing about this
    tool's contract changed: the refusal is still a ValueError carrying the
    same sentence.
    """
    from .services import program_files

    try:
        return program_files.write(
            conn, placement, tool=tool, summary=summary,
            mutate=write, open_batch=_open_batch,
        )
    except program_files.ProgramWriteRefused as refused:
        raise ValueError(
            "refused by towerkit's validator — nothing written: " + str(refused)
        ) from refused


def _program_layers(conn: sqlite3.Connection, placement_ref: str) -> dict[str, Any]:
    from towerkit.model import load_program

    from . import sync as sync_mod

    placement = _resolve_linked_placement(conn, placement_ref)
    program = load_program(sync_mod.program_file(conn, placement))
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


def _program_market_premium(
    conn: sqlite3.Connection, placement_ref: str, layer_id: str,
    carrier: str, premium: str | None = None,
) -> dict[str, Any]:
    from . import sync as sync_mod
    from .money import parse_money_cents

    placement = _resolve_linked_placement(conn, placement_ref)
    cents = parse_money_cents(premium) if premium else None
    batch, warnings = _program_write(
        conn, placement,
        tool="program_market_premium",
        summary=(
            f"cleared the stated market premiums on {layer_id}"
            if cents is None
            else f"stated {carrier}'s premium on {layer_id}"
        ),
        write=lambda: sync_mod.set_participant_premium(
            conn, placement.id, layer_id, carrier, cents
        ),
    )
    return {
        "layer_id": layer_id,
        "carrier": carrier,
        "premium_cents": cents,
        "cleared": cents is None,
        "warnings": warnings,
        "batch": batch.ref,
    }


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
    """services.program_files.revert_file, with this surface's ref resolution.

    The body moved there on 2026-08-19 so the web's Recent Changes rail could
    be the second caller rather than a hand-copied twin — the same move
    `_program_write` made. Nothing about this tool's contract changed."""
    from .services import program_files

    return program_files.revert_file(conn, _resolve_batch(conn, batch_ref))


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


def _member_deactivate(
    conn: sqlite3.Connection, name: str, cascade: bool = False
) -> dict[str, Any]:
    """Retire a colleague — the rule (refusal while assigned, cascade as ONE
    revertible batch) lives in services.team.member_deactivate now, shared
    with the web surface; this wrapper only resolves the name and stamps
    per-entity provenance through the hook."""
    from .services import team as team_svc

    member = _find_member(conn, name)
    result = team_svc.member_deactivate(
        conn, member.id, cascade=cascade, source="mcp", provenance=_provenance,
    )
    return {"name": result.name, "active": False,
            "unassigned": result.unassigned, "batch": result.batch}


def _member_reactivate(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """Bring a retired colleague back — services.team.member_reactivate owns
    the rule (see _member_deactivate)."""
    from .services import team as team_svc

    member = _find_member(conn, name)
    result = team_svc.member_reactivate(
        conn, member.id, source="mcp", provenance=_provenance,
    )
    return {"name": result.name, "active": True, "batch": result.batch}


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

    task = _resolve_task(conn, task_ref)
    with _open_batch(
        conn, tool="task_reopen", org_id=task.org_id,
        summary=f"reopened task: {task.title}",
    ) as batch:
        task = tasks_repo.reopen(conn, task_ref)
        _provenance(conn, "task", task.id)
    return {"task_ref": task.id, "status": task.status, "batch": batch.ref}


def _request_item_waive(conn: sqlite3.Connection, item_ref: str) -> dict[str, Any]:
    from .repo import rfi as rfi_repo

    item = _resolve_rfi_item(conn, item_ref)
    request = rfi_repo.get_request(conn, item.request_id)
    with _open_batch(
        conn, tool="request_item_waive", org_id=request.org_id,
        summary=f"waived an item: {item.prompt[:60]}",
    ) as batch:
        item = rfi_repo.update_item(conn, item.id, status="waived")
        _provenance(conn, "rfi_item", item.id)
    return {"item_ref": item.id, "status": item.status,
            "request_ref": request.ref, "batch": batch.ref}


# edit_field's allowlist: (kind -> field -> value type), DERIVED from the
# FormSpec builders in forms/entities.py and filtered by the denylist in
# mcpsurface.py. Add a Field(...) to a builder and the TUI, the web AND this
# surface get it; the denylist is what decides whether it should be reachable
# here, one field at a time, each with its reason written down.
#
# Each value is the field's real KIND as its form declares it — per-field, not
# a name-wide default, because a field NAME is not globally 1:1 with a kind:
# task.description is a one-line "text" (the textarea is `detail`) while
# project.description IS the textarea. "text" routes through the cleaner map
# (bookkit.forms.spec.CLEANERS) like enrich_field; "textarea" is stored
# verbatim; "money" parses to integer cents; "date" through parse_human_date;
# "int" plain; a tuple is a closed vocabulary and refusals list it.
#
# THE DELIBERATE ABSENCES NOW LIVE IN mcpsurface.DENIED, with a reason each.
# They used to be comments here, which is how the org entry came to be
# `dict(_ENRICHABLE_ORG)` with nothing recording that as a decision.
_EDITABLE: dict[str, dict[str, Any]] = mcpsurface.editable()

# Fields that exist but are owned by a transition tool. The generic refusal
# only lists what IS editable; these say where the caller should go instead.
# WHY each of these is denied is in mcpsurface.DENIED, one sentence per field;
# this table holds only the destination. tests/test_mcp_surface.py asserts
# every key here is actually denied there, so the two cannot disagree.
_EDIT_REDIRECTS: dict[tuple[str, str], str] = {
    ("team_member", "active"): "member_deactivate / member_reactivate",
    # status and received_on move together (services.rfi.mark_received), so
    # neither is a single-field compare-and-set. Without these the model got
    # the generic "not editable; allowed: [...]" list and no idea where to go.
    ("rfi_item", "status"): "request_item_received / request_item_waive",
    ("rfi_item", "received_on"): "request_item_received",
    ("task", "status"): "task_complete / task_reopen",
    ("task", "completed_at"): "task_complete / task_reopen",
    ("opportunity", "stage"): "opportunity_stage",
    ("opportunity", "outcome"): "opportunity_stage",
    ("opportunity", "closed_at"): "opportunity_stage",
    ("opportunity", "loss_reason"): "opportunity_stage",
    ("contact", "active"): "contact_remove",
}


# ONE OWNER FOR THE VALUE RULES. The money/date/select sentence lives in
# mcpsurface.VALUE_RULES and is interpolated here, so edit_field's description
# and describe's note are the same string rather than two hand-written
# accounts of one argument. They were two, and they contradicted each other.
_EDIT_FIELD_DESCRIPTION = (
    "Deliberately OVERWRITE one field — compare-and-set. `expecting` must be "
    "the value a read just showed you; a mismatch refuses and names what the "
    "field really holds, writing nothing. expecting omitted asserts the field "
    "is BLANK (use enrich_field for routine blank-filling). `kind` is "
    "org|contact|opportunity|project|project_need|task|team_member|"
    "team_assignment|rfi_request|rfi_item; `ref` is the exact name "
    "(org/contact/team_member — contact also needs `client`) or the exact "
    "ref/id a read returned — for team_assignment, the `assignment_id` that "
    "team_roster returns. Stage moves are opportunity_stage, never this. "
    "Call `describe` for the fields of a kind and their types.\n\n"
    + mcpsurface.VALUE_RULES
)


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
        from .forms.spec import date_refusal

        parsed = parse_human_date(value)
        if parsed is None:
            raise ValueError(date_refusal(value))
        return parsed.isoformat()
    if vtype == "int":
        return int(value)
    if isinstance(vtype, tuple):
        if value not in vtype:
            raise ValueError(f"{field!r} must be one of {list(vtype)}, not {value!r}")
        # a select over an INT column (task.priority): the options are strings
        # because every select's options are, but the column is not, and a
        # str written to it makes compare-and-set refuse '2' for holding 2.
        # mcpsurface.IntChoices carries that coercion off the column type.
        return int(value) if isinstance(vtype, mcpsurface.IntChoices) else value
    return _clean_by_kind(vtype, value)


def _as_expecting(vtype: Any, value: Any) -> str:
    """The stored value rendered as the string `expecting` would take back.

    A REFUSAL THAT NAMES A RETRY MUST NAME ONE THAT WORKS. `{value!r}` on an
    enum column prints `<OrgStatus.PROSPECT: 'prospect'>`, and a model that
    follows the instruction literally passes that repr and is refused again,
    now by the vocabulary check — the dead end this codebase treats as worse
    than silence. org.status is the surface's only enum-typed column and it
    became reachable with the derivation.
    """
    if value is None:
        return "None"
    if vtype == "money":
        from .money import format_cents

        return repr(format_cents(int(value)))
    return repr(str(value))


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

        need = _resolve_need(conn, ref)
        project = projects_repo.get_project(conn, need.project_id)
        return need.id, project.org_id, need
    if kind == "task":
        task = _resolve_task(conn, ref)
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

        item = _resolve_rfi_item(conn, ref)
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


def _guard_org_rename(
    conn: sqlite3.Connection, org_id: str, new_name: str
) -> None:
    """Delegates to repo/orgs, which owns the rule (see its docstring for the
    resolver corruption it stops). It has to be CALLED here as well as living
    there because `_edit_field` writes through `base.update` generically, not
    through `orgs.update` — the repo guard covers the TUI and the web, which
    both rename via forms.entities.apply_org, and this line covers MCP."""
    from .repo import orgs

    orgs.guard_name(conn, new_name, org_id)


# GUARDS ON IDENTITY, keyed by the field that carries it. A name is what every
# other tool resolves on, so a rename onto a name already in use makes every
# later lookup land on the wrong row — CLAUDE.md records that failure for team
# members, and org.name became writable with the derivation and had nothing.
# A table rather than a chain of ifs, because the next identity column should
# be one line and an argument about the reason, not a fourth branch.
_RENAME_GUARDS: dict[tuple[str, str], Callable[[sqlite3.Connection, str, Any], None]] = {
    ("team_member", "name"): _guard_member_rename,
    ("org", "name"): _guard_org_rename,
}


def _consistency_project(conn: sqlite3.Connection, row: Any, field: str, value: Any) -> None:
    consistency.check_project_dates(
        value if field == "start_on" else row.start_on,
        value if field == "end_on" else row.end_on,
    )


def _consistency_request(conn: sqlite3.Connection, row: Any, field: str, value: Any) -> None:
    consistency.check_request_dates(
        value if field == "requested_on" else row.requested_on,
        value if field == "due_on" else row.due_on,
    )


def _consistency_item_due(conn: sqlite3.Connection, row: Any, field: str, value: Any) -> None:
    from .repo import rfi as rfi_repo

    consistency.check_item_due(rfi_repo.get_request(conn, row.request_id).requested_on, value)


# CROSS-FIELD GUARDS, keyed by the field whose write could break the pair.
#
# `_edit_field` writes ONE column through `base.update`, which is exactly the
# shape a consistency rule is invisible to: nothing in a single-column write
# has any reason to look at the column beside it. The apply_* functions in
# forms/entities.py inherit the same rules from services/consistency.py, so
# the TUI, the web and the assistant refuse the same combinations with the
# same sentence — which is the whole reason those rules are a module and not
# five inline comparisons.
#
# Keyed BOTH WAYS on purpose: `end_on` typed before `start_on` and `start_on`
# typed after `end_on` are the same broken row, and a table that only guarded
# one of them would just teach a caller which order to break it in.
_CONSISTENCY_GUARDS: dict[
    tuple[str, str], Callable[[sqlite3.Connection, Any, str, Any], None]
] = {
    ("project", "start_on"): _consistency_project,
    ("project", "end_on"): _consistency_project,
    ("rfi_request", "requested_on"): _consistency_request,
    ("rfi_request", "due_on"): _consistency_request,
    ("rfi_item", "due_on"): _consistency_item_due,
}


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
        # a denied field says WHY, in the sentence mcpsurface records — a bare
        # "allowed: [...]" list tells a model nothing about whether to look
        # for another door or stop asking
        reason = mcpsurface.denial_reason(kind, field)
        if reason is not None:
            raise ValueError(f"{field!r} is not editable on a {kind}: {reason}")
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
                f"{kind}.{field} is not blank — it holds "
                f"{_as_expecting(vtype, current)}; pass expecting=<that "
                f"value> to overwrite deliberately"
            )
    elif blank or current != expected:
        raise ValueError(
            f"{kind}.{field} holds {_as_expecting(vtype, current)}, not what "
            f"you expected ({_as_expecting(vtype, expected)}) — re-read the "
            f"record and retry"
        )

    cleaned = _clean_typed(vtype, field, value)
    guard = _RENAME_GUARDS.get((kind, field))
    if guard is not None:
        guard(conn, entity_id, cleaned)
    pair_guard = _CONSISTENCY_GUARDS.get((kind, field))
    if pair_guard is not None:
        pair_guard(conn, row, field, cleaned)
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
    """Fill-blanks-only: refuses to touch a field that already has a value.
    Additive, single-field, event-logged. The overwrite path is edit_field,
    which takes `expecting` — the refusal below says so, because "edits happen
    in the TUI" stopped being true and a refusal that names no next step is
    the same dead end as a silent one."""
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
        # _as_expecting, not {current!r}: the derived surface put org.status —
        # the one enum-typed column — into this map, and a repr names an
        # `expecting` (<OrgStatus.PROSPECT: 'prospect'>) the vocabulary check
        # would then refuse. A refusal must name a retry that works.
        shown = _as_expecting(allowed[field], current)
        raise ValueError(
            f"{org.name}{' / ' + target.name if contact else ''} already has "
            f"{field}={shown} — enrich_field is fill-blanks-only; to "
            f"overwrite deliberately use edit_field with expecting={shown}")
    # _clean_typed, not _clean_by_kind: the derived surface carries closed
    # vocabularies (contact.role, org.status) as tuples, and _clean_by_kind
    # would fall through to clean_text and write a value outside the list
    cleaned = _clean_typed(allowed[field], field, value)
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

    # refused, never a silently-created row
    found = _resolve_rfi_item(conn, item_ref)
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
    from .forms.spec import date_refusal

    # A pasted numbered list must be cleaned identically here and in the TUI's
    # paste box, so this shares that splitter rather than re-deriving the
    # regex. rfi_paste is pure `re` and lives in imports/ — the MCP server is
    # headless and must never import from tui/ (test_mcpserver_never_imports_the_tui).
    from .imports.rfi_paste import split_items
    from .repo import placements
    from .repo import projects as projects_repo
    from .repo import rfi as rfi_repo

    if placement_ref and project_ref:
        raise ValueError(
            "a request is scoped to a placement OR a project, never both")
    org = _resolve_client(conn, client)
    fields: dict[str, Any] = {}
    if market:
        fields["market_org_id"] = _resolve_market(conn, market).id
    requested_on = date.today().isoformat()
    if due_on:
        parsed = parse_human_date(due_on)
        if parsed is None:
            raise ValueError(date_refusal(due_on))
        fields["due_on"] = parsed.isoformat()
        # A request is always asked TODAY on this surface, so "by last friday"
        # — which parse_human_date happily reads — would file a request that is
        # overdue in the same breath it is created. Same rule the form path
        # inherits in forms/entities.apply_request.
        consistency.check_request_dates(requested_on, fields["due_on"])
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
            conn, org.id, title, requested_on, **fields)
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
    conn: sqlite3.Connection, today: date, limit: int = 20, days: int = 14,
    client: str | None = None,
) -> list[dict[str, Any]]:
    """Recent batched writes, newest first, and whether they have been put
    back. EVERY source, not just this server's — repo.batches.recent has no
    source filter and never had one, while the tool docstring claimed
    otherwise for long enough that a model would never have reached for this
    to answer "what changed on this account this week". `source` is returned
    so the caller can tell them apart rather than being told a half-truth."""
    from .repo import batches as batches_repo
    from .services import batches as batches_svc

    org_id = _resolve_client(conn, client).id if client is not None else None
    since = (today - timedelta(days=days)).isoformat()
    recent = batches_repo.recent(
        conn, since=since, limit=limit, org_id=org_id)
    labels = batches_svc.account_names(conn, recent)  # one query, not N
    out = []
    for batch in recent:
        account = None
        if batch.org_id:
            account = labels.get(batch.org_id, "(deleted account)")
        out.append({
            "ref": batch.ref, "tool": batch.tool, "summary": batch.summary,
            "source": batch.source, "account": account, "at": batch.created_at,
            "reverted": batch.reverted_at is not None,
        })
    return out


def _revert_batch(
    conn: sqlite3.Connection, ref: str, now: str, force: bool = False
) -> dict[str, Any]:
    """Undo one batched write. Refuses outright if anything in it was changed
    since, listing what blocks it — never a partial write unless forced."""
    from .services import batches as batches_svc

    _resolve_batch(conn, ref)          # refuse with a recovery path, not KeyError
    result = batches_svc.revert(conn, ref, now=now, force=force)
    return {
        "ref": result.batch.ref,
        "applied": result.applied,
        "reverted": [
            {"entity": c.entity_type, "id": c.entity_id, "field": c.field}
            for c in result.reverted
        ],
        "refused": [
            # `why` is the planner's own sentence where it has one — a
            # dependent conflict is not a field that moved, so entity/field/
            # current describe it wrongly on their own, and the assistant
            # would report "the submission was created" as the blocker. One
            # home for that sentence (services/batches.dependent_clause), read
            # by this surface and the browser's toast alike.
            {"entity": c.change.entity_type, "id": c.change.entity_id,
             "field": c.change.field, "batch_set": c.change.new_value,
             "current": c.current_value, "why": c.clause}
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
                # include_internal=True: this is Grant's own assistant
                # reading his own book. Hiding internal work from him here
                # would be the silent failure the feature exists to prevent —
                # the rows come through, flagged.
                for s in export_open_items.compose(
                    conn, org.id, today, include_internal=True)
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
             "category": t.category, "due": t.due_on,
             # the same fact the per-client rows carry, so an Internal task
             # is labelled in BOTH branches of this tool, not just one
             "internal": is_internal_category(t.category)}
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


def _opportunities(
    conn: sqlite3.Connection, client: str | None = None,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    """Deals, WITH refs — the same hole _recent_activity was added to close
    for interactions, still open one entity over. pipeline_status returns
    per-stage aggregates and _search returns no ids by design, so before this
    the only OPP- ref a model could ever hold was one returned by
    opportunity_create or opportunity_stage in the same session: every deal
    on the book was unreachable to opportunity_stage and to
    edit_field(kind="opportunity"). Book-wide by default and each row names
    its account, so one call turns a remembered description into a ref."""
    from .money import format_cents
    from .repo import opportunities as opportunities_repo
    from .repo import orgs

    closed = ("won", "lost")
    if client is not None:
        org = _resolve_client(conn, client)
        opps = opportunities_repo.for_org(
            conn, org.id, open_only=not include_closed)
        names = {org.id: org.name}
    else:
        opps = [
            o for o in opportunities_repo.by_stage(conn)
            if include_closed or str(o.stage) not in closed
        ]
        names = {o.id: o.name for o in orgs.list_orgs(conn)}
    return [
        {
            "opportunity_ref": o.ref,
            "account": names.get(o.org_id, "(deleted account)"),
            "title": o.title,
            "stage": str(o.stage),
            "lines": o.lines,
            "target_premium": format_cents(o.target_premium)
            if o.target_premium else None,
            "target_effective": o.target_effective,
            "probability_pct": o.probability_pct,
            "source": o.source,
        }
        for o in opps
    ]


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


# --- marketing: who we went to, and what they said ---------------------------
#
# Seven tools over repo/lines.py, repo/marketing.py and
# services/marketing_report.py. NO RULE OF THEIR OWN lives here: the near-match
# warning, the duplicate refusal, the carrier-or-intermediary CHECK, the
# submission roll-up and the clearance warning are all in repo/, where the web
# inherits them too. What lives here is resolution, entry cleaning, and the
# shape of a reply.


def _resolve_placement(conn: sqlite3.Connection, placement_ref: str) -> Any:
    """_resolve_linked_placement's sibling for the marketing verbs, which work
    on the BOOK's placement row rather than on a towerkit file: a program
    nobody has drawn yet is still a placement being marketed, and requiring a
    linked file would put the whole renewal cycle behind a tower."""
    from .repo import placements as placements_repo

    placement = placements_repo.find(conn, placement_ref)
    if placement is None:
        raise ValueError(
            f"no placement {placement_ref!r} — read list_programs for exact PLC refs"
        )
    return placement


def _resolve_line(conn: sqlite3.Connection, line: str) -> Any:
    """The exact name, abbreviation or slug of a line of coverage — a write
    target is never fuzzy-matched. The near matches go in the REFUSAL, which
    is repo/lines.py's rule (advisory, never a block) applied to resolution."""
    from .repo import lines as lines_repo

    found = lines_repo.by_name(conn, line) or lines_repo.get(conn, line)
    if found is not None:
        return found
    near = lines_repo.near_matches(conn, line, cutoff=60)
    hint = ", ".join(f"{m.name} ({m.id})" for m, _score in near) if near else "none close"
    raise ValueError(
        f"no line of coverage matching {line!r} — nearest: {hint}. Read "
        f"lines_list for the vocabulary, or line_add to record a new one."
    )


def _resolve_response(conn: sqlite3.Connection, response_ref: str) -> Any:
    """Exact id only, and the refusal names where one comes from:
    marketing_report's `responses` index exists so this tool has a ref to
    take, the way open_items exists for task_complete."""
    from .repo import marketing

    try:
        return marketing.get_response(conn, response_ref)
    except KeyError:
        raise ValueError(
            f"no market response {response_ref!r} — read marketing_report for "
            f"that placement and use an id from its `responses` list"
        ) from None


def _rate_micros(field: str, value: str | None) -> int | None:
    """A RATE IS NOT MONEY, so it does not go through parse_money_cents.

    The parser, the ×1,000,000 scale and the refusal all live in money.py —
    this is the tool-argument wrapper over them, and it exists only to name
    which argument was refused. There were three copies of that scale before
    2026-08-25 and the web needed a fourth; a rate parsed one way here and
    another way in a cell editor is the same 1.42 stored a millionfold
    apart."""
    from .money import MoneyParseError, parse_rate_micros

    if value is None:
        return None
    try:
        return parse_rate_micros(value)
    except MoneyParseError as exc:
        # money.py owns the parser AND the sentence; this adds only the
        # argument name, which is the one thing a tool caller needs and the
        # parser cannot know.
        raise ValueError(f"{field!r}: {exc}") from None


def _exposure(basis_key: str | None, field: str, value: str | None) -> int | None:
    """CENTS when the basis is monetary, a whole COUNT when it is not — the one
    decision models.RatingBasis.monetary exists to make, READ here rather than
    judged here.

    Refuses without a basis instead of picking one: 42 power units and $0.42
    are the same digits, and a client-facing report renders them a
    hundred-thousandfold apart."""
    from .models import RATING_BASIS_KEYS, rating_basis

    if value is None:
        return None
    if basis_key is None:
        raise ValueError(
            f"{field!r} needs its rating basis in the same call — one of "
            f"{list(RATING_BASIS_KEYS)} — so the figure is stored as money or "
            f"as a count rather than guessed"
        )
    if rating_basis(basis_key).monetary:
        from .money import parse_money_cents

        return int(parse_money_cents(value))
    # money.py owns the count parser and its refusal, exactly as it owns the
    # money and rate ones. This branch used to hold its own `int(...)`, and
    # the web needed the same rule for its exposure cells — a second copy that
    # floored or refused differently would file a fraction as a count on one
    # surface and refuse it on the other.
    from .money import MoneyParseError, parse_count

    try:
        return parse_count(value)
    except MoneyParseError as exc:
        raise ValueError(f"{field!r} on a {basis_key!r} basis: {exc}") from None


def _check_basis(field: str, key: str | None) -> str | None:
    """models.rating_basis is the vocabulary check and raises with the list;
    calling it for its refusal is how a basis is validated even when no
    exposure arrives with it."""
    from .models import rating_basis

    if key is None:
        return None
    rating_basis(key)
    return key


def _lines_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from .repo import lines as lines_repo

    return [
        {
            "line_id": line.id,
            "name": line.name,
            "abbr": line.abbr,
            "acord_code": line.acord_code,
        }
        for line in lines_repo.all_lines(conn)
    ]


def _line_add(
    conn: sqlite3.Connection,
    name: str,
    abbr: str | None = None,
    acord_code: str | None = None,
) -> dict[str, Any]:
    """The near-match warning reaches the REPLY, not only the refusal.

    repo/lines.py refuses an exact duplicate and deliberately does NOT refuse a
    near one — `Excess Liability` and `Employers Liability` are four letters
    apart and are not the same thing, so a block nobody can override makes a
    correct entry impossible. A human sees that warning beside the field and
    decides; an assistant that never sees it is exactly how a fifth spelling
    of General Liability gets in. So it is measured BEFORE the write and
    returned with the new line rather than swallowed.

    The duplicate guard is repo's and is caught, not re-implemented here: a
    second copy in a caller is the thing repo/team.py's comment is about, and
    the failed create rolls back with its own batch row."""
    from .repo import lines as lines_repo

    near = [
        {"line_id": match.id, "name": match.name, "score": score}
        for match, score in lines_repo.near_matches(conn, name)
    ]
    with _open_batch(
        conn, tool="line_add", summary=f"added line of coverage {name!r}"
    ) as batch:
        try:
            line_id = lines_repo.create(
                conn, name, abbr=abbr, acord_code=acord_code
            )
        except lines_repo.DuplicateLine as dup:
            raise ValueError(
                f"a line of coverage named {dup.existing.name!r} already exists "
                f"as {dup.existing.id!r} — use that one rather than a second "
                f"spelling of it"
            ) from None
        _provenance(conn, "line_of_coverage", line_id)
    line = lines_repo.get(conn, line_id)
    assert line is not None  # just written
    return {
        "line_id": line.id,
        "name": line.name,
        "abbr": line.abbr,
        "acord_code": line.acord_code,
        "near_matches": near,
        "batch": batch.ref,
    }


def _market_approach(
    conn: sqlite3.Connection,
    placement_ref: str,
    line: str,
    market: str | None = None,
    via: str | None = None,
    attach: str | None = None,
    limit: str | None = None,
    sent_on: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record an approach: who we went to, on which line of coverage, through
    whom.

    THE SUBMISSION IS THE PACKAGE AND THE RESPONSE IS THE ANSWER. One
    submission goes to one market carrying every line, and the response rows
    hang off it one per line — so an approach REUSES the live submission
    already out to that market on this placement and creates one only when
    there is none. Otherwise one email becomes three submissions and "who did
    we approach" stops being answerable, which is the shape migration 015's
    header refuses.

    The submission is addressed to the INTERMEDIARY where there is one: a
    package sent to RT Specialty went to RT Specialty, whatever paper they
    come back with. `submission.market_org_id` is NOT NULL, so this is not a
    preference — it is the only truthful value available at that moment.

    Clearance conflicts are WARNED, never refused, exactly as repo/marketing
    reports them: the double approach is sometimes deliberate and a hard block
    would make a legitimate entry impossible (the `line-gap` rule again)."""
    from .repo import marketing, orgs
    from .services import marketing_entry

    if not (market or via):
        raise ValueError(
            "an approach needs `market` (the carrier) or `via` (the wholesaler "
            "or MGA) — if the wholesaler has not named the paper yet, give "
            "`via` alone and fill the carrier in later with market_responded"
        )
    placement = _resolve_placement(conn, placement_ref)
    coverage = _resolve_line(conn, line)
    carrier = _resolve_market(conn, market) if market else None
    intermediary = _resolve_market(conn, via) if via else None
    addressed = intermediary or carrier
    assert addressed is not None  # one of the two, checked above

    when = (
        _clean_typed("date", "sent_on", sent_on)
        if sent_on
        else date.today().isoformat()
    )
    fields: dict[str, Any] = {}
    if attach:
        fields["attach"] = _clean_typed("money", "attach", attach)
    if limit:
        fields["lim"] = _clean_typed("money", "limit", limit)
    if notes:
        fields["notes"] = notes

    with _open_batch(
        conn,
        tool="market_approach",
        org_id=placement.org_id,
        summary=f"approached {addressed.name} on {coverage.name}",
    ) as batch:
        # THE SUBMISSION RULE IS THE SERVICE'S, not this tool's — the web's
        # add-market row records the same act and must reuse the same live
        # submission (services/marketing_entry.py).
        recorded = marketing_entry.approach(
            conn,
            placement.id,
            coverage.id,
            sent_on=when,
            market_org_id=carrier.id if carrier is not None else None,
            via_org_id=intermediary.id if intermediary is not None else None,
            **fields,
        )
        response, submission = recorded.response, recorded.submission
        _provenance(conn, "market_response", response.id)

    conflicts = marketing.clearance_conflicts(conn, response)
    names = orgs.names_for(
        conn, {c.via_org_id or "" for c in conflicts} - {""}
    )
    return {
        "response_id": response.id,
        "line": coverage.name,
        "market": carrier.name if carrier else None,
        "via": intermediary.name if intermediary else None,
        "status": response.status,
        "submission_id": submission.id,
        "submission_is_new": recorded.submission_is_new,
        "sent_on": submission.sent_on,
        "clearance_warnings": [
            f"{carrier.name if carrier else 'this carrier'} is already being "
            f"reached on {coverage.name} via "
            f"{names.get(c.via_org_id or '', 'a direct approach')}"
            for c in conflicts
        ],
        "batch": batch.ref,
    }


def _market_responded(
    conn: sqlite3.Connection,
    response_ref: str,
    status: str | None = None,
    responded_on: str | None = None,
    quote_expires_on: str | None = None,
    rate: str | None = None,
    premium: str | None = None,
    fees: str | None = None,
    decline_reason: str | None = None,
    decline_reason_public: str | None = None,
) -> dict[str, Any]:
    """What the market said, onto the row that recorded the approach.

    The submission's status is NOT written here and cannot be: repo/marketing
    rolls it up from its response rows after every write, because two
    hand-maintained copies of one fact disagree and then nobody knows which is
    right. The rolled-up value comes back in the reply so the caller can see
    what its edit did to the parent.

    The two decline reasons are two fields on purpose. `decline_reason` is
    internal free text and NEVER reaches a client; `decline_reason_public`
    takes the controlled vocabulary and is what a client-facing report
    prints."""
    from .models import PUBLIC_DECLINE_REASONS
    from .repo import lines as lines_repo
    from .repo import orgs, placements
    from .repo import submissions as submissions_repo
    from .services import marketing_entry

    response = _resolve_response(conn, response_ref)
    changes: dict[str, Any] = {}
    if status is not None:
        changes["status"] = status  # repo._validate_status refuses with the list
    if responded_on is not None:
        changes["responded_on"] = _clean_typed("date", "responded_on", responded_on)
    if quote_expires_on is not None:
        # THE DATE THE CHASE QUEUE IS KEYED ON. It lived only on the
        # submission and only the Pipeline's form could write it, so the
        # assistant could record a quote and had no way to say when it dies —
        # "built but not accessible", on the field whose absence loses money
        # rather than time (2026-08-26).
        changes["quote_expires_on"] = _clean_typed(
            "date", "quote_expires_on", quote_expires_on
        )
    if rate is not None:
        changes["rate_micros"] = _rate_micros("rate", rate)
    if premium is not None:
        changes["premium"] = _clean_typed("money", "premium", premium)
    if fees is not None:
        changes["policy_fees"] = _clean_typed("money", "fees", fees)
    if decline_reason is not None:
        changes["decline_reason"] = decline_reason
    if decline_reason_public is not None:
        changes["decline_reason_public"] = _clean_typed(
            PUBLIC_DECLINE_REASONS, "decline_reason_public", decline_reason_public
        )
    if not changes:
        raise ValueError(
            "market_responded was given nothing to record — pass at least one "
            "of status, responded_on, quote_expires_on, rate, premium, fees or "
            "a decline reason"
        )

    submission = submissions_repo.get(conn, response.submission_id)
    org_id = (
        placements.get(conn, submission.placement_id).org_id
        if submission.placement_id
        else None
    )
    coverage = lines_repo.get(conn, response.line_id)
    who = orgs.names_for(
        conn, {response.market_org_id or "", response.via_org_id or ""} - {""}
    )
    market_name = who.get(response.market_org_id or "") or who.get(
        response.via_org_id or "", "a market"
    )
    said = status or "an update"
    with _open_batch(
        conn,
        tool="market_responded",
        org_id=org_id,
        summary=(
            f"recorded {said} from {market_name} on "
            f"{coverage.name if coverage else response.line_id}"
        ),
    ) as batch:
        # THE SERVICE, not the repo writer under it — the same home the web's
        # response cell posts through, so "a date that witnesses an act cannot
        # be in the future" binds the assistant too. `responded_on` reaches
        # here as a human date through `_clean_typed`, and parse_human_date
        # FUTURE-BIASES a bare month and day.
        fresh = marketing_entry.responded(conn, response.id, changes)

    rolled = submissions_repo.get(conn, fresh.submission_id)
    return {
        "response_id": fresh.id,
        "line": coverage.name if coverage else response.line_id,
        "market": market_name,
        "status": fresh.status,
        "responded_on": fresh.responded_on,
        "quote_expires_on": fresh.quote_expires_on,
        "submission_id": rolled.id,
        "submission_status": str(
            rolled.status.value if hasattr(rolled.status, "value") else rolled.status
        ),
        "batch": batch.ref,
    }


def _submission_sent_on(
    conn: sqlite3.Connection,
    response_ref: str,
    sent_on: str,
) -> dict[str, Any]:
    """Correct the date the package behind one market response went out.

    A REFUSAL MUST NEVER NAME A FIX THAT DOES NOT EXIST, and this one did.
    `repo.marketing._reply_guard` refuses a reply dated before its submission
    went out and its sentence names two ways out — correct the reply, or
    correct the date the submission went out. The web grew the second one as
    the grid's Sent cell on 2026-08-26; on MCP there was no `submission_*`
    tool at all, so one transposed digit in `market_approach` wedged the reply
    date on every row of that package, permanently, with the assistant being
    told to make a correction it had no verb for (D8). A change that lands on
    the web and not on MCP has shipped to two thirds of its users.

    ADDRESSED BY THE RESPONSE, the way the web's cell is: the submission is
    what gets written, but the response is the row the caller is holding (it
    is what `marketing_report` hands back), one response hangs off exactly one
    submission, and `_resolve_response` is the scope check that already
    exists. A ref naming the submission would need a second resolver for a row
    no report prints.

    IT MOVES EVERY LINE ON THE PACKAGE, and the reply says which: one
    submission carries every line of coverage it was sent on, so this is never
    a one-row edit and a caller that thinks it is would mis-report what it
    just did. Both refusals are inherited rather than restated — the future
    check is `services.consistency`'s (the same rule `market_approach`
    applies) and "not after an answer already recorded" is
    `repo.submissions._sent_guard`'s, under the write itself.
    """
    from datetime import date as _date

    from .repo import marketing, orgs, placements
    from .repo import submissions as submissions_repo
    from .services import consistency

    response = _resolve_response(conn, response_ref)
    when = _clean_typed("date", "sent_on", sent_on)
    consistency.check_not_future(
        when, label="a submission sent", today=_date.today().isoformat()
    )
    submission = submissions_repo.get(conn, response.submission_id)
    org_id = (
        placements.get(conn, submission.placement_id).org_id
        if submission.placement_id
        else None
    )
    who = orgs.names_for(conn, {submission.market_org_id})
    market_name = who.get(submission.market_org_id, "a market")
    with _open_batch(
        conn,
        tool="submission_sent_on",
        org_id=org_id,
        summary=f"corrected the date we went to {market_name}",
    ) as batch:
        submissions_repo.update(conn, submission.id, sent_on=when)

    fresh = submissions_repo.get(conn, submission.id)
    moved = marketing.responses_for_submission(conn, submission.id)
    return {
        "submission_id": fresh.id,
        "market": market_name,
        "sent_on": fresh.sent_on,
        # EVERY ROW THIS MOVED, named. The caller asked about one response and
        # changed the package all of them hang off.
        "responses_affected": [r.id for r in moved],
        "batch": batch.ref,
    }


def _resolve_submission(conn: sqlite3.Connection, submission_ref: str) -> Any:
    """A submission by its exact id, with the refusal naming where an id comes
    from — the shape `_market_assign_line` settled on, lifted out because two
    verbs now take one.

    A submission has no REF of its own (no `SUB-0004`): it is an internal row a
    report hands back, which is why every tool that addresses one says "exact
    id" and names the index it came from rather than pretending there is a
    human-typable handle.
    """
    from .repo import submissions as submissions_repo

    try:
        return submissions_repo.get(conn, submission_ref)
    except KeyError:
        raise ValueError(
            f"no submission {submission_ref!r} — read marketing_report for that "
            f"placement and use an id from its `responses` list "
            f"(`submission_id`) or from `submissions_with_no_line`"
        ) from None


def _submission_org_and_market(
    conn: sqlite3.Connection, submission: Any
) -> tuple[str | None, str]:
    """(the account this package belongs to, the market it went to).

    NAMED DEAD OR ALIVE, so the undo sentence says who the package went to even
    where that market has since been merged away — the reading the composer
    takes about the same fact.
    """
    from .repo import orgs, placements

    org_id = (
        placements.get(conn, submission.placement_id).org_id
        if submission.placement_id
        else None
    )
    named = orgs.names_for_any(conn, {submission.market_org_id or ""})
    return org_id, named.get(submission.market_org_id or "", "this market")


def _submission_withdraw(
    conn: sqlite3.Connection, submission_ref: str
) -> dict[str, Any]:
    """We pulled the package. One column, one batch, nothing cascading.

    IT EXISTS BECAUSE THE CAPABILITY LOST ITS ONLY DOOR. The Pipeline's
    Response form used to offer the SUBMISSION statuses and was the one writer
    of `withdrawn` anywhere in the app; pointing that form at `market_response`
    on 2026-08-26 correctly gave it the response vocabulary, which has no such
    word — and MCP never had one at all (`_edit_field` refuses kind
    'submission'). A state three code paths refuse on, that nothing can enter,
    is a rule with no subject; and a control the browser gets and the assistant
    does not has shipped to two thirds of its users (CLAUDE.md).

    The rule is `services.marketing_entry.withdraw`'s, shared with the web, so
    the refusal on an already-withdrawn package is the same sentence on both.
    """
    from .services import marketing_entry

    submission = _resolve_submission(conn, submission_ref)
    org_id, market_name = _submission_org_and_market(conn, submission)
    with _open_batch(
        conn,
        tool="submission_withdraw",
        org_id=org_id,
        summary=f"withdrew the package to {market_name}",
    ) as batch:
        marketing_entry.withdraw(conn, submission.id)
    return {
        "submission_id": submission.id,
        "market": market_name,
        "status": "withdrawn",
        # WHAT STAYS. Naming the count is how a caller can see that pulling a
        # package did not remove the marketing it recorded.
        "responses_kept": len(
            _responses_for(conn, submission.id)
        ),
        "batch": batch.ref,
    }


def _submission_reinstate(
    conn: sqlite3.Connection, submission_ref: str
) -> dict[str, Any]:
    """Put a withdrawn package back, at whatever its rows say it is.

    NOT 'out' BY DEFAULT — see `services.marketing_entry.reinstate`, which owns
    that rule and shares it with the web's Reinstate button. Handing the
    recomputed status back is how a caller can see which one it took.
    """
    from .repo import submissions as submissions_repo
    from .services import marketing_entry

    submission = _resolve_submission(conn, submission_ref)
    org_id, market_name = _submission_org_and_market(conn, submission)
    with _open_batch(
        conn,
        tool="submission_reinstate",
        org_id=org_id,
        summary=f"put the package to {market_name} back at market",
    ) as batch:
        marketing_entry.reinstate(conn, submission.id)
    fresh = submissions_repo.get(conn, submission.id)
    return {
        "submission_id": fresh.id,
        "market": market_name,
        "status": str(
            fresh.status.value if hasattr(fresh.status, "value") else fresh.status
        ),
        "quoted_premium": fresh.quoted_premium,
        "quoted_limit": fresh.quoted_limit,
        "quote_expires_on": fresh.quote_expires_on,
        "batch": batch.ref,
    }


def _responses_for(conn: sqlite3.Connection, submission_id: str) -> list[Any]:
    from .repo import marketing

    return marketing.responses_for_submission(conn, submission_id)


def _set_placement_line(
    conn: sqlite3.Connection,
    placement_ref: str,
    line: str,
    expiring_premium: str | None = None,
    expiring_exposure: str | None = None,
    expiring_rate: str | None = None,
    expiring_basis: str | None = None,
    expected_exposure: str | None = None,
    rating_basis: str | None = None,
    rate_per: str | None = None,
    limit_sought: str | None = None,
    attach_sought: str | None = None,
) -> dict[str, Any]:
    """What ONE line of coverage on ONE placement is expected to do: the
    expiring figures a client's comparison is built on, and the exposure and
    basis every market response inherits unless it overrides them.

    UPSERT, deliberately: "this line expects X" has no meaningful difference
    between the first statement and the second, and repo/marketing owns the
    one-row-per-(placement, line) rule so this cannot write a second row.

    An exposure is stored as CENTS or as a whole COUNT depending on its basis,
    and is refused when no basis is known — 42 power units and $0.42 are the
    same digits."""
    from .repo import marketing

    placement = _resolve_placement(conn, placement_ref)
    coverage = _resolve_line(conn, line)
    current = marketing.placement_line(conn, placement.id, coverage.id)

    expiring_key = _check_basis("expiring_basis", expiring_basis) or (
        current.expiring_basis if current else None
    )
    current_key = _check_basis("rating_basis", rating_basis) or (
        current.rating_basis if current else None
    )

    fields: dict[str, Any] = {}
    if expiring_premium is not None:
        fields["expiring_premium"] = _clean_typed(
            "money", "expiring_premium", expiring_premium
        )
    if expiring_exposure is not None:
        fields["expiring_exposure"] = _exposure(
            expiring_key, "expiring_exposure", expiring_exposure
        )
    if expiring_rate is not None:
        fields["expiring_rate_micros"] = _rate_micros("expiring_rate", expiring_rate)
    if expiring_basis is not None:
        fields["expiring_basis"] = expiring_basis
    if expected_exposure is not None:
        fields["expected_exposure"] = _exposure(
            current_key, "expected_exposure", expected_exposure
        )
    if rating_basis is not None:
        fields["rating_basis"] = rating_basis
    if rate_per is not None:
        fields["rate_per"] = _clean_typed("int", "rate_per", rate_per)
    if limit_sought is not None:
        fields["limit_sought"] = _clean_typed("money", "limit_sought", limit_sought)
    if attach_sought is not None:
        fields["attach_sought"] = _clean_typed("money", "attach_sought", attach_sought)
    if not fields:
        raise ValueError(
            "set_placement_line was given nothing to set — pass at least one "
            "expiring figure, an expected exposure, a basis, a rate_per, a "
            "limit sought or an attach sought"
        )

    with _open_batch(
        conn,
        tool="set_placement_line",
        org_id=placement.org_id,
        summary=f"set expectations for {coverage.name} on {placement.ref}",
    ) as batch:
        row = marketing.set_placement_line(
            conn, placement.id, coverage.id, **fields
        )

    return {
        "placement_ref": placement.ref,
        "line": coverage.name,
        "line_id": coverage.id,
        "expiring_premium": row.expiring_premium,
        "expiring_exposure": row.expiring_exposure,
        "expiring_rate_micros": row.expiring_rate_micros,
        "expiring_basis": row.expiring_basis,
        "expected_exposure": row.expected_exposure,
        "rating_basis": row.rating_basis,
        "rate_per": row.rate_per,
        "limit_sought": row.limit_sought,
        "attach_sought": row.attach_sought,
        "batch": batch.ref,
    }


def _report_text(report: Any, sections: list[Any], headers: list[str]) -> str:
    """The composed report as text a model can read in one piece.

    Columns that are blank the whole way down a section are DROPPED from that
    section: eighteen headers over a block where four carry anything is the
    undifferentiated density the UI rule names, and here it is also paid for
    in tokens. Nothing is summarised away — every row and every non-empty cell
    survives."""
    out = [
        f"{report.account} — {report.program}",
        f"period {report.period} · as of {report.as_of} · {report.audience} report",
    ]
    for section in sections:
        out.append("")
        out.append(str(section.label or ""))
        rows = [
            tuple(list(r) + [""] * (len(headers) - len(r)))[: len(headers)]
            for r in section.rows
        ]
        keep = [
            i for i in range(len(headers)) if any(row[i].strip() for row in rows)
        ]
        if not keep:
            keep = [0]
        widths = [
            max([len(headers[i])] + [len(row[i]) for row in rows]) for i in keep
        ]
        out.append(
            "  ".join(
                headers[i].ljust(w) for i, w in zip(keep, widths, strict=True)
            ).rstrip()
        )
        for row in rows:
            out.append(
                "  ".join(
                    row[i].ljust(w) for i, w in zip(keep, widths, strict=True)
                ).rstrip()
            )
    return "\n".join(out)


def _marketing_report(
    conn: sqlite3.Connection,
    placement_ref: str,
    audience: str = "client",
    as_of: str | None = None,
) -> dict[str, Any]:
    """The composed marketing report, rendered as text.

    `today` is a PARAMETER all the way down: services/marketing_report never
    reads the wall clock, so a report can be composed as of any date and two
    runs of the same date agree. This tool is the only place a default is
    applied, and it says so in the reply's `as_of`.

    The `responses` index is not decoration — a composed row carries no id, so
    without it there is no way to name the row market_responded should
    update."""
    from .repo import lines as lines_repo
    from .repo import marketing, orgs
    from .services import marketing_report as report_svc

    placement = _resolve_placement(conn, placement_ref)
    audiences = (report_svc.CLIENT, report_svc.INTERNAL)
    if audience not in audiences:
        raise ValueError(
            f"audience must be one of {list(audiences)}, not {audience!r}"
        )
    today = (
        date.fromisoformat(_clean_typed("date", "as_of", as_of))
        if as_of
        else date.today()
    )
    report = report_svc.compose(conn, placement.id, today, audience)
    sections = report_svc.to_sections(report)
    headers = [header for header, _width, _right in report_svc.columns(audience)]

    responses = marketing.responses_for_placement(conn, placement.id)
    vocabulary = {line.id: line.name for line in lines_repo.all_lines(conn)}
    # THE INDEX NAMES THINGS THAT ALREADY HAPPENED, so it reads the same way
    # the composed report above it does: a line of coverage that has been
    # retired and a market org that has been deleted are still the line and
    # the market this response was recorded against. Through the living
    # lookups the agent got a raw slug for one and `null` for the other — the
    # id it needs to name the row is here, and the words it needs to say
    # WHICH row were missing (2026-08-25, the same defect the panel had).
    for line_id in {r.line_id for r in responses} - set(vocabulary):
        retired = lines_repo.get_any(conn, line_id)
        if retired is not None:
            vocabulary[line_id] = retired.name
    who = orgs.names_for_any(
        conn,
        ({r.market_org_id or "" for r in responses}
         | {r.via_org_id or "" for r in responses}) - {""},
    )
    return {
        "account": report.account,
        "program": report.program,
        "period": report.period,
        "as_of": report.as_of,
        "audience": report.audience,
        "report": _report_text(report, sections, headers),
        "responses": [
            {
                "response_id": r.id,
                # THE PACKAGE THIS ANSWER HANGS OFF, so a verb addressed to the
                # SUBMISSION has a ref to take. `submission_withdraw` and
                # `submission_reinstate` are about the package rather than
                # about one line's answer, and a submission has no ref of its
                # own — before this the only submission ids the assistant could
                # see were the ones with NO answers at all
                # (`submissions_with_no_line`), so a package could be pulled
                # only while nobody had replied to it.
                "submission_id": r.submission_id,
                "line": vocabulary.get(r.line_id, r.line_id),
                "market": who.get(r.market_org_id or ""),
                "via": who.get(r.via_org_id or ""),
                "status": r.status,
                # WHEN THESE TERMS DIE. The rendered report above is the
                # CLIENT's columns and deliberately does not carry it (the
                # expiry is the broker's chase clock), so without it here the
                # assistant could WRITE an expiry through `market_responded`
                # and never read one back — half a link in the chain CLAUDE.md
                # says a schema change is not done without.
                "quote_expires_on": r.quote_expires_on,
            }
            for r in responses
        ],
        # THE PACKAGES WITH NO LINE OF COVERAGE RECORDED, and the ids
        # `market_assign_line` takes. The rendered report above prints these
        # rows in their own block (services.marketing_report's provisional
        # block), and without this index the assistant could READ that a market
        # was approached and had no way to name the row it would fix — the same
        # half-a-link the `responses` index exists to close one table up.
        "submissions_with_no_line": [
            {
                "submission_id": row.submission_id,
                "market": row.market,
                "status": row.status_key,
                "sent_on": row.submitted_on,
                "responded_on": row.responded_on,
                "quoted_premium": row.premium,
                "quoted_limit": row.lim,
                "quote_expires_on": row.quote_expires_on,
            }
            for row in report.provisional
        ],
    }


def _market_assign_line(
    conn: sqlite3.Connection, submission_ref: str, line: str
) -> dict[str, Any]:
    """Give a package the line of coverage nobody recorded.

    NOTHING IS INVENTED: the six facts the submission itself recorded move onto
    the response row that will state them from now on, and the line is the one
    thing the caller supplies because it is the one thing not in the data. The
    rule is services.marketing_entry.assign_line's — the same one the web's
    assign control uses — so the refusals a withdrawn or already-answered
    package raises are the same words on both surfaces.

    Exact submission id only, and the refusal names where one comes from:
    marketing_report's `submissions_with_no_line` index exists so this tool has
    a ref to take, the way `responses` exists for market_responded.
    """
    from .repo import orgs as orgs_repo
    from .repo import submissions as submissions_repo
    from .services import marketing_entry

    try:
        submission = submissions_repo.get(conn, submission_ref)
    except KeyError:
        raise ValueError(
            f"no submission {submission_ref!r} — read marketing_report for that "
            f"placement and use an id from its `submissions_with_no_line` list"
        ) from None
    coverage = _resolve_line(conn, line)
    placement_id = submission.placement_id
    if placement_id is None:
        # An OPPORTUNITY's submission. There is no placement behind it, so
        # there is no marketing panel and no `placement_line` — and the
        # Pipeline's own Response form is where an opportunity's answer is
        # recorded, line and all.
        raise ValueError(
            f"submission {submission_ref!r} is on an opportunity rather than a "
            f"placement — record what the market said on the opportunity's own "
            f"Response form, which asks for the line of coverage"
        )
    placement = _resolve_placement(conn, placement_id)
    # NAMED DEAD OR ALIVE, so the undo toast says who the package went to even
    # where that market has since been deleted from the book — the reading the
    # composer takes about the same fact.
    named = orgs_repo.names_for_any(conn, {submission.market_org_id or ""})
    market_name = named.get(submission.market_org_id or "", "this market")
    with _open_batch(
        conn,
        tool="market_assign_line",
        org_id=placement.org_id,
        summary=f"{coverage.name} for the package to {market_name}",
    ) as batch:
        response = marketing_entry.assign_line(conn, submission.id, coverage.id)
        _provenance(conn, "market_response", response.id)
    rolled = submissions_repo.get(conn, submission.id)
    return {
        "response_id": response.id,
        "submission_id": submission.id,
        "line": coverage.name,
        "status": response.status,
        # THE PACKAGE, RECOMPUTED FROM ITS ONE ROW. It should read exactly as
        # it read before — every status maps to one that rolls back up to
        # itself — and handing it back is how a caller can see that it did.
        "submission_status": str(rolled.status),
        "quoted_premium": rolled.quoted_premium,
        "quoted_limit": rolled.quoted_limit,
        "batch": batch.ref,
    }
