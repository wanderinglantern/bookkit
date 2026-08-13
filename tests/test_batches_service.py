"""Batch revert rules — collapse, conflict detection, and the revert itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import base, orgs
from bookkit.repo import batches as batches_repo
from bookkit.services import batches as batches_svc


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "batches.db")
    yield connection
    connection.close()


def _batch(conn, tool="enrich_field", org_id=None):
    return batches_repo.create(
        conn, batch_id=batches_repo.new_batch_id(), source="mcp", tool=tool,
        summary="a test batch", org_id=org_id,
    )


def test_plan_collapses_a_field_written_twice_to_one_net_change(conn):
    """The batch set website a -> b -> c. Reverting must restore a, once, and
    must NOT read the superseded b as a conflict."""
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
        base.update(conn, "org", org.id, {"website": "c"})

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert len(plan.updates) == 1
    assert plan.updates[0].old_value == "a"
    assert plan.updates[0].new_value == "c"


def test_plan_flags_a_field_changed_since_as_a_conflict(conn):
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
    base.update(conn, "org", org.id, {"website": "grant-typed-this"})

    plan = batches_svc.plan_revert(conn, made)
    assert not plan.clean
    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].current_value == "grant-typed-this"
    assert plan.conflicts[0].change.field == "website"


def test_plan_treats_a_created_row_as_a_soft_delete(conn):
    made = _batch(conn)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        org = orgs.create(conn, kind="client", name="Acme")

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert [c.entity_id for c in plan.creates] == [org.id]


def test_a_created_row_dominates_its_own_field_edits(conn):
    """Created THEN edited in the same batch: revert soft-deletes the row and
    must not conflict-check fields on a row it is about to delete."""
    made = _batch(conn)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        org = orgs.create(conn, kind="client", name="Acme")
        base.update(conn, "org", org.id, {"website": "b"})

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert [c.entity_id for c in plan.creates] == [org.id]
    assert plan.updates == []


def test_plan_ignores_source_provenance_events(conn):
    org = orgs.create(conn, kind="client", name="Acme")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.log_event(conn, "org", org.id, "source", None, "mcp")

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert plan.updates == [] and plan.creates == [] and plan.deletes == []


def test_plan_reverts_a_soft_delete_by_undeleting(conn):
    org = orgs.create(conn, kind="client", name="Acme")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.soft_delete(conn, "org", org.id)

    plan = batches_svc.plan_revert(conn, made)
    assert plan.clean
    assert [c.entity_id for c in plan.deletes] == [org.id]


# -- applying the revert ------------------------------------------------------

NOW = "2026-08-13T18:00:00Z"


def test_revert_restores_field_values_and_stamps_the_batch(conn):
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})

    result = batches_svc.revert(conn, made.ref, now=NOW)
    assert result.applied
    assert orgs.get(conn, org.id).website == "a"
    assert batches_repo.get_by_ref(conn, made.ref).reverted_at == NOW


def test_revert_soft_deletes_what_the_batch_created(conn):
    made = _batch(conn)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        org = orgs.create(conn, kind="client", name="Acme")

    batches_svc.revert(conn, made.ref, now=NOW)
    with pytest.raises(KeyError):
        orgs.get(conn, org.id)


def test_revert_undeletes_what_the_batch_deleted(conn):
    org = orgs.create(conn, kind="client", name="Acme")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.soft_delete(conn, "org", org.id)

    batches_svc.revert(conn, made.ref, now=NOW)
    assert orgs.get(conn, org.id).name == "Acme"


def test_a_conflicted_revert_writes_absolutely_nothing(conn):
    """All-or-nothing: assert the DB is untouched, not merely that it refused."""
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
        base.update(conn, "org", org.id, {"legal_name": "Acme Ltd"})
    base.update(conn, "org", org.id, {"website": "grant-typed-this"})

    result = batches_svc.revert(conn, made.ref, now=NOW)
    assert not result.applied
    assert len(result.refused) == 1
    got = orgs.get(conn, org.id)
    assert got.website == "grant-typed-this"      # untouched
    assert got.legal_name == "Acme Ltd"           # the clean one NOT reverted
    assert batches_repo.get_by_ref(conn, made.ref).reverted_at is None


def test_force_reverts_the_clean_changes_and_reports_the_rest(conn):
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
        base.update(conn, "org", org.id, {"legal_name": "Acme Ltd"})
    base.update(conn, "org", org.id, {"website": "grant-typed-this"})

    result = batches_svc.revert(conn, made.ref, now=NOW, force=True)
    assert result.applied
    assert len(result.reverted) == 1 and len(result.refused) == 1
    got = orgs.get(conn, org.id)
    assert got.website == "grant-typed-this"      # conflicted, left alone
    assert got.legal_name is None                 # clean, reverted


def test_a_second_revert_is_refused(conn):
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})

    batches_svc.revert(conn, made.ref, now=NOW)
    with pytest.raises(batches_svc.AlreadyReverted):
        batches_svc.revert(conn, made.ref, now=NOW)


def test_revert_is_not_itself_undoable_or_batched(conn):
    """The revert's own writes carry note='revert' (or base.undelete's own
    'undelete') and no batch_id, so u skips them and a revert cannot be
    batch-reverted."""
    from bookkit.repo import events

    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})

    batches_svc.revert(conn, made.ref, now=NOW)
    rows = conn.execute(
        "SELECT batch_id FROM event_log WHERE note IN ('revert', 'undelete')"
    ).fetchall()
    assert rows and all(r[0] is None for r in rows)
    last = events.last_mutation(conn)
    assert last is None or last.note not in ("revert", "undelete")


def test_revert_raises_on_an_unknown_ref(conn):
    with pytest.raises(KeyError):
        batches_svc.revert(conn, "MCP-9999", now=NOW)
