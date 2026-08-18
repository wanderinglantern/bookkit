"""Account page shell: header, tabs, right rail, and the helpers every tab
module shares. No SQL here — reads go through repo/ and services/.

Four tabs (Program, Relationship, Work, Pipeline) replace the old three-tab
Overview/Contacts/Interactions shell — see
docs/superpowers/specs/2026-08-17-web-visual-direction.md. The renewal
invariant survives the rail's removal: the header badge and the right
rail's snapshot row both print `RenewalItem.renewal_on` and
`RenewalItem.days_remaining` from the SAME object, never
`placement.period_to`.

This module used to own every tab's route. Task 8 split it: it now keeps
only the shell (this file) plus the helpers below (_conn, _org, _header,
_context, _save) — routes/relationship.py, routes/work.py and
routes/pipeline.py import them rather than redefining them, so the four
tasks that touch a tab each land in one file instead of colliding here.
"relationship" is no longer in _PANEL_TEMPLATE: routes/relationship.py
registers its own GET /accounts/{ref}/relationship, and app.py includes
that router before this one so the specific route wins over this module's
generic {tab} catch-all (both match the same two-segment path)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import money, sync
from ...forms.spec import BatchSpec, FieldError, FormSpec, parse_values
from ...models import (
    Contact,
    EventBatch,
    Interaction,
    Org,
    Placement,
    RfiItem,
    RfiRequest,
    Task,
)
from ...repo import base as base_repo
from ...repo import batches as batches_repo
from ...repo import contacts as contacts_repo
from ...repo import interactions as interactions_repo
from ...repo import opportunities as opportunities_repo
from ...repo import orgs as orgs_repo
from ...repo import placements as placements_repo
from ...repo import projects as projects_repo
from ...repo import rfi as rfi_repo
from ...repo import submissions as submissions_repo
from ...repo import tasks as tasks_repo
from ...repo import team as team_repo
from ...services import batches as batches_svc
from ...services import book as book_service
from ...services import renewals
from ...services import rfi as rfi_service
from ..app import TEMPLATES
from ..forms_render import render_form

router = APIRouter()

# Tab id -> label, in tab-bar order. "relationship" is the default landing
# tab (Grant, 2026-08-17): Program/Work/Pipeline all need writes this task
# doesn't build; Relationship is the one a broker actually opens first.
TABS: tuple[tuple[str, str], ...] = (
    ("program", "Program"),
    ("relationship", "Relationship"),
    ("work", "Work"),
    ("pipeline", "Pipeline"),
)
DEFAULT_TAB = "relationship"

# "relationship" deliberately absent — see the module docstring.
_PANEL_TEMPLATE = {
    "program": "account/program.html",
    "work": "account/work.html",
    "pipeline": "account/pipeline.html",
}


def _conn(request: Request) -> sqlite3.Connection:
    """THIS THREAD's connection, never a shared one — the read routes are sync
    defs and FastAPI runs those in an anyio worker threadpool, so concurrent
    requests are concurrent threads.

    Not every caller is one, though: the eight `async def` write routes in
    relationship.py and work.py run on the event loop, so this returns the
    LOOP thread's connection to all of them at once (under uvicorn, that is
    app.state.conn). What keeps them apart is that asyncio does not preempt
    and nothing awaits inside a transaction — see web.app.ThreadConnections
    for both halves, and for what sharing one cost the read path."""
    return request.app.state.connections.get()  # type: ignore[no-any-return]


def _org(request: Request, ref: str) -> Org:
    org = orgs_repo.find(_conn(request), ref)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no account matches {ref!r}")
    return org


# --- ownership: {ref} plus an entity id is TWO claims, and both get checked --
#
# Every route below /accounts/{ref}/ that also names an entity id is making a
# compound claim: this account, AND this row of it. Only the id was ever
# resolved, so a stale tab or a pasted URL could edit — or REMOVE — a row
# belonging to another account while the page rendered this one's, and the
# contact remove-confirm rendered a header naming one account beside a
# consequence naming the other (fix round 1, 2026-08-18). routes/changes.py
# had the check because it was written for it specifically; the other eighteen
# did not. So the rule lives HERE, once, the way team name-uniqueness had to
# live in repo/team.py: a guard in one caller is a guard the other eighteen
# write straight past. (Eighteen is the count the code carries — five handlers
# in relationship.py, thirteen in work.py — and tests/test_web_scoping.py
# drives every one of them.)

_Owned = TypeVar("_Owned", Contact, Task, RfiRequest, RfiItem, Interaction)


def _not_here(kind: str, entity_id: str, org: Org) -> HTTPException:
    """One sentence for every mismatch, and it says nothing about the OTHER
    account. 404, not 403: from this page's point of view the row does not
    exist, and naming whose it is would leak a name off an account the request
    has not asked for."""
    return HTTPException(status_code=404, detail=f"no {kind} {entity_id!r} on {org.name}")


def _owner_org_ids(
    conn: sqlite3.Connection, entity: Contact | Task | RfiRequest | RfiItem | Interaction
) -> set[str]:
    """Which account(s) an entity belongs to — the ownership rule itself.

    A task is the one with two answers, and the reason this is a set:
    repo.tasks.open_tasks_for_client joins through placement because a
    placement-attached task may carry `org_id` NULL and still be the client's.
    `task.org_id == org.id` alone would 404 rows the same page just rendered.
    An item belongs to whoever its request does — items carry no org of their
    own."""
    if isinstance(entity, Contact | RfiRequest | Interaction):
        return {entity.org_id}
    if isinstance(entity, RfiItem):
        try:
            return _owner_org_ids(conn, rfi_repo.get_request(conn, entity.request_id))
        except KeyError:
            # An item whose request was removed belongs to no account any more,
            # so it is not this one's: 404, the same answer every other miss
            # gets. This call sits OUTSIDE `_owned`'s try, so letting the
            # KeyError out was a 500 and a traceback — and htmx drops 5xx
            # exactly as it drops 4xx (fix round 2). Mirrors the deleted
            # placement two lines below.
            return set()
    owners = {entity.org_id} if entity.org_id else set()
    if entity.placement_id:
        try:
            owners.add(placements_repo.get(conn, entity.placement_id).org_id)
        except KeyError:  # a deleted placement carries nothing onto the page
            pass
    return owners


def _owned(
    conn: sqlite3.Connection,
    org: Org,
    kind: str,
    entity_id: str,
    fetch: Callable[[sqlite3.Connection, str], _Owned],
) -> _Owned:
    """THE guard: resolve the entity, prove it is this account's, hand it back.

    `fetch` is the caller's own repo getter, so the entity comes back TYPED and
    the route needs no second read. Both refusals — unknown id and someone
    else's id — are the same 404, deliberately: telling the two apart is how a
    guessable id becomes a membership oracle."""
    try:
        entity = fetch(conn, entity_id)
    except KeyError:
        raise _not_here(kind, entity_id, org) from None
    if org.id not in _owner_org_ids(conn, entity):
        raise _not_here(kind, entity_id, org)
    return entity


def _owned_item(
    conn: sqlite3.Connection, org: Org, request_id: str, item_id: str
) -> RfiItem:
    """An item is reached through TWO ids, so both are checked: the request is
    this account's, and the item is that request's. Without the second check an
    item could be edited under a request it does not belong to — the panel that
    comes back would list a row the write never touched."""
    _owned(conn, org, "request", request_id, rfi_repo.get_request)
    item = _owned(conn, org, "item", item_id, rfi_repo.get_item)
    if item.request_id != request_id:
        raise _not_here("item", item_id, org)
    return item


def _owns_raw_row(conn: sqlite3.Connection, org: Org, kind: str, entity_id: str) -> None:
    """The same ownership question asked of the RAW row, dead ones included.

    Only the two DESTRUCTIVE flows need this — removing a contact, deleting an
    interaction. Their repo getters are alive-filtered, so guarding those
    routes through `_owned` would turn an already-removed row into "no contact
    …"/"no interaction …" — burying the services' far better "already
    removed"/"already deleted", which is precisely what a double-submitted
    confirm, or a stale second tab, produces. Ownership is checked here;
    liveness stays the service's answer to give.

    Both halves of each flow use it, GET and POST: the confirm GET is reached
    from a control the page rendered while the row was alive, and htmx swaps
    neither 4xx nor 5xx, so a bare 404 there is no swap, no message and no
    change at all."""
    row = base_repo.raw_row(conn, kind, entity_id)
    if row is None or str(row["org_id"]) != org.id:
        raise _not_here(kind, entity_id, org)


def _owns_contact_row(conn: sqlite3.Connection, org: Org, contact_id: str) -> None:
    """Kept as its own name because relationship.py's two removal routes read
    better for it; the rule itself lives once, above."""
    _owns_raw_row(conn, org, "contact", contact_id)


def _save(
    request: Request,
    org: Org,
    spec: FormSpec,
    action: str,
    raw: dict[str, str],
    write: Any,
) -> HTMLResponse | None:
    """Parse, then run `write` inside ONE batch. Returns a re-rendered form
    fragment on refusal (input intact, nothing written), or None on success.

    Shared by every tab module's whole-record forms (contacts' `new`, and
    later tasks' own). The exception propagates out of open_batch so the
    transaction rolls back: a refused save leaves nothing behind and costs
    nothing retyped."""
    try:
        values = parse_values(spec, raw)
    except FieldError as exc:
        return HTMLResponse(render_form(request, spec, action, exc.message, raw))

    batch = BatchSpec.for_title(spec.title, org_id=org.id)
    try:
        with batches_svc.open_batch(
            _conn(request), source="web", tool=batch.tool,
            summary=batch.sentence(values), org_id=org.id,
        ):
            write(values)
    except Exception as exc:  # a refused save is a message, never a 500
        return HTMLResponse(render_form(request, spec, action, str(exc), raw))
    return None


def _header(conn: sqlite3.Connection, org: Org) -> dict[str, Any]:
    """THE RENEWAL DATE IS RenewalItem.renewal_on, never placement.period_to.
    Printing period_to beside a renewal_on countdown is the bug that made a
    future date render as '70d over' on four surfaces. Both the header's
    overdue badge and the right rail's snapshot row read renewal_on and
    days_remaining off this SAME dict, built from one RenewalItem."""
    item = renewals.next_for_org(conn, org.id)
    if item is None:
        return {
            "org": org, "renewal_on": None, "days_remaining": None,
            "overdue": False, "renewal_placement": None,
        }
    return {
        "org": org,
        "renewal_on": item.renewal_on,
        "days_remaining": item.days_remaining,
        "overdue": item.days_remaining < 0,
        # The PLACEMENT, carried but never printed as a date: the snapshot's
        # tower rows describe this one program and need its id, its name and
        # its ref. period_to stays off every surface — the countdown and the
        # date it counts to are still renewal_on's alone, above.
        "renewal_placement": item.placement,
    }


def _open_needs_count(conn: sqlite3.Connection, org_id: str) -> int:
    """Needs still in ATTENTION_STATUSES across every project on this
    account — the same filter tui/screens/account.py applies per project."""
    count = 0
    for project in projects_repo.projects_for_org(conn, org_id):
        for need in projects_repo.needs_for_project(conn, project.id):
            if need.status in projects_repo.ATTENTION_STATUSES:
                count += 1
    return count


def _open_requests_count(conn: sqlite3.Connection, org_id: str) -> int:
    """Open is derived, not stored — services.rfi.is_open is the one rule."""
    return sum(
        1 for r in rfi_repo.requests_for_org(conn, org_id) if rfi_service.is_open(conn, r.id)
    )


def _open_work_count(conn: sqlite3.Connection, org_id: str) -> int:
    open_tasks = tasks_repo.open_tasks_for_client(conn, org_id)
    return len(open_tasks) + _open_needs_count(conn, org_id) + _open_requests_count(conn, org_id)


def _counts(conn: sqlite3.Connection, org: Org, open_work: int) -> dict[str, int]:
    """Tab badge counts — every one a real repo read, never a placeholder.
    `open_work` is computed once by the caller and shared with `_snapshot`
    (review round 1, 2026-08-17: it was computed twice per render)."""
    contacts = contacts_repo.for_org(conn, org.id)
    interactions = interactions_repo.for_org(conn, org.id, limit=200)
    opportunities = opportunities_repo.for_org(conn, org.id, open_only=False)
    submissions_count = sum(
        len(submissions_repo.for_opportunity(conn, o.id)) for o in opportunities
    )
    return {
        "program": len(placements_repo.for_org(conn, org.id)),
        "relationship": len(contacts) + len(interactions),
        "work": open_work,
        "pipeline": len(opportunities) + submissions_count,
    }


def _unplaced_value(layers: list[dict[str, Any]]) -> str:
    """The open share AND the layer it is open on — "20% on 3rd Excess".

    A bare percentage does not say where the hole is, and a hole on the
    primary is a different conversation from one at the top of the tower.
    The widest hole leads because that is the one being worked; a "+N" tail
    says how many others there are rather than spilling a list into a 296px
    rail. "none" when every layer is fully signed — that is a real read with
    a real answer, not an absent one."""
    open_layers = sorted(
        (layer for layer in layers if layer["signed_pct"] < 100),
        key=lambda layer: layer["signed_pct"],
    )
    if not open_layers:
        return "none"
    widest = open_layers[0]
    # signed_pct is bps/100, so a whole percent prints whole ("20%", not
    # "20.0%") and a part-percent share still shows its fraction
    share = 100 - widest["signed_pct"]
    text = f"{share:g}% on {widest['name']}"
    # "+1 more", not "+1": beside a percentage a bare "+1" scans as "+1%"
    # (review round 1, item E).
    return text if len(open_layers) == 1 else f"{text} +{len(open_layers) - 1} more"


# No figure to print. NOT "$0": a zero is a claim about the money, and both
# places this appears are places where there is no claim to make (review round
# 1, item B). One treatment for both, because they are the same defect — the
# omit guard tested for "no program", not for "no data".
NO_FIGURE = "—"


def _program_premium(scope: str, layers: list[dict[str, Any]]) -> tuple[str, str]:
    """(value, title) for the `program premium` row.

    Three cases, and the two that are not "every layer priced" are why this is
    a function. Every layer's premium unset sums to 0 and would print
    `$0` — a program worth nothing rather than a program whose premium is not
    recorded. SOME unset silently understates a MONEY figure, which is exactly
    what the money-columns-say-whose-money rule is about: it prints with a `~`
    and the title names how many layers are missing. `~` rather than dropping
    the row, because a partial total is still the best figure available and
    hiding it helps nobody; rather than a footnote, because the rail is 296px
    and the mark has to travel with the number."""
    priced = [layer for layer in layers if layer["premium_cents"] is not None]
    missing = len(layers) - len(priced)
    if not priced:
        return NO_FIGURE, f"{scope} · no layer carries a premium"
    total = money.format_cents_compact(sum(layer["premium_cents"] for layer in priced))
    if not missing:
        return total, scope
    return f"~{total}", f"{scope} · {missing} of {len(layers)} layers carry no premium"


def _top_of_tower(scope: str, layers: list[dict[str, Any]]) -> tuple[str, str]:
    """(value, title) for the `top of tower` row.

    This row is the tallest DOLLAR column, which is Grant's call (2026-08-18)
    and standard broker shorthand. His reason for keeping it is also the reason
    it cannot be computed from arithmetic alone: **WC, if present, is the
    tallest thing in the program and has no dollar limit at all.** towerkit's
    `Layer.statutory` is "no dollar limit (WC Part A); limit MUST be 0"
    (`towerkit/src/towerkit/model.py:98`), so a statutory layer contributes 0 to
    `max(attach + limit)` — identical, to `max()`, to a layer with no cover.
    They are opposite facts.

    So the flag is read, never inferred. Inferring it from `top <= 0` (which is
    what this function did before the flag existed) makes a confident domain
    claim about any all-zero program, statutory or not, and is silent on the
    common case: a real tower that ALSO carries unlimited statutory cover, where
    the printed figure is right and incomplete. The tower diagram draws that
    layer above everything else (`TowerLayout.chevrons`), so a rail that never
    mentions it disagrees with the picture beside it."""
    statutory = [layer for layer in layers if layer["statutory"]]
    priced = [layer for layer in layers if not layer["statutory"]]
    if not priced:
        # Every layer is statutory: a tower with no ceiling, not one with no
        # height. There is no figure, so no figure is printed.
        return NO_FIGURE, f"{scope} · no dollar limit (statutory cover)"
    top = max(layer["attach_cents"] + layer["limit_cents"] for layer in priced)
    if top <= 0:
        # Not statutory, and still zero — a real gap in the file rather than
        # unlimited cover. Say that, rather than borrowing statutory's sentence.
        return NO_FIGURE, f"{scope} · no layer carries a limit"
    value = money.format_cents_compact(top)
    if statutory:
        return value, f"{scope} · tallest dollar layer; also unlimited statutory cover"
    return value, scope


def _tower_rows(
    placement: Placement | None, layers: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The three rows that describe ONE program, wrapped in a group captioned
    with its name.

    THE SCOPE IS THE WHOLE RISK HERE. `bound premium`, one row above, is
    summed over every bound placement on the account; these three are one
    placement's tower. Money columns say whose money — the book's per-account
    figure was once whichever placement renewed next, which showed revenue
    that did not exist. So the group is captioned with the program name and
    the placement ref, and each row also carries that caption as a `title`:
    `program premium` cannot be read as an account total when the line above
    it says "Casualty Program · PLC-0006".

    The caption and its rows are returned as ONE nested group, not as a flat
    run under a sentinel row. Positional membership was the weakness the
    caption could not cover: an account-scoped row inserted between two of
    these rendered under the program caption and inside the rule, and every
    test passed (review round 1, item C). Nesting makes membership structural.

    None when the placement has no linked program file (layer_details returns
    [] then). A zero would be a lie — "$0 program premium" reads as a program
    worth nothing rather than as no program at all."""
    if not layers or placement is None:
        return None
    scope = f"{placement.program_name} · {placement.ref}"
    premium_value, premium_title = _program_premium(scope, layers)
    top_value, top_title = _top_of_tower(scope, layers)
    unplaced = _unplaced_value(layers)
    rows = [
        ("program premium", premium_value, premium_title, False),
        ("top of tower", top_value, top_title, False),
        # warn, never colour alone: the share and the layer name both read
        # without it
        ("unplaced", unplaced, scope, unplaced != "none"),
    ]
    return {
        "scope": scope,
        "rows": [
            {
                "label": label, "value": value, "overdue": False, "muted": False,
                "warn": warn, "scoped": True, "title": title,
            }
            for label, value, title, warn in rows
        ],
    }


def _snapshot(
    conn: sqlite3.Connection,
    org: Org,
    header: dict[str, Any],
    open_work: int,
    layers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Only rows with a real read behind them. `layers` is the renewal
    placement's tower, read once by the caller (it is file I/O) — see
    `_tower_rows` for why those three rows are captioned and why they are
    omitted rather than zeroed."""
    rows: list[dict[str, Any]] = []
    if header["renewal_on"]:
        overdue = header["overdue"]
        days = header["days_remaining"]
        suffix = f"{-days}d over" if overdue else f"{days}d"
        rows.append({
            "label": "next renewal",
            "value": f"{header['renewal_on']} · {suffix}",
            "overdue": overdue,
            "muted": False,
        })
    rows.append({
        "label": "bound premium",
        "value": money.format_cents_compact(book_service.bound_premium_for_org(conn, org.id)),
        "overdue": False,
        "muted": False,
    })
    tower = _tower_rows(header["renewal_placement"], layers)
    if tower is not None:
        rows.append(tower)
    rows.append({
        "label": "open work",
        "value": str(open_work),
        "overdue": False,
        "muted": False,
    })
    last = interactions_repo.last_for_org(conn, org.id)
    if last is not None:
        # muted per the design source's own inline style for this row
        # (color:#7B7974) — every other snapshot value takes --ink.
        rows.append({
            "label": "last touch", "value": last.occurred_on, "overdue": False, "muted": True,
        })
    return rows


def _change_time(created_at: str, today: date) -> str:
    """HH:MM for a change from today; the bare ISO date otherwise — a change
    from three days ago must not read as if it just happened."""
    if "T" not in created_at:
        return created_at
    day, time_part = created_at.split("T", 1)
    return time_part[:5] if day == today.isoformat() else day


def _change_row(batch: EventBatch, today: date) -> dict[str, Any]:
    # `ref` is what the rail's Revert button POSTs to (routes/changes.py) —
    # the batch's own ref, never its position in the list, because the list
    # is re-read on every render and a reverted row drops out of it.
    return {
        "ref": batch.ref,
        "time": _change_time(batch.created_at, today),
        "what": batch.summary,
        "who": batch.source,
        "reverted": batch.reverted_at is not None,
    }


def _revert_action(org_ref: str, batch_ref: str, tab: str) -> str:
    """One url shape, built in one place: the rail's per-change Revert and the
    top bar's Undo pill are the same POST against different batches."""
    return f"/accounts/{org_ref}/changes/{batch_ref}/revert?tab={tab}"


def _context(
    conn: sqlite3.Connection, org: Org, tab: str, request: Request
) -> dict[str, Any]:
    """`request` is here for its query string alone: a revert answers with an
    HX-Redirect carrying an outcome token, and the toast that token renders
    belongs to whichever tab page the browser lands on. Building it here rather
    than in each tab route is what stops the next tab from forgetting it — the
    shell owns the toast the same way it owns the rail."""
    header = _header(conn, org)
    open_work = _open_work_count(conn, org.id)
    counts = _counts(conn, org, open_work)

    # ONE layer_details call per render: it opens and parses the towerkit
    # JSON file, so a second caller is a second disk read of the same bytes —
    # the same de-duplication `open_work` got in review round 1, 2026-08-17.
    # Empty list when the renewal placement has no linked file, which is what
    # makes the tower rows omit themselves rather than print zeros.
    renewal_placement = header["renewal_placement"]
    layers = (
        sync.layer_details(conn, renewal_placement.id)
        if renewal_placement is not None
        else []
    )

    # One read of recent batches, scoped to this account, shared by both the
    # RECENT CHANGES list and the top-bar Undo pill (review round 1,
    # 2026-08-17: batches_repo.recent used to be called twice per render).
    # since="" sorts before every real ISO timestamp — "most recent N, no
    # cutoff" — the one verified read for this (repo.batches.recent).
    org_batches = [
        b for b in batches_repo.recent(conn, since="", limit=200) if b.org_id == org.id
    ]
    today = date.today()
    changes = [_change_row(b, today) for b in org_batches[:8]]
    last_undo = next((b for b in org_batches if b.reverted_at is None), None)

    # partials/topbar.html is shared with /book, which has no `tab` in its
    # context — so the pill's POST url is assembled HERE, whole, rather than
    # from two variables in the shared partial. The partial then needs nothing
    # from the account page's tab vocabulary, and /book cannot render a
    # half-formed action even if it later grows a `last_undo` of its own.
    undo_action = (
        _revert_action(org.ref, last_undo.ref, tab) if last_undo is not None else None
    )

    # Imported inside the function on purpose: routes/changes.py imports this
    # module's shared helpers (_conn, _org, TABS), so a module-level import
    # back the other way would be a cycle. The dependency stays one-way at
    # import time; the message text stays in one file.
    from .changes import toast_for

    return {
        "header": header,
        "tab": tab,
        "tabs": [
            {"id": tab_id, "label": label, "count": counts[tab_id]} for tab_id, label in TABS
        ],
        "snapshot": _snapshot(conn, org, header, open_work, layers),
        "team": team_repo.for_org(conn, org.id),
        "changes": changes,
        "last_undo": last_undo,
        "undo_action": undo_action,
        "toast": toast_for(conn, org, request.query_params),
    }


@router.get("/accounts/{ref}")
def account_root(ref: str) -> RedirectResponse:
    return RedirectResponse(url=f"/accounts/{ref}/{DEFAULT_TAB}", status_code=307)


@router.get("/accounts/{ref}/{tab}", response_class=HTMLResponse)
def account_tab(request: Request, ref: str, tab: str) -> HTMLResponse:
    if tab not in _PANEL_TEMPLATE:
        raise HTTPException(status_code=404, detail=f"no such tab {tab!r}")
    conn = _conn(request)
    org = _org(request, ref)
    return TEMPLATES.TemplateResponse(
        request, _PANEL_TEMPLATE[tab], _context(conn, org, tab, request)
    )
