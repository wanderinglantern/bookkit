"""Batch revert rules — collapse, conflict detection, and the revert itself."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import base, contacts, orgs
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


# -- review findings: the dead-or-alive seam ----------------------------------


def test_reverting_an_update_on_a_since_deleted_entity_refuses_not_crashes(conn):
    """The plan compared against the raw row while base.update applies through
    the alive() filter — a batch-touched org soft-deleted since made a 'clean'
    revert raise KeyError mid-transaction. It must refuse and say why."""
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
    base.soft_delete(conn, "org", org.id)          # user deletes it afterwards

    result = batches_svc.revert(conn, made.ref, now=NOW)
    assert not result.applied
    assert result.refused, "a dead target is a conflict, not a crash"
    assert batches_repo.get_by_ref(conn, made.ref).reverted_at is None


def test_update_then_delete_in_one_batch_reverts_cleanly(conn):
    """Undeletes apply before field restores, so a batch that edited and then
    soft-deleted the same row reverts without tripping the alive() filter."""
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.update(conn, "org", org.id, {"website": "b"})
        base.soft_delete(conn, "org", org.id)

    result = batches_svc.revert(conn, made.ref, now=NOW)
    assert result.applied
    got = orgs.get(conn, org.id)                   # alive again
    assert got.website == "a"


def test_user_edits_to_a_batch_created_row_block_its_revert(conn):
    """The contract says 'changed since → refused'. A row the batch created
    and the user then edited must not vanish silently under a zero-conflict
    revert; force is the explicit override."""
    made = _batch(conn)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        org = orgs.create(conn, kind="client", name="Acme")
    base.update(conn, "org", org.id, {"website": "https://grant-typed.example"})

    result = batches_svc.revert(conn, made.ref, now=NOW)
    assert not result.applied
    assert result.refused
    assert orgs.get(conn, org.id).website == "https://grant-typed.example"

    # force's contract is "revert the clean rest, skip the conflicted" — it
    # never destroys post-batch user work. The edited row SURVIVES force,
    # reported as refused; removing it is what the delete flows are for.
    # Here there IS no clean rest, so nothing moves: applied means "the book
    # moved", not "the call ran" (changed 2026-08-15). Reporting success for
    # a no-op also used to mark the batch reverted, which burned it — the
    # user could never put it back even after undoing their own edit.
    forced = batches_svc.revert(conn, made.ref, now=NOW, force=True)
    assert not forced.applied
    assert batches_repo.get_by_ref(conn, made.ref).reverted_at is None
    assert orgs.get(conn, org.id).website == "https://grant-typed.example"
    assert {(c.change.entity_id, c.change.field) for c in forced.refused} == {
        (org.id, "created")
    }


def test_force_does_not_apply_or_double_report_a_conflicted_delete(conn):
    """A batch-deleted row the user already restored: force must leave it
    alone (no spurious undelete event) and report it ONLY as refused."""
    org = orgs.create(conn, kind="client", name="Acme", website="a")
    other = orgs.create(conn, kind="client", name="Beta", website="x")
    made = _batch(conn, org_id=org.id)
    with db.transaction(conn, batch=db.BatchState(batch_id=made.id)):
        base.soft_delete(conn, "org", org.id)
        base.update(conn, "org", other.id, {"website": "y"})
    base.undelete(conn, "org", org.id)             # user brings it back

    before = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
    result = batches_svc.revert(conn, made.ref, now=NOW, force=True)
    assert result.applied
    reverted_ids = {(c.entity_id, c.field) for c in result.reverted}
    refused_ids = {(c.change.entity_id, c.change.field) for c in result.refused}
    assert (org.id, "deleted_at") in refused_ids
    assert (org.id, "deleted_at") not in reverted_ids
    assert reverted_ids & refused_ids == set()
    # no spurious deleted_at None→None event for the refused delete
    spurious = conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE entity_id = ?"
        " AND field = 'deleted_at' AND rowid > ?", (org.id, before)
    ).fetchone()[0]
    assert spurious == 0
    assert orgs.get(conn, other.id).website == "x"  # the clean change reverted


# --- Task 15a: joining consecutive edits to one record into one undo unit ---


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, start: str):
    """Pins db.utc_now() to `start` and returns a setter to move it forward.

    Both repo.batches.create() (the created_at stamp) and open_batch's join
    window read db.utc_now() via the module attribute rather than an imported
    name, so patching it here — once — controls both."""
    current = {"t": start}
    monkeypatch.setattr(db, "utc_now", lambda: current["t"])

    def _set(t: str) -> None:
        current["t"] = t

    return _set


def _contact(conn, org_id, role="CFO", title="Finance"):
    return contacts.create(
        conn, org_id=org_id, first_name="Ann", last_name="Lee", role=role, title=title
    )


def test_consecutive_edits_to_one_record_join_a_single_batch(conn):
    """Correcting four cells on a contact is one edit run, and should revert as
    one thing rather than four."""
    org = orgs.create(conn, kind="client", name="Acme")
    person = _contact(conn, org.id)

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=person.id,
    ) as first:
        base.update(conn, "contact", person.id, {"role": "COO"})

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=person.id,
    ) as second:
        base.update(conn, "contact", person.id, {"title": "Ops"})

    assert first.id == second.id

    result = batches_svc.revert(conn, second.ref, now=NOW)
    assert result.applied
    restored = contacts.get(conn, person.id)
    assert restored.role == "CFO"
    assert restored.title == "Finance"


def test_edits_to_different_records_do_not_join(conn):
    """Same tool, same account, different contact — two actions, two batches.
    Merging them would let one revert undo work on a record the user never
    touched."""
    org = orgs.create(conn, kind="client", name="Acme")
    ann = _contact(conn, org.id, role="CFO")
    bo = contacts.create(conn, org_id=org.id, first_name="Bo", last_name="Diaz", role="COO")

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=ann.id,
    ) as first:
        base.update(conn, "contact", ann.id, {"role": "CEO"})

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=bo.id,
    ) as second:
        base.update(conn, "contact", bo.id, {"role": "CFO"})

    assert first.id != second.id

    result = batches_svc.revert(conn, first.ref, now=NOW)
    assert result.applied
    assert contacts.get(conn, ann.id).role == "CFO"
    assert contacts.get(conn, bo.id).role == "CFO"  # bo's own edit untouched


def test_a_batch_older_than_the_window_does_not_join(conn, monkeypatch):
    """Come back after lunch and finish the row: that is a new action."""
    org = orgs.create(conn, kind="client", name="Acme")
    person = _contact(conn, org.id)
    set_now = _freeze_clock(monkeypatch, "2026-08-14T09:00:00+00:00")

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=person.id,
    ) as first:
        base.update(conn, "contact", person.id, {"role": "COO"})

    set_now("2026-08-14T09:01:01+00:00")  # 61s later, past JOIN_WINDOW_SECONDS

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=person.id,
    ) as second:
        base.update(conn, "contact", person.id, {"title": "Ops"})

    assert first.id != second.id


def test_a_reverted_batch_is_never_joined(conn):
    """Joining a reverted batch would resurrect it."""
    org = orgs.create(conn, kind="client", name="Acme")
    person = _contact(conn, org.id)

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=person.id,
    ) as first:
        base.update(conn, "contact", person.id, {"role": "COO"})

    batches_svc.revert(conn, first.ref, now=NOW)

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=person.id,
    ) as second:
        base.update(conn, "contact", person.id, {"title": "Ops"})

    assert first.id != second.id
    assert batches_repo.get(conn, first.id).reverted_at is not None


def test_omitting_entity_id_keeps_todays_behaviour(conn):
    """Every existing caller passes no entity_id and must be unaffected."""
    org = orgs.create(conn, kind="client", name="Acme")
    person = _contact(conn, org.id)

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id,
    ) as first:
        base.update(conn, "contact", person.id, {"role": "COO"})

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id,
    ) as second:
        base.update(conn, "contact", person.id, {"title": "Ops"})

    assert first.id != second.id


def test_a_hop_to_another_record_and_back_does_not_rejoin(conn):
    """A -> B -> A within the window is three separate actions, not two joined
    plus a resumption of the first. most_recent() looks at the single latest
    batch, period — an earlier batch on A is not reconsidered once it is no
    longer the most recent row, even though it is still well inside the
    window and still targets only A."""
    org = orgs.create(conn, kind="client", name="Acme")
    ann = _contact(conn, org.id, role="CFO")
    bo = contacts.create(conn, org_id=org.id, first_name="Bo", last_name="Diaz", role="COO")

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=ann.id,
    ) as first:
        base.update(conn, "contact", ann.id, {"role": "CEO"})

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=bo.id,
    ) as second:
        base.update(conn, "contact", bo.id, {"role": "CFO"})

    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact", summary="edit contact — Acme",
        org_id=org.id, entity_id=ann.id,
    ) as third:
        base.update(conn, "contact", ann.id, {"title": "Ops"})

    assert len({first.id, second.id, third.id}) == 3


def test_two_threads_editing_different_records_never_share_a_batch(db_path, monkeypatch):
    """The join decision is read outside the lock, so two threads can see the
    same candidate. An empty candidate used to be joinable by either of them —
    and reverting the shared batch undid an edit the user never made.

    A barrier around repo.batches.events_for (which _targets_only calls to
    read the candidate's events) forces both threads to make their join
    decision at the same instant, reproducing the race deterministically
    instead of hoping the OS scheduler interleaves them.

    EACH THREAD OPENS ITS OWN CONNECTION, on the strict check_same_thread
    default, because that is now what the web surface does (Task 19,
    web.app.ThreadConnections). It used to share one, mirroring the app of
    the day, and failed 1 run in 25 — not as a test artifact but because
    sharing a sqlite3.Connection across threads corrupts reads: the setup
    died (an all-NULL EventBatch row out of most_recent, or a row that
    vanished and stranded a thread at the barrier) while the invariant under
    test never once broke across 350+ reproductions. Modelling a hazard the
    app no longer has would keep costing runs and prove nothing.

    THE BARRIER STAYS — it is what makes the TOCTOU window real, and dropping
    it would leave a test that passes for the wrong reason. This variant ran
    25/25 clean, and it still has teeth: relaxing `_targets_only` back to the
    subset test its docstring warns against fails it 8 times out of 8."""
    connection = db.connect(db_path)
    try:
        org = orgs.create(connection, kind="client", name="Acme")
        ann = contacts.create(
            connection, org_id=org.id, first_name="Ann", last_name="Lee", role="CFO"
        )
        bo = contacts.create(
            connection, org_id=org.id, first_name="Bo", last_name="Diaz", role="COO"
        )

        # The precondition the race needs: an EMPTY candidate batch, same
        # source/tool/org as both threads will use. base.update logs nothing
        # on a no-op write, so a real open_batch call can leave one behind —
        # this seeds that directly rather than relying on a coincidence.
        empty = batches_repo.create(
            connection, batch_id=batches_repo.new_batch_id(), source="web",
            tool="edit_contact", summary="edit contact — Acme", org_id=org.id,
        )

        barrier = threading.Barrier(2)
        original_events_for = batches_repo.events_for

        def _synced_events_for(conn, batch_id):
            barrier.wait(timeout=5)
            return original_events_for(conn, batch_id)

        monkeypatch.setattr(batches_repo, "events_for", _synced_events_for)

        batch_ids: dict[str, str] = {}
        errors: list[BaseException] = []

        def _edit(entity, new_role, key):
            # Opened, used and closed inside this thread, so the strict
            # check_same_thread default holds — the arrangement db.connect's
            # docstring describes and the one the web surface now keeps.
            own = db.connect(db_path, migrate=False)
            try:
                with batches_svc.open_batch(
                    own, source="web", tool="edit_contact",
                    summary="edit contact — Acme", org_id=org.id, entity_id=entity.id,
                ) as batch:
                    base.update(own, "contact", entity.id, {"role": new_role})
                batch_ids[key] = batch.id
            except BaseException as exc:  # surfaced on the main thread below
                errors.append(exc)
            finally:
                own.close()

        t_x = threading.Thread(target=_edit, args=(ann, "CEO", "x"))
        t_y = threading.Thread(target=_edit, args=(bo, "CFO", "y"))
        t_x.start()
        t_y.start()
        t_x.join(timeout=5)
        t_y.join(timeout=5)

        assert not errors, errors
        assert batch_ids["x"] != batch_ids["y"], (
            "two different entities landed in the same batch "
            f"({empty.ref}) — reverting it would undo the other thread's edit too"
        )
    finally:
        connection.close()
