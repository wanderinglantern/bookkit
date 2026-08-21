"""Building a tower in the browser: the stack, not the whole program.

Spec: docs/superpowers/specs/2026-08-21-web-tower-builder-design.md
"""

from __future__ import annotations

import re
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

    A REFUSAL KEEPS THE TYPING (spec, section 2; whole-branch review finding
    1, 2026-08-21). This test used to assert the opposite — that "Sub Dollar"
    was GONE from the response — which is what the bug looked like, not what
    the spec asks for: `_programs_panel` blanked every stack editor on the
    page. Now it proves the typed name survives the refusal, in the ONE
    fragment answer the route returns.
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
    assert "Sub Dollar" in refused.text, "the typed name did not survive the refusal"
    assert "dollar" in refused.text.lower() or "limit" in refused.text.lower()


def test_an_unparseable_limit_is_refused_before_the_write(app_and_org) -> None:
    """The other half: the parse arm, kept as its own test now that the one
    above no longer covers it by accident.

    Also inverted with the three the whole-branch review named (finding 1):
    the parse-error branch answers through the same `refused()` helper in
    `stack_insert` as every other refusal, so it keeps the typing too — a
    fourth instance of the same wrong assertion the review's three examples
    had, caught by making the fix uniform across every refusal branch rather
    than patching only the branches the review happened to quote.
    """
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
    assert "Bad" in refused.text, "the typed name did not survive the refusal"


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

    A REFUSAL KEEPS THE TYPING (spec, section 2; whole-branch review finding
    1). This used to assert "Sneaky" was gone, which was the bug, not the
    spec — inverted to prove the typed name survives alongside the route's
    own refusal message.
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
    assert "Sneaky" in refused.text, "the typed name did not survive the refusal"


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
    so the message names both legal values.

    A REFUSAL KEEPS THE TYPING (spec, section 2; whole-branch review finding
    1). Inverted from asserting "Sneaky Kind" was gone (the bug) to asserting
    it survived (the spec), alongside the still-unwritten stack and the
    still-present message naming both legal values."""
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
    assert "Sneaky Kind" in refused.text, "the typed name did not survive the refusal"
    assert "layer" in refused.text.lower() and "buffer" in refused.text.lower()
    assert len(_stack_of(conn, placement, line_id)) == before


def test_the_stack_editor_has_no_attachment_input(app_and_org) -> None:
    """THE DESIGN, asserted. An attachment input is how two slabs come to
    share one; there is not one to fill in.

    BOUNDED AT BOTH ENDS (nit 4, whole-branch review 2026-08-21): this used
    to slice from `stack-editor` to the end of the WHOLE PAGE, which passed
    for an accidental reason — the pre-existing "Add layer" form (which used
    to carry `attach_cents`) is fetched on demand via `hx-get` and was never
    present in the base page HTML at all, not because the slice was actually
    scoped to the editor. `_stack_editor_markup` bounds the end at the next
    sibling section instead, and the "insert buffer" check proves that slice
    is not vacuous before trusting the negative assertion below it. See
    `test_no_attachment_input_survives_anywhere_on_the_program_tab` for the
    claim the spec actually makes (finding 2, same review): that form still
    exists — it is the only web control that can add a layer across a whole
    multi-line program or price one at creation — but no longer takes a typed
    attachment either.
    """
    client, org = app_and_org
    page = client.get(f"/accounts/{org.ref}/program").text
    editor = _stack_editor_markup(page)

    # Not vacuous: reaches the insert form's own LAST control.
    assert "insert buffer" in editor

    assert 'name="attach' not in editor
    assert 'name="anchor"' in editor
    assert 'name="position"' in editor


def test_no_attachment_input_survives_anywhere_on_the_program_tab(app_and_org) -> None:
    """THE SPEC'S OWN CLAIM, checked at the surface, not just the editor
    (finding 2, whole-branch review 2026-08-21): "the overlap is
    unconstructible" has to be true of the whole Program tab, not merely of
    `sync.insert_layer`'s own path. The pre-existing "Add layer" button sat
    on the same panel, wired to a form that still took a typed `attach_cents`
    — the exact control shape ("add a layer" plus an attachment box) that
    drew two D&O excess layers on top of each other in the first place.

    That form is NOT deleted: it is the only web control that can add a
    layer across every line of a multi-line program in one call, or price a
    layer at creation, neither of which `sync.insert_layer` does (checked
    before removing anything, per the review's instruction not to assume
    superseded). Only the typed attachment came out of it
    (`_layer_add_fields`; `sync.add_layer(attach_cents=None, ...)` leaves
    towerkit's own suggested-attach standing). Checked at both surfaces the
    button offers: the base page load, and the form itself once opened —
    the base page alone would pass before AND after this fix, since the
    form's fields only exist once its own `hx-get` is followed.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)

    page = client.get(f"/accounts/{org.ref}/program").text
    assert 'name="attach' not in page, "an attachment input is on the base page"

    add_form = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/layers/new"
    ).text
    # Not vacuous: the surviving fields are still there, proving this is the
    # real "Add layer" form and not an empty or 404 response.
    assert 'name="name"' in add_form
    assert 'name="line"' in add_form
    assert 'name="limit_cents"' in add_form
    assert 'name="premium_cents"' in add_form
    assert 'name="attach' not in add_form, (
        "the 'Add layer' form still takes a typed attachment"
    )


def _slab_blocks(editor: str) -> str:
    """Every `<div class="slab...">` in the editor, concatenated — each
    bounded by the next slab or by the stack's own `<form class="stack-insert">`,
    whichever comes first. "+ carrier" living somewhere in the page proves
    nothing about WHERE it lives; this proves it is inside a slab and not,
    say, the insert form."""
    blocks = []
    start = editor.find('<div class="slab')
    while start != -1:
        next_slab = editor.find('<div class="slab', start + 1)
        next_form = editor.find('<form class="stack-insert"', start + 1)
        candidates = [c for c in (next_slab, next_form) if c != -1]
        stop = min(candidates) if candidates else len(editor)
        blocks.append(editor[start:stop])
        start = next_slab
    return "\n".join(blocks)


def _stack_insert_form(editor: str) -> str:
    """The stack's own insert form, bounded by its own `</form>` — it does
    not nest another form, so the naive index-of-close is exact."""
    start = editor.index('<form class="stack-insert"')
    stop = editor.index("</form>", start) + len("</form>")
    return editor[start:stop]


def test_add_carrier_sits_on_the_slab_and_add_layer_on_the_stack(
    app_and_org
) -> None:
    """The whole fix for the reported bug: sharing a slab and adding a layer
    are visibly different acts, in different places.

    Scoped to WHERE each control lives, not merely that its words appear
    somewhere on the page — "+ carrier" anywhere in the document would have
    passed even sitting on the stack's own form."""
    client, org = app_and_org
    page = client.get(f"/accounts/{org.ref}/program").text
    editor = page[page.index("stack-editor") :]

    assert "+ carrier" in _slab_blocks(editor)
    form = _stack_insert_form(editor)
    assert "insert layer" in form or "+ layer" in form
    assert "insert buffer" in form


def test_a_multi_line_layer_says_it_appears_in_other_stacks(
    app_and_org
) -> None:
    """A layer spanning three lines appears in three stacks and an edit in one
    moves all of them. The row says so rather than hiding it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    shared = [ly for ly in program.layers if len(ly.applies_to) > 1]
    assert shared, "fixture drifted — no multi-line layer to warn about"

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "also on" in page


def _arrange_three_single_line_slabs(conn, placement, line_id):
    """The seeded first line (gl) carries only Primary GL (single-line) and
    Umbrella (spans gl+al). Umbrella is FOLLOWS-UNDERLYING once anything is
    inserted beneath it (`sync.insert_layer` sets `follows_underlying` on any
    multi-line slab in the column, 2026-08-21), so removing the slab directly
    below it makes Umbrella legitimately RESEAT via `heal_follows` — that is
    not the bug this feature exists to catch, and asserting against it would
    prove nothing (confirmed by running it: Umbrella's attach moved from 7M
    back to 2M, no gap, because it closed correctly onto its true underlying).

    So two inserts, not one: Mid 1 seats on Primary GL, Mid 2 seats on Mid 1.
    Mid 2 is single-line and NOT follows-underlying, so removing Mid 1 (its
    neighbour below) cannot legitimately move it — any movement there is the
    silent-reseat bug. `sync.insert_layer` is Task 4's proven verb; using it
    twice is still one honest way to reach a three-slab stack, not a shortcut
    around the invariant being tested."""
    program = sync.linked_program(conn, placement.id).program
    stack = program.layers_for_line(line_id)
    bottom = stack[0]

    diags = sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=bottom.id,
        position="above", name="Mid 1", limit_cents=3_000_000_00,
    )
    assert diags.ok, [d.message for d in diags.errors]
    mid1 = next(
        ly for ly in sync.linked_program(conn, placement.id).program.layers_for_line(line_id)
        if ly.name == "Mid 1"
    )

    diags = sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=mid1.id,
        position="above", name="Mid 2", limit_cents=2_000_000_00,
    )
    assert diags.ok, [d.message for d in diags.errors]

    fresh = sync.linked_program(conn, placement.id).program
    stack = fresh.layers_for_line(line_id)
    middle = next(ly for ly in stack if ly.name == "Mid 1")
    above = next(ly for ly in stack if ly.name == "Mid 2")
    assert not above.follows_underlying, "picked the wrong neighbour — it can legitimately reseat"
    return middle, above


def test_removing_a_mid_stack_slab_leaves_the_gap(app_and_org) -> None:
    """Closing the tower up silently would MOVE cover the client bought. The
    gap is true, and the diagnostics strip says so."""
    from towerkit.validate import validate_program

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id

    middle, above = _arrange_three_single_line_slabs(conn, placement, line_id)
    above_attach_before = above.attach

    resp = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers/{middle.id}/remove"
    )
    assert resp.status_code == 200

    fresh = sync.linked_program(conn, placement.id).program
    remaining = fresh.layers_for_line(line_id)
    # 1. the removed slab is gone
    assert all(ly.id != middle.id for ly in remaining)
    # 2. the slab above did NOT move down — the value it had BEFORE the
    # removal, not merely "some slab still sits at the old attachment".
    survivor = next(ly for ly in remaining if ly.id == above.id)
    assert survivor.attach == above_attach_before, (
        "the tower closed up and moved cover the client bought"
    )
    # 3. the file is still saveable — a gap is STATED, not refused
    diags = validate_program(fresh)
    assert diags.ok, [d.message for d in diags.errors]
    gaps = [d for d in diags.warnings if d.code == "line-gap" and d.ref == ("line", line_id)]
    assert gaps, [d.code for d in diags.items]


def test_the_remove_confirm_says_a_gap_will_be_left(app_and_org) -> None:
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    middle, _above = _arrange_three_single_line_slabs(conn, placement, line_id)

    page = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/layers/{middle.id}/remove"
    ).text

    assert "gap" in page.lower()
    assert "buffer" in page.lower(), "the confirm does not offer the other answer"


def _tower_panel_markup(page: str) -> str:
    """Just the drawing's markup, so an assertion about the picture cannot be
    satisfied by the stack editor's list of the same names.

    Sliced to the tower panel's own `<section class="tower" ...>` ... `</section>`
    — bounded at BOTH ends. Bounding only the start and taking the rest of the
    page would still pass on the layers TABLE underneath the drawing
    (`_layers_panel.html`'s `layer.cells.name`), which also prints every
    layer's name — a third copy of the same trap the buffer test already
    dodges on the stack editor above it.
    """
    start = page.index('class="tower"')
    end = page.index("</section>", start)
    return page[start:end]


def test_the_drawing_and_the_editor_never_disagree(app_and_org) -> None:
    """Both read the same file. A drawing that showed a different stack from
    the list beside it would make the picture untrustworthy, which is the one
    thing it is for.

    SCOPED TO THE DRAWING. The stack editor prints every layer name as well, so
    an assertion against the whole page passes on Task 6's markup alone — the
    same trap the buffer test below already avoids.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program

    page = client.get(f"/accounts/{org.ref}/program").text
    drawing = _tower_panel_markup(page)

    for layer in program.layers:
        assert layer.name in drawing, (
            f"{layer.name} is in the file and not in the drawing"
        )


def test_a_buffer_draws_as_a_buffer(app_and_org) -> None:
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[-1]

    sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
        position="above", name="Uninsured band", limit_cents=5_000_000_00,
        buffer=True,
    )

    page = client.get(f"/accounts/{org.ref}/program").text

    # SCOPED TO THE DRAWING. The stack editor also emits `is-buffer`, so an
    # unscoped assertion would pass on Task 6's markup alone and prove nothing
    # about the tower panel (caught in the pre-flight scan, ruling R3).
    assert 'class="tower-layer' in page
    drawn = [
        frag for frag in page.split('class="tower-layer')[1:]
        if "is-buffer" in frag.split(">")[0]
    ]
    assert drawn, "the drawing does not mark the buffer"


def test_a_buffer_is_labelled_in_the_drawing(app_and_org) -> None:
    """Hatch alone is a convention the reader has to already know — the spec
    calls for hatched AND LABELLED so an uninsured band reads as a DECISION,
    not a rendering artefact (fix round 1). The word comes from towerkit's
    `layer_terms` (render/labels.py), which appends "— buffer" to the
    outline's own terms the same way `_stack_editor.html` prints "buffer"
    beside a slab, so the two surfaces never describe the fact in different
    vocabulary. It lands in the outline `<div>`'s `title` attribute
    (`title="{{ layer.name }} — {{ layer.terms }}"` in `_tower_panel.html`).

    SCOPED TO THE DRAWING (`_tower_panel_markup`). The stack editor also
    prints "buffer" beside a slab, so an unscoped assertion would pass on
    Task 6's markup alone and prove nothing about the tower panel — the same
    trap `test_a_buffer_draws_as_a_buffer` above already dodges.

    SCOPED WITHIN THE DRAWING TOO: the outline div already carries an
    `is-buffer` CSS class (task 8's first cut), and `"buffer" in drawing`
    alone is satisfied by that class name — a machine convention, not a
    word a reader sees — without `layer_terms` contributing anything at all
    (a mutant that proved this: see the report). "— buffer" (the em dash
    `layer_terms` actually emits) cannot be satisfied by the class name.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[-1]

    sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
        position="above", name="Uninsured band", limit_cents=5_000_000_00,
        buffer=True,
    )

    page = client.get(f"/accounts/{org.ref}/program").text
    drawing = _tower_panel_markup(page)

    assert "\u2014 buffer" in drawing, "the drawing hatches but never says so"


def test_a_buffer_reads_uninsured_not_to_be_placed(app_and_org) -> None:
    """The word must be in the VISIBLE text a client is shown, not just a
    hover tooltip, and it must not say the OPPOSITE thing.

    Fix round 2 (Grant, 2026-08-21): a buffer has no participants, so before
    towerkit's fix, `is_pending` (signed_bps == 0) was true for it exactly
    as for a genuinely pending layer, and the buffer's own block printed
    "To be placed" — telling a reader cover is coming to a band the broker
    deliberately left uninsured. A hover tooltip saying "buffer" (the
    earlier assertion in this file) did not offset a wrong visible word.

    SCOPED to the buffer's own `.tower-block` (the VISIBLE `tower-line`
    spans), not the whole drawing and not the outline `<div>`'s `title`
    attribute — both `.tower-layer` (outline) and `.tower-block` (visible
    text) carry the same `data-layer-id`, so a plain substring search would
    find the title attribute's "— buffer" first and prove nothing about
    what is actually rendered on screen.

    CORRECTED, fix round 3: the layer is named "Second Excess", not
    "Uninsured band" — the earlier name let the positive "Uninsured"
    assertion below pass off the LAYER'S OWN NAME (which appears in the
    heading line regardless of the fix) rather than anything
    unplaced_label actually produced. Caught by re-checking every
    assertion in this file for the same class of coincidence the
    is-buffer-class-name and title-attribute mutants already found twice.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[-1]

    sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
        position="above", name="Second Excess", limit_cents=5_000_000_00,
        buffer=True,
    )
    fresh = sync.linked_program(conn, placement.id).program
    buf = next(ly for ly in fresh.layers if ly.name == "Second Excess")

    page = client.get(f"/accounts/{org.ref}/program").text
    drawing = _tower_panel_markup(page)

    block_pattern = re.compile(
        r'<div class="tower-block[^"]*"\s+data-layer-id="'
        + re.escape(buf.id)
        + r'"[^>]*>(.*?)</div>',
        re.DOTALL,
    )
    match = block_pattern.search(drawing)
    assert match, "the buffer's own tower-block is not in the drawing"
    visible_lines = re.findall(r'<span class="tower-line">([^<]*)</span>', match.group(1))

    assert visible_lines, "no visible text on the buffer's own block"
    assert any("Uninsured" in line for line in visible_lines), (
        f"the buffer's visible text never says so: {visible_lines}"
    )
    assert not any("To be placed" in line for line in visible_lines), (
        f"the buffer's visible text claims cover is coming: {visible_lines}"
    )


def test_a_buffer_is_not_marked_pending(app_and_org) -> None:
    """Fix round 3 (Grant, 2026-08-21): towerkit's WebLayer.pending was True
    for a buffer (is_pending's own predicate — signed_bps == 0 — is true for
    a buffer too, since it has no participants at all). This was harmless
    only by coincidence: `.is-buffer`'s own CSS rule already forces the same
    dashed border `.is-pending` would (app.css), so nothing on screen
    depended on the flag being wrong. "Harmless by coincidence is how the
    next bug gets in" — a buffer is not awaiting a decision, it IS the
    decision, so its outline must never carry `is-pending`.

    SCOPED to the buffer's own outline `.tower-layer` div specifically (not
    a bare substring search over the whole drawing), because OTHER layers
    in the fixture may legitimately be pending and would otherwise let a
    false pass hide behind them.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[-1]

    sync.insert_layer(
        conn, placement.id, line_id=line_id, anchor_layer_id=anchor.id,
        position="above", name="Second Excess", limit_cents=5_000_000_00,
        buffer=True,
    )
    fresh = sync.linked_program(conn, placement.id).program
    buf = next(ly for ly in fresh.layers if ly.name == "Second Excess")

    page = client.get(f"/accounts/{org.ref}/program").text
    drawing = _tower_panel_markup(page)

    outline_pattern = re.compile(
        r'<div class="tower-layer([^"]*)"\s+data-layer-id="'
        + re.escape(buf.id)
        + r'"[^>]*>',
    )
    match = outline_pattern.search(drawing)
    assert match, "the buffer's own outline is not in the drawing"
    class_suffix = match.group(1)
    assert "is-buffer" in class_suffix, "the buffer lost its own class"
    assert "is-pending" not in class_suffix, (
        f"the buffer's outline is still marked pending: {class_suffix!r}"
    )


def _stack_editor_markup(page: str) -> str:
    """Just the stack editor's own markup, bounded at BOTH ends.

    The brief's version sliced to the first `</div>` inside the editor, which
    closes an INNER element (the outer `stack-editor` div wraps several
    `<section class="stack">` blocks, each full of its own nested divs) — so
    every assertion after that slice ran against a handful of characters and
    proved nothing. `_tower_panel_markup` above bounds a section by its own
    matching close tag; the stack editor has no single close this test can
    find reliably (it is one `<div>` wrapping N `<section>`s, however many
    lines the program has), so this follows the amendment's fallback: bound
    the END at the next known SIBLING section instead. `_layers_panel.html`
    always prints `<dl class="program-facts">` immediately after the
    `{% include "account/_stack_editor.html" %}` block, so the slice from the
    editor's own class to just before that dl covers the whole editor —
    every stack, every slab, and the insert form's own last button — without
    running past it into the rest of the program panel.
    """
    start = page.index('class="stack-editor"')
    end = page.index('class="program-facts"', start)
    return page[start:end]


def test_a_whole_tower_is_buildable_without_a_pointer(app_and_org) -> None:
    """If this fails, drag stops being polish and becomes a requirement.

    Every control the builder needs is a form control or a button — no
    drag handles, no click-only affordances — so a keyboard reaches all of it.

    Proven two ways, per the amendment: absence of pointer-only affordances in
    the markup is necessary but not sufficient, so this also DRIVES an actual
    build the way a keyboard user's form submits do — POST the insert route
    for a layer, then for a buffer, and confirm both land.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    anchor = program.layers_for_line(line_id)[0]
    before = _stack_of(conn, placement, line_id)

    page = client.get(f"/accounts/{org.ref}/program").text
    editor = _stack_editor_markup(page)

    # Prove the slice is not vacuous: it reaches the insert form's own LAST
    # control, not just its first few characters.
    assert "insert buffer" in editor

    assert "draggable" not in editor
    assert "onmousedown" not in editor
    # Every interactive element is a real, natively-focusable control — a
    # div or span standing in for one, wired to hx-post/hx-get, is a mouse
    # trap a keyboard (and a screen reader) cannot reach.
    for handler in re.findall(r'<(?:div|span)[^>]*hx-(?:post|get)=', editor):
        raise AssertionError(f"a div/span is doing a control's job: {handler}")

    # Drive it: a keyboard user reaches every one of these through Tab and
    # Enter/Space, never a drag. Same anchor twice, following the proven
    # pattern above (test_the_file_is_never_written_with_an_overlap) — each
    # insert seats fresh on the same existing slab and pushes upward.
    layer_done = client.post(
        _insert_url(org, placement, line_id),
        data={"name": "Keyboard Layer", "limit_cents": "10m",
              "anchor": anchor.id, "position": "above", "kind": "layer"},
    )
    assert layer_done.status_code == 200
    assert "Keyboard Layer" in layer_done.text

    buffer_done = client.post(
        _insert_url(org, placement, line_id),
        data={"name": "Keyboard Buffer", "limit_cents": "5m",
              "anchor": anchor.id, "position": "above", "kind": "buffer"},
    )
    assert buffer_done.status_code == 200
    assert "Keyboard Buffer" in buffer_done.text

    after = _stack_of(conn, placement, line_id)
    assert len(after) == len(before) + 2
