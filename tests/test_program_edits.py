"""Transactional program edits from bookkit — all via write_through, with
towerkit's validator as the gatekeeper — plus market merging."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_linking_flow import make_program, write_program
from towerkit.model import load_program

from bookkit import sync
from bookkit.money import MoneyParseError, parse_share_bps
from bookkit.repo import aliases, contacts, orgs, placements, submissions
from bookkit.services.merge import MergeError, merge_markets


@pytest.fixture
def linked(conn: sqlite3.Connection, tmp_path: Path):
    client = orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")
    path = write_program(
        tmp_path / "p" / "test.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01", tbd_line=True),
    )
    assert sync.confirm_link(conn, path, client.id).ok
    placement = placements.by_program_path(conn, str(path))
    return conn, client, placement, path


def test_parse_share_bps() -> None:
    assert parse_share_bps("25%") == 2500
    assert parse_share_bps("25") == 2500
    assert parse_share_bps("12.5") == 1250
    assert parse_share_bps("33.34%") == 3334
    assert parse_share_bps("100") == 10_000
    assert parse_share_bps("0.25") == 25  # a quarter PERCENT — one rule, no guessing
    for bad in ("0", "101", "33.333", "a third"):
        with pytest.raises(MoneyParseError):
            parse_share_bps(bad)


def test_update_program_dates(linked) -> None:
    conn, _, placement, path = linked
    diags = sync.update_program(
        conn, placement.id, period_from="2026-02-01", period_to="2027-02-01"
    )
    assert diags.ok
    program = load_program(path)
    assert program.period.start.isoformat() == "2026-02-01"
    refreshed = placements.get(conn, placement.id)
    assert refreshed.period_from == "2026-02-01"  # projection followed
    # nonsense dates are refused before anything is written
    bad = sync.update_program(conn, placement.id, period_to="2025-01-01")
    assert not bad.ok
    assert load_program(path).period.end.isoformat() == "2027-02-01"


def test_update_layer_premium_and_policy(linked) -> None:
    conn, _, placement, path = linked
    diags = sync.update_layer(
        conn, placement.id, "primary-gl",
        premium_cents=1_100_000_00, policy_number="GLP-2026-0042",
        period_from="2026-02-01", period_to="2027-02-01",
    )
    assert diags.ok
    layer = load_program(path).layers[0]
    assert layer.premium == 1_100_000
    assert layer.policy_number == "GLP-2026-0042"
    assert layer.period is not None and layer.period.start.isoformat() == "2026-02-01"


def test_update_layer_refuses_gap(linked) -> None:
    conn, _, placement, path = linked
    before = path.read_text()
    diags = sync.update_layer(conn, placement.id, "primary-gl", attach_cents=999_00)
    assert not diags.ok  # line no longer starts at $0 → towerkit refuses
    assert path.read_text() == before


def test_add_layer_pending(linked) -> None:
    conn, _, placement, path = linked
    diags = sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=10_000_000_00,
    )
    assert diags.ok
    program = load_program(path)
    added = next(ly for ly in program.layers if ly.name == "1st Excess")
    assert added.id == "1st-excess"
    assert added.participants == []  # pending — 'To be placed'
    assert added.attach == 2_000_000 and added.limit == 10_000_000
    details = sync.layer_details(conn, placement.id)
    assert any(d["id"] == "1st-excess" and d["signed_pct"] == 0 for d in details)
    # statutory travels with the layer: without it a reader of these dicts
    # cannot tell unlimited cover from a limit that happens to be zero.
    assert all("statutory" in d for d in details)
    assert not any(d["statutory"] for d in details)


def test_add_layer_with_no_typed_attach_seats_on_the_existing_top(linked) -> None:
    """`attach_cents=None` is the web's own calling convention now (whole-
    branch review finding 2, 2026-08-21): the "Add layer" form dropped its
    typed attachment input, and `layer_add` always calls `sync.add_layer`
    this way. This used to reach as blank/None and crash with "unsupported
    operand type(s) for %: 'NoneType' and 'int'" (found 2026-08-19,
    formerly guarded by a web-level test that assumed a broker COULD type a
    blank attach — that surface is gone, so the regression is guarded here
    instead, directly against the function that must never regress it).

    Leaving `attach_cents` out lets `edit.add_layer`'s own suggested attach —
    the top of the existing stack for these lines — stand, unmodified: same
    "position decides the attachment" rule the stack editor already proves,
    for the form that can still span every line or price at creation."""
    conn, _, placement, path = linked
    before_top = max(
        (
            ly.attach + ly.limit
            for ly in load_program(path).layers
            if ly.limit > 0 and "gl" in ly.applies_to
        ),
        default=0,
    )

    first = sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=None, limit_cents=10_000_000_00,
    )
    assert first.ok, [d.message for d in first.errors]
    second = sync.add_layer(
        conn, placement.id, "2nd Excess", ["gl"],
        attach_cents=None, limit_cents=5_000_000_00,
    )
    assert second.ok, [d.message for d in second.errors]

    program = load_program(path)
    first_layer = next(ly for ly in program.layers if ly.name == "1st Excess")
    second_layer = next(ly for ly in program.layers if ly.name == "2nd Excess")
    assert first_layer.attach == before_top, (
        "the first layer did not seat on the existing top of the gl line"
    )
    assert second_layer.attach == first_layer.attach + first_layer.limit, (
        "the second layer did not seat on top of the first — a typed attach "
        "is gone, but a GAP or an OVERLAP must not take its place"
    )


def test_add_participant_and_oversign_refused(linked) -> None:
    conn, _, placement, path = linked
    diags = sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=10_000_000_00,
    )
    assert diags.ok
    assert sync.add_participant(conn, placement.id, "1st-excess", "Chubb", 6000).ok
    assert sync.add_participant(conn, placement.id, "1st-excess", "AXA XL", 4000).ok
    program = load_program(path)
    layer = next(ly for ly in program.layers if ly.id == "1st-excess")
    assert layer.signed_bps == 10_000
    before = path.read_text()
    # a third market at any share would over-sign: refused, file untouched
    over = sync.add_participant(conn, placement.id, "1st-excess", "Zurich", 500)
    assert not over.ok
    assert path.read_text() == before
    # same carrier twice is refused too
    dup = sync.add_participant(conn, placement.id, "1st-excess", "Chubb", 100)
    assert not dup.ok


def test_edit_conflict_surfaces_as_diagnostic(linked) -> None:
    conn, _, placement, path = linked
    path.write_text(path.read_text().replace("Primary GL", "Primary General Liability"))
    diags = sync.update_layer(conn, placement.id, "primary-gl", premium_cents=100_00)
    assert not diags.ok
    assert any(d.code == "conflict" for d in diags.errors)


def test_merge_markets_folds_duplicate_with_alias(conn: sqlite3.Connection, tmp_path) -> None:
    client = orgs.create(conn, kind="client", name="Client A", status="active")
    real = orgs.create(conn, kind="market", name="AXA XL", status="active")
    orgs.set_market_profile(conn, real.id, market_type="carrier", am_best_rating="A+")
    dupe = orgs.create(conn, kind="market", name="Axa XL", status="active")
    contacts.create(conn, dupe.id, first_name="Ute", last_name="Meyer", role="underwriter")
    placement = placements.create(conn, client.id, "Casualty", "2026-01-01", "2027-01-01")
    submissions.create(conn, dupe.id, "2026-05-01", placement_id=placement.id)

    result = merge_markets(conn, dupe.id, real.id)
    assert result.alias_added == "Axa XL"
    assert result.moved_contacts == 1 and result.moved_submissions == 1
    assert aliases.resolve(conn, "Axa XL") == real.id  # towers keep resolving
    assert [c.name for c in contacts.for_org(conn, real.id)] == ["Ute Meyer"]
    assert submissions.for_market(conn, real.id)
    with pytest.raises(KeyError):
        orgs.get(conn, dupe.id)  # soft-deleted

    with pytest.raises(MergeError):
        merge_markets(conn, real.id, client.id)  # clients are not markets


def test_program_lines_helper(linked) -> None:
    conn, _, placement, _ = linked
    assert sync.program_lines(conn, placement.id) == [
        ("gl", "General Liability"), ("cy", "Cyber"),
    ]


# --- the panel a layer is actually placed with -------------------------------


def test_layer_details_carries_the_carrier_panel(linked) -> None:
    """AE review: program_layers' description promised participants and
    sync.layer_details returned none, so an assistant reading the contract
    believed it could see who is on the 2nd excess when it could not. The
    DESCRIPTION was right and the data was thin — program_summary is the tool
    that is deliberately slim, and says so; this is the tower.

    Three layers on purpose, each a different shape, and every one asserted:
    a fixture that decorates only the first layer passes a per-layer bug."""
    conn, _, placement, path = linked
    assert sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=10_000_000_00, premium_cents=300_000_00,
    ).ok
    assert sync.add_participant(conn, placement.id, "1st-excess", "Chubb", 6000).ok
    assert sync.add_participant(conn, placement.id, "1st-excess", "AXA XL", 4000).ok

    panels = {d["id"]: d["participants"] for d in sync.layer_details(conn, placement.id)}
    assert panels["primary-gl"] == [
        {
            "carrier": "Zurich", "share_pct": 100.0,
            "limit_cents": 2_000_000_00, "premium_cents": 900_000_00,
        },
    ]
    # a layer with no panel is 'To be placed' — an EMPTY list, never absent,
    # because absent and unplaced are different facts to a reader
    assert panels["primary-cy"] == []
    assert panels["1st-excess"] == [
        {
            "carrier": "Chubb", "share_pct": 60.0,
            "limit_cents": 6_000_000_00, "premium_cents": 180_000_00,
        },
        {
            "carrier": "AXA XL", "share_pct": 40.0,
            "limit_cents": 4_000_000_00, "premium_cents": 120_000_00,
        },
    ]
    # the shares add up to the signed figure already on the layer — same units
    # in the same dict, which is the whole reason share is a percentage here
    for detail in sync.layer_details(conn, placement.id):
        assert sum(p["share_pct"] for p in detail["participants"]) == detail["signed_pct"]


def test_layer_details_premium_share_is_none_when_the_layer_has_none(linked) -> None:
    """A share of an unknown premium is unknown, not zero — the same rule the
    projection already applies (sync.project_file)."""
    conn, _, placement, _ = linked
    assert sync.add_layer(
        conn, placement.id, "2nd Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=5_000_000_00,
    ).ok
    assert sync.add_participant(conn, placement.id, "2nd-excess", "Berkley", 10_000).ok
    detail = next(
        d for d in sync.layer_details(conn, placement.id) if d["id"] == "2nd-excess"
    )
    assert detail["premium_cents"] is None
    assert detail["participants"] == [
        {
            "carrier": "Berkley", "share_pct": 100.0,
            "limit_cents": 5_000_000_00, "premium_cents": None,
        },
    ]


def test_layer_details_still_opens_the_file_once(linked, monkeypatch) -> None:
    """The panel comes off the program already in memory. layer_details does
    file I/O per call and the web page was deliberately reduced to ONE call per
    render; a per-layer load would undo that silently, and three layers is the
    smallest fixture that can tell one load from several."""
    conn, _, placement, _ = linked
    assert sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=10_000_000_00,
    ).ok
    calls = []
    real = sync.load_program

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(sync, "load_program", counting)
    details = sync.layer_details(conn, placement.id)
    assert len(details) == 3
    assert len(calls) == 1, calls


# --- the follows-underlying boundary -----------------------------------------
#
# towerkit's other two write paths (mcpserver._write, tui EditSession.mutate)
# heal derived state between the mutation and the check. bookkit's
# write_through did not, so the SAME mutation was refused here and accepted
# there — and the refusal named an attachment the broker never set.


def _follows_program(insured: str = "Test Client, Inc."):
    """A primary and a follows-underlying umbrella seated on top of it."""
    from datetime import date as _date

    from towerkit.model import Layer, Line, Participant, Period, Program, Retention, RetentionType
    from towerkit.model import Placement as TkPlacement

    return Program(
        insured=insured,
        program="Casualty Program",
        placement=TkPlacement.BOUND,
        period=Period(start=_date(2026, 1, 1), end=_date(2027, 1, 1)),
        lines=[Line(id="gl", name="General Liability", abbr="GL")],
        layers=[
            Layer(
                id="primary-gl", name="Primary GL", applies_to=["gl"],
                attach=0, limit=2_000_000, premium=900_000,
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            ),
            Layer(
                id="umbrella", name="Umbrella", applies_to=["gl"],
                attach=2_000_000, limit=5_000_000, premium=300_000,
                follows_underlying=True,
                participants=[Participant(carrier="AIG", share_bps=10_000)],
            ),
        ],
        retentions=[
            Retention(applies_to=["gl"], type=RetentionType.DEDUCTIBLE, amount=100_000)
        ],
    )


@pytest.fixture
def follows(conn: sqlite3.Connection, tmp_path: Path):
    client = orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")
    path = write_program(tmp_path / "p" / "follows.json", _follows_program())
    assert sync.confirm_link(conn, path, client.id).ok
    return conn, placements.by_program_path(conn, str(path)), path


def test_raising_a_primary_limit_under_a_follows_layer_is_accepted(follows) -> None:
    """The reviewer's reproduction: identical mutation, both cycles, same
    answer. Before the fix bookkit returned ok=False with
    `layer-follows-attach` while towerkit returned ok=True."""
    from towerkit import edit
    from towerkit.validate import validate_program

    conn, placement, path = follows

    # towerkit's cycle: mutate → heal → validate
    tk = _follows_program()
    tk.layers[0].limit = 3_000_000
    edit.heal_follows(tk)
    assert validate_program(tk).ok

    # bookkit's cycle, same mutation
    diags = sync.update_layer(conn, placement.id, "primary-gl", limit_cents=3_000_000_00)
    assert diags.ok, [(d.code, d.message) for d in diags.errors]

    on_disk = load_program(path)
    assert on_disk.layers[0].limit == 3_000_000
    # the healed attachment is what was WRITTEN, not merely what was validated
    assert on_disk.layers[1].attach == 3_000_000


def test_healing_happens_before_validation_not_after(follows) -> None:
    """Position in the cycle, asserted directly. If heal_follows ran after
    validate_program the edit above would still be refused; if it ran after
    dump_program the file would keep the stale attachment. Both are checked by
    reading the file the write actually produced."""
    conn, placement, path = follows
    assert sync.update_layer(conn, placement.id, "primary-gl", limit_cents=4_000_000_00).ok
    on_disk = load_program(path)
    assert on_disk.layers[1].attach == 4_000_000, "healed after the dump — file is stale"
    # and the healed file is itself valid, i.e. validation saw the healed state
    from towerkit.validate import validate_program

    assert validate_program(on_disk).ok


def test_lowering_a_primary_limit_reseats_the_follows_layer_too(follows) -> None:
    """Healing is not a one-directional patch: dropping the underlying limit
    pulls the umbrella DOWN, which is the case that used to strand it above a
    gap rather than below an overlap."""
    conn, placement, path = follows
    assert sync.update_layer(conn, placement.id, "primary-gl", limit_cents=1_000_000_00).ok
    assert load_program(path).layers[1].attach == 1_000_000


def test_a_new_layer_id_cannot_collide_with_a_line_id(linked) -> None:
    """`sync._slug` considered only LAYER ids taken, so naming a layer after a
    line handed it that line's id — and the validator does not catch it,
    because nothing looks across the two collections. towerkit.edit.unique_id
    takes the union, which is the rule towerkit's own surfaces obey."""
    from towerkit.validate import validate_program

    conn, _, placement, path = linked
    before = load_program(path)
    assert "cy" in {ln.id for ln in before.lines}

    diags = sync.add_layer(
        conn, placement.id, "CY", ["cy"], 5_000_000_00, 5_000_000_00
    )
    assert diags.ok, [(d.code, d.message) for d in diags.errors]

    after = load_program(path)
    line_ids = {ln.id for ln in after.lines}
    layer_ids = [ly.id for ly in after.layers]
    assert len(layer_ids) == len(set(layer_ids)), "layer ids collided with each other"
    assert not (set(layer_ids) & line_ids), (
        f"a layer took a line's id: layers={layer_ids} lines={sorted(line_ids)}"
    )
    # the collision is invisible to the validator — this test is the only guard
    assert validate_program(after).ok


def test_add_layer_keeps_the_broker_s_name_price_and_seat(linked) -> None:
    """Delegating the append to towerkit.edit.add_layer must not let towerkit's
    auto-name ('2nd Excess') or its suggested attachment leak through: bookkit
    is placing a layer the broker has already named, seated and priced."""
    conn, _, placement, path = linked
    assert sync.add_layer(
        conn, placement.id, "First Excess GL", ["gl"],
        2_000_000_00, 5_000_000_00, premium_cents=250_000_00,
    ).ok
    layer = next(ly for ly in load_program(path).layers if ly.name == "First Excess GL")
    assert layer.id == "first-excess-gl"
    assert layer.attach == 2_000_000
    assert layer.limit == 5_000_000
    assert layer.premium == 250_000
    assert layer.participants == []  # still 'To be placed'


# --- correcting and removing a market, and re-scoping a layer ------------------
#
# sync.py could add a market to a layer and could not change or remove one, and
# nothing wrapped towerkit.edit.set_applies_to. CRUD needs all four; these are
# the three that were missing (2026-08-19).


def _seat(conn, placement, path):
    """A layer with one market on half of it, so there is something to correct
    and open capacity left to correct it into.

    The bridge below is not decoration. towerkit refuses a GAP as firmly as an
    overlap, and a layer may only apply to lines whose towers it actually
    continues — so re-scoping is testable at all only where the lines top out
    together. The seeded program has GL running to $2M and Cyber to $5M; the
    bridge lifts GL to $5M so a layer attaching there can legitimately cover
    both. Two earlier drafts of this fixture were refused, correctly, for a gap
    and then for an overlap.
    """
    assert sync.add_layer(
        conn, placement.id, "GL Bridge", ["gl"],
        attach_cents=2_000_000_00, limit_cents=3_000_000_00,
    ).ok
    assert sync.add_layer(
        conn, placement.id, "2nd Excess", ["gl"],
        attach_cents=5_000_000_00, limit_cents=10_000_000_00,
    ).ok
    assert sync.add_participant(conn, placement.id, "2nd-excess", "Chubb", 5000).ok
    return "2nd-excess"


def test_a_markets_share_is_corrected_in_place(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)

    assert sync.update_participant(
        conn, placement.id, layer_id, "Chubb", share_bps=6000
    ).ok

    layer = next(ly for ly in load_program(path).layers if ly.id == layer_id)
    assert [(p.carrier, p.share_bps) for p in layer.participants] == [("Chubb", 6000)]


def test_a_market_is_renamed_without_moving_its_share(linked) -> None:
    """A carrier corrected to its right name keeps the seat it was on. Doing
    this as remove-then-add would drop the share on the floor between the two
    writes, and a refused second half would leave the layer short."""
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)

    assert sync.update_participant(
        conn, placement.id, layer_id, "Chubb", new_carrier="Chubb Bermuda"
    ).ok

    layer = next(ly for ly in load_program(path).layers if ly.id == layer_id)
    assert [(p.carrier, p.share_bps) for p in layer.participants] == [
        ("Chubb Bermuda", 5000)
    ]


def test_correcting_a_market_that_is_not_on_the_layer_is_refused(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    before = path.read_text()

    refused = sync.update_participant(
        conn, placement.id, layer_id, "Zurich", share_bps=1000
    )

    assert not refused.ok
    assert "Zurich" in refused.errors[0].message
    assert path.read_text() == before


def test_correcting_a_market_onto_a_name_already_seated_is_refused(linked) -> None:
    """Two rows for one carrier on one layer is a double-count of its share."""
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    assert sync.add_participant(conn, placement.id, layer_id, "AXA XL", 2000).ok
    before = path.read_text()

    refused = sync.update_participant(
        conn, placement.id, layer_id, "AXA XL", new_carrier="Chubb"
    )

    assert not refused.ok
    assert path.read_text() == before


def test_removing_the_only_market_leaves_the_layer_unplaced(linked) -> None:
    """The LAYER survives. Losing a layer because its last market fell away
    destroys the tower's shape — towerkit's own word for what is left is
    'To be placed', and that is a state, not an absence."""
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)

    assert sync.remove_participant(conn, placement.id, layer_id, "Chubb").ok

    layers = load_program(path).layers
    layer = next(ly for ly in layers if ly.id == layer_id)
    assert layer.participants == []
    assert layer.signed_bps == 0


def test_removing_a_market_that_is_not_there_is_refused(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    before = path.read_text()

    refused = sync.remove_participant(conn, placement.id, layer_id, "Zurich")

    assert not refused.ok
    assert path.read_text() == before


def test_a_layer_is_re_scoped_to_other_lines(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    # gl and cy both top out at $5M once _seat's bridge is in, which is what
    # makes a layer attaching there legitimately cover both.
    assert sync.set_applies_to(conn, placement.id, layer_id, ["gl", "cy"]).ok

    layer = next(ly for ly in load_program(path).layers if ly.id == layer_id)
    assert layer.applies_to == ["gl", "cy"]


def test_re_scoping_onto_a_line_the_program_does_not_have_is_refused(linked) -> None:
    """towerkit raises KeyError for an unknown line, and KeyError is NOT one of
    the exceptions sync._mutate folds into diagnostics — so without the
    wrapper's own guard this reached the browser as a 500 rather than a
    refusal."""
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    before = path.read_text()

    refused = sync.set_applies_to(conn, placement.id, layer_id, ["not-a-line"])

    assert not refused.ok
    assert "not-a-line" in refused.errors[0].message
    assert path.read_text() == before


def test_a_layer_must_apply_to_at_least_one_line(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)

    refused = sync.set_applies_to(conn, placement.id, layer_id, [])

    assert not refused.ok


def test_remove_layer_takes_its_seats_with_it(linked) -> None:
    """D2 (2026-08-19): no surface could remove a mis-added layer at all."""
    conn, _, placement, path = linked
    assert sync.add_layer(
        conn, placement.id, "Mistake Layer", ["gl"],
        attach_cents=2_000_000_00, limit_cents=5_000_000_00,
    ).ok
    assert sync.add_participant(
        conn, placement.id, "mistake-layer", "Oops Re", 5_000
    ).ok

    diags = sync.remove_layer(conn, placement.id, "mistake-layer")

    assert diags.ok
    program = load_program(path)
    assert "mistake-layer" not in [ly.id for ly in program.layers]
    assert "Oops Re" not in path.read_text()


def test_remove_layer_refuses_an_unknown_id(linked) -> None:
    conn, _, placement, path = linked
    before = path.read_bytes()

    diags = sync.remove_layer(conn, placement.id, "never-existed")

    assert not diags.ok
    assert path.read_bytes() == before


def test_remove_layer_leaves_the_gap_stated_not_refused(linked) -> None:
    """Removing a middle layer leaves the one above floating over an open
    band. line-gap is a WARNING, not a refusal (2026-08-21): sliding
    '2nd Excess' down to close the tower is not done — that would silently
    change what the client is covered for — so the write SUCCEEDS and the
    gap is stated, in towerkit's own words, rather than hidden by a silent
    reseat."""
    conn, _, placement, path = linked
    assert sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=5_000_000_00,
    ).ok
    assert sync.add_layer(
        conn, placement.id, "2nd Excess", ["gl"],
        attach_cents=7_000_000_00, limit_cents=5_000_000_00,
    ).ok
    before = path.read_bytes()

    diags = sync.remove_layer(conn, placement.id, "1st-excess")

    assert diags.ok, [d.message for d in diags.errors]
    assert path.read_bytes() != before
    assert any(d.code == "line-gap" for d in diags.warnings)
    # 2nd Excess did NOT reseat onto Primary GL's top — the invariant itself
    remaining = load_program(path).layers_for_line("gl")
    survivor = next(ly for ly in remaining if ly.id == "2nd-excess")
    assert survivor.attach == 7_000_000, "the tower closed up and moved cover"


# --- phase 3: lines become bookkit-editable (D1) -------------------------------


def test_add_line_arrives_with_a_pending_layer(linked) -> None:
    """towerkit's validator makes an empty line an ERROR (line-empty), so a
    bare new line could never be written through. add_line therefore creates
    the line WITH a pending 'To be placed' layer — the same shape scaffold
    uses — and the whole write is valid in one step."""
    conn, _, placement, path = linked

    diags = sync.add_line(conn, placement.id, "Marine Cargo")

    assert diags.ok, diags.errors
    program = load_program(path)
    line = next(ln for ln in program.lines if ln.name == "Marine Cargo")
    covering = [ly for ly in program.layers if line.id in ly.applies_to]
    assert covering, "the new line arrived empty — the validator refuses that"
    assert covering[0].participants == []


def test_rename_line_cascades_applies_to(linked) -> None:
    conn, _, placement, path = linked
    program = load_program(path)
    old_id = program.lines[0].id

    diags = sync.rename_line(conn, placement.id, old_id, "General Casualty")

    assert diags.ok, diags.errors
    program = load_program(path)
    line = next(ln for ln in program.lines if ln.name == "General Casualty")
    assert old_id not in [ln.id for ln in program.lines]
    for layer in program.layers:
        assert old_id not in layer.applies_to
    assert any(line.id in ly.applies_to for ly in program.layers)


def test_rename_line_refuses_an_unknown_id(linked) -> None:
    conn, _, placement, path = linked
    before = path.read_bytes()

    diags = sync.rename_line(conn, placement.id, "never-was", "Anything")

    assert not diags.ok
    assert path.read_bytes() == before


def test_remove_line_cascades_and_the_validator_still_gates(linked) -> None:
    """remove_line cascades: the id leaves every appliesTo and anything left
    empty goes with it. The fixture's cy line has its own primary layer, so
    removing cy takes that layer along."""
    conn, _, placement, path = linked

    diags = sync.remove_line(conn, placement.id, "cy")

    assert diags.ok, diags.errors
    program = load_program(path)
    assert "cy" not in [ln.id for ln in program.lines]
    assert all("cy" not in ly.applies_to for ly in program.layers)


def test_remove_line_refuses_an_unknown_id(linked) -> None:
    conn, _, placement, path = linked
    before = path.read_bytes()

    diags = sync.remove_line(conn, placement.id, "never-was")

    assert not diags.ok
    assert path.read_bytes() == before


def test_set_applies_to_moves_a_layer_between_lines(linked) -> None:
    """The verb has existed, tested and dead, since the sync layer was built;
    phase 3 gives it its first caller, so these assertions go load-bearing.

    The move must be geometrically VALID — spanning a primary across a line
    that already has one is an overlap towerkit refuses (correctly; that
    refusal has its own test below). A 1st excess on gl narrowing... rather:
    an excess added across gl at its top, then widened is a gap on cy — so
    the honest valid move here is narrowing an excess that spans both lines
    down to one."""
    conn, _, placement, path = linked
    # an excess above BOTH primaries needs equal tops; gl tops at 2M and cy
    # at 5M in this fixture, so raise gl with its own excess first
    assert sync.add_layer(
        conn, placement.id, "GL Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=3_000_000_00,
    ).ok
    assert sync.add_layer(
        conn, placement.id, "Umbrella Both", ["gl", "cy"],
        attach_cents=5_000_000_00, limit_cents=5_000_000_00,
    ).ok

    diags = sync.set_applies_to(conn, placement.id, "umbrella-both", ["gl"])

    assert diags.ok, diags.errors
    program = load_program(path)
    layer = next(ly for ly in program.layers if ly.id == "umbrella-both")
    assert layer.applies_to == ["gl"]


def test_set_applies_to_refuses_a_move_that_overlaps(linked) -> None:
    """Spanning the gl primary across cy overlaps cy's own primary — the
    refusal, in towerkit's words with nothing written, is the point."""
    conn, _, placement, path = linked
    before = path.read_bytes()

    diags = sync.set_applies_to(conn, placement.id, "primary-gl", ["gl", "cy"])

    assert not diags.ok
    assert any("OVERLAP" in d.message for d in diags.errors)
    assert path.read_bytes() == before


def test_set_applies_to_refuses_an_empty_set(linked) -> None:
    conn, _, placement, path = linked
    before = path.read_bytes()

    diags = sync.set_applies_to(conn, placement.id, "primary-gl", [])

    assert not diags.ok
    assert path.read_bytes() == before


def test_statutory_on_forces_the_limit_to_zero(linked) -> None:
    """'Fully built but not accessible' (Grant, 2026-08-19): statutory was
    modelled, projected, rendered and never writable outside towerkit's own
    editor. On: benefits, no dollar limit — the model's own rule."""
    conn, _, placement, path = linked

    diags = sync.set_statutory(conn, placement.id, "primary-cy", True)

    assert diags.ok, diags.errors
    layer = next(ly for ly in load_program(path).layers if ly.id == "primary-cy")
    assert layer.statutory is True
    assert layer.limit == 0


def test_statutory_off_requires_the_replacing_limit(linked) -> None:
    conn, _, placement, path = linked
    assert sync.set_statutory(conn, placement.id, "primary-cy", True).ok
    before = path.read_bytes()

    refused = sync.set_statutory(conn, placement.id, "primary-cy", False)

    assert not refused.ok
    assert path.read_bytes() == before

    diags = sync.set_statutory(
        conn, placement.id, "primary-cy", False, limit_cents=5_000_000_00
    )
    assert diags.ok, diags.errors
    layer = next(ly for ly in load_program(path).layers if ly.id == "primary-cy")
    assert layer.statutory is False
    assert layer.limit == 5_000_000


def test_follows_underlying_hands_the_attachment_to_the_tower(linked) -> None:
    conn, _, placement, path = linked
    assert sync.add_layer(
        conn, placement.id, "GL Excess", ["gl"],
        attach_cents=2_000_000_00, limit_cents=3_000_000_00,
    ).ok

    diags = sync.set_follows_underlying(conn, placement.id, "gl-excess", True)

    assert diags.ok, diags.errors
    # raise the primary; the excess must follow on the next write's heal
    assert sync.update_layer(
        conn, placement.id, "primary-gl", limit_cents=3_000_000_00
    ).ok
    layer = next(ly for ly in load_program(path).layers if ly.id == "gl-excess")
    assert layer.follows_underlying is True
    assert layer.attach == 3_000_000, "heal_follows did not re-seat the follower"


# --- phase 4: retentions, sublimits, line order, restack -----------------------


def test_retention_lifecycle_add_edit_remove(linked) -> None:
    conn, _, placement, path = linked

    diags = sync.add_retention(
        conn, placement.id, ["gl"], "deductible", amount_cents=250_000_00
    )
    assert diags.ok, diags.errors
    program = load_program(path)
    index = len(program.retentions) - 1
    assert program.retentions[index].amount == 250_000
    assert str(program.retentions[index].type) == "deductible"

    diags = sync.edit_retention(
        conn, placement.id, index, type="sir", amount_cents=500_000_00
    )
    assert diags.ok, diags.errors
    retention = load_program(path).retentions[index]
    assert retention.amount == 500_000
    assert str(retention.type) == "sir"
    assert retention.applies_to == ["gl"], "an untouched field moved"

    count_before = len(load_program(path).retentions)
    assert sync.remove_retention(conn, placement.id, index).ok
    assert len(load_program(path).retentions) == count_before - 1


def test_retention_bad_index_refuses_with_file_untouched(linked) -> None:
    conn, _, placement, path = linked
    before = path.read_bytes()

    diags = sync.edit_retention(conn, placement.id, 99, amount_cents=1_00)

    assert not diags.ok
    assert path.read_bytes() == before


def test_retention_sub_dollar_amount_is_refused(linked) -> None:
    conn, _, placement, path = linked
    before = path.read_bytes()

    diags = sync.add_retention(conn, placement.id, ["gl"], "deductible", amount_cents=250_000_50)

    assert not diags.ok
    assert path.read_bytes() == before


def test_sublimit_lifecycle(linked) -> None:
    conn, _, placement, path = linked

    assert sync.add_sublimit(
        conn, placement.id, "Flood", 1_000_000_00, ["gl"]
    ).ok
    program = load_program(path)
    index = len(program.sublimits) - 1
    assert program.sublimits[index].name == "Flood"
    assert program.sublimits[index].amount == 1_000_000

    assert sync.edit_sublimit(
        conn, placement.id, index, amount_cents=2_000_000_00
    ).ok
    assert load_program(path).sublimits[index].amount == 2_000_000
    assert load_program(path).sublimits[index].name == "Flood"

    assert sync.remove_sublimit(conn, placement.id, index).ok
    assert all(s.name != "Flood" for s in load_program(path).sublimits)


def test_move_line_reorders_and_off_the_end_is_a_noop(linked) -> None:
    conn, _, placement, path = linked
    order = [ln.id for ln in load_program(path).lines]
    assert len(order) >= 2

    assert sync.move_line(conn, placement.id, order[0], +1).ok
    moved = [ln.id for ln in load_program(path).lines]
    assert moved[0] == order[1] and moved[1] == order[0]

    # off the end: towerkit's contract is a no-op, not an error
    assert sync.move_line(conn, placement.id, moved[-1], +1).ok
    assert [ln.id for ln in load_program(path).lines] == moved


def test_program_terms_reads_what_the_editors_need(linked) -> None:
    conn, _, placement, path = linked
    assert sync.add_retention(
        conn, placement.id, ["gl"], "deductible", amount_cents=250_000_00
    ).ok
    assert sync.add_sublimit(conn, placement.id, "Flood", 1_000_000_00, ["gl"]).ok

    terms = sync.program_terms(conn, placement.id)

    retention = terms["retentions"][-1]
    assert retention["type"] == "deductible"
    assert retention["amount_cents"] == 250_000_00
    assert retention["applies_to"] == ["gl"]
    sublimit = terms["sublimits"][-1]
    assert sublimit["name"] == "Flood"
    assert sublimit["amount_cents"] == 1_000_000_00


# --- the worksheet's structural verbs (program-worksheet redesign, 2026-08-24)


def test_move_layer_swaps_neighbours_and_reseats_the_column(linked) -> None:
    """Position is the attachment, so a move is a swap plus a reseat — one
    mutation, the same rule as insert_layer, or the half-shifted tower is
    refused by the validator before it ever reaches disk."""
    conn, _, placement, path = linked
    assert sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=None, limit_cents=10_000_000_00,
    ).ok
    assert sync.add_layer(
        conn, placement.id, "2nd Excess", ["gl"],
        attach_cents=None, limit_cents=5_000_000_00,
    ).ok

    diags = sync.move_layer(conn, placement.id, "2nd-excess", direction="down")
    assert diags.ok, [d.message for d in diags.errors]

    program = load_program(path)
    by_id = {ly.id: ly for ly in program.layers}
    # The swap: 2nd Excess now sits where 1st Excess did, and the column is
    # contiguous — every attachment recomputed, not just the moved slab's.
    assert by_id["2nd-excess"].attach == 2_000_000
    assert by_id["1st-excess"].attach == 7_000_000
    assert by_id["1st-excess"].attach == by_id["2nd-excess"].attach + by_id["2nd-excess"].limit


def test_move_layer_off_the_end_is_refused_not_ignored(linked) -> None:
    """write_through dumps on success, so a tolerated no-op move would rewrite
    the file and log an event for nothing. A refusal says something."""
    conn, _, placement, path = linked
    before = path.read_text()
    diags = sync.move_layer(conn, placement.id, "primary-gl", direction="down")
    assert not diags.ok
    assert "bottom" in diags.errors[0].message
    assert path.read_text() == before


def test_split_layer_makes_two_slabs_in_the_same_band(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    assert sync.set_applies_to(conn, placement.id, layer_id, ["gl", "cy"]).ok
    assert sync.update_layer(
        conn, placement.id, layer_id, premium_cents=3_000_000_00
    ).ok

    diags = sync.split_layer(
        conn, placement.id, layer_id,
        move_line_ids=["cy"], new_name="Cyber 2nd Excess",
        kept_premium_cents=2_400_000_00, moved_premium_cents=600_000_00,
    )
    assert diags.ok, [d.message for d in diags.errors]

    program = load_program(path)
    original = next(ly for ly in program.layers if ly.id == layer_id)
    split = next(ly for ly in program.layers if ly.name == "Cyber 2nd Excess")
    # Same band: attachment and limit unchanged on both sides of the split.
    assert split.attach == original.attach and split.limit == original.limit
    assert original.applies_to == ["gl"] and split.applies_to == ["cy"]
    # The new slab arrives unplaced and the premium division totals exactly.
    assert split.participants == []
    assert original.premium == 2_400_000 and split.premium == 600_000


def test_split_layer_premiums_must_total_the_original(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    assert sync.set_applies_to(conn, placement.id, layer_id, ["gl", "cy"]).ok
    assert sync.update_layer(
        conn, placement.id, layer_id, premium_cents=3_000_000_00
    ).ok
    before = path.read_text()

    diags = sync.split_layer(
        conn, placement.id, layer_id,
        move_line_ids=["cy"], new_name="Cyber 2nd Excess",
        kept_premium_cents=2_400_000_00, moved_premium_cents=500_000_00,
    )
    assert not diags.ok
    assert "must total" in diags.errors[0].message
    assert "$3,000,000" in diags.errors[0].message
    assert path.read_text() == before


def test_split_layer_refuses_to_move_every_line(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    assert sync.set_applies_to(conn, placement.id, layer_id, ["gl", "cy"]).ok
    before = path.read_text()

    diags = sync.split_layer(
        conn, placement.id, layer_id,
        move_line_ids=["gl", "cy"], new_name="Everything",
    )
    assert not diags.ok
    assert "rename it instead" in diags.errors[0].message
    assert path.read_text() == before


def test_preview_projects_without_writing(linked) -> None:
    """The dry-run behind the worksheet's consequence blocks: same guards,
    same heal, same validation — and the file bytes untouched."""
    conn, _, placement, path = linked
    before = path.read_text()

    def mutation(program) -> None:
        layer = next(ly for ly in program.layers if ly.id == "primary-gl")
        layer.limit = 4_000_000

    program, diags = sync.preview(conn, placement.id, mutation)
    assert program is not None
    assert diags.ok, [d.message for d in diags.errors]
    projected = next(ly for ly in program.layers if ly.id == "primary-gl")
    assert projected.limit == 4_000_000
    assert path.read_text() == before  # projected, never saved


def test_preview_surfaces_a_refusal_the_same_way_the_write_would(linked) -> None:
    conn, _, placement, path = linked

    def mutation(program) -> None:
        raise ValueError("no")

    program, diags = sync.preview(conn, placement.id, mutation)
    assert program is None
    assert not diags.ok and diags.errors[0].message == "no"


def test_layer_details_derives_open_capacity_and_the_dollar_columns(linked) -> None:
    """The worksheet's derived $ columns and the open-capacity row are computed
    ONCE, here, beside the signed figure they complement — no surface
    multiplies money (program-worksheet redesign, 2026-08-24)."""
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)  # Chubb at 50% of $10M

    row = next(d for d in sync.layer_details(conn, placement.id) if d["id"] == layer_id)
    assert row["top_cents"] == row["attach_cents"] + row["limit_cents"]
    assert row["open_pct"] == 50.0
    assert row["open_limit_cents"] == 5_000_000_00
    seat = row["participants"][0]
    assert seat["carrier"] == "Chubb"
    assert seat["limit_cents"] == 5_000_000_00


def test_share_preview_projects_the_signed_figure_without_writing(linked) -> None:
    """The worksheet's write preview: same guards as the commit, the would-be
    signed figure and the dollars still open — file untouched."""
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)  # Chubb at 50% of $10M
    before = path.read_text()

    result = sync.share_preview(conn, placement.id, layer_id, "Chubb", 8000)

    assert result["ok"], result["errors"]
    assert result["share_was_pct"] == 50.0 and result["share_pct"] == 80.0
    assert result["signed_pct"] == 80.0
    assert result["open_limit_cents"] == 2_000_000_00
    assert path.read_text() == before


def test_share_preview_reports_a_refusal_in_towerkits_words(linked) -> None:
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    before = path.read_text()

    result = sync.share_preview(conn, placement.id, layer_id, "Chubb", 15_000)

    assert not result["ok"]
    assert result["errors"], "an oversigned preview said nothing"
    assert path.read_text() == before


def test_rescope_preview_states_what_the_dropped_line_keeps(linked) -> None:
    """Design 3B: 'Crime would be left with $10,000,000 of cover and nothing
    above it' — derived from the projected program, not composed twice."""
    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    assert sync.set_applies_to(conn, placement.id, layer_id, ["gl", "cy"]).ok
    before = path.read_text()

    result = sync.rescope_preview(conn, placement.id, layer_id, ["gl"])

    assert result["dropped"], "nothing reported for the dropped line"
    dropped = result["dropped"][0]
    assert dropped["line_id"] == "cy"
    # cy keeps its primary ($5M); the 2nd excess above it is what leaves.
    assert dropped["left_with_cents"] == 5_000_000_00
    assert dropped["was_top"] is True
    assert result["keeps"] == ["GL"]
    assert path.read_text() == before


def test_move_layer_up_swaps_the_other_way_and_the_top_is_refused(linked) -> None:
    """The review found only 'down' covered — a direction swap in move_layer
    would ship silently (C25). Both directions, both edges."""
    conn, _, placement, path = linked
    assert sync.add_layer(
        conn, placement.id, "1st Excess", ["gl"],
        attach_cents=None, limit_cents=10_000_000_00,
    ).ok

    up = sync.move_layer(conn, placement.id, "primary-gl", direction="up")
    assert up.ok, [d.message for d in up.errors]
    program = load_program(path)
    by_id = {ly.id: ly for ly in program.layers}
    assert by_id["1st-excess"].attach == 0, "up did not move the slab up"
    assert by_id["primary-gl"].attach == 10_000_000

    before = path.read_text()
    top = sync.move_layer(conn, placement.id, "primary-gl", direction="up")
    assert not top.ok
    assert "top" in top.errors[0].message
    assert path.read_text() == before


def test_split_layer_keeps_a_buffer_a_buffer(linked) -> None:
    """A buffer split by line is still a buffer on both sides — dropping the
    flag turned a chosen uninsured band into 'To be placed' (review C4)."""
    from towerkit.model import dump_program

    conn, _, placement, path = linked
    layer_id = _seat(conn, placement, path)
    assert sync.set_applies_to(conn, placement.id, layer_id, ["gl", "cy"]).ok
    assert sync.remove_participant(conn, placement.id, layer_id, "Chubb").ok
    # flip the flag on disk directly — buffers are built via insert_layer in
    # the app, but this test needs an existing multi-line buffer
    program = load_program(path)
    target = next(ly for ly in program.layers if ly.id == layer_id)
    target.buffer = True
    target.premium = None
    dump_program(program, path)
    assert sync.project(conn, path).ok

    diags = sync.split_layer(
        conn, placement.id, layer_id,
        move_line_ids=["cy"], new_name="Cyber Buffer",
    )
    assert diags.ok, [d.message for d in diags.errors]
    program = load_program(path)
    split = next(ly for ly in program.layers if ly.name == "Cyber Buffer")
    assert split.buffer is True, "the split half stopped being a buffer"
    original = next(ly for ly in program.layers if ly.id == layer_id)
    assert original.buffer is True


def test_split_layer_refuses_statutory(linked) -> None:
    """Statutory cover is one benefit scheme, not a band that splits — the
    refusal says so instead of failing sideways on the limit (review C4)."""
    from towerkit.model import dump_program

    conn, _, placement, path = linked
    # a statutory layer on its own line, written directly (the guarded seams
    # are not the thing under test here)
    program = load_program(path)
    from towerkit.model import Layer, Line

    program.lines.append(Line(id="wc", name="Workers Comp", abbr="WC"))
    program.layers.append(
        Layer(
            id="wc-a", name="WC Part A", applies_to=["wc"],
            attach=0, limit=0, statutory=True, participants=[],
        )
    )
    dump_program(program, path)
    assert sync.project(conn, path).ok
    before = path.read_text()

    diags = sync.split_layer(
        conn, placement.id, "wc-a", move_line_ids=["wc"], new_name="Part B"
    )
    assert not diags.ok
    assert "statutory" in diags.errors[0].message
    assert path.read_text() == before
