"""A layer whose markets all state their own premium IS their sum — and stays
their sum through every writer of the seats, not only through the verb that
established the rule.

`edit.set_participant_premium` writes three numbers in one act and holds the
invariant while IT is writing. Binding a market, unbinding one and splitting a
layer all change the participant list; before `edit.heal_premiums` ran in the
write path, each of them left the sum stale — a layer claiming $1,960,000 whose
one remaining market was paid $520,000, carried into `placement.total_premium`,
the Book headline and the account header while `proj_participant` stayed right.
Two answers from one file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_linking_flow import make_program, write_program
from towerkit.model import load_program

from bookkit import sync
from bookkit.repo import orgs, placements


@pytest.fixture
def shared(conn: sqlite3.Connection, tmp_path: Path):
    """A linked program whose primary GL layer is SHARED by two markets —
    the shape the stated-premium rule exists for."""
    client = orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")
    path = write_program(
        tmp_path / "p" / "test.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01", tbd_line=True),
    )
    assert sync.confirm_link(conn, path, client.id).ok
    placement = placements.by_program_path(conn, str(path))
    # Zurich arrives at 100%; seat Swiss Re beside it at 60/40.
    assert sync.update_participant(
        conn, placement.id, "primary-gl", "Zurich", share_bps=4_000
    ).ok
    assert sync.add_participant(
        conn, placement.id, "primary-gl", "Swiss Re", 6_000
    ).ok
    return conn, placement, path


def _layer(path: Path, layer_id: str = "primary-gl"):
    return next(ly for ly in load_program(path).layers if ly.id == layer_id)


def test_unbinding_a_market_re_sums_a_fully_stated_layer(shared) -> None:
    """The bug, end to end. State Zurich's premium (which freezes Swiss Re and
    sets the layer to their sum), then take Swiss Re off: the layer must be
    what its one remaining market is paid, and the placement total with it."""
    conn, placement, path = shared
    assert sync.set_participant_premium(
        conn, placement.id, "primary-gl", "Zurich", 520_000_00
    ).ok
    stated = _layer(path)
    # 520,000 typed, and Swiss Re frozen at the 60% of $900,000 it was showing
    assert stated.premium == 1_060_000
    assert [(p.carrier, p.premium) for p in stated.participants] == [
        ("Zurich", 520_000),
        ("Swiss Re", 540_000),
    ]

    assert sync.remove_participant(conn, placement.id, "primary-gl", "Swiss Re").ok

    healed = _layer(path)
    assert [(p.carrier, p.premium) for p in healed.participants] == [("Zurich", 520_000)]
    assert healed.premium == 520_000, (
        "the layer kept the sum of a market it no longer has — the phantom "
        "$540,000 rides into placement.total_premium"
    )
    assert placements.get(conn, placement.id).total_premium == (
        520_000_00 + 400_000_00  # GL, plus the untouched Cyber layer
    )


def test_binding_a_market_invents_no_figure_and_says_so(shared) -> None:
    """The heal is deliberately narrow: ANY seat without a premium and it
    touches nothing. A market bound onto a stated layer has no price yet, and
    guessing one would state a figure nobody agreed to — the mixed state is
    reported instead, and `set_participant_premium` resolves it when the broker
    prices the seat."""
    conn, placement, path = shared
    assert sync.set_participant_premium(
        conn, placement.id, "primary-gl", "Zurich", 520_000_00
    ).ok

    # make room for a third market — a fully-signed layer refuses one
    assert sync.update_participant(
        conn, placement.id, "primary-gl", "Swiss Re", share_bps=5_000
    ).ok
    diags = sync.add_participant(conn, placement.id, "primary-gl", "AXA", 1_000)
    assert diags.ok

    layer = _layer(path)
    assert layer.premium == 1_060_000, "the heal invented a premium for the new seat"
    assert [p.premium for p in layer.participants] == [520_000, 540_000, None]
    assert "premium-split" in {w.code for w in diags.warnings}


def test_a_layer_with_no_markets_is_never_zeroed(shared) -> None:
    """`all([])` is True and `sum([])` is 0, so a heal that forgot to ask
    whether the layer HAS seats would wipe the premium off every unplaced
    layer and every buffer on the program — on every write."""
    conn, placement, path = shared
    assert not _layer(path, "primary-cy").participants
    assert sync.set_participant_premium(
        conn, placement.id, "primary-gl", "Zurich", 520_000_00
    ).ok
    assert _layer(path, "primary-cy").premium == 400_000
