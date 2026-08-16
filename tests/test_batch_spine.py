"""The batch spine: nested transactions, and TUI writes as revertible units.

These cover the machinery every TUI write now sits on. The behaviours asserted
here are the ones that were broken before it existed: a refused save leaving
half its rows behind, and a TUI write that `R` could not reach.
"""

from __future__ import annotations

import sqlite3

import pytest

from bookkit import db
from bookkit.repo import base, orgs
from bookkit.repo import batches as batches_repo
from bookkit.services import batches as batches_svc


def _client(conn: sqlite3.Connection, name: str = "Atomic Industries") -> str:
    return orgs.create(conn, kind="client", name=name, status="active").id


# --- nesting joins, it does not nest ---------------------------------------


def test_nested_transaction_joins_the_outer_one(conn: sqlite3.Connection) -> None:
    """SQLite has no nested BEGIN. An inner block must join, not raise."""
    org_id = _client(conn)
    with db.transaction(conn):
        base.update(conn, "org", org_id, {"owner": "outer"})
        with db.transaction(conn):  # would be "cannot start a transaction within"
            base.update(conn, "org", org_id, {"hq_city": "Chicago"})
    row = base.get(conn, "org", org_id)
    assert row is not None
    assert row["owner"] == "outer"
    assert row["hq_city"] == "Chicago"


def test_inner_failure_rolls_back_the_whole_outer_block(
    conn: sqlite3.Connection,
) -> None:
    """All-or-nothing has to span the join, or the joined block is a lie."""
    org_id = _client(conn)
    base.update(conn, "org", org_id, {"owner": "before"})
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            base.update(conn, "org", org_id, {"owner": "after"})
            with db.transaction(conn):
                base.update(conn, "org", org_id, {"hq_city": "Chicago"})
                raise RuntimeError("boom")
    row = base.get(conn, "org", org_id)
    assert row is not None
    assert row["owner"] == "before"
    assert row["hq_city"] is None


def test_inner_batch_is_ignored_so_the_outer_action_owns_the_undo_unit(
    conn: sqlite3.Connection,
) -> None:
    org_id = _client(conn)
    outer = batches_repo.new_batch_id()
    inner = batches_repo.new_batch_id()
    with db.transaction(conn, batch=db.BatchState(batch_id=outer)):
        with db.transaction(conn, batch=db.BatchState(batch_id=inner)):
            base.update(conn, "org", org_id, {"owner": "grant"})
    stamped = {
        row["batch_id"]
        for row in conn.execute(
            "SELECT batch_id FROM event_log WHERE entity_id = ? AND field = 'owner'",
            (org_id,),
        )
    }
    assert stamped == {outer}


# --- a TUI write is a revertible unit --------------------------------------


def test_tui_batch_is_listed_and_revertible(conn: sqlite3.Connection) -> None:
    org_id = _client(conn)
    with batches_svc.open_batch(
        conn, source="tui", tool="edit_account", summary="edited Atomic", org_id=org_id
    ) as batch:
        base.update(conn, "org", org_id, {"owner": "grant", "hq_city": "Chicago"})
        ref = batch.ref

    assert any(b.ref == ref for b in batches_repo.recent(conn, since="2000-01-01"))

    result = batches_svc.revert(conn, ref, now="2026-08-14T09:00:00+00:00")
    assert result.applied
    row = base.get(conn, "org", org_id)
    assert row is not None
    assert row["owner"] is None
    assert row["hq_city"] is None


def test_multi_field_write_reverts_as_one_unit(conn: sqlite3.Connection) -> None:
    """The failure behind the lost-deal bug: four fields written together, of
    which a field-granular undo could only ever put back the last."""
    org_id = _client(conn)
    base.update(conn, "org", org_id, {"owner": "grant", "status": "active"})
    with batches_svc.open_batch(
        conn, source="tui", tool="close_account", summary="closed Atomic"
    ) as batch:
        base.update(
            conn, "org", org_id,
            {"status": "dormant", "owner": "unassigned", "hq_city": "Denver"},
        )
        ref = batch.ref

    batches_svc.revert(conn, ref, now="2026-08-14T09:00:00+00:00")
    row = base.get(conn, "org", org_id)
    assert row is not None
    assert row["status"] == "active"
    assert row["owner"] == "grant"
    assert row["hq_city"] is None


def test_a_failed_write_leaves_nothing_behind(conn: sqlite3.Connection) -> None:
    org_id = _client(conn)
    with pytest.raises(RuntimeError):
        with batches_svc.open_batch(
            conn, source="tui", tool="edit_account", summary="edited Atomic"
        ):
            base.update(conn, "org", org_id, {"owner": "grant"})
            raise RuntimeError("refused")

    row = base.get(conn, "org", org_id)
    assert row is not None
    assert row["owner"] is None
    # and the batch row itself is gone, not left dangling in the changes list
    assert conn.execute("SELECT COUNT(*) FROM event_batch").fetchone()[0] == 0


# --- the TUI actually opens them -------------------------------------------


async def test_a_form_save_lands_as_one_revertible_batch(snapshot_db) -> None:
    """End-to-end: the proof that push_form's default batch engages. Before
    this, a TUI save wrote events with batch_id NULL and `R` could not see it."""
    from bookkit import db as db_mod
    from bookkit.tui.app import BookkitApp

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        before = app.conn.execute(
            "SELECT COUNT(*) FROM event_batch WHERE source = 'tui'"
        ).fetchone()[0]
        await pilot.press("ctrl+t")           # new task, a plain push_form
        await pilot.pause()
        for ch in "Call the broker back":
            await pilot.press(ch)
        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.pause()
        after = app.conn.execute(
            "SELECT id, ref, tool, summary FROM event_batch WHERE source = 'tui'"
        ).fetchall()
        assert len(after) == before + 1, "the save did not open a batch"
        batch_id = after[-1]["id"]
        stamped = app.conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE batch_id = ?", (batch_id,)
        ).fetchone()[0]
        assert stamped, "events were written outside the batch they belong to"
        assert db_mod is not None


# --- the undeclared-field guard --------------------------------------------


def test_an_undeclared_event_field_is_refused_at_write_time(
    conn: sqlite3.Connection,
) -> None:
    """The landmine this guard exists to defuse: an event naming something the
    table has no column for only fails later, inside `u`, on another record."""
    org_id = _client(conn)
    with pytest.raises(ValueError, match="NON_MUTATION_FIELDS"):
        base.log_event(conn, "org", org_id, "not_a_column", None, "x")


def test_declared_bookkeeping_fields_are_allowed(conn: sqlite3.Connection) -> None:
    """The three that were missing, and the two that were already there."""
    org_id = _client(conn)
    for field in ("created", "source", "import", "carrier_alias", "merged_from"):
        base.log_event(conn, "org", org_id, field, None, "x")
    logged = {
        row["field"]
        for row in conn.execute(
            "SELECT field FROM event_log WHERE entity_id = ?", (org_id,)
        )
    }
    assert {"import", "carrier_alias", "merged_from"} <= logged


def test_undo_after_an_import_finds_nothing_instead_of_crashing(
    conn: sqlite3.Connection,
) -> None:
    """`u` straight after an import used to raise IndexError, because 'import'
    has no column behind it.

    Two things fixed it, and both are asserted here: the field is declared
    bookkeeping so it is never written back, and `u` is batch-granular and
    scoped to the TUI, so an import — deliberately unbatched, because its DB
    snapshot is the rollback — is simply not something `u` reaches."""
    from bookkit.services import undo

    org_id = _client(conn)
    base.update(conn, "org", org_id, {"owner": "grant"})
    base.log_event(conn, "org", org_id, "import", None, "book.xlsx")

    assert undo.undo_last(conn) is None    # used to raise IndexError
    row = base.get(conn, "org", org_id)
    assert row is not None and row["owner"] == "grant"
