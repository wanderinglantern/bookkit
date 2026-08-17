"""Account page routes. No SQL here — reads go through repo/ and services/."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...models import Org
from ...repo import contacts as contacts_repo
from ...repo import interactions as interactions_repo
from ...repo import opportunities as opportunities_repo
from ...repo import orgs as orgs_repo
from ...repo import tasks as tasks_repo
from ...repo import team as team_repo
from ...services import renewals
from ..app import TEMPLATES

router = APIRouter()


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn  # type: ignore[no-any-return]


def _org(request: Request, ref: str) -> Org:
    org = orgs_repo.find(_conn(request), ref)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no account matches {ref!r}")
    return org


def _header(conn: sqlite3.Connection, org: Org) -> dict[str, Any]:
    """The renewal shown is RenewalItem.renewal_on — the date days_remaining
    counts to. Printing placement.period_to beside that countdown is the bug
    that made a future date render as '70d over' on four surfaces."""
    item = renewals.next_for_org(conn, org.id)
    if item is None:
        # No rail at all when there is no live renewal — an empty rail would
        # imply a clock that is not running.
        return {"org": org, "renewal_on": None, "days_remaining": None,
                "overdue": False, "lines": "", "bucket": None, "rail_pct": None}
    overdue = item.days_remaining < 0
    return {
        "org": org,
        "renewal_on": item.renewal_on,
        "days_remaining": item.days_remaining,
        "overdue": overdue,
        "lines": item.lines,
        "bucket": item.bucket,
        # Position along the 120-day rail. Overdue pins to the left overrun and
        # is never expressed as a position — overdue is decided by
        # days_remaining < 0, never by where a marker lands.
        "rail_pct": None if overdue else min(100.0, max(0.0, item.days_remaining / 120 * 100)),
    }


@router.get("/accounts/{ref}")
def account_root(ref: str) -> RedirectResponse:
    return RedirectResponse(url=f"/accounts/{ref}/overview", status_code=307)


@router.get("/accounts/{ref}/overview", response_class=HTMLResponse)
def overview(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    return TEMPLATES.TemplateResponse(
        request,
        "account/overview.html",
        {
            "header": _header(conn, org),
            "tab": "overview",
            "contacts": contacts_repo.for_org(conn, org.id)[:8],
            "interactions": interactions_repo.for_org(conn, org.id, limit=8),
            "tasks": tasks_repo.open_tasks_for_client(conn, org.id),
            "opportunities": opportunities_repo.for_org(conn, org.id, open_only=True),
            "team": team_repo.for_org(conn, org.id),
        },
    )
