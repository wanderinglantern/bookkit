from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bookkit import connector, db


def test_command_is_the_console_script_beside_the_interpreter() -> None:
    """The Cowork panel's Command field must not be a bare name.

    `bookctl` at the repo root is an sh wrapper and install.sh never puts it
    on PATH, so a GUI app resolves nothing. The console script that
    [project.scripts] installs sits beside the running interpreter.
    """
    assert connector.fields().command == str(Path(sys.executable).parent / "bookctl")


def test_off_default_db_becomes_a_db_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DB found only via $BOOKKIT_DB must be pinned in Arguments.

    The connector is launched by a GUI app that inherits none of the shell's
    environment, so a path that resolves here would resolve to the empty
    default there — an assistant reading a book with nothing in it.
    """
    path = tmp_path / "real.db"
    monkeypatch.setenv("BOOKKIT_DB", str(path))

    got = connector.fields()

    assert got.arguments == ["--db", str(path), "mcp"]
    assert got.env == {}


def test_check_fails_when_the_database_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connector pointed at nothing must say so, not start and read empty."""
    monkeypatch.setenv("BOOKKIT_DB", str(tmp_path / "nope.db"))

    report = connector.check()

    assert report.ok is False
    database = next(c for c in report.checks if c.label == "database")
    assert database.ok is False
    assert "nope.db" in database.detail


def test_check_fails_when_the_schema_is_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unmigrated DB half-works: reads that touch a missing table raise
    inside a tool call, where the assistant sees a bare SQL error."""
    path = tmp_path / "old.db"
    db.connect(path, migrate=False).close()
    monkeypatch.setenv("BOOKKIT_DB", str(path))

    report = connector.check()

    schema = next(c for c in report.checks if c.label == "schema")
    assert schema.ok is False
    assert report.ok is False


def test_check_passes_on_a_migrated_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "good.db"
    db.connect(path).close()
    monkeypatch.setenv("BOOKKIT_DB", str(path))

    report = connector.check()

    assert report.ok is True, [c for c in report.checks if not c.ok]


def test_check_fails_when_the_console_script_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Command field is the one value the panel cannot validate for you."""
    path = tmp_path / "good.db"
    db.connect(path).close()
    monkeypatch.setenv("BOOKKIT_DB", str(path))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "python"))

    report = connector.check()

    command = next(c for c in report.checks if c.label == "command")
    assert command.ok is False
    assert report.ok is False


def test_check_confirms_startup_writes_nothing_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stdout is the MCP wire. One stray print and the connector dies on a
    protocol parse error, which the panel reports as a generic failure.
    """
    path = tmp_path / "good.db"
    db.connect(path).close()
    monkeypatch.setenv("BOOKKIT_DB", str(path))

    report = connector.check()

    stdout = next(c for c in report.checks if c.label == "stdout")
    assert stdout.ok is True


def test_check_identifies_which_database_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two books exist — the real one and this Mac's stale experiment. Size and
    last-modified are what tell them apart at a glance.
    """
    path = tmp_path / "good.db"
    db.connect(path).close()
    monkeypatch.setenv("BOOKKIT_DB", str(path))

    database = next(c for c in connector.check().checks if c.label == "database")

    assert str(path) in database.detail
    assert "modified" in database.detail


def test_check_never_migrates_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--check is a diagnostic and must leave the file alone.

    build_server creates and migrates its own connection (31bbda4), so an
    unguarded stdout probe would quietly upgrade the very database it was
    asked to report as behind.
    """
    path = tmp_path / "old.db"
    db.connect(path, migrate=False).close()
    monkeypatch.setenv("BOOKKIT_DB", str(path))

    connector.check()

    conn = db.connect(path, migrate=False)
    try:
        assert db.schema_version(conn) == 0
    finally:
        conn.close()
