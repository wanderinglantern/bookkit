"""Account page routes. No SQL here — reads go through repo/ and services/.

Four tabs (Program, Relationship, Work, Pipeline) replace the old three-tab
Overview/Contacts/Interactions shell — see
docs/superpowers/specs/2026-08-17-web-visual-direction.md. Every tab this
task ships is an empty panel; Tasks 8-13 fill them in. The renewal invariant
survives the rail's removal: the header badge and the right rail's snapshot
row both print `RenewalItem.renewal_on` and `RenewalItem.days_remaining`
from the SAME object, never `placement.period_to`."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import money
from ...models import EventBatch, Org
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
from ...services import book as book_service
from ...services import renewals
from ...services import rfi as rfi_service
from ..app import TEMPLATES

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

_PANEL_TEMPLATE = {
    "program": "account/program.html",
    "relationship": "account/relationship.html",
    "work": "account/work.html",
    "pipeline": "account/pipeline.html",
}


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn  # type: ignore[no-any-return]


def _org(request: Request, ref: str) -> Org:
    org = orgs_repo.find(_conn(request), ref)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no account matches {ref!r}")
    return org


def _header(conn: sqlite3.Connection, org: Org) -> dict[str, Any]:
    """THE RENEWAL DATE IS RenewalItem.renewal_on, never placement.period_to.
    Printing period_to beside a renewal_on countdown is the bug that made a
    future date render as '70d over' on four surfaces. Both the header's
    overdue badge and the right rail's snapshot row read renewal_on and
    days_remaining off this SAME dict, built from one RenewalItem."""
    item = renewals.next_for_org(conn, org.id)
    if item is None:
        return {"org": org, "renewal_on": None, "days_remaining": None, "overdue": False}
    return {
        "org": org,
        "renewal_on": item.renewal_on,
        "days_remaining": item.days_remaining,
        "overdue": item.days_remaining < 0,
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


def _snapshot(
    conn: sqlite3.Connection, org: Org, header: dict[str, Any], open_work: int
) -> list[dict[str, Any]]:
    """Only rows with a real read behind them. `program premium`,
    `top of tower` and `unplaced` are omitted entirely — this task has no
    towerkit tower read to source them from ("omit a row rather than invent
    a number")."""
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
    return {
        "time": _change_time(batch.created_at, today),
        "what": batch.summary,
        "who": batch.source,
        "reverted": batch.reverted_at is not None,
    }


def _context(conn: sqlite3.Connection, org: Org, tab: str) -> dict[str, Any]:
    header = _header(conn, org)
    open_work = _open_work_count(conn, org.id)
    counts = _counts(conn, org, open_work)

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

    return {
        "header": header,
        "tab": tab,
        "tabs": [
            {"id": tab_id, "label": label, "count": counts[tab_id]} for tab_id, label in TABS
        ],
        "snapshot": _snapshot(conn, org, header, open_work),
        "team": team_repo.for_org(conn, org.id),
        "changes": changes,
        "last_undo": last_undo,
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
        request, _PANEL_TEMPLATE[tab], _context(conn, org, tab)
    )
