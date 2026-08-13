"""bookctl mcp — stdio MCP server for the work-machine cowork assistant.

Read tools run on a read-only connection (mode=ro — enforced by the
database). Exactly five write tools exist, all additive, all inside
db.transaction, all event-logged with source=mcp. stdout is protocol;
anything human goes to stderr (never print here).

NOTE ON SDK NAMING: the brief this module was built from named the
FastMCP-era API (`mcp.server.fastmcp.FastMCP`). The installed SDK
(mcp==1.28.1) has renamed that class to `MCPServer`, moved to
`mcp.server.mcpserver.MCPServer` (also re-exported as `mcp.server.MCPServer`).
The constructor kwargs used here (`name`, `instructions`), the `.tool()`
decorator, `._tool_manager.list_tools()`, and `.run()` (stdio by default)
are unchanged in shape — only the class name and import path moved, so this
module follows the installed SDK rather than the brief's example.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import db
from .services.renewals import RenewalItem


def build_server(db_path: Path | str | None = None) -> MCPServer:
    server = MCPServer(
        "bookkit",
        instructions=(
            "Grant's book of business. Money values are formatted dollars; "
            "dates are ISO. Use search/open_items to find refs before "
            "completing or enriching anything — never guess an id."
        ),
    )
    ro = db.connect_readonly(db_path)
    rw = db.connect(db_path)
    _register_read_tools(server, ro)
    _register_write_tools(server, rw)
    return server


def _register_read_tools(server: MCPServer, ro: sqlite3.Connection) -> None:
    @server.tool()
    def today_brief() -> dict[str, Any]:
        """Today's working brief: due tasks, renewals in the 120-day window,
        project needs, stale accounts, submissions past SLA."""
        return _today_brief(ro)


def _register_write_tools(server: MCPServer, rw: sqlite3.Connection) -> None:
    pass  # Task 4


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


def serve(db_path: Path | str | None = None) -> None:
    build_server(db_path).run()  # stdio transport is the SDK's default
