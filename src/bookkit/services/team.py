"""Team rules every surface shares: the specialist lookup, and retiring /
reinstating a colleague.

Deactivation lived as a private helper in mcpserver, which meant the web
surface (gap 7) would have had to re-implement the refusal, the cascade and
the one-batch rule — the exact drift repo/team.py's name guard exists to
prevent. The rule now lives here; mcpserver delegates (with its own
provenance stamp — see `member_deactivate`'s `provenance` hook) and the web
calls it directly. Each caller differs only by `source`, the same seam
services/contacts.py settled: the service opens the batch itself because the
cascade and the active flip must be ONE undo unit no matter which surface
asked, and db.transaction nests by joining — a caller wrapping this in its
own open_batch would leave a second, permanently empty batch row behind."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..models import TeamMember
from ..repo import base, team
from . import batches as batches_svc


@dataclass(frozen=True)
class SpecialistMatch:
    member: TeamMember
    score: float
    evidence: str  # the specialty/lines text that matched


def find_specialists(conn: sqlite3.Connection, query: str) -> list[SpecialistMatch]:
    """Rank active members against a line/specialty query, best first.
    Matches the member's own specialty AND the lines on their assignments —
    someone actively placing cyber counts as a cyber person."""
    out: list[SpecialistMatch] = []
    for member in team.list_members(conn):
        haystacks = [member.specialty or ""]
        haystacks += [
            row["lines"] or "" for row in team.for_member(conn, member.id)
        ]
        best, evidence = 0.0, ""
        for hay in haystacks:
            if not hay:
                continue
            score = fuzz.partial_ratio(query.lower(), hay.lower())
            if score > best:
                best, evidence = score, hay
        if best >= 60:
            out.append(SpecialistMatch(member, best, evidence))
    out.sort(key=lambda m: -m.score)
    return out


# --- retiring and reinstating a colleague ------------------------------------


def assignment_label(row: sqlite3.Row) -> str:
    """How one assignment reads in a refusal or a confirm: the client, plus
    the placement ref when it is deal-level rather than account-level. Moved
    from mcpserver so the web's confirm step and the MCP refusal name an
    assignment with the same words."""
    keys = row.keys()
    account = row["org_name"] if "org_name" in keys else None
    placement = row["placement_ref"] if "placement_ref" in keys else None
    label = account or "unscoped"
    return f"{label} ({placement})" if placement else label


@dataclass(frozen=True)
class Deactivation:
    """What a deactivation did, in the words every surface repeats."""

    member_id: str
    name: str
    unassigned: int
    batch: str


Provenance = Callable[[sqlite3.Connection, str, str], None]


def member_deactivate(
    conn: sqlite3.Connection,
    member_id: str,
    *,
    cascade: bool = False,
    source: str,
    provenance: Provenance | None = None,
) -> Deactivation:
    """Retire a colleague. Refuses while they still hold assignments — a
    roster that silently keeps pointing at someone who left is worse than a
    refusal — and names every one so the caller can act. cascade=True removes
    them and deactivates in ONE batch, so revert_batch puts it all back.

    `source` is the surface: 'mcp' | 'tui' | 'web'. `provenance` is the MCP
    server's per-entity source stamp, invoked inside the batch for each row
    touched — a hook rather than a hardcoded stamp because the batch row
    already records the source and the other surfaces add nothing per entity
    (the services/contacts.py precedent, minus dropping MCP's existing trail:
    tests pin that a cascaded removal leaves the same audit stamp as a
    standalone team_unassign).

    Raises KeyError for an unknown member (repo.team.get_member's own), and
    ValueError with the refusal sentence for everything else."""
    member = team.get_member(conn, member_id)
    if not member.active:
        raise ValueError(f"{member.name} is already inactive")
    rows = team.for_member(conn, member.id)
    if rows and not cascade:
        labels = ", ".join(assignment_label(r) for r in rows)
        raise ValueError(
            f"{member.name} is still on {len(rows)} assignments: {labels} — "
            f"unassign them first, or pass cascade=True to remove all "
            f"{len(rows)} and deactivate as one revertible batch"
        )
    summary = f"deactivated {member.name}"
    if rows:
        summary += f" and removed {len(rows)} assignments"
    # org_id stays None: a cascade spans clients, so no single org owns it.
    with batches_svc.open_batch(
        conn, source=source, tool="member_deactivate", summary=summary,
    ) as batch:
        for row in rows:
            team.unassign(conn, str(row["id"]))
            if provenance is not None:
                provenance(conn, "team_assignment", str(row["id"]))
        base.update(conn, "team_member", member.id, {"active": 0},
                    note=f"{source} deactivate")
        if provenance is not None:
            provenance(conn, "team_member", member.id)
    return Deactivation(
        member_id=member.id, name=member.name, unassigned=len(rows),
        batch=batch.ref,
    )


@dataclass(frozen=True)
class Reactivation:
    member_id: str
    name: str
    batch: str


def member_reactivate(
    conn: sqlite3.Connection,
    member_id: str,
    *,
    source: str,
    provenance: Provenance | None = None,
) -> Reactivation:
    """Bring a retired colleague back. Assignments a cascade removed do NOT
    come back — revert_batch is the undo for those."""
    member = team.get_member(conn, member_id)
    if member.active:
        raise ValueError(f"{member.name} is already active")
    with batches_svc.open_batch(
        conn, source=source, tool="member_reactivate",
        summary=f"reactivated {member.name}",
    ) as batch:
        base.update(conn, "team_member", member.id, {"active": 1},
                    note=f"{source} reactivate")
        if provenance is not None:
            provenance(conn, "team_member", member.id)
    return Reactivation(member_id=member.id, name=member.name, batch=batch.ref)
