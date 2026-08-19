"""Who is chasing a task — the candidate list, the resolution rule, and the
one write path for the three columns that hold the answer.

WHY THIS IS A MODULE AND NOT A FIELD. `task.assignee_*` is three columns
that only mean something together: a kind without an id is a dangling
reference, an id without a kind names no table, and a freeform name beside
either is a contradiction. Nothing outside this module writes them. That is
the same rule repo/team.py's `_guard_name` follows — a guard on IDENTITY
belongs in repo/, where every surface inherits it, rather than in whichever
caller happened to be written first (CLAUDE.md).

WHAT A NAME COLLISION DOES. Two people can share a name, and one of them can
be ours while the other is the client's — which is precisely the pair that
would flip the client-facing Owner column. So:

  * every suggestion is offered QUALIFIED — "Sam Garcia — our team",
    "Sam Garcia — Atomic Industries" — and an exact match on that label
    resolves to that person, unambiguously, whichever way the collision
    falls;
  * a bare name typed past the picker resolves only when it names exactly
    ONE candidate. Two candidates called Sam Garcia resolve to NEITHER;
  * anything unresolved is stored as a freeform name, with no kind — and a
    row with no kind renders `Us` on the client's copy.

So a collision degrades to "ours", never to "yours". The two directions are
not symmetric: telling a client we own something they actually owe us is an
overclaim they will correct; telling them they owe us something we own is a
false demand on a document they read. Refusing to guess is the same call
parse_human_date makes on a bare number, for the same reason.

Resolving to an id at pick time does NOT make the question moot, because the
field is freeform by requirement — the AE has to be able to name someone the
book has never heard of. Both halves are handled above."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..models import AssigneeKind, Task
from . import base, contacts, orgs, team

# What separates a person from where they work, in a suggestion label. A
# character no name contains, so splitting a label back apart is never
# ambiguous — and the same em dash every other composed label in the book
# uses (section headers, project labels).
_QUALIFIER_SEP = " — "

# What the team's own people are qualified BY. They have no org row, and
# "our team" is what the AE would say out loud.
OUR_TEAM = "our team"


@dataclass(frozen=True)
class Candidate:
    """One person the picker can offer, and what picking them stores."""

    kind: AssigneeKind
    id: str
    name: str
    qualifier: str  # "our team", or the org they work at

    @property
    def label(self) -> str:
        return f"{self.name}{_QUALIFIER_SEP}{self.qualifier}"


def candidates(conn: sqlite3.Connection, org_id: str | None = None) -> list[Candidate]:
    """Everyone a task can be assigned to, in one ordered list.

    THREE SOURCES, and the third is the one an earlier version of this design
    left out: team members, the account's own contacts, and contacts at
    MARKET orgs. Underwriters and wholesalers are records in this book —
    `contacts.for_org` never filtered by org kind and the markets screen has
    bound `w` → add_underwriter since it was written — so a picker without
    them is missing exactly the people the AE spends the day chasing.

    `org_id` scopes the second source only. Passing None (the global task
    form, reached by ctrl+t before an account has been chosen) yields team
    plus markets, which is honest: the account is not known yet, so its
    contacts cannot be offered. Typing one of them anyway lands as freeform,
    on the safe side of the export.

    Deduplicated on (kind, id): a task filed against a MARKET org would
    otherwise see its own contacts twice, once per source."""
    found: dict[tuple[str, str], Candidate] = {}

    for member in team.list_members(conn):
        found[("team", member.id)] = Candidate(
            AssigneeKind.TEAM, member.id, member.name, OUR_TEAM
        )

    if org_id is not None:
        try:
            org_name = orgs.get(conn, org_id).name
        except KeyError:
            org_name = ""
        if org_name:
            for contact in contacts.for_org(conn, org_id):
                found.setdefault(
                    ("contact", contact.id),
                    Candidate(AssigneeKind.CONTACT, contact.id, contact.name, org_name),
                )

    for row in contacts.at_market_orgs(conn):
        name = f"{row['first_name']} {row['last_name']}"
        found.setdefault(
            ("contact", str(row["id"])),
            Candidate(
                AssigneeKind.CONTACT, str(row["id"]), name, str(row["org_name"])
            ),
        )

    return sorted(found.values(), key=lambda c: (c.name.lower(), c.qualifier.lower()))


def resolve(pool: list[Candidate], typed: str | None) -> Candidate | None:
    """One typed string → the person it names, or None.

    Two passes, in this order and no others. An exact (case-folded) match on
    the QUALIFIED label wins first: that is what the picker inserts, and it
    is unique by construction. Failing that, a bare name resolves only when
    it names exactly one candidate — because a bare name matching two is the
    collision this module exists for, and the answer to an ambiguous
    identity is to refuse it, not to take the first row (which is exactly the
    bug repo/team.py's uniqueness guard was written to stop)."""
    text = (typed or "").strip().casefold()
    if not text:
        return None
    for candidate in pool:
        if candidate.label.casefold() == text:
            return candidate
    matches = [c for c in pool if c.name.casefold() == text]
    return matches[0] if len(matches) == 1 else None


def columns(
    conn: sqlite3.Connection, typed: str | None, *, org_id: str | None = None
) -> dict[str, Any]:
    """The three column values one typed string becomes.

    Always all three keys, always explicitly — including the Nones. Clearing
    an assignee has to null the columns the last one left behind, and a dict
    that omitted them would let a stale id survive a rename to somebody the
    book has never heard of."""
    picked = resolve(candidates(conn, org_id), typed)
    if picked is not None:
        return {
            "assignee_kind": picked.kind.value,
            "assignee_id": picked.id,
            "assignee_name": None,
        }
    text = (typed or "").strip()
    return {
        "assignee_kind": None,
        "assignee_id": None,
        "assignee_name": text or None,
    }


def set_on_task(
    conn: sqlite3.Connection,
    task_id: str,
    typed: str | None,
    *,
    org_id: str | None = None,
    note: str | None = None,
) -> None:
    """Write the answer to one task. THE only write path for these columns.

    One `base.update` call, so all three land in one statement and the caller's
    open batch owns them as one undo unit — three separate updates would be
    three events `u` puts back one at a time, and the halfway state (a kind
    with no id) means nothing."""
    base.update(conn, "task", task_id, columns(conn, typed, org_id=org_id), note)


# --- reading it back ----------------------------------------------------------


def _resolved(conn: sqlite3.Connection, task: Task) -> Candidate | None:
    """The person a resolved assignment points at, or None when the row has
    gone. Both target tables soft-delete, so a retired colleague or a removed
    contact leaves an id pointing at nothing — which reads as unassigned
    rather than as a crash on a list of somebody's open work."""
    if task.assignee_kind is None or not task.assignee_id:
        return None
    try:
        if task.assignee_kind is AssigneeKind.TEAM:
            member = team.get_member(conn, task.assignee_id)
            return Candidate(AssigneeKind.TEAM, member.id, member.name, OUR_TEAM)
        contact = contacts.get(conn, task.assignee_id)
        try:
            qualifier = orgs.get(conn, contact.org_id).name
        except KeyError:
            qualifier = ""
        return Candidate(AssigneeKind.CONTACT, contact.id, contact.name, qualifier)
    except KeyError:
        return None


def name_of(conn: sqlite3.Connection, task: Task) -> str:
    """The PLAIN name, for a table cell — "" when nobody is on it.

    Read live, never denormalized onto the task: a colleague who marries and
    is renamed in the team screen is renamed on every task they hold, with no
    sweep and nothing left saying the old name."""
    candidate = _resolved(conn, task)
    if candidate is not None:
        return candidate.name
    return task.assignee_name or ""


def label_of(conn: sqlite3.Connection, task: Task) -> str:
    """The QUALIFIED label, for prefilling an editor — "" when unassigned.

    Not the plain name: what a form pre-fills has to be a value its own
    resolver accepts back unchanged, or opening a task and pressing save
    would quietly downgrade a resolved assignee to freeform (which is exactly
    the ENTRY ACCEPTS CENTS rule in CLAUDE.md, applied to a different field)."""
    candidate = _resolved(conn, task)
    if candidate is not None:
        return candidate.label
    return task.assignee_name or ""
