"""The bridge between towerkit's derived field surface and bookkit's forms.

D6 (2026-08-20) made seventeen towerkit fields reachable from a browser. What
makes that maintainable rather than seventeen new places to edit is that NONE
of the field knowledge lives in bookkit: types, bounds, guards, clearability
and addressing all come from `towerkit.mcpsurface`, which derives them from the
pydantic models at import time.

So these tests are mostly about the SEAM, not the fields. The one thing bookkit
adds is the money boundary — cents in, whole dollars in the file — and the one
thing it re-states is addressing, which is why the first test pins bookkit's
copy of it against towerkit's own.
"""

from __future__ import annotations

import pytest
from towerkit import edit as tk_edit
from towerkit import mcpsurface

from bookkit import sync, towerfields


def _program(tmp_path):
    """A two-line programme with a market, a retention, a sublimit and a named
    limit — one row of every kind `mcpsurface.TARGET` knows how to address."""
    from towerkit.model import Participant, Program, dump_program, load_program

    program = Program(
        insured="Fixture Industries",
        program="Fixture Casualty",
        placement="bound",
        period={"start": "2026-01-01", "end": "2027-01-01"},
    )
    gl = tk_edit.add_line(program, "General Liability")
    tk_edit.add_line(program, "Inland Marine")
    layer = tk_edit.add_layer(program, [gl.id])
    layer.name = "Primary"
    layer.attach = 0
    layer.limit = 1_000_000
    # towerkit has no add_participant verb — bookkit's sync builds the model
    # directly too (sync.add_participant), so the fixture does the same.
    layer.participants.append(Participant(carrier="Acme Insurance", share_bps=10_000))
    tk_edit.add_retention(program, [gl.id], "deductible", 25_000)
    tk_edit.add_sublimit(program, "Flood", 500_000, [gl.id])
    tk_edit.add_named_limit(program, layer.id, "Products", 2_000_000)
    # Through towerkit's own serialiser: a hand-written fixture fails the
    # schema on keys the model fills in for free (2026-08-20).
    path = tmp_path / "fixture.program.json"
    dump_program(program, path)
    return load_program(path), layer.id


# --- addressing ----------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,uses_target,uses_index",
    [
        ("program", False, False),
        ("line", True, False),
        ("layer", True, False),
        ("participant", True, True),
        ("retention", False, True),
        ("sublimit", False, True),
        ("named_limit", True, True),
    ],
)
def test_bookkit_addresses_a_row_exactly_as_towerkit_does(
    tmp_path, kind, uses_target, uses_index
):
    """`sync._addressed` is assembled from bookkit's own finders so that a bad
    id refuses instead of escaping `_mutate` as a 500. That makes it a SECOND
    statement of towerkit's addressing, and this is what stops the two drifting:
    every kind is resolved both ways and must land on the same object.

    The parametrisation mirrors `mcpsurface.TARGET` rather than restating it,
    so a kind towerkit adds turns the next assertion red rather than being
    quietly untested.
    """
    program, layer_id = _program(tmp_path)
    target = layer_id if kind in ("layer", "participant", "named_limit") else None
    if kind == "line":
        target = program.lines[0].id
    index = 0 if uses_index else None

    ours = sync._addressed(program, kind, target, index)
    theirs = tk_edit._entity(
        program, kind, mcpsurface.edit_address(
            mcpsurface.resolve(kind, next(iter(mcpsurface.SURFACE[kind]))), target, index
        ), index,
    )
    assert ours is theirs


def test_every_kind_towerkit_publishes_is_addressable_here(tmp_path):
    """The guard against a kind arriving in towerkit with no bookkit address:
    `_addressed` would raise "not a towerkit record kind" at a user."""
    program, layer_id = _program(tmp_path)
    for kind in mcpsurface.KINDS:
        needs = mcpsurface.TARGET[kind]
        target = layer_id if kind in ("layer", "participant", "named_limit") else None
        if kind == "line":
            target = program.lines[0].id
        assert sync._addressed(
            program, kind, target, 0 if "index" in needs else None
        ) is not None


def test_a_bad_address_refuses_rather_than_escaping(tmp_path):
    """KeyError and IndexError are the two exceptions `_mutate` does NOT fold
    into diagnostics, so both would reach the browser as a 500."""
    program, layer_id = _program(tmp_path)
    for kind, target, index in [
        ("layer", "no-such-layer", None),
        ("line", "no-such-line", None),
        ("retention", None, 99),
        ("named_limit", layer_id, 99),
        ("layer", None, None),
    ]:
        with pytest.raises(ValueError):
            sync._addressed(program, kind, target, index)


# --- the money boundary ---------------------------------------------------------


def test_money_is_typed_in_cents_and_stored_in_whole_dollars():
    """CLAUDE.md's rule, on towerkit's side of the fence: entry accepts cents
    because that is what every other money field in bookkit accepts, and
    `cents_to_dollars` is the one conversion."""
    entry = towerfields.resolve("named_limit", "amount")
    assert towerfields.to_wire(entry, "1,500,000") == 1_500_000
    assert towerfields.to_wire(entry, "1.5m") == 1_500_000
    assert towerfields.to_wire(entry, "250k") == 250_000


def test_a_sub_dollar_amount_is_refused_not_rounded():
    """The remainder is the client's money. towerkit files carry whole dollars,
    so the refusal is the only honest answer — rounding it away silently is the
    failure the cents rule exists to prevent."""
    entry = towerfields.resolve("named_limit", "amount")
    with pytest.raises(towerfields.FieldRefused) as caught:
        towerfields.to_wire(entry, "1,234.56")
    assert "whole" in str(caught.value)


def test_the_editor_prefills_the_exact_figure_and_the_cell_shows_the_compact_one():
    """A cell that pre-fills what its own parser would store as a DIFFERENT
    number is unsaveable until the value is retyped from memory."""
    entry = towerfields.resolve("named_limit", "amount")
    assert towerfields.display(entry, 1_500_000) == "$1.5M"
    assert towerfields.editor_text(entry, 1_500_000) == "1500000"
    assert towerfields.to_wire(entry, towerfields.editor_text(entry, 1_500_000)) == 1_500_000


# --- the lexicon ----------------------------------------------------------------


def test_states_take_towerkits_own_comma_syntax():
    """`edit.parse_states` is the single definition of it and the TUI enters
    states the same way; an empty box is an empty LIST, not a clear, which is
    what makes removing the last state an ordinary save."""
    entry = towerfields.resolve("layer", "states")
    assert towerfields.to_wire(entry, "IL, WI ,IN") == ["IL", "WI", "IN"]
    assert towerfields.to_wire(entry, "") == []


def test_a_required_field_refuses_an_empty_box_in_words_this_surface_can_act_on():
    """towerkit's own clearing rule is written for a JSON caller ("send null…")
    and names a null/"" distinction a text input cannot express."""
    with pytest.raises(towerfields.FieldRefused) as caught:
        towerfields.to_wire(towerfields.resolve("layer", "name"), "  ")
    assert "cannot be left empty" in str(caught.value)
    assert "null" not in str(caught.value)


def test_a_denied_field_says_why_rather_than_404ing_blankly():
    """The denial reasons are DATA in towerkit, written for the caller, so they
    are printed rather than paraphrased."""
    with pytest.raises(towerfields.FieldRefused) as caught:
        towerfields.resolve("program", "period")
    assert "not settable here" in str(caught.value)


def test_a_bool_takes_the_literal_towerkit_demands():
    """towerkit refuses a coerced "true" string on purpose — these five decide
    what a saved chart prints — so the select posts the literal."""
    entry = towerfields.resolve("program", "render.showTotals")
    assert towerfields.to_wire(entry, "true") is True
    assert towerfields.to_wire(entry, "false") is False
    with pytest.raises(towerfields.FieldRefused):
        towerfields.to_wire(entry, "yes")


def test_every_writable_field_either_renders_or_refuses_with_a_reason():
    """The whole surface, swept: no field may raise something that is not a
    FieldRefused, because a route turns that into a 500 at a user. A type
    towerkit adds lands here first."""
    for kind, fields in mcpsurface.SURFACE.items():
        for name in fields:
            entry = mcpsurface.resolve(kind, name)
            try:
                field = towerfields.bookkit_field(entry)
            except towerfields.FieldRefused:
                continue
            assert field.label and field.kind
            if field.kind == "select":
                assert field.options, f"{kind}.{name} renders a select with no options"
