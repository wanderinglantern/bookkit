"""`u` undoes the last WRITER ACTION, and only ones this app wrote.

Before this, undo walked event_log field by field: it could revert a sync
projection the user never made, put back one of the four fields a stage move
wrote, and loop forever on a soft delete reporting success each time.
"""

from __future__ import annotations

import sqlite3

import pytest

from bookkit.repo import base, orgs
from bookkit.repo import batches as batches_repo
from bookkit.services import batches as batches_svc
from bookkit.services import undo

NOW = "2026-08-14T09:00:00+00:00"


def _client(conn: sqlite3.Connection, name: str = "Atomic Industries") -> str:
    return orgs.create(conn, kind="client", name=name, status="active").id


def _tui_write(
    conn: sqlite3.Connection, org_id: str, changes: dict[str, object], tool: str = "edit_account"
) -> str:
    with batches_svc.open_batch(
        conn, source="tui", tool=tool, summary=f"{tool} on Atomic", org_id=org_id
    ) as batch:
        base.update(conn, "org", org_id, changes)
        return batch.ref


# --- one writer action, one undo -------------------------------------------


def test_u_puts_back_every_field_the_action_wrote(conn: sqlite3.Connection) -> None:
    """The lost-deal shape: four fields written together. Field-granular undo
    could only ever restore the last one, leaving a dead record half alive."""
    org_id = _client(conn)
    base.update(conn, "org", org_id, {"status": "active", "owner": "grant"})
    _tui_write(conn, org_id, {"status": "lost", "owner": "unassigned", "hq_city": "Denver"})

    result = undo.undo_last(conn)

    assert result is not None and result.applied
    row = base.get(conn, "org", org_id)
    assert row is not None
    assert row["status"] == "active"
    assert row["owner"] == "grant"
    assert row["hq_city"] is None


def test_u_walks_backwards_through_actions_instead_of_pinning(
    conn: sqlite3.Connection,
) -> None:
    org_id = _client(conn)
    _tui_write(conn, org_id, {"owner": "first"})
    _tui_write(conn, org_id, {"owner": "second"})

    assert undo.undo_last(conn) is not None      # undoes 'second'
    row = base.get(conn, "org", org_id)
    assert row is not None and row["owner"] == "first"

    assert undo.undo_last(conn) is not None      # undoes 'first'
    row = base.get(conn, "org", org_id)
    assert row is not None and row["owner"] is None

    assert undo.undo_last(conn) is None          # and then stops


def test_u_on_a_soft_delete_restores_once_and_then_moves_on(
    conn: sqlite3.Connection,
) -> None:
    """The infinite loop: undelete re-logged a skipped note, so the original
    delete stayed newest forever and `u` reported success on every press."""
    org_id = _client(conn)
    with batches_svc.open_batch(
        conn, source="tui", tool="delete_account", summary="deleted Atomic"
    ):
        base.soft_delete(conn, "org", org_id)

    assert undo.undo_last(conn) is not None
    assert base.get(conn, "org", org_id) is not None      # alive again
    assert undo.undo_last(conn) is None                   # not the same one again


# --- scoped to what this app wrote -----------------------------------------


def test_u_never_reverts_an_assistant_write(conn: sqlite3.Connection) -> None:
    """`u` is the TUI's undo; R on the changes table handles MCP batches."""
    org_id = _client(conn)
    with batches_svc.open_batch(
        conn, source="mcp", tool="edit_field", summary="assistant set owner"
    ):
        base.update(conn, "org", org_id, {"owner": "assistant"})

    assert undo.undo_last(conn) is None
    row = base.get(conn, "org", org_id)
    assert row is not None and row["owner"] == "assistant"


def test_u_never_reverts_an_unbatched_machine_write(conn: sqlite3.Connection) -> None:
    """Sync projection and imports write outside any batch. Pressing `u` on a
    freshly opened app used to revert placement.synced_at."""
    org_id = _client(conn)
    base.update(conn, "org", org_id, {"owner": "projected"})   # no batch

    assert undo.undo_last(conn) is None
    row = base.get(conn, "org", org_id)
    assert row is not None and row["owner"] == "projected"


def test_u_skips_an_already_reverted_batch(conn: sqlite3.Connection) -> None:
    org_id = _client(conn)
    ref = _tui_write(conn, org_id, {"owner": "grant"})
    batches_svc.revert(conn, ref, now=NOW)

    assert undo.undo_last(conn) is None


# --- surfacing, not guessing ------------------------------------------------


def test_u_refuses_and_says_so_when_the_record_changed_since(
    conn: sqlite3.Connection,
) -> None:
    org_id = _client(conn)
    _tui_write(conn, org_id, {"owner": "grant"})
    base.update(conn, "org", org_id, {"owner": "someone else"})   # changed since

    result = undo.undo_last(conn)

    assert result is not None
    assert not result.applied
    assert result.refused
    assert "owner" in result.description
    row = base.get(conn, "org", org_id)
    assert row is not None and row["owner"] == "someone else"     # untouched


def test_the_toast_names_the_action_not_a_schema_path(
    conn: sqlite3.Connection,
) -> None:
    """It used to read "placement.status: 'quoted' → 'bound'" — a column path
    and a Python repr, with no clue which client it belonged to."""
    org_id = _client(conn)
    _tui_write(conn, org_id, {"owner": "grant"}, tool="edit_account")

    result = undo.undo_last(conn)

    assert result is not None
    assert "edit_account on Atomic" in result.description
    assert "org." not in result.description
    assert "'" not in result.description


# --- the force-revert bookkeeping bug ---------------------------------------


def test_a_force_revert_that_applies_nothing_does_not_burn_the_batch(
    conn: sqlite3.Connection,
) -> None:
    """force=True on a fully conflicted batch returned applied=True with an
    empty reverted list, then marked the batch reverted — so the user could
    never put it back even after undoing their own change."""
    org_id = _client(conn)
    ref = _tui_write(conn, org_id, {"owner": "grant"})
    base.update(conn, "org", org_id, {"owner": "someone else"})

    result = batches_svc.revert(conn, ref, now=NOW, force=True)

    assert not result.applied
    assert result.reverted == []
    assert batches_repo.get_by_ref(conn, ref).reverted_at is None


def test_a_force_revert_that_applies_something_still_marks_it(
    conn: sqlite3.Connection,
) -> None:
    org_id = _client(conn)
    with batches_svc.open_batch(
        conn, source="tui", tool="edit_account", summary="edit"
    ) as batch:
        base.update(conn, "org", org_id, {"owner": "grant", "hq_city": "Chicago"})
        ref = batch.ref
    base.update(conn, "org", org_id, {"owner": "someone else"})   # one conflict

    result = batches_svc.revert(conn, ref, now=NOW, force=True)

    assert result.applied
    assert result.refused
    row = base.get(conn, "org", org_id)
    assert row is not None
    assert row["hq_city"] is None                  # the clean one reverted
    assert row["owner"] == "someone else"          # the conflicted one did not
    assert batches_repo.get_by_ref(conn, ref).reverted_at == NOW


# --- an action that promises `u` must be reachable by it ---------------------


def test_todays_task_done_is_reachable_by_u_and_does_not_revert_something_else(
    conn: sqlite3.Connection,
) -> None:
    """Today's `d` called tasks_repo.complete with NO batch and then toasted
    "task done — u to undo". `undo_last` resolves the most recent BATCH, so `u`
    reached past it to whatever was batched before: a contact edit, then a task
    done, then `u` reported "undid edited Dana Reed", rolled that contact's
    title back, and left the task done (2026-08-18).

    The account screen's identical action was already wrapped in
    `_batched(tool="task_done")` and shows the same toast — a promise one
    surface keeps and the other does not.

    This pins the RULE. That Today's own keystroke takes the batched path is
    pinned separately, by pressing `d` on the real screen, in
    tests/test_tui.py::test_todays_d_is_undone_by_u_not_the_action_before_it —
    a green suite proves nothing broke, not that the new path is taken
    (CLAUDE.md)."""
    from bookkit.repo import contacts as contacts_repo
    from bookkit.repo import tasks as tasks_repo

    org_id = _client(conn)
    contact = contacts_repo.create(
        conn, org_id=org_id, first_name="Dana", last_name="Reed", title="Risk Manager"
    )
    task = tasks_repo.create(
        conn, title="Call the carrier", org_id=org_id, due_on="2026-08-20"
    )

    _tui_write(conn, org_id, {"name": "Atomic Industries, Inc."}, tool="edit_account")
    with batches_svc.open_batch(
        conn, source="tui", tool="contact_edit", summary="edited Dana Reed",
        org_id=org_id,
    ):
        base.update(conn, "contact", contact.id, {"title": "VP Risk"})

    # what Today's `d` does, batched the way the toast promises
    with batches_svc.open_batch(
        conn, source="tui", tool="task_done", summary="completed a task", org_id=org_id
    ):
        tasks_repo.complete(conn, task.id)

    result = undo.undo_last(conn, now=NOW)
    assert result is not None and result.applied
    assert "task" in result.summary, (
        f"`u` reached past the task and undid {result.summary!r} instead"
    )
    assert tasks_repo.get(conn, task.id).status != "done", "the task is still done"
    assert contacts_repo.get(conn, contact.id).title == "VP Risk", (
        "`u` silently rolled back an earlier, unrelated action"
    )


def test_setting_a_primary_contact_is_one_undo_unit(conn: sqlite3.Connection) -> None:
    """set_primary clears one contact's flag and sets another's. Unbatched, `u`
    could not reach it at all; batched but untransacted, a mid-loop failure on
    the autocommit connection left the org with ZERO primaries — a state no
    surface renders as wrong, the primary simply vanishes from every header."""
    from bookkit.repo import contacts as contacts_repo

    org_id = _client(conn)
    dana = contacts_repo.create(
        conn, org_id=org_id, first_name="Dana", last_name="Reed", is_primary=1
    )
    kim = contacts_repo.create(conn, org_id=org_id, first_name="Kim", last_name="Ito")

    with batches_svc.open_batch(
        conn, source="tui", tool="contact_set_primary",
        summary="set the primary contact", org_id=org_id,
    ):
        contacts_repo.set_primary(conn, kim.id)
    assert contacts_repo.get(conn, kim.id).is_primary
    assert not contacts_repo.get(conn, dana.id).is_primary

    result = undo.undo_last(conn, now=NOW)
    assert result is not None and result.applied
    # BOTH sides go back, or the org is left in a state neither contact was in
    assert contacts_repo.get(conn, dana.id).is_primary, "the old primary was not restored"
    assert not contacts_repo.get(conn, kim.id).is_primary


def test_set_primary_never_leaves_an_org_with_no_primary(
    conn: sqlite3.Connection, monkeypatch
) -> None:
    """The clear-then-set loop runs on an AUTOCOMMIT connection, so without a
    transaction each write landed on its own and a failure between the clear
    and the set left zero primaries. Reproduced by failing the second write —
    the exact moment the old primary is already cleared."""
    from bookkit.repo import contacts as contacts_repo

    org_id = _client(conn)
    dana = contacts_repo.create(
        conn, org_id=org_id, first_name="Dana", last_name="Reed", is_primary=1
    )
    kim = contacts_repo.create(conn, org_id=org_id, first_name="Kim", last_name="Ito")

    real_update, calls = base.update, {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:          # the clear landed; the set has not run
            raise RuntimeError("connection dropped mid-loop")
        return real_update(*args, **kwargs)

    monkeypatch.setattr(base, "update", flaky)
    with pytest.raises(RuntimeError):
        contacts_repo.set_primary(conn, kim.id)
    monkeypatch.undo()

    primaries = [
        c.id for c in contacts_repo.for_org(conn, org_id, active_only=False) if c.is_primary
    ]
    assert primaries == [dana.id], (
        f"a failed set_primary left the org with {len(primaries)} primary contacts"
    )
