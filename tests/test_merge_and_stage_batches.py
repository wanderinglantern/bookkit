"""Merges and pipeline stage moves as one atomic, revertible unit.

The last two unbatched TUI writes. Both were flagged by the usability audit:
a merge ran 4-10 writes on the AUTOCOMMIT connection with no transaction, so
anything failing mid-way left submissions on the target and the source still
live; and closing a deal wrote four fields that undo could only ever put one
of back, leaving a lost deal sitting at its old probability.
"""

from __future__ import annotations

import sqlite3

import pytest

from bookkit import db
from bookkit.repo import base, opportunities, orgs, placements
from bookkit.repo import batches as batches_repo
from bookkit.repo import tasks as tasks_repo
from bookkit.services import batches as batches_svc
from bookkit.services import merge, pipeline, undo

NOW = "2026-08-14T09:00:00+00:00"


def _two_placements(conn: sqlite3.Connection) -> tuple[str, str, str]:
    org = orgs.create(conn, kind="client", name="Atomic Industries", status="active")
    src = placements.create(
        conn, org_id=org.id, program_name="Casualty", status="quoted",
        period_from="2026-01-01", period_to="2027-01-01",
    )
    dst = placements.create(
        conn, org_id=org.id, program_name="Casualty (dup)", status="quoted",
        period_from="2026-01-01", period_to="2027-01-01",
    )
    return org.id, src.id, dst.id


# --- merges are atomic ------------------------------------------------------


def test_a_merge_that_fails_partway_leaves_nothing_behind(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AUTOCOMMIT trap: without a transaction each reassignment committed
    on its own, so a failure left the book half-merged with no way back."""
    org_id, src, dst = _two_placements(conn)
    task = tasks_repo.create(conn, "Chase the quote", org_id=org_id, placement_id=src)

    from bookkit.repo import documents

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("disk went away")

    monkeypatch.setattr(documents, "reassign_placement", boom)

    with pytest.raises(RuntimeError):
        merge.merge_placements(conn, src, dst)

    # the task must NOT have moved, and the source must still be alive
    assert tasks_repo.get(conn, task.id).placement_id == src
    assert base.get(conn, "placement", src) is not None


def test_a_merge_is_one_revertible_unit(conn: sqlite3.Connection) -> None:
    org_id, src, dst = _two_placements(conn)
    task = tasks_repo.create(conn, "Chase the quote", org_id=org_id, placement_id=src)

    with batches_svc.open_batch(
        conn, source="tui", tool="merge_placements",
        summary="merged two placements", org_id=org_id,
    ) as batch:
        merge.merge_placements(conn, src, dst)
        ref = batch.ref

    assert tasks_repo.get(conn, task.id).placement_id == dst
    assert base.get(conn, "placement", src) is None       # soft-deleted

    result = batches_svc.revert(conn, ref, now=NOW)

    assert result.applied
    assert base.get(conn, "placement", src) is not None    # alive again
    assert tasks_repo.get(conn, task.id).placement_id == src


def test_a_market_merge_is_one_revertible_unit(conn: sqlite3.Connection) -> None:
    keep = orgs.create(conn, kind="market", name="AXA XL")
    dupe = orgs.create(conn, kind="market", name="Axa XL")

    with batches_svc.open_batch(
        conn, source="tui", tool="merge_markets", summary="merged two markets"
    ) as batch:
        merge.merge_markets(conn, dupe.id, keep.id)
        ref = batch.ref

    assert base.get(conn, "org", dupe.id) is None

    batches_svc.revert(conn, ref, now=NOW)
    assert base.get(conn, "org", dupe.id) is not None


# --- closing a deal is undoable --------------------------------------------


def _opp(conn: sqlite3.Connection) -> tuple[str, str]:
    org = orgs.create(conn, kind="client", name="Atomic Industries", status="active")
    opp = opportunities.create(
        conn, org_id=org.id, title="New cyber line", stage="presented",
        probability_pct=75,
    )
    return org.id, opp.id


def test_marking_a_deal_lost_is_undone_whole(conn: sqlite3.Connection) -> None:
    """THE lost-deal bug. move_stage writes stage, closed_at, outcome and
    probability_pct together; field-granular undo restored only the last of
    them, so the deal stayed lost AND regained its old probability — a state
    that poisons every weighted-pipeline number."""
    org_id, opp_id = _opp(conn)

    with batches_svc.open_batch(
        conn, source="tui", tool="close_lost", summary="marked a deal lost",
        org_id=org_id,
    ):
        pipeline.move_stage(conn, opp_id, "lost")

    lost = opportunities.get(conn, opp_id)
    assert lost.stage == "lost" and lost.probability_pct == 0

    result = undo.undo_last(conn)

    assert result is not None and result.applied
    back = opportunities.get(conn, opp_id)
    assert back.stage == "presented"
    assert back.probability_pct == 75
    assert back.outcome is None
    assert back.closed_at is None


def test_advancing_a_deal_is_undoable(conn: sqlite3.Connection) -> None:
    org_id, opp_id = _opp(conn)
    nxt = pipeline.allowed_next("presented")[0]

    with batches_svc.open_batch(
        conn, source="tui", tool="advance_card", summary="advanced a deal",
        org_id=org_id,
    ):
        pipeline.move_stage(conn, opp_id, nxt)

    assert opportunities.get(conn, opp_id).stage == nxt
    assert undo.undo_last(conn) is not None
    assert opportunities.get(conn, opp_id).stage == "presented"


def test_the_changes_list_names_the_merge(conn: sqlite3.Connection) -> None:
    org_id, src, dst = _two_placements(conn)
    with batches_svc.open_batch(
        conn, source="tui", tool="merge_placements",
        summary="merged PLC-0001 into PLC-0002", org_id=org_id,
    ):
        merge.merge_placements(conn, src, dst)

    batch = batches_repo.last_undoable(conn, source="tui")
    assert batch is not None
    assert batch.tool == "merge_placements"
    assert "PLC-0001" in batch.summary
    assert batch.org_id == org_id
    assert db is not None


def test_u_undoes_a_merge_which_is_what_the_picker_promises(
    conn: sqlite3.Connection,
) -> None:
    """The MergePicker hint reads 'undoable with u'. Before merges were
    batched that was false, and worse than false: a single-field undo restored
    the source placement while every submission, task and document stayed on
    the target — a half-merged book that looked repaired."""
    org_id, src, dst = _two_placements(conn)
    task = tasks_repo.create(conn, "Chase the quote", org_id=org_id, placement_id=src)

    with batches_svc.open_batch(
        conn, source="tui", tool="merge_placements",
        summary="merged PLC-0001 into PLC-0002", org_id=org_id,
    ):
        merge.merge_placements(conn, src, dst)

    result = undo.undo_last(conn)

    assert result is not None and result.applied
    assert base.get(conn, "placement", src) is not None
    assert tasks_repo.get(conn, task.id).placement_id == src


# --- the TUI call sites actually use it -------------------------------------


async def test_closing_a_deal_from_the_board_is_undoable_end_to_end(
    snapshot_db,
) -> None:
    """Phase 1's lesson: a test that opens the batch itself proves the
    mechanism, not that the keystroke uses it. This drives the real board."""
    from bookkit.tui.app import BookkitApp

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("p")                     # pipeline
        await pilot.pause()
        before = [
            (o.id, o.stage, o.probability_pct)
            for o in opportunities.by_stage(app.conn)
            if o.stage in pipeline.OPEN_STAGES
        ]
        assert before, "seed has no open opportunities to close"

        await pilot.press("less_than_sign")        # mark lost
        await pilot.pause()
        await pilot.pause()

        batch = batches_repo.last_undoable(app.conn, source="tui")
        assert batch is not None and batch.tool == "close_lost", (
            "the keystroke wrote outside a batch"
        )

        closed = [o for o in before if opportunities.get(app.conn, o[0]).stage == "lost"]
        assert closed, "nothing was closed"
        opp_id, old_stage, old_prob = closed[0]

        result = undo.undo_last(app.conn)
        assert result is not None and result.applied
        back = opportunities.get(app.conn, opp_id)
        assert back.stage == old_stage
        assert back.probability_pct == old_prob    # not left at 0
        assert back.closed_at is None


# --- the alias half of a market merge ---------------------------------------


def _two_markets(conn: sqlite3.Connection) -> tuple[str, str]:
    source = orgs.create(conn, kind="market", name="Axa XL", status="active")
    target = orgs.create(conn, kind="market", name="AXA XL", status="active")
    return source.id, target.id


def test_reverting_a_market_merge_brings_the_aliases_back_too(
    conn: sqlite3.Connection,
) -> None:
    """`aliases.reassign_market` was a bulk UPDATE with NO event log — the one
    sub-write of a market merge that left no trace at all. Every one of its
    seven siblings is row-by-row through base.update "or the merge cannot be
    reverted", and this was the proof: reverting brought the duplicate market
    back to life with every alias still pointing at the survivor, so a towerkit
    file spelling the carrier that way went on resolving to the wrong org and
    the resurrected record answered to nothing (2026-08-18).

    Three aliases with three different fates in one revert: the duplicate's own
    two go home, the one the merge INVENTED from its name is removed, and the
    survivor's own is not touched."""
    from bookkit.repo import aliases

    source_id, target_id = _two_markets(conn)
    aliases.set_alias(conn, "AXA XL Insurance Co", source_id)
    aliases.set_alias(conn, "Axa-XL", source_id)
    aliases.set_alias(conn, "AXA XL Corp", target_id)      # the survivor's own

    with batches_svc.open_batch(
        conn, source="tui", tool="merge_markets", summary="merged Axa XL"
    ) as batch:
        merge.merge_markets(conn, source_id, target_id)
    assert aliases.for_market(conn, source_id) == []

    result = batches_svc.revert(conn, batch.ref, now=NOW)
    assert result.applied, result.refused

    assert aliases.for_market(conn, source_id) == ["AXA XL Insurance Co", "Axa-XL"]
    assert aliases.for_market(conn, target_id) == ["AXA XL Corp"], (
        "the alias the merge invented from the duplicate's name outlived it"
    )
    assert aliases.resolve(conn, "Axa-XL") == source_id, (
        "a towerkit file spelling the carrier that way still resolves to the "
        "org the merge folded it into"
    )


def test_an_alias_moved_since_the_merge_refuses_the_whole_revert(
    conn: sqlite3.Connection,
) -> None:
    """'Surface, don't guess', the same rule every other lane follows: if the
    alias no longer points where this batch left it, someone moved it since and
    putting it back would discard their change silently."""
    from bookkit.repo import aliases

    source_id, target_id = _two_markets(conn)
    elsewhere = orgs.create(conn, kind="market", name="Somewhere Else", status="active")
    aliases.set_alias(conn, "Axa-XL", source_id)

    with batches_svc.open_batch(
        conn, source="tui", tool="merge_markets", summary="merged Axa XL"
    ) as batch:
        merge.merge_markets(conn, source_id, target_id)

    aliases.set_alias(conn, "Axa-XL", elsewhere.id)        # moved again, by hand

    result = batches_svc.revert(conn, batch.ref, now=NOW)
    assert not result.applied
    assert [c.change.new_value for c in result.refused] == ["Axa-XL"]
    assert aliases.resolve(conn, "Axa-XL") == elsewhere.id, "the later move was lost"


def test_the_alias_move_is_in_the_event_log_at_all(conn: sqlite3.Connection) -> None:
    """The narrow fact underneath both tests above: the bulk UPDATE wrote ZERO
    event_log rows, so nothing downstream could even see that it happened."""
    from bookkit.repo import aliases

    source_id, target_id = _two_markets(conn)
    aliases.set_alias(conn, "Axa-XL", source_id)
    before = conn.execute("SELECT MAX(rowid) AS r FROM event_log").fetchone()["r"] or 0

    merge.merge_markets(conn, source_id, target_id)

    moved = conn.execute(
        "SELECT entity_id, old_value, new_value FROM event_log"
        " WHERE rowid > ? AND field = 'carrier_alias' AND old_value IS NOT NULL",
        (before,),
    ).fetchall()
    assert [tuple(r) for r in moved] == [(target_id, source_id, "Axa-XL")]
