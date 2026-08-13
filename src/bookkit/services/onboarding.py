"""Client onboarding: the flow is data, and the data is the state.

Steps are declared once here; the TUI wizard renders them and a future MCP
front-end will walk the same list. There is no wizard-state table —
completeness() derives per-step status from what's actually in the book, so
resuming (or filling a gap out-of-band) needs no bookkeeping.

Attention scope: a client nags in incomplete_clients() only while it's
plausibly still being onboarded — status 'prospect', or created within the
last 90 days (decided by Grant 2026-08-13). Without that fence every legacy
client missing an owner would flood attention forever. A client that flips
to active and is never finished onboarding falls out of attention after day
90 — deliberate: 'prospect' status is the lever that never falls off, so
leaving prospect is what has to happen before the fence applies."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..models import Org
from ..repo import contacts, orgs, placements
from ..repo import projects as projects_repo
from ..repo import tasks as tasks_repo

COMPLETE = "complete"
PARTIAL = "partial"
UNTOUCHED = "untouched"

ONBOARDING_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    required: bool  # required steps gate is_complete(); optional ones inform


STEPS: tuple[Step, ...] = (
    Step("org", "account basics", True),
    Step("contacts", "contacts", True),
    Step("program", "program & lines", True),
    Step("projects", "projects & needs", False),
    Step("followups", "follow-ups", False),
)


@dataclass(frozen=True)
class StepStatus:
    step: Step
    state: str
    summary: str  # one line for the wizard pane


def _org_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    missing = [f for f in ("owner", "industry") if not getattr(org, f)]
    if not missing:
        return COMPLETE, f"owner {org.owner} · {org.industry}"
    return PARTIAL, "missing " + " and ".join(missing)


def _contacts_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    people = contacts.for_org(conn, org.id)
    if not people:
        return UNTOUCHED, "no contacts yet"
    reachable = [c for c in people if c.email or c.phone or c.mobile]
    if reachable:
        primary = "★ primary set" if any(c.is_primary for c in people) else "no primary"
        return COMPLETE, f"{len(people)} contact(s) · {primary}"
    return PARTIAL, f"{len(people)} contact(s), none with email or phone"


def _program_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    pls = placements.for_org(conn, org.id)
    if not pls:
        return UNTOUCHED, "no program yet"
    return COMPLETE, " · ".join(p.program_name for p in pls[:3])


def _projects_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    projs = projects_repo.projects_for_org(conn, org.id)
    if not projs:
        return UNTOUCHED, "none recorded (optional)"
    bare = [p for p in projs if not projects_repo.needs_for_project(conn, p.id)]
    if bare:
        return PARTIAL, f"{len(bare)} project(s) with no needs listed"
    return COMPLETE, f"{len(projs)} project(s), needs listed"


def _followups_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    # open_tasks_for_client (not open_tasks) — a placement-attached task can
    # carry org_id NULL, so org_id=-filtering alone would drop it. See that
    # function's docstring in repo/tasks.py.
    open_tasks = tasks_repo.open_tasks_for_client(conn, org.id)
    if open_tasks:
        return COMPLETE, f"{len(open_tasks)} open task(s)"
    return UNTOUCHED, "no follow-up task (optional)"


_STATE_FNS = {
    "org": _org_state,
    "contacts": _contacts_state,
    "program": _program_state,
    "projects": _projects_state,
    "followups": _followups_state,
}


def completeness(conn: sqlite3.Connection, org_id: str) -> list[StepStatus]:
    org = orgs.get(conn, org_id)
    out: list[StepStatus] = []
    for step in STEPS:
        state, summary = _STATE_FNS[step.key](conn, org)
        out.append(StepStatus(step, state, summary))
    return out


def first_incomplete(conn: sqlite3.Connection, org_id: str) -> str | None:
    statuses = completeness(conn, org_id)
    for required in (True, False):
        for status in statuses:
            if status.step.required is required and status.state != COMPLETE:
                return status.step.key
    return None


def is_complete(conn: sqlite3.Connection, org_id: str) -> bool:
    return all(
        s.state == COMPLETE for s in completeness(conn, org_id) if s.step.required
    )


def incomplete_clients(
    conn: sqlite3.Connection, today: date
) -> list[tuple[Org, str]]:
    """Clients still inside the onboarding window with required steps open,
    oldest first — the attention feed."""
    floor = (today - timedelta(days=ONBOARDING_WINDOW_DAYS)).isoformat()
    out: list[tuple[Org, str]] = []
    for org in orgs.list_orgs(conn, kind="client"):
        if org.status != "prospect" and org.created_at[:10] < floor:
            continue
        missing = [
            s.step.label
            for s in completeness(conn, org.id)
            if s.step.required and s.state != COMPLETE
        ]
        if missing:
            out.append((org, ", ".join(missing)))
    return sorted(out, key=lambda pair: pair[0].created_at)
