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


def _org(conn) -> str:
    from bookkit.repo import orgs

    return orgs.create(conn, name="Endeavour Energy", kind="client").id


def test_create_request_allocates_a_ref(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "Sompo — property questions", "2026-08-05")
    assert req.ref.startswith("RFI-")
    assert req.title == "Sompo — property questions"
    assert req.requested_on == "2026-08-05"
    assert rfi.get_request(conn, req.id).id == req.id


def test_requests_for_org_excludes_deleted(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    keep = rfi.create_request(conn, org_id, "keep", "2026-08-05")
    drop = rfi.create_request(conn, org_id, "drop", "2026-08-05")
    rfi.delete_request(conn, drop.id)
    assert [r.id for r in rfi.requests_for_org(conn, org_id)] == [keep.id]


def test_update_request_is_event_logged(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "old", "2026-08-05")
    rfi.update_request(conn, req.id, title="new")
    assert rfi.get_request(conn, req.id).title == "new"
    events = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE entity_id = ?", (req.id,)
    ).fetchone()[0]
    assert events >= 2, "create and update must both land in the event log"


def test_get_request_raises_for_unknown(conn) -> None:
    from bookkit.repo import rfi

    with pytest.raises(KeyError):
        rfi.get_request(conn, "nope")
