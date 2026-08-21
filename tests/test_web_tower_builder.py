"""Building a tower in the browser: the stack, not the whole program.

Spec: docs/superpowers/specs/2026-08-21-web-tower-builder-design.md
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import sync
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o for o in orgs.list_orgs(conn, kind="client")
        if [p for p in placements.for_org(conn, o.id) if p.program_path]
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _linked(conn, org):
    from bookkit.repo import placements

    return next(p for p in placements.for_org(conn, org.id) if p.program_path)


def test_layer_details_reports_whether_a_slab_is_a_buffer(app_and_org) -> None:
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)

    rows = sync.layer_details(conn, placement.id)

    assert rows, "fixture drifted — no layers"
    assert all("buffer" in row for row in rows)
    assert not any(row["buffer"] for row in rows)


def _stack_of(conn, placement, line_id):
    program = sync.linked_program(conn, placement.id).program
    return [
        (ly.id, ly.attach, ly.top)
        for ly in program.layers_for_line(line_id)
    ]


def test_inserting_above_seats_on_the_slab_below(app_and_org) -> None:
    """Attachment is COMPUTED. There is no attachment to type, which is what
    makes Grant's overlap unconstructible rather than merely detectable."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    bottom = program.layers_for_line(line_id)[0]
    before_ids = {row[0] for row in _stack_of(conn, placement, line_id)}

    diags = sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=bottom.id,
        position="above", name="Test Excess", limit_cents=10_000_000_00,
    )

    assert diags.ok, [d.message for d in diags.errors]
    stack = _stack_of(conn, placement, line_id)
    # Found by SET DIFFERENCE against the ids that were there before. Picking
    # "the first row that is not the anchor" would return a pre-existing layer
    # on any line with more than one slab, and then assert something true about
    # the wrong row (caught in review of the plan, ruling R5).
    new_ids = {row[0] for row in stack} - before_ids
    assert len(new_ids) == 1, new_ids
    inserted = next(row for row in stack if row[0] in new_ids)
    assert inserted[1] == bottom.top, "the new slab did not seat on the one below"


def test_inserting_mid_stack_pushes_everything_above_it_up(app_and_org) -> None:
    """And does it in ONE mutation, so write_through — which only accepts a
    file that validates — never sees a half-shifted tower."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    before = _stack_of(conn, placement, line_id)
    if len(before) < 2:
        pytest.skip("this line has no stack to insert into")
    bottom_id, _, bottom_top = before[0]

    diags = sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=bottom_id,
        position="above", name="Wedge", limit_cents=1_000_000_00,
    )

    assert diags.ok, [d.message for d in diags.errors]
    after = _stack_of(conn, placement, line_id)
    assert len(after) == len(before) + 1
    # no two slabs share an attachment — the invariant, stated directly
    attaches = [row[1] for row in after]
    assert len(attaches) == len(set(attaches))


def test_the_file_is_never_written_with_an_overlap(app_and_org) -> None:
    """THE REGRESSION, at the source. Grant's tower had two slabs at one
    attachment; the editor cannot produce that state."""
    from towerkit.validate import validate_program

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[0]

    for n in range(3):
        diags = sync.insert_layer(
            conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
            position="above", name=f"Excess {n}", limit_cents=5_000_000_00,
        )
        # ASSERT THE WRITE HAPPENED. Without this the test passes when every
        # insert is REFUSED — nothing was written, so of course nothing
        # overlaps. It did exactly that until the implementer noticed
        # (2026-08-21): a green test proving only that the feature did not run.
        assert diags.ok, [d.message for d in diags.errors]

    fresh = sync.linked_program(conn, placement.id).program
    overlaps = [
        d for d in validate_program(fresh).errors if d.code == "line-overlap"
    ]
    assert not overlaps


def test_a_buffer_is_inserted_with_no_carriers_and_no_premium(app_and_org) -> None:
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[0]

    diags = sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
        position="above", name="Buffer", limit_cents=5_000_000_00, buffer=True,
    )

    assert diags.ok, [d.message for d in diags.errors]
    fresh = sync.linked_program(conn, placement.id).program
    buf = next(ly for ly in fresh.layers if ly.buffer)
    assert not buf.participants
    assert not buf.premium


def test_one_insert_is_one_undo_unit(app_and_org) -> None:
    from bookkit.repo import batches as batches_repo

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[0]
    before = len(_stack_of(conn, placement, line_id))

    from bookkit.services import batches as batches_svc
    from bookkit.services import program_files

    program_files.write(
        conn, placement, tool="program_layer_add", summary="insert",
        mutate=lambda: sync.insert_layer(
            conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
            position="above", name="Once", limit_cents=1_000_000_00,
        ),
        open_batch=lambda c, **kw: batches_svc.open_batch(c, source="web", **kw),
    )

    batches = batches_repo.recent(conn, "0000", limit=2)
    assert len(batches) == 1, [b.tool for b in batches]
    assert len(_stack_of(conn, placement, line_id)) == before + 1


def test_above_and_below_put_the_slab_on_different_sides_of_the_anchor(
    app_and_org,
) -> None:
    """POSITION IS THE STRUCTURE, so it has to be tested AS structure.

    Every other assertion here checks attachments are unique and contiguous,
    and those hold no matter WHERE a slab lands — a mutation replacing the
    ordered insert with `order.append(layer)` left them all green
    (found 2026-08-21). This one fails on that mutation, because it asks the
    only question that distinguishes the two: which side of the anchor.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[0]

    before = {ly.id for ly in program.layers_for_line(line_id)}
    assert sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
        position="above", name="Sits Above", limit_cents=1_000_000_00,
    ).ok
    stack = sync.linked_program(conn, placement.id).program.layers_for_line(line_id)
    ids = [ly.id for ly in stack]
    above_id = next(i for i in ids if i not in before)
    assert ids.index(above_id) == ids.index(anchor.id) + 1, (
        f"'above' did not put it directly above the anchor: {ids}"
    )

    before = set(ids)
    assert sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
        position="below", name="Sits Below", limit_cents=1_000_000_00,
    ).ok
    stack = sync.linked_program(conn, placement.id).program.layers_for_line(line_id)
    ids = [ly.id for ly in stack]
    below_id = next(i for i in ids if i not in before)
    assert ids.index(below_id) == ids.index(anchor.id) - 1, (
        f"'below' did not put it directly below the anchor: {ids}"
    )


def _insert_url(org, placement, line_id):
    return (
        f"/accounts/{org.ref}/program/{placement.id}/lines/{line_id}/layers"
    )


def test_the_route_inserts_and_answers_with_the_panel(app_and_org) -> None:
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[0]

    done = client.post(
        _insert_url(org, placement, line_id),
        data={"name": "New Excess", "limit_cents": "10m",
              "anchor": anchor.id, "position": "above", "kind": "layer"},
    )

    assert done.status_code == 200
    assert "New Excess" in done.text


def test_a_refused_write_comes_back_as_the_panel_not_a_status_code(
    app_and_org,
) -> None:
    """htmx swaps nothing on a 4xx or a 5xx, so a route that refuses with a
    status leaves a control that looks simply dead.

    The refusal has to come from the WRITE, not from parsing: a sub-dollar
    limit parses to 150 cents perfectly well and is refused inside the mutation
    by towerkit's whole-dollar rule. An unparseable limit tests the parser's
    try/except instead, and passed with the write's own arm deleted (mutation,
    2026-08-21).
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id

    refused = client.post(
        _insert_url(org, placement, line_id),
        data={"name": "Sub Dollar", "limit_cents": "1.50",
              "anchor": "", "position": "above", "kind": "layer"},
    )

    assert refused.status_code == 200
    assert "Sub Dollar" not in refused.text
    assert "dollar" in refused.text.lower() or "limit" in refused.text.lower()


def test_an_unparseable_limit_is_refused_before_the_write(app_and_org) -> None:
    """The other half: the parse arm, kept as its own test now that the one
    above no longer covers it by accident."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id

    refused = client.post(
        _insert_url(org, placement, line_id),
        data={"name": "Bad", "limit_cents": "not a number",
              "anchor": "", "position": "above", "kind": "layer"},
    )

    assert refused.status_code == 200
    assert "Bad" not in refused.text


def _other_placement_layer_id(conn, placement):
    """A REAL layer id that belongs to some other placement in the book.

    The point of the route's anchor guard is not to reject nonsense — `sync`
    rejects nonsense by itself. It is to reject a well-formed id that names a
    layer on somebody ELSE's tower, which is what a body-supplied id lets a
    caller try.

    DEVIATION FROM THE BRIEF'S VERBATIM HELPER, and why: every seeded program
    in this book uses the same four layer names (Primary GL/AL/IM, Umbrella),
    so towerkit's name-derived slugs collide across EVERY placement — the id
    picked from "some other placement" (e.g. 'primary-im') is, by name alone,
    ALSO already a member of THIS placement's own `known` set (just on a
    different line). The brief's `rows[0]["id"]` version returns such a
    colliding id every time in this fixture, so the guard's `anchor not in
    known` check trivially passes and the scenario it is meant to test never
    happens — confirmed by running it and getting sync.insert_layer's OWN
    "no layer '...' on gl" message instead of the route's "reload the tab"
    one. A layer with a name no seeded program uses is inserted on the other
    placement so its id genuinely cannot collide."""
    from bookkit.repo import placements as placements_repo

    other = next(p for p in placements_repo.all_linked(conn) if p.id != placement.id)
    other_program = sync.linked_program(conn, other.id).program
    other_line_id = other_program.lines[0].id
    diags = sync.insert_layer(
        conn, other.id, line_id=other_line_id, anchor_layer_id=None,
        position="above", name="Stolen Anchor Zzyzx", limit_cents=1_000_000_00,
    )
    assert diags.ok, [d.message for d in diags.errors]
    fresh = sync.linked_program(conn, other.id).program
    stolen = next(ly for ly in fresh.layers if ly.name == "Stolen Anchor Zzyzx")
    return stolen.id


def test_an_anchor_from_another_placement_is_refused(app_and_org) -> None:
    """The anchor arrives in the BODY, and a body id is only checked if
    somebody checks it.

    The refusal must come from the ROUTE, before any write is attempted — so
    the assertion is on the route's own words. `sync.insert_layer` would also
    refuse this id, which is exactly why an assertion on "no layer" alone
    passed with the guard deleted (mutation, 2026-08-21).
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    stolen = _other_placement_layer_id(conn, placement)
    assert stolen is not None, "fixture drifted — no second linked placement"

    refused = client.post(
        _insert_url(org, placement, line_id),
        data={"name": "Sneaky", "limit_cents": "1m",
              "anchor": stolen, "position": "above", "kind": "layer"},
    )

    assert refused.status_code == 200
    assert "reload the tab" in refused.text, refused.text[:400]
    assert placement.ref in refused.text
    assert "Sneaky" not in refused.text


def test_another_accounts_program_is_a_404(app_and_org) -> None:
    from bookkit.repo import orgs

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    other = next(
        o for o in orgs.list_orgs(conn, kind="client") if o.id != org.id
    )

    got = client.post(
        f"/accounts/{other.ref}/program/{placement.id}/lines/{line_id}/layers",
        data={"name": "x", "limit_cents": "1m", "anchor": "",
              "position": "above", "kind": "layer"},
    )

    assert got.status_code == 404


def test_an_unrecognised_kind_is_refused_before_the_write(app_and_org) -> None:
    """`position` is checked (sync.insert_layer raises on anything but
    "above"/"below"); `kind` was not — anything but the literal string
    "buffer" silently coerced to a plain layer. Fails safe, but the
    project's rule is that a constrained field is checked SERVER-SIDE too:
    "markup constrains a mouse and nothing else." A refusal names the fix,
    so the message names both legal values."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    before = len(_stack_of(conn, placement, line_id))

    refused = client.post(
        _insert_url(org, placement, line_id),
        data={"name": "Sneaky Kind", "limit_cents": "1m", "anchor": "",
              "position": "above", "kind": "sneaky"},
    )

    assert refused.status_code == 200
    assert "Sneaky Kind" not in refused.text
    assert "layer" in refused.text.lower() and "buffer" in refused.text.lower()
    assert len(_stack_of(conn, placement, line_id)) == before
