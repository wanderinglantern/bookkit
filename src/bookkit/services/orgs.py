"""Org-level business rules every writing surface shares.

THE CREATE-DOOR DUPLICATE GUARD LIVES HERE, once. It existed twice before —
inline in mcpserver.client_create and inline in the navigator's onboarding
commit, both `rapidfuzz WRatio, score_cutoff=87` over the client list — while
the book screen's own new-account form had neither copy and wrote straight
past the rule. That is the repo/team.py name-uniqueness story again: a guard
in one caller is a guard the other callers skip. Extracting it is what lets
the web's create form, the TUI's two forms and MCP refuse the same near-name
with the same score.

This is deliberately NOT repo/orgs.guard_name. That guard is exact-name and
protects RENAMES (taking a name away from the row that answers to it), and it
deliberately exempts create because importers and sync legitimately mint
duplicate orgs from unchecked data — see its docstring. THIS guard is fuzzy
and belongs only on the create doors where a human is on the other end to
answer "did you mean the existing one?".
"""

from __future__ import annotations

import sqlite3

from ..models import Org
from ..repo import orgs as orgs_repo

# rapidfuzz WRatio, 0-100. 'Henderson Grp' vs 'Henderson Group' scores ~95;
# 87 is deliberately loose enough to catch near-misses while staying below
# scores for genuinely distinct short names (measured for mcpserver's
# client_create, 2026-08-13; the navigator's onboarding form adopted the
# same number).
DUPLICATE_CUTOFF = 87


def find_duplicate(conn: sqlite3.Connection, name: str) -> Org | None:
    """The existing client `name` is a near-miss of, or None.

    Candidates are CLIENTS — the book — because that is the list where two
    rows answering to one name makes every later name lookup land on the
    wrong account. Markets arrive deduplicated through their own alias
    machinery and importers, and their cure is services.merge, not a refusal
    here.
    """
    from rapidfuzz import fuzz, process

    existing = {o.name: o for o in orgs_repo.list_orgs(conn, kind="client")}
    match = process.extractOne(
        name, list(existing), scorer=fuzz.WRatio, score_cutoff=DUPLICATE_CUTOFF
    )
    return existing[match[0]] if match else None
