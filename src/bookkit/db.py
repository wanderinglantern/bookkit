"""Connection factory, PRAGMAs, migration runner.

This module is the ONLY place that opens the database file, so swapping in
SQLCipher later is a one-function change. The file is created 0600 under
$XDG_DATA_HOME/bookkit/ — it holds client contacts and premium figures.

Migrations are numbered forward-only SQL files; each is applied inside its own
transaction and recorded in schema_version.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

_MIGRATION_RE = re.compile(r"^(\d{3})_.+\.sql$")


def utc_now() -> str:
    """UTC ISO-8601 timestamp, second precision — the canonical created_at/updated_at form."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_db_path() -> Path:
    """$BOOKKIT_DB overrides; else $XDG_DATA_HOME/bookkit/bookkit.db."""
    env = os.environ.get("BOOKKIT_DB")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "bookkit" / "bookkit.db"


def migrations_dir() -> Path:
    """The repo-root migrations/ directory (editable install), or the copy
    shipped inside the package for wheel installs."""
    packaged = Path(__file__).resolve().parent / "migrations"
    if packaged.is_dir():
        return packaged
    repo = Path(__file__).resolve().parents[2] / "migrations"
    if repo.is_dir():
        return repo
    raise FileNotFoundError("no migrations directory found")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """A REAL write transaction on the autocommit connection. BEGIN IMMEDIATE
    takes the write lock up front; any exception rolls the whole batch back.
    Without this, conn.commit()/rollback() are silent no-ops."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def connect(path: Path | str | None = None, migrate: bool = True) -> sqlite3.Connection:
    """Open (creating if needed, mode 0600) and optionally migrate the database."""
    db_path = Path(path) if path is not None else default_db_path()
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.exists():
            db_path.touch(mode=0o600)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; explicit BEGIN/COMMIT
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    if migrate:
        apply_migrations(conn)
    return conn


def connect_readonly(path: Path | str | None = None) -> sqlite3.Connection:
    """A mode=ro URI connection: read-only enforced by SQLite itself, not by
    convention — the MCP server's read tools use this."""
    target = Path(path) if path else default_db_path()
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    got = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    return int(got or 0)


def pending_migrations(conn: sqlite3.Connection) -> list[tuple[int, Path]]:
    current = schema_version(conn)
    found: list[tuple[int, Path]] = []
    for entry in sorted(migrations_dir().iterdir()):
        match = _MIGRATION_RE.match(entry.name)
        if match and int(match.group(1)) > current:
            found.append((int(match.group(1)), entry))
    return found


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply every pending migration, each in its own transaction. Returns the
    versions applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version"
        " (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied: list[int] = []
    for version, path in pending_migrations(conn):
        sql = path.read_text(encoding="utf-8")
        script = (
            "BEGIN;\n"
            + sql
            + f"\nINSERT INTO schema_version (version, applied_at) "
            f"VALUES ({version}, '{utc_now()}');\nCOMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def integrity_check(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")


def backup(conn: sqlite3.Connection, dest: Path) -> Path:
    """VACUUM INTO copy — safe against a live WAL database — plus an integrity
    check of the copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"refusing to overwrite existing backup {dest}")
    conn.execute("VACUUM INTO ?", (str(dest),))
    os.chmod(dest, 0o600)
    check = sqlite3.connect(dest)
    try:
        if not integrity_check(check):
            raise RuntimeError(f"backup {dest} failed integrity check")
    finally:
        check.close()
    return dest
