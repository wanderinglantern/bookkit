"""program_link — confirmed towerkit-file ↔ org links (§5.2)."""

from __future__ import annotations

import sqlite3

from ..db import utc_now


def confirm(conn: sqlite3.Connection, path: str, org_id: str, insured_name: str) -> None:
    conn.execute(
        "INSERT INTO program_link (path, org_id, insured_name, confirmed_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT (path) DO UPDATE SET org_id = excluded.org_id,"
        " insured_name = excluded.insured_name, confirmed_at = excluded.confirmed_at",
        (path, org_id, insured_name, utc_now()),
    )


def org_for_path(conn: sqlite3.Connection, path: str) -> str | None:
    row = conn.execute("SELECT org_id FROM program_link WHERE path = ?", (path,)).fetchone()
    return row["org_id"] if row else None


def all_links(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM program_link ORDER BY path").fetchall()


def forget(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM program_link WHERE path = ?", (path,))
