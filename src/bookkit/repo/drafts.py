"""Scratch drafts: half-typed quick-capture text survives crashes and esc."""

from __future__ import annotations

import sqlite3

from ..db import utc_now
from ..ids import new_ulid


def save(conn: sqlite3.Connection, screen: str, payload: str) -> None:
    conn.execute("DELETE FROM draft WHERE screen = ?", (screen,))
    conn.execute(
        "INSERT INTO draft (id, screen, payload, saved_at) VALUES (?, ?, ?, ?)",
        (new_ulid(), screen, payload, utc_now()),
    )


def load(conn: sqlite3.Connection, screen: str) -> str | None:
    row = conn.execute(
        "SELECT payload FROM draft WHERE screen = ? ORDER BY saved_at DESC LIMIT 1",
        (screen,),
    ).fetchone()
    return row["payload"] if row else None


def clear(conn: sqlite3.Connection, screen: str) -> None:
    conn.execute("DELETE FROM draft WHERE screen = ?", (screen,))
