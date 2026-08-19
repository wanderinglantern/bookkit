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
from ..repo import orgs, placements
from ..repo import projects as projects_repo
from ..repo import rfi as rfi_repo

# What asker_name returns when there is no market name to show. The TUI dims
# these and leaves a real name plain, so the set is part of the contract.
ASKER_PLACEHOLDERS = frozenset({"—", "(merged market)"})


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


@dataclass(frozen=True)
class Removal:
    """What a removal did, so the caller can say it rather than guess."""

    request_id: str
    title: str
    org_id: str
    items: int
    batch: str


def _answered(items: list[RfiItem]) -> list[RfiItem]:
    """The items a client has already acted on. Either half counts: a response
    is what they told us, and `received` is our record that they sent it."""
    return [i for i in items if i.response or i.status == "received" or i.received_on]


def remove_request(
    conn: sqlite3.Connection, request_id: str, *, source: str
) -> Removal:
    """Take an information request off the book, with its items, as ONE
    revertible batch.

    FILED IN ERROR, not withdrawn — the two are different facts and get
    different verbs. A request WITHDRAWN was a real ask we have since dropped;
    it stays in the book with `cancelled_at` set, and the client's copy can
    still explain why it stopped. A request filed IN ERROR was never true, and
    leaving a withdrawn ghost of it says something about the account that did
    not happen. This is the second one. (Grant hit exactly this on 2026-08-19:
    an MCP call filed an RFI he never asked for, and no surface anywhere could
    take it back — `rfi_repo.delete_request` had been sitting there with no
    caller since the feature shipped.)

    ITS ITEMS GO WITH IT. An item is reachable only through its request, so an
    item left behind is unreachable rather than preserved — and the batch puts
    both back together, because a request restored without its items is a
    heading with nothing under it.

    REFUSED once anybody has answered. An answered ask is history: the client
    told us something, and deleting the question deletes their answer with it.
    The refusal names the alternative rather than just saying no.

    `source` is the surface: 'mcp' | 'tui' | 'web'.
    """
    from ..repo import base
    from ..services import batches as batches_svc

    row = base.raw_row(conn, "rfi_request", request_id)
    if row is None:
        raise ValueError(
            f"no information request {request_id!r} — read the account's "
            f"requests for exact ids"
        )
    if row["deleted_at"]:
        raise ValueError(f"{row['ref']} was already removed on {row['deleted_at'][:10]}")

    request = rfi_repo.get_request(conn, request_id)
    items = rfi_repo.items_for_request(conn, request_id)
    answered = _answered(items)
    if answered:
        named = ", ".join(f"{i.prompt[:40]!r}" for i in answered[:3])
        raise ValueError(
            f"{request.ref} has {len(answered)} answered item(s) — {named}. "
            f"Deleting the question deletes the client's answer with it. Set "
            f"cancelled_at to withdraw the request instead, and keep the record."
        )

    with batches_svc.open_batch(
        conn, source=source, tool="request_remove", org_id=request.org_id,
        summary=f"removed information request {request.ref}: {request.title}",
    ) as batch:
        for item in items:
            rfi_repo.delete_item(conn, item.id)
        rfi_repo.delete_request(conn, request_id)

    return Removal(
        request_id=request_id, title=request.title, org_id=request.org_id,
        items=len(items), batch=batch.ref,
    )


@dataclass(frozen=True)
class ItemRemoval:
    item_id: str
    prompt: str
    request_id: str
    batch: str


def remove_item(conn: sqlite3.Connection, item_id: str, *, source: str) -> ItemRemoval:
    """Take ONE ask off a request — a line filed in error, not a line answered.

    The request survives even when this was its last item: a request with no
    items is an ask not yet written down (`is_open` says so below), which is
    not the same as a withdrawn one, and silently withdrawing it here would
    make the two indistinguishable.
    """
    from ..repo import base
    from ..services import batches as batches_svc

    row = base.raw_row(conn, "rfi_item", item_id)
    if row is None:
        raise ValueError(f"no request item {item_id!r} — read the request for exact ids")
    if row["deleted_at"]:
        raise ValueError(f"that item was already removed on {row['deleted_at'][:10]}")

    item = rfi_repo.get_item(conn, item_id)
    if _answered([item]):
        raise ValueError(
            f"{item.prompt[:40]!r} has been answered — deleting it deletes the "
            f"client's answer. Waive it instead if it is no longer needed."
        )
    request = rfi_repo.get_request(conn, item.request_id)

    with batches_svc.open_batch(
        conn, source=source, tool="request_item_remove", org_id=request.org_id,
        summary=f"removed an item from {request.ref}: {item.prompt[:60]}",
    ) as batch:
        rfi_repo.delete_item(conn, item_id)

    return ItemRemoval(
        item_id=item_id, prompt=item.prompt, request_id=item.request_id,
        batch=batch.ref,
    )


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


def scope_label(conn: sqlite3.Connection, request: RfiRequest) -> str:
    """What a request is about, for one column: the placement's ref, the
    project's name, or an em dash for an account-level ask (onboarding).

    One helper, two screens — the resolution rule is not duplicated."""
    if request.placement_id:
        try:
            return placements.get(conn, request.placement_id).ref
        except KeyError:
            return "(deleted placement)"
    if request.project_id:
        try:
            return projects_repo.get_project(conn, request.project_id).name
        except KeyError:
            return "(deleted project)"
    return "—"


def asker_name(conn: sqlite3.Connection, request: RfiRequest) -> str:
    """Who to chase for a response: the market's name, an em dash for an
    internal ask (onboarding) that names no market, or "(merged market)" when
    the market was merged away underneath the request.

    One helper, three surfaces (the chase queue, the account tab, the client
    sheet) — the resolution rule is not duplicated. Callers that style their
    output dim the ASKER_PLACEHOLDERS and leave a real name plain."""
    if not request.market_org_id:
        return "—"
    try:
        return orgs.get(conn, request.market_org_id).name
    except KeyError:
        return "(merged market)"


def effective_due(item: RfiItem, request: RfiRequest) -> str | None:
    """When an item is actually needed: its own due date, falling back to its
    request's. One rule, used by the queue, the tab, and the sheet.

    None means neither side set one — an undated ask, which still has to
    appear everywhere (no date window drops it)."""
    return item.due_on or request.due_on


def outstanding_requests(
    conn: sqlite3.Connection, today: date, days: int = 120
) -> list[RfiChase]:
    horizon = (today + timedelta(days=days)).isoformat()
    out: list[RfiChase] = []
    for row in rfi_repo.outstanding_rows(conn, horizon):
        earliest = row["earliest_due"]
        fields = {k: row[k] for k in row.keys() if k in RfiRequest.model_fields}
        out.append(
            RfiChase(
                request=RfiRequest.model_validate(fields),
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
    """d on an item: received, dated.

    This is TWO field writes (status and received_on), so base.update logs
    two events and a single `u` reverts only the later one — leaving the item
    received with received_on NULL. That matches tasks_repo.complete, so it is
    parity rather than a regression, but nothing here should claim otherwise."""
    return rfi_repo.update_item(conn, item_id, status="received", received_on=on)
