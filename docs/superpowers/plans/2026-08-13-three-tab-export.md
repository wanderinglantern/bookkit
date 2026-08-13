# Three-Tab Open-Items Workbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow the open-items export into the three-sheet client deliverable the spec fixes (spec section "Additional worksheets (added 2026-08-13)"): sheets in order **Open Items · Projects · Schedule of Insurance**. Sheet 1 keeps its exact current content; sheet 2 is the full projects report (every need on every live project, no days-open); sheet 3 concatenates towerkit `build_soi` sections per linked placement plus minimal book-data sections for unlinked ones.

**Architecture:** towerkit's now-merged multi-sheet API does all rendering: `new_workbook()` (added in Task 1) → `render_table_sheet` / `render_soi_sheet` per sheet → `finalize_workbook` once. bookkit's `services/export_open_items.py` grows two new PURE composers (`compose_projects`, `compose_soi` — no rendering imports, no wall clock) and `write()` is restructured onto the multi-sheet path. `compose()` and sheet 1 output are untouched. Program files are loaded with `towerkit.model.load_program` — the exact loader `sync.py` already uses for `placement.program_path`.

**Why Task 1 touches towerkit at all (the one non-bookkit commit):** bookkit's `tests/test_conventions.py::test_no_openpyxl_outside_imports_package` asserts the literal string `"openpyxl"` appears nowhere in any src file outside `imports/` — so `from openpyxl import Workbook` is out, even in a comment. The namespace re-import escape (`from towerkit.render.table_xlsx import Workbook`) was probed against bookkit's toolchain and FAILS: towerkit ships `py.typed` and bookkit's mypy is `strict = true` (which enables `no_implicit_reexport`), producing `error: Module "towerkit.render.table_xlsx" does not explicitly export attribute "Workbook" [attr-defined]`. A `# type: ignore[attr-defined]` would work today but becomes an error under `warn_unused_ignores` the day towerkit adds `__all__` — rejected. The clean route is a five-line towerkit factory `new_workbook()` (a function *defined* in `table_xlsx.py` is explicitly exported); it also closes a real gap in the multi-sheet contract, whose own docstring tells consumers to "build a `Workbook()`" that a convention-bound consumer cannot name.

**Tech Stack:** Python 3.11+, openpyxl (towerkit only), SQLite, pytest. No CLI/TUI changes — `write()`'s signature is unchanged, so `bookctl export open-items` and the Navigator `x` action pick up the new sheets for free.

## Global Constraints

- bookkit work lands on branch `feat/three-tab-export`, cut from current bookkit main (`6f04302`). Task 1 is the single towerkit commit, on towerkit main — everything after it is bookkit-only.
- Gates before EVERY commit, in the repo being committed: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. Never pipe test output before the `&&` gate — redirect to a file, gate on the command, tail the file after.
- No new bookkit dependency. openpyxl stays towerkit's; the word "openpyxl" must not appear in any bookkit src file outside `imports/` (convention-tested), and test files import it lazily inside test bodies as the existing tests do.
- Determinism: `today` stays a parameter of `write()`; the two NEW sheets do no date math at all (dates come from the book / the program file), so `write()`'s signature and the two-runs-byte-identical property are unchanged. `finalize_workbook` is called exactly ONCE per workbook.
- Tests never hardcode "today" for time math. New tests use fixed date literals only for data (needed-by, periods) and fixed `date(...)` values for the `today` parameter — no assertion depends on the wall clock.
- Money is integer CENTS in bookkit; display via `bookkit.money.format_cents`; cents→whole-dollars crosses the boundary only via `bookkit.money.cents_to_dollars` (SOI premiums — towerkit speaks whole dollars).
- Sheet 1 content is IDENTICAL to current main: same title expression, same columns, same rows, same row-height rule, same empty-book row. Existing export tests keep passing; `load_workbook(path).active` remains the Open Items sheet (index 0), so no existing assertion needs to change.
- No TUI work in this plan — the textual-modal-forms skill read is not required.

**Resolved ambiguities (spec → concrete rules):**
- "Non-completed" / "live" project = `status not in ("completed", "cancelled")`. The spec says "non-completed" but also "live projects"; a cancelled project is not live and has no business in a client deliverable.
- A live project with zero needs still gets its section (label row, no body rows) — sheet 2 is the *full* projects report.
- `build_soi` labels are group names (or `None`); the "under program-name labels" nesting is flattened by prefixing: `label = program_name` when the section label is `None`, else `f"{program_name} — {label}"`. This avoids empty header sections and spurious `$0.00` total rows a label-only section would render.
- A linked placement whose file is unreadable/moved, or whose program has no layers (`build_soi` → `[]`), falls back to the minimal book-data section — the policy list is never silently partial (same never-raise stance as `sync.line_labels`).
- The unlinked section carries the placement status (the spec's "program name, period, status, premium") in its LABEL — `f"{program_name} ({Status})"` — because the SOI row has no status column and the caller-fixed row fields leave limits/retention empty.
- Sub-dollar premium cents cannot render in the SOI's whole-dollar column: delegate to `money.cents_to_dollars` first, and on its refusal floor to dollars — the documented display-floor rule `format_cents_compact` already uses. Display only; nothing is written back anywhere.
- Sheet titles: sheet 1 keeps `f"Open Items — {org.name}"[:31]` (sanitized) exactly as today; sheets 2 and 3 are the spec's plain names "Projects" and "Schedule of Insurance".

**Data-safety note:** this plan is read-only against the database — no migrations, no writes, no schema change. Nothing to back up.

---

### Task 1: towerkit — `new_workbook()` factory (the multi-sheet contract's missing first step)

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/table_xlsx.py`
- Modify: `/Users/grantgreeson/Developer/towerkit/tests/test_table_xlsx.py` (append)

**Interfaces:**
- Produces (consumed by bookkit Task 4): `new_workbook() -> Workbook`.

- [ ] **Step 1: Failing test**

Append to `tests/test_table_xlsx.py` (it already has the `theme` fixture, `COLS`, `SECTIONS`, and imports `load_workbook`):

```python
def test_new_workbook_starts_multi_sheet_composition(theme, tmp_path: Path):
    # bookkit cannot name openpyxl (convention-tested) nor re-import Workbook
    # from this namespace (its strict mypy: no_implicit_reexport) — this
    # factory is how convention-bound consumers start the multi-sheet flow.
    from towerkit.render.table_xlsx import (
        finalize_workbook,
        new_workbook,
        render_table_sheet,
        sanitize_sheet_title,
    )

    wb = new_workbook()
    ws = wb.active
    ws.title = sanitize_sheet_title("One")
    render_table_sheet(ws, COLS, SECTIONS, theme=theme)
    ws2 = wb.create_sheet(sanitize_sheet_title("Two"))
    render_table_sheet(ws2, COLS, SECTIONS, theme=theme)
    path = finalize_workbook(wb, tmp_path / "multi.xlsx")
    assert load_workbook(path).sheetnames == ["One", "Two"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/grantgreeson/Developer/towerkit && uv run pytest tests/test_table_xlsx.py -k new_workbook -v 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'new_workbook'`.

- [ ] **Step 3: Implement**

Append to `src/towerkit/render/table_xlsx.py`, after `sanitize_sheet_title`:

```python
def new_workbook() -> Workbook:
    """Start a multi-sheet composition (see the module docstring's contract).

    Exists so consumers that must not name openpyxl anywhere in their source
    (bookkit convention-tests exactly that, and its strict mypy rejects
    re-importing Workbook from this namespace as an implicit re-export) can
    still obtain the Workbook. Everyone else may construct Workbook directly."""
    return Workbook()
```

Also extend the module docstring's contract sentence: change "build a `Workbook()`" to "build a workbook (`new_workbook()`, or `Workbook()` directly)".

- [ ] **Step 4: Gates and commit (towerkit)**

```bash
cd /Users/grantgreeson/Developer/towerkit && uv run pytest -q > /tmp/tk-test.log && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/tk-test.log
git add src/towerkit/render/table_xlsx.py tests/test_table_xlsx.py
git commit -m "render: new_workbook() factory — multi-sheet entry for openpyxl-free consumers"
```

---

### Task 2: bookkit — pure Projects-sheet composition

**Files:**
- Modify: `/Users/grantgreeson/Developer/bookkit/src/bookkit/services/export_open_items.py`
- Test: `/Users/grantgreeson/Developer/bookkit/tests/test_services.py` (append)

**Interfaces:**
- Produces (consumed by Task 4):
  - `SheetSection(label: str | None, rows: tuple[tuple[str, ...], ...])`
  - `compose_projects(conn, org_id: str) -> list[SheetSection]` — one section per live project, ALL its needs; `[]` when no live projects (sheet omitted).
  - `_LIVE_EXCLUDED = ("completed", "cancelled")`, `_project_label(project) -> str`.

- [ ] **Step 1: Cut the branch**

```bash
cd /Users/grantgreeson/Developer/bookkit && git checkout -b feat/three-tab-export main
```

- [ ] **Step 2: Failing tests**

Append to `tests/test_services.py` (module already imports `orgs`, `placements`, `projects_repo`, `date`):

```python
def test_compose_projects_full_report_live_projects_only(conn):
    from bookkit.services.export_open_items import compose_projects

    org = orgs.create(conn, name="Proj Co", kind="client", status="active", owner="grant")
    live = projects_repo.create_project(
        conn, org.id, "Warehouse Expansion", status="active",
        start_on="2026-06-01", end_on="2027-06-01",
    )
    projects_repo.add_need(
        conn, live.id, "Builder's Risk", "2026-09-01",
        limit_cents=25_000_000_00, notes="GC requires evidence",
    )
    projects_repo.add_need(conn, live.id, "GL", "2026-09-15", status="placed")
    done = projects_repo.create_project(conn, org.id, "Old HQ Fit-out", status="completed")
    projects_repo.add_need(conn, done.id, "Property", "2025-01-01")
    projects_repo.create_project(conn, org.id, "Shelved", status="cancelled")

    sections = compose_projects(conn, org.id)
    assert len(sections) == 1  # completed and cancelled projects are not live
    section = sections[0]
    assert section.label == "Warehouse Expansion — Active (2026-06-01 → 2027-06-01)"
    # every need regardless of status; line, notes, needed-by, prettified
    # status, formatted limit — and NO days-open (five columns, no date math)
    assert section.rows == (
        ("Builder's Risk", "GC requires evidence", "2026-09-01", "Identified", "$25,000,000"),
        ("GL", "", "2026-09-15", "Placed", ""),
    )


def test_compose_projects_needless_live_project_still_sections(conn):
    from bookkit.services.export_open_items import compose_projects

    org = orgs.create(conn, name="Plan Co", kind="client")
    projects_repo.create_project(conn, org.id, "Planning Stage")  # status "planned"
    sections = compose_projects(conn, org.id)
    assert sections[0].label == "Planning Stage — Planned"
    assert sections[0].rows == ()


def test_compose_projects_empty_when_no_live_projects(conn):
    from bookkit.services.export_open_items import compose_projects

    org = orgs.create(conn, name="No Proj Co", kind="client")
    assert compose_projects(conn, org.id) == []
    projects_repo.create_project(conn, org.id, "Done", status="completed")
    assert compose_projects(conn, org.id) == []
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_services.py -k compose_projects -v 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'compose_projects'`.

- [ ] **Step 4: Implement**

In `services/export_open_items.py`, add `Project` to the models import (`from ..models import Project, Task`), then append after `compose`:

```python
# --- sheet 2: Projects — the full projects report, not the unmet slice ---------

_LIVE_EXCLUDED = ("completed", "cancelled")  # spec's "non-completed" = live only


@dataclass(frozen=True)
class SheetSection:
    """A styled-table section as plain data — label plus ready-to-render
    string rows. Pure counterpart of towerkit's TableSection (which lives in
    render/ and must not be imported at module level)."""

    label: str | None
    rows: tuple[tuple[str, ...], ...]


def _project_label(project: Project) -> str:
    label = f"{project.name} — {_status_label(project.status)}"
    if project.start_on and project.end_on:
        label += f" ({project.start_on} → {project.end_on})"
    elif project.start_on:
        label += f" (starts {project.start_on})"
    elif project.end_on:
        label += f" (ends {project.end_on})"
    return label


def compose_projects(conn: sqlite3.Connection, org_id: str) -> list[SheetSection]:
    """One section per live project, EVERY need regardless of status — the
    client's projects data in full (sheet 1 keeps only the unmet slice).
    Empty list ⇒ the Projects sheet is omitted, not rendered blank."""
    sections: list[SheetSection] = []
    for project in projects_repo.projects_for_org(conn, org_id):
        if project.status in _LIVE_EXCLUDED:
            continue
        rows = tuple(
            (
                n.line,
                n.notes or "",
                n.needed_by,
                _status_label(n.status),
                format_cents(n.limit_cents) if n.limit_cents else "",
            )
            for n in projects_repo.needs_for_project(conn, project.id)
        )
        sections.append(SheetSection(_project_label(project), rows))
    return sections
```

- [ ] **Step 5: Run** — `uv run pytest tests/test_services.py -q > /tmp/bk-test.log && tail -3 /tmp/bk-test.log` → PASS.

- [ ] **Step 6: Gates and commit**

```bash
uv run pytest -q > /tmp/bk-test.log && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/bk-test.log
git add src/bookkit/services/export_open_items.py tests/test_services.py
git commit -m "export: pure Projects-sheet composition — every need on every live project"
```

---

### Task 3: bookkit — pure SOI-sheet composition

**Files:**
- Modify: `/Users/grantgreeson/Developer/bookkit/src/bookkit/services/export_open_items.py`
- Test: `/Users/grantgreeson/Developer/bookkit/tests/test_services.py` (append)

**Interfaces:**
- Consumes: `towerkit.soi.build_soi` / `SoiSection` / `SoiRow` (pure module — no openpyxl), `towerkit.model.load_program` (the exact loader `sync.py` uses for `placement.program_path`), `money.cents_to_dollars`.
- Produces (consumed by Task 4): `compose_soi(conn, org_id: str) -> list[SoiSection]` — non-empty iff the org has any placement; `_premium_dollars(cents: int | None) -> int | None`.

- [ ] **Step 1: Failing tests**

Append to `tests/test_services.py`:

```python
def test_compose_soi_linked_placement_uses_towerkit_soi(conn, tmp_path):
    from towerkit.model import Layer, Line, Participant, Period, Program, dump_program
    from towerkit.model import Placement as TkPlacement

    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Linked Co", kind="client", status="active")
    p = placements.create(
        conn, org.id, "2026 Package", "2025-10-01", "2026-10-01", status="bound"
    )
    program = Program(
        insured="Linked Co", program="Package Program", placement=TkPlacement.BOUND,
        period=Period(start=date(2025, 10, 1), end=date(2026, 10, 1)),
        lines=[Line(id="gl", name="General Liability", abbr="GL")],
        layers=[
            Layer(
                id="gl1", name="Primary GL", applies_to=["gl"],
                attach=0, limit=1_000_000, premium=52_000,
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            )
        ],
    )
    path = tmp_path / "package.json"
    dump_program(program, path)
    placements.update(conn, p.id, program_path=str(path))

    sections = compose_soi(conn, org.id)
    assert len(sections) == 1
    section = sections[0]
    # build_soi's unlabeled section takes the program name as its label
    assert section.label == "2026 Package"
    row = section.rows[0]
    assert row.insured == "Linked Co"
    assert row.coverage == "General Liability"
    assert row.carrier == "Zurich"
    assert row.effective == date(2025, 10, 1)
    assert row.premium == 52_000  # whole dollars, straight from the file


def test_compose_soi_unlinked_placement_gets_book_data_section(conn):
    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Paper Co", kind="client")
    placements.create(
        conn, org.id, "Legacy Property", "2025-01-01", "2026-01-01",
        status="bound", total_premium=12_345_00,
    )
    sections = compose_soi(conn, org.id)
    assert len(sections) == 1
    assert sections[0].label == "Legacy Property (Bound)"
    row = sections[0].rows[0]
    assert row.insured == "Paper Co"
    assert row.coverage == "Legacy Property"
    assert row.carrier == "See policy documents"
    assert row.policy_number == ""
    assert row.effective == date(2025, 1, 1)
    assert row.expiration == date(2026, 1, 1)
    assert row.limits == "" and row.retention == ""
    assert row.premium == 12_345  # cents → whole dollars via the money boundary


def test_compose_soi_unreadable_file_falls_back_to_book_data(conn, tmp_path):
    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Moved Co", kind="client")
    placements.create(
        conn, org.id, "Moved Program", "2025-01-01", "2026-01-01",
        program_path=str(tmp_path / "gone.json"),  # linked, but the file moved
    )
    sections = compose_soi(conn, org.id)
    assert len(sections) == 1  # the policy list is never silently partial
    assert sections[0].rows[0].carrier == "See policy documents"


def test_compose_soi_empty_when_no_placements(conn):
    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Bare Co", kind="client")
    assert compose_soi(conn, org.id) == []


def test_premium_dollars_delegates_then_floors_for_display():
    from bookkit.services.export_open_items import _premium_dollars

    assert _premium_dollars(None) is None
    assert _premium_dollars(500_000_00) == 500_000
    # sub-dollar cents: money.cents_to_dollars refuses; the SOI's whole-dollar
    # display column floors instead (format_cents_compact's documented rule)
    assert _premium_dollars(500_000_50) == 500_000
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_services.py -k "compose_soi or premium_dollars" -v 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'compose_soi'`.

- [ ] **Step 3: Implement**

In `services/export_open_items.py`: both `towerkit.soi` and `towerkit.model` are pure (no openpyxl, no matplotlib), so they import at module top — only `towerkit.render.*` stays lazy inside `write()`. Add to the imports:

```python
from towerkit.model import load_program
from towerkit.soi import SoiRow, SoiSection, build_soi

from ..models import Placement, Project, Task
from ..money import MoneyParseError, cents_to_dollars, format_cents
```

Append after `compose_projects`:

```python
# --- sheet 3: Schedule of Insurance — towerkit's SOI machinery, per client ------

_UNLINKED_CARRIER = "See policy documents"


def _premium_dollars(cents: int | None) -> int | None:
    """Placement premium cents → the SOI's whole-dollar premium column.
    Delegates to the guarded money boundary first; on its sub-dollar refusal
    floors to dollars — display only, the same deliberate floor
    format_cents_compact documents. Nothing is written back anywhere."""
    if cents is None:
        return None
    try:
        return cents_to_dollars(cents)
    except MoneyParseError:
        return cents // 100


def _book_data_section(org_name: str, placement: Placement) -> SoiSection:
    """Minimal SOI section for a placement with no (readable) towerkit file —
    program name, period, status, premium from book data, so the policy list
    is complete, never silently partial."""
    row = SoiRow(
        insured=org_name,
        coverage=placement.program_name,
        carrier=_UNLINKED_CARRIER,
        policy_number="",
        effective=date.fromisoformat(placement.period_from),
        expiration=date.fromisoformat(placement.period_to),
        limits="",
        retention="",
        premium=_premium_dollars(placement.total_premium),
    )
    return SoiSection(
        label=f"{placement.program_name} ({_status_label(str(placement.status))})",
        rows=(row,),
    )


def compose_soi(conn: sqlite3.Connection, org_id: str) -> list[SoiSection]:
    """build_soi sections for every LINKED placement, each under a
    program-name label (prefixing flattens the per-program nesting); minimal
    book-data sections for UNLINKED, unreadable, or layerless ones. Non-empty
    exactly when the org has any placement — the sheet-inclusion rule."""
    org = orgs.get(conn, org_id)
    out: list[SoiSection] = []
    for placement in placements.for_org(conn, org_id):
        sections: list[SoiSection] = []
        if placement.program_path:
            try:
                program = load_program(Path(placement.program_path))
            except Exception:  # moved/unreadable file — fall back to book data
                program = None
            if program is not None:
                sections = [
                    SoiSection(
                        label=placement.program_name
                        if section.label is None
                        else f"{placement.program_name} — {section.label}",
                        rows=section.rows,
                    )
                    for section in build_soi(program)
                ]
        if not sections:
            sections = [_book_data_section(org.name, placement)]
        out.extend(sections)
    return out
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_services.py -q > /tmp/bk-test.log && tail -3 /tmp/bk-test.log` → PASS.

- [ ] **Step 5: Gates and commit**

```bash
uv run pytest -q > /tmp/bk-test.log && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/bk-test.log
git add src/bookkit/services/export_open_items.py tests/test_services.py
git commit -m "export: pure SOI composition — build_soi per linked placement, book-data fallback"
```

---

### Task 4: bookkit — `write()` onto the multi-sheet API; order, inclusion, determinism

**Files:**
- Modify: `/Users/grantgreeson/Developer/bookkit/src/bookkit/services/export_open_items.py` (restructure `write`, refresh module docstring)
- Test: `/Users/grantgreeson/Developer/bookkit/tests/test_services.py` (append)

**Interfaces:**
- Consumes: `new_workbook` (Task 1), `render_table_sheet` / `render_soi_sheet` / `finalize_workbook` / `sanitize_sheet_title` (towerkit main), `compose_projects` (Task 2), `compose_soi` (Task 3).
- Produces: `write(conn, org_id: str, out_path: Path, today: date) -> Path` — signature unchanged; CLI and Navigator callers untouched.

- [ ] **Step 1: Failing tests**

Append to `tests/test_services.py`:

```python
def test_write_three_tab_order_and_headers(conn, tmp_path):
    from bookkit.services.export_open_items import write

    client = orgs.create(conn, kind="client", name="Acme", status="active", owner="grant")
    tasks.create(conn, "Chase updated loss runs", org_id=client.id)
    placements.create(
        conn, client.id, "Acme Property 25-26", "2025-10-01", "2026-10-01",
        status="bound", total_premium=250_000_00,
    )
    live = projects_repo.create_project(conn, client.id, "Warehouse Expansion")
    projects_repo.add_need(conn, live.id, "Builder's Risk", "2026-09-01")

    path = write(conn, client.id, tmp_path / "w.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook  # test-only import; src never imports it
    wb = load_workbook(path)
    # spec-fixed sheet order; sheet 1 title unchanged from the single-sheet era
    assert wb.sheetnames == ["Open Items — Acme", "Projects", "Schedule of Insurance"]
    assert [c.value for c in wb["Projects"][1]] == [
        "Line", "Notes", "Needed by", "Status", "Limit"]
    assert [c.value for c in wb["Schedule of Insurance"][1]] == [
        "Insured", "Line of Coverage", "Carrier", "Policy Number", "Effective Date",
        "Expiration Date", "Limits", "Deductible / SIR / Retention", "Premium"]
    # show_premiums=True end to end: the book-data premium in whole dollars
    soi_values = [c.value for row in wb["Schedule of Insurance"].iter_rows() for c in row]
    assert 250_000 in soi_values


def test_write_omits_sheets_without_data(conn, tmp_path):
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Solo Co", kind="client")
    tasks.create(conn, "one open task", org_id=org.id)
    path = write(conn, org.id, tmp_path / "s.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook
    assert load_workbook(path).sheetnames == ["Open Items — Solo Co"]


def test_write_three_tab_deterministic(conn, tmp_path):
    from bookkit.services.export_open_items import write

    client = orgs.create(conn, kind="client", name="Det Co", status="active")
    placements.create(conn, client.id, "Det Program", "2025-01-01", "2026-01-01")
    live = projects_repo.create_project(conn, client.id, "Det Project")
    projects_repo.add_need(conn, live.id, "GL", "2026-09-01")
    a = write(conn, client.id, tmp_path / "a.xlsx", date(2026, 8, 13))
    b = write(conn, client.id, tmp_path / "b.xlsx", date(2026, 8, 13))
    assert a.read_bytes() == b.read_bytes()  # three sheets, one finalize, same bytes
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_services.py -k "three_tab or omits_sheets" -v 2>&1 | tail -3`
Expected: FAIL — workbook has one sheet / missing "Projects".

- [ ] **Step 3: Implement**

Replace `write()` (keep `_COLUMNS` as is) — sheet 1 is the current body verbatim, only re-plumbed onto the sheet-level API; `wb.active` is sheet 1, so `load_workbook(...).active` in the existing tests still lands on Open Items:

```python
_PROJECT_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Line", 28.0), ("Notes", 50.0), ("Needed by", 16.0),
    ("Status", 14.0), ("Limit", 16.0),
)


def write(conn: sqlite3.Connection, org_id: str, out_path: Path, today: date) -> Path:
    """The three-tab client deliverable — Open Items · Projects · Schedule of
    Insurance — rendered via towerkit so every sheet carries SOI formatting
    exactly (the money.parse_share pattern: formatting authority in one
    place). Projects appears only when live projects exist; the SOI sheet
    whenever any placement exists; finalize runs ONCE."""
    from towerkit.render.soi_xlsx import render_soi_sheet
    from towerkit.render.table_xlsx import (
        TableColumn,
        TableSection,
        finalize_workbook,
        new_workbook,
        render_table_sheet,
        sanitize_sheet_title,
    )
    from towerkit.theme import load_theme

    org = orgs.get(conn, org_id)
    theme = load_theme(None)
    wb = new_workbook()

    # Sheet 1 — Open Items: content identical to the single-sheet era.
    columns = [TableColumn(h, w) for h, w in _COLUMNS]
    sections = [
        TableSection(
            s.label,
            tuple((r.item, r.description, r.detail, r.kind, r.due, r.status)
                  for r in s.rows),
        )
        for s in compose(conn, org_id, today)
    ] or [TableSection(None, ((f"No open items as of {today.isoformat()}",
                               "", "", "", "", ""),))]
    ws = wb.active
    ws.title = sanitize_sheet_title(f"Open Items — {org.name}"[:31])
    render_table_sheet(
        ws, columns, sections, theme=theme,
        # Detail is the only multi-line column; two-line floor like the SOI
        row_height=lambda values: 18.0 * max(2, str(values[2]).count("\n") + 1),
    )

    # Sheet 2 — Projects: omitted (not blank) when no live projects.
    project_sections = compose_projects(conn, org_id)
    if project_sections:
        ws_projects = wb.create_sheet(sanitize_sheet_title("Projects"))
        project_columns = [TableColumn(h, w) for h, w in _PROJECT_COLUMNS[:-1]]
        project_columns.append(TableColumn("Limit", 16.0, align="right"))
        render_table_sheet(
            ws_projects, project_columns,
            [TableSection(s.label, s.rows) for s in project_sections],
            theme=theme,
            # Notes is the only multi-line column; same two-line floor
            row_height=lambda values: 18.0 * max(2, str(values[1]).count("\n") + 1),
        )

    # Sheet 3 — Schedule of Insurance: whenever any placement exists
    # (compose_soi is non-empty exactly then). The client's own program:
    # show_premiums=True.
    soi_sections = compose_soi(conn, org_id)
    if soi_sections:
        ws_soi = wb.create_sheet(sanitize_sheet_title("Schedule of Insurance"))
        render_soi_sheet(ws_soi, soi_sections, theme=theme, show_premiums=True)

    return finalize_workbook(wb, out_path)
```

Refresh the module docstring's first paragraph to describe the three-tab workbook (sheet order, inclusion rules, "rendering is towerkit's job" unchanged).

- [ ] **Step 4: Run the full bookkit suite**

Run: `uv run pytest -q > /tmp/bk-test.log && tail -3 /tmp/bk-test.log`
Expected: ALL pass — including every pre-existing export test unchanged (`test_write_open_items_deterministic_and_styled` now writes three sheets, but its `.active` assertions target sheet 1, which is byte-for-byte the same content; `test_write_empty_book_says_so` stays single-sheet) and `test_conventions.py` (the word "openpyxl" still absent from services/).

- [ ] **Step 5: Gates and commit**

```bash
uv run pytest -q > /tmp/bk-test.log && uv run mypy src && uv run ruff check src tests
tail -3 /tmp/bk-test.log
git add src/bookkit/services/export_open_items.py tests/test_services.py
git commit -m "export: three-tab workbook — Open Items, Projects, Schedule of Insurance"
```

- [ ] **Step 6: Cross-repo gate + shipping note (do not skip)**

Run towerkit's gates once more from its repo root (`uv run pytest -q > /tmp/tk-test.log && uv run mypy src && uv run ruff check src tests; tail -3 /tmp/tk-test.log`) — Task 1's factory must still be green against main.

Shipping: the work machine consumes towerkit as a wheel, and Task 1's `new_workbook()` is a hard runtime requirement of the new `write()` — a towerkit RELEASE (version bump + wheelhouse/PyPI per the drill in towerkit's CLAUDE.md) must land before `bookctl export open-items` runs there. No bookkit dependency changed, so no bookkit wheelhouse refresh. Grant's real data lives on the production machine: verify locally against seeded sample data and hand him `bookctl export open-items <org>` for the real check.
