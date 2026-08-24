"""The last mile of the tower panel: geometry to CSS.

towerkit's `render/web.py` hands over layout rects and every string it means to
show already chosen from `render/labels.py`. This module turns the geometry
into CSS percentages and stops.

THE TWO AXES ARE NOT IN THE SAME UNITS, and assuming they were is how the
first version of this drew a tower with blocks at `left: 237.5%`. `y` is a
fraction of the chart height, `[0, 1]`. `x` is in COLUMN units and spans
`[0, TowerLayout.width]` — one column per line of coverage, so a four-line
program is nearly four units wide. The export knows this and sets its x-limit
to `tower.width` (render/mpl_program.py); the panel divides by it. Read the
layout's contract before touching this, not this module's summary of it.

IT COMPOSES NO TEXT. A carrier line, a money line or a heading built here would
be a second renderer describing the same program, and the whole R66 agreement
rule (spec D2.1) exists to stop the panel and the export saying different
things about one tower. The rule is not that they wrap alike — it is that every
fact either of them asserts came from the one authority. An f-string here
breaks that quietly, and the export is the copy that reaches a client.

IT RECOMPUTES NO GEOMETRY, for the same reason: the rects pass through
untouched, and `test_geometry_is_passed_through_not_recomputed` in towerkit's
suite holds the other end.
"""

from __future__ import annotations

from typing import Any

from towerkit.model import Program
from towerkit.render.web import WebTower, build_web_tower

CHART_HEIGHT_PX = 240.0
"""Height of the chart DRAWING AREA in CSS pixels — not the panel's outer box.

The outer box also carries the header row, the retention band below the zero
line and the caveat, none of which are part of the 100%-tall region a block's
height is a fraction of. In the design prototype the outer panel is 340px and
the drawing area ~240px. Passing 340 makes every block look ~42% taller than it
renders, UNDER-fires every label-drop threshold and drops too few labels — and
no test that merely checks the thresholds are applied consistently can catch
it, because both numbers are positive floats and everything downstream agrees
with itself either way.

`app.css` declares the same number as `--tower-chart-height`, and
tests/test_web_tower.py holds the two together: one fact stored twice is a
drift waiting to happen, and this particular drift is invisible.
"""


def pct(value: float) -> float:
    """`[0, 1]` to a CSS percentage.

    Rounded, never truncated: a truncated width leaves a hairline gap between
    adjacent blocks, and on a tower a gap between two carriers reads as a
    participant nobody recorded.
    """
    return round(value * 100, 4)


def panel(program: Program) -> dict[str, Any]:
    """Template context for one program's tower, at the panel's own height."""
    return context(build_web_tower(program, CHART_HEIGHT_PX))


def context(web: WebTower) -> dict[str, Any]:
    """A built WebTower as the template wants it.

    Split from `panel` so a test can hand in a tower it built itself, and so
    the conversion is exercised without a file on disk.
    """
    # The x span. `or 1.0` for a degenerate layout with no columns at all —
    # dividing by zero there would turn an empty tower into a crash on a page
    # that otherwise renders fine.
    span = web.width or 1.0
    return {
        "caveat": web.caveat,
        "chart_height_px": CHART_HEIGHT_PX,
        "top_dollars": web.top_dollars,
        "blocks": [
            {
                "layer_id": block.layer_id,
                # the renderer's own choice of what this block says, after its
                # own drop rule — bookkit neither adds to it nor edits it
                "lines": list(block.lines),
                "carrier": block.carrier,
                "unplaced": block.carrier is None,
                "rects": [_rect(r, span) for r in block.rects],
            }
            for block in web.blocks
        ],
        "layers": [
            {
                "layer_id": layer.layer_id,
                "name": layer.name,
                "terms": layer.terms,
                "pending": layer.pending,
                "statutory": layer.statutory,
                # The drawing must not present an uninsured band as cover.
                # Same word the stack editor uses (`is-buffer`), so one CSS
                # rule serves both. Direct access, not getattr-with-default:
                # a default of False would let a missing/older towerkit
                # silently drop the flag and leave the drawing disagreeing
                # with the file — `pending` and `statutory` are direct too.
                "buffer": layer.buffer,
                "outlines": [_rect(r, span) for r in layer.outlines],
            }
            for layer in web.layers
        ],
        "ref_lines": [
            {"label": line.label, "bottom": pct(line.y)} for line in web.ref_lines
        ],
        "retentions": [
            {"label": r.label, "rects": [_rect(rect, span) for rect in r.rects]}
            for r in web.retentions
        ],
        "groups": [
            {"label": g.label, "left": pct(g.x0 / span), "width": pct((g.x1 - g.x0) / span)}
            for g in web.groups
        ],
        "chevrons": [_rect(r, span) for r in web.chevrons],
        "retention_band": pct(web.retention_band),
    }


def _rect(rect: Any, span: float) -> dict[str, float]:
    """One layout rect as the four CSS numbers that place it.

    `span` is the layout's total width in column units — x is divided by it,
    y is not. See the module docstring: the two axes are in different units and
    treating them alike put blocks at `left: 237.5%`.

    `bottom`, not `top`: the layout's y grows upward from the zero line the way
    a tower does, and flipping it here would be the one piece of geometry this
    module recomputed rather than carried.
    """
    return {
        "left": pct(rect.x0 / span),
        "bottom": pct(rect.y0),
        "width": pct(rect.width / span),
        "height": pct(rect.height),
    }
