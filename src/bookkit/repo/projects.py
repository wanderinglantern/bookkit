"""Client projects and their insurance needs — needed-by dates are attention
(like renewals): an unmet need never falls off the radar, however old."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from ..ids import PROJECT_REF, next_ref
from ..models import Project, ProjectNeed
from . import base

ATTENTION_STATUSES = ("identified", "quoted")  # placed / not_needed are done


def create_project(
    conn: sqlite3.Connection, org_id: str, name: str, **fields: Any
) -> Project:
    fields.setdefault("ref", next_ref(conn, PROJECT_REF))
    project_id = base.insert(
        conn, "project", {"org_id": org_id, "name": name, **fields}
    )
    return get_project(conn, project_id)


def find_project(conn: sqlite3.Connection, ref_or_id: str) -> Project | None:
    """By ref (PRJ-0001, case-insensitive) or id — opportunities.find's twin,
    so MCP can resolve the refs its reads hand out."""
    row = conn.execute(
        f"SELECT * FROM project WHERE (id = ? OR ref = ? COLLATE NOCASE)"
        f" AND {base.alive()}",
        (ref_or_id, ref_or_id),
    ).fetchone()
    return Project.from_row(row) if row else None


def get_project(conn: sqlite3.Connection, project_id: str) -> Project:
    row = base.get(conn, "project", project_id)
    if row is None:
        raise KeyError(f"project {project_id} not found")
    return Project.from_row(row)


def projects_for_org(conn: sqlite3.Connection, org_id: str) -> list[Project]:
    rows = conn.execute(
        f"""SELECT * FROM project WHERE org_id = ? AND {base.alive()}
            ORDER BY status = 'active' DESC, start_on IS NULL, start_on""",
        (org_id,),
    ).fetchall()
    return [Project.from_row(r) for r in rows]


def update_project(
    conn: sqlite3.Connection, project_id: str, note: str | None = None, **changes: Any
) -> Project:
    base.update(conn, "project", project_id, changes, note)
    return get_project(conn, project_id)


def delete_project(conn: sqlite3.Connection, project_id: str) -> None:
    base.soft_delete(conn, "project", project_id)


# --- needs ---------------------------------------------------------------------


def add_need(
    conn: sqlite3.Connection, project_id: str, line: str, needed_by: str, **fields: Any
) -> ProjectNeed:
    need_id = base.insert(
        conn,
        "project_need",
        {"project_id": project_id, "line": line, "needed_by": needed_by, **fields},
    )
    return get_need(conn, need_id)


def get_need(conn: sqlite3.Connection, need_id: str) -> ProjectNeed:
    row = base.get(conn, "project_need", need_id)
    if row is None:
        raise KeyError(f"project need {need_id} not found")
    return ProjectNeed.from_row(row)


def needs_for_project(conn: sqlite3.Connection, project_id: str) -> list[ProjectNeed]:
    rows = conn.execute(
        f"""SELECT * FROM project_need WHERE project_id = ? AND {base.alive()}
            ORDER BY needed_by""",
        (project_id,),
    ).fetchall()
    return [ProjectNeed.from_row(r) for r in rows]


def update_need(
    conn: sqlite3.Connection, need_id: str, note: str | None = None, **changes: Any
) -> ProjectNeed:
    base.update(conn, "project_need", need_id, changes, note)
    return get_need(conn, need_id)


def delete_need(conn: sqlite3.Connection, need_id: str) -> None:
    base.soft_delete(conn, "project_need", need_id)


def needs_due(
    conn: sqlite3.Connection, today: date, days: int = 90
) -> list[sqlite3.Row]:
    """Open needs whose needed-by falls inside the window — or is already
    past. Joined with project and org for display."""
    horizon = (today + timedelta(days=days)).isoformat()
    marks = ", ".join("?" for _ in ATTENTION_STATUSES)
    return conn.execute(
        f"""
        SELECT pn.*, pr.name AS project_name, pr.org_id AS org_id,
               o.name AS org_name
        FROM project_need pn
        JOIN project pr ON pr.id = pn.project_id
        JOIN org o ON o.id = pr.org_id
        WHERE pn.needed_by <= ? AND pn.status IN ({marks})
          AND {base.alive('pn')} AND {base.alive('pr')} AND {base.alive('o')}
        ORDER BY pn.needed_by
        """,
        (horizon, *ATTENTION_STATUSES),
    ).fetchall()
