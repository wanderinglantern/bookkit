from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "rfi.db")
    yield connection
    connection.close()


def test_migration_creates_rfi_tables(conn) -> None:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"rfi_request", "rfi_item"} <= tables


def test_migration_is_idempotent(conn) -> None:
    assert db.pending_migrations(conn) == []


def test_request_scope_is_exclusive(conn) -> None:
    """A request points at a placement OR a project, never both.

    FK enforcement is off for this one insert on purpose: db.connect sets
    PRAGMA foreign_keys=ON, so the fake org/placement/project ids below would
    raise IntegrityError on the FOREIGN KEY before SQLite ever evaluated the
    CHECK — and the test would still pass with the CHECK deleted. Scoping it
    to the CHECK is the whole point of the test."""
    import sqlite3

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO rfi_request "
                "(id, ref, org_id, placement_id, project_id, title, requested_on,"
                " created_at, updated_at) "
                "VALUES ('x','RFI-9999','o','p','pr','t','2026-08-13','n','n')"
            )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def test_models_expose_rfi_vocabularies() -> None:
    from bookkit.models import RFI_ITEM_KINDS, RFI_ITEM_STATUSES

    assert RFI_ITEM_STATUSES == ("outstanding", "received", "waived")
    assert RFI_ITEM_KINDS == ("question", "document")
