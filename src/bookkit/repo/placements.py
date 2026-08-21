"""Placements — the renewal calendar spine. One row per program per period."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ids import PLACEMENT_REF, next_ref
from ..models import Placement
from . import base


def create(
    conn: sqlite3.Connection,
    org_id: str,
    program_name: str,
    period_from: str,
    period_to: str,
    **fields: Any,
) -> Placement:
    fields.setdefault("ref", next_ref(conn, PLACEMENT_REF))
    placement_id = base.insert(
        conn,
        "placement",
        {
            "org_id": org_id,
            "program_name": program_name,
            "period_from": period_from,
            "period_to": period_to,
            **fields,
        },
    )
    return get(conn, placement_id)


def get(conn: sqlite3.Connection, placement_id: str) -> Placement:
    row = base.get(conn, "placement", placement_id)
    if row is None:
        raise KeyError(f"placement {placement_id} not found")
    return Placement.from_row(row)


def find(conn: sqlite3.Connection, ref_or_id: str) -> Placement | None:
    row = conn.execute(
        f"SELECT * FROM placement WHERE (id = ? OR ref = ?) AND {base.alive()}",
        (ref_or_id, ref_or_id),
    ).fetchone()
    return Placement.from_row(row) if row else None


def for_org(conn: sqlite3.Connection, org_id: str) -> list[Placement]:
    rows = conn.execute(
        f"SELECT * FROM placement WHERE org_id = ? AND {base.alive()} ORDER BY period_to DESC",
        (org_id,),
    ).fetchall()
    return [Placement.from_row(r) for r in rows]


def expiring_between(
    conn: sqlite3.Connection, start: str, end: str, statuses: tuple[str, ...] | None = None
) -> list[Placement]:
    where = [base.alive(), "period_to >= ?", "period_to <= ?"]
    params: list[Any] = [start, end]
    if statuses:
        where.append(f"status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    rows = conn.execute(
        f"SELECT * FROM placement WHERE {' AND '.join(where)} ORDER BY period_to", params
    ).fetchall()
    return [Placement.from_row(r) for r in rows]


def unlinked_overlapping(
    conn: sqlite3.Connection, org_id: str, start: str, end: str
) -> list[Placement]:
    """File-less placements whose period overlaps [start, end) — adoption
    candidates when a towerkit file appears for this org."""
    rows = conn.execute(
        f"""SELECT * FROM placement WHERE org_id = ? AND program_path IS NULL
            AND period_from < ? AND period_to > ? AND {base.alive()}
            ORDER BY period_to""",
        (org_id, end, start),
    ).fetchall()
    return [Placement.from_row(r) for r in rows]


def all_linked(conn: sqlite3.Connection) -> list[Placement]:
    rows = conn.execute(
        f"SELECT * FROM placement WHERE program_path IS NOT NULL AND {base.alive()}"
    ).fetchall()
    return [Placement.from_row(r) for r in rows]


def by_program_path(conn: sqlite3.Connection, path: str) -> Placement | None:
    """The placement linked to this file, whichever spelling its row holds.

    Paths are stored relative to a program root now (see programpath) and were
    stored absolute before, so an equality test against one spelling misses
    the other. Getting this wrong does not fail loudly: `sync` would decide it
    had never seen the file, and either adopt a different placement or create
    a duplicate one beside the real one."""
    from .. import programpath

    forms = programpath.stored_forms(conn, path)
    placeholders = ", ".join("?" for _ in forms)
    row = conn.execute(
        f"SELECT * FROM placement WHERE program_path IN ({placeholders})"
        f" AND {base.alive()}",
        forms,
    ).fetchone()
    return Placement.from_row(row) if row else None


def next_renewal_for_org(conn: sqlite3.Connection, org_id: str, today: str) -> Placement | None:
    row = conn.execute(
        f"""SELECT * FROM placement WHERE org_id = ? AND period_to >= ? AND {base.alive()}
            ORDER BY period_to LIMIT 1""",
        (org_id, today),
    ).fetchone()
    return Placement.from_row(row) if row else None


def update(
    conn: sqlite3.Connection, placement_id: str, note: str | None = None, **changes: Any
) -> Placement:
    base.update(conn, "placement", placement_id, changes, note)
    return get(conn, placement_id)


def delete(conn: sqlite3.Connection, placement_id: str) -> None:
    base.soft_delete(conn, "placement", placement_id)


# What a program CARRIES, per table, alive only. Here and not in the service
# that reads it because repo/ owns every query — and because these six are the
# whole set of tables that name a placement, which is a fact about the schema
# and belongs beside it. A seventh added by a migration and forgotten here is
# how a removal would start stranding rows on a dead foreign key.
_DEPENDANTS: tuple[tuple[str, str, str], ...] = (
    ("submission", "submission", "submissions"),
    ("task", "task", "tasks"),
    ("rfi_request", "information request", "information requests"),
    ("document", "document", "documents"),
    ("team_assignment", "team assignment", "team assignments"),
    ("project_need", "project need", "project needs"),
)


def dependants(conn: sqlite3.Connection, placement_id: str) -> list[tuple[str, int]]:
    """Live rows pointing at this placement, as (plural-aware label, count),
    in schema order — empty when nothing holds it.

    Counts, not records: the caller is composing a refusal, not a screen. The
    label is returned WITH the count so no caller has to own the pluralisation
    of "information request", which is the sort of thing two callers spell two
    ways.
    """
    found: list[tuple[str, int]] = []
    for table, one, many in _DEPENDANTS:
        count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE placement_id = ? AND {base.alive()}",
                (placement_id,),
            ).fetchone()[0]
        )
        if count:
            found.append((one if count == 1 else many, count))
    return found
