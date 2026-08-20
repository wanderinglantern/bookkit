"""Today — the way into the day's work (audit gap #1).

The TUI's Today panes and the Navigator's ATTENTION leaves fold into ONE web
page: they are the same eight questions ("what needs me?") asked from two
screens, and a browser tab does not need the tree/pane split a terminal
does. Overdue renewals NEVER fall off (the 120-day window rule); every row
links to the account surface that acts on it; tasks can be completed right
here because "done" is the one action the day's list itself owes you.

The Navigator's cross-account CHANGES list rides along as the last section
— its per-row Revert reuses the account route (204 + HX-Redirect), so a
revert lands you on the account with the outcome toast, which is where the
consequences are visible. The batch before→after diff stays TUI-only for
now (parity.SCREENS names it).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...money import format_cents_compact
from ...repo import batches as batches_repo
from ...repo import orgs as orgs_repo
from ...repo import projects as projects_repo
from ...repo import tasks as tasks_repo
from ...services import batches as batches_svc
from ...services import onboarding as onboarding_svc
from ...services import quotes as quotes_svc
from ...services import renewals, sla, staleness
from ...services import rfi as rfi_svc
from ..app import TEMPLATES
from .account import _conn

router = APIRouter()


def _sections(conn: sqlite3.Connection, today: date) -> dict[str, Any]:
    items = renewals.upcoming(conn, today, days=120)
    overdue = [i for i in items if i.days_remaining < 0]
    soon = [i for i in items if i.days_remaining >= 0]
    due_tasks = tasks_repo.open_tasks(conn, due_by=today.isoformat())
    needs = projects_repo.needs_due(conn, today, days=120)
    late = sla.past_sla(conn, today)
    expiring = quotes_svc.expiring(conn, today, days=120)
    undated = quotes_svc.undated(conn, today)
    pending_onboarding = onboarding_svc.incomplete_clients(conn, today)
    chases = rfi_svc.outstanding_requests(conn, today, days=120)
    stale = staleness.stale_accounts(conn, today)
    since = (today - timedelta(days=14)).isoformat()
    changes = [b for b in batches_repo.recent(conn, since, limit=20)]

    org_ids = (
        {i.org.id for i in items}
        | {t.org_id for t in due_tasks if t.org_id}
        | {row["org_id"] for row in needs}
        | {s.account.id for s in late}
        | {q.org_id for q in expiring + undated}
        | {org.id for org, _ in pending_onboarding}
        | {a.org.id for a in stale}
        | {b.org_id for b in changes if b.org_id}
    )
    # ONE lookup, carrying the ref and the name TOGETHER. Two lookups is how
    # the tasks table came to print `ACC-0004` where every other section on
    # this page printed the account's name: it held only the ref map.
    accounts = orgs_repo.labels_for(conn, org_ids)

    return {
        "today": today.isoformat(),
        "accounts": accounts,
        "overdue": overdue,
        "soon": soon,
        "tasks": due_tasks,
        "needs": [dict(row) for row in needs],
        "late": late,
        "quotes": expiring,
        "undated_quotes": undated,
        "onboarding": pending_onboarding,
        "chases": chases,
        "stale": stale[:15],
        "changes": changes,
        "money": format_cents_compact,
    }


@router.get("/today", response_class=HTMLResponse)
def today_page(request: Request) -> HTMLResponse:
    conn = _conn(request)
    return TEMPLATES.TemplateResponse(
        request, "today.html", _sections(conn, date.today())
    )


@router.post("/today/tasks/{task_id}/done", response_class=HTMLResponse)
def today_task_done(request: Request, task_id: str) -> HTMLResponse:
    """Done, from the list that surfaced it — the one write Today owes you.
    Same repo call and batch shape as the account route; the answer is the
    refreshed tasks SECTION, because completing a task changes this list and
    nothing else on the page."""
    conn = _conn(request)
    try:
        task = tasks_repo.get(conn, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such task") from None
    with batches_svc.open_batch(
        conn, source="web", tool="task_done",
        summary=f"completed {task.title}", org_id=task.org_id,
    ):
        tasks_repo.complete(conn, task_id)
    sections = _sections(conn, date.today())
    return TEMPLATES.TemplateResponse(request, "partials/_today_tasks.html", sections)
