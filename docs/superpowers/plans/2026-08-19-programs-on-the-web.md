# Programs on the web — Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create, read, edit and draw insurance programs in the browser — the
whole loop, not a viewer.

**Architecture:** Every program write goes through the guarded towerkit file
cycle that already exists and is already in production behind the MCP server:
load → mutate → validate → canonical dump → re-project, sha256-guarded. The
web becomes the **second caller** of that path, never a second implementation
of it. Reading rides `sync.layer_details`; drawing rides towerkit's
`render/web.py`; editing rides the inline-cell contract the contacts, tasks
and request-items tables already use.

**Tech Stack:** FastAPI + Jinja2 + htmx (no new dependency), towerkit
`edit.py` / `render/web.py` / `render/labels.py` / `money.py`, bookkit
`sync.py` / `services/program_files.py`.

**Spec:** `docs/superpowers/specs/2026-08-17-towerkit-web-conversion.md`
— D5 (guarded writes), D6 (money and share), D7 (inline-cell seam), D2.1
(the R66 agreement rule), D3/D4 (scale and label-drop).

**Sequencing note — this plan reorders the spec's D8.** D8 put the drawn
tower first and creating a program last (slice 6). Grant, 2026-08-19: the
intent is CRUD — "create, edit programs and generate schematics and track
program info" — and on D8's order the create path arrives several slices after
the picture. The phases below put the working loop first and the graphic
fourth. The spec's risk-first instinct is kept where it still applies: the
renderer is still proven end-to-end before anything depends on its geometry,
and the write-conflict handling still gets its own phase and its own review.

## Global Constraints

- **Phases 2 and 3 write.** This is not a read-only project. Phase 1 is read
  only because it is the surface the editing rides on, not because writing is
  out of scope.
- **One seam, two callers.** `mcpserver._program_write`, `_raise_on_errors`
  and `_program_revert_file` move to `services/program_files.py`.
  `mcpserver.py` becomes a caller. Nothing about its tool contract changes.
  Copying them into a web route instead is the failure this constraint exists
  to prevent.
- **No string composed in bookkit for the tower.** Every label rendered in
  Phase 4 comes off a `WebBlock`/`WebLayer`/`WebGroup`/`WebRetention` field.
  An f-string that builds a carrier line, money line or heading is exactly the
  divergence R66 forbids.
- **No geometry recomputed in bookkit.** Rects arrive as `[0, 1]` floats and
  are multiplied by 100. Nothing else.
- **`chart_height_px` is the DRAWING AREA, never the panel box.** Passing the
  outer height under-fires every drop threshold and drops too few labels,
  silently. See `build_web_tower`'s docstring.
- **Money is cents on the wire, dollars in the file.** Entry accepts
  `"1,234.56"`; `sync._require_dollars` **refuses** sub-dollar amounts rather
  than rounding. That refusal must reach the field as its error text.
- **Shares are one rule, towerkit's.** `bookkit.money.parse_share_bps`
  delegates to `towerkit.money.parse_share`. Do not write a percent→bps
  conversion anywhere in this plan.
- **Route registration order.** Every route under `/accounts/{ref}/program...`
  must be registered **before** `account.router` in `web/app.py` — Starlette
  resolves across routers by registration order, not specificity.
- **Every write is one batch, `source="web"`.** Program batches use the same
  `tool` names the MCP tools already use (`program_layer_edit`,
  `program_layer_add`, `program_bind`) so the changes list stays uniform
  across surfaces.
- **`services.batches.revert` refuses program batches by design** — file
  contents are not event_log rows. The file-side revert is
  `program_files.restore` via the dispatcher (Phase 5).
- **Gates**: `uv run --no-sync python -m pytest -q`, `uv run --no-sync python
  -m mypy src`, `uv run --no-sync python -m ruff check src tests`. A bare
  `uv run pytest` falls through to Anaconda's and reports a bogus
  `ModuleNotFoundError: No module named 'bookkit'`.
- **towerkit work goes in its own `git worktree`** (Phase 4 only). Every
  bookkit worktree compiles against whatever branch is checked out in
  `/Users/grantgreeson/Developer/towerkit`.

---

## What already exists — do not rebuild it

Checked at bookkit `44e01a8` / towerkit `0405cca`, by reading the code:

| thing | where | state |
|---|---|---|
| the guarded write cycle | `sync.py _mutate`, `write_through` | built, in production via MCP |
| `update_layer` (name, policy_number, attach/limit/premium cents, period) | `sync.py update_layer` | built |
| `add_layer(name, line_ids, attach_cents, limit_cents, premium_cents)` | `sync.py add_layer` | built |
| `add_participant(layer_id, carrier, share_bps)` | `sync.py add_participant` | built |
| `scaffold_program(conn, placement_id, dest) -> (Path\|None, Diagnostics)` | `sync.py scaffold_program` | built |
| layers + participants of a placement, money in cents | `sync.py layer_details` | built |
| batched write + pre-image snapshot | `mcpserver._program_write` | built, **needs extracting** |
| file-side revert | `mcpserver._program_revert_file` | built, **needs extracting** |
| snapshot capture/restore | `services/program_files.py capture`, `restore` | built |
| whole-record placement form | `forms/entities.py placement_form` | built |
| inline-cell contract (3 routes + `Field` tuple) | `web/routes/relationship.py`, `forms/inline.py` | built, 3 tables use it |
| `WebTower` + `build_web_tower` | towerkit `render/web.py` | built, merged |
| Program tab route + count badge | `web/routes/account.py` generic `{tab}` | built |
| the Program panel | `web/templates/account/program.html` | **stub that lies** |

### New surface this plan must add (it does not exist yet — I checked)

- `sync.update_participant(conn, placement_id, layer_id, carrier, share_bps=…, new_carrier=…)`
- `sync.remove_participant(conn, placement_id, layer_id, carrier)`
- `sync.set_applies_to(conn, placement_id, layer_id, line_ids)` wrapping
  `towerkit.edit.set_applies_to`
- `Field.kind == "share"` in `forms/spec.py`
- `LAYER_FIELDS`, `PARTICIPANT_FIELDS` in `forms/inline.py`
- `ProgramWriteRefused(ValueError)` carrying `Diagnostics`

D7 assumed participant editing was covered by `add_participant`. It is not:
`sync.py` can add a market to a layer and cannot change or remove one. CRUD
means all four.

### The R66 agreement obligation is nearly discharged already

D2.1 deferred the agreement test's mechanics to this plan, having been broken
twice by specifying them against a module that did not exist. The module
exists now, and towerkit's `tests/test_render_web.py` already carries four of
the five facts.

| fact (D2.1) | test in towerkit | covered? |
|---|---|---|
| 1. Geometry — one `TowerLayout`, same gamma | `test_geometry_is_passed_through_not_recomputed` | rects **yes**, exact. Gamma **no** — Task 4.1 |
| 2. Label text quoted, never composed | `test_no_block_string_is_composed_here` | **yes**, set-membership |
| 3. Which block carries the heading | `test_the_heading_goes_where_labels_py_says` | **yes** |
| 4. Whether a layer is pending | `test_pending_is_decided_once` | **yes** |
| 5. Money and share formatting | folded into fact 2's universe | **yes** |

**The mechanics, settled:** the agreement test does **not** compare the two
renderers to each other. It compares each to the shared authority. That is why
it cannot conflate wrapping — a permitted fit difference — with fact
divergence, which is how both adversarial passes broke a per-block
string-comparison design. Membership in the authority's own output permits fit
to differ and forbids facts to.

---

## File structure

| file | responsibility | phase |
|---|---|---|
| `src/bookkit/web/routes/program.py` (create) | every Program tab route: list, cells, adds, create, scaffold | 1–5 |
| `src/bookkit/web/templates/account/program.html` (rewrite) | placements + layers, replacing the stub | 1 |
| `src/bookkit/web/templates/account/_layers_panel.html` (create) | the layer/participant tables — the editing surface | 1 |
| `src/bookkit/services/program_files.py` (modify) | gains `write`, `ProgramWriteRefused`, `revert_file` | 2 |
| `src/bookkit/mcpserver.py` (modify) | becomes a caller of the above; tool contract unchanged | 2 |
| `src/bookkit/forms/spec.py` (modify) | one new `Field.kind`, `"share"` | 2 |
| `src/bookkit/forms/inline.py` (modify) | `LAYER_FIELDS`, `PARTICIPANT_FIELDS` | 2 |
| `src/bookkit/sync.py` (modify) | `update_participant`, `remove_participant`, `set_applies_to` | 2 |
| `src/bookkit/web/tower.py` (create) | pure: `WebTower` → CSS percentages. `CHART_HEIGHT_PX` | 4 |
| `src/bookkit/web/templates/account/_tower_panel.html` (create) | the drawn tower | 4 |
| `src/bookkit/web/templates/account/_conflict.html` (create) | Reload / Overwrite / Keep editing | 5 |
| `tests/test_web_program.py` (create) | routes, reads, writes, seams | 1–5 |
| `tests/test_web_tower.py` (create) | pure percentage conversion | 4 |

---

# Phase 1 — See the programs (read)

The stub is worse than empty: the tab badge counts placements while the panel
prints "empty — add the first row", so an account with two programs reads as
an account with none. That contradiction is what made Grant conclude programs
could not be created at all. Phase 1 also builds the layer table that Phase 2
turns into the editor — the editing rides this surface, so it comes first.

## Task 1.1: The Program tab lists placements and their layers

**Files:**
- Create: `src/bookkit/web/routes/program.py`
- Create: `src/bookkit/web/templates/account/_layers_panel.html`
- Rewrite: `src/bookkit/web/templates/account/program.html`
- Modify: `src/bookkit/web/app.py`
- Test: `tests/test_web_program.py`

**Interfaces:**
- Consumes: `repo.placements.for_org(conn, org_id) -> list[Placement]`,
  `sync.layer_details(conn, placement_id) -> list[dict]`,
  `routes.account._conn / _org / _context`
- Produces: `router` exporting `GET /accounts/{ref}/program`

- [ ] **Step 1: Read the two things this task quotes**

`web/routes/pipeline.py` — copy its exact `_context(...)` call shape and its
router/template wiring; do not assume the argument order from this plan.
`web/routes/work.py` — the `_ITEM_CELL_CLASS` map and `prose` column class,
which the layer table reuses.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_web_program.py`:

```python
"""The Program tab: placements, their layers, and the markets on them.

Writes arrive in Phase 2. What this file asserts about Phase 1 is that the
panel stops contradicting the badge above it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import db
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

    assert "empty — add the first row" not in client.get(f"/accounts/{org.ref}/program").text


def test_an_account_with_no_placements_says_so_honestly(snapshot_db: Path):
    from bookkit.repo import orgs

    app = create_app(snapshot_db)
    bare = orgs.create(app.state.conn, kind="client", name="No Programs Co")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        page = client.get(f"/accounts/{bare.ref}/program")

    assert page.status_code == 200
    assert "no programs on this account" in page.text
```

- [ ] **Step 3: Run them and watch them fail**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q tests/test_web_program.py
```
Expected: FAIL — the stub renders the empty state and no placement names.

- [ ] **Step 4: Write `src/bookkit/web/routes/program.py`**

```python
"""The Program tab: placements, layers, the markets on them, and the tower.

Registered BEFORE account.router in app.py: this module's
GET /accounts/{ref}/program and account.py's generic GET /accounts/{ref}/{tab}
match the same two-segment path, and Starlette resolves across routers by
registration order rather than specificity.

Every write here goes through services.program_files.write — the same batched,
snapshot-taking wrapper the MCP server uses. A direct sync.* call from a route
would write outside a batch and leave no pre-image, which is the one thing
that makes a program write unrevertible."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ... import sync
from ...repo import placements as placements_repo
from ..app import TEMPLATES
from .account import _conn, _context, _org

router = APIRouter()


def _programs(request: Request, org: Any) -> list[dict[str, Any]]:
    """Each placement with its layers. ONE layer_details call per placement:
    it opens and parses the towerkit file, so a second caller is a second disk
    read of the same bytes."""
    conn = _conn(request)
    return [
        {"placement": placement, "layers": sync.layer_details(conn, placement.id)}
        for placement in placements_repo.for_org(conn, org.id)
    ]


@router.get("/accounts/{ref}/program", response_class=HTMLResponse)
def program_tab(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    context = {
        **_context(request, org, "program"),
        "programs": _programs(request, org),
    }
    return TEMPLATES.TemplateResponse(request, "account/program.html", context)
```

- [ ] **Step 5: Write the templates**

`src/bookkit/web/templates/account/program.html`:

```html
{% extends "account/page.html" %}
{% block panel %}
  {# The stub this replaces printed the addable-list empty state
     unconditionally, while the tab badge counted the placements it was
     claiming did not exist. #}
  {% if programs %}
    {% for program in programs %}
      {% with placement = program.placement, layers = program.layers %}
        {% include "account/_layers_panel.html" %}
      {% endwith %}
    {% endfor %}
  {% else %}
    <div class="panel-empty">
      <p>no programs on this account</p>
    </div>
  {% endif %}
{% endblock %}
```

`src/bookkit/web/templates/account/_layers_panel.html`:

```html
{# One placement: its header facts, then its layers and the markets on each.
   This table is the surface Phase 2 turns into the editor, so its columns are
   the layer's editable fields (forms.inline.LAYER_FIELDS) and nothing else.

   rows-fit + prose: a layer name is prose and would otherwise size the table
   to its longest unwrapped value and push the columns to its right off the
   panel — the bug fixed on the request items table on 2026-08-19. #}
<section class="program" id="program-{{ placement.id }}">
  <div class="people-head">
    <h2 class="people-label">{{ placement.program_name }}</h2>
    <span class="people-count mono">{{ placement.ref }}</span>
    <span class="people-head-spacer"></span>
    <span class="hint mono">{{ placement.period_from }} → {{ placement.period_to }}</span>
  </div>

  {% if layers %}
    <div class="table-scroll">
      <table class="rows rows-fit">
        <thead>
          <tr><th>Layer</th><th>Attaches</th><th>Limit</th><th>Premium</th>
              <th>Signed</th><th>Markets</th></tr>
        </thead>
        <tbody>
          {% for layer in layers %}
            <tr>
              <td class="prose">{{ layer.name }}</td>
              <td class="num">{{ layer.attach_cents | cents }}</td>
              <td class="num">{% if layer.statutory %}statutory{% else %}{{ layer.limit_cents | cents }}{% endif %}</td>
              <td class="num">{% if layer.premium_cents is none %}—{% else %}{{ layer.premium_cents | cents }}{% endif %}</td>
              <td class="num">{{ layer.signed_pct }}%</td>
              <td class="prose">
                {% if layer.participants %}
                  {% for part in layer.participants %}
                    <span class="market">{{ part.carrier }} {{ part.share_pct }}%</span>
                  {% endfor %}
                {% else %}
                  <span class="unplaced">To be placed</span>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% elif placement.program_path %}
    <div class="panel-empty">
      <p>the linked file has no layers yet</p>
    </div>
  {% else %}
    <div class="panel-empty">
      <p>no program file linked — nothing to draw or edit yet</p>
    </div>
  {% endif %}
</section>
```

The three-way empty state is deliberate: "no file linked", "a file with no
layers" and "layers present" are three different facts, and collapsing the
first two is how the stub came to lie in the first place.

**Check whether a `cents` Jinja filter exists** in this codebase's environment
before using it. If it does not, format with `money.format_cents` in
`_programs()` and pass the strings — do not add a filter in this task.

- [ ] **Step 6: Register the router before account.router**

In `src/bookkit/web/app.py`:

```python
    from .routes import account, book, changes, pipeline, program, relationship, work
    ...
    app.include_router(program.router)   # before account.router — see the comment above
    app.include_router(account.router)
```

- [ ] **Step 7: Run the tests**

Expected: PASS.

- [ ] **Step 8: Prove the route order matters**

Move `include_router(program.router)` after `account.router`, re-run, watch
the generic `{tab}` route win and the placement-name assertion fail. Restore
by editing back — **never `git checkout -- <file>`**, which discards
uncommitted work and has cost this project mutation proofs before.

- [ ] **Step 9: Gate and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q > gate.txt 2>&1; tail -3 gate.txt
uv run --no-sync python -m mypy src && uv run --no-sync python -m ruff check src tests
git add src/bookkit/web/routes/program.py src/bookkit/web/templates/account/ \
        src/bookkit/web/app.py tests/test_web_program.py
git commit -m "feat: the Program tab shows the programs it has been counting"
```

---

# Phase 2 — Edit them (write)

## Task 2.1: Extract the write wrapper into a service

**Files:**
- Modify: `src/bookkit/services/program_files.py`
- Modify: `src/bookkit/mcpserver.py`
- Test: `tests/test_mcp_program.py` (must stay green, unmodified)

**Interfaces:**
- Produces:
  - `class ProgramWriteRefused(ValueError)` with `.diags`
  - `def write(conn, placement, tool, summary, mutate, *, open_batch) -> tuple[Any, list[str]]`
  - `def raise_on_errors(diags) -> list[str]`

- [ ] **Step 1: Read `mcpserver._program_write`, `_raise_on_errors` and `_open_batch`**

Transcribe them; do not retype from this plan. `_program_write` reads the
pre-image **before** opening the batch and calls `program_files.capture`
**after** a successful write, so a refused write leaves no snapshot debris.
Both properties must survive the move.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_web_program.py`:

```python
def test_the_write_wrapper_refuses_with_the_diagnostics_intact():
    """A flat string is enough for an MCP client and not enough for the web:
    the route has to tell an ordinary validation refusal (re-render the cell
    with the message) from a conflict (offer Reload/Overwrite/Keep editing),
    and only the diagnostics carry the code that distinguishes them."""
    from towerkit.diagnostics import Diagnostics

    from bookkit.services.program_files import ProgramWriteRefused, raise_on_errors

    diags = Diagnostics()
    diags.error("the file moved under this write", code="conflict")

    with pytest.raises(ProgramWriteRefused) as refused:
        raise_on_errors(diags)

    assert refused.value.diags is diags
    assert any(d.code == "conflict" for d in refused.value.diags.errors)
    assert "moved under" in str(refused.value)
```

Check `towerkit.diagnostics.Diagnostics`'s real constructor and `error()`
signature before running — this test's fixture is written from the shape
`sync._mutate` uses, not transcribed.

- [ ] **Step 3: Run it and watch it fail**

Expected: FAIL — `ImportError: cannot import name 'ProgramWriteRefused'`.

- [ ] **Step 4: Move the code**

Add to `services/program_files.py`:

```python
class ProgramWriteRefused(ValueError):
    """A program write towerkit's validator refused — nothing was written.

    Carries the Diagnostics, not a flattened string, because the web has to
    tell two refusals apart that the MCP server does not: an ordinary
    validation refusal (over-signed layer, sub-dollar money, a layer id that
    no longer exists), which re-renders the cell with the message, and a
    conflict (code == "conflict"), which offers Reload / Overwrite / Keep
    editing. Being a ValueError means the ordinary case still rolls the batch
    back with no new code."""

    def __init__(self, diags: Any) -> None:
        self.diags = diags
        super().__init__("; ".join(d.message for d in diags.errors))
```

`raise_on_errors(diags)` keeps `_raise_on_errors`'s behaviour and raises
`ProgramWriteRefused(diags)` instead of a bare `ValueError`. `write(...)`
keeps `_program_write`'s body verbatim, taking `open_batch` as a parameter so
the service does not import the MCP server. `mcpserver._program_write` and
`_raise_on_errors` become one-line delegations.

- [ ] **Step 5: Run the whole MCP program suite unmodified**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q \
  tests/test_mcp_program.py tests/test_mcpserver.py tests/test_web_program.py
```
Expected: PASS with **no edits to the MCP tests** — that is what proves the
extraction preserved behaviour rather than reimplemented it. If an MCP test
needs changing, the move was wrong; revisit before editing the test.

- [ ] **Step 6: Commit**

```bash
git commit -am "refactor: one program-write seam, two callers"
```

## Task 2.2: The `share` field kind and the inline field tuples

**Files:**
- Modify: `src/bookkit/forms/spec.py`
- Modify: `src/bookkit/forms/inline.py`
- Test: `tests/test_form_entry.py`, `tests/test_web_program.py`

**Interfaces:**
- Produces: `Field.kind == "share"`; `LAYER_FIELDS`, `PARTICIPANT_FIELDS`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_share_parses_through_towerkits_one_rule():
    """CLAUDE.md: one percent→bps rule, owned by towerkit money.parse_share;
    bookkit delegates. A second conversion here is how 33.33% becomes 3333
    bps in one place and 333300 in another."""
    from bookkit.forms.spec import Field, parse_value

    field = Field("share_pct", "share", "share")

    assert parse_value(field, "33.33%") == parse_value(field, "33.33")
    assert parse_value(field, "100%") == 10_000


def test_a_share_over_one_hundred_percent_is_refused():
    from bookkit.forms.spec import Field, parse_value

    with pytest.raises(ValueError):
        parse_value(Field("share_pct", "share", "share"), "140%")
```

Confirm the second assertion against `towerkit.money.parse_share`'s actual
behaviour before implementing — if towerkit permits >100% (over-signing is
refused by the validator, not the parser), delete this test rather than
weaken the parser to satisfy it, and note why in the commit.

- [ ] **Step 2: Run, watch fail, implement**

In `forms/spec.py`: add `"share"` to the kind comment, to `CLEANERS`, and to
`parse_value`'s branch table, wired to `bookkit.money.parse_share_bps`.
Display goes through `towerkit.money.format_share`.

In `forms/inline.py`:

```python
LAYER_FIELDS: tuple[Field, ...] = (
    Field("name", "layer", required=True),
    Field("policy_number", "policy no"),
    Field("attach_cents", "attaches at", "money"),
    Field("limit_cents", "limit", "money"),
    Field("premium_cents", "premium", "money"),
    Field("period_from", "from", "date"),
    Field("period_to", "to", "date"),
)
"""Keys are `sync.update_layer`'s own keyword names, so a cell route passes
`**{key: value}` straight through. `signed_pct` and `statutory` are DERIVED
(the sum of the participants' shares; a towerkit model rule) and are not here
— a cell that edits a derived value writes nothing and reads as broken."""

PARTICIPANT_FIELDS: tuple[Field, ...] = (
    Field("carrier", "market", required=True),
    Field("share_pct", "share", "share"),
)
```

- [ ] **Step 3: Commit**

```bash
git commit -am "feat: a share is a field kind, and a layer has an inline field list"
```

## Task 2.3: `sync.py` gains the three missing writers

D7 assumed participant editing rode `add_participant`. It does not exist:
`sync.py` can add a market to a layer and cannot change or remove one.

**Files:**
- Modify: `src/bookkit/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Produces:
  - `update_participant(conn, placement_id, layer_id, carrier, *, share_bps=None, new_carrier=None) -> Diagnostics`
  - `remove_participant(conn, placement_id, layer_id, carrier) -> Diagnostics`
  - `set_applies_to(conn, placement_id, layer_id, line_ids) -> Diagnostics`

- [ ] **Step 1: Write the failing tests**

One per writer, each asserting the FILE changed and the projection followed —
not merely that `Diagnostics.ok` was true. Model them on the existing
`add_participant` tests in `tests/test_sync.py`; read those first.

Include the refusal cases, which are the point:
- `update_participant` on a carrier not on the layer refuses and names who is.
- `remove_participant` of the only participant leaves the layer *pending*
  (`To be placed`), not deleted — losing a layer because its last market fell
  away destroys the tower's shape.
- `set_applies_to` with a line id the program does not have refuses.

- [ ] **Step 2: Implement — five-line write-through wrappers**

Each follows `update_layer`'s exact shape: a `mutate(program)` closure passed
to `_mutate(conn, placement_id, mutate)`. `set_applies_to` calls
`towerkit.edit.set_applies_to` rather than assigning the list directly — the
same rule `add_layer` follows for `edit.add_layer`.

**Opportunistic fix while here** (spec open question 3, my recommendation
Grant has not overruled): `sync.py`'s existing direct `.append()` calls should
go through `towerkit.edit` in this task, since it is already in this
neighbourhood. If that turns out to be more than a mechanical change, stop and
leave it — it is orthogonal debt, not this plan's job.

- [ ] **Step 3: Commit**

```bash
git commit -am "feat: a market can be corrected and removed, not only added"
```

## Task 2.4: Layer cells edit in place

**Files:**
- Modify: `src/bookkit/web/routes/program.py`
- Modify: `src/bookkit/web/templates/account/_layers_panel.html`
- Test: `tests/test_web_program.py`

**Interfaces:**
- Produces: `GET/POST /accounts/{ref}/program/{placement_id}/layers/{layer_id}/cell/{key}`
  and `.../cell/{key}/edit`

- [ ] **Step 1: Read `web/routes/work.py`'s three cell routes and copy their shape**

Including `_ITEM_CELL_CLASS` — the column class must come from **one map**,
consulted by the panel render, the display route and the editor. Three
literals is how a cell loses its formatting the moment it is edited
(fixed on the items table, 2026-08-19).

- [ ] **Step 2: Write the failing seam test**

```python
def test_editing_a_layer_writes_the_file_and_leaves_a_revertible_batch(app_and_org, tmp_path):
    """The seam, not the outcome. A response that merely looks right passes
    even when the route wrote outside a batch and left no pre-image — which is
    exactly what makes a program write unrevertible. Assert the batch, its
    source, its tool, the snapshot, and the bytes on disk."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _placement_with_file(conn, org, tmp_path)   # helper from Task 3.2
    before = Path(placement.program_path).read_bytes()
    layer_id = sync.layer_details(conn, placement.id)[0]["id"]

    saved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/cell/name",
        data={"name": "Primary Casualty"},
    )

    assert saved.status_code == 200
    assert Path(placement.program_path).read_bytes() != before, "the file did not change"
    batch = _latest_batch(conn)
    assert batch.source == "web" and batch.tool == "program_layer_edit"
    snapshot = Path(placement.program_path).parent / ".mcp-snapshots" / f"{batch.ref}.json"
    assert snapshot.exists(), "no pre-image captured — this write cannot be reverted"


def test_a_sub_dollar_premium_is_refused_not_rounded(app_and_org, tmp_path):
    """towerkit files carry whole dollars. Rounding here silently changes a
    client's premium; refusing says so."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _placement_with_file(conn, org, tmp_path)
    before = Path(placement.program_path).read_bytes()
    layer_id = sync.layer_details(conn, placement.id)[0]["id"]

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/cell/premium_cents",
        data={"premium_cents": "1,234.56"},
    )

    assert refused.status_code == 200
    assert "1,234.56" in refused.text, "the typed value was not kept for correction"
    assert Path(placement.program_path).read_bytes() == before, "the file was written anyway"
```

- [ ] **Step 3: Implement the three routes**

The POST resolves the field from `LAYER_FIELDS`, parses with `parse_value`,
and calls `program_files.write(...)` with
`lambda: sync.update_layer(conn, placement_id, layer_id, **{key: value})`,
`tool="program_layer_edit"`. `ProgramWriteRefused` is caught and re-renders
the editor cell with `str(exc)` and the typed value — the same shape
`item_cell_save` already has.

**Do not add conflict handling yet.** A conflict arrives as an ordinary
refusal message in this phase; Phase 5 gives it its own partial. That is the
spec's own staging (D8 slice 2's "no conflict UI yet") and it is right: the
conflict path deserves its own review rather than riding in on a phase that is
already large.

- [ ] **Step 4: Make the cells clickable in the template**

Replace the plain `<td>`s for the `LAYER_FIELDS` keys with
`render_cell_display(...)` output, exactly as `_items_panel.html` does. Keep
`signed_pct`, `statutory` and the markets column as plain cells — they are
derived or edited elsewhere.

- [ ] **Step 5: Run, gate, commit**

```bash
git commit -am "feat: a layer is edited where it is read"
```

## Task 2.5: Add a layer, add / correct / remove a market

**Files:**
- Modify: `src/bookkit/web/routes/program.py`, `_layers_panel.html`
- Test: `tests/test_web_program.py`

- [ ] **Step 1: Write the failing tests**

- adding a layer appends it as pending (`To be placed`) with the typed
  name/attach/limit, in one `program_layer_add` batch with a snapshot;
- adding a market that over-signs the layer is refused by towerkit's
  validator, the file is unchanged, and the message names the over-sign;
- removing the only market leaves the layer pending rather than removing it.

- [ ] **Step 2: Implement the ghost-row adds**

Follow the visual direction's add pattern already built for contacts and
request items: a `+ Add layer` / `+ Add market` control that renders a form
into the panel's `.form-host`, posting to
`POST /accounts/{ref}/program/{placement_id}/layers` and
`.../layers/{layer_id}/participants`. Both go through `program_files.write`
with tools `program_layer_add` and `program_bind`.

- [ ] **Step 3: Run, gate, commit**

```bash
git commit -am "feat: layers and markets are added, corrected and removed in the browser"
```

---

# Phase 3 — Create them

## Task 3.1: A new placement on the Program tab

**Files:**
- Modify: `src/bookkit/web/routes/program.py`, `program.html`, `web/parity.py`
- Test: `tests/test_web_program.py`

- [ ] **Step 1: Write the failing test** — `POST .../program/placements` with
  `placement_form`'s fields creates the placement, in one `source="web"`
  batch, and it appears on the tab.

- [ ] **Step 2: Implement** using the existing whole-record form seam:
  `forms.entities.placement_form(conn=conn)` + `routes.account._save`, exactly
  as `request_new`/`task_new` do. No new form builder — `placement_form`
  already exists.

- [ ] **Step 3: Update `web/parity.py`** — `add_here`'s note names the
  placements tab as one of the ones without an add. Update that sentence to
  match what is now true. Run `tests/test_web_parity.py`.

- [ ] **Step 4: Commit**

## Task 3.2: Scaffold the towerkit file

**Files:**
- Modify: `src/bookkit/web/routes/program.py`, `_layers_panel.html`
- Test: `tests/test_web_program.py`

**Interfaces:**
- Produces: test helper `_placement_with_file(conn, org, tmp_path)` used by
  Phase 2's tests — write it here and import it there, or define it in a
  shared fixture module. Both phases need it; only one may define it.

- [ ] **Step 1: Read `tui/screens/account.py action_scaffold_tower`**

It is the behaviour to mirror: refuse when the placement already has a file
(naming it), refuse when no program root is configured (pointing at the
setting), and default the destination to
`roots[0] / f"{slug}-{year}.json"` where `slug` is the first two words of the
org name and `year` is `placement.period_from[:4]`.

- [ ] **Step 2: Write the failing tests**

- scaffolding writes a file at the default path, links it to the placement,
  and the tab then shows the layer table rather than "no program file linked";
- scaffolding a placement that already has a file is refused, and the refusal
  names the existing path;
- with no configured root, the refusal points at the setting instead of
  writing anywhere.

- [ ] **Step 3: Implement** — a confirm-then-POST route
  (`GET .../program/{placement_id}/scaffold` renders the destination for
  confirmation and writes nothing; `POST` creates it), matching the
  server-rendered confirm pattern used for contact removal. Creating a file is
  exactly the kind of thing that gets a confirmation screen.

- [ ] **Step 4: Gate and commit**

```bash
git commit -m "feat: a program file is created from the browser"
```

---

# Phase 4 — Draw them

## Task 4.1: Both renderers agree on the gamma (towerkit)

The last unclosed R66 fact. `build_web_tower` and `render_program` each
default `gamma` to `scale.DEFAULT_GAMMA`, and nothing asserts they stay equal.
Change one default and both suites stay green while the panel and the export
describe two different towers.

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/tests/test_render_web.py`

- [ ] **Step 1: Create the towerkit worktree**

```bash
cd /Users/grantgreeson/Developer/towerkit
git worktree add .claude/worktrees/web-gamma -b web-gamma-agreement
cd .claude/worktrees/web-gamma && uv sync --group dev
```

- [ ] **Step 2: Write the test**

```python
class TestBothRenderersShareOneScale:
    """R66 fact 1 has two halves. The rects are checked exactly by
    test_geometry_is_passed_through_not_recomputed; this is the other half —
    the two renderers must build those rects at the SAME gamma. Nothing else
    catches a change to one default: both signatures stay valid, both suites
    stay green, and the panel and the export quietly describe two different
    towers."""

    def test_the_panel_and_the_export_default_to_the_same_gamma(self) -> None:
        import inspect

        from towerkit.render.mpl_program import render_program
        from towerkit.render.web import build_web_tower
        from towerkit.scale import DEFAULT_GAMMA

        panel = inspect.signature(build_web_tower).parameters["gamma"].default
        export = inspect.signature(render_program).parameters["gamma"].default

        assert panel == export == DEFAULT_GAMMA
```

- [ ] **Step 3: Mutate to prove it fails** — set `build_web_tower`'s default
  to `1.0`, re-run, expect FAIL naming both values, restore by editing back.

- [ ] **Step 4: Gate and commit.** Note the one known environmental failure:
  `tests/test_connector.py::test_roots_fall_back_to_bookkits_configuration`
  fails on towerkit `main` too and does not fail inside a fresh worktree.

## Task 4.2: The percentage conversion

**Files:**
- Create: `src/bookkit/web/tower.py`
- Test: `tests/test_web_tower.py`

**Interfaces:**
- Produces: `CHART_HEIGHT_PX: float = 240.0`, `pct(value) -> float`,
  `panel(program) -> dict`, `context(web_tower) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
"""The last mile: [0, 1] geometry to CSS percentages, and nothing else.

Pure — no database, no request, no browser. Percentage arithmetic in a Jinja
template is arithmetic no test can reach, which is why this module exists."""

from __future__ import annotations

from bookkit.web import tower


def test_the_unit_interval_becomes_percent():
    assert tower.pct(0.0) == 0.0
    assert tower.pct(1.0) == 100.0


def test_a_percentage_is_rounded_not_truncated():
    """A truncated width leaves a hairline gap between adjacent blocks that
    reads as a missing participant."""
    assert tower.pct(1 / 3) == 33.3333


def test_the_chart_height_is_the_drawing_area_not_the_panel_box():
    """build_web_tower's docstring: the design prototype's outer panel is
    340px and its drawing area ~240px. Passing 340 makes every block look 42%
    taller than it renders, UNDER-fires every drop threshold and drops too few
    labels — silently, since both are positive floats."""
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

- [ ] **Step 2: Implement `web/tower.py`**

`panel(program)` calls `build_web_tower(program, CHART_HEIGHT_PX)` and hands
the result to `context(web)`, which converts every rect through `pct` and
carries each block's `lines`, `unplaced` flag, each layer's `name`/`terms`/
`pending`/`statutory`, the ref lines, retentions, groups, chevrons and the
caveat. It composes **no strings**: a carrier line, money line or heading built
here would be a second renderer describing the same program.

- [ ] **Step 3: Mutate `CHART_HEIGHT_PX` to 340.0**, watch the CSS test fail,
  restore.

- [ ] **Step 4: Commit**

## Task 4.3: The tower panel

**Files:**
- Create: `src/bookkit/web/templates/account/_tower_panel.html`
- Modify: `src/bookkit/web/static/app.css`, `_layers_panel.html`
- Test: `tests/test_web_program.py`

- [ ] **Step 1: Write the failing R66-at-the-bookkit-end test**

```python
def test_the_panel_prints_no_string_the_renderer_did_not_give_it(app_and_org, tmp_path):
    """towerkit's suite proves the renderer quotes labels.py. This proves
    bookkit prints what the renderer handed it rather than composing a line of
    its own — the same rule, at the other end of the seam."""
    from towerkit.io import load_program

    from bookkit.web.tower import panel

    client, org = app_and_org
    placement = _placement_with_file(client.app.state.conn, org, tmp_path)
    built = panel(load_program(Path(placement.program_path)))

    page = client.get(f"/accounts/{org.ref}/program").text

    for block in built["blocks"]:
        for line in block["lines"]:
            assert line in page, f"the panel dropped a line the renderer chose: {line!r}"
```

Confirm towerkit's loader import path (`towerkit.io` vs `towerkit.model`)
before running — `sync.py` imports `load_program`; copy from there.

- [ ] **Step 2: Implement the template** — absolutely-positioned divs at the
  percentages from `web.tower`, layer outlines behind participant blocks, ref
  lines, group bands, retention labels, and the caveat rendered **outside**
  any clipped container. Unplaced capacity is hatched, not merely paler:
  colour alone is not a signal in this app.

- [ ] **Step 3: Add the CSS**, declaring `--tower-chart-height: 240px` on
  `.tower-chart` so Task 4.2's test has something to hold.

- [ ] **Step 4: Look at it.** Serve a seeded book, open the tab, check at 1440
  and 1180 that blocks sit inside their outlines, hatching reads, the caveat
  prints, and nothing is clipped at the panel's right edge. Screenshots go in
  the handoff.

- [ ] **Step 5: Gate and commit**

---

# Phase 5 — Protect them

## Task 5.1: The conflict three-way

**Files:**
- Create: `src/bookkit/web/templates/account/_conflict.html`
- Modify: `src/bookkit/web/routes/program.py`
- Test: `tests/test_web_program.py`

- [ ] **Step 1: Write the failing test** (the spec names this one explicitly)

Write the file out-of-band to simulate the towerkit TUI, then POST the same
layer edit through the web. Assert: the response is the conflict partial and
not a generic refusal; nothing was written; and **Reload then Overwrite lands
the user's edit on top of the out-of-band change rather than clobbering it**.
That last clause is the whole design claim and is otherwise just a paragraph.

- [ ] **Step 2: Implement**

The route inspects `exc.diags.errors` for `code == "conflict"` and renders the
three-way instead of the ordinary refusal:
- **Reload** — `sync.project(conn, path)` so the recorded `source_sha256`
  catches up, discard the draft, re-render the cell from the fresh read.
- **Overwrite** — re-project, then re-apply the *same single mutation*. It is
  a retry, not a clobber: `write_through` loads fresh on every call, so no
  `force` parameter is needed on `sync.write_through`, and a structural change
  someone else made (a new layer) survives.
- **Keep editing** — leave the field open with the typed value and the message.

**This is deliberately narrower than towerkit's own TUI offers.**
`EditSession.save(force=True)` force-writes the entire in-memory program,
which is correct there (one long-lived session) and wrong here (each POST is
one field, freshly loaded).

- [ ] **Step 3: Gate and commit**

## Task 5.2: The right-rail revert, for program batches

- [ ] **Step 1: Write the failing test** — reverting a `program_*` batch from
  the rail restores the file's pre-image, and refuses (naming why) when the
  file has moved since.

- [ ] **Step 2: Implement** by moving `mcpserver._program_revert_file` into
  `services/program_files.py` and dispatching to it from the rail's revert
  route when `batch.tool.startswith("program_")`. `services.batches.revert`
  already refuses those batches; the dispatcher is what makes the refusal
  actionable instead of a dead end.

- [ ] **Step 3: Flip `web/parity.py`** — `edit_layer` and `add_layer` to
  IMPLEMENTED; `renew_placement` and `scaffold_tower` per what Phase 3 shipped
  (`scaffold_tower` yes, `renew_placement` no). `open_towerkit` stays PENDING
  on the projects side either way. Run `tests/test_web_parity.py` and read any
  failure before editing it.

- [ ] **Step 4: Full gates, gate the MERGE commit, write the handoff**

Three times in this project two branches that each passed were wrong together.
Merge to main, confirm the merged tree is identical to the gated tree
(`git diff --quiet <branch-tip> HEAD`), and re-run if it is not. Handoff goes
in `handoffs/2026-08-19-Programs-On-The-Web.md`, citing **function names, not
line numbers** — line citations went stale three times in one night.

---

## Deliberately not in this plan

- **`renew_placement`** (D8 slice 6). Renewal is a program-identity question
  the roadmap has an open spec entry for; wiring the button before that is
  settled would bake in an answer.
- **`applies_to` chip toggle, restack, drag-to-resize** (D8 slice 4).
  `sync.set_applies_to` is built in Task 2.3 so the write exists, but the
  chip UI and the drag island are their own slice.
- **SVG/PDF export, the Towers browser page, the Compare screen** (D8 slice 5).
  Export needs the file-download response the web spec does not yet cover.
- **Live drag-preview geometry** — see the spec's "recommend against".
- **The layers-sheet bulk edit** and **xlsx SOI export** — see "what should
  not be ported".

## Self-review

**Spec coverage.** D5 (guarded writes, extracted seam, conflict three-way,
revert dispatcher) — Tasks 2.1, 2.4, 5.1, 5.2. D6 (money cents-on-the-wire,
the `share` kind) — Task 2.2, with the refusal asserted in 2.4. D7 (inline
cell seam, `LAYER_FIELDS`/`PARTICIPANT_FIELDS`, ghost-row adds, no new
FormSpec) — Tasks 2.2, 2.4, 2.5. D2/D3/D4 (the panel, its scale, its
label-drop base height) — Tasks 4.2, 4.3. D2.1 (R66) — Task 4.1 plus the four
phase-A tests, plus the bookkit-end test in 4.3.

**Where this plan departs from the spec, and why:** D8's slice order. Stated
at the top; approved by Grant on 2026-08-19.

**Known soft spots, called out rather than hidden:**

1. `_context`'s exact signature is quoted from memory of `routes/pipeline.py`.
   Task 1.1 Step 1 says to read it first.
2. The `cents` Jinja filter may not exist. Task 1.1 Step 5 gives the fallback.
3. `towerkit.diagnostics.Diagnostics`'s constructor and `error()` signature in
   Task 2.1's test are written from the shape `sync._mutate` uses, not
   transcribed. Check before running.
4. Whether `towerkit.money.parse_share` refuses >100% is unverified; Task 2.2
   says to check and delete the test rather than weaken the parser.
5. `towerkit`'s loader import path (`towerkit.io` vs `towerkit.model`) differs
   between call sites in this repo; copy from `sync.py`.
6. The removal semantics for the last participant on a layer (Task 2.3) are my
   reading of what a tower should do, not a rule found in towerkit. If
   towerkit's validator disagrees, towerkit wins.
