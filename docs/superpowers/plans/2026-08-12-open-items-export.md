# Open-Items Export (.xlsx, SOI formatting) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export a client-facing open-items workbook (tasks + unmet needs + outstanding submissions) styled exactly like towerkit's SOI workbooks, plus the Task `description` field both features need.

**Architecture:** Two repos. towerkit extracts a generic styled-table writer from `render/soi_xlsx.py` (byte-identical SOI output guarded by a golden test); bookkit composes sections purely in `services/export_open_items.py` and delegates rendering — bookkit's export path never imports openpyxl. Task gains an additive `description` column surfaced in the TUI task tables.

**Tech Stack:** Python 3.11+, openpyxl (towerkit only), SQLite, argparse CLI, Textual TUI, pytest.

## Global Constraints

- Gates before EVERY commit, in the repo being committed: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. Never pipe test output before the `&&` gate — redirect to a file, gate on the command, tail the file after.
- Money is integer CENTS in bookkit; format only via `bookkit.money.format_cents`. Dates in outputs are ISO strings.
- repo/ owns every SQL query; services/, tui/, cli.py contain zero raw SQL.
- bookkit migrations are additive-only. The one migration here (`ALTER TABLE task ADD COLUMN description TEXT`) is non-destructive; `db.connect` auto-applies it. No backup machinery needed beyond what exists, but say so in the commit message.
- Determinism: workbook writers never read the wall clock; `today` is always a parameter. `date.today()` may appear only in CLI/TUI entry layers.
- towerkit lives at `/Users/grantgreeson/Developer/towerkit` (bookkit depends on it as an editable path dep). towerkit tasks commit in towerkit; bookkit tasks in bookkit.
- No new bookkit runtime dependency. openpyxl stays towerkit's.

---

### Task 1: towerkit — golden-bytes guard for `write_soi`

The refactor in Task 2 must not change SOI output by a single byte. Capture the current output hash BEFORE touching anything.

**Files:**
- Modify: `/Users/grantgreeson/Developer/towerkit/tests/test_soi_xlsx.py` (append)

**Interfaces:**
- Produces: `GOLDEN_SHA` constant + `test_refactor_golden_bytes` that Task 2 must keep green.

- [ ] **Step 1: Write the test with a placeholder hash**

Append to `tests/test_soi_xlsx.py` (it already has `program`, `theme` fixtures and the `_write` helper):

```python
# Refactor guard: extracting the generic table writer (render/table_xlsx.py)
# must not change SOI output. docProps/core.xml embeds provenance() — the
# CURRENT git sha and dirty marker — so RAW file bytes change with every
# commit; the guard therefore hashes every zip entry EXCEPT core.xml.
# Regenerate GOLDEN_SHA only on a deliberate style/content change or an
# openpyxl bump — never to make a refactor pass.
GOLDEN_SHA = "FILL_ME"


def _content_hash(xlsx_path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with zipfile.ZipFile(xlsx_path) as z:
        for name in sorted(z.namelist()):
            if name == "docProps/core.xml":
                continue
            digest.update(name.encode())
            digest.update(z.read(name))
    return digest.hexdigest()


def test_refactor_golden_content(program, theme, tmp_path):
    path = _write(program, theme, tmp_path / "golden.xlsx")
    assert _content_hash(path) == GOLDEN_SHA
```

(`zipfile` and `Path` are already imported at the top of this test file.)

- [ ] **Step 2: Run it to get the real hash**

Run: `cd /Users/grantgreeson/Developer/towerkit && uv run pytest tests/test_soi_xlsx.py::test_refactor_golden_content -v 2>&1 | tail -5`
Expected: FAIL; the assertion message contains the actual sha256. Copy it into `GOLDEN_SHA`. Because core.xml is excluded, this hash is stable across commits and dirty/clean trees — verify by running once more AFTER committing (Step 4) and confirming it still passes.

- [ ] **Step 3: Run again to verify it passes**

Run: `uv run pytest tests/test_soi_xlsx.py -q`
Expected: all pass.

- [ ] **Step 4: Commit (towerkit)**

```bash
cd /Users/grantgreeson/Developer/towerkit && git add tests/test_soi_xlsx.py && git commit -m "test: golden-bytes guard on write_soi ahead of table-writer extraction"
```

---

### Task 2: towerkit — extract `render/table_xlsx.py`, refactor `write_soi` onto it

**Files:**
- Create: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/table_xlsx.py`
- Modify: `/Users/grantgreeson/Developer/towerkit/src/towerkit/render/soi_xlsx.py`
- Test: `/Users/grantgreeson/Developer/towerkit/tests/test_table_xlsx.py`

**Interfaces:**
- Produces (consumed by bookkit Task 6):
  - `TableColumn(header: str, width: float, number_format: str | None = None, align: str = "left", wrap: bool = True)`
  - `TableSection(label: str | None, rows: tuple[tuple[Any, ...], ...], total: Any = None)`
  - `write_table(columns, sections, *, title: str, theme: Theme, out_path: Path, row_height: Callable[[tuple], float] | None = None) -> Path`

- [ ] **Step 1: Write failing tests for the generic writer**

`tests/test_table_xlsx.py`:

```python
"""Generic styled-table writer: SOI styling for arbitrary sectioned tables."""

import hashlib
from pathlib import Path

import pytest
from openpyxl import load_workbook

from towerkit.render.table_xlsx import TableColumn, TableSection, write_table
from towerkit.theme import load_theme

COLS = (
    TableColumn("Item", 30.0),
    TableColumn("Amount", 12.0, number_format='"$"#,##0.00', align="right"),
)
SECTIONS = (
    TableSection("Section A", (("first", 100), ("second", 200)), total=300),
    TableSection(None, (("loose row", 5),)),
)


@pytest.fixture()
def theme():
    return load_theme(None)


def test_headers_sections_and_total(theme, tmp_path: Path):
    path = write_table(COLS, SECTIONS, title="T", theme=theme, out_path=tmp_path / "t.xlsx")
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == ["Item", "Amount"]
    assert ws["A2"].value == "Section A"      # label row
    assert ws["B2"].value == 300              # total in last column
    assert ws["A3"].value == "first"
    assert ws["A6"].value == "loose row"      # unlabeled section has no label row
    assert ws.freeze_panes == "A2"


def test_two_runs_byte_identical(theme, tmp_path: Path):
    a = write_table(COLS, SECTIONS, title="T", theme=theme, out_path=tmp_path / "a.xlsx")
    b = write_table(COLS, SECTIONS, title="T", theme=theme, out_path=tmp_path / "b.xlsx")
    assert a.read_bytes() == b.read_bytes()


def test_row_height_hook(theme, tmp_path: Path):
    path = write_table(
        COLS, SECTIONS, title="T", theme=theme, out_path=tmp_path / "h.xlsx",
        row_height=lambda values: 44.0,
    )
    ws = load_workbook(path).active
    assert ws.row_dimensions[3].height == 44.0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_table_xlsx.py -v 2>&1 | tail -3`
Expected: FAIL — `ModuleNotFoundError: towerkit.render.table_xlsx`.

- [ ] **Step 3: Create `render/table_xlsx.py`**

MOVE `_argb`, `_normalize_zip`, `_PINNED`, `_PINNED_W3CDTF`, `_MODIFIED_RE` from `soi_xlsx.py` VERBATIM (cut, don't copy — soi_xlsx imports them back). Then:

```python
"""Generic styled-table workbook writer — the SOI look for any sectioned table.

Extracted from soi_xlsx.py so other tools (bookkit's open-items export)
delegate formatting here instead of copying it. Same determinism contract:
pinned workbook properties + epoch-rewritten archive → byte-identical runs."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..theme import Theme
from .common import provenance

# ... _PINNED, _argb, _PINNED_W3CDTF, _MODIFIED_RE, _normalize_zip moved here verbatim ...


@dataclass(frozen=True)
class TableColumn:
    header: str
    width: float
    number_format: str | None = None
    align: str = "left"   # body-cell horizontal alignment
    wrap: bool = True     # False → wrap_text omitted entirely (see note below)


@dataclass(frozen=True)
class TableSection:
    label: str | None
    rows: tuple[tuple[Any, ...], ...]
    total: Any = None  # rendered in the last column of the label row


def write_table(
    columns: Sequence[TableColumn],
    sections: Sequence[TableSection],
    *,
    title: str,
    theme: Theme,
    out_path: Path,
    row_height: Callable[[tuple[Any, ...]], float] | None = None,
) -> Path:
    soi = theme.soi
    ncols = len(columns)
    thin = Side(style="thin", color=_argb(soi.border))
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(name=soi.font, size=soi.size, bold=True,
                       color=_argb(soi.effective_header_text))
    body_font = Font(name=soi.font, size=soi.size, color=_argb(soi.body_text))
    header_fill = PatternFill("solid", fgColor=_argb(soi.header_fill))
    band_fill = PatternFill("solid", fgColor=_argb(soi.band_fill))

    wb = Workbook()
    ws = wb.active
    ws.title = title
    for i, col in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = col.width
    for i, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=i, value=col.header)
        cell.font, cell.fill, cell.border = header_font, header_fill, border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 36.0
    ws.freeze_panes = "A2"

    row_ix = 2
    for section in sections:
        if section.label is not None:
            merge_end = ncols - 1 if section.total is not None else ncols
            ws.merge_cells(start_row=row_ix, start_column=1,
                           end_row=row_ix, end_column=merge_end)
            label = ws.cell(row=row_ix, column=1, value=section.label)
            label.font, label.fill = header_font, header_fill
            label.alignment = Alignment(vertical="center")
            if section.total is not None:
                total = ws.cell(row=row_ix, column=ncols, value=section.total)
                total.font, total.fill = header_font, header_fill
                if columns[-1].number_format:
                    total.number_format = columns[-1].number_format
                total.alignment = Alignment(horizontal="right", vertical="center")
            for c in range(1, ncols + 1):
                cell = ws.cell(row=row_ix, column=c)
                cell.fill = header_fill
                cell.border = border
            ws.row_dimensions[row_ix].height = 22.0
            row_ix += 1
        for band, values in enumerate(section.rows):
            for c, (col, value) in enumerate(zip(columns, values), start=1):
                cell = ws.cell(row=row_ix, column=c, value=value)
                cell.font, cell.border = body_font, border
                if band % 2 == 1:
                    cell.fill = band_fill
                if col.number_format:
                    cell.number_format = col.number_format
                # BYTE-IDENTICAL SUBTLETY: the old SOI date branch built
                # Alignment(horizontal=..., vertical="top") with NO wrap_text
                # argument. wrap_text=False serializes differently from
                # wrap_text omitted — so wrap=False must OMIT the argument,
                # not pass False.
                if col.wrap:
                    cell.alignment = Alignment(
                        horizontal=col.align, vertical="top", wrap_text=True
                    )
                else:
                    cell.alignment = Alignment(horizontal=col.align, vertical="top")
            if row_height is not None:
                ws.row_dimensions[row_ix].height = row_height(values)
            row_ix += 1

    props = wb.properties
    props.creator = provenance()
    props.created = _PINNED
    props.modified = _PINNED
    props.lastModifiedBy = None

    buffer = BytesIO()
    wb.save(buffer)
    _normalize_zip(buffer.getvalue(), out_path)
    return out_path
```

- [ ] **Step 4: Refactor `write_soi` onto it**

Replace the body of `write_soi` in `soi_xlsx.py` (keep `_HEADERS`, `_WIDTHS`, `_CURRENCY`, `_DATE_FMT`, `_row_height`; delete the moved helpers and now-unused imports):

```python
from .table_xlsx import TableColumn, TableSection, write_table


def write_soi(
    sections: list[SoiSection],
    *,
    title: str,
    theme: Theme,
    out_path: Path,
    show_premiums: bool = True,
) -> Path:
    ncols = 9 if show_premiums else 8
    columns: list[TableColumn] = []
    for i, (header, width) in enumerate(
        zip(_HEADERS[:ncols], _WIDTHS[:ncols]), start=1
    ):
        if i in (5, 6):  # effective / expiration
            columns.append(TableColumn(header, width, number_format=_DATE_FMT, wrap=False))
        elif i == 9:     # premium
            columns.append(TableColumn(header, width, number_format=_CURRENCY, align="right"))
        else:
            columns.append(TableColumn(header, width))

    table_sections: list[TableSection] = []
    for section in sections:
        rows = []
        for row in section.rows:
            values: list[object] = [
                row.insured, row.coverage, row.carrier, row.policy_number,
                datetime.combine(row.effective, datetime.min.time()),
                datetime.combine(row.expiration, datetime.min.time()),
                row.limits, row.retention,
            ]
            if show_premiums:
                values.append(row.premium)
            rows.append(tuple(values))
        table_sections.append(TableSection(
            section.label, tuple(rows),
            total=section.premium_total
            if (section.label is not None and show_premiums) else None,
        ))

    return write_table(
        columns, table_sections, title=title, theme=theme, out_path=out_path,
        row_height=lambda values: _row_height(str(values[6]), str(values[7])),
    )
```

- [ ] **Step 5: Run the full towerkit suite — the golden test is the verdict**

Run: `uv run pytest -q 2>&1 | tail -3`
Expected: all pass, `test_refactor_golden_content` included. If the golden test fails, DIFF the styles: unzip both workbooks and compare `xl/styles.xml` — the usual culprits are the wrap_text-omitted-vs-False subtlety and alignment argument order. Fix `write_table`, never the hash.

- [ ] **Step 6: Gates and commit (towerkit)**

Run: `uv run mypy src && uv run ruff check src tests`

```bash
git add src/towerkit/render/table_xlsx.py src/towerkit/render/soi_xlsx.py tests/test_table_xlsx.py
git commit -m "render: extract generic styled-table writer; write_soi delegates (byte-identical)"
```

---

### Task 3: bookkit — Task `description` column (additive migration + model + form)

Grant's call: `title` = the task name, `description` = brief one-line text, `detail` = long-form notes (markdown allowed).

**Files:**
- Create: `/Users/grantgreeson/Developer/bookkit/migrations/008_task_description.sql`
- Modify: `src/bookkit/models.py` (Task), `src/bookkit/tui/widgets/entity_forms.py` (task_form)
- Test: `tests/test_repo.py` (append)

**Interfaces:**
- Produces: `Task.description: str | None`; `task_form` carries a `description` Field between `title` and `due_on`.

- [ ] **Step 1: Failing test**

Append to `tests/test_repo.py`:

```python
def test_task_description_round_trips(conn):
    task = tasks_repo.create(
        conn, "chase GL quote",
        description="waiting on Zurich since Monday",
        detail="## Notes\n- called 8/10, no answer\n- try the London desk",
    )
    got = tasks_repo.get(conn, task.id)
    assert got.description == "waiting on Zurich since Monday"
    assert got.detail.startswith("## Notes")
```

(Match the file's existing import style for `tasks_repo`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_repo.py::test_task_description_round_trips -v 2>&1 | tail -3`
Expected: FAIL (no such column / validation error).

- [ ] **Step 3: Migration + model**

`migrations/008_task_description.sql` (additive-only, per repo rule — no backup needed, existing rows read NULL):

```sql
ALTER TABLE task ADD COLUMN description TEXT;
```

In `models.py`, add to `Task` after `title`:

```python
    description: str | None = None  # brief one-liner; `detail` holds the long notes
```

In `entity_forms.py::task_form`, insert after the title Field:

```python
        Field("description", "description", placeholder="one-line summary"),
```

(the `detail` textarea stays last).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_repo.py tests/test_tui_forms.py -q 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/008_task_description.sql src/bookkit/models.py src/bookkit/tui/widgets/entity_forms.py tests/test_repo.py
git commit -m "tasks: additive description column — brief line between title and long detail"
```

---

### Task 4: bookkit — surface description + detail in the task tables

Three task tables: Navigator attention "tasks due" (`_fill_attention_table`), Navigator per-account group (`_fill_group_table`), AccountScreen overview (`#ov-tasks`, account.py:363-373).

**Files:**
- Modify: `src/bookkit/tui/screens/navigator.py` (TASK_INLINE at :70, `_fill_attention_table` tasks branch at :425, `_fill_group_table` tasks branch at :500)
- Modify: `src/bookkit/tui/screens/account.py:363-373`
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Consumes: `Task.description` from Task 3.
- Produces: a `_task_detail_cell(task) -> Text` helper in navigator.py reused by account.py.

- [ ] **Step 1: Failing pilot test**

Append to `tests/test_tui.py`, following its existing seeded-app pattern (look at how it builds `BookkitApp` on a seeded db and asserts table columns):

```python
async def test_task_tables_show_description_and_detail(seeded_db):
    app = BookkitApp(seeded_db)
    tasks_repo.create(
        app.conn, "call broker", description="brief line", detail="**long** notes",
        due_on=date.today().isoformat(),
    )
    async with app.run_test(size=(160, 44)) as pilot:
        # dive into attention → tasks due
        tree = app.screen.query_one("#nav-tree")
        # walk to the "tasks due" attention leaf and select it
        ...
```

If tree-walking proves brittle, test the layer below instead — call `NavigatorScreen._fill_attention_table` directly with a mounted screen (the file's existing tests show which style it uses; follow it). The assertion that matters:

```python
        table = app.screen.query_one("#nav-table", InlineTable)
        headers = [str(c.label) for c in table.columns.values()]
        assert "description" in headers and "detail" in headers
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tui.py -k description -v 2>&1 | tail -3`

- [ ] **Step 3: Implement**

In `navigator.py`, add a module-level helper near `_attention_label`:

```python
def _task_detail_cell(task: Task) -> Text:
    """First line of the long notes, dimmed and clipped — full text lives in
    the e form; this is for review at a glance."""
    if not task.detail:
        return dash()
    first = task.detail.strip().splitlines()[0]
    return Text(first[:57] + "…" if len(first) > 58 else first, style=theme.DIM)
```

(import `Task` under TYPE_CHECKING if not already; `dash`, `Text`, `theme` are already imported.)

Update `TASK_INLINE` (:70) — description is inline-editable with `i`; detail is form-only:

```python
TASK_INLINE = {
    0: Field("due_on", "due", "date"),
    1: Field("title", "task", required=True),
    2: Field("description", "description"),
}
```

Attention tasks branch (:425): columns become `("due", "task", "description", "detail", "account")`; each row adds `task.description or dash()` and `_task_detail_cell(task)` between title and account name.

Group tasks branch (:500): columns become `("due", "task", "description", "detail", "status")`; same two cells between title and status.

`account.py:366`: columns become `("due", right("due in"), "task", "description", "detail")`; row adds `t.description or dash()` and `_task_detail_cell(t)` (import the helper: `from .navigator import _task_detail_cell`).

Update `ROW_HINTS["tasks"]` (navigator.py:50) to mention the new inline column is still `i`-editable (text unchanged is fine — `i` already advertised).

- [ ] **Step 4: Run TUI tests**

Run: `uv run pytest tests/test_tui.py tests/test_tui_forms.py -q 2>&1 | tail -3`
Expected: PASS (existing tests asserting old column counts may need updating — update assertions, not behavior).

- [ ] **Step 5: Commit**

```bash
git add src/bookkit/tui/screens/navigator.py src/bookkit/tui/screens/account.py tests/test_tui.py
git commit -m "tasks: description + detail columns on every task table; description i-editable"
```

---

### Task 5: bookkit — `submissions.outstanding_for_org`

**Files:**
- Modify: `src/bookkit/repo/submissions.py`
- Test: `tests/test_repo.py` (append)

**Interfaces:**
- Produces: `outstanding_for_org(conn, org_id) -> list[sqlite3.Row]` — rows carry all submission columns plus `market_name`, `about` (program or opportunity title), `about_placement_id` ('' when opportunity-only).

- [ ] **Step 1: Failing test**

```python
def test_outstanding_for_org_joins_market_and_subject(conn):
    client = orgs.create(conn, name="Acme", kind="client")
    market = orgs.create(conn, name="Zurich", kind="market")
    p = placements.create(conn, org_id=client.id, program_name="Acme Property 25-26",
                          period_from="2025-10-01", period_to="2026-10-01")
    submissions.create(conn, market.id, "2026-08-01", placement_id=p.id)
    rows = submissions.outstanding_for_org(conn, client.id)
    assert len(rows) == 1
    assert rows[0]["market_name"] == "Zurich"
    assert rows[0]["about"] == "Acme Property 25-26"
```

(Mirror the file's actual `placements.create` signature — check how existing tests in `test_repo.py` build placements and copy that.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_repo.py -k outstanding_for_org -v 2>&1 | tail -3`

- [ ] **Step 3: Implement** (append to `repo/submissions.py`):

```python
def outstanding_for_org(conn: sqlite3.Connection, org_id: str) -> list[sqlite3.Row]:
    """Everything still out at market for ONE client, joined for display:
    market name plus what it's about (program name or opportunity title)."""
    return conn.execute(
        f"""
        SELECT s.*, m.name AS market_name,
               COALESCE(p.program_name, o.title) AS about,
               COALESCE(p.id, '') AS about_placement_id
        FROM submission s
        JOIN org m ON m.id = s.market_org_id
        LEFT JOIN placement p ON p.id = s.placement_id
        LEFT JOIN opportunity o ON o.id = s.opportunity_id
        WHERE s.status = 'out' AND {base.alive('s')}
          AND (p.org_id = ? OR o.org_id = ?)
        ORDER BY s.sent_on
        """,
        (org_id, org_id),
    ).fetchall()
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_repo.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git add … && git commit -m "repo: outstanding submissions scoped to one client, joined for display"`

---

### Task 6: bookkit — pure composition: `services/export_open_items.py`

**Files:**
- Create: `src/bookkit/services/export_open_items.py`
- Test: `tests/test_services.py` (append)

**Interfaces:**
- Consumes: Task 3 (`Task.description`), Task 5 (`outstanding_for_org`).
- Produces: `ExportRow(item, details, kind, due, status, days_open)`, `ExportSection(label, rows)`, `flatten_markdown(text) -> str`, `compose(conn, org_id: str, today: date) -> list[ExportSection]`.

- [ ] **Step 1: Failing tests**

```python
def test_flatten_markdown_strips_marks_keeps_bullets():
    from bookkit.services.export_open_items import flatten_markdown
    text = "## Head\n- **bold** item\n* second [link](http://x)\n`code`"
    assert flatten_markdown(text) == "Head\n- bold item\n- second link\ncode"


def test_compose_groups_by_program_project_and_general(conn):
    # build: client with an org-level task, a placement-attached task,
    # an outstanding submission on that placement, and a project need
    ...
    sections = compose(conn, client.id, date(2026, 8, 12))
    labels = [s.label for s in sections]
    assert labels[0].startswith("General")
    assert any(l.startswith("Acme Property") for l in labels)
    assert any(l.startswith("Project — ") for l in labels)
    task_row = sections[0].rows[0]
    assert task_row.kind == "Task" and task_row.days_open >= 0
    assert "brief line" in task_row.details  # description first line of the cell


def test_compose_empty_book_returns_no_sections(conn):
    org = orgs.create(conn, name="Empty Co", kind="client")
    assert compose(conn, org.id, date(2026, 8, 12)) == []
```

Fill the `...` fixture-building with the same repo calls used in Task 5's test plus `tasks_repo.create(..., placement_id=p.id)` and `projects_repo.create_project` / `add_need`.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_services.py -k "flatten or compose" -v 2>&1 | tail -3`

- [ ] **Step 3: Implement**

```python
"""Client-facing open-items list, composed PURELY — rendering is towerkit's
job (write() in this module glues to towerkit.render.table_xlsx; nothing in
bookkit imports openpyxl). Sections: General (org-level tasks), one per
placement (its tasks + outstanding submissions), one per project (unmet
needs). Determinism: `today` is a parameter, never the wall clock."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date

from ..models import Org, Task
from ..money import format_cents
from ..repo import orgs, placements, submissions
from ..repo import projects as projects_repo
from ..repo import tasks as tasks_repo


@dataclass(frozen=True)
class ExportRow:
    item: str
    details: str
    kind: str      # "Task" | "Need" | "Submission"
    due: str       # ISO or ""
    status: str
    days_open: int


@dataclass(frozen=True)
class ExportSection:
    label: str | None
    rows: tuple[ExportRow, ...]


_MD_STRIP = (
    (re.compile(r"```.*?```", re.S), ""),          # fenced code blocks
    (re.compile(r"^#{1,6}\s*", re.M), ""),          # headings
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),  # links → text
    (re.compile(r"[*_]{1,3}(\S(?:.*?\S)?)[*_]{1,3}"), r"\1"),  # emphasis
    (re.compile(r"`([^`]*)`"), r"\1"),              # inline code
    (re.compile(r"^\s*[*+]\s+", re.M), "- "),       # bullets normalize to "- "
)


def flatten_markdown(text: str) -> str:
    """Markdown notes → clean plain text for a spreadsheet cell. Bullets
    survive as '- ' lines; everything decorative is stripped."""
    out = text
    for pattern, repl in _MD_STRIP:
        out = pattern.sub(repl, out)
    return "\n".join(line.rstrip() for line in out.splitlines() if line.strip())


def _days_since(created_at: str, today: date) -> int:
    return (today - date.fromisoformat(created_at[:10])).days


def _task_row(task: Task, today: date) -> ExportRow:
    details = "\n".join(
        part for part in (task.description or "", flatten_markdown(task.detail or ""))
        if part
    )
    overdue = bool(task.due_on) and task.due_on < today.isoformat()
    return ExportRow(
        item=task.title, details=details, kind="Task", due=task.due_on or "",
        status="Overdue" if overdue else "Open",
        days_open=_days_since(task.created_at, today),
    )


def compose(conn: sqlite3.Connection, org_id: str, today: date) -> list[ExportSection]:
    org = orgs.get(conn, org_id)
    sections: list[ExportSection] = []

    org_tasks = tasks_repo.open_tasks(conn, org_id=org.id)
    general = tuple(_task_row(t, today) for t in org_tasks if not t.placement_id)
    by_placement: dict[str, list[Task]] = {}
    for t in org_tasks:
        if t.placement_id:
            by_placement.setdefault(t.placement_id, []).append(t)

    subs = submissions.outstanding_for_org(conn, org.id)
    subs_by_placement: dict[str, list[sqlite3.Row]] = {}
    loose_subs: list[sqlite3.Row] = []
    for row in subs:
        if row["about_placement_id"]:
            subs_by_placement.setdefault(row["about_placement_id"], []).append(row)
        else:
            loose_subs.append(row)

    general_rows = list(general) + [
        ExportRow(
            item=f"Submission to {row['market_name']}",
            details=row["about"] or "", kind="Submission", due="",
            status="Out at market", days_open=_days_since(row["sent_on"], today),
        )
        for row in loose_subs
    ]
    if general_rows:
        sections.append(ExportSection(f"General — {org.name}", tuple(general_rows)))

    from .. import sync  # line labels for section headers, matching attention tables

    for placement in placements.for_org(conn, org.id):
        rows = [_task_row(t, today) for t in by_placement.get(placement.id, [])]
        rows += [
            ExportRow(
                item=f"Submission to {row['market_name']}",
                details=row["about"] or "", kind="Submission", due="",
                status="Out at market", days_open=_days_since(row["sent_on"], today),
            )
            for row in subs_by_placement.get(placement.id, [])
        ]
        if rows:
            lines = sync.line_labels(placement.program_path)
            label = placement.program_name + (f" ({lines})" if lines else "")
            sections.append(ExportSection(label, tuple(rows)))

    for project in projects_repo.projects_for_org(conn, org.id):
        needs = [
            n for n in projects_repo.needs_for_project(conn, project.id)
            if n.status in projects_repo.ATTENTION_STATUSES
        ]
        if needs:
            sections.append(ExportSection(
                f"Project — {project.name}",
                tuple(
                    ExportRow(
                        item=f"{n.line} cover",
                        details="\n".join(part for part in (
                            n.notes or "",
                            f"Limit {format_cents(n.limit_cents)}" if n.limit_cents else "",
                        ) if part),
                        kind="Need", due=n.needed_by, status=n.status,
                        days_open=_days_since(n.created_at, today),
                    )
                    for n in needs
                ),
            ))
    return sections
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_services.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "export: pure open-items composition — sections per program/project + general"`

---

### Task 7: bookkit — the writer glue + determinism/empty-book tests

**Files:**
- Modify: `src/bookkit/services/export_open_items.py` (append `write`)
- Test: `tests/test_services.py` (append)

**Interfaces:**
- Consumes: towerkit `write_table` (Task 2), `compose` (Task 6).
- Produces: `write(conn, org_id: str, out_path: Path, today: date) -> Path`.

- [ ] **Step 1: Failing tests**

```python
def test_write_open_items_deterministic_and_styled(conn, tmp_path):
    # reuse the fixture-building from test_compose_groups...
    a = export_open_items.write(conn, client.id, tmp_path / "a.xlsx", date(2026, 8, 12))
    b = export_open_items.write(conn, client.id, tmp_path / "b.xlsx", date(2026, 8, 12))
    assert a.read_bytes() == b.read_bytes()
    from openpyxl import load_workbook  # test-only import; src never imports it
    ws = load_workbook(a).active
    assert [c.value for c in ws[1]] == [
        "Item", "Details", "Type", "Due / Needed by", "Status", "Days open"]


def test_write_empty_book_says_so(conn, tmp_path):
    org = orgs.create(conn, name="Empty Co", kind="client")
    path = export_open_items.write(conn, org.id, tmp_path / "e.xlsx", date(2026, 8, 12))
    from openpyxl import load_workbook
    assert load_workbook(path).active["A2"].value == "No open items as of 2026-08-12"
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement** (append to the module):

```python
_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Item", 30.0), ("Details", 58.0), ("Type", 12.0),
    ("Due / Needed by", 16.0), ("Status", 14.0), ("Days open", 10.0),
)


def write(conn: sqlite3.Connection, org_id: str, out_path: "Path", today: date) -> "Path":
    """Render via towerkit so the workbook carries SOI formatting exactly —
    formatting authority stays in one place (the money.parse_share pattern)."""
    from towerkit.render.table_xlsx import TableColumn, TableSection, write_table
    from towerkit.theme import load_theme

    org = orgs.get(conn, org_id)
    columns = [TableColumn(h, w) for h, w in _COLUMNS[:-1]]
    columns.append(TableColumn("Days open", 10.0, align="right"))

    sections = [
        TableSection(
            s.label,
            tuple((r.item, r.details, r.kind, r.due, r.status, r.days_open)
                  for r in s.rows),
        )
        for s in compose(conn, org_id, today)
    ] or [TableSection(None, ((f"No open items as of {today.isoformat()}",
                               "", "", "", "", ""),))]

    return write_table(
        columns, sections,
        title=f"Open Items — {org.name}"[:31],  # Excel sheet-title cap
        theme=load_theme(None), out_path=out_path,
        # Details is the only multi-line column; two-line floor like the SOI
        row_height=lambda values: 18.0 * max(2, str(values[1]).count("\n") + 1),
    )
```

Add `from pathlib import Path` at the top of the module (drop the string annotations).

- [ ] **Step 4: Run** — `uv run pytest tests/test_services.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "export: SOI-formatted open-items workbook via towerkit table writer"`

---

### Task 8: bookkit — `bookctl export open-items`

**Files:**
- Modify: `src/bookkit/cli.py` (parser block after the `import` parser at :56; dispatch branch after `import` at :184)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `export_open_items.write` (Task 7).

- [ ] **Step 1: Failing test** (mirror `test_cli.py`'s existing pattern of calling `cli.main([...])` with a seeded tmp db and capsys):

```python
def test_export_open_items_writes_workbook(tmp_path, capsys):
    db_path = tmp_path / "b.db"
    conn = db.connect(db_path)
    org = orgs.create(conn, name="Acme", kind="client")
    tasks_repo.create(conn, "chase quote", org_id=org.id)
    conn.close()
    out = tmp_path / "acme.xlsx"
    rc = cli.main(["--db", str(db_path), "export", "open-items", "Acme", "--out", str(out)])
    assert rc == 0 and out.exists()


def test_export_unknown_org_suggests(tmp_path, capsys):
    db_path = tmp_path / "b.db"
    conn = db.connect(db_path)
    orgs.create(conn, name="Acme Corp", kind="client")
    conn.close()
    rc = cli.main(["--db", str(db_path), "export", "open-items", "Acme Copr"])
    assert rc == 2
    assert "Acme Corp" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

Parser (after the `import` parser block):

```python
    export_p = sub.add_parser("export", help="client-facing workbook exports")
    export_p.add_argument("flow", choices=["open-items"])
    export_p.add_argument("org", help="client name or ref")
    export_p.add_argument("--out", type=Path, default=None,
                          help="default: <ref>-open-items-<date>.xlsx")
```

Dispatch (after the `import` branch):

```python
    if args.command == "export":
        from .repo import orgs as orgs_repo
        from .services import export_open_items

        org = orgs_repo.find(conn, args.org) or orgs_repo.find_by_name(conn, args.org)
        if org is None:
            from rapidfuzz import process

            names = [o.name for o in orgs_repo.list_orgs(conn, kind="client")]
            close = process.extract(args.org, names, limit=3, score_cutoff=60)
            hint = f" — did you mean: {', '.join(m[0] for m in close)}" if close else ""
            print(f"no client matching {args.org!r}{hint}")
            return 2
        today = date.today()
        out = args.out or Path(f"{org.ref}-open-items-{today.isoformat()}.xlsx")
        path = export_open_items.write(conn, org.id, out, today)
        print(f"wrote {path}")
        return 0
```

(`date` is already imported at the top of cli.py.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_cli.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "cli: bookctl export open-items — SOI-formatted client deliverable"`

---

### Task 9: bookkit — Navigator export action (`x`)

**Files:**
- Modify: `src/bookkit/tui/screens/navigator.py` (BINDINGS at :93; new action method near the other `action_*_row` methods after :522)
- Test: `tests/test_tui.py` (append)

- [ ] **Step 1: Failing pilot test** — seed a client, focus its account node in the tree, press `x`, assert the file exists in `tmp_path` (chdir the test with `monkeypatch.chdir(tmp_path)` so the default filename lands there).

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

Add to BINDINGS: `Binding("x", "export_row", "Export open items", show=False),`

Read the existing `action_edit_row` (below :522) to see exactly how it resolves the org for the current tree node vs a focused table row (`self._current` / `self._row_org` + the table's cursor key helper), then:

```python
    def action_export_row(self) -> None:
        """x — the open-items workbook for the client under the cursor."""
        from ...services import export_open_items

        org_id = self._export_target_org()   # same resolution the row actions use
        if org_id is None:
            self.notify("select a client first", severity="warning")
            return
        conn = self.app.conn
        org = orgs.get(conn, org_id)
        today = date.today()
        out = Path(f"{org.ref}-open-items-{today.isoformat()}.xlsx")
        path = export_open_items.write(conn, org_id, out, today)
        self.notify(f"wrote {path}")
```

`_export_target_org` mirrors the org-resolution in the existing row actions: `self._current[0] == "account"` → its payload; a focused table row → `self._row_org` lookup; else None. Extract that resolution into the helper if the row actions inline it — one implementation, shared.

Also append `x export open items` to the account-node hint if `_render_hint` shows per-node hints for accounts (check `_render_hint`'s account branch; add only where hints already render).

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "navigator: x exports the selected client's open-items workbook"`

---

### Task 10: bookkit — convention test + spec correction + final gates

**Files:**
- Create: `tests/test_conventions.py`
- Modify: `docs/superpowers/specs/2026-08-12-open-items-export-design.md`

- [ ] **Step 1: Write the convention test**

The spec said "no openpyxl import anywhere in bookkit src" — that was WRONG when written: `imports/readers.py` and `imports/fieldspec.py` legitimately lazy-import it for workbook reading/templates. The real rule: openpyxl never enters services/, tui/, or cli.py, so the export path stays render-free.

```python
"""Architecture conventions that grep can enforce."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "bookkit"


def test_no_openpyxl_outside_imports_package():
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        if rel.parts[0] == "imports":
            continue  # readers/templates own workbook I/O
        assert "openpyxl" not in path.read_text(), f"openpyxl leaked into {rel}"


def test_no_raw_sql_in_tui_or_imports():
    for pkg in ("tui", "imports"):
        for path in (SRC / pkg).rglob("*.py"):
            assert ".execute(" not in path.read_text(), \
                f"raw SQL in {path.relative_to(SRC)} — queries live in repo/"
```

(If a pre-existing convention test already covers the SQL rule elsewhere, keep both — this one is cheap.)

- [ ] **Step 2: Correct the spec** — in the export spec's Testing section, replace "no openpyxl import anywhere in bookkit src" with "no openpyxl outside the imports/ package (readers/templates already use it)".

- [ ] **Step 3: Full gates, both repos**

```bash
cd /Users/grantgreeson/Developer/towerkit && uv run pytest -q && uv run mypy src && uv run ruff check src tests
cd /Users/grantgreeson/Developer/bookkit && uv run pytest -q && uv run mypy src && uv run ruff check src tests
```

- [ ] **Step 4: Commit** — `git add tests/test_conventions.py docs/superpowers/specs/2026-08-12-open-items-export-design.md && git commit -m "test: convention scans (openpyxl containment, no raw SQL in tui/imports)"`

- [ ] **Step 5: Shipping note (do not skip)** — this feature needs a towerkit RELEASE before the work machine can use it (bookkit's wheelhouse/PyPI flow pulls towerkit as a wheel there). Follow the new-dep/release drill in towerkit's CLAUDE.md. No bookkit dependency changed, so no bookkit wheelhouse refresh is triggered by this plan.

---

### Task 11: Task `category` — migration, vocab, form (feature add 2026-08-12)

Freeform grouping label for tasks — vocabulary-completed like lines, NEVER a hard-coded enum. Grant's ask: group tasks in bookkit and SOV-style in the export.

**Files:**
- Create: `migrations/009_task_category.sql`
- Modify: `src/bookkit/models.py` (Task), `src/bookkit/repo/vocab.py`, `src/bookkit/tui/widgets/entity_forms.py` (task_form)
- Test: `tests/test_repo.py` (append)

**Interfaces:**
- Produces: `Task.category: str | None`; `vocab.task_categories(conn) -> list[str]`; task_form carries a suggestions-wired `category` Field.

- [ ] **Step 1: Failing test**

```python
def test_task_category_round_trips_and_feeds_vocab(conn):
    from bookkit.repo import vocab

    tasks_repo.create(conn, "chase quote", category="Renewal")
    tasks_repo.create(conn, "send COI", category="Certificates")
    tasks_repo.create(conn, "misc")  # no category
    assert vocab.task_categories(conn) == ["Certificates", "Renewal"]
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_repo.py -k category -v 2>&1 | tail -3`

- [ ] **Step 3: Implement**

`migrations/009_task_category.sql`:

```sql
ALTER TABLE task ADD COLUMN category TEXT;
```

`models.py` Task, after `description`:

```python
    category: str | None = None  # freeform grouping label, vocab-completed
```

`repo/vocab.py`:

```python
def task_categories(conn: sqlite3.Connection) -> list[str]:
    return _dedupe(_column(conn, "task", "category"))
```

`entity_forms.py::task_form` — add after the `description` Field (task_form already receives `conn`; guard for None):

```python
        Field("category", "category",
              suggestions=tuple(vocab.task_categories(conn)) if conn else ()),
```

(`vocab` is already imported in entity_forms.py.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_repo.py tests/test_tui_forms.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "tasks: freeform vocab-completed category for grouping"`

---

### Task 12: category grouping in the task tables

**Files:**
- Modify: `src/bookkit/tui/screens/navigator.py` (both task-table branches + TASK_INLINE), `src/bookkit/tui/screens/account.py` (#ov-tasks fill)
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Consumes: Task 11's column; Task 4's table layout (columns currently due/task/description/detail/…).

- [ ] **Step 1: Failing test** — extend the Task 4 test (or add a sibling) asserting a `category` column exists on the attention tasks table and rows arrive grouped: seed three tasks with categories B, A, A and assert the table's row order puts the two A-category tasks adjacent.

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

Grouping is display-level: repo ordering stays authoritative for briefs. In each of the three task-table fills, sort the fetched list before rendering:

```python
rows = sorted(task_list, key=lambda t: ((t.category or "~"), t.due_on or "~"))
```

("~" sorts uncategorized/undated last.) Insert a `category` column immediately after "task" title column in all three tables, rendering `Text(t.category, style=theme.AMBER) if t.category else dash()`. Update TASK_INLINE so `category` is `i`-editable — recount the column indexes after insertion and keep due/title/description editable at their new positions:

```python
TASK_INLINE = {
    0: Field("due_on", "due", "date"),
    1: Field("title", "task", required=True),
    2: Field("category", "category"),
    3: Field("description", "description"),
}
```

(Order above assumes columns due · task · category · description · detail · … — verify against the actual Task 4 layout and keep indexes true to it.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui.py -q 2>&1 | tail -3` → PASS (update any column-count assertions).

- [ ] **Step 5: Commit** — `git commit -m "tasks: category column + grouping across the task tables"`

---

### Task 13: category sections in the export (SOV-style)

**Files:**
- Modify: `src/bookkit/services/export_open_items.py` (compose)
- Test: `tests/test_services.py` (append)

**Interfaces:**
- Consumes: Task 6's compose + Task 11's column. ExportRow/ExportSection shapes unchanged.

- [ ] **Step 1: Failing test**

```python
def test_compose_sections_org_tasks_by_category(conn):
    org = orgs.create(conn, name="Cat Co", kind="client")
    tasks_repo.create(conn, "renew GL", org_id=org.id, category="Renewal")
    tasks_repo.create(conn, "renew AL", org_id=org.id, category="Renewal")
    tasks_repo.create(conn, "send COI", org_id=org.id, category="Certificates")
    tasks_repo.create(conn, "misc", org_id=org.id)
    labels = [s.label for s in compose(conn, org.id, date(2026, 8, 12))]
    assert labels == ["Certificates — Cat Co", "Renewal — Cat Co", "General — Cat Co"]
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement** — in `compose`, replace the single General section for org-level tasks: group `general` rows' source tasks by `category` (alphabetical, case-insensitive), one section per category labeled `f"{category} — {org.name}"`, uncategorized tasks LAST as `f"General — {org.name}"` (loose opportunity submissions stay in General). Placement and project sections unchanged (REVIEW POINT in spec: category does not subdivide placement sections yet).

- [ ] **Step 4: Run** — `uv run pytest tests/test_services.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "export: org-level tasks sectioned by category, SOV-style"`
