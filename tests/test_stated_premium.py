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
from datetime import date
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


@pytest.fixture
def spanning(conn: sqlite3.Connection, tmp_path: Path):
    """A layer that covers TWO lines of coverage and is shared by two markets
    — the only shape `split_layer` applies to. Built as a file rather than
    grown through the verbs, because widening a layer onto a line that already
    has one is an overlap towerkit refuses, and removing that one first is a
    `line-empty` error: the shape is legal, the path to it through the app is
    not."""
    from towerkit.model import Layer, Line, Participant, Period, Placement, Retention, RetentionType
    from towerkit.model import Program as TkProgram

    client = orgs.create(conn, kind="client", name="Span Co.", status="active")
    lines = [
        Line(id="gl", name="General Liability", abbr="GL"),
        Line(id="al", name="Auto Liability", abbr="AL"),
    ]
    path = write_program(
        tmp_path / "p" / "span.json",
        TkProgram(
            insured="Span Co.",
            program="Casualty Program",
            placement=Placement.BOUND,
            period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
            lines=lines,
            layers=[
                Layer(
                    id="primary-gl",
                    name="Primary Casualty",
                    applies_to=["gl", "al"],
                    attach=0,
                    limit=2_000_000,
                    premium=900_000,
                    participants=[
                        Participant(carrier="Zurich", share_bps=4_000),
                        Participant(carrier="Swiss Re", share_bps=6_000),
                    ],
                )
            ],
            retentions=[
                Retention(
                    applies_to=[ln.id],
                    type=RetentionType.DEDUCTIBLE,
                    amount=100_000,
                )
                for ln in lines
            ],
        ),
    )
    assert sync.confirm_link(conn, path, client.id).ok
    placement = placements.by_program_path(conn, str(path))
    return conn, placement, path, "al"


def test_splitting_a_market_stated_layer_is_refused_like_the_inline_cell(spanning) -> None:
    """`split_layer` wrote `layer.premium` with a bare setattr, so the split
    form performed the write the worksheet cell REFUSES — one module holding a
    second opinion about when a market-derived sum may be typed over. With
    `heal_premiums` re-deriving the sum immediately afterwards the typed figure
    then vanished while the new slab kept the half it had been divided into,
    inventing money the tower never had.

    Both writes go through towerkit's choke point now, so both get the same
    refusal and the same way out."""
    conn, placement, path, moved_line = spanning
    assert sync.set_participant_premium(
        conn, placement.id, "primary-gl", "Zurich", 520_000_00
    ).ok
    before = path.read_text()

    refused = sync.update_layer(
        conn, placement.id, "primary-gl", premium_cents=1_000_000_00
    )
    assert not refused.ok
    split = sync.split_layer(
        conn,
        placement.id,
        "primary-gl",
        move_line_ids=[moved_line],
        new_name="Primary GL (Auto half)",
        kept_premium_cents=600_000_00,
        moved_premium_cents=460_000_00,
    )
    assert not split.ok, "the split form wrote the figure the inline cell refuses"
    assert "comes from its markets" in " ".join(d.message for d in split.errors)
    assert path.read_text() == before, "refused, but the file changed anyway"


def test_a_layer_with_a_typed_premium_still_splits(spanning) -> None:
    """The guard is about market-STATED premiums only — an ordinary layer
    divides by hand exactly as before."""
    conn, placement, path, moved_line = spanning
    assert sync.split_layer(
        conn,
        placement.id,
        "primary-gl",
        move_line_ids=[moved_line],
        new_name="Primary GL (Auto half)",
        kept_premium_cents=600_000_00,
        moved_premium_cents=300_000_00,
    ).ok
    program = load_program(path)
    kept = next(ly for ly in program.layers if ly.id == "primary-gl")
    moved = next(ly for ly in program.layers if ly.name.endswith("(Auto half)"))
    assert (kept.premium, moved.premium) == (600_000, 300_000)


def test_the_advisories_reach_the_caller_on_both_surfaces(shared) -> None:
    """A CONSEQUENCE MUST REACH EVERY SURFACE (surface sweep, 2026-08-24).

    towerkit returns advisories naming the seats it froze and the sum it set —
    the two of the three numbers the caller did not send — and this wrapper
    dropped them on the floor. The web has a preview, so a human was told
    anyway; MCP has none, and `program_market_premium`'s own docstring
    promises "two are ones you did not send". A change that lands on the web
    and not on MCP has shipped to two thirds of its users.
    """
    conn, placement, path = shared

    diags = sync.set_participant_premium(
        conn, placement.id, "primary-gl", "Zurich", 520_000_00
    )
    assert diags.ok
    codes = [d.code for d in diags.warnings]
    assert codes[:2] == ["premium-frozen", "premium-summed"], (
        f"the advisories are missing or buried: {codes}"
    )
    said = " ".join(d.message for d in diags.warnings)
    assert "Swiss Re at $540,000" in said, "the frozen seat is not named"
    assert "$1,060,000" in said, "the sum it set is not named"


def test_a_refused_premium_advises_nothing(shared) -> None:
    """An advisory describes what the write DID, so a refusal must carry
    none: "premium is now $1,060,000, the sum of its markets" would be a
    sentence about a file that never changed.

    The reachable refusals — a carrier that is not seated, a stale file —
    raise before towerkit is asked, so there is nothing to leak. The `ok`
    guard on the way out covers the ordering itself: it is the reason a
    refusal added later, between the mutation and the return, cannot start
    advising about a write that did not land.
    """
    conn, placement, path = shared
    before = path.read_text()

    diags = sync.set_participant_premium(
        conn, placement.id, "primary-gl", "Nobody At All", 520_000_00
    )

    assert not diags.ok
    assert not [d for d in diags.warnings if d.code.startswith("premium-")]
    assert path.read_text() == before


def test_the_mcp_tool_hands_the_advisories_back(shared) -> None:
    """The surface the finding is about: the tool's return value, not the
    diagnostics object behind it."""
    from bookkit.mcpserver import _program_market_premium

    conn, placement, path = shared
    out = _program_market_premium(
        conn, placement.ref, "primary-gl", "Zurich", "520k"
    )
    said = " ".join(out["warnings"])
    assert "the figure each was already showing" in said
    assert "the sum of its markets" in said
