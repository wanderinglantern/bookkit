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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from pathlib import Path

_MIGRATION_RE = re.compile(r"^(\d{3})_.+\.sql$")

BLAST_CAP = 250
"""Most entities one batched writer action may touch before it is refused.
Grant's call, 2026-08-14 (was 25, then 50)."""

BUSY_TIMEOUT_MS = 5000
"""How long a writer waits for the write lock before giving up.

This is ALSO Python's sqlite3 default (`connect(timeout=5.0)`), so the value
is not a change — stating it is. The TUI and the MCP server both hold
read-write connections to the same file and `transaction()` takes the lock up
front with BEGIN IMMEDIATE, so the day someone passes `timeout=` or swaps the
driver, losing this silently turns every overlap into 'database is locked'."""


class BlastRadiusExceeded(Exception):
    """A batched write tried to touch more entities than its cap allows."""


@dataclass
class BatchState:
    """Ambient state for one batched action: which batch events belong to, and
    how many distinct entities it has touched so far."""

    batch_id: str
    cap: int = BLAST_CAP
    entities: set[str] = dc_field(default_factory=set)

    def touch(self, entity_id: str) -> None:
        """Count a distinct entity against the cap. Raising here rides the
        existing ROLLBACK in transaction(), so an over-cap write leaves NOTHING
        behind — and the check cannot be forgotten by a future write tool,
        because it lives under log_event rather than in any tool."""
        if entity_id in self.entities:
            return
        if len(self.entities) >= self.cap:
            raise BlastRadiusExceeded(
                f"this action would touch more than {self.cap} records; "
                "narrow it and try again"
            )
        self.entities.add(entity_id)


_current_batch: ContextVar[BatchState | None] = ContextVar(
    "bookkit_current_batch", default=None
)

_tx_depth: ContextVar[int] = ContextVar("bookkit_tx_depth", default=0)
"""How many transaction() blocks deep this context is. Only the outermost
issues BEGIN/COMMIT; see transaction() for why nesting joins."""


def current_batch() -> BatchState | None:
    """The batch events written right now belong to, or None.

    A ContextVar rather than an attribute on the connection: sqlite3.Connection
    is a C type with no __dict__ and rejects attribute assignment. It is also
    the right scope for the MCP server's async tool wrappers, where a module
    global would bleed between concurrent calls."""
    return _current_batch.get()


def utc_now() -> str:
    """UTC ISO-8601 timestamp, second precision — the canonical created_at/updated_at form."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_db_path(env: Mapping[str, str] | None = None) -> Path:
    """$BOOKKIT_DB overrides; else $XDG_DATA_HOME/bookkit/bookkit.db.

    `env` defaults to this process's environment. Pass an explicit mapping to
    resolve the path some *other* process would land on — connector.py passes
    an empty one to ask "where would a GUI app that inherits no shell
    environment look?", which is the whole question the Command/Arguments
    fields have to answer.
    """
    source = os.environ if env is None else env
    override = source.get("BOOKKIT_DB")
    if override:
        return Path(override).expanduser()
    xdg = source.get("XDG_DATA_HOME")
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
def transaction(
    conn: sqlite3.Connection, batch: BatchState | None = None
) -> Iterator[None]:
    """A REAL write transaction on the autocommit connection. BEGIN IMMEDIATE
    takes the write lock up front; any exception rolls the whole batch back.
    Without this, conn.commit()/rollback() are silent no-ops.

    `batch` groups every event written inside the block under one id, so one
    writer action becomes one undoable unit. It defaults to None, which is why
    imports/commit.py stays unbatched without needing a special case.

    NESTING JOINS, it does not nest: SQLite has no nested BEGIN, and once the
    TUI opens a batch around a whole writer action, inner helpers that already
    wrap their own writes (entity_actions' RFI paste, services/merge) would
    otherwise raise "cannot start a transaction within a transaction". An
    inner call joins the outer one — same lock, same commit, all-or-nothing
    across both — and an inner `batch=` is deliberately IGNORED, because the
    outermost writer action is what the user thinks of as one undo unit."""
    depth = _tx_depth.get()
    if depth:
        token = _tx_depth.set(depth + 1)
        try:
            yield
        finally:
            _tx_depth.reset(token)
        return

    batch_token = _current_batch.set(batch)
    depth_token = _tx_depth.set(1)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        _tx_depth.reset(depth_token)
        _current_batch.reset(batch_token)


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
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if migrate:
        apply_migrations(conn)
    return conn


def connect_readonly(path: Path | str | None = None) -> sqlite3.Connection:
    """A mode=ro URI connection: read-only enforced by SQLite itself, not by
    convention — the MCP server's read tools use this."""
    target = Path(path) if path else default_db_path()
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
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


def snapshot(conn: sqlite3.Connection, db_path: Path) -> Path:
    """Timestamped backup into `backups/` beside the database.

    The importers' rollback story, and now `seed --force`'s too — one
    implementation rather than a second copy that drifts. Collisions inside
    one second get a counter, because two writes in the same second must not
    silently overwrite each other's only rollback."""
    backups = db_path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().replace(":", "-")
    dest, n = backups / f"{db_path.name}.{stamp}.bak", 2
    while dest.exists():
        dest, n = backups / f"{db_path.name}.{stamp}.{n}.bak", n + 1
    return backup(conn, dest)


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
