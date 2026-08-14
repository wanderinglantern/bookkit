from __future__ import annotations

import sqlite3
import stat
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


def test_blast_cap_defaults_to_50(tmp_path):
    """50 is Grant's call (2026-08-13) — this pins that a refactor doesn't
    silently change how big an MCP write can get."""
    from bookkit import db

    assert db.BLAST_CAP == 50
    assert db.BatchState(batch_id="01B").cap == 50
