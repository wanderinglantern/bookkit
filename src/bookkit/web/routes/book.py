"""GET / and GET /book — the way in.

Verified 2026-08-17: GET / returned 404 and so did /accounts. Every account
tab worked but nothing linked to any of them — the only way to reach the
app was already knowing a ref and typing /accounts/ACC-0001/relationship by
hand. GET / redirects to /book, and /book is a full-bleed accounts table
whose rows link to the account they name. This is the whole task.

No SQL here: reads go through repo/orgs, repo/interactions and
services/book, services/renewals — the SAME renewals.next_for_org and
services.book.bound_premium_for_org every other renewal-date and
money-column surface uses. In particular: orgs.clients_with_recency's
`premium` field is NEVER read here. That query returns the single bound
placement with the latest period_to — right for the staleness service it
was written for, wrong here. CLAUDE.md records what this exact column
showed once it was wired to that field instead: "an account with $15.6M
across two bound placements read $8M" — revenue that did not exist.
bound_premium_for_org is the summed figure, already used by four other
call sites (tui/screens/{book,navigator,account}.py and the web account
page's snapshot row)."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import money
from ...models import Org
from ...repo import interactions as interactions_repo
from ...repo import orgs as orgs_repo
from ...services import book as book_service
from ...services import renewals
from ..app import TEMPLATES

router = APIRouter()


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn  # type: ignore[no-any-return]


def _status_class(status: str) -> str:
    """Colour is signal, never decoration (visual-direction spec) — every
    coloured state here still prints the status word itself, this only
    supplies the ink. Every row on /book is currently status='active'
    (that's the filter), but the mapping is written generally rather than
    special-cased to the one value that can appear today."""
    if status == "active":
        return "is-good"
    if status in ("lost", "declined", "lapsed"):
        return "is-danger"
    if status == "dormant":
        return "muted"
    return ""


def _renewal_class(days: int | None) -> str:
    """THE RENEWAL DATE IS RenewalItem.renewal_on; overdue is decided by
    days_remaining < 0, never grid position — same rule, fifth surface."""
    if days is None:
        return "muted"
    if days < 0:
        return "is-danger"
    if days <= 60:
        return "is-warn"
    return ""


def _due_text(days: int | None) -> str:
    if days is None:
        return "—"
    if days < 0:
        return f"◆ {-days}d over"
    return f"{days}d"


def _due_class(days: int | None) -> str:
    if days is None:
        return "muted"
    if days < 0:
        return "is-danger"
    if days <= 60:
        return "is-warn"
    return "muted"


def _row(conn: sqlite3.Connection, org: Org) -> dict[str, Any]:
    item = renewals.next_for_org(conn, org.id)
    last = interactions_repo.last_for_org(conn, org.id)
    days = item.days_remaining if item is not None else None
    return {
        "ref": org.ref,
        "name": org.name,
        "owner": org.owner or "—",
        "status": org.status,
        "status_class": _status_class(org.status),
        "renewal_text": item.renewal_on if item is not None else "—",
        "renewal_class": _renewal_class(days),
        "due_text": _due_text(days),
        "due_class": _due_class(days),
        "premium": money.format_cents_compact(book_service.bound_premium_for_org(conn, org.id)),
        "last_touch": last.occurred_on if last is not None else "—",
    }


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/book", status_code=307)


@router.get("/book", response_class=HTMLResponse)
def book(request: Request) -> HTMLResponse:
    conn = _conn(request)
    clients = orgs_repo.list_orgs(conn, kind="client", status="active")
    rows = [_row(conn, org) for org in clients]
    total_cents = sum(book_service.bound_premium_for_org(conn, o.id) for o in clients)
    context = {
        "rows": rows,
        "count": len(rows),
        "total_premium": money.format_cents_compact(total_cents),
    }
    return TEMPLATES.TemplateResponse(request, "book.html", context)
