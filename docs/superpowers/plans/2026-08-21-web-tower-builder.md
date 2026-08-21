# Web Tower Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a wrong tower hard to BUILD in the browser — attachment computed from stack position rather than typed, carriers added on the slab they share, and a deliberate uninsured band expressible as a buffer instead of a false `GAP`.

**Architecture:** A per-line stack editor on bookkit's Program tab is the model; the existing towerkit-rendered drawing stays a read view of it. Every write goes through `services.program_files.write` → `sync.*` → `towerkit.edit.*`, so it is validated, snapshotted and revertible; no new towerkit VERBS are needed, only one new model FIELD (`Layer.buffer`) and the validator rule that goes with it.

**Tech Stack:** Python 3.12, FastAPI + Jinja2 + htmx (bookkit web), pydantic v2 (towerkit model), pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-web-tower-builder-design.md`

## Global Constraints

- **Two repos, towerkit first.** `Layer.buffer` lands in `../towerkit` and is pushed before bookkit reads it. `./bookctl web` now refuses to start on that skew (`src/bookkit/doctor.py`), which is the protection — do not work around it.
- **Gates before every commit:** `uv run --no-sync python -m pytest -q`, `uv run --no-sync python -m mypy src`, `uv run --no-sync python -m ruff check src tests`. In towerkit: `uv run --group dev pytest -q`, `uv run --group dev mypy src`, `uv run --group dev ruff check src tests`. Never pipe test output before a `&&` gate — redirect to a file and tail it.
- **towerkit schema is derived.** After ANY change to `towerkit/model.py`, run `uv run --group dev python tools/sync_schema.py`, then add the human `description` by hand to BOTH `schema/program.schema.json` and `src/towerkit/schema/program.schema.json`, then `tools/sync_schema.py --check` must print "schema is in sync with model.py".
- **towerkit's reviewed-count gate:** adding a writable Layer field turns `tests/test_mcp_surface.py::test_the_writable_counts_match_the_reviewed_contract` red. That is deliberate — update the count AND the docstring reason.
- **bookkit's parity ledgers:** a new towerkit field turns `tests/test_web_parity.py` red until `web/parity.py::TOWERKIT_MODEL_FIELDS` names it. The red test IS the ticket.
- **Money is integer CENTS in bookkit and whole DOLLARS in towerkit files.** Conversion only in `sync.py` (`_require_dollars`) / `money.py`.
- **Never pre-fill a figure that comes off a document** (`.claude/skills/data-entry-integrity/SKILL.md`, rule 8). `limit` and `premium` inputs start empty.
- **Every `<select>` renders a blank option**, required or not; `forms.spec.checked_option` re-checks server-side.
- **One writer action is one undo unit.** `db.transaction` nests by JOINING and an inner `batch=` is ignored, so the outermost action owns the batch.
- **Every finding/refusal names the fix.** htmx swaps nothing on 4xx/5xx — a route that refuses must answer with the fragment carrying the reason, never a bare status.
- **Verify tests can fail.** After each task, mutate the production line the test guards and confirm the test goes red. A guard whose deletion leaves the suite green is not a guard.

---

### Task 1: `Layer.buffer` in towerkit

**Files:**
- Modify: `../towerkit/src/towerkit/model.py` (the `Layer` class, after `auditable`)
- Modify: `../towerkit/schema/program.schema.json`, `../towerkit/src/towerkit/schema/program.schema.json` (generated, then described by hand)
- Modify: `../towerkit/tests/test_mcp_surface.py` (the reviewed-count gate)
- Test: `../towerkit/tests/test_buffer_layer.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Layer.buffer: bool` (alias `buffer`, default `False`, `OMIT_EMPTY`). Read by Tasks 2, 3, 6, 8.

- [ ] **Step 1: Write the failing test**

Create `../towerkit/tests/test_buffer_layer.py`:

```python
"""A buffer is a deliberate uninsured band, not a hole.

Grant, 2026-08-21: "there are situations where there may be a buffer layer in
a tower that is technically uninsured, though. in the diagram, that could be
represented by a 'buffer' layer and not just a gap tho."

towerkit reported `line-gap` for such a band — a FALSE REFUSAL on a structure
that is really placed.
"""

from __future__ import annotations

import json

from towerkit.model import Layer, program_to_jsonable
from test_validate import make_program


def _buffer(**kw) -> Layer:
    base = dict(id="buf", name="Buffer", applies_to=["gl"],
                attach=5_000_000, limit=5_000_000, buffer=True)
    return Layer(**{**base, **kw})


def test_a_buffer_layer_exists_and_defaults_off() -> None:
    assert _buffer().buffer is True
    assert Layer(id="x", name="X", applies_to=["gl"], attach=0,
                 limit=1_000_000).buffer is False


def test_not_a_buffer_writes_no_key() -> None:
    """OMIT_EMPTY: adding the field changes the shape of no existing file."""
    program = make_program()
    assert "buffer" not in json.dumps(program_to_jsonable(program))
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd ../towerkit && uv run --group dev pytest tests/test_buffer_layer.py -q`
Expected: FAIL — `Layer` has no field `buffer` (pydantic rejects the kwarg).

- [ ] **Step 3: Add the field**

In `../towerkit/src/towerkit/model.py`, immediately after the `auditable` field on `Layer`:

```python
    # A DELIBERATE UNINSURED BAND, not a hole. Some towers are placed with a
    # gap the insured carries: nobody is on this slab, and that is the point.
    # Without it, `_check_line_stack` reports `line-gap` — a false refusal on a
    # structure a broker really bought (Grant, 2026-08-21).
    #
    # It is a SLAB and not an absence: it has attach and limit like any layer,
    # so the stack above it seats on its top exactly as it would on real cover.
    # What it does not have is participants or premium, and `validate` refuses
    # them on it — a buffer with a carrier on it is a layer, and calling it a
    # buffer would hide real cover from every total.
    buffer: Annotated[bool, OMIT_EMPTY] = False
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd ../towerkit && uv run --group dev pytest tests/test_buffer_layer.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Regenerate the schema and describe it**

Run: `cd ../towerkit && uv run --group dev python tools/sync_schema.py`

Then in BOTH `schema/program.schema.json` and `src/towerkit/schema/program.schema.json`, replace the generated stub:

```json
        "buffer": {
          "type": "boolean"
        },
```

with:

```json
        "buffer": {
          "description": "A deliberate uninsured band the insured carries. Has an attachment and a limit like any layer, but no carriers and no premium — and it does not report as a gap.",
          "type": "boolean"
        },
```

Run: `cd ../towerkit && uv run --group dev python tools/sync_schema.py --check`
Expected: `schema is in sync with model.py`

- [ ] **Step 6: Update the reviewed-count gate**

Run: `cd ../towerkit && uv run --group dev pytest tests/test_mcp_surface.py -q`
Expected: FAIL — `assert 15 == 14`.

In `../towerkit/tests/test_mcp_surface.py::test_the_writable_counts_match_the_reviewed_contract`, change `13` to `14` for layer (12 scalars + 2 dotted period entries becomes 13 + 2), and extend the docstring:

```python
    13 as of 2026-08-21, when `buffer` landed — a deliberate uninsured band,
    which unlike the other two additions DOES carry validation rules (no
    participants, no premium, and it suppresses the gap it would otherwise be
    reported as). Reviewed and admitted.
```

- [ ] **Step 7: Run the full towerkit suite**

Run: `cd ../towerkit && uv run --group dev pytest -q --ignore=tests/test_connector.py > /tmp/tk.log 2>&1; echo $?; tail -3 /tmp/tk.log`
Expected: exit 0. (`tests/test_connector.py` has one pre-existing PATH-dependent failure on this machine; it is unrelated and must not be "fixed" here.)

- [ ] **Step 8: Commit**

```bash
cd ../towerkit
git add -A src tests schema
git commit -m "feat: a buffer layer is a deliberate uninsured band"
```

---

### Task 2: the validator learns what a buffer means

**Files:**
- Modify: `../towerkit/src/towerkit/validate.py` (`_check_line_stack`, and a new `_check_buffer`)
- Test: `../towerkit/tests/test_buffer_layer.py` (append)

**Interfaces:**
- Consumes: `Layer.buffer` from Task 1.
- Produces: diagnostic codes `buffer-participants`, `buffer-premium`. `_check_line_stack` no longer emits `line-gap` across a buffer's span.

- [ ] **Step 1: Write the failing tests**

Append to `../towerkit/tests/test_buffer_layer.py`:

```python
from towerkit.model import Line, Participant
from towerkit.validate import validate_program
from test_validate import layer as plain_layer


def _stack(*layers):
    return make_program(lines=[Line(id="gl", name="General Liability")],
                        layers=list(layers), retentions=[])


def _codes(program) -> set[str]:
    return {d.code for d in validate_program(program).items}


def test_a_buffer_suppresses_the_gap_it_would_otherwise_be() -> None:
    """THE POINT. Primary to $5M, nothing to $10M, excess above — reported as
    a GAP until the band could be declared."""
    program = _stack(
        plain_layer("primary", ["gl"], 0, 5_000_000),
        _buffer(),
        plain_layer("xs", ["gl"], 10_000_000, 10_000_000),
    )
    assert "line-gap" not in _codes(program)


def test_the_same_stack_without_the_buffer_still_reports_the_gap() -> None:
    """The rule must not have been deleted, only made declarable."""
    program = _stack(
        plain_layer("primary", ["gl"], 0, 5_000_000),
        plain_layer("xs", ["gl"], 10_000_000, 10_000_000),
    )
    assert "line-gap" in _codes(program)


def test_a_buffer_with_a_carrier_on_it_is_refused() -> None:
    """A buffer with a carrier is a LAYER. Calling it a buffer would hide real
    cover from every total."""
    program = _stack(
        plain_layer("primary", ["gl"], 0, 5_000_000),
        _buffer(participants=[Participant(carrier="Zurich", share_bps=10_000)]),
    )
    assert "buffer-participants" in _codes(program)


def test_a_buffer_with_a_premium_is_refused() -> None:
    program = _stack(
        plain_layer("primary", ["gl"], 0, 5_000_000),
        _buffer(premium=25_000),
    )
    assert "buffer-premium" in _codes(program)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `cd ../towerkit && uv run --group dev pytest tests/test_buffer_layer.py -q`
Expected: FAIL — the gap is still reported, and neither buffer code exists.

- [ ] **Step 3: Teach `_check_line_stack` about buffers**

In `../towerkit/src/towerkit/validate.py::_check_line_stack`, the stack is currently built as:

```python
    stack = [ly for ly in covering if not ly.statutory and ly.limit > 0]
```

Buffers must be IN the stack (they occupy a band and the layer above seats on
them) but must not be reported against. Replace the gap branch:

```python
    for below, above in zip(stack, stack[1:], strict=False):
        above_attach = _effective_attach(program, above, line_id)
        if above_attach > below.top:
            diags.error(
                "line-gap",
                f"{line_id}: GAP {below.name}→{above.name} at "
                f"{format_money(below.top)} vs {format_money(above_attach)}",
                ("line", line_id),
            )
```

with:

```python
    for below, above in zip(stack, stack[1:], strict=False):
        above_attach = _effective_attach(program, above, line_id)
        if above_attach > below.top:
            # A BUFFER IS A DECLARED GAP. The band between these two is
            # uninsured on purpose when either side says so, and reporting it
            # would be a false refusal on a tower a broker really placed
            # (Grant, 2026-08-21). Without a buffer the rule is unchanged.
            if below.buffer or above.buffer:
                continue
            diags.error(
                "line-gap",
                f"{line_id}: GAP {below.name}→{above.name} at "
                f"{format_money(below.top)} vs {format_money(above_attach)}",
                ("line", line_id),
            )
```

- [ ] **Step 4: Add the buffer's own rules**

Add to `../towerkit/src/towerkit/validate.py`, beside `_check_states`:

```python
def _check_buffer(layer: Layer, diags: Diagnostics, ref: tuple[str, Any]) -> None:
    """A buffer is a band NOBODY is on.

    Both rules exist for the same reason: a buffer is excluded from signed
    limits and premium totals, so a carrier or a premium recorded on one is
    real money the book would stop counting. If there is a carrier, it is a
    layer — say so rather than quietly dropping it from the totals.
    """
    if not layer.buffer:
        return
    if layer.participants:
        diags.error(
            "buffer-participants",
            f"{layer.name}: a buffer is uninsured, but "
            f"{len(layer.participants)} carrier(s) are on it — clear the "
            f"buffer flag if this band is placed",
            ref,
        )
    if layer.premium:
        diags.error(
            "buffer-premium",
            f"{layer.name}: a buffer is uninsured, so it cannot carry a "
            f"premium of {format_money(layer.premium)}",
            ref,
        )
```

and call it from `_check_layer_detail`, immediately after `_check_states(layer, diags, ref)`:

```python
    _check_buffer(layer, diags, ref)
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `cd ../towerkit && uv run --group dev pytest tests/test_buffer_layer.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Verify the tests can fail**

Mutate and confirm red, restoring after each:
1. Delete the `if below.buffer or above.buffer: continue` lines → `test_a_buffer_suppresses_the_gap_it_would_otherwise_be` fails.
2. Change `if layer.participants:` to `if False:` → `test_a_buffer_with_a_carrier_on_it_is_refused` fails.
3. Change `if not layer.buffer: return` to `return` → both buffer-rule tests fail.

- [ ] **Step 7: Run the full towerkit suite, then commit**

```bash
cd ../towerkit
uv run --group dev pytest -q --ignore=tests/test_connector.py > /tmp/tk.log 2>&1; echo $?; tail -3 /tmp/tk.log
uv run --group dev mypy src && uv run --group dev ruff check src tests
git add -A src tests
git commit -m "feat: a buffer declares its gap instead of being refused as one"
```

---

### Task 3: bookkit reads the field — parity ledger and projection

**Files:**
- Modify: `src/bookkit/web/parity.py` (`TOWERKIT_MODEL_FIELDS`)
- Modify: `src/bookkit/sync.py` (`layer_details_of`, wherever the per-layer dict is built)
- Test: `tests/test_web_parity.py` (already exists; it goes red and then green)

**Interfaces:**
- Consumes: `Layer.buffer` from Task 1.
- Produces: `layer_details()` dicts gain a `"buffer": bool` key, read by Tasks 5, 6, 8.

- [ ] **Step 1: Run the suite to see the ledger go red**

Run: `uv run --no-sync python -m pytest tests/test_web_parity.py -q`
Expected: FAIL — `towerkit fields with no parity entry: ['Layer.buffer']`.

- [ ] **Step 2: Name it in the ledger**

In `src/bookkit/web/parity.py::TOWERKIT_MODEL_FIELDS`, after the `Layer.auditable` entry:

```python
    "Layer.buffer": (
        "the stack editor's `insert buffer`, beside `insert layer` "
        "(routes/program.py). A deliberate uninsured band: it seats the stack "
        "above it like any slab, carries no carriers and no premium, and "
        "suppresses the `line-gap` it would otherwise be reported as. NOT an "
        "inline cell — a band that became insured by someone clearing a "
        "checkbox would silently change what the client is covered for; it is "
        "converted through its own control, confirm-first."
    ),
```

- [ ] **Step 3: Find where layer dicts are built**

Run: `grep -n "\"statutory\":" src/bookkit/sync.py`
This is `layer_details_of`. Add `"buffer": layer.buffer,` beside `"statutory"`, with:

```python
        # A buffer is drawn and counted differently from cover, so the surface
        # needs it on the row rather than re-opening the file to ask.
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_web_tower_builder.py`:

```python
"""Building a tower in the browser: the stack, not the whole program.

Spec: docs/superpowers/specs/2026-08-21-web-tower-builder-design.md
"""

from __future__ import annotations

import json
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
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `uv run --no-sync python -m pytest tests/test_web_parity.py tests/test_web_tower_builder.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bookkit/web/parity.py src/bookkit/sync.py tests/test_web_tower_builder.py
git commit -m "feat: bookkit reads towerkit's buffer flag"
```

---

### Task 4: `sync.insert_layer` — attachment computed, column recomputed

**Files:**
- Modify: `src/bookkit/sync.py` (new `insert_layer`, beside `add_layer`)
- Test: `tests/test_web_tower_builder.py` (append)

**Interfaces:**
- Consumes: `program_files.write`, `towerkit.edit.add_layer`.
- Produces: `sync.insert_layer(conn, placement_id, *, line_id, anchor_layer_id, position, name, limit_cents, buffer=False) -> Diagnostics`, where `position` is `"above"` or `"below"` and `anchor_layer_id` is `None` to seat at the bottom. Used by Task 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_tower_builder.py`:

```python
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
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: FAIL — `module 'bookkit.sync' has no attribute 'insert_layer'`.

- [ ] **Step 3: Write the implementation**

Add to `src/bookkit/sync.py`, immediately after `add_layer`:

```python
def insert_layer(
    conn: sqlite3.Connection,
    placement_id: str,
    *,
    line_id: str,
    anchor_layer_id: str | None,
    position: str,
    name: str,
    limit_cents: int,
    buffer: bool = False,
) -> Diagnostics:
    """Put a slab INTO a stack, and let the position decide the attachment.

    THERE IS NO ATTACHMENT ARGUMENT, deliberately. A typed attachment is how
    two slabs come to share one — Grant built a quota share as two layers at
    `$5M xs $5M` and the renderer drew them on top of each other with their
    labels overprinting (2026-08-21). Position decides: a slab seats on the
    top of the one beneath it, or at $0 when it is the first.

    ONE MUTATION FOR THE WHOLE COLUMN. Inserting mid-stack pushes everything
    above it up, and those attachments are recomputed HERE, inside the same
    mutation, because `write_through` only accepts a file that validates — a
    two-step insert would have to write a half-shifted tower to get there.
    That is also why towerkit's `restack` is not wanted: web/parity.py records
    it as unreachable through the guarded seam for exactly this reason.

    `anchor_layer_id=None` seats the slab at the bottom of the line.
    """
    if position not in ("above", "below"):
        raise ValueError(f"position is 'above' or 'below', not {position!r}")

    def mutate(program: Program) -> None:
        limit = _require_dollars(limit_cents, "limit")
        stack = program.layers_for_line(line_id)
        layer = edit_add_layer(program, [line_id])
        layer.name = name
        layer.limit = limit
        layer.buffer = buffer
        layer.participants = []
        layer.premium = None

        order = [ly for ly in stack if ly.id != layer.id]
        if anchor_layer_id is None:
            order.insert(0, layer)
        else:
            index = next(
                (i for i, ly in enumerate(order) if ly.id == anchor_layer_id), None
            )
            if index is None:
                raise ValueError(f"no layer {anchor_layer_id!r} on {line_id}")
            order.insert(index + 1 if position == "above" else index, layer)

        # RESEAT THE WHOLE COLUMN, bottom up.
        #
        # A SLAB THAT SPANS SEVERAL LINES GETS `follows_underlying`, NOT A
        # NUMBER. This column's arithmetic is not true of the others: an
        # umbrella over GL and AL sits on a different stack in each, so pinning
        # GL's figure onto it opens a gap in AL and towerkit refuses the whole
        # write. `follows_underlying` is exactly this case — `heal_follows`
        # re-derives the attachment per column on every write, and `validate`
        # checks it per column too. (Found by the implementer, 2026-08-21: the
        # seeded umbrella spans GL and AL, and every insert below it was
        # refused until this branch existed.)
        floor = 0
        for slab in order:
            if len(slab.applies_to) > 1:
                slab.follows_underlying = True
            elif not slab.follows_underlying:
                slab.attach = floor
            # In THIS column every slab seats on the floor — whether its
            # attachment is pinned here or derived by heal_follows — so the
            # next one tops out a limit higher. Contiguous by construction.
            floor += slab.limit

    return _mutate(conn, placement_id, mutate)
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the tests can fail**

Mutate and confirm red, restoring after each:
1. Replace the reseat loop body with `pass` → `test_inserting_above_seats_on_the_slab_below` fails.
2. Change `order.insert(index + 1 if position == "above" else index, layer)` to always `order.append(layer)` → `test_inserting_mid_stack_pushes_everything_above_it_up` fails.
3. Delete `layer.buffer = buffer` → `test_a_buffer_is_inserted_with_no_carriers_and_no_premium` fails.

- [ ] **Step 6: Gate and commit**

```bash
uv run --no-sync python -m mypy src && uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m pytest -q > /tmp/bk.log 2>&1; echo $?; tail -3 /tmp/bk.log
git add src/bookkit/sync.py tests/test_web_tower_builder.py
git commit -m "feat: a slab's attachment comes from its position in the stack"
```

---

### Task 5: the stack editor routes

**Files:**
- Modify: `src/bookkit/web/routes/program.py` (new routes beside the layer routes)
- Test: `tests/test_web_tower_builder.py` (append)

**Interfaces:**
- Consumes: `sync.insert_layer` from Task 4.
- Produces: `POST /accounts/{ref}/program/{placement_id}/lines/{line_id}/layers` (form fields: `name`, `limit_cents`, `anchor` — a layer id or `""` for the bottom — `position` — `above`/`below` — and `kind` — `layer`/`buffer`). Answers with the program panel. Used by Task 6.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_tower_builder.py`:

```python
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


def test_a_refusal_comes_back_as_the_panel_not_a_status_code(app_and_org) -> None:
    """htmx swaps nothing on a 4xx or a 5xx, so a route that refuses with a
    status leaves a control that looks simply dead."""
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
    assert "not a number" in refused.text or "money" in refused.text.lower()


def test_an_anchor_from_another_placement_is_refused(app_and_org) -> None:
    """The anchor arrives in the BODY, and a body id is only checked if
    somebody checks it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id

    refused = client.post(
        _insert_url(org, placement, line_id),
        data={"name": "Sneaky", "limit_cents": "1m",
              "anchor": "not-a-layer-here", "position": "above",
              "kind": "layer"},
    )

    assert refused.status_code == 200
    assert "Sneaky" not in refused.text or "no layer" in refused.text.lower()


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
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: FAIL — 404 on the insert route (it does not exist).

- [ ] **Step 3: Write the route**

Add to `src/bookkit/web/routes/program.py`, after `layer_details_row`:

```python
@router.post(
    "/accounts/{ref}/program/{placement_id}/lines/{line_id}/layers",
    response_class=HTMLResponse,
)
async def stack_insert(
    request: Request, ref: str, placement_id: str, line_id: str
) -> HTMLResponse:
    """Put a slab into a line's stack. Position decides the attachment.

    NO ATTACHMENT FIELD IS ACCEPTED, and that is the feature: a typed
    attachment is how two slabs come to share one. `anchor` names the slab this
    one goes above or below, and `""` means the bottom of the line.

    The anchor arrives in the BODY, so it is checked against THIS placement's
    own layers — an id in a body is only checked if somebody checks it, which
    is the hole `forms.spec.checked_option` exists to close.
    """
    org = _org(request, ref)
    conn = _conn(request)
    placement = _owned(conn, org, "placement", placement_id, placements_repo.get)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    anchor = str(form.get("anchor", "")).strip() or None
    position = str(form.get("position", "above"))
    kind = str(form.get("kind", "layer"))
    raw_limit = str(form.get("limit_cents", ""))

    known = {row["id"] for row in sync.layer_details(conn, placement_id)}
    if anchor is not None and anchor not in known:
        return _programs_panel(
            request, ref, org,
            error=f"no layer {anchor!r} on {placement.ref} — reload the tab",
        )
    if not name:
        return _programs_panel(request, ref, org, error="a slab needs a name")
    try:
        limit_cents = int(parse_value(_LAYER_CELLS["limit_cents"], raw_limit) or 0)
    except ValueError as exc:
        return _programs_panel(request, ref, org, error=str(exc))

    try:
        program_files.write(
            conn, placement,
            tool="program_layer_add",
            summary=(
                f"inserted {name} on {line_id}"
                if kind != "buffer"
                else f"declared a buffer on {line_id}"
            ),
            mutate=lambda: sync.insert_layer(
                conn, placement_id, line_id=line_id, anchor_layer_id=anchor,
                position=position, name=name, limit_cents=limit_cents,
                buffer=kind == "buffer",
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:
        return _programs_panel(request, ref, org, error=str(exc))
    forget_program_reads(request)
    return _programs_panel(request, ref, org)
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the tests can fail**

1. Delete the `if anchor is not None and anchor not in known:` guard → `test_an_anchor_from_another_placement_is_refused` fails.
2. Change the `except Exception` arm to `raise` → `test_a_refusal_comes_back_as_the_panel_not_a_status_code` fails.

- [ ] **Step 6: Gate and commit**

```bash
uv run --no-sync python -m mypy src && uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m pytest -q > /tmp/bk.log 2>&1; echo $?; tail -3 /tmp/bk.log
git add src/bookkit/web/routes/program.py tests/test_web_tower_builder.py
git commit -m "feat: insert a slab into a line's stack from the browser"
```

---

### Task 6: the stack editor's markup

**Files:**
- Create: `src/bookkit/web/templates/account/_stack_editor.html`
- Modify: `src/bookkit/web/templates/account/_layers_panel.html` (include it)
- Modify: `src/bookkit/web/routes/program.py` (`_section_html` context: `stacks`)
- Modify: `src/bookkit/web/static/app.css`
- Test: `tests/test_web_tower_builder.py` (append)

**Interfaces:**
- Consumes: the route from Task 5, `layer_details` from Task 3.
- Produces: markup only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_tower_builder.py`:

```python
def test_the_stack_editor_has_no_attachment_input(app_and_org) -> None:
    """THE DESIGN, asserted. An attachment input is how two slabs come to
    share one; there is not one to fill in."""
    client, org = app_and_org
    page = client.get(f"/accounts/{org.ref}/program").text
    editor = page[page.index("stack-editor") :]

    assert 'name="attach' not in editor
    assert 'name="anchor"' in editor
    assert 'name="position"' in editor


def test_add_carrier_sits_on_the_slab_and_add_layer_on_the_stack(
    app_and_org
) -> None:
    """The whole fix for the reported bug: sharing a slab and adding a layer
    are visibly different acts, in different places."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    page = client.get(f"/accounts/{org.ref}/program").text

    assert "+ carrier" in page
    assert "insert layer" in page or "+ layer" in page
    assert "insert buffer" in page


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
    if not shared:
        pytest.skip("no multi-line layer in this fixture")

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "also on" in page
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: FAIL — `stack-editor` is not on the page.

- [ ] **Step 3: Build the context**

In `src/bookkit/web/routes/program.py::_section_html`, add to the render call:

```python
        stacks=_stacks(request, ref, placement),
```

and add the builder above `_section_html`:

```python
def _stacks(request: Request, ref: str, placement: Any) -> list[dict[str, Any]]:
    """One stack per LINE, bottom-up — the editor's model.

    A layer spanning several lines appears in EACH of their stacks, because it
    does. `also_on` carries the other lines so the row can say so: a layer that
    silently moves two other columns is worse than one that warns.
    """
    conn = _conn(request)
    linked = linked_for(request, conn, placement.id)
    if linked.program is None:
        return []
    program = linked.program
    base = f"/accounts/{ref}/program/{placement.id}"
    out: list[dict[str, Any]] = []
    for line in program.lines:
        slabs = []
        for slab in program.layers_for_line(line.id):
            others = [
                other.label
                for other in program.lines
                if other.id != line.id and other.id in slab.applies_to
            ]
            slabs.append({
                "id": slab.id,
                "name": slab.name,
                "attach": format_cents_compact(slab.attach * 100),
                "limit": format_cents_compact(slab.limit * 100),
                "buffer": slab.buffer,
                "statutory": slab.statutory,
                "also_on": others,
                "carriers": [
                    {"name": p.carrier, "share": f"{p.share_bps / 100:g}%"}
                    for p in slab.participants
                ],
                "signed": f"{slab.signed_bps / 100:g}%",
            })
        out.append({
            "line_id": line.id,
            "label": line.label,
            "name": line.name,
            "slabs": list(reversed(slabs)),  # top of tower first, as drawn
            "insert_action": f"{base}/lines/{line.id}/layers",
        })
    return out
```

- [ ] **Step 4: Write the template**

Create `src/bookkit/web/templates/account/_stack_editor.html`:

```html
{# The stack, per line, top of tower first — the order the drawing shows.

   POSITION IS THE STRUCTURE. There is no attachment field anywhere in here,
   and that absence is the feature: a typed attachment is how two slabs come to
   share one, which is what drew two D&O excess layers on top of each other
   with their labels overprinting (Grant, 2026-08-21). A slab seats on the one
   beneath it.

   `+ carrier` sits ON the slab; `insert layer` sits on the stack. Sharing a
   slab and adding a layer are different acts and look it — nothing here
   invites a second layer as the way to add a second carrier. #}
<div class="stack-editor" id="stack-{{ placement.id }}">
  {% for stack in stacks %}
    <section class="stack" aria-label="{{ stack.name }} stack">
      <h3 class="stack-line">{{ stack.name }} <span class="detail-label">{{ stack.label }}</span></h3>

      {% for slab in stack.slabs %}
        <div class="slab{% if slab.buffer %} is-buffer{% endif %}">
          <div class="slab-head">
            <span class="slab-name">{{ slab.name }}</span>
            <span class="slab-band mono">{{ slab.limit }} xs {{ slab.attach }}</span>
            {% if slab.buffer %}
              <span class="detail-label" title="a deliberate uninsured band">buffer</span>
            {% elif slab.statutory %}
              <span class="detail-label">statutory</span>
            {% else %}
              <span class="detail-label">{{ slab.signed }} signed</span>
            {% endif %}
            {% if slab.also_on %}
              {# It appears in those stacks too, and an edit here moves them. #}
              <span class="detail-label"
                    title="this slab spans more than one line — editing it here changes those columns too"
                    >also on {{ slab.also_on | join(", ") }}</span>
            {% endif %}
          </div>

          {% if not slab.buffer %}
            <ul class="slab-carriers">
              {% for carrier in slab.carriers %}
                <li>{{ carrier.name }} <span class="mono">{{ carrier.share }}</span></li>
              {% endfor %}
              <li>
                <button type="button" class="row-action-btn"
                        hx-get="{{ base }}/layers/{{ slab.id }}/markets/new"
                        hx-target="#program-{{ placement.id }} .form-host"
                        hx-swap="innerHTML">+ carrier</button>
              </li>
            </ul>
          {% endif %}
        </div>
      {% endfor %}

      {# The stack's own controls. `anchor` names which slab to sit against and
         is a SELECT of this line's slabs — the picker's options are the
         authority, re-checked server-side, and there is no free text to type
         an id into. #}
      <form class="stack-insert" hx-post="{{ stack.insert_action }}"
            hx-target="#programs-panel" hx-swap="outerHTML">
        <label class="field">
          <span class="field-label">name</span>
          <input type="text" name="name" required placeholder="2nd Excess">
        </label>
        <label class="field">
          <span class="field-label">limit</span>
          <input type="text" name="limit_cents" required placeholder="10m">
        </label>
        <label class="field">
          <span class="field-label">goes</span>
          <select name="position">
            <option value=""></option>
            <option value="above" selected>above</option>
            <option value="below">below</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">this slab</span>
          <select name="anchor">
            <option value="">the ground ($0)</option>
            {% for slab in stack.slabs %}
              <option value="{{ slab.id }}">{{ slab.name }}</option>
            {% endfor %}
          </select>
        </label>
        <button type="submit" class="btn btn-primary" name="kind" value="layer">insert layer</button>
        <button type="submit" class="btn" name="kind" value="buffer"
                title="a deliberate uninsured band">insert buffer</button>
      </form>
    </section>
  {% endfor %}
</div>
```

- [ ] **Step 5: Include it and style it**

In `src/bookkit/web/templates/account/_layers_panel.html`, immediately after the `_program_diagnostics.html` include:

```html
    {% if linked %}
      {# The stack editor is the MODEL; the drawing below is a view of it. #}
      {% include "account/_stack_editor.html" %}
    {% endif %}
```

Add to `src/bookkit/web/static/app.css`, beside `.program-diagnostics`:

```css
/* The stack editor. Top of tower first, matching the drawing, so the two
   read in the same direction — a list that ran the other way would make the
   reader flip every comparison. */
.stack-editor { display: flex; flex-wrap: wrap; gap: 1.25rem; margin: 0 0 1rem; }
.stack { min-width: 18rem; flex: 1 1 18rem; }
.stack-line { font-size: var(--hint-size); margin: 0 0 0.35rem; }

.slab {
  border: 1px solid var(--hairline);
  border-radius: 3px;
  padding: 0.35rem 0.5rem;
  margin-bottom: 0.25rem;
  background: var(--paper);
}

/* Hatched, and the same grey the drawing gives unplaced capacity: a band
   nobody is on should not read as cover in either place. */
.slab.is-buffer {
  background: repeating-linear-gradient(
    45deg, var(--wash), var(--wash) 5px, var(--stone) 5px, var(--stone) 10px
  );
  border-style: dashed;
}

.slab-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
.slab-name { font-weight: 700; font-size: 0.9rem; }
.slab-band { font-size: var(--hint-size); color: var(--muted); }

.slab-carriers {
  list-style: none;
  margin: 0.2rem 0 0;
  padding-left: 0.75rem;
  font-size: var(--hint-size);
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.stack-insert { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.5rem; margin-top: 0.4rem; }
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py tests/test_web_input_integrity.py -q`
Expected: PASS. (`test_web_input_integrity.py` is included because it asserts every in-row control has a REAL `<label>` and an `aria-label` — the new form must satisfy it. If it fails, wrap the control in a `<label>` rather than adding an `aria-label` alone.)

- [ ] **Step 7: Verify the tests can fail**

1. Add `<input name="attach_cents">` to the form → `test_the_stack_editor_has_no_attachment_input` fails.
2. Delete the `+ carrier` button → `test_add_carrier_sits_on_the_slab_and_add_layer_on_the_stack` fails.

- [ ] **Step 8: Gate and commit**

```bash
uv run --no-sync python -m mypy src && uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m pytest -q > /tmp/bk.log 2>&1; echo $?; tail -3 /tmp/bk.log
git add src/bookkit/web/ tests/test_web_tower_builder.py
git commit -m "feat: the stack editor — position is the structure"
```

---

### Task 7: removing a slab leaves a gap, and asks

**Files:**
- Modify: `src/bookkit/web/routes/program.py` (the existing layer-remove confirm)
- Modify: `src/bookkit/web/templates/account/_layer_remove_confirm.html`
- Test: `tests/test_web_tower_builder.py` (append)

**Interfaces:**
- Consumes: Task 4's `insert_layer` (for the convert-to-buffer path).
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_tower_builder.py`:

```python
def test_removing_a_mid_stack_slab_leaves_the_gap(app_and_org) -> None:
    """Closing the tower up silently would MOVE cover the client bought. The
    gap is true, and the diagnostics strip says so."""
    from towerkit.validate import validate_program

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    line_id = program.lines[0].id
    stack = program.layers_for_line(line_id)
    if len(stack) < 3:
        pytest.skip("need three slabs to remove from the middle")
    middle = stack[1]
    above_attach = stack[2].attach

    client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers/{middle.id}/remove"
    )

    fresh = sync.linked_program(conn, placement.id).program
    remaining = fresh.layers_for_line(line_id)
    assert all(ly.id != middle.id for ly in remaining)
    assert any(ly.attach == above_attach for ly in remaining), (
        "the tower closed up and moved cover the client bought"
    )


def test_the_remove_confirm_says_a_gap_will_be_left(app_and_org) -> None:
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program
    stack = program.layers_for_line(program.lines[0].id)
    if len(stack) < 3:
        pytest.skip("need three slabs")

    page = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/layers/{stack[1].id}/remove"
    ).text

    assert "gap" in page.lower()
    assert "buffer" in page.lower(), "the confirm does not offer the other answer"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: the confirm test fails (no gap/buffer wording). The removal test may already pass — `edit.remove_layer` does not reseat — which is the behaviour being pinned.

- [ ] **Step 3: Say it in the confirm**

In `src/bookkit/web/templates/account/_layer_remove_confirm.html`, add to the notes list:

```html
    {# The tower does NOT close up. Moving the layers above down would change
       what the client is covered for without anybody choosing that; leaving
       the band open is true, and the diagnostics strip says GAP until it is
       resolved — by placing something there, or by declaring it a buffer. #}
    <li>The layers above stay where they are, so this leaves an open band.
        The tower will report a <strong>GAP</strong> until you place something
        there or declare it a <strong>buffer</strong>.</li>
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the test can fail**

Delete the `<li>` just added → `test_the_remove_confirm_says_a_gap_will_be_left` fails.

- [ ] **Step 6: Gate and commit**

```bash
uv run --no-sync python -m mypy src && uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m pytest -q > /tmp/bk.log 2>&1; echo $?; tail -3 /tmp/bk.log
git add src/bookkit/web/ tests/test_web_tower_builder.py
git commit -m "feat: removing a slab leaves the gap, and says so"
```

---

### Task 8: the drawing shows buffers, and the two surfaces agree

**Files:**
- Modify: `../towerkit/src/towerkit/render/web.py` (buffer styling in the geometry payload)
- Modify: `src/bookkit/web/tower.py` (pass the flag through)
- Modify: `src/bookkit/web/templates/account/_tower_panel.html`
- Modify: `src/bookkit/web/static/app.css`
- Test: `tests/test_web_tower_builder.py` (append), `tests/test_web_tower.py`

**Interfaces:**
- Consumes: `Layer.buffer` from Task 1.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_tower_builder.py`:

```python
def test_the_drawing_and_the_editor_never_disagree(app_and_org) -> None:
    """Both read the same file. A drawing that showed a different stack from
    the list beside it would make the picture untrustworthy, which is the one
    thing it is for."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)
    program = sync.linked_program(conn, placement.id).program

    page = client.get(f"/accounts/{org.ref}/program").text

    for layer in program.layers:
        assert layer.name in page, f"{layer.name} is in the file and not on the page"


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
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: `test_a_buffer_draws_as_a_buffer` fails — the drawing has no buffer class.

- [ ] **Step 3: Carry the flag through the geometry**

In `src/bookkit/web/tower.py`, wherever each layer's dict is built for the template, add:

```python
            # The drawing must not present an uninsured band as cover. Same
            # word the stack editor uses, so one CSS rule serves both.
            "buffer": bool(getattr(layer, "buffer", False)),
```

In `src/bookkit/web/templates/account/_tower_panel.html`, on the layer outline div, extend the class list:

```html
        <div class="tower-layer{% if layer.pending %} is-pending{% endif %}{% if layer.buffer %} is-buffer{% endif %}"
```

Add to `src/bookkit/web/static/app.css`:

```css
/* A buffer in the drawing: hatched, never filled with a carrier colour. The
   same treatment the stack editor gives it, because a band nobody is on must
   not read as cover in either place. */
.tower-layer.is-buffer {
  background: repeating-linear-gradient(
    45deg, transparent, transparent 5px, var(--unplaced) 5px, var(--unplaced) 10px
  );
  border-style: dashed;
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py tests/test_web_tower.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the test can fail**

Delete the `{% if layer.buffer %} is-buffer{% endif %}` from the template → `test_a_buffer_draws_as_a_buffer` fails.

- [ ] **Step 6: Gate and commit**

```bash
uv run --no-sync python -m mypy src && uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m pytest -q > /tmp/bk.log 2>&1; echo $?; tail -3 /tmp/bk.log
git add src/bookkit/web/ tests/
git commit -m "feat: a buffer draws as a band nobody is on"
```

---

### Task 9: keyboard-only build, and the docs that outlive it

**Files:**
- Modify: `CLAUDE.md` (the Program tab's rules)
- Modify: `changelog.md`
- Test: `tests/test_web_tower_builder.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_tower_builder.py`:

```python
def test_a_whole_tower_is_buildable_without_a_pointer(app_and_org) -> None:
    """If this fails, drag stops being polish and becomes a requirement.

    Every control the builder needs is a form control or a button — no
    drag handles, no click-only affordances — so a keyboard reaches all of it.
    """
    import re

    client, org = app_and_org
    page = client.get(f"/accounts/{org.ref}/program").text
    editor = page[page.index("stack-editor") :]
    editor = editor[: editor.index("</div>")] if "</div>" in editor else editor

    assert "draggable" not in editor
    assert "onmousedown" not in editor
    # every interactive element is a real control, not a div with a handler
    for handler in re.findall(r'<div[^>]*hx-(?:post|get)=', editor):
        raise AssertionError(f"a div is doing a control's job: {handler}")
```

- [ ] **Step 2: Run it**

Run: `uv run --no-sync python -m pytest tests/test_web_tower_builder.py -q`
Expected: PASS (this pins the property rather than driving new code).

- [ ] **Step 3: Record the rules that outlive the plan**

In `CLAUDE.md`, in the UI conventions section, add:

```markdown
- A SLAB'S ATTACHMENT COMES FROM ITS POSITION, never from a field (Grant,
  2026-08-21). The stack editor inserts above/below and recomputes the whole
  column in ONE mutation, so `write_through` never sees a half-shifted tower.
  A typed attachment is how two slabs come to share one: a quota share built as
  two layers at `$5M xs $5M` drew on top of itself with the labels
  overprinting, and towerkit had been reporting `line-overlap` the whole time
  while the web said nothing. CARRIERS ARE ADDED ON THE SLAB (`+ carrier`),
  layers on the stack — sharing and stacking must never look like the same act.
- A BUFFER IS A SLAB, NOT A GAP. A deliberate uninsured band has an attachment
  and a limit, carries no carriers and no premium, and suppresses the
  `line-gap` it would otherwise be refused as. Removing a mid-stack slab LEAVES
  the gap and says so — closing the tower up would move cover the client
  bought.
```

- [ ] **Step 4: Changelog**

Add to `changelog.md` under today's date, in `### Added`:

```markdown
- **Build a tower in the browser.** Layers are inserted above or below what is
  already there and the attachment is worked out from the position — there is
  no attachment to type, which is what made two carriers sharing one slab turn
  into two layers drawn on top of each other. Carriers are added on the slab
  they share. A deliberate uninsured band is a **buffer**: a real slab that
  carries nobody, draws hatched, and stops being reported as a gap.
```

- [ ] **Step 5: Full gate and commit**

```bash
uv run --no-sync python -m mypy src && uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m pytest -q > /tmp/bk.log 2>&1; echo $?; tail -3 /tmp/bk.log
git add -A
git commit -m "docs: position is the structure, and a buffer is a slab"
```

---

## Self-review

**Spec coverage.** Section 1 (stack editor) → Tasks 4, 5, 6. Section 2 (write
path) → Tasks 4, 5, and the gap-on-removal half → Task 7. Section 3 (buffers) →
Tasks 1, 2, 3, and drawing in Task 8. Section 4 (drawing) → Task 8; the
select-a-row-highlights-a-slab half is NOT covered and is deliberately deferred
— it needs the drag work's hit-testing and belongs with approach B. Section 5
(refusals/testing) → the mutation steps in every task, plus Task 9's
keyboard check.

**Placeholders.** None: every code step carries the actual code, and every test
step the actual test.

**Type consistency.** `sync.insert_layer(conn, placement_id, *, line_id,
anchor_layer_id, position, name, limit_cents, buffer=False) -> Diagnostics` is
defined in Task 4 and called with those exact names in Tasks 5 and 8.
`Layer.buffer` is defined in Task 1 and read in 2, 3, 6, 8. The route's form
field names (`name`, `limit_cents`, `anchor`, `position`, `kind`) are defined in
Task 5 and are the ones the Task 6 template posts.

**Known gap, stated rather than hidden:** highlighting a slab when its row is
selected is in the spec and not in this plan. It is one line of the spec and a
whole hit-testing mechanism; it goes with approach B.
