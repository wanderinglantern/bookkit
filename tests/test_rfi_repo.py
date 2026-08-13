from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import base


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


def test_items_order_by_category_then_creation(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "safety manual", category="Safety")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    rfi.add_item(conn, req.id, "tax return", category="Financials")
    rfi.add_item(conn, req.id, "anything else")  # uncategorised sorts last
    prompts = [i.prompt for i in rfi.items_for_request(conn, req.id)]
    assert prompts == [
        "audited financials", "tax return", "safety manual", "anything else",
    ]


def test_items_within_a_category_keep_paste_order(conn) -> None:
    """Ten items added back-to-back share a second-precision created_at, so
    only the tie-break orders them. This is the paste-a-litany case."""
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "onboarding docs", "2026-08-05")
    expected = [f"question {n}" for n in range(10)]
    for prompt in expected:
        rfi.add_item(conn, req.id, prompt, category="Financials")
    assert [i.prompt for i in rfi.items_for_request(conn, req.id)] == expected


def test_item_defaults_are_question_and_outstanding(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "q", "2026-08-05")
    item = rfi.add_item(conn, req.id, "how many vehicles?")
    assert item.kind == "question"
    assert item.status == "outstanding"
    assert item.received_on is None


def test_update_item_records_a_response(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "q", "2026-08-05")
    item = rfi.add_item(conn, req.id, "how many vehicles?")
    rfi.update_item(
        conn, item.id, response="42, all owned", received_on="2026-08-12",
        status="received",
    )
    got = rfi.get_item(conn, item.id)
    assert got.response == "42, all owned"
    assert got.received_on == "2026-08-12"
    assert got.status == "received"


def test_deleted_items_disappear(conn) -> None:
    from bookkit.repo import rfi

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "q", "2026-08-05")
    item = rfi.add_item(conn, req.id, "gone")
    rfi.delete_item(conn, item.id)
    assert rfi.items_for_request(conn, req.id) == []


def test_merging_markets_moves_requests_to_the_survivor(conn) -> None:
    """A market merge soft-deletes the loser; a request still pointing at it
    would render a dead name and blow up the edit form. The merge repoints it."""
    from bookkit.repo import orgs, rfi
    from bookkit.services import merge

    client = _org(conn)
    dupe = orgs.create(conn, name="Axa XL", kind="market")
    real = orgs.create(conn, name="AXA XL", kind="market")
    req = rfi.create_request(
        conn, client, "property questions", "2026-08-05", market_org_id=dupe.id
    )
    merge.merge_markets(conn, dupe.id, real.id)
    assert rfi.get_request(conn, req.id).market_org_id == real.id
    moved = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE entity_id = ? AND field = 'market_org_id'",
        (req.id,),
    ).fetchone()[0]
    assert moved == 1, "the move is event-logged like every other write"


def test_outstanding_rows_drop_a_merged_away_market_name(conn) -> None:
    from bookkit.repo import orgs, rfi

    client = _org(conn)
    dupe = orgs.create(conn, name="Axa XL", kind="market")
    req = rfi.create_request(
        conn, client, "property questions", "2026-08-05",
        due_on="2026-08-19", market_org_id=dupe.id,
    )
    rfi.add_item(conn, req.id, "how many vehicles?")
    base.soft_delete(conn, "org", dupe.id)
    rows = rfi.outstanding_rows(conn, "2026-12-31")
    assert len(rows) == 1, "the request stays in the chase queue"
    assert rows[0]["market_name"] is None


def test_rfi_categories_vocabulary(conn) -> None:
    from bookkit.repo import rfi, vocab

    org_id = _org(conn)
    req = rfi.create_request(conn, org_id, "docs", "2026-08-05")
    rfi.add_item(conn, req.id, "a", category="Financials")
    rfi.add_item(conn, req.id, "b", category="Safety")
    rfi.add_item(conn, req.id, "c")
    assert vocab.rfi_categories(conn) == ["Financials", "Safety"]
