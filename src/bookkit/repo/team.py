"""Internal team members and their account/placement assignments."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import TeamAssignment, TeamMember
from . import base


def _guard_name(
    conn: sqlite3.Connection, name: str, member_id: str | None = None
) -> None:
    """Two colleagues sharing a name makes every lookup ambiguous — the MCP
    server's _find_member and _edit_target both take the FIRST match, so a
    later write lands on the wrong row.

    The guard used to live only in mcpserver, which meant the TUI wrote
    straight past it: renaming Dana to "Leo Novak" in the team screen saved
    silently and every subsequent team_assign / edit_field / member_deactivate
    naming Leo Novak hit Dana's row. It belongs here, where both surfaces
    inherit it."""
    for other in list_members(conn, active_only=False):
        if other.id != member_id and other.name.lower() == name.lower():
            raise ValueError(
                f"team member {other.name} already holds that name — rename "
                f"or deactivate them first"
            )


def create_member(conn: sqlite3.Connection, name: str, **fields: Any) -> TeamMember:
    _guard_name(conn, name)
    member_id = base.insert(conn, "team_member", {"name": name, **fields})
    return get_member(conn, member_id)


def get_member(conn: sqlite3.Connection, member_id: str) -> TeamMember:
    row = base.get(conn, "team_member", member_id)
    if row is None:
        raise KeyError(f"team member {member_id} not found")
    return TeamMember.from_row(row)


def list_members(conn: sqlite3.Connection, active_only: bool = True) -> list[TeamMember]:
    where = base.alive()
    if active_only:
        where += " AND active = 1"
    rows = conn.execute(
        f"SELECT * FROM team_member WHERE {where} ORDER BY name"
    ).fetchall()
    return [TeamMember.from_row(r) for r in rows]


def update_member(
    conn: sqlite3.Connection, member_id: str, note: str | None = None, **changes: Any
) -> TeamMember:
    if changes.get("name"):
        _guard_name(conn, str(changes["name"]), member_id)
    base.update(conn, "team_member", member_id, changes, note)
    return get_member(conn, member_id)


def delete_member(conn: sqlite3.Connection, member_id: str) -> None:
    base.soft_delete(conn, "team_member", member_id)


# --- assignments --------------------------------------------------------------


def assign(
    conn: sqlite3.Connection,
    team_member_id: str,
    org_id: str | None = None,
    placement_id: str | None = None,
    **fields: Any,
) -> TeamAssignment:
    if (org_id is None) == (placement_id is None):
        raise ValueError("exactly one of org_id / placement_id must be set")
    assignment_id = base.insert(
        conn,
        "team_assignment",
        {
            "team_member_id": team_member_id,
            "org_id": org_id,
            "placement_id": placement_id,
            **fields,
        },
    )
    row = base.get(conn, "team_assignment", assignment_id)
    return TeamAssignment.from_row(row)  # type: ignore[arg-type]


def unassign(conn: sqlite3.Connection, assignment_id: str) -> None:
    base.soft_delete(conn, "team_assignment", assignment_id)


def for_org(conn: sqlite3.Connection, org_id: str) -> list[sqlite3.Row]:
    """Account-level assignments plus deal-level ones on this org's
    placements, with the member joined in."""
    return conn.execute(
        f"""
        SELECT ta.*, tm.name AS member_name, tm.title AS member_title,
               tm.specialty, p.ref AS placement_ref, p.program_name
        FROM team_assignment ta
        JOIN team_member tm ON tm.id = ta.team_member_id
        LEFT JOIN placement p ON p.id = ta.placement_id
        WHERE (ta.org_id = ? OR p.org_id = ?)
          AND {base.alive('ta')} AND {base.alive('tm')}
        ORDER BY ta.placement_id IS NOT NULL, tm.name
        """,
        (org_id, org_id),
    ).fetchall()


def for_member(conn: sqlite3.Connection, member_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT ta.*, o.name AS org_name, o.id AS resolved_org_id,
               p.ref AS placement_ref, p.program_name
        FROM team_assignment ta
        LEFT JOIN placement p ON p.id = ta.placement_id
        LEFT JOIN org o ON o.id = COALESCE(ta.org_id, p.org_id)
        WHERE ta.team_member_id = ? AND {base.alive('ta')}
        ORDER BY o.name
        """,
        (member_id,),
    ).fetchall()
