"""Existing-record vocabularies for inline completion — data consistency:
the form suggests what the book already calls things, so 'AXA XL' never
gains a sibling spelled 'Axa XL'. Read-only; every list is alphabetical,
de-duplicated case-insensitively (first spelling wins)."""

from __future__ import annotations

import sqlite3

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
    return _dedupe(_column(conn, "task", "category"))


def rfi_categories(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "rfi_item", "category"))
