"""The last mile: [0, 1] geometry to CSS percentages, and nothing else.

Pure — no database, no request, no browser. Percentage arithmetic inside a
Jinja template is arithmetic no test can reach, which is the whole reason this
module exists rather than a few filters.

towerkit's own suite proves the renderer quotes labels.py and recomputes no
geometry (tests/test_render_web.py). What is asserted here is that bookkit
carries both across the seam without touching them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bookkit.web import tower


def test_the_unit_interval_becomes_percent():
    assert tower.pct(0.0) == 0.0
    assert tower.pct(1.0) == 100.0
    assert tower.pct(0.25) == 25.0


def test_a_percentage_is_rounded_not_truncated():
    """A truncated width leaves a hairline gap between adjacent blocks, which
    reads as a missing participant on a tower whose whole job is showing who
    is on what."""
    assert tower.pct(1 / 3) == 33.3333


def test_the_chart_height_is_the_drawing_area_not_the_panel_box():
    """build_web_tower's own docstring: the design prototype's outer panel is
    340px and its drawing area ~240px. Passing 340 makes every block look ~42%
    taller than it renders, which UNDER-fires every label-drop threshold and
    drops too few labels — silently, because both numbers are positive floats
    and nothing downstream can tell them apart."""
    assert tower.CHART_HEIGHT_PX == 240.0


def test_the_css_declares_the_same_height_the_panel_is_built_at():
    """The constant and the stylesheet are ONE fact stored twice. If they
    drift, every label-drop decision is made against a height the browser is
    not using, and nothing anywhere fails."""
    css = (Path(tower.__file__).parent / "static" / "app.css").read_text()
    declared = re.search(r"--tower-chart-height:\s*(\d+(?:\.\d+)?)px", css)

    assert declared, "app.css does not declare --tower-chart-height"
    assert float(declared.group(1)) == tower.CHART_HEIGHT_PX


# --- what the seam carries ----------------------------------------------------


@pytest.fixture
def drawn(snapshot_db: Path):
    """A real program off bookkit's OWN seeded book, rendered.

    Not towerkit's sample file: reaching into the sibling repo's test data
    couples this suite to a path bookkit does not own, and the seed already
    projects real towerkit files for exactly this reason."""
    from towerkit.model import load_program

    from bookkit import db
    from bookkit.repo import orgs, placements

    conn = db.connect(snapshot_db)
    linked = [
        p
        for org in orgs.list_orgs(conn, kind="client")
        for p in placements.for_org(conn, org.id)
        if p.program_path
    ]
    assert linked, "the seeded book projected no program files"
    program = load_program(Path(linked[0].program_path))
    return program, tower.panel(program)


def test_every_block_carries_the_lines_the_renderer_chose(drawn):
    """bookkit picks no strings. Each line rendered is one the renderer put on
    the block — the bookkit end of the agreement rule."""
    from towerkit.render.web import build_web_tower

    program, built = drawn
    reference = build_web_tower(program, tower.CHART_HEIGHT_PX)

    assert [b["lines"] for b in built["blocks"]] == [list(b.lines) for b in reference.blocks]


def test_geometry_crosses_the_seam_as_percentages_of_the_same_rects(drawn):
    from towerkit.render.web import build_web_tower

    program, built = drawn
    reference = build_web_tower(program, tower.CHART_HEIGHT_PX)

    span = reference.width
    for block, source in zip(built["blocks"], reference.blocks, strict=True):
        for rect, original in zip(block["rects"], source.rects, strict=True):
            # x is divided by the layout width, y is not — the two axes are in
            # different units and this assertion is where that is written down
            assert rect["left"] == tower.pct(original.x0 / span)
            assert rect["width"] == tower.pct(original.width / span)
            assert rect["bottom"] == tower.pct(original.y0)
            assert rect["height"] == tower.pct(original.height)


def test_the_caveat_survives_the_crossing(drawn):
    """"Not to scale" is a claim about the drawing, and the drawing is what
    bookkit renders — dropping it would leave a compressed tower asserting a
    linear one."""
    from towerkit.render.web import build_web_tower

    program, built = drawn
    assert built["caveat"] == build_web_tower(program, tower.CHART_HEIGHT_PX).caveat


# --- the two axes are not in the same units -----------------------------------


def test_no_percentage_ever_leaves_the_box(drawn):
    """THE bug this file exists to prevent.

    y is a fraction of the chart height; x is in COLUMN units and spans
    [0, TowerLayout.width] — one column per line of cover. Treating them alike
    put a four-line program's blocks at `left: 237.5%` with `width: 112.5%`:
    two of its four layers were off-screen and a carrier on the umbrella was
    invisible. Every number this module emits is a percentage of the box, so
    none of them may exceed 100."""
    _program, built = drawn

    # Blocks and layer outlines only. RETENTIONS ARE NOT IN THE BOX and must
    # not be: the retention band is drawn BELOW the zero line, so its rects
    # carry a negative y by construction — asserted separately below, because
    # "outside the box" is the fact about them rather than a bug.
    everything = [
        rect
        for block in built["blocks"] for rect in block["rects"]
    ] + [
        rect
        for layer in built["layers"] for rect in layer["outlines"]
    ]
    assert everything, "nothing was drawn — the test proves nothing"

    for rect in everything:
        assert 0 <= rect["left"] <= 100, rect
        assert 0 <= rect["bottom"] <= 100, rect
        assert rect["left"] + rect["width"] <= 100.0001, rect


def test_the_tower_fills_its_own_width(drawn):
    """The rightmost block ends AT the edge. A tower that stopped at 40% would
    satisfy the bounds check above while drawing a picture of the wrong shape."""
    _program, built = drawn

    right = max(
        rect["left"] + rect["width"]
        for block in built["blocks"] for rect in block["rects"]
    )
    assert right == pytest.approx(100.0, abs=0.01)


def test_every_participant_is_drawn(drawn):
    """One block per seat. Two of four layers vanished off the right-hand edge
    before the units were fixed, and the page still rendered."""
    from towerkit.render.web import build_web_tower

    program, built = drawn
    reference = build_web_tower(program, tower.CHART_HEIGHT_PX)

    assert len(built["blocks"]) == len(reference.blocks)
    assert len(built["blocks"]) > 1, "a single-block tower cannot show this"


def test_a_retention_sits_below_the_zero_line(drawn):
    """Not a bounds violation — the shape of the thing. A retention is what the
    insured carries before any layer attaches, so it is drawn below the line
    every layer is measured from. The template prints these as labels rather
    than positioning them; the geometry is carried across the seam whole so a
    later slice can draw the band without re-deriving it."""
    _program, built = drawn

    if not built["retentions"]:
        pytest.skip("the seeded program records no retention")
    for retention in built["retentions"]:
        for rect in retention["rects"]:
            assert rect["bottom"] < 0, rect
