"""The renewal calendar — months across, accounts down, a chip at each
expiry, coloured by status (audit gap #1's second half). The one page that
makes the year legible at a glance; the TUI's grid, translated.

Overdue gets its own LEADING column, exactly like the terminal: an overdue
renewal never falls off and never hides inside a month cell where it would
read as merely upcoming. Anything overdue is decided by days_remaining < 0,
never by where a cell lands in the grid (the four-surface renewal-date bug,
CLAUDE.md)."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...repo import orgs as orgs_repo
from ...services import renewals
from ..app import TEMPLATES
from .account import _conn

router = APIRouter()


def _add_months(d: date, months: int) -> date:
    month0 = d.month - 1 + months
    return date(d.year + month0 // 12, month0 % 12 + 1, 1)


def _grid(conn: sqlite3.Connection, today: date) -> dict[str, Any]:
    months = [_add_months(today.replace(day=1), i) for i in range(12)]
    items = renewals.upcoming(conn, today, days=365)

    rows: dict[str, dict[str, Any]] = {}
    for item in items:
        row = rows.setdefault(
            item.org.id,
            {"org": item.org, "overdue": [], "cells": {m.isoformat(): [] for m in months}},
        )
        if item.days_remaining < 0:
            row["overdue"].append(item)
            continue
        renews = date.fromisoformat(item.renewal_on)
        key = renews.replace(day=1).isoformat()
        if key in row["cells"]:
            row["cells"][key].append(item)

    refs: dict[str, str] = {}
    for org_id in rows:
        try:
            refs[org_id] = str(orgs_repo.get(conn, org_id).ref)
        except KeyError:
            continue

    ordered = sorted(rows.values(), key=lambda r: str(r["org"].name))
    return {
        "months": [{"key": m.isoformat(), "label": m.strftime("%b %y")} for m in months],
        "rows": ordered,
        "refs": refs,
        "today": today.isoformat(),
    }


@router.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request) -> HTMLResponse:
    conn = _conn(request)
    return TEMPLATES.TemplateResponse(
        request, "calendar.html", _grid(conn, date.today())
    )
