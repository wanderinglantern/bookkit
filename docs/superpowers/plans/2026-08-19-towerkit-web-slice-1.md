# Tower panel on the web (slice 1) — Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Program tab's lying stub with the real thing — every
placement on the account, and the linked program drawn as a tower panel in
HTML — read-only.

**Architecture:** towerkit's `render/web.py` (merged 2026-08-18, phase A)
already turns a `Program` into `WebTower`: geometry in `[0, 1]`, every string
quoted from `render/labels.py`, no plotting library and no text measurement.
bookkit's job here is the last mile only — load the program for a placement,
call `build_web_tower` at the **chart drawing area's** height, and turn `[0, 1]`
rects into CSS percentages. No new geometry, no new strings, no writes.

**Tech Stack:** FastAPI + Jinja2 + htmx (no new dependency), towerkit
`render/web.py`, `render/labels.py`, `layout.py`, `scale.py`.

**Spec:** `docs/superpowers/specs/2026-08-17-towerkit-web-conversion.md`
— D2/D2.1 (the split and the R66 agreement rule), D3 (no y-axis), D4 (the
label-drop rule and its base height), D8 slice 1 (scope).

## Global Constraints

- **Read-only.** No `sync.py` write-through call appears in this slice. Layer
  editing, `add_layer`, the conflict three-way and the revert dispatcher are
  slices 2 and 3 — see spec D8.
- **No string composed in bookkit.** Every label rendered comes off a
  `WebBlock`/`WebLayer`/`WebGroup`/`WebRetention` field. An f-string that
  builds a carrier line, a money line or a heading in a template or route is
  the exact divergence R66 exists to prevent.
- **No geometry recomputed in bookkit.** Rects arrive as `[0, 1]` floats and
  are multiplied by 100 to become percentages. Nothing else.
- **`chart_height_px` is the DRAWING AREA, never the panel box.** The panel
  outer box also holds the header row, the retention band and the caveat
  line. Passing the outer height under-fires every drop threshold and drops
  too few labels, silently. See `build_web_tower`'s docstring.
- **Route registration order.** Any route more specific than
  `GET /accounts/{ref}/{tab}` must be registered **before** `account.router`
  in `web/app.py` — Starlette resolves by registration order, not
  specificity (`web/app.py`, the comment above `include_router` calls).
- **Gates**: `uv run --no-sync python -m pytest -q`, `uv run --no-sync python
  -m mypy src`, `uv run --no-sync python -m ruff check src tests`. A bare
  `uv run pytest` falls through to Anaconda's and reports a bogus
  `ModuleNotFoundError: No module named 'bookkit'`.
- **towerkit work goes in its own `git worktree`.** Every bookkit worktree
  compiles against whatever branch is checked out in
  `/Users/grantgreeson/Developer/towerkit`; a branch left there makes clean
  bookkit branches gate red.

---

## What already exists — do not rebuild it

Checked at bookkit `6b0c76f` / towerkit `0405cca`, not assumed:

| thing | where | state |
|---|---|---|
| `WebTower`, `WebBlock`, `WebLayer`, `WebRefLine`, `WebRetention`, `WebGroup` | towerkit `render/web.py` | built, merged |
| `build_web_tower(program, chart_height_px, gamma=DEFAULT_GAMMA)` | towerkit `render/web.py:206` | built, merged |
| The drop rule, the caveat, ref-line thinning, group bands | inside `build_web_tower` | built, merged |
| Placements for an account | `repo/placements.py:51 for_org` | exists |
| The layers of a placement's linked file, money in cents | `sync.py:896 layer_details` | exists |
| Program tab route + count badge | `web/routes/account.py` generic `{tab}` | exists |
| The panel template | `web/templates/account/program.html` | **stub that lies** |

### The R66 agreement obligation is nearly discharged already

D2.1 requires slice 1 to carry an agreement test over five facts, and
deliberately left its mechanics to this plan. Read
`towerkit/tests/test_render_web.py` before writing anything new: phase A
already carries four of the five.

| fact (D2.1) | test | covers it? |
|---|---|---|
| 1. Geometry — one `TowerLayout`, same gamma | `test_geometry_is_passed_through_not_recomputed` | rects: **yes**, exact. Gamma: **no** — see Task 1 |
| 2. Label text quoted, never composed | `test_no_block_string_is_composed_here` | **yes** — set-membership against a universe built from `labels.py` |
| 3. Which block carries the heading | `test_the_heading_goes_where_labels_py_says` | **yes** |
| 4. Whether a layer is pending | `test_pending_is_decided_once` | **yes** |
| 5. Money and share formatting | folded into fact 2's universe (`block_premium_label`, `layer_terms`) | **yes** |

**The mechanics D2.1 was worried about, settled:** the agreement test does
**not** compare the two renderers to each other. It compares each of them to
the shared authority. That is why it does not conflate wrapping with
divergence — the two adversarial passes both broke on a per-block string
comparison between renderers, which fails whenever a fitter legitimately
picks a different rung of the same ladder. Membership in the authority's own
output ("every string this renderer can emit is one `labels.py` offered for
this block") permits fit to differ and forbids facts to. That is the shape
already in `test_no_block_string_is_composed_here`, and it is correct.

One gap remains, and Task 1 closes it.

---

## File structure

| file | responsibility |
|---|---|
| `towerkit/tests/test_render_web.py` (modify) | the last R66 fact: both renderers share one gamma default |
| `src/bookkit/web/tower.py` (create) | pure: `WebTower` → template-ready percentages. No SQL, no I/O, no strings of its own. `CHART_HEIGHT_PX` lives here |
| `src/bookkit/web/routes/program.py` (create) | the Program tab route: placements list + the panel for one placement |
| `src/bookkit/web/templates/account/program.html` (rewrite) | placements table, replacing the stub |
| `src/bookkit/web/templates/account/_tower_panel.html` (create) | the tower itself |
| `src/bookkit/web/static/app.css` (modify) | tower panel styles, including the one declared chart height |
| `src/bookkit/web/app.py` (modify) | register `program.router` before `account.router` |
| `tests/test_web_program.py` (create) | route + panel tests |
| `tests/test_web_tower.py` (create) | pure percentage-conversion tests |

`web/tower.py` is pure on purpose: it is the file that can be tested without a
database, a request or a browser, and keeping the conversion there is what
stops percentage arithmetic leaking into a Jinja template where nothing can
test it.

---

## Task 1: Both renderers agree on the gamma (towerkit)

The last unclosed R66 fact. `build_web_tower` and `render_program` each
default `gamma` to `scale.DEFAULT_GAMMA` today, and nothing asserts they stay
equal. Change one default and the panel and the export describe two different
towers — the exact failure R66 exists to prevent, and the only one of the five
facts phase A left uncovered.

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/tests/test_render_web.py`

**Interfaces:**
- Consumes: `towerkit.render.web.build_web_tower`,
  `towerkit.render.mpl_program.render_program`, `towerkit.scale.DEFAULT_GAMMA`
- Produces: nothing importable — a guard test only.

- [ ] **Step 1: Create the towerkit worktree**

```bash
cd /Users/grantgreeson/Developer/towerkit
git worktree add .claude/worktrees/web-gamma -b web-gamma-agreement
cd .claude/worktrees/web-gamma
uv sync --group dev
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_render_web.py`:

```python
class TestBothRenderersShareOneScale:
    """R66 fact 1 has two halves. The rects are checked exactly by
    test_geometry_is_passed_through_not_recomputed; this is the other half —
    the two renderers must build those rects at the SAME gamma.

    Nothing else catches a change to one default. Both signatures would still
    be valid Python, both suites would still be green, and the panel and the
    export would quietly describe two different towers — the single failure
    R66 exists to prevent.
    """

    def test_the_panel_and_the_export_default_to_the_same_gamma(self) -> None:
        import inspect

        from towerkit.render.mpl_program import render_program
        from towerkit.render.web import build_web_tower
        from towerkit.scale import DEFAULT_GAMMA

        panel = inspect.signature(build_web_tower).parameters["gamma"].default
        export = inspect.signature(render_program).parameters["gamma"].default

        assert panel == export == DEFAULT_GAMMA
```

- [ ] **Step 3: Run it and watch it pass, then MUTATE to prove it can fail**

```bash
cd /Users/grantgreeson/Developer/towerkit/.claude/worktrees/web-gamma
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q \
  tests/test_render_web.py -k gamma
```
Expected: PASS. Then change `build_web_tower`'s default to `gamma: float = 1.0`
and re-run: expected FAIL naming both values. **Restore the default by
copying the saved file back, never with `git checkout -- <file>`** — that
discards uncommitted work and has cost this project a set of mutation proofs
before.

- [ ] **Step 4: Gate and commit**

```bash
cd /Users/grantgreeson/Developer/towerkit/.claude/worktrees/web-gamma
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q > /tmp/gate.txt 2>&1; tail -3 /tmp/gate.txt
git add tests/test_render_web.py
git commit -m "test: the panel and the export are drawn at one scale"
```

Note the one known environmental failure in towerkit's suite:
`tests/test_connector.py::test_roots_fall_back_to_bookkits_configuration`
fails on `main` too (no `bookctl` on PATH from that checkout) and does not
fail inside a fresh worktree.

---

## Task 2: The percentage conversion (`web/tower.py`)

**Files:**
- Create: `src/bookkit/web/tower.py`
- Test: `tests/test_web_tower.py`

**Interfaces:**
- Consumes: `towerkit.render.web.build_web_tower`, `WebTower`
- Produces:
  - `CHART_HEIGHT_PX: float = 240.0`
  - `def pct(value: float) -> float` — `[0, 1]` → percent, rounded to 4dp
  - `def panel(program: Program) -> dict[str, Any]` — the template context

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_tower.py`:

```python
"""The last mile: [0, 1] geometry to CSS percentages, and nothing else.

Pure — no database, no request, no browser. Percentage arithmetic in a Jinja
template is arithmetic no test can reach, which is the whole reason this
module exists."""

from __future__ import annotations

import pytest

from bookkit.web import tower


def test_the_unit_interval_becomes_percent():
    assert tower.pct(0.0) == 0.0
    assert tower.pct(1.0) == 100.0
    assert tower.pct(0.25) == 25.0


def test_a_percentage_is_rounded_not_truncated():
    """A truncated width leaves a hairline gap between adjacent blocks that
    reads as a missing participant."""
    assert tower.pct(1 / 3) == 33.3333


def test_the_chart_height_is_the_drawing_area_not_the_panel_box():
    """build_web_tower's own docstring: the outer panel in the design
    prototype is 340px and the drawing area ~240px. Passing 340 makes every
    block look 42% taller than it renders, which UNDER-fires every drop
    threshold and drops too few labels — silently, since both numbers are
    positive floats and nothing else can tell them apart."""
    assert tower.CHART_HEIGHT_PX == 240.0


def test_the_css_declares_the_same_height_the_panel_is_built_at():
    """The constant and the stylesheet are one fact stored twice. If they
    drift, every label-drop decision is made against a height the browser is
    not using, and nothing anywhere fails."""
    import re
    from pathlib import Path

    css = (Path(tower.__file__).parent / "static" / "app.css").read_text()
    declared = re.search(r"--tower-chart-height:\s*(\d+(?:\.\d+)?)px", css)

    assert declared, "app.css does not declare --tower-chart-height"
    assert float(declared.group(1)) == tower.CHART_HEIGHT_PX
```

- [ ] **Step 2: Run them and watch them fail**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q tests/test_web_tower.py
```
Expected: FAIL — `ModuleNotFoundError: No module named 'bookkit.web.tower'`.

- [ ] **Step 3: Write `src/bookkit/web/tower.py`**

```python
"""The last mile of the tower panel: geometry to CSS.

towerkit's `render/web.py` hands over rects in `[0, 1]` and every string
already chosen from `render/labels.py`. This module multiplies by 100 and
stops. It composes NO text: a carrier line, a money line or a heading built
here would be a second renderer describing the same program, which is the one
thing R66 forbids (spec D2.1)."""

from __future__ import annotations

from typing import Any

from towerkit.model import Program
from towerkit.render.web import WebTower, build_web_tower

CHART_HEIGHT_PX = 240.0
"""Height of the chart DRAWING AREA in CSS pixels — not the panel's outer box.

The outer box also carries the header row, the retention band below the zero
line and the caveat line, none of which are part of the 100%-tall region a
block's height is a fraction of. In the design prototype the outer panel is
340px and the drawing area ~240px. Passing 340 makes every block look ~42%
taller than it renders, under-fires every label-drop threshold and drops too
few labels — and no test that merely checks the thresholds are applied
consistently can catch it. `app.css` declares the same number as
`--tower-chart-height`, and tests/test_web_tower.py holds the two together."""


def pct(value: float) -> float:
    """`[0, 1]` to a CSS percentage. Rounded, never truncated: a truncated
    width leaves a hairline gap between adjacent blocks that reads as a
    missing participant."""
    return round(value * 100, 4)


def panel(program: Program) -> dict[str, Any]:
    """Template context for one program's tower."""
    return context(build_web_tower(program, CHART_HEIGHT_PX))


def context(web: WebTower) -> dict[str, Any]:
    """The WebTower as the template wants it. Split from `panel` so a test can
    hand in a WebTower it built itself."""
    return {
        "caveat": web.caveat,
        "chart_height_px": CHART_HEIGHT_PX,
        "blocks": [
            {
                "layer_id": block.layer_id,
                "lines": block.lines,
                "unplaced": block.carrier is None,
                "rects": [_rect(r) for r in block.rects],
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
                "outlines": [_rect(r) for r in layer.outlines],
            }
            for layer in web.layers
        ],
        "ref_lines": [
            {"label": line.label, "bottom": pct(line.y)} for line in web.ref_lines
        ],
        "retentions": [
            {"label": r.label, "rects": [_rect(rect) for rect in r.rects]}
            for r in web.retentions
        ],
        "groups": [
            {"label": g.label, "left": pct(g.x0), "width": pct(g.x1 - g.x0)}
            for g in web.groups
        ],
        "chevrons": [_rect(r) for r in web.chevrons],
        "retention_band": pct(web.retention_band),
    }


def _rect(rect: Any) -> dict[str, float]:
    return {
        "left": pct(rect.x),
        "bottom": pct(rect.y),
        "width": pct(rect.width),
        "height": pct(rect.height),
    }
```

- [ ] **Step 4: Add the CSS custom property**

In `src/bookkit/web/static/app.css`, in the tower section added by Task 4,
declare the height as a custom property so the stylesheet and the constant
are checkable against each other:

```css
.tower-chart {
  --tower-chart-height: 240px;
  position: relative;
  height: var(--tower-chart-height);
}
```

- [ ] **Step 5: Run the tests**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q tests/test_web_tower.py
```
Expected: PASS.

- [ ] **Step 6: Prove the height test can fail**

Change `CHART_HEIGHT_PX` to `340.0`, re-run, expect
`test_the_css_declares_the_same_height_the_panel_is_built_at` to FAIL.
Restore from a saved copy.

- [ ] **Step 7: Commit**

```bash
git add src/bookkit/web/tower.py src/bookkit/web/static/app.css tests/test_web_tower.py
git commit -m "feat: the tower's geometry becomes CSS, and nothing else"
```

---

## Task 3: The placements list replaces the stub

The stub is worse than empty: the tab badge counts placements while the panel
prints "empty — add the first row", so an account with two programs reads as
an account with none. That contradiction is what made Grant conclude programs
could not be created at all (2026-08-19).

**Files:**
- Create: `src/bookkit/web/routes/program.py`
- Rewrite: `src/bookkit/web/templates/account/program.html`
- Modify: `src/bookkit/web/app.py`
- Test: `tests/test_web_program.py`

**Interfaces:**
- Consumes: `repo.placements.for_org(conn, org_id) -> list[Placement]`,
  `routes.account._conn/_org/_context`, `web.tower.panel`
- Produces: `router` (FastAPI `APIRouter`) exporting
  `GET /accounts/{ref}/program`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_program.py`:

```python
"""The Program tab: every placement on the account, and the linked program
drawn as a tower.

Read-only this slice. The write path (layer editing, the conflict three-way)
is spec D8 slices 2 and 3."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = orgs.list_orgs(conn, kind="client")[0]
    if not placements.for_org(conn, org.id):
        placements.create(
            conn, org.id, "Casualty Program", "2026-01-01", "2027-01-01",
            status="bound", total_premium=750_000_00,
        )
    # base_url: web/origin.py refuses TestClient's default Host of "testserver"
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def test_the_program_tab_lists_the_placements(app_and_org):
    client, org = app_and_org
    from bookkit.repo import placements

    page = client.get(f"/accounts/{org.ref}/program")

    assert page.status_code == 200
    for placement in placements.for_org(client.app.state.conn, org.id):
        assert placement.program_name in page.text
        assert placement.ref in page.text


def test_the_panel_never_contradicts_the_tab_badge(app_and_org):
    """The stub printed the addable-list empty state unconditionally, so an
    account with two placements read as an account with none."""
    client, org = app_and_org

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "empty — add the first row" not in page


def test_an_account_with_no_placements_says_so_honestly(snapshot_db: Path):
    from bookkit.repo import orgs

    app = create_app(snapshot_db)
    conn = app.state.conn
    bare = orgs.create(conn, kind="client", name="No Programs Co")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        page = client.get(f"/accounts/{bare.ref}/program")

    assert page.status_code == 200
    assert "no programs on this account" in page.text
```

- [ ] **Step 2: Run and watch them fail**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q tests/test_web_program.py
```
Expected: FAIL — the stub renders "empty — add the first row" and no
placement names.

- [ ] **Step 3: Write `src/bookkit/web/routes/program.py`**

```python
"""The Program tab: the account's placements, and the tower for the linked
program file.

READ-ONLY this slice. No sync.py write-through call belongs here yet — layer
editing and the write-conflict three-way are spec D8 slices 2 and 3.

Registered BEFORE account.router in app.py: this module's
GET /accounts/{ref}/program and account.py's generic GET /accounts/{ref}/{tab}
match the same two-segment path, and Starlette resolves across routers by
registration order rather than specificity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...repo import placements as placements_repo
from ..app import TEMPLATES
from ..tower import panel
from .account import _conn, _context, _org

router = APIRouter()


def _tower_for(placement: Any) -> dict[str, Any] | None:
    """The panel for a placement's linked file, or None when there is no file
    (or it will not load). None and an empty tower are different facts: one
    means "no program file", the other "a program with nothing in it", and the
    template says different things about them."""
    if not placement.program_path:
        return None
    try:
        from towerkit.io import load_program
    except ImportError:  # pragma: no cover - towerkit is a hard dependency
        return None
    try:
        return panel(load_program(Path(placement.program_path)))
    except Exception:
        return None


@router.get("/accounts/{ref}/program", response_class=HTMLResponse)
def program_tab(request: Request) -> HTMLResponse:
    ref = request.path_params["ref"]
    org = _org(request, ref)
    conn = _conn(request)
    rows = placements_repo.for_org(conn, org.id)
    context = {
        **_context(request, org, "program"),
        "placements": rows,
        "towers": {p.id: _tower_for(p) for p in rows},
    }
    return TEMPLATES.TemplateResponse(request, "account/program.html", context)
```

Check `_context`'s real signature in `web/routes/account.py` before writing
this — mirror exactly how `routes/pipeline.py` calls it, rather than assuming
the argument order above.

- [ ] **Step 4: Rewrite the template**

`src/bookkit/web/templates/account/program.html`:

```html
{% extends "account/page.html" %}
{% block panel %}
  {# The stub this replaces printed the addable-list empty state
     unconditionally, while the tab badge counted the placements it was
     claiming did not exist. #}
  {% if placements %}
    <div class="table-scroll">
      <table class="rows rows-fit">
        <thead>
          <tr><th>Program</th><th>Ref</th><th>Period</th><th>Status</th><th>Premium</th></tr>
        </thead>
        <tbody>
          {% for p in placements %}
            <tr>
              <td class="prose">{{ p.program_name }}</td>
              <td class="mono">{{ p.ref }}</td>
              <td class="mono">{{ p.period_from }} → {{ p.period_to }}</td>
              <td><span class="status-{{ p.status }}">{{ p.status }}</span></td>
              <td class="num">{{ p.total_premium | cents }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    {% for p in placements %}
      {% if towers[p.id] %}
        {% with tower = towers[p.id], caption = p.program_name ~ " · " ~ p.ref %}
          {% include "account/_tower_panel.html" %}
        {% endwith %}
      {% endif %}
    {% endfor %}
  {% else %}
    <div class="panel-empty">
      <p>no programs on this account</p>
    </div>
  {% endif %}
{% endblock %}
```

Confirm a `cents` filter exists in this codebase's Jinja environment before
using it; if it does not, format in the route with `money.format_cents` and
pass the string, rather than adding a filter in this slice.

- [ ] **Step 5: Register the router BEFORE account.router**

In `src/bookkit/web/app.py`, beside the existing includes:

```python
    from .routes import account, book, changes, pipeline, program, relationship, work
    ...
    app.include_router(program.router)   # before account.router — see the comment above
    app.include_router(account.router)
```

- [ ] **Step 6: Run the tests**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q tests/test_web_program.py
```
Expected: PASS.

- [ ] **Step 7: Prove the route order matters**

Move `app.include_router(program.router)` to *after* `account.router` and
re-run: the generic `{tab}` route wins and the placement-name assertion fails.
Restore. This is the same trap `relationship.router` already carries a comment
about, and the reason it is worth proving once rather than trusting.

- [ ] **Step 8: Commit**

```bash
git add src/bookkit/web/routes/program.py src/bookkit/web/templates/account/program.html \
        src/bookkit/web/app.py tests/test_web_program.py
git commit -m "feat: the Program tab shows the programs it has been counting"
```

---

## Task 4: The tower panel itself

**Files:**
- Create: `src/bookkit/web/templates/account/_tower_panel.html`
- Modify: `src/bookkit/web/static/app.css`
- Test: `tests/test_web_program.py` (append)

**Interfaces:**
- Consumes: the `tower` dict from `web.tower.context`, and `caption`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_program.py`:

```python
def _program_fixture(conn, org, tmp_path: Path):
    """A placement with a real linked towerkit file — two layers, one of them
    partly placed, so the panel has both a carrier block and unplaced
    capacity to draw."""
    from bookkit import sync
    from bookkit.repo import placements

    placement = placements.create(
        conn, org.id, "Tower Test Program", "2026-01-01", "2027-01-01",
        status="bound", total_premium=500_000_00,
    )
    dest, diags = sync.scaffold_program(conn, placement.id, tmp_path / "tower.json")
    assert dest is not None, [d.message for d in diags.errors]
    return placements.get(conn, placement.id)


def test_the_tower_paints_the_blocks_the_renderer_produced(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _program_fixture(conn, org, tmp_path)

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "tower-chart" in page, "no tower drawn for a placement with a linked file"
    assert placement.program_name in page


def test_the_panel_prints_no_string_the_renderer_did_not_give_it(app_and_org, tmp_path):
    """R66, at the bookkit end. towerkit's own suite proves the renderer
    quotes labels.py; this proves bookkit prints what the renderer handed it
    rather than composing a line of its own."""
    from pathlib import Path as _Path

    from towerkit.io import load_program

    from bookkit.web.tower import panel

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _program_fixture(conn, org, tmp_path)

    built = panel(load_program(_Path(placement.program_path)))
    page = client.get(f"/accounts/{org.ref}/program").text

    for block in built["blocks"]:
        for line in block["lines"]:
            assert line in page, f"the panel dropped a line the renderer chose: {line!r}"
```

- [ ] **Step 2: Run and watch them fail**

Expected: FAIL — `tower-chart` is not in the page yet.

- [ ] **Step 3: Write `_tower_panel.html`**

```html
{# One program's tower. Every string here comes off the WebTower the renderer
   built (bookkit.web.tower.context) — this template composes none of its own,
   because a second renderer describing the same program is exactly what the
   R66 rule forbids (spec D2.1). Geometry is percentages, already converted.

   The chart's height is CSS (--tower-chart-height) and MUST match
   bookkit.web.tower.CHART_HEIGHT_PX, which is what the label-drop decisions
   were made against; tests/test_web_tower.py holds the two together. #}
<section class="tower" aria-label="Program tower — {{ caption }}">
  <div class="tower-head">
    <h2 class="tower-caption">{{ caption }}</h2>
    {% for group in tower.groups %}
      <span class="tower-group" style="left:{{ group.left }}%;width:{{ group.width }}%">{{ group.label }}</span>
    {% endfor %}
  </div>

  <div class="tower-chart">
    {% for line in tower.ref_lines %}
      <div class="tower-refline" style="bottom:{{ line.bottom }}%"><span>{{ line.label }}</span></div>
    {% endfor %}

    {% for layer in tower.layers %}
      {% for rect in layer.outlines %}
        <div class="tower-layer{% if layer.pending %} is-pending{% endif %}{% if layer.statutory %} is-statutory{% endif %}"
             style="left:{{ rect.left }}%;bottom:{{ rect.bottom }}%;width:{{ rect.width }}%;height:{{ rect.height }}%"
             title="{{ layer.name }} — {{ layer.terms }}"></div>
      {% endfor %}
    {% endfor %}

    {% for block in tower.blocks %}
      {% for rect in block.rects %}
        <div class="tower-block{% if block.unplaced %} is-unplaced{% endif %}"
             style="left:{{ rect.left }}%;bottom:{{ rect.bottom }}%;width:{{ rect.width }}%;height:{{ rect.height }}%">
          {% if loop.first %}
            {% for line in block.lines %}<span class="tower-line">{{ line }}</span>{% endfor %}
          {% endif %}
        </div>
      {% endfor %}
    {% endfor %}
  </div>

  <div class="tower-retentions">
    {% for retention in tower.retentions %}
      <span class="tower-retention">{{ retention.label }}</span>
    {% endfor %}
  </div>

  {% if tower.caveat %}
    <p class="tower-caveat">{{ tower.caveat }}</p>
  {% endif %}
</section>
```

- [ ] **Step 4: Add the CSS**

Append to `src/bookkit/web/static/app.css`, in one block with the
`--tower-chart-height` declaration Task 2 introduced:

```css
/* --- the tower panel (web/templates/account/_tower_panel.html) -------------
   Geometry arrives as percentages from bookkit.web.tower; nothing here
   recomputes it. The chart height is declared once, as a custom property,
   because the label-drop decisions were made against that exact number
   server-side — tests/test_web_tower.py holds the two together. */
.tower { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 1.5rem; }

.tower-head { position: relative; }

.tower-caption {
  margin: 0;
  font-size: var(--label-size);
  font-family: var(--mono);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
}

.tower-group { position: absolute; bottom: 0; font-size: var(--hint-size); color: var(--muted); }

.tower-chart {
  --tower-chart-height: 240px;
  position: relative;
  height: var(--tower-chart-height);
  border-bottom: 1px solid var(--ink);
}

.tower-refline { position: absolute; left: 0; right: 0; border-top: 1px dashed var(--grid); }
.tower-refline span { font-size: var(--hint-size); color: var(--muted); }

.tower-layer { position: absolute; border: 1px solid var(--border); }
.tower-layer.is-pending { border-style: dashed; }

.tower-block {
  position: absolute;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 0 0.25rem;
  background: var(--grid);
  border: 1px solid var(--border);
}

/* Unplaced capacity is hatched, not merely paler: colour alone is not a
   signal in this app, and "nobody is on this layer" is the fact a reader
   most needs to catch. */
.tower-block.is-unplaced {
  background: repeating-linear-gradient(
    45deg, var(--stone), var(--stone) 4px, var(--unplaced) 4px, var(--unplaced) 5px
  );
}

.tower-line {
  font-size: var(--hint-size);
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tower-retentions { display: flex; gap: 1rem; font-size: var(--hint-size); color: var(--muted); }
.tower-caveat { font-size: var(--hint-size); color: var(--muted); font-style: italic; }
```

- [ ] **Step 5: Run the tests**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q tests/test_web_program.py tests/test_web_tower.py
```
Expected: PASS.

- [ ] **Step 6: Look at it**

```bash
uv run --no-sync python -m bookkit.cli --db <a seeded demo db> web --port 8947 --no-browser
```
Open `/accounts/<ref>/program`. Confirm by eye: blocks sit inside their layer
outlines, unplaced capacity is hatched, the caveat prints, and nothing is
clipped at the panel's right edge. A screenshot at 1440 and 1180 belongs in
the handoff.

- [ ] **Step 7: Commit**

```bash
git add src/bookkit/web/templates/account/_tower_panel.html \
        src/bookkit/web/static/app.css tests/test_web_program.py
git commit -m "feat: the tower is drawn in the browser"
```

---

## Task 5: Gates, parity ledger, and the handoff

- [ ] **Step 1: Check the parity ledger needs nothing**

`web/parity.py`'s `show_tab` entry already covers the Program tab, and this
slice implements no TUI action: `edit_layer`, `add_layer`, `renew_placement`,
`scaffold_tower` and `open_towerkit` all stay PENDING until slices 2 and 6.
Run `tests/test_web_parity.py` and confirm it stays green **without edits** —
if it demands a change, read why before making one.

- [ ] **Step 2: Full gates**

```bash
find src tests -name __pycache__ -exec rm -rf {} + 2>/dev/null
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q > gate.txt 2>&1; tail -3 gate.txt
uv run --no-sync python -m mypy src
uv run --no-sync python -m ruff check src tests
```

Never pipe test output before an `&&` gate — pipes eat exit codes and red
suites get committed.

- [ ] **Step 3: Gate the MERGE commit, not just the branch**

Three times in this project two branches that each passed were wrong
together. Merge to main, confirm the merged tree is identical to the tree you
gated (`git diff --quiet <branch-tip> HEAD`), and re-run the suite if it is
not.

- [ ] **Step 4: Write the handoff**

`handoffs/2026-08-19-Tower-Panel.md` — goal, state, next step with function
names (**not** line numbers: they go stale within a day when several branches
land, and citing them has produced wrong references three times), decisions
and what was rejected, what was tried that failed, gotchas, open questions.

---

## Self-review

**Spec coverage.** D2 (HTML panel, no SVG on screen) — Tasks 2 and 4. D2.1
(R66 agreement) — Task 1 plus the four phase-A tests named in the table above,
and the bookkit-end test in Task 4 Step 1. D3 (no y-axis; the caveat) — the
caveat renders in Task 4; no axis is drawn anywhere. D4 (label-drop rule and
its base height) — enforced inside `build_web_tower`, and the base height is
pinned in both directions by Task 2's CSS test. D8 slice 1 (placements list
replacing the stub, panel read-only) — Task 3.

**Deliberately not covered here**, per D8: writes (slice 2), the conflict
three-way and the revert dispatcher (slice 3), `applies_to`/restack/drag
(slice 4), SVG export and the Towers page (slice 5), renew/scaffold/open
(slice 6). None of those may leak into this slice.

**Known soft spots in this plan, called out rather than hidden:**

1. `_context`'s exact signature is quoted from memory of `routes/pipeline.py`
   rather than transcribed. Task 3 Step 3 says to check it first.
2. The `cents` Jinja filter may not exist. Task 3 Step 4 says to check and
   gives the fallback.
3. The CSS is a first cut against the design prototype's shape, not a
   transcription of it. Expect a visual pass after Task 4 Step 6; the
   geometry is correct by construction, the styling is not yet reviewed.
4. `scaffold_program`'s signature in Task 4's fixture should be confirmed
   against `sync.py:687` — it returns `(Path | None, Diagnostics)`, which the
   fixture assumes.
