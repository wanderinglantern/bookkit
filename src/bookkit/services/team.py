"""'Who do I go to for a cyber placement?' — specialist lookup across
member specialties and live assignment lines."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..models import TeamMember
from ..repo import team


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
