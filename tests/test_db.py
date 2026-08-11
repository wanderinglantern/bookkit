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
    connection.close()
    bad_dir = tmp_path / "migrations"
    bad_dir.mkdir()
    for entry in db.migrations_dir().iterdir():
        (bad_dir / entry.name).write_text(entry.read_text())
    (bad_dir / "002_bad.sql").write_text("CREATE TABLE will_fail (x); SYNTAX ERROR;")
    monkeypatch.setattr(db, "migrations_dir", lambda: bad_dir)
    connection = db.connect(db_path, migrate=False)
    with pytest.raises(sqlite3.Error):
        db.apply_migrations(connection)
    assert db.schema_version(connection) == 1
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
