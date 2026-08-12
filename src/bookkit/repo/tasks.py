"""Tasks — the to-do list, attachable to an org or a placement."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import utc_now
from ..models import Task
from . import base


def create(conn: sqlite3.Connection, title: str, **fields: Any) -> Task:
    task_id = base.insert(conn, "task", {"title": title, **fields})
    return get(conn, task_id)


def get(conn: sqlite3.Connection, task_id: str) -> Task:
    row = base.get(conn, "task", task_id)
    if row is None:
        raise KeyError(f"task {task_id} not found")
    return Task.from_row(row)


def open_tasks(
    conn: sqlite3.Connection, org_id: str | None = None, due_by: str | None = None
) -> list[Task]:
    where = [base.alive(), "status = 'open'"]
    params: list[Any] = []
    if org_id is not None:
        where.append("org_id = ?")
        params.append(org_id)
    if due_by is not None:
        where.append("due_on IS NOT NULL AND due_on <= ?")
        params.append(due_by)
    rows = conn.execute(
        f"SELECT * FROM task WHERE {' AND '.join(where)}"
        " ORDER BY due_on IS NULL, due_on, priority",
        params,
    ).fetchall()
    return [Task.from_row(r) for r in rows]


def open_tasks_for_client(conn: sqlite3.Connection, org_id: str) -> list[Task]:
    """Open tasks owned by ONE client, directly or via one of its placements —
    mirrors the ownership join in submissions.outstanding_for_org. Needed
    because a placement-attached task can carry org_id NULL (legal; see
    module docstring), so `open_tasks(org_id=...)` alone would drop it."""
    rows = conn.execute(
        f"""
        SELECT task.* FROM task
        LEFT JOIN placement p ON p.id = task.placement_id
        WHERE {base.alive('task')} AND task.status = 'open'
          AND (task.org_id = ? OR p.org_id = ?)
        ORDER BY task.due_on IS NULL, task.due_on, task.priority
        """,
        (org_id, org_id),
    ).fetchall()
    return [Task.from_row(r) for r in rows]


def reassign_placement(conn: sqlite3.Connection, from_id: str, to_id: str) -> int:
    """Bulk move for placement merges; the service logs the event."""
    cur = conn.execute(
        "UPDATE task SET placement_id = ? WHERE placement_id = ?", (to_id, from_id)
    )
    return cur.rowcount


def complete(conn: sqlite3.Connection, task_id: str) -> Task:
    base.update(conn, "task", task_id, {"status": "done", "completed_at": utc_now()})
    return get(conn, task_id)


def drop(conn: sqlite3.Connection, task_id: str) -> Task:
    base.update(conn, "task", task_id, {"status": "dropped"})
    return get(conn, task_id)


def reopen(conn: sqlite3.Connection, task_id: str) -> Task:
    base.update(conn, "task", task_id, {"status": "open", "completed_at": None})
    return get(conn, task_id)


def update(conn: sqlite3.Connection, task_id: str, note: str | None = None, **changes: Any) -> Task:
    base.update(conn, "task", task_id, changes, note)
    return get(conn, task_id)


def delete(conn: sqlite3.Connection, task_id: str) -> None:
    base.soft_delete(conn, "task", task_id)
