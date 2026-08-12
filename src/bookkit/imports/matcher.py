"""Match staged records to what the book already holds. Placement matching is
period-aware by spec: the key is (account, program, effective period), never
account alone — a client with staggered effective dates has several live
placements and a row must land on the right one, or ask."""

from __future__ import annotations

import sqlite3

from rapidfuzz import fuzz

from ..repo import contacts, orgs, placements

_ORG_THRESHOLD = 90


def match_org(conn: sqlite3.Connection, name: str) -> tuple[str | None, list[str]]:
    """(org_id, []) on an exact or single confident fuzzy match against client
    orgs; (None, candidates) when several are plausible — an explicit pick,
    never a guess; (None, []) when nothing comes close."""
    exact = orgs.find_by_name(conn, name)
    if exact is not None and exact.kind == "client":
        return exact.id, []
    scored = [
        (org, fuzz.WRatio(name.lower(), org.name.lower()))
        for org in orgs.list_orgs(conn, kind="client")
    ]
    hits = [(org, score) for org, score in scored if score >= _ORG_THRESHOLD]
    if len(hits) == 1:
        return hits[0][0].id, []
    if hits:
        return None, [org.name for org, _ in hits]
    return None, []


def match_contact(conn: sqlite3.Connection, org_id: str, email: str) -> str | None:
    wanted = email.strip().lower()
    for contact in contacts.for_org(conn, org_id, active_only=False):
        if contact.email is not None and contact.email.lower() == wanted:
            return contact.id
    return None


def match_placement(
    conn: sqlite3.Connection,
    org_id: str,
    program_name: str,
    period_from: str,
    period_to: str,
) -> str | None:
    """Same program (case-insensitive) AND overlapping period — ISO date
    strings compare lexicographically, so no parsing needed."""
    wanted = program_name.strip().lower()
    for placement in placements.for_org(conn, org_id):
        if placement.program_name.strip().lower() != wanted:
            continue
        # half-open periods: an expiry that equals the next inception is
        # adjacency (renew-at-birth), not overlap
        if placement.period_from < period_to and period_from < placement.period_to:
            return placement.id
    return None
