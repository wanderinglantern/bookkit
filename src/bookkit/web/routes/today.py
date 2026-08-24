"""Today — the way into the day's work (audit gap #1).

The TUI's Today panes and the Navigator's ATTENTION leaves fold into ONE web
page: they are the same eight questions ("what needs me?") asked from two
screens, and a browser tab does not need the tree/pane split a terminal
does. Overdue renewals NEVER fall off (the 120-day window rule); every row
links to the account surface that acts on it; a task can be taken off the
list right here — done, or dropped — because the day's list owes you both
ways out of it, and they are different facts (see routes/work.drop_task).

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

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...models import Org
from ...money import format_cents_compact
from ...repo import batches as batches_repo
from ...repo import orgs as orgs_repo
from ...repo import projects as projects_repo
from ...repo import tasks as tasks_repo
from ...services import onboarding as onboarding_svc
from ...services import quotes as quotes_svc
from ...services import renewals, sla, staleness
from ...services import rfi as rfi_svc
from ..app import TEMPLATES
from . import work
from .account import _conn

router = APIRouter()


def _needs_you(
    accounts: dict[str, Any],
    overdue: list[Any],
    due_tasks: list[Any],
    late: list[Any],
    today: date,
) -> list[dict[str, Any]]:
    """ONE morning list, worst first (design 4C): overdue renewals, overdue
    items and anything past SLA stop being three sections of equal weight
    and become the screen's spine. Every row states what, whose, how late,
    and where acting on it lives — and a task keeps its done/drop right
    here, because the day's list owes you both ways off it."""
    iso = today.isoformat()
    rows: list[dict[str, Any]] = []
    for item in overdue:
        label = accounts.get(item.org.id)
        rows.append({
            "kind": "renewal",
            # COVER is what runs out; when the placement is unlinked the
            # clause is dropped — a sentence, unlike the Cover column, may
            # simply not claim what it does not know (the program name never
            # stands in, the standing renewal-row rule)
            "what": (
                f"Renewal ran out — {item.cover}" if item.cover
                else "Renewal ran out"
            ),
            # PRINT THE DATE YOU COUNT TO (the standing rule): a countdown
            # without renewal_on beside it is the bug four reviewers found
            # independently in 2026-08-15's costume
            "due": item.renewal_on,
            "org_id": item.org.id,
            "state_class": "state-overdue",
            "state": f"overdue · {-item.days_remaining}d",
            "where": "Program",
            "link": f"/accounts/{label.ref}/program" if label else None,
            "task_id": None,
            "sort": (0, item.days_remaining),
        })
    for task in due_tasks:
        overdue_task = task.due_on is not None and task.due_on < iso
        days_over = 0
        if overdue_task and task.due_on:
            days_over = (today - date.fromisoformat(task.due_on)).days
        rows.append({
            "kind": "task",
            "what": task.title,
            "due": task.due_on or "—",
            "org_id": task.org_id,
            "state_class": "state-overdue" if overdue_task else "state-soon",
            "state": f"overdue · {days_over}d" if overdue_task else "due today",
            "where": "Open items",
            "link": "/items",
            "task_id": task.id,
            "sort": (0 if overdue_task else 1, -days_over),
        })
    for sub in late:
        rows.append({
            "kind": "sla",
            "what": f"{sub.market.name} — no response, {sub.days_out}d out",
            "due": sub.submission.sent_on,
            "org_id": sub.account.id,
            "state_class": "state-soon",
            "state": "past SLA",
            "where": "Pipeline",
            "link": None,
            "task_id": None,
            "sort": (1, -sub.days_out),
        })
    rows.sort(key=lambda row: row["sort"])
    return rows


def _sections(conn: sqlite3.Connection, today: date) -> dict[str, Any]:
    items = renewals.upcoming(conn, today, days=120)
    overdue = [i for i in items if i.days_remaining < 0]
    soon = [i for i in items if i.days_remaining >= 0]
    due_tasks = tasks_repo.open_tasks(conn, due_by=today.isoformat())
    all_open = tasks_repo.open_tasks(conn)
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
    open_overdue = sum(
        1 for t in all_open
        if t.due_on is not None and t.due_on < today.isoformat()
    )
    needs_you = _needs_you(accounts, overdue, due_tasks, late, today)

    return {
        "today": today.isoformat(),
        # the band's name for the day — a date a person says, not an ISO stamp
        "today_said": f"{today:%A}, {today.day} {today:%B}",
        "standing": (
            f"{len(overdue)} overdue · {len(all_open)} open items · "
            f"{len(chases)} requests to chase"
        ),
        "accounts": accounts,
        "needs_you": needs_you,
        "overdue": overdue,
        "soon": soon,
        "tasks": due_tasks,
        "open_count": len(all_open),
        "open_overdue": open_overdue,
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


def _tasks_section(request: Request, conn: sqlite3.Connection) -> HTMLResponse:
    """Taking a task off the list changes the Needs-you list and nothing else
    on the page, so the answer is that SECTION — one element, per the htmx
    parse-context rule."""
    return TEMPLATES.TemplateResponse(
        request, "partials/_today_needs.html", _sections(conn, date.today())
    )


@router.post("/today/tasks/{task_id}/done", response_class=HTMLResponse)
def today_task_done(request: Request, task_id: str) -> HTMLResponse:
    """Done, from the list that surfaced it — the one write Today owes you.

    The write is work.complete_task, the same call the account tab and the
    book-wide list make. It used to be a fourth hand-rolled copy of the batch
    and its sentence here; a summary reworded in one of four places is exactly
    the copy that quietly differs.
    """
    conn = _conn(request)
    task = work.task_or_404(conn, task_id)
    org: Org | None = orgs_repo.get(conn, task.org_id) if task.org_id else None
    work.complete_task(conn, org, task_id)
    return _tasks_section(request, conn)


@router.post("/today/tasks/{task_id}/drop", response_class=HTMLResponse)
def today_task_drop(request: Request, task_id: str) -> HTMLResponse:
    """Drop — not happening, as against done. Today lists what is DUE, which
    is where a task overtaken by events is most likely to be noticed, so the
    two ways off the list belong on the same row here as everywhere else.
    The write is work.drop_task, shared with both other surfaces."""
    conn = _conn(request)
    task = work.task_or_404(conn, task_id)
    org: Org | None = orgs_repo.get(conn, task.org_id) if task.org_id else None
    work.drop_task(conn, org, task_id)
    return _tasks_section(request, conn)
