"""Existing-record vocabularies for inline completion — data consistency:
the form suggests what the book already calls things, so 'AXA XL' never
gains a sibling spelled 'Axa XL'. Read-only; every list is alphabetical,
de-duplicated case-insensitively (first spelling wins)."""

from __future__ import annotations

import sqlite3

from ..models import CONTACT_ROLES, INTERNAL_CATEGORY
from . import base


def _dedupe(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        cleaned = value.strip()
        if cleaned:
            seen.setdefault(cleaned.lower(), cleaned)
    return sorted(seen.values(), key=str.lower)


def _column(conn: sqlite3.Connection, table: str, column: str, where: str = "") -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM {table} "
        f"WHERE {column} IS NOT NULL AND {base.alive()} {where}"
    ).fetchall()
    return [str(r[0]) for r in rows]


def owners(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "org", "owner"))


def industries(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "org", "industry"))


def program_names(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "placement", "program_name"))


def market_names(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "org", "name", "AND kind = 'market'"))


def specialties(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "team_member", "specialty"))


def lines(conn: sqlite3.Connection) -> list[str]:
    """One lines-of-cover vocabulary across everything that names lines:
    appetite, project needs, opportunities, and team assignments (the last
    two split on commas — they hold lists)."""
    values = _column(conn, "appetite", "line")
    values += _column(conn, "project_need", "line")
    for source_table, column in (("opportunity", "lines"), ("team_assignment", "lines")):
        for blob in _column(conn, source_table, column):
            values += [part for part in blob.split(",")]
    return _dedupe(values)


def task_categories(conn: sqlite3.Connection) -> list[str]:
    """Existing task categories PLUS the well-known Internal category — the
    one flag that keeps a task out of the client export has to be OFFERED
    before anyone has typed it once, or nobody discovers it exists. It lives
    here rather than in the form for the same reason the team-name guard
    lives in repo/team.py: every surface inherits it. _dedupe folds case and
    keeps the first spelling, so a book that already says "internal" does not
    gain a sibling."""
    return _dedupe([*_column(conn, "task", "category"), INTERNAL_CATEGORY])


def rfi_categories(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "rfi_item", "category"))


def contact_roles(conn: sqlite3.Connection) -> list[str]:
    """The DECLARED contact-role vocabulary (models.CONTACT_ROLES) plus every
    role the book already uses — the option set for the role PICKER, on both
    surfaces.

    Both halves are load-bearing, and for opposite reasons. Without the
    declared list a fresh book offers nothing and `role` is a text box again,
    so nobody discovers that "broker_of_record" is a thing the field can say.
    Without the book's own values the picker would REFUSE a role already
    stored — `forms.spec.checked_option` is authoritative on the way in, so a
    bare select over CONTACT_ROLES would make every contact whose role was
    typed before this existed unsaveable until somebody re-classified them
    (Grant, 2026-08-20: constrain new entry, strand nothing already typed).

    The book comes FIRST so its own spelling wins a case collision — _dedupe
    folds case and keeps the first spelling, the same rule task_categories
    relies on so a book saying "internal" does not gain a sibling. A stored
    role therefore always appears in the options EXACTLY as stored, which is
    what a select needs to pre-select it."""
    return _dedupe([*_column(conn, "contact", "role"), *CONTACT_ROLES])


def contact_titles(conn: sqlite3.Connection) -> list[str]:
    """Job titles the book's contacts already carry.

    SUGGESTIONS, not a picker: a title is prose off a signature block ("VP,
    Risk Management & Insurance"), so the valid set is not knowable and a
    select would refuse the next real one. Completion still stops "VP Risk"
    and "V.P. Risk" from both existing at the same company.

    Contacts only — team_member.title is a different population (our internal
    grades, not a client's org chart) and mixing them would offer a broker
    title on a client contact and the reverse."""
    return _dedupe(_column(conn, "contact", "title"))
