from __future__ import annotations

import sqlite3
import stat
import time
from pathlib import Path

import pytest

from bookkit import db


def test_migrations_apply_to_empty_db(conn: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for expected in (
        "org", "market_profile", "appetite", "contact", "interaction",
        "interaction_contact", "task", "placement", "opportunity", "submission",
        "document", "event_log", "program_link", "proj_layer", "proj_participant",
        "proj_retention", "draft", "schema_version", "ref_counter",
    ):
        assert expected in tables


def test_migrations_are_idempotent(db_path: Path) -> None:
    first = db.connect(db_path)
    assert db.schema_version(first) >= 1
    first.close()
    again = db.connect(db_path)
    assert db.apply_migrations(again) == []
    again.close()


def test_migration_versions_recorded(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT version, applied_at FROM schema_version").fetchall()
    assert [r["version"] for r in rows] == list(range(1, len(rows) + 1))
    assert all(r["applied_at"] for r in rows)


def test_db_file_created_0600(db_path: Path) -> None:
    connection = db.connect(db_path)
    connection.close()
    mode = stat.S_IMODE(db_path.stat().st_mode)
    assert mode == 0o600


def test_failed_migration_rolls_back(db_path: Path, tmp_path: Path, monkeypatch) -> None:
    connection = db.connect(db_path)
    applied = db.schema_version(connection)
    connection.close()
    bad_dir = tmp_path / "migrations"
    bad_dir.mkdir()
    for entry in db.migrations_dir().iterdir():
        (bad_dir / entry.name).write_text(entry.read_text())
    (bad_dir / "099_bad.sql").write_text("CREATE TABLE will_fail (x); SYNTAX ERROR;")
    monkeypatch.setattr(db, "migrations_dir", lambda: bad_dir)
    connection = db.connect(db_path, migrate=False)
    with pytest.raises(sqlite3.Error):
        db.apply_migrations(connection)
    assert db.schema_version(connection) == applied
    tables = {
        r[0]
        for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "will_fail" not in tables
    connection.close()


def _migrations_plus(tmp_path: Path, name: str, sql: str) -> Path:
    """A copy of the real migrations directory with one more file in it."""
    bad_dir = tmp_path / "migrations"
    bad_dir.mkdir(exist_ok=True)
    for entry in db.migrations_dir().iterdir():
        (bad_dir / entry.name).write_text(entry.read_text())
    (bad_dir / name).write_text(sql)
    return bad_dir


def test_a_migration_may_not_take_its_own_transaction_control(
    db_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """A migration containing its own `COMMIT;` ends the runner's transaction
    early, so everything after it lands unconditionally. One that then FAILED
    left its tables committed, never advanced schema_version, and made the
    runner's own `ROLLBACK` raise "cannot rollback - no transaction is active"
    on the way out — so every later `connect()` died on "table already exists"
    and the book was unopenable on all four surfaces, permanently, with no
    message naming the cause (2026-08-18).

    Nothing can undo committed DDL, so the file is refused before the first
    statement of the first pending migration runs, and the database is left
    exactly as it was."""
    bad_dir = _migrations_plus(
        tmp_path, "099_commits_early.sql",
        "CREATE TABLE widget (id TEXT PRIMARY KEY);\n"
        "COMMIT;\n"
        "CREATE TABLE widget (id TEXT PRIMARY KEY);\n",
    )
    monkeypatch.setattr(db, "migrations_dir", lambda: bad_dir)

    with pytest.raises(db.MigrationRefused, match="transaction control"):
        db.connect(db_path)

    # and again — the refusal is stable, not a one-time wedge
    with pytest.raises(db.MigrationRefused):
        db.connect(db_path)

    raw = sqlite3.connect(db_path)
    try:
        left = raw.execute(
            "SELECT name FROM sqlite_master WHERE name = 'widget'"
        ).fetchone()
    finally:
        raw.close()
    assert left is None, "a refused migration committed a table anyway"


def test_the_trigger_bodies_in_the_real_migrations_are_not_transaction_control(
    tmp_path: Path,
) -> None:
    """001_initial.sql defines eight triggers, every one of them
    `CREATE TRIGGER ... BEGIN ... END;`. A keyword grep would refuse the whole
    schema; the check splits statements the way sqlite's own shell does, so a
    trigger's BEGIN is inside a CREATE, not a statement of its own."""
    for entry in sorted(db.migrations_dir().iterdir()):
        if entry.suffix == ".sql":
            db.check_migration(entry, entry.read_text(encoding="utf-8"))


class _CommitsThenFails:
    """A connection whose executescript ends the transaction and THEN fails —
    the exact state a migration with its own `COMMIT;` produced. Everything
    else delegates to the real connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def executescript(self, _script: str):
        self._conn.execute("BEGIN")
        self._conn.execute("CREATE TABLE half_applied (x)")
        self._conn.execute("COMMIT")
        raise sqlite3.OperationalError("table half_applied already exists")


def test_a_migration_error_is_not_replaced_by_the_rollback_that_follows_it(
    db_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """The runner rolled back UNCONDITIONALLY. With the transaction already
    gone, `ROLLBACK` raised out of the except block and REPLACED the real
    error with "cannot rollback - no transaction is active" — so the failure
    that actually broke the book never reached anyone, and what they got
    instead was a message about the cleanup (2026-08-18).

    `check_migration` now refuses the file that produces that state, so this
    is no longer reachable THROUGH a migration. The guard is asserted on the
    state itself rather than through a file, because a defence that only holds
    while a second defence holds is worth nothing on the day the first one is
    evaded — and it costs one `if`."""
    bad_dir = _migrations_plus(tmp_path, "099_pending.sql", "CREATE TABLE later (x);\n")
    monkeypatch.setattr(db, "migrations_dir", lambda: bad_dir)
    connection = db.connect(db_path, migrate=False)
    try:
        with pytest.raises(sqlite3.Error) as err:
            db.apply_migrations(_CommitsThenFails(connection))  # type: ignore[arg-type]
    finally:
        connection.close()
    assert "half_applied already exists" in str(err.value), (
        f"the real error was replaced by its own cleanup: {err.value}"
    )
    assert "cannot rollback" not in str(err.value)


def test_a_failing_migration_still_reports_its_own_error(
    db_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """The ordinary failing migration, where the transaction IS still open and
    the rollback is the right thing to do — the `if` must not have turned the
    rollback off."""
    bad_dir = _migrations_plus(
        tmp_path, "099_fails.sql",
        "CREATE TABLE will_fail (x);\nSELECT bogus_function_that_does_not_exist();\n",
    )
    monkeypatch.setattr(db, "migrations_dir", lambda: bad_dir)
    connection = db.connect(db_path, migrate=False)
    try:
        with pytest.raises(sqlite3.Error) as err:
            db.apply_migrations(connection)
        assert "bogus_function_that_does_not_exist" in str(err.value)
        # asserted on the LIVE connection, before it is closed: closing one
        # discards an open transaction anyway, so a check made afterwards
        # passes whether the rollback ran or not and pins nothing
        assert not connection.in_transaction, "the transaction was left open"
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'will_fail'"
        ).fetchone() is None, "the rollback did not happen — the guard disabled it"
    finally:
        connection.close()


def test_backup_vacuum_into(db_path: Path, tmp_path: Path) -> None:
    connection = db.connect(db_path)
    dest = tmp_path / "backups" / "book.db"
    db.backup(connection, dest)
    assert dest.exists()
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    copy = sqlite3.connect(dest)
    assert db.integrity_check(copy)
    copy.close()
    with pytest.raises(FileExistsError):
        db.backup(connection, dest)
    connection.close()


def test_pragmas(db_path: Path) -> None:
    connection = db.connect(db_path)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    connection.close()


def test_connect_readonly_refuses_writes(tmp_path):
    path = tmp_path / "ro.db"
    db.connect(path).close()  # create + migrate
    ro = db.connect_readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO setting (key, value) VALUES ('x', 'y')")
    ro.close()


# -- batch stamping (MCP undo units) -----------------------------------------


def test_events_inside_a_batch_share_its_id(tmp_path):
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    state = db.BatchState(batch_id="01BATCH", cap=99)
    with db.transaction(conn, batch=state):
        orgs.create(conn, kind="client", name="Acme")
    rows = conn.execute(
        "SELECT DISTINCT batch_id FROM event_log WHERE batch_id IS NOT NULL"
    ).fetchall()
    assert [r[0] for r in rows] == ["01BATCH"]


def test_events_outside_a_batch_are_unstamped(tmp_path):
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    orgs.create(conn, kind="client", name="Acme")          # no transaction
    with db.transaction(conn):                              # transaction, no batch
        orgs.create(conn, kind="client", name="Beta")
    rows = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE batch_id IS NOT NULL"
    ).fetchone()
    assert rows[0] == 0


def test_batch_context_is_cleared_after_the_block(tmp_path):
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    with db.transaction(conn, batch=db.BatchState(batch_id="01B", cap=99)):
        orgs.create(conn, kind="client", name="Acme")
    assert db.current_batch() is None
    orgs.create(conn, kind="client", name="Beta")
    in_batch = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE batch_id = '01B'"
    ).fetchone()[0]
    stamped = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE batch_id IS NOT NULL"
    ).fetchone()[0]
    assert in_batch == stamped     # nothing leaked into the batch afterwards


@pytest.mark.asyncio
async def test_concurrent_batches_do_not_bleed(tmp_path):
    """The failure that would be silent and severe: MCP tools run under async
    wrappers, so two batches can be in flight in one process. A ContextVar
    keeps them apart; a module global would not."""
    import asyncio

    from bookkit import db

    seen: dict[str, str | None] = {}

    async def run(name: str) -> None:
        token = db._current_batch.set(db.BatchState(batch_id=name, cap=99))
        try:
            await asyncio.sleep(0)          # force interleaving
            state = db.current_batch()
            seen[name] = None if state is None else state.batch_id
        finally:
            db._current_batch.reset(token)

    await asyncio.gather(run("01AAA"), run("01BBB"))
    assert seen == {"01AAA": "01AAA", "01BBB": "01BBB"}
    assert db.current_batch() is None


def test_batch_context_is_cleared_when_the_block_raises(tmp_path):
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    with pytest.raises(RuntimeError):
        with db.transaction(conn, batch=db.BatchState(batch_id="01B", cap=99)):
            orgs.create(conn, kind="client", name="Acme")
            raise RuntimeError("boom")
    assert db.current_batch() is None
    assert conn.execute("SELECT COUNT(*) FROM org").fetchone()[0] == 0


def test_blast_cap_rolls_the_whole_batch_back(tmp_path):
    """A cap that raises AFTER writing is worse than no cap — assert the
    database is untouched, not merely that an error came out."""
    from bookkit import db
    from bookkit.repo import orgs

    conn = db.connect(tmp_path / "b.db")
    with pytest.raises(db.BlastRadiusExceeded):
        with db.transaction(conn, batch=db.BatchState(batch_id="01B", cap=3)):
            for n in range(5):
                orgs.create(conn, kind="client", name=f"Client {n}")

    assert conn.execute("SELECT COUNT(*) FROM org").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 0


def test_blast_cap_counts_entities_not_events(tmp_path):
    """Three field writes on ONE row is 1 against the cap, not 3."""
    from bookkit import db
    from bookkit.repo import base, orgs

    conn = db.connect(tmp_path / "b.db")
    org = orgs.create(conn, kind="client", name="Acme")
    with db.transaction(conn, batch=db.BatchState(batch_id="01B", cap=1)):
        base.update(conn, "org", org.id, {"website": "https://a.example"})
        base.update(conn, "org", org.id, {"legal_name": "Acme Ltd"})
        base.update(conn, "org", org.id, {"domain": "a.example"})

    assert orgs.get(conn, org.id).domain == "a.example"


def test_blast_cap_defaults_to_250(tmp_path):
    """250 is Grant's call (2026-08-14) — this pins that a refactor doesn't
    silently change how big an MCP write can get."""
    from bookkit import db

    assert db.BLAST_CAP == 250
    assert db.BatchState(batch_id="01B").cap == 250


def test_concurrent_transactions_do_not_collide(db_path):
    """_tx_depth is a ContextVar, so a second THREAD on the same connection
    sees depth 0 and would issue its own BEGIN IMMEDIATE. Unreachable until the
    web layer started running handlers in uvicorn's threadpool."""
    import threading
    import time

    from bookkit import db

    conn = db.connect(db_path, check_same_thread=False)
    errors: list[str] = []

    def writer(hold: float) -> None:
        try:
            with db.transaction(conn):
                time.sleep(hold)
        except Exception as exc:  # noqa: BLE001 — the assertion is the point
            errors.append(f"{type(exc).__name__}: {exc}")

    first = threading.Thread(target=writer, args=(0.25,))
    second = threading.Thread(target=writer, args=(0.0,))
    first.start()
    time.sleep(0.05)
    second.start()
    first.join()
    second.join()

    assert not errors, errors


def test_connect_sets_a_busy_timeout(tmp_path):
    """F2, as REDUCED: this value is also Python's sqlite3 default, so the
    guarantee already held — what was missing is anything pinning it. The TUI
    and the MCP server both hold read-write connections to the same file, so a
    driver swap or a stray timeout= silently reintroduces 'database is locked'."""
    from bookkit import db

    path = tmp_path / "timeout.db"
    conn = db.connect(path)
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS
        assert db.BUSY_TIMEOUT_MS >= 5000
    finally:
        conn.close()
    ro = db.connect_readonly(path)
    try:
        assert ro.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS
    finally:
        ro.close()


def test_a_second_writer_waits_for_the_lock_instead_of_failing(tmp_path):
    """F2, characterisation: while one connection holds the write lock, a
    second write blocks and then succeeds rather than raising. This passed
    before BUSY_TIMEOUT_MS existed — which is how the original P0 finding was
    disproved — and it is here so the guarantee stops being accidental."""
    import threading

    from bookkit import db
    from bookkit.repo import orgs

    path = tmp_path / "contended.db"
    db.connect(path).close()  # migrate once, up front
    writer = db.connect(path)
    holding = threading.Event()

    def hold_the_write_lock() -> None:
        # the connection must be CREATED in this thread: sqlite3 objects are
        # thread-bound, and a holder that dies on import leaves no lock at all
        # (which would make this test pass for the wrong reason)
        holder = db.connect(path, migrate=False)
        try:
            with db.transaction(holder):
                holding.set()
                time.sleep(0.4)
        finally:
            holder.close()

    thread = threading.Thread(target=hold_the_write_lock)
    thread.start()
    try:
        assert holding.wait(timeout=2), "holder never took the write lock"
        started = time.monotonic()
        org = orgs.create(writer, kind="client", name="Waited For Lock")
        waited = time.monotonic() - started
        assert orgs.get(writer, org.id).name == "Waited For Lock"
        assert waited > 0.1, f"write did not actually contend (took {waited:.3f}s)"
    finally:
        thread.join()
        writer.close()
