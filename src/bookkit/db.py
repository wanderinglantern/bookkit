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
import threading
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

_tx_lock = threading.RLock()
"""Serializes the OUTERMOST transaction across threads, within THIS process.

_tx_depth is a ContextVar, so a second thread on the same connection sees
depth 0 and issues its own BEGIN IMMEDIATE — "cannot start a transaction
within a transaction". Unreachable while the TUI, CLI and MCP server were
each single-threaded on their connection; reachable the moment the web layer
runs handlers in uvicorn's threadpool. SQLite serializes writers at the file
level anyway, so waiting here costs nothing a writer was not already going to
wait for.

Process-local: the TUI runs as a separate OS process with its own Python
interpreter and therefore its own module-level `_tx_lock` instance, so this
gives it no cross-process exclusion and creates no cross-process cycle —
SQLite's own file-level locking (BEGIN IMMEDIATE, busy_timeout) is what
serializes writers across processes; this lock only serializes threads
inside one.

An RLock is a defensive margin, not a requirement, as of the join logic in
services.batches.open_batch: that code reads prior events with plain SELECTs
before ever calling transaction(), so it never re-enters this lock while
holding it, and a plain Lock would behave identically today. Keep it an RLock
anyway — the cost of reentrancy is nothing, and it is what stops a future
nested acquire from becoming a same-thread deadlock instead of a bug report.

DO NOT DELETE THIS AS REDUNDANT now that the web layer gives every thread its
own connection (web.app.ThreadConnections, 2026-08-18). The "transaction
within a transaction" error above is indeed gone with the shared connection,
but two BEGIN IMMEDIATEs on two connections now contend at the SQLite FILE
level instead, where losing means burning the whole BUSY_TIMEOUT_MS wait and
then raising "database is locked".

Measured both ways, because the obvious form of that claim is false: ordinary
concurrent web saves do NOT need this lock — 4 writers x 300 POSTs came back
clean without it, each transaction being microseconds long and busy_timeout
absorbing the overlap. What needs it is a writer holding the transaction
LONGER than the timeout, IN THIS PROCESS. The web app has exactly one such
writer today: the batch revert behind web/routes/changes.py, whose single
transaction (services.batches.revert) applies up to BLAST_CAP entities of
undelete/update/soft-delete with an event_log row each. A bulk import or a
TUI batch is NOT an example, though both are longer — they run in a SEPARATE
PROCESS with its own instance of this lock, which therefore excludes nothing;
there the "database is locked" refusal happens exactly as the paragraph above
warns, and only SQLite's own busy_timeout stands between it and the user.
With the lock a concurrent save queues and succeeds; without it the save is
refused after 5s with "database is locked" and the user's edit is lost.
tests/test_web_concurrency.py asserts that, so deleting this lock fails the
suite rather than a review.

Known and deliberately not restructured: an `async def` route that waits here
blocks the WHOLE event loop, because this is a threading.RLock and the write
routes run on the loop (see web.app.ThreadConnections). Harmless while every
web write is microseconds long; read this before adding a slow one."""


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
    outermost writer action is what the user thinks of as one undo unit.

    Only the OUTERMOST call takes `_tx_lock` (see its docstring): a joining
    call already runs inside the holder's BEGIN IMMEDIATE/COMMIT, so there is
    nothing left for it to serialize against."""
    depth = _tx_depth.get()
    if depth:
        token = _tx_depth.set(depth + 1)
        try:
            yield
        finally:
            _tx_depth.reset(token)
        return

    _tx_lock.acquire()
    try:
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
    finally:
        _tx_lock.release()


def connect(
    path: Path | str | None = None,
    migrate: bool = True,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open (creating if needed, mode 0600) and optionally migrate the database.

    check_same_thread=False is for the web layer only, and ONLY so that its
    lifespan can close connections it did not open: a FastAPI app's lifespan
    runs on the ASGI event loop's own thread (uvicorn, and TestClient's
    portal), not the thread that called create_app(), so the strict check
    makes even closing a connection on shutdown raise ProgrammingError.

    It is NOT a licence to share the connection. Route handlers declared
    `def` rather than `async def` do not run on the event loop's thread at
    all — Starlette hands them to an anyio worker threadpool, so concurrent
    requests are concurrent THREADS. This docstring used to say handlers ran
    on the loop's thread, web/app.py believed it, and one connection served
    all of them; ~21% of requests at 6 concurrent workers came back wrong and
    event_log took permanent damage. web.app.ThreadConnections is the
    arrangement that replaced it: one connection per thread, still
    check_same_thread=False purely for the shutdown sweep.

    `async def` handlers DO run on the loop's thread, and bookkit's eight web
    write routes are async (they await request.form()), so they really do all
    share one connection. That is safe only because asyncio does not preempt
    and no `await` sits inside a transaction — a rule, enforced by
    tests/test_conventions.py::test_no_await_inside_a_transaction, not a
    property of this function.

    The TUI and CLI stay on the strict default: each opens and uses its
    connection from a single thread, and the check catches a real bug
    there."""
    db_path = Path(path) if path is not None else default_db_path()
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.exists():
            db_path.touch(mode=0o600)
    conn = sqlite3.connect(  # autocommit; explicit BEGIN/COMMIT
        db_path, isolation_level=None, check_same_thread=check_same_thread
    )
    conn.row_factory = sqlite3.Row
    # busy_timeout FIRST: it is the only pragma here that changes how the
    # other three behave when a writer holds the file, and journal_mode=WAL
    # can need an exclusive lock to convert the journal — with a zero timeout
    # it gives up instantly instead of waiting. Insurance, not a measured fix:
    # no defect was found in the old order (WAL readers do not block, ~2ms
    # under both BEGIN IMMEDIATE and BEGIN EXCLUSIVE). It matters more now
    # that connections open on the REQUEST path (web.app.ThreadConnections),
    # not only at startup.
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    if migrate:
        snapshot_before_migrations(conn, db_path)
        apply_migrations(conn)
    return conn


def snapshot_before_migrations(
    conn: sqlite3.Connection, db_path: Path
) -> Path | None:
    """The rollback for a schema change, taken before the first one runs.

    CLAUDE.md's rule is that a bulk write snapshots first (imports/commit.py
    has done this since it was written) — a migration is the same bet with
    worse odds, because it changes the SHAPE of the file and a half-applied
    one is not something a user can unpick by hand. `connect(migrate=True)`
    is where migrations actually run, on the TUI's, the CLI's, the web
    layer's and the MCP server's first connection alike, so the snapshot
    belongs here rather than in any one caller.

    Returns the backup path, or None when there is nothing to protect:

    - `:memory:` has no file to copy (every test connection);
    - nothing pending means no schema change is about to happen — so an
      ordinary open of an up-to-date book does NOT litter backups/;
    - schema_version 0 is a database with no schema yet. 001_initial on an
      empty file cannot destroy data that does not exist, and snapshotting
      it would put an empty .bak beside every freshly created book.
    """
    if str(db_path) == ":memory:":
        return None
    if not pending_migrations(conn):
        return None
    if schema_version(conn) == 0:
        return None
    return snapshot(conn, db_path)


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
    check of the copy.

    A copy that FAILS the check is deleted before the error is raised. It used
    to be left where it landed, under an ordinary timestamped name, which is
    the worst of both outcomes: the caller is told the backup failed, and the
    person who comes looking for a rollback weeks later finds a file that
    looks exactly like a good one. Nothing distinguishes a torn VACUUM from a
    finished one at the filesystem level, so the only honest state is absence
    — and the caller already knows, because this raises.

    REAL CORRUPTION RAISES; IT DOES NOT RETURN FALSE. `PRAGMA integrity_check`
    on a torn copy comes back as `sqlite3.DatabaseError: database disk image
    is malformed`, so a cleanup written as `if not ok:` alone is never reached
    on the failure it exists for — the exception propagates straight past it
    and a 216-byte file stays on disk under an ordinary backup name
    (2026-08-18). Every path out of here that is not a finished, verified copy
    now removes `dest` first, because this is the rollback for every migration
    and every `seed --force`.

    The two stages are caught separately so the VACUUM's own error keeps its
    type: a backups directory that is not writable must still surface as the
    `sqlite3.Error` the caller (and `test_a_failed_snapshot_aborts_the_migration`)
    expects, not as a RuntimeError about an integrity check that never ran."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"refusing to overwrite existing backup {dest}")
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
        os.chmod(dest, 0o600)
    except BaseException:
        # a VACUUM that raised part-way still leaves whatever it managed to
        # write; dest did not exist a moment ago, so removing it is safe
        dest.unlink(missing_ok=True)
        raise
    try:
        check = sqlite3.connect(dest)
        try:
            ok = integrity_check(check)
        finally:
            # closed before any unlink, so Windows and a locked file cannot
            # turn a failed backup into a failed cleanup on top of it
            check.close()
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup {dest} failed integrity check: {exc}") from exc
    if not ok:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup {dest} failed integrity check")
    return dest
