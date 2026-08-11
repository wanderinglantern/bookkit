"""Key/value settings, JSON-encoded. Program roots are the first tenant."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import utc_now

PROGRAM_ROOTS = "program_roots"


def get(conn: sqlite3.Connection, key: str) -> Any | None:
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def set_value(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO setting (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (key, json.dumps(value), utc_now()),
    )


def get_program_roots(conn: sqlite3.Connection) -> list[str]:
    return list(get(conn, PROGRAM_ROOTS) or [])


def set_program_roots(conn: sqlite3.Connection, roots: list[str]) -> None:
    set_value(conn, PROGRAM_ROOTS, roots)
