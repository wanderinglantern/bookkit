# bookkit Web Account Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `bookctl web` — a localhost browser surface serving one bookkit account page (Overview, Contacts, Interactions) that can read and edit records, with every write forming one revertible batch.

**Architecture:** Extract the presentation-agnostic form layer (`Field`, `FormSpec`, `BatchSpec`, the value parser, the cleaner map) out of `tui/widgets/` into a new `bookkit/forms/` package that both the TUI and the web layer consume. The web layer is FastAPI routes rendering Jinja templates, with HTMX for partial swaps. It holds no field lists, no validators, no normalisation, and no SQL — it fetches through `repo/`, renders a `FormSpec`, and calls the same `apply_*` function the TUI calls inside `services.batches.open_batch(source="web", …)`.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, Jinja2 (via `fastapi.templating`), HTMX (vendored, no build step), pytest + `fastapi.testclient.TestClient`, SQLite.

**Spec:** `docs/superpowers/specs/2026-08-17-web-frontend-design.md`

## Global Constraints

- **Bind to loopback only.** `uvicorn` host is `127.0.0.1`, never `0.0.0.0`. The database is mode 0600 and holds client contacts and premium figures.
- **No raw SQL in `web/`.** `.execute(` must not appear anywhere under `src/bookkit/web/`. Queries live in `repo/`.
- **`web/` never imports `bookkit.tui`, and `tui/` never imports `bookkit.web`.** Shared code lives in `bookkit/forms/` or it is not shared.
- **Money inputs are `<input type="text">`.** Never `type="number"` — it rejects `1,234.56` client-side before the server sees it. Entry accepts cents.
- **Date inputs are `<input type="text">`.** Never `type="date"` — a native picker bypasses `parse_human_date`, whose job is to *refuse* bare 1–2 digit input.
- **The renewal date is `RenewalItem.renewal_on`**, never `placement.period_to`. Print the same date you count to. Overdue is `days_remaining < 0`.
- **Every write goes through `services.batches.open_batch(conn, source="web", tool=…, summary=…, org_id=…)`.** No repo write outside a batch.
- **Gates before every commit:** `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. Put the gate on the command itself, never after a pipe — pipes eat exit codes and red suites get committed.
- **Gate output goes to `$GATE`, never `/tmp`.** Concurrent pytest runs interleave in `/tmp`. Set it once per shell session to this session's scratchpad directory before running any gate:
  ```bash
  export GATE=<this session's scratchpad directory>
  ```
  Every gate command below writes to `"$GATE/out.txt"`, `"$GATE/mypy.txt"`, or `"$GATE/ruff.txt"`.
- **Baseline for this branch: 757 passed, 38 snapshots, exit 0.** Any task that ends with a different passing count has changed behaviour — find out why before committing.
- **`uvicorn` and `starlette` are already installed transitively via `mcp`.** Task 4 still declares them explicitly (a transitive dependency is not a contract), but expect `uv sync` to report a smaller delta than the four new names suggest.
- **Line length 100** (ruff config), target `py311`.
- **Appearance is governed by `docs/superpowers/specs/2026-08-17-web-visual-direction.md`.** Read it before writing any template or stylesheet. Where it and a task's inline HTML disagree, it wins on appearance; the task wins on structure and routes. Load-bearing rules from it: no literal hex outside `bookkit/palette.py`; mono only where values align (dates, money, counts, refs) and sans for language; every coloured state also carries a word or glyph; `:focus-visible` is always a visible gold outline; radius is 2px on controls and 0 elsewhere; no shadows or gradients.
- Work in the worktree `.claude/worktrees/web-account` on branch `web-account`. A fresh worktree needs `uv sync --group dev`, and gates run as `uv run --no-sync python -m pytest`.

---

## File Structure

**Created:**

- `src/bookkit/forms/__init__.py` — re-exports nothing; the package is a namespace only.
- `src/bookkit/forms/spec.py` — `Field`, `FormSpec`, `BatchSpec`, `dropped`, `PLACEHOLDERS`, `CLEANERS`, `parse_value`, `parse_values`, `FieldError`. No Textual, no FastAPI.
- `src/bookkit/forms/entities.py` — the 17 `*_form()` builders and 13 `apply_*()` appliers, moved verbatim from `tui/widgets/entity_forms.py`.
- `src/bookkit/web/__init__.py` — empty.
- `src/bookkit/web/app.py` — `create_app(db_path)` returning a `FastAPI`; owns the connection lifecycle.
- `src/bookkit/web/serve.py` — `serve(db_path, port)`; uvicorn bootstrap and browser open.
- `src/bookkit/web/theme_css.py` — `css_variables()`, deriving CSS custom properties from `tui/theme.py` colour constants.
- `src/bookkit/web/routes/__init__.py` — empty.
- `src/bookkit/web/routes/account.py` — account page routes (overview, contacts, interactions).
- `src/bookkit/web/templates/base.html` — page shell.
- `src/bookkit/web/templates/macros/form.html` — the one form macro that renders any `FormSpec`.
- `src/bookkit/web/templates/account/*.html` — page and partial templates.
- `src/bookkit/web/static/htmx.min.js` — vendored, downloaded once.
- `src/bookkit/web/static/app.css`
- `tests/test_web_forms_spec.py`, `tests/test_web_shell.py`, `tests/test_web_parity.py`, `tests/test_web_account.py`, `tests/test_web_writes.py`

**Modified:**

- `src/bookkit/tui/widgets/forms.py` — keeps `FormModal` only; imports the dataclasses and parser from `bookkit.forms.spec`.
- `src/bookkit/tui/widgets/entity_forms.py` — **deleted**; call sites re-pointed at `bookkit.forms.entities`.
- `src/bookkit/mcpserver.py:976` — `_FIELD_CLEANERS` deleted, re-pointed at `bookkit.forms.spec`.
- `src/bookkit/cli.py` — new `web` subcommand.
- `tests/test_conventions.py` — two new rules.
- `pyproject.toml` — three runtime deps, one dev dep, package-data inclusion.

---

### Task 1: Extract the form spec and the shared value parser

The parser is the load-bearing piece: `FormModal._parse` is already a `@staticmethod` taking `(Field, str | None)` with no Textual dependency. Moving it makes money parsing, date refusal, and field cleaning identical on both surfaces by construction rather than by discipline.

**Files:**
- Create: `src/bookkit/forms/__init__.py`, `src/bookkit/forms/spec.py`
- Modify: `src/bookkit/tui/widgets/forms.py`
- Test: `tests/test_web_forms_spec.py`

**Interfaces:**
- Consumes: `bookkit.dates.parse_human_date`, `bookkit.money.parse_money_cents`, `bookkit.money.MoneyParseError`, `bookkit.money.format_cents`, `bookkit.normalize.{clean_text,clean_email,clean_phone,clean_url,clean_domain,clean_linkedin,clean_naics}`
- Produces:
  - `Field(key, label, kind="text", options=(), required=False, placeholder="", optional_select=False, suggestions=())`
  - `FormSpec(title, fields, initial={})`
  - `BatchSpec(tool, summary, org_id=None)` with `.sentence(values)` and `.for_title(title, org_id=None)`
  - `dropped(values: dict[str, Any]) -> dict[str, Any]`
  - `CLEANERS: dict[str, Callable[[str], str]]`, `PLACEHOLDERS: dict[str, str]`
  - `FieldError(Exception)` with `.field_key: str` and `.message: str`
  - `parse_value(field: Field, raw: str | None) -> Any`
  - `parse_values(spec: FormSpec, raw: Mapping[str, str | None]) -> dict[str, Any]` — raises `FieldError`
  - `initial_text(field: Field, initial: Any) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_forms_spec.py`:

```python
"""The parser both surfaces share. If these behaviours ever differ between
the TUI and the web, a record saved in one place is not the record saved in
the other."""

from __future__ import annotations

import pytest

from bookkit.forms.spec import (
    BatchSpec,
    Field,
    FieldError,
    FormSpec,
    dropped,
    initial_text,
    parse_value,
    parse_values,
)


def test_money_accepts_cents():
    """bookkit stores cents and format_cents renders them, so a form that
    refuses its own pre-filled value makes the record unsaveable."""
    assert parse_value(Field("premium", "premium", "money"), "1,234.56") == 123456


def test_money_accepts_shorthand():
    assert parse_value(Field("premium", "premium", "money"), "1.5m") == 150000000


def test_money_refusal_is_a_field_error():
    with pytest.raises(ValueError):
        parse_value(Field("premium", "premium", "money"), "not money")


def test_bare_number_is_not_a_date():
    """dateparser reads '5' as a MONTH and future-biases it; 'the 5th' once
    saved as 2027-05-01 and fell off every attention window."""
    with pytest.raises(ValueError):
        parse_value(Field("due_on", "due", "date"), "5")


def test_human_date_parses_to_iso():
    assert parse_value(Field("due_on", "due", "date"), "2026-10-15") == "2026-10-15"


def test_email_is_cleaned():
    assert parse_value(Field("email", "email", "email"), "  A@B.COM ") == "a@b.com"


def test_textarea_is_stored_verbatim():
    field = Field("notes", "notes", "textarea")
    assert parse_value(field, "  two  spaces\nand a line  ") == "  two  spaces\nand a line  "


def test_blank_becomes_none():
    assert parse_value(Field("title", "title"), "   ") is None


def test_parse_values_reports_the_offending_field():
    spec = FormSpec("edit thing", [Field("due_on", "due", "date")])
    with pytest.raises(FieldError) as caught:
        parse_values(spec, {"due_on": "5"})
    assert caught.value.field_key == "due_on"
    assert "due" in caught.value.message


def test_parse_values_enforces_required():
    spec = FormSpec("new thing", [Field("first_name", "first name", required=True)])
    with pytest.raises(FieldError) as caught:
        parse_values(spec, {"first_name": ""})
    assert caught.value.field_key == "first_name"
    assert "required" in caught.value.message


def test_dropped_strips_none_but_keeps_empty_string():
    assert dropped({"a": None, "b": "", "c": 0}) == {"b": "", "c": 0}


def test_initial_text_renders_money_without_a_dollar_sign():
    assert initial_text(Field("premium", "premium", "money"), 123456) == "1,234.56"


def test_batch_spec_derives_tool_from_title_without_the_record_name():
    batch = BatchSpec.for_title("edit contact — Atomic Industries")
    assert batch.tool == "edit_contact"
    assert batch.sentence({}) == "edit contact — Atomic Industries"
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_forms_spec.py -q > "$GATE/out.txt" 2>&1; tail -5 "$GATE/out.txt"
```

Expected: collection error, `ModuleNotFoundError: No module named 'bookkit.forms'`.

- [ ] **Step 3: Create the package and move the dataclasses**

Create `src/bookkit/forms/__init__.py` as an empty file.

Create `src/bookkit/forms/spec.py`. Move `Field`, `FormSpec`, `BatchSpec`, `dropped`, `_PLACEHOLDERS` (renamed `PLACEHOLDERS`) and `_CLEANERS` (renamed `CLEANERS`) out of `tui/widgets/forms.py` **verbatim**, then add the parser:

```python
"""Presentation-agnostic form definitions and the one value parser.

Both surfaces render from these: the TUI through FormModal, the web through
the Jinja form macro. The parser lives here rather than on either renderer so
that money round-trips as cents and a bare number is refused as a date in
exactly one place."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from ..dates import parse_human_date
from ..money import MoneyParseError, format_cents, parse_money_cents
from ..normalize import (
    clean_domain,
    clean_email,
    clean_linkedin,
    clean_naics,
    clean_phone,
    clean_text,
    clean_url,
)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    # text | textarea | select | date | money | int
    # + normalised kinds: email | phone | url | domain | linkedin | naics
    kind: str = "text"
    options: tuple[tuple[str, str], ...] = ()  # (label, value) for select
    required: bool = False
    placeholder: str = ""
    optional_select: bool = False  # allow_blank for selects
    # existing-record vocabulary: dropdown menu (tab/enter picks) plus inline
    # ghost text (right arrow accepts) — data consistency by completion
    suggestions: tuple[str, ...] = ()


@dataclass
class FormSpec:
    title: str
    fields: list[Field]
    initial: dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class BatchSpec:
    """What undo unit this form's save belongs to."""

    tool: str
    summary: str | Callable[[dict[str, Any]], str]
    org_id: str | None = None

    def sentence(self, values: dict[str, Any]) -> str:
        return self.summary(values) if callable(self.summary) else self.summary

    @staticmethod
    def for_title(title: str, org_id: str | None = None) -> BatchSpec:
        """'edit contact — Atomic Industries' becomes tool 'edit_contact' with
        the whole title as the summary: the changes list groups by tool, so the
        slug must not carry the record's name, while the sentence should."""
        head = title.split("—")[0].split("(")[0].strip().lower()
        return BatchSpec(
            tool="_".join(head.split()[:3]) or "form", summary=title, org_id=org_id
        )


PLACEHOLDERS = {
    "date": "today · fri · +2w · 2026-10-15",
    "money": "1.5m · 250k · 1,500,000",
    "phone": "312 555 0142 · +44 …",
    "email": "name@company.com",
    "linkedin": "profile URL or handle",
    "url": "https://company.com",
    "domain": "company.com",
    "naics": "6-digit code · 524126",
}

# Everything typed gets cleaned on save; textarea (multi-line notes) is the
# one kind stored verbatim.
CLEANERS: dict[str, Callable[[str], str]] = {
    "text": clean_text,
    "email": clean_email,
    "phone": clean_phone,
    "url": clean_url,
    "domain": clean_domain,
    "linkedin": clean_linkedin,
    "naics": clean_naics,
    "textarea": lambda text: text,
}


class FieldError(Exception):
    """A value the parser refused, tagged with the field it came from so a
    renderer can put focus (TUI) or an inline message (web) in the right
    place."""

    def __init__(self, field_key: str, message: str) -> None:
        super().__init__(message)
        self.field_key = field_key
        self.message = message


def parse_value(field: Field, raw: str | None) -> Any:
    """One raw widget/form string → the stored representation. Money returns
    integer cents, dates return ISO strings, everything else is cleaned."""
    text = (raw or "").strip()
    if field.kind == "textarea":
        # verbatim, but a whitespace-only note is still nothing
        return (raw or "") if (raw or "").strip() else None
    if not text:
        return None
    if field.kind == "date":
        parsed = parse_human_date(text)
        if parsed is None:
            raise ValueError(f"cannot read a date from {text!r}")
        return parsed.isoformat()
    if field.kind == "money":
        try:
            return parse_money_cents(text)
        except MoneyParseError as exc:
            raise ValueError(str(exc)) from exc
    if field.kind == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{text!r} is not a whole number") from exc
    cleaner = CLEANERS.get(field.kind, clean_text)
    return cleaner(text)


def parse_values(spec: FormSpec, raw: Mapping[str, str | None]) -> dict[str, Any]:
    """Parse every field in the spec, refusing on the first bad or missing
    required value. Raises FieldError."""
    values: dict[str, Any] = {}
    for field in spec.fields:
        try:
            values[field.key] = parse_value(field, raw.get(field.key))
        except ValueError as exc:
            raise FieldError(field.key, f"{field.label}: {exc}") from exc
        if field.required and values[field.key] in (None, ""):
            raise FieldError(field.key, f"{field.label} is required")
    return values


def initial_text(field: Field, initial: Any) -> str:
    """The string a renderer should pre-fill. Money renders as plain cents
    with no dollar sign, because the parser accepts exactly that back."""
    if initial is None:
        return ""
    if field.kind == "money":
        return format_cents(int(initial)).lstrip("$")
    return str(initial)


def dropped(values: dict[str, Any]) -> dict[str, Any]:
    """Strip None entries so optional blanks don't overwrite on edit."""
    return {k: v for k, v in values.items() if v is not None}
```

- [ ] **Step 4: Run the new test and confirm it passes**

```bash
uv run --no-sync python -m pytest tests/test_web_forms_spec.py -q > "$GATE/out.txt" 2>&1; tail -5 "$GATE/out.txt"
```

Expected: 12 passed.

- [ ] **Step 5: Re-point `FormModal` at the shared module**

In `src/bookkit/tui/widgets/forms.py`:
1. Delete the `Field`, `FormSpec`, `BatchSpec` class definitions, the `_PLACEHOLDERS` and `_CLEANERS` dicts, the `dropped` function, and the `FormModal._parse` staticmethod and `FormModal._initial_text` staticmethod.
2. Replace the `bookkit.dates` / `bookkit.money` / `bookkit.normalize` imports with:

```python
from ...forms.spec import (
    BatchSpec,
    Field,
    FieldError,
    FormSpec,
    PLACEHOLDERS,
    dropped,
    initial_text,
    parse_values,
)
```

3. Replace the body of `action_save`'s parse loop with a call to the shared parser, preserving the notify-and-focus behaviour exactly:

```python
    def action_save(self) -> None:
        raw = {f.key: self._drain(f) for f in self.spec.fields}
        try:
            values = parse_values(self.spec, raw)
        except FieldError as exc:
            self.notify(exc.message, severity="error")
            self.query_one(f"#form-{exc.field_key}").focus()
            return
        if self._commit is not None:
            try:
                error = self._run_commit(values)
            except Exception as exc:  # a failed save must never crash the TUI
                error = str(exc)
            if error is not None:
                self.notify(error, severity="error")
                return
        if self._draft_key:
            from ...repo import drafts

            drafts.clear(self.app.conn, self._draft_key)
        self.dismiss(values)
```

4. In `_compose_fields`, replace `self._initial_text(f, initial)` with `initial_text(f, initial)` and `_PLACEHOLDERS` with `PLACEHOLDERS`.

- [ ] **Step 6: Run the whole suite — the existing tests are the gate on this move**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
```

Expected: PASS, same count as before the change. `tests/test_tui_forms.py` and `tests/test_form_entry.py` are the ones that would catch a behaviour change; if either fails, the move changed semantics — fix the move, do not adjust the test.

- [ ] **Step 7: Run mypy and ruff**

```bash
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -5 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -5 "$GATE/ruff.txt"
```

Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add src/bookkit/forms/ src/bookkit/tui/widgets/forms.py tests/test_web_forms_spec.py
git commit -m "forms: extract Field/FormSpec/BatchSpec and the value parser out of the TUI

FormModal._parse was already a staticmethod with no Textual dependency. Moving
it to bookkit.forms.spec makes money-as-cents, the bare-number date refusal,
and field cleaning identical on every surface by construction."
```

---

### Task 2: Delete the `_FIELD_CLEANERS` duplicate in the MCP server

`mcpserver.py:976` holds a hand-copied duplicate of the TUI cleaner map, kept in sync by a comment. Two copies today; the web would make three. This task removes the duplicate now that there is one home for it.

**Files:**
- Modify: `src/bookkit/mcpserver.py` (the `_FIELD_CLEANERS` dict and `_clean_field_value`)
- Test: `tests/test_web_forms_spec.py` (append)

**Interfaces:**
- Consumes: `bookkit.forms.spec.CLEANERS` from Task 1
- Produces: no new public surface; `_clean_field_value(field, value)` keeps its signature

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_forms_spec.py`:

```python
def test_mcp_cleans_exactly_like_the_forms_do():
    """mcpserver kept a hand-copied duplicate of the cleaner map, held in sync
    by a comment. One home, or the surfaces drift."""
    from bookkit import mcpserver
    from bookkit.forms.spec import Field, parse_value

    for kind, raw in [
        ("email", "  A@B.COM "),
        ("phone", "(312) 555-0142"),
        ("url", "company.com"),
        ("domain", "https://company.com/path"),
        ("linkedin", "in/someone"),
        ("naics", "524126"),
        ("text", "  spaced  "),
    ]:
        assert mcpserver._clean_field_value(kind, raw) == parse_value(Field(kind, kind, kind), raw), kind


def test_mcp_has_no_second_cleaner_map():
    from pathlib import Path

    import bookkit

    source = (Path(bookkit.__file__).parent / "mcpserver.py").read_text()
    assert "_FIELD_CLEANERS" not in source, "the duplicate cleaner map is back"
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_forms_spec.py -q -k mcp > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: `test_mcp_has_no_second_cleaner_map` FAILS on the assert. The equivalence test may already pass — that is fine and expected; it is the regression guard, not the driver.

- [ ] **Step 3: Delete the duplicate**

In `src/bookkit/mcpserver.py`, delete the `_FIELD_CLEANERS` dict and its comment block, and replace `_clean_field_value` with:

```python
def _clean_field_value(field: str, value: str) -> str:
    """One cleaner map, shared with the forms (bookkit.forms.spec.CLEANERS),
    so an MCP-entered email is identical to one typed on either surface.
    Unknown fields fall through to clean_text, matching parse_value."""
    from .forms.spec import CLEANERS
    from .normalize import clean_text

    cleaner = CLEANERS.get(field, clean_text)
    return cleaner(value)
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run --no-sync python -m pytest tests/test_web_forms_spec.py tests/test_mcpserver.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: PASS. If `test_mcpserver.py` fails on a `notes` field, note that the old map mapped `"notes"` to `None` (verbatim) while `CLEANERS` keys on *kind* not field name — check whether the failing call passes a field named `notes` and, if so, keep behaviour by leaving `notes` out of the enrich path rather than re-adding a map.

- [ ] **Step 5: Full gates and commit**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add src/bookkit/mcpserver.py tests/test_web_forms_spec.py
git commit -m "mcp: delete the duplicated field-cleaner map

Two copies of the normalisation rules kept in sync by a comment. One home now
(bookkit.forms.spec.CLEANERS), with a test that fails if a second one returns."
```

---

### Task 3: Move `entity_forms.py` into the shared package

A pure move. The existing suite is the gate — every screen already exercises these builders.

**Files:**
- Create: `src/bookkit/forms/entities.py`
- Delete: `src/bookkit/tui/widgets/entity_forms.py`
- Modify: every module importing `entity_forms`

**Interfaces:**
- Consumes: `bookkit.forms.spec.{Field, FormSpec, dropped}` from Task 1
- Produces: `bookkit.forms.entities` exporting 17 `*_form()` builders and 13 `apply_*()` appliers, names unchanged — notably `contact_form(existing=None)`, `apply_contact(conn, org_id, values, existing=None)`, `interaction_form(existing)`, `apply_interaction(conn, values, existing)`, `org_form(existing=None, default_kind="client", *, conn=None)`, `apply_org(conn, values, existing=None)`, `org_form_initial_profile(conn, existing)`

- [ ] **Step 1: Find every importer**

```bash
grep -rn "entity_forms" src tests | tee "$GATE/importers.txt"
```

Record the list — every one gets rewritten in Step 3.

- [ ] **Step 2: Move the file**

```bash
git mv src/bookkit/tui/widgets/entity_forms.py src/bookkit/forms/entities.py
```

Then in `src/bookkit/forms/entities.py` fix the now-wrong relative imports: `from ...models import (...)` becomes `from ..models import (...)`, `from ...repo import (...)` becomes `from ..repo import (...)`, and `from .forms import Field, FormSpec, dropped` becomes `from .spec import Field, FormSpec, dropped`.

- [ ] **Step 3: Rewrite the call sites**

For each path in `"$GATE/importers.txt"`, replace the import with `from bookkit.forms import entities as entity_forms` (absolute) or the matching relative form — e.g. in `src/bookkit/tui/screens/account.py`, `from ..widgets import entity_forms` becomes `from ...forms import entities as entity_forms`. Keeping the local alias `entity_forms` means the ~100 usage sites in the screens need no edit.

- [ ] **Step 4: Run the whole suite**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
```

Expected: PASS with the same count as Task 2. A failure here is a missed import, not a behaviour change.

- [ ] **Step 5: Confirm nothing references the old location**

```bash
grep -rn "widgets.entity_forms\|widgets import entity_forms" src tests
```

Expected: no output.

- [ ] **Step 6: Gates and commit**

```bash
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add -A
git commit -m "forms: move entity_forms into bookkit.forms.entities

The builders never imported Textual and the appliers only take a connection.
They belong beside the spec, where a second surface can call them."
```

---

### Task 4: Web package skeleton, `bookctl web`, and the convention tests

**Files:**
- Create: `src/bookkit/web/__init__.py`, `src/bookkit/web/app.py`, `src/bookkit/web/serve.py`, `src/bookkit/web/templates/base.html`, `src/bookkit/web/static/app.css`
- Modify: `pyproject.toml`, `src/bookkit/cli.py`, `tests/test_conventions.py`
- Test: `tests/test_web_shell.py`

**Interfaces:**
- Consumes: `bookkit.db.connect(path, migrate=True)`
- Produces:
  - `bookkit.web.app.create_app(db_path: Path | str | None) -> FastAPI` — the app carries the connection at `app.state.conn`
  - `bookkit.web.serve.serve(db_path: Path | str | None, port: int, open_browser: bool = True) -> int`
  - CLI: `bookctl web [--port N] [--no-browser]`

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`, add to `[project].dependencies`:

```toml
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "python-multipart>=0.0.9",
    "jinja2>=3.1",
```

Add to `[dependency-groups].dev`:

```toml
    "httpx>=0.27",
```

Add package data so templates and static files ship in the wheel — under `[tool.hatch.build.targets.wheel]`:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/bookkit/web/templates" = "bookkit/web/templates"
"src/bookkit/web/static" = "bookkit/web/static"
```

Then:

```bash
uv sync --group dev
```

- [ ] **Step 2: Vendor HTMX**

```bash
mkdir -p src/bookkit/web/static
curl -fSL -o src/bookkit/web/static/htmx.min.js https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js
ls -l src/bookkit/web/static/htmx.min.js
```

Expected: a file of roughly 47 KB. It is committed to the repo — the work machine has no npm and no unpkg access.

- [ ] **Step 3: Write the failing test**

Create `tests/test_web_shell.py`:

```python
"""The server starts, serves its shell, and stays on loopback."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app) as test_client:
        yield test_client


def test_healthz_reports_the_database(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_static_htmx_is_served(client):
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text[:2000].lower()


def test_cli_registers_the_web_command():
    from bookkit.cli import build_parser

    args = build_parser().parse_args(["web", "--port", "8931"])
    assert args.command == "web"
    assert args.port == 8931


def test_serve_binds_loopback_only():
    """The database is 0600 and holds client contacts and premium figures.
    0.0.0.0 would publish the whole book to the LAN."""
    import inspect

    from bookkit.web import serve

    source = inspect.getsource(serve)
    assert "127.0.0.1" in source
    assert "0.0.0.0" not in source
```

- [ ] **Step 4: Run it and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_shell.py -q > "$GATE/out.txt" 2>&1; tail -5 "$GATE/out.txt"
```

Expected: `ModuleNotFoundError: No module named 'bookkit.web'`.

- [ ] **Step 5: Write the app factory**

Create `src/bookkit/web/__init__.py` (empty) and `src/bookkit/web/app.py`:

```python
"""FastAPI application factory.

The web layer holds no field lists, no validators, no normalisation and no
SQL: it reads through repo/, renders a FormSpec from bookkit.forms, and writes
through the same apply_* the TUI calls, inside one batch."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))


def create_app(db_path: Path | str | None = None) -> FastAPI:
    from .. import db

    app = FastAPI(title="bookkit", docs_url=None, redoc_url=None)
    conn: sqlite3.Connection = db.connect(db_path)
    app.state.conn = conn
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "db": str(db_path) if db_path else "default"}

    from .routes import account

    app.include_router(account.router)
    return app
```

Create `src/bookkit/web/routes/__init__.py` (empty) and a minimal `src/bookkit/web/routes/account.py` for now:

```python
"""Account page routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
```

Create `src/bookkit/web/serve.py`:

```python
"""uvicorn bootstrap. Loopback only — the database holds client contacts and
premium figures at mode 0600, and 0.0.0.0 would publish it to the LAN."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"


def serve(db_path: Path | str | None, port: int, open_browser: bool = True) -> int:
    import uvicorn

    from .app import create_app

    if open_browser:
        threading.Timer(0.7, webbrowser.open, args=(f"http://{HOST}:{port}/",)).start()
    uvicorn.run(create_app(db_path), host=HOST, port=port, log_level="warning")
    return 0
```

Create `src/bookkit/web/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}bookkit{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css">
  <script src="/static/htmx.min.js" defer></script>
</head>
<body>
  <main id="main">{% block content %}{% endblock %}</main>
</body>
</html>
```

Create `src/bookkit/web/static/app.css` with a single line for now: `:root { color-scheme: dark; }` — Task 11 fills it from the theme.

- [ ] **Step 6: Wire the CLI**

`cli.py` currently builds its parser inline inside `_run`. Extract it so the test can reach it: move the parser construction into a module-level `build_parser() -> argparse.ArgumentParser` returning the configured parser, and have `_run` call it. Add the subcommand beside the others:

```python
    web_p = sub.add_parser("web", help="serve the browser interface on localhost")
    web_p.add_argument("--port", type=int, default=8931)
    web_p.add_argument("--no-browser", action="store_true")
```

Add the dispatch branch in `_run`, beside the `mcp` branch and **before** the shared `db.connect` call, because `serve` opens its own connection:

```python
    if args.command == "web":
        from .web.serve import serve

        return serve(args.db, args.port, open_browser=not args.no_browser)
```

Add `"web"` to `READ_ONLY_COMMANDS` so a typo'd `--db` refuses instead of conjuring an empty book.

- [ ] **Step 7: Run the test and confirm it passes**

```bash
uv run --no-sync python -m pytest tests/test_web_shell.py -q > "$GATE/out.txt" 2>&1; tail -5 "$GATE/out.txt"
```

Expected: 4 passed.

- [ ] **Step 8: Add the convention tests**

Append to `tests/test_conventions.py`:

```python
def test_no_raw_sql_in_web():
    for path in (SRC / "web").rglob("*.py"):
        assert ".execute(" not in path.read_text(), \
            f"raw SQL in {path.relative_to(SRC)} — queries live in repo/"


def test_web_and_tui_never_import_each_other():
    """Shared code lives in bookkit.forms or it is not shared. A helper copied
    across the boundary is how the surfaces drift."""
    for path in (SRC / "web").rglob("*.py"):
        assert "bookkit.tui" not in path.read_text() and "from ..tui" not in path.read_text(), \
            f"{path.relative_to(SRC)} imports the TUI"
    for path in (SRC / "tui").rglob("*.py"):
        assert "bookkit.web" not in path.read_text() and "from ..web" not in path.read_text(), \
            f"{path.relative_to(SRC)} imports the web layer"
```

- [ ] **Step 9: Verify the convention tests can fail**

They are negative assertions — the class that passes for the wrong reason. Prove each one:

```bash
echo "# bookkit.tui" >> src/bookkit/web/app.py
uv run --no-sync python -m pytest tests/test_conventions.py -q > "$GATE/out.txt" 2>&1; tail -5 "$GATE/out.txt"
```

Expected: `test_web_and_tui_never_import_each_other` FAILS. Now revert:

```bash
git checkout src/bookkit/web/app.py 2>/dev/null || sed -i '' -e '$d' src/bookkit/web/app.py
```

Repeat for the SQL rule by appending `# .execute(` to a web module, confirming `test_no_raw_sql_in_web` fails, then reverting. Only after both have been seen failing is this step done.

- [ ] **Step 10: Full gates and commit**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add -A
git commit -m "web: FastAPI shell, bookctl web, and the boundary conventions

Loopback only. web/ holds no SQL and never imports the TUI; both rules are
asserted, and both assertions were confirmed capable of failing."
```

---

### Task 5: The parity ledger

Introduced now, with everything pending, so it cannot be forgotten later. Scoped to `AccountScreen` for this slice; stage 2 extends it to the other screens.

**Files:**
- Create: `tests/test_web_parity.py`, `src/bookkit/web/parity.py`

**Interfaces:**
- Consumes: `bookkit.tui.screens.account.AccountScreen.BINDINGS`
- Produces: `bookkit.web.parity.IMPLEMENTED: dict[str, str]` (TUI action name → web route path) and `PENDING: dict[str, str]` (TUI action name → reason)

Note: `web/parity.py` names TUI actions as **strings**; it does not import the TUI, so the Task 4 convention holds.

- [ ] **Step 1: Write the ledger module with everything pending**

Create `src/bookkit/web/parity.py`:

```python
"""What the web surface covers of the TUI's account actions, and what it does
not yet.

The destination is 1:1. Narrowing early slices is build order, not scope, and
this ledger is what stops the two from being confused: tests/test_web_parity.py
fails on any AccountScreen action that is in neither dict, so a new TUI feature
turns the suite red until its web equivalent is built or consciously deferred.

Keys are TUI action names (Binding.action, with any argument stripped)."""

from __future__ import annotations

# action name -> the web route that covers it
IMPLEMENTED: dict[str, str] = {}

# action name -> why it is not covered yet
PENDING: dict[str, str] = {}
```

- [ ] **Step 2: Write the ledger test**

Create `tests/test_web_parity.py`:

```python
"""Every account action is implemented on the web, or explicitly deferred.

Nothing may be silently missing: the gap has to be a number you can read.
"""

from __future__ import annotations

from bookkit.web.parity import IMPLEMENTED, PENDING


def _account_actions() -> set[str]:
    """Every action AccountScreen binds, with arguments stripped
    ("show_tab('tab-overview')" -> "show_tab") and screen-level navigation
    excluded — 'app.pop_screen' is the browser's back button."""
    from bookkit.tui.screens.account import AccountScreen

    actions: set[str] = set()
    for binding in AccountScreen.BINDINGS:
        action = getattr(binding, "action", None) or ""
        name = action.split("(")[0].strip()
        if not name or name.startswith("app."):
            continue
        actions.add(name)
    return actions


def test_every_account_action_is_implemented_or_explicitly_pending():
    actions = _account_actions()
    accounted = set(IMPLEMENTED) | set(PENDING)
    missing = actions - accounted
    assert not missing, (
        "AccountScreen actions in neither IMPLEMENTED nor PENDING: "
        f"{sorted(missing)} — add each to bookkit/web/parity.py, with a route "
        "if it is built or a one-line reason if it is not"
    )


def test_the_ledger_has_no_stale_entries():
    """An entry for an action the TUI no longer binds is a lie about coverage."""
    actions = _account_actions()
    stale = (set(IMPLEMENTED) | set(PENDING)) - actions
    assert not stale, f"ledger names actions AccountScreen no longer binds: {sorted(stale)}"


def test_an_action_is_not_both_implemented_and_pending():
    overlap = set(IMPLEMENTED) & set(PENDING)
    assert not overlap, f"both implemented and pending: {sorted(overlap)}"
```

- [ ] **Step 3: Run it to get the real action list**

```bash
uv run --no-sync python -m pytest tests/test_web_parity.py -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
```

Expected: `test_every_account_action_is_implemented_or_explicitly_pending` FAILS, and the assertion message prints the complete sorted list of action names.

- [ ] **Step 4: Fill `PENDING` from that list**

Copy every action name the failure printed into `PENDING` in `src/bookkit/web/parity.py`, each with a one-line reason. Use the reason to say *why*, not *that* — for example:

```python
PENDING: dict[str, str] = {
    "add_here": "slice 1 covers contacts and interactions only",
    "edit_here": "slice 1 covers contacts and interactions only",
    "renew_placement": "placements tab — stage 3, needs towerkit writes",
    "edit_layer": "placements tab — stage 3, needs towerkit writes",
    # ... one line per remaining action from the failure output
}
```

Do not invent names. Every key must come from the test's output.

- [ ] **Step 5: Run it and confirm it passes**

```bash
uv run --no-sync python -m pytest tests/test_web_parity.py -q > "$GATE/out.txt" 2>&1; tail -5 "$GATE/out.txt"
```

Expected: 3 passed.

- [ ] **Step 6: Verify the ledger test can fail**

```bash
uv run --no-sync python - <<'PY'
import re, pathlib
p = pathlib.Path("src/bookkit/web/parity.py")
text = p.read_text()
first = re.search(r'^\s+"(\w+)": ', text, re.M).group(1)
p.write_text(text.replace(f'"{first}": ', '"__removed__": ', 1))
print("removed", first)
PY
uv run --no-sync python -m pytest tests/test_web_parity.py -q > "$GATE/out.txt" 2>&1; tail -8 "$GATE/out.txt"
git checkout src/bookkit/web/parity.py
```

Expected: two failures — the missing action, and the now-stale `__removed__` entry. Both must be observed before this step is done.

- [ ] **Step 7: Commit**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
git add src/bookkit/web/parity.py tests/test_web_parity.py
git commit -m "web: parity ledger for the account screen

Every AccountScreen action is implemented on the web or explicitly deferred
with a reason. A new TUI binding turns this red until it is one or the other,
so the remaining distance to 1:1 stays a number rather than a memory."
```

---

### Task 6: The form macro, and the test that every `FormSpec` renders completely

Without this test an unhandled `Field.kind` renders nothing, and the form saves while silently dropping a field.

**Files:**
- Create: `src/bookkit/web/templates/macros/form.html`, `src/bookkit/web/forms_render.py`
- Test: `tests/test_web_form_render.py`

**Interfaces:**
- Consumes: `bookkit.forms.spec.{FormSpec, Field, PLACEHOLDERS, initial_text}`
- Produces: `bookkit.web.forms_render.render_form(request, spec, action, error=None, submitted=None) -> str` — returns the form's HTML fragment

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_form_render.py`:

```python
"""One FormSpec, two surfaces. The macro must render every field of every
builder — a kind it does not know renders nothing, and the form then saves
while dropping that field with no error anywhere."""

from __future__ import annotations

import inspect

import pytest

from bookkit.forms import entities
from bookkit.forms.spec import Field, FormSpec
from bookkit.web.forms_render import render_form


def _spec_builders():
    for name, fn in vars(entities).items():
        if name.endswith("_form") or name.endswith("_form_initial_profile"):
            if inspect.isfunction(fn):
                yield name, fn


def test_there_are_builders_to_check():
    """Guards the loop below: if the discovery breaks, the parametrised tests
    silently pass on an empty set."""
    assert len(list(_spec_builders())) >= 17


@pytest.mark.parametrize("kind", ["text", "textarea", "select", "date", "money", "int",
                                  "email", "phone", "url", "domain", "linkedin", "naics"])
def test_every_field_kind_renders_a_named_input(kind: str):
    spec = FormSpec("probe", [Field("probe_key", "probe label", kind,
                                    options=(("a", "a"),) if kind == "select" else ())])
    html = render_form(None, spec, action="/probe")
    assert 'name="probe_key"' in html, f"kind {kind!r} rendered no named input"
    assert "probe label" in html


def test_money_and_date_are_text_inputs():
    """type=number rejects '1,234.56' before the server sees it; type=date
    bypasses parse_human_date, whose job is to refuse a bare number."""
    spec = FormSpec("probe", [
        Field("premium", "premium", "money"),
        Field("due_on", "due", "date"),
    ])
    html = render_form(None, spec, action="/probe")
    assert 'type="number"' not in html
    assert 'type="date"' not in html


def test_required_fields_are_marked():
    spec = FormSpec("probe", [Field("name", "name", required=True)])
    html = render_form(None, spec, action="/probe")
    assert "required" in html


def test_submitted_values_win_over_initial():
    """A refused save re-renders with what the user typed, not what was there
    before — commit-in-place is the platform default."""
    spec = FormSpec("probe", [Field("name", "name")], initial={"name": "old"})
    html = render_form(None, spec, action="/probe", submitted={"name": "typed"})
    assert "typed" in html
    assert 'value="old"' not in html


def test_the_error_message_is_rendered():
    spec = FormSpec("probe", [Field("due_on", "due", "date")])
    html = render_form(None, spec, action="/probe", error="due: cannot read a date from '5'")
    assert "cannot read a date from" in html
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_form_render.py -q > "$GATE/out.txt" 2>&1; tail -5 "$GATE/out.txt"
```

Expected: `ModuleNotFoundError: No module named 'bookkit.web.forms_render'`.

- [ ] **Step 3: Write the macro**

Create `src/bookkit/web/templates/macros/form.html`:

```html
{% macro render_field(f, value, placeholder) %}
  <div class="field">
    <label for="f-{{ f.key }}">{{ f.label }}{% if f.required %} <span class="req">*</span>{% endif %}</label>
    {% if f.kind == "select" %}
      <select id="f-{{ f.key }}" name="{{ f.key }}"{% if f.required %} required{% endif %}>
        {% if f.optional_select or not f.required %}<option value=""></option>{% endif %}
        {% for label, val in f.options %}
          <option value="{{ val }}"{% if value == val %} selected{% endif %}>{{ label }}</option>
        {% endfor %}
      </select>
    {% elif f.kind == "textarea" %}
      <textarea id="f-{{ f.key }}" name="{{ f.key }}" rows="5"
                {% if f.required %}required{% endif %}>{{ value }}</textarea>
    {% else %}
      {# text for every remaining kind on purpose: type=number rejects
         "1,234.56" and type=date bypasses parse_human_date's refusal #}
      <input type="text" id="f-{{ f.key }}" name="{{ f.key }}"
             value="{{ value }}" placeholder="{{ placeholder }}"
             {% if f.suggestions %}list="l-{{ f.key }}"{% endif %}
             {% if f.required %}required{% endif %}>
      {% if f.suggestions %}
        <datalist id="l-{{ f.key }}">
          {% for s in f.suggestions %}<option value="{{ s }}"></option>{% endfor %}
        </datalist>
      {% endif %}
    {% endif %}
  </div>
{% endmacro %}

{% macro form(spec, action, rows, error) %}
  <form class="entity-form" method="post" action="{{ action }}"
        hx-post="{{ action }}" hx-target="closest .form-host" hx-swap="innerHTML">
    <h2 class="form-title">{{ spec.title }}</h2>
    {% if error %}<p class="form-error" role="alert">{{ error }}</p>{% endif %}
    {% for row in rows %}{{ render_field(row.field, row.value, row.placeholder) }}{% endfor %}
    <div class="form-actions">
      <button type="submit">Save</button>
    </div>
  </form>
{% endmacro %}
```

- [ ] **Step 4: Write the renderer**

Create `src/bookkit/web/forms_render.py`:

```python
"""Render any FormSpec to HTML.

One macro renders every form in bookkit.forms.entities, because they are all
the same dataclass. Adding a Field to a builder makes the input appear on both
surfaces — there is no second list to update."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..forms.spec import Field, FormSpec, PLACEHOLDERS, initial_text
from .app import TEMPLATES


@dataclass(frozen=True)
class _Row:
    field: Field
    value: str
    placeholder: str


def _rows(spec: FormSpec, submitted: dict[str, str] | None) -> list[_Row]:
    rows: list[_Row] = []
    for f in spec.fields:
        if submitted is not None:
            value = submitted.get(f.key, "")
        else:
            value = initial_text(f, spec.initial.get(f.key))
        rows.append(_Row(f, value, f.placeholder or PLACEHOLDERS.get(f.kind, "")))
    return rows


def render_form(
    request: Any,
    spec: FormSpec,
    action: str,
    error: str | None = None,
    submitted: dict[str, str] | None = None,
) -> str:
    """The form fragment. On a refused save pass `submitted` and `error` — the
    user's input is re-rendered exactly as typed, which is commit-in-place."""
    template = TEMPLATES.env.get_template("macros/form.html")
    module = template.make_module({})
    return str(module.form(spec, action, _rows(spec, submitted), error))
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
uv run --no-sync python -m pytest tests/test_web_form_render.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: all pass. If a `kind` fails, add a branch to the macro — do not weaken the test.

- [ ] **Step 6: Verify the kind test can fail**

Temporarily change the macro's final `{% else %}` branch to render nothing, run the parametrised test, confirm the twelve kind cases fail, then restore.

- [ ] **Step 7: Gates and commit**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add -A
git commit -m "web: one macro renders any FormSpec, asserted over every builder

Money and dates stay text inputs: type=number rejects 1,234.56 client-side and
type=date bypasses the bare-number refusal in parse_human_date."
```

---

### Task 7: The account page — header and Overview

**Files:**
- Create: `src/bookkit/web/templates/account/page.html`, `src/bookkit/web/templates/account/overview.html`
- Modify: `src/bookkit/web/routes/account.py`
- Test: `tests/test_web_account.py`

**Interfaces:**
- Consumes: `bookkit.repo.orgs.{find,get}`, `bookkit.repo.contacts.for_org(conn, org_id, active_only=True)`, `bookkit.repo.interactions.for_org(conn, org_id, limit=200)`, `bookkit.services.renewals.next_for_org(conn, org_id, today=None)`, `bookkit.repo.tasks`, `bookkit.repo.opportunities`, `bookkit.services.team`
- Produces: routes `GET /accounts/{ref}` (redirect), `GET /accounts/{ref}/overview`

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_account.py`:

```python
"""The account page. The renewal-date assertion is named after the bug: Today,
Book, the account header and the calendar all printed placement.period_to
beside a countdown computed from renewal_on, so a date twenty days in the
future rendered red as '70d over'."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs

    org = orgs.list_orgs(app.state.conn, kind="client")[0]
    with TestClient(app) as client:
        yield client, org


def test_account_root_redirects_to_overview(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"].endswith("/overview")


def test_overview_names_the_account(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/overview")
    assert response.status_code == 200
    assert org.name in response.text


def test_unknown_account_is_404(app_and_org):
    client, _ = app_and_org
    assert client.get("/accounts/nope-does-not-exist/overview").status_code == 404


def test_header_prints_the_date_it_counts_to(app_and_org):
    """THE RENEWAL DATE IS RenewalItem.renewal_on, never placement.period_to.
    Print the same date you count to, or a future date renders as overdue."""
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    if item is None:
        pytest.skip("seeded account has no live renewal")
    response = client.get(f"/accounts/{org.ref}/overview")
    assert item.renewal_on in response.text
    assert item.placement.period_to not in response.text or \
        item.placement.period_to == item.renewal_on


def test_overdue_is_decided_by_days_remaining(app_and_org):
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    if item is None:
        pytest.skip("seeded account has no live renewal")
    response = client.get(f"/accounts/{org.ref}/overview")
    assert ("is-overdue" in response.text) == (item.days_remaining < 0)


def test_overview_shows_the_five_sections(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/overview")
    for heading in ("Team", "Key contacts", "Recent interactions",
                    "Open tasks", "Open opportunities"):
        assert heading in response.text, f"missing section: {heading}"
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_account.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: every test fails with 404, because no routes exist yet.

- [ ] **Step 3: Write the routes**

Replace `src/bookkit/web/routes/account.py`:

```python
"""Account page routes. No SQL here — reads go through repo/ and services/."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...models import Org
from ...repo import contacts as contacts_repo
from ...repo import interactions as interactions_repo
from ...repo import opportunities as opportunities_repo
from ...repo import orgs as orgs_repo
from ...repo import tasks as tasks_repo
from ...services import renewals
from ..app import TEMPLATES

router = APIRouter()


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn  # type: ignore[no-any-return]


def _org(request: Request, ref: str) -> Org:
    org = orgs_repo.find(_conn(request), ref)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no account matches {ref!r}")
    return org


def _header(conn: sqlite3.Connection, org: Org) -> dict[str, Any]:
    """The renewal shown is RenewalItem.renewal_on — the date days_remaining
    counts to. Printing placement.period_to beside that countdown is the bug
    that made a future date render as '70d over' on four surfaces."""
    item = renewals.next_for_org(conn, org.id)
    if item is None:
        # No rail at all when there is no live renewal — an empty rail would
        # imply a clock that is not running.
        return {"org": org, "renewal_on": None, "days_remaining": None,
                "overdue": False, "lines": "", "bucket": None, "rail_pct": None}
    overdue = item.days_remaining < 0
    return {
        "org": org,
        "renewal_on": item.renewal_on,
        "days_remaining": item.days_remaining,
        "overdue": overdue,
        "lines": item.lines,
        "bucket": item.bucket,
        # Position along the 120-day rail. Overdue pins to the left overrun and
        # is never expressed as a position — overdue is decided by
        # days_remaining < 0, never by where a marker lands.
        "rail_pct": None if overdue else min(100.0, max(0.0, item.days_remaining / 120 * 100)),
    }


@router.get("/accounts/{ref}")
def account_root(ref: str) -> RedirectResponse:
    return RedirectResponse(url=f"/accounts/{ref}/overview", status_code=307)


@router.get("/accounts/{ref}/overview", response_class=HTMLResponse)
def overview(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    return TEMPLATES.TemplateResponse(
        request,
        "account/overview.html",
        {
            "header": _header(conn, org),
            "tab": "overview",
            "contacts": contacts_repo.for_org(conn, org.id)[:8],
            "interactions": interactions_repo.for_org(conn, org.id, limit=8),
            "tasks": tasks_repo.open_tasks_for_client(conn, org.id),
            "opportunities": opportunities_repo.for_org(conn, org.id, open_only=True),
            "team": [],
        },
    )
```

These names are verified against the repo as of this plan: `tasks.open_tasks_for_client(conn, org_id)` (**not** `open_for_org`), `opportunities.for_org(conn, org_id, open_only=False)`, `contacts.for_org(conn, org_id, active_only=True)`, `interactions.for_org(conn, org_id, limit=200)`. If any has drifted, use the real one — never add a query to `web/`.

- [ ] **Step 4: Write the templates**

Create `src/bookkit/web/templates/account/page.html`:

```html
{% extends "base.html" %}
{% block title %}{{ header.org.name }} — bookkit{% endblock %}
{% block content %}
  <header class="account-header{% if header.overdue %} is-overdue{% endif %}">
    <h1>{{ header.org.name }}</h1>
    <span class="status-pill">{{ header.org.status }}</span>
    {% if header.renewal_on %}{% include "account/_renewal_rail.html" %}{% endif %}
  </header>
  <nav class="tabs">
    <a href="/accounts/{{ header.org.ref }}/overview"
       class="{% if tab == 'overview' %}is-current{% endif %}">Overview</a>
    <a href="/accounts/{{ header.org.ref }}/contacts"
       class="{% if tab == 'contacts' %}is-current{% endif %}">Contacts</a>
    <a href="/accounts/{{ header.org.ref }}/interactions"
       class="{% if tab == 'interactions' %}is-current{% endif %}">Interactions</a>
  </nav>
  <div class="tab-panel">{% block panel %}{% endblock %}</div>
{% endblock %}
```

Create `src/bookkit/web/templates/account/_renewal_rail.html` — the signature element from the visual direction. The date is printed **at the marker**, sourced from the same object as the count beside it, which is what makes the four-surface bug hard to draw:

```html
<div class="rail{% if header.overdue %} is-overdue{% endif %}">
  <div class="rail-track" role="img"
       aria-label="renews {{ header.renewal_on }},
         {% if header.overdue %}{{ -header.days_remaining }} days overdue
         {% else %}in {{ header.days_remaining }} days{% endif %}">
    {% if header.overdue %}
      <span class="rail-overrun">over</span>
    {% else %}
      <span class="rail-marker" style="left: {{ '%.1f'|format(header.rail_pct) }}%"></span>
    {% endif %}
    <span class="rail-tick" style="left: 25%"></span>
    <span class="rail-tick" style="left: 50%"></span>
    <span class="rail-tick" style="left: 75%"></span>
  </div>
  <div class="rail-scale">
    <span>overdue</span><span>0–30</span><span>31–60</span><span>61–90</span><span>91–120</span>
  </div>
  <p class="rail-date">renews <time datetime="{{ header.renewal_on }}">{{ header.renewal_on }}</time></p>
  <div class="rail-count">
    <span class="rail-days">{% if header.overdue %}{{ -header.days_remaining }}{% else %}{{ header.days_remaining }}{% endif %}</span>
    <span class="rail-unit">{% if header.overdue %}days over{% else %}days{% endif %}</span>
  </div>
  {% if header.lines %}<p class="rail-lines">{{ header.lines }}</p>{% endif %}
</div>
```

Create `src/bookkit/web/templates/account/overview.html`:

```html
{% extends "account/page.html" %}
{% block panel %}
  <section class="card"><h2>Team</h2>
    {% if team %}<ul>{% for m in team %}<li>{{ m }}</li>{% endfor %}</ul>
    {% else %}<p class="empty">No one assigned.</p>{% endif %}
  </section>
  <section class="card"><h2>Key contacts</h2>
    {% if contacts %}<ul>{% for c in contacts %}
      <li>{{ c.first_name }} {{ c.last_name }}{% if c.title %} — {{ c.title }}{% endif %}</li>
    {% endfor %}</ul>{% else %}<p class="empty">No contacts yet.</p>{% endif %}
  </section>
  <section class="card"><h2>Recent interactions</h2>
    {% if interactions %}<ul>{% for i in interactions %}
      <li><time>{{ i.occurred_on }}</time> {{ i.subject }}</li>
    {% endfor %}</ul>{% else %}<p class="empty">Nothing logged yet.</p>{% endif %}
  </section>
  <section class="card"><h2>Open tasks</h2>
    {% if tasks %}<ul>{% for t in tasks %}
      <li>{{ t.title }}{% if t.due_on %} <time>{{ t.due_on }}</time>{% endif %}</li>
    {% endfor %}</ul>{% else %}<p class="empty">Nothing open.</p>{% endif %}
  </section>
  <section class="card"><h2>Open opportunities</h2>
    {% if opportunities %}<ul>{% for o in opportunities %}
      <li>{{ o.title }} — {{ o.stage }}</li>
    {% endfor %}</ul>{% else %}<p class="empty">Nothing in the pipeline.</p>{% endif %}
  </section>
{% endblock %}
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
uv run --no-sync python -m pytest tests/test_web_account.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: all pass, with at most the two renewal tests skipped if the seeded account has no live renewal. **If they skip, pick an account that does have one** — a skipped renewal test protects nothing. Find one with:

```bash
uv run --no-sync python -c "
from bookkit import db, seed, sync
" ; uv run --no-sync python -m pytest tests/test_web_account.py -q -rs > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

If skipped, change the fixture to select the first org for which `renewals.next_for_org` is not None, and assert that at least one exists.

- [ ] **Step 6: Verify the renewal test can fail**

In `_header`, temporarily return `item.placement.period_to` as `renewal_on`. Run `tests/test_web_account.py -k renewal`. It must fail whenever `period_to != renewal_on` in the seed. If it passes, the seeded account has them equal and the test is worthless — pick an account where they differ, then restore `_header`.

- [ ] **Step 7: Gates and commit**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add -A
git commit -m "web: account header and Overview

The header prints the date it counts to (RenewalItem.renewal_on) and decides
overdue by days_remaining < 0, with a test named after the bug that shipped
that pairing wrong on four surfaces."
```

---

### Task 8: Contacts — read, add, and edit through the shared apply, in one batch

**Files:**
- Create: `src/bookkit/web/templates/account/contacts.html`, `src/bookkit/web/templates/account/_contacts_panel.html`
- Modify: `src/bookkit/web/routes/account.py`, `src/bookkit/web/parity.py`
- Test: `tests/test_web_writes.py`

**Interfaces:**
- Consumes: `bookkit.forms.entities.{contact_form, apply_contact}`, `bookkit.forms.spec.{BatchSpec, FieldError, parse_values}`, `bookkit.services.batches.open_batch`, `bookkit.web.forms_render.render_form`
- Produces: routes `GET/POST /accounts/{ref}/contacts/new`, `GET/POST /accounts/{ref}/contacts/{contact_id}/edit`, `GET /accounts/{ref}/contacts`

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_writes.py`:

```python
"""Web writes are batched writes.

The assertion is deliberately NOT 'the field changed'. A plain outcome check
passes even when the route writes outside a batch — which is exactly how 33
FormModal call sites bypassed the batched push_form seam while the suite
stayed green. What is asserted is that the batch exists, that it carries
source='web', and that reverting it puts the record back."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs

    org = orgs.list_orgs(app.state.conn, kind="client")[0]
    with TestClient(app) as client:
        yield client, org


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, limit=1)
    return found[0] if found else None


def test_editing_a_contact_writes_one_web_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    contact = contacts_repo.for_org(conn, org.id)[0]
    before = contact.title

    response = client.post(
        f"/accounts/{org.ref}/contacts/{contact.id}/edit",
        data={"first_name": contact.first_name, "last_name": contact.last_name,
              "email": contact.email or "", "phone": contact.phone or "",
              "mobile": contact.mobile or "", "title": "Head of Risk",
              "role": contact.role or "", "linkedin": contact.linkedin or "",
              "notes": contact.notes or ""},
    )
    assert response.status_code == 200

    assert contacts_repo.get(conn, contact.id).title == "Head of Risk"

    batch = _latest_batch(conn)
    assert batch is not None, "the edit wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"
    assert batch.tool == "edit_contact"


def test_the_web_batch_reverts(app_and_org):
    """One writer action, one undo unit — on every surface."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    contact = contacts_repo.for_org(conn, org.id)[0]
    before = contact.title

    client.post(
        f"/accounts/{org.ref}/contacts/{contact.id}/edit",
        data={"first_name": contact.first_name, "last_name": contact.last_name,
              "email": contact.email or "", "phone": contact.phone or "",
              "mobile": contact.mobile or "", "title": "Interim CFO",
              "role": contact.role or "", "linkedin": contact.linkedin or "",
              "notes": contact.notes or ""},
    )
    batch = _latest_batch(conn)
    batches_svc.revert(conn, batch)
    assert contacts_repo.get(conn, contact.id).title == before


def test_adding_a_contact_writes_one_web_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import contacts as contacts_repo

    before = len(contacts_repo.for_org(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "Dana", "last_name": "Okafor", "email": "DANA@EXAMPLE.COM",
              "phone": "", "mobile": "", "title": "", "role": "", "linkedin": "",
              "notes": ""},
    )
    assert response.status_code == 200
    after = contacts_repo.for_org(conn, org.id)
    assert len(after) == before + 1

    created = [c for c in after if c.last_name == "Okafor"][0]
    assert created.email == "dana@example.com", "the shared cleaner did not run"

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"
```

- [ ] **Step 2: Confirm the batch listing helper**

```bash
grep -n "^def recent" src/bookkit/repo/batches.py
```

Expected: `def recent(` at roughly line 53 — verified as of this plan. Check its signature accepts `limit`; if it has drifted, fix `_latest_batch` in the test. Do not add SQL to the test.

- [ ] **Step 3: Run the test and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_writes.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: 404s — the routes do not exist.

- [ ] **Step 4: Write the write path**

Add to `src/bookkit/web/routes/account.py`:

```python
from ...forms.entities import apply_contact, contact_form
from ...forms.spec import BatchSpec, FieldError, FormSpec, parse_values
from ...repo import contacts as contacts_repo
from ...services import batches as batches_svc
from ..forms_render import render_form


def _save(
    request: Request,
    org: Org,
    spec: FormSpec,
    action: str,
    raw: dict[str, str],
    write: Any,
) -> HTMLResponse | None:
    """Parse, then run `write` inside ONE batch. Returns a re-rendered form
    fragment on refusal (input intact, nothing written), or None on success.

    The exception propagates out of open_batch so the transaction rolls back:
    a refused save leaves nothing behind and costs nothing retyped."""
    try:
        values = parse_values(spec, raw)
    except FieldError as exc:
        return HTMLResponse(render_form(request, spec, action, exc.message, raw))

    batch = BatchSpec.for_title(spec.title, org_id=org.id)
    try:
        with batches_svc.open_batch(
            _conn(request), source="web", tool=batch.tool,
            summary=batch.sentence(values), org_id=org.id,
        ):
            write(values)
    except Exception as exc:  # a refused save is a message, never a 500
        return HTMLResponse(render_form(request, spec, action, str(exc), raw))
    return None


@router.get("/accounts/{ref}/contacts", response_class=HTMLResponse)
def contacts_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    return TEMPLATES.TemplateResponse(
        request, "account/contacts.html",
        {"header": _header(conn, org), "tab": "contacts",
         "contacts": contacts_repo.for_org(conn, org.id)},
    )


def _contacts_panel(request: Request, org: Org) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_contacts_panel.html",
        {"header": {"org": org}, "contacts": contacts_repo.for_org(_conn(request), org.id)},
    )


@router.get("/accounts/{ref}/contacts/new", response_class=HTMLResponse)
def contact_new_form(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    spec = contact_form()
    return HTMLResponse(render_form(request, spec, f"/accounts/{ref}/contacts/new"))


@router.post("/accounts/{ref}/contacts/new", response_class=HTMLResponse)
async def contact_create(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    spec = contact_form()
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/contacts/new"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_contact(_conn(request), org.id, values),
    )
    return refused or _contacts_panel(request, org)


@router.get("/accounts/{ref}/contacts/{contact_id}/edit", response_class=HTMLResponse)
def contact_edit_form(request: Request, ref: str, contact_id: str) -> HTMLResponse:
    _org(request, ref)
    existing = contacts_repo.get(_conn(request), contact_id)
    spec = contact_form(existing)
    action = f"/accounts/{ref}/contacts/{contact_id}/edit"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/contacts/{contact_id}/edit", response_class=HTMLResponse)
async def contact_update(request: Request, ref: str, contact_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = contacts_repo.get(conn, contact_id)
    spec = contact_form(existing)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/contacts/{contact_id}/edit"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_contact(conn, org.id, values, existing),
    )
    return refused or _contacts_panel(request, org)
```

- [ ] **Step 5: Write the templates**

Create `src/bookkit/web/templates/account/_contacts_panel.html`:

```html
<div class="form-host"></div>
<table class="rows">
  <thead><tr><th>Name</th><th>Title</th><th>Email</th><th>Phone</th><th></th></tr></thead>
  <tbody>
    {% for c in contacts %}
      <tr>
        <td>{{ c.first_name }} {{ c.last_name }}</td>
        <td>{{ c.title or "" }}</td>
        <td>{{ c.email or "" }}</td>
        <td>{{ c.phone or "" }}</td>
        <td><button hx-get="/accounts/{{ header.org.ref }}/contacts/{{ c.id }}/edit"
                    hx-target="previous .form-host" hx-swap="innerHTML">Edit</button></td>
      </tr>
    {% else %}
      <tr><td colspan="5" class="empty">No contacts yet.</td></tr>
    {% endfor %}
  </tbody>
</table>
<button hx-get="/accounts/{{ header.org.ref }}/contacts/new"
        hx-target="previous .form-host" hx-swap="innerHTML">Add contact</button>
```

Create `src/bookkit/web/templates/account/contacts.html`:

```html
{% extends "account/page.html" %}
{% block panel %}
  <div id="contacts-panel">
    {% include "account/_contacts_panel.html" %}
  </div>
{% endblock %}
```

- [ ] **Step 6: Run the test and confirm it passes**

```bash
uv run --no-sync python -m pytest tests/test_web_writes.py -q > "$GATE/out.txt" 2>&1; tail -15 "$GATE/out.txt"
```

Expected: 3 passed.

- [ ] **Step 7: Verify the batch assertion can fail**

In `_save`, temporarily call `write(values)` **outside** the `open_batch` block. Run `tests/test_web_writes.py`. `test_editing_a_contact_writes_one_web_batch` must fail on the "wrote outside any batch" assert, and `test_the_web_batch_reverts` must fail too. Restore. Without seeing this, the seam is not proven.

- [ ] **Step 8: Flip the ledger entries**

In `src/bookkit/web/parity.py`, move the contact-related actions from `PENDING` to `IMPLEMENTED` with their route paths. Use only action names already present in the file.

- [ ] **Step 9: Gates and commit**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add -A
git commit -m "web: contacts read, add, and edit through the shared apply

One writer action, one undo unit, source='web'. The tests assert the batch and
its revert rather than the changed field — an outcome assertion passes even
when the route writes outside a batch."
```

---

### Task 9: The refusal contract

**Files:**
- Test: `tests/test_web_writes.py` (append)
- Modify: `src/bookkit/web/routes/account.py` only if a test fails

**Interfaces:**
- Consumes: everything from Task 8. No new production surface expected.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_writes.py`:

```python
def test_a_refused_save_keeps_every_value_and_writes_nothing(app_and_org):
    """Commit-in-place: the form comes back with the input intact and the
    error, and the transaction rolled back — a refused save leaves nothing
    behind and costs nothing retyped."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import contacts as contacts_repo

    before_count = len(contacts_repo.for_org(conn, org.id))
    before_batches = len(batches_repo.recent(conn, limit=50))

    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "", "last_name": "Okafor", "email": "dana@example.com",
              "phone": "", "mobile": "", "title": "Head of Risk", "role": "",
              "linkedin": "", "notes": "call back Tuesday"},
    )

    assert response.status_code == 200
    assert "required" in response.text
    # every other value survives the refusal
    assert "Okafor" in response.text
    assert "dana@example.com" in response.text
    assert "Head of Risk" in response.text
    assert "call back Tuesday" in response.text
    # and nothing was written
    assert len(contacts_repo.for_org(conn, org.id)) == before_count
    assert len(batches_repo.recent(conn, limit=50)) == before_batches
```

The date-refusal test belongs to Task 10, not here: it exercises the interactions
route, which does not exist yet, and committing it now would leave the suite red
against this plan's own gating constraint.

- [ ] **Step 2: Run the test and confirm behaviour**

```bash
uv run --no-sync python -m pytest tests/test_web_writes.py -q > "$GATE/out.txt" 2>&1; tail -15 "$GATE/out.txt"
```

Expected: all four tests pass — three from Task 8 plus this one — if Task 8's `_save` is correct. If it fails on a missing value, the renderer is not receiving `raw` as `submitted`; fix `_save`, not the test.

- [ ] **Step 3: Verify the "nothing was written" assertion can fail**

Temporarily make `_save` swallow the `FieldError` and write anyway. Confirm the test fails on the count assertions. Restore.

- [ ] **Step 4: Full gates and commit**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add tests/test_web_writes.py
git commit -m "web: assert the refusal contract — input intact, nothing written

A refused save re-renders the form with every value the user typed and rolls
the transaction back, so neither a row nor a batch survives it."
```

---

### Task 10: Interactions — read, edit, and delete with confirmation

**Files:**
- Create: `src/bookkit/web/templates/account/interactions.html`, `src/bookkit/web/templates/account/_interactions_panel.html`, `src/bookkit/web/templates/account/_confirm.html`
- Modify: `src/bookkit/web/routes/account.py`, `src/bookkit/web/parity.py`
- Test: `tests/test_web_writes.py` (append)

**Interfaces:**
- Consumes: `bookkit.forms.entities.{interaction_form, apply_interaction}`, `bookkit.repo.interactions.{for_org, get, update, delete}`
- Produces: routes `GET /accounts/{ref}/interactions`, `GET/POST /accounts/{ref}/interactions/{id}/edit`, `GET/POST /accounts/{ref}/interactions/{id}/delete`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_writes.py`:

```python
def test_a_bare_number_is_refused_as_a_date_on_the_web_too(app_and_org):
    """dateparser reads '5' as a MONTH and future-biases it. The refusal lives
    in parse_human_date, and the web reaches it through the same parser the
    TUI uses — not a second validator."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import interactions as interactions_repo

    interaction = interactions_repo.for_org(conn, org.id, limit=1)[0]
    response = client.post(
        f"/accounts/{org.ref}/interactions/{interaction.id}/edit",
        data={"occurred_on": "5", "type": interaction.type,
              "subject": interaction.subject, "body": interaction.body or ""},
    )
    assert response.status_code == 200
    assert "cannot read a date" in response.text
    assert interactions_repo.get(conn, interaction.id).occurred_on == interaction.occurred_on


def test_interactions_tab_shows_the_note_body(app_and_org):
    """The body was stored and never shown anywhere before review F33 — the
    log is the index, the note is the point."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import interactions as interactions_repo

    logged = [i for i in interactions_repo.for_org(conn, org.id) if i.body]
    if not logged:
        pytest.skip("seed has no interaction with a body")
    response = client.get(f"/accounts/{org.ref}/interactions")
    assert logged[0].body[:40] in response.text


def test_deleting_an_interaction_is_confirmed_then_batched(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import interactions as interactions_repo

    target = interactions_repo.for_org(conn, org.id)[0]

    confirm = client.get(f"/accounts/{org.ref}/interactions/{target.id}/delete")
    assert confirm.status_code == 200
    assert "Delete" in confirm.text
    # a GET must never destroy anything
    assert interactions_repo.get(conn, target.id) is not None

    response = client.post(f"/accounts/{org.ref}/interactions/{target.id}/delete")
    assert response.status_code == 200

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"
    assert batch.tool == "delete_interaction"
```

- [ ] **Step 2: Run and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_writes.py -q > "$GATE/out.txt" 2>&1; tail -15 "$GATE/out.txt"
```

Expected: the three interaction tests fail with 404.

- [ ] **Step 3: Add the routes**

Add to `src/bookkit/web/routes/account.py`:

```python
from ...forms.entities import apply_interaction, interaction_form


@router.get("/accounts/{ref}/interactions", response_class=HTMLResponse)
def interactions_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    return TEMPLATES.TemplateResponse(
        request, "account/interactions.html",
        {"header": _header(conn, org), "tab": "interactions",
         "interactions": interactions_repo.for_org(conn, org.id)},
    )


def _interactions_panel(request: Request, org: Org) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_interactions_panel.html",
        {"header": {"org": org},
         "interactions": interactions_repo.for_org(_conn(request), org.id)},
    )


@router.get("/accounts/{ref}/interactions/{item_id}/edit", response_class=HTMLResponse)
def interaction_edit_form(request: Request, ref: str, item_id: str) -> HTMLResponse:
    _org(request, ref)
    existing = interactions_repo.get(_conn(request), item_id)
    action = f"/accounts/{ref}/interactions/{item_id}/edit"
    return HTMLResponse(render_form(request, interaction_form(existing), action))


@router.post("/accounts/{ref}/interactions/{item_id}/edit", response_class=HTMLResponse)
async def interaction_update(request: Request, ref: str, item_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = interactions_repo.get(conn, item_id)
    spec = interaction_form(existing)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/interactions/{item_id}/edit"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_interaction(conn, values, existing),
    )
    return refused or _interactions_panel(request, org)


@router.get("/accounts/{ref}/interactions/{item_id}/delete", response_class=HTMLResponse)
def interaction_delete_confirm(request: Request, ref: str, item_id: str) -> HTMLResponse:
    """A server-rendered confirm step, not a JavaScript confirm() — so it stays
    testable and the POST that follows lands inside a batch."""
    _org(request, ref)
    existing = interactions_repo.get(_conn(request), item_id)
    return TEMPLATES.TemplateResponse(
        request, "account/_confirm.html",
        {"title": "Delete interaction",
         "detail": f"{existing.occurred_on} — {existing.subject}",
         "action": f"/accounts/{ref}/interactions/{item_id}/delete"},
    )


@router.post("/accounts/{ref}/interactions/{item_id}/delete", response_class=HTMLResponse)
def interaction_delete(request: Request, ref: str, item_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = interactions_repo.get(conn, item_id)
    with batches_svc.open_batch(
        conn, source="web", tool="delete_interaction",
        summary=f"deleted interaction {existing.subject}", org_id=org.id,
    ):
        interactions_repo.delete(conn, item_id)
    return _interactions_panel(request, org)
```

- [ ] **Step 4: Write the templates**

Create `src/bookkit/web/templates/account/_confirm.html`:

```html
<form class="confirm" method="post" action="{{ action }}"
      hx-post="{{ action }}" hx-target="closest .form-host" hx-swap="innerHTML">
  <h2>{{ title }}</h2>
  <p>{{ detail }}</p>
  <div class="form-actions">
    <button type="submit" class="danger">Delete</button>
    <button type="button" onclick="this.closest('.form-host').innerHTML=''">Cancel</button>
  </div>
</form>
```

Create `src/bookkit/web/templates/account/_interactions_panel.html`:

```html
<div class="form-host"></div>
<ul class="log">
  {% for i in interactions %}
    <li>
      <div class="log-head">
        <time>{{ i.occurred_on }}</time>
        <span class="log-type">{{ i.type }}</span>
        <span class="log-subject">{{ i.subject }}</span>
      </div>
      {% if i.body %}<p class="log-body">{{ i.body }}</p>{% endif %}
      <div class="log-actions">
        <button hx-get="/accounts/{{ header.org.ref }}/interactions/{{ i.id }}/edit"
                hx-target="closest .log" hx-swap="beforebegin">Edit</button>
        <button hx-get="/accounts/{{ header.org.ref }}/interactions/{{ i.id }}/delete"
                hx-target="closest .log" hx-swap="beforebegin">Delete</button>
      </div>
    </li>
  {% else %}
    <li class="empty">Nothing logged yet.</li>
  {% endfor %}
</ul>
```

Note: both buttons target `beforebegin` of the list, which lands the fragment in the `.form-host` div above it. If that proves fiddly, change `hx-target` to `"previous .form-host"` and `hx-swap="innerHTML"` — matching the contacts panel.

Create `src/bookkit/web/templates/account/interactions.html`:

```html
{% extends "account/page.html" %}
{% block panel %}
  <div id="interactions-panel">
    {% include "account/_interactions_panel.html" %}
  </div>
{% endblock %}
```

- [ ] **Step 5: Run the whole write suite**

```bash
uv run --no-sync python -m pytest tests/test_web_writes.py -q > "$GATE/out.txt" 2>&1; tail -15 "$GATE/out.txt"
```

Expected: all pass, including `test_a_bare_number_is_refused_as_a_date_on_the_web_too` from Task 9.

- [ ] **Step 6: Verify the delete confirmation can fail**

Temporarily make the `GET .../delete` route perform the delete. Confirm `test_deleting_an_interaction_is_confirmed_then_batched` fails on the "a GET must never destroy anything" assertion. Restore.

- [ ] **Step 7: Flip the ledger entries and commit**

Move the interaction actions from `PENDING` to `IMPLEMENTED` in `src/bookkit/web/parity.py`.

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add -A
git commit -m "web: interactions read, edit, and delete behind a server confirm

The note body is shown, not just the log line. Delete is a POST behind a
rendered confirm step, inside a batch, so R can put it back."
```

---

### Task 11: Requests — the chase list, create, and edit

Fast-tracked into slice 1 at Grant's request (2026-08-17). A request is an ask you are waiting on an answer to; the tab is master/detail over `RfiRequest` → `RfiItem`. This task builds the request level only.

**Files:**
- Create: `src/bookkit/web/templates/account/requests.html`, `src/bookkit/web/templates/account/_requests_panel.html`
- Modify: `src/bookkit/web/routes/account.py`, `src/bookkit/web/parity.py`, `src/bookkit/web/templates/account/page.html` (add the tab link)
- Test: `tests/test_web_requests.py`

**Interfaces:**
- Consumes: `bookkit.forms.entities.{request_form, apply_request}`, `bookkit.repo.rfi.{requests_for_org, get_request}`, `bookkit.services.rfi.{is_open, scope_label, asker_name}`, the `_save` helper from Task 8
- Produces: routes `GET /accounts/{ref}/requests`, `GET/POST /accounts/{ref}/requests/new`, `GET/POST /accounts/{ref}/requests/{request_id}/edit`

**Exact signatures — these differ from the contact/interaction pattern, do not guess:**

```python
request_form(existing: RfiRequest | None = None, *, conn=None, org_id=None) -> FormSpec
apply_request(conn, values: dict, org_id: str, existing: RfiRequest | None = None) -> RfiRequest
rfi_repo.requests_for_org(conn, org_id) -> list[RfiRequest]
rfi_svc.is_open(conn, request_id) -> bool
rfi_svc.scope_label(conn, request) -> str
rfi_svc.asker_name(conn, request) -> str
```

**`request_form` must always be called with both `conn=` and `org_id=`.** Without them the market, placement, and project select options come back empty, and the form's own dead-FK guard then blanks a live scope rather than a dead one. Read the comment at `request_form` before wiring it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_requests.py`:

```python
"""Requests — what you are waiting on, and who owes it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, rfi as rfi_repo

    org = next(
        (o for o in orgs.list_orgs(app.state.conn, kind="client")
         if rfi_repo.requests_for_org(app.state.conn, o.id)),
        None,
    )
    assert org is not None, "seed has no client with information requests"
    with TestClient(app) as client:
        yield client, org


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, limit=1)
    return found[0] if found else None


def test_requests_tab_lists_the_asks(app_and_org):
    client, org = app_and_org
    from bookkit.repo import rfi as rfi_repo

    requests = rfi_repo.requests_for_org(client.app.state.conn, org.id)
    response = client.get(f"/accounts/{org.ref}/requests")
    assert response.status_code == 200
    assert requests[0].title in response.text


def test_requests_tab_says_who_was_asked_and_what_it_is_about(app_and_org):
    """A request with no asker and no scope is an ask you cannot chase."""
    client, org = app_and_org
    from bookkit.repo import rfi as rfi_repo
    from bookkit.services import rfi as rfi_svc

    conn = client.app.state.conn
    request = rfi_repo.requests_for_org(conn, org.id)[0]
    response = client.get(f"/accounts/{org.ref}/requests")
    assert rfi_svc.scope_label(conn, request) in response.text
    assert rfi_svc.asker_name(conn, request) in response.text


def test_editing_a_request_writes_one_web_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    request = rfi_repo.requests_for_org(conn, org.id)[0]
    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/edit",
        data={"title": "Sompo — property questions",
              "requested_on": request.requested_on,
              "due_on": request.due_on or "", "market_org_id": request.market_org_id or "",
              "placement_id": request.placement_id or "",
              "project_id": request.project_id or "",
              "cancelled_at": request.cancelled_at or "",
              "notes": request.notes or ""},
    )
    assert response.status_code == 200
    assert rfi_repo.get_request(conn, request.id).title == "Sompo — property questions"

    batch = _latest_batch(conn)
    assert batch is not None, "the edit wrote outside any batch — `R` cannot reach it"
    assert batch.source == "web"


def test_creating_a_request_writes_one_web_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    before = len(rfi_repo.requests_for_org(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/requests/new",
        data={"title": "Loss run refresh", "requested_on": "2026-08-14",
              "due_on": "", "market_org_id": "", "placement_id": "",
              "project_id": "", "cancelled_at": "", "notes": ""},
    )
    assert response.status_code == 200
    assert len(rfi_repo.requests_for_org(conn, org.id)) == before + 1

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"


def test_a_request_with_a_bad_date_is_refused_intact(app_and_org):
    """A bare number is not a date, on every surface."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    before = len(rfi_repo.requests_for_org(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/requests/new",
        data={"title": "Loss run refresh", "requested_on": "5",
              "due_on": "", "market_org_id": "", "placement_id": "",
              "project_id": "", "cancelled_at": "", "notes": "chase Friday"},
    )
    assert response.status_code == 200
    assert "cannot read a date" in response.text
    assert "Loss run refresh" in response.text
    assert "chase Friday" in response.text
    assert len(rfi_repo.requests_for_org(conn, org.id)) == before
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_requests.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: 404s on every test.

- [ ] **Step 3: Add the routes**

Add to `src/bookkit/web/routes/account.py`:

```python
from ...forms.entities import apply_request, request_form
from ...repo import rfi as rfi_repo
from ...services import rfi as rfi_svc


def _request_rows(conn: sqlite3.Connection, org: Org) -> list[dict[str, Any]]:
    """One row per ask: what it is, who owes it, what it is about, and whether
    it is still open. A request with no items reads open by convention — it is
    an ask you have not written down yet, not a finished one."""
    rows = []
    for request in rfi_repo.requests_for_org(conn, org.id):
        rows.append({
            "request": request,
            "asker": rfi_svc.asker_name(conn, request),
            "scope": rfi_svc.scope_label(conn, request),
            "open": rfi_svc.is_open(conn, request.id),
            "open_count": rfi_repo.open_item_count(conn, request.id),
            "total_count": rfi_repo.item_count(conn, request.id),
        })
    return rows


@router.get("/accounts/{ref}/requests", response_class=HTMLResponse)
def requests_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    return TEMPLATES.TemplateResponse(
        request, "account/requests.html",
        {"header": _header(conn, org), "tab": "requests",
         "rows": _request_rows(conn, org)},
    )


def _requests_panel(request: Request, org: Org) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_requests_panel.html",
        {"header": {"org": org}, "rows": _request_rows(_conn(request), org)},
    )


@router.get("/accounts/{ref}/requests/new", response_class=HTMLResponse)
def request_new_form(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    spec = request_form(conn=_conn(request), org_id=org.id)
    return HTMLResponse(render_form(request, spec, f"/accounts/{ref}/requests/new"))


@router.post("/accounts/{ref}/requests/new", response_class=HTMLResponse)
async def request_create(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    spec = request_form(conn=conn, org_id=org.id)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/requests/new"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_request(conn, values, org.id),
    )
    return refused or _requests_panel(request, org)


@router.get("/accounts/{ref}/requests/{request_id}/edit", response_class=HTMLResponse)
def request_edit_form(request: Request, ref: str, request_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = rfi_repo.get_request(conn, request_id)
    spec = request_form(existing, conn=conn, org_id=org.id)
    action = f"/accounts/{ref}/requests/{request_id}/edit"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/requests/{request_id}/edit", response_class=HTMLResponse)
async def request_update(request: Request, ref: str, request_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = rfi_repo.get_request(conn, request_id)
    spec = request_form(existing, conn=conn, org_id=org.id)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/requests/{request_id}/edit"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_request(conn, values, org.id, existing),
    )
    return refused or _requests_panel(request, org)
```

- [ ] **Step 4: Write the templates**

Add a Requests link to `src/bookkit/web/templates/account/page.html`'s `<nav class="tabs">`, following the existing pattern exactly.

Create `src/bookkit/web/templates/account/_requests_panel.html`. Per the visual direction: mono for dates and counts, sans for titles and names, and the open/closed state carries a word, never colour alone.

```html
<div class="form-host"></div>
<div class="scroller">
  <table class="rows">
    <thead>
      <tr><th>Request</th><th>Asked by</th><th>About</th><th>Asked</th>
          <th>Due</th><th>Open</th><th></th></tr>
    </thead>
    <tbody>
      {% for r in rows %}
        <tr class="{% if not r.open %}is-closed{% endif %}">
          <td>{{ r.request.title }}</td>
          <td>{{ r.asker }}</td>
          <td>{{ r.scope }}</td>
          <td class="num">{{ r.request.requested_on }}</td>
          <td class="num">{{ r.request.due_on or "—" }}</td>
          <td class="num">
            {% if r.request.cancelled_at %}cancelled
            {% elif r.open %}{{ r.open_count }} of {{ r.total_count }}
            {% else %}all in{% endif %}
          </td>
          <td>
            <a href="/accounts/{{ header.org.ref }}/requests/{{ r.request.id }}">Items</a>
            <button hx-get="/accounts/{{ header.org.ref }}/requests/{{ r.request.id }}/edit"
                    hx-target="previous .form-host" hx-swap="innerHTML">Edit</button>
          </td>
        </tr>
      {% else %}
        <tr><td colspan="7" class="empty">Nothing outstanding. Add a request when you ask for something.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
<button hx-get="/accounts/{{ header.org.ref }}/requests/new"
        hx-target="previous .form-host" hx-swap="innerHTML">Add request</button>
```

The `Items` link points at the detail route built in Task 12. Until then it 404s; Task 12's tests cover it. Do not build that route here.

Create `src/bookkit/web/templates/account/requests.html` extending `account/page.html` and including the panel, following the shape of `contacts.html` exactly.

- [ ] **Step 5: Run the test and confirm it passes**

```bash
uv run --no-sync python -m pytest tests/test_web_requests.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: 5 passed.

- [ ] **Step 6: Verify the batch assertion can fail**

Temporarily move `apply_request` outside the `_save` batch block. Confirm `test_editing_a_request_writes_one_web_batch` fails on the "wrote outside any batch" assert. Restore.

- [ ] **Step 7: Flip the ledger entries, gates, commit**

Move the request actions from `PENDING` to `IMPLEMENTED` in `src/bookkit/web/parity.py`.

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add -A
git commit -m "web: information requests — the chase list, create, and edit

A request says who was asked and what it is about, because a request with
neither is one you cannot chase."
```

---

### Task 12: Request items — the detail list, add, edit, and mark received

**Files:**
- Create: `src/bookkit/web/templates/account/request_detail.html`, `src/bookkit/web/templates/account/_items_panel.html`
- Modify: `src/bookkit/web/routes/account.py`, `src/bookkit/web/parity.py`
- Test: `tests/test_web_requests.py` (append)

**Interfaces:**
- Consumes: `bookkit.forms.entities.{rfi_item_form, apply_rfi_item}`, `bookkit.repo.rfi.{items_for_request, get_item, get_request}`, `bookkit.services.rfi.{effective_due, mark_received}`
- Produces: routes `GET /accounts/{ref}/requests/{request_id}`, `GET/POST …/items/new`, `GET/POST …/items/{item_id}/edit`, `POST …/items/{item_id}/received`

**Exact signatures:**

```python
rfi_item_form(existing: RfiItem | None = None, *, conn=None) -> FormSpec
apply_rfi_item(conn, values: dict, request_id: str, existing: RfiItem | None = None) -> RfiItem
rfi_repo.items_for_request(conn, request_id) -> list[RfiItem]
rfi_svc.effective_due(item, request) -> str | None
rfi_svc.mark_received(conn, item_id, on: str) -> RfiItem
```

Two things the code already handles — inherit them, do not reimplement:

1. **`apply_rfi_item` owns the status/`received_on` pair.** Status not `received` clears the date; the form is the only door to *waiving* an item. Never write `received_on` from a route.
2. **`mark_received` writes two fields**, so a field-granular undo would revert only the later one and leave a received item with a null date. Web writes go through `open_batch`, which makes both one batch — so `R` restores the pair correctly. This is a property the web gets for free; do not add compensating logic.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_requests.py`:

```python
def _first_request_with_items(conn, org_id):
    from bookkit.repo import rfi as rfi_repo

    for request in rfi_repo.requests_for_org(conn, org_id):
        if rfi_repo.items_for_request(conn, request.id):
            return request
    return None


def test_request_detail_lists_its_items(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    request = _first_request_with_items(conn, org.id)
    if request is None:
        pytest.skip("seed has no request with items")
    from bookkit.repo import rfi as rfi_repo

    items = rfi_repo.items_for_request(conn, request.id)
    response = client.get(f"/accounts/{org.ref}/requests/{request.id}")
    assert response.status_code == 200
    assert items[0].prompt in response.text


def test_marking_an_item_received_stamps_the_date_in_one_batch(app_and_org):
    """status OWNS received_on — the pair can never disagree. Both writes land
    in one batch, so a revert restores both."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo
    from bookkit.services import batches as batches_svc

    request = _first_request_with_items(conn, org.id)
    if request is None:
        pytest.skip("seed has no request with items")
    outstanding = [i for i in rfi_repo.items_for_request(conn, request.id)
                   if i.status == "outstanding"]
    if not outstanding:
        pytest.skip("seed has no outstanding item")
    item = outstanding[0]

    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/received")
    assert response.status_code == 200

    after = rfi_repo.get_item(conn, item.id)
    assert after.status == "received"
    assert after.received_on, "received without a date — the pair disagreed"

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"

    batches_svc.revert(conn, batch)
    restored = rfi_repo.get_item(conn, item.id)
    assert restored.status == "outstanding"
    assert not restored.received_on, "revert left a stale received date"


def test_adding_an_item_writes_one_web_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import rfi as rfi_repo

    request = rfi_repo.requests_for_org(conn, org.id)[0]
    before = len(rfi_repo.items_for_request(conn, request.id))
    response = client.post(
        f"/accounts/{org.ref}/requests/{request.id}/items/new",
        data={"prompt": "loss runs 2021-2025", "kind": "document",
              "category": "Financials", "due_on": "", "detail": "",
              "status": "outstanding", "received_on": "", "response": ""},
    )
    assert response.status_code == 200
    assert len(rfi_repo.items_for_request(conn, request.id)) == before + 1

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"
```

- [ ] **Step 2: Run and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_requests.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: the three new tests fail with 404. **If any of them skips**, the seed lacks the fixture data and the test protects nothing — find a request with items using `bookctl` against the demo DB and adjust the fixture rather than accepting the skip.

- [ ] **Step 3: Add the routes**

Add to `src/bookkit/web/routes/account.py`:

```python
from ...forms.entities import apply_rfi_item, rfi_item_form


def _items_context(conn: sqlite3.Connection, org: Org, request_id: str) -> dict[str, Any]:
    request_row = rfi_repo.get_request(conn, request_id)
    items = rfi_repo.items_for_request(conn, request_id)
    return {
        "header": {"org": org},
        "request": request_row,
        "items": [{"item": i, "due": rfi_svc.effective_due(i, request_row)} for i in items],
    }


@router.get("/accounts/{ref}/requests/{request_id}", response_class=HTMLResponse)
def request_detail(request: Request, ref: str, request_id: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    context = _items_context(conn, org, request_id)
    context["header"] = _header(conn, org)
    context["tab"] = "requests"
    return TEMPLATES.TemplateResponse(request, "account/request_detail.html", context)


def _items_panel(request: Request, org: Org, request_id: str) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "account/_items_panel.html",
        _items_context(_conn(request), org, request_id),
    )


@router.get("/accounts/{ref}/requests/{request_id}/items/new", response_class=HTMLResponse)
def item_new_form(request: Request, ref: str, request_id: str) -> HTMLResponse:
    _org(request, ref)
    spec = rfi_item_form(conn=_conn(request))
    action = f"/accounts/{ref}/requests/{request_id}/items/new"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/requests/{request_id}/items/new", response_class=HTMLResponse)
async def item_create(request: Request, ref: str, request_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    spec = rfi_item_form(conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/requests/{request_id}/items/new"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_rfi_item(conn, values, request_id),
    )
    return refused or _items_panel(request, org, request_id)


@router.get("/accounts/{ref}/requests/{request_id}/items/{item_id}/edit",
            response_class=HTMLResponse)
def item_edit_form(request: Request, ref: str, request_id: str, item_id: str) -> HTMLResponse:
    _org(request, ref)
    conn = _conn(request)
    existing = rfi_repo.get_item(conn, item_id)
    spec = rfi_item_form(existing, conn=conn)
    action = f"/accounts/{ref}/requests/{request_id}/items/{item_id}/edit"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/requests/{request_id}/items/{item_id}/edit",
             response_class=HTMLResponse)
async def item_update(request: Request, ref: str, request_id: str, item_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    existing = rfi_repo.get_item(conn, item_id)
    spec = rfi_item_form(existing, conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/requests/{request_id}/items/{item_id}/edit"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_rfi_item(conn, values, request_id, existing),
    )
    return refused or _items_panel(request, org, request_id)


@router.post("/accounts/{ref}/requests/{request_id}/items/{item_id}/received",
             response_class=HTMLResponse)
def item_received(request: Request, ref: str, request_id: str, item_id: str) -> HTMLResponse:
    """Received, dated, in one batch. mark_received writes status AND
    received_on; the batch is what makes the pair revert together."""
    from datetime import date

    org = _org(request, ref)
    conn = _conn(request)
    existing = rfi_repo.get_item(conn, item_id)
    with batches_svc.open_batch(
        conn, source="web", tool="rfi_item_received",
        summary=f"received {existing.prompt}", org_id=org.id,
    ):
        rfi_svc.mark_received(conn, item_id, date.today().isoformat())
    return _items_panel(request, org, request_id)
```

- [ ] **Step 4: Write the templates**

Create `src/bookkit/web/templates/account/_items_panel.html`. Status carries a word; `due` comes from `effective_due` (the item's own date, else the request's), never from the item alone:

```html
<div class="form-host"></div>
<div class="scroller">
  <table class="rows">
    <thead>
      <tr><th>Item</th><th>Type</th><th>Group</th><th>Needed by</th>
          <th>Status</th><th></th></tr>
    </thead>
    <tbody>
      {% for row in items %}
        <tr class="is-{{ row.item.status }}">
          <td>
            {{ row.item.prompt }}
            {% if row.item.detail %}<span class="item-detail">{{ row.item.detail }}</span>{% endif %}
          </td>
          <td>{{ row.item.kind }}</td>
          <td>{{ row.item.category or "—" }}</td>
          <td class="num">{{ row.due or "—" }}</td>
          <td class="num">
            {{ row.item.status }}{% if row.item.received_on %} {{ row.item.received_on }}{% endif %}
          </td>
          <td>
            {% if row.item.status == "outstanding" %}
              <button hx-post="/accounts/{{ header.org.ref }}/requests/{{ request.id }}/items/{{ row.item.id }}/received"
                      hx-target="closest .form-host" hx-swap="innerHTML">Mark received</button>
            {% endif %}
            <button hx-get="/accounts/{{ header.org.ref }}/requests/{{ request.id }}/items/{{ row.item.id }}/edit"
                    hx-target="previous .form-host" hx-swap="innerHTML">Edit</button>
          </td>
        </tr>
      {% else %}
        <tr><td colspan="6" class="empty">No items yet. Add what you asked for.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
<button hx-get="/accounts/{{ header.org.ref }}/requests/{{ request.id }}/items/new"
        hx-target="previous .form-host" hx-swap="innerHTML">Add item</button>
```

Note the `Mark received` button targets `closest .form-host` while `Edit` targets `previous .form-host`. Both must resolve to the panel's own host div — verify in the browser at Task 14 Step 8 and simplify to one pattern if they do not.

Create `src/bookkit/web/templates/account/request_detail.html` extending `account/page.html`, showing the request's title, asker, scope, and dates above the items panel, then including `_items_panel.html`.

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
uv run --no-sync python -m pytest tests/test_web_requests.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: 8 passed, 0 skipped.

- [ ] **Step 6: Verify the received/revert assertion can fail**

Temporarily call `rfi_svc.mark_received` outside the `open_batch` block. Confirm `test_marking_an_item_received_stamps_the_date_in_one_batch` fails at the batch or the revert assertion. Restore.

- [ ] **Step 7: Flip the ledger, gates, commit**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
git add -A
git commit -m "web: request items — add, edit, and mark received

Marking received writes status and received_on together inside one batch, so a
revert restores the pair instead of leaving a received item with no date."
```

---

### Task 13: Open items — the account's task list

**Files:**
- Create: `src/bookkit/web/templates/account/open_items.html`, `src/bookkit/web/templates/account/_open_items_panel.html`
- Modify: `src/bookkit/web/routes/account.py`, `src/bookkit/web/parity.py`, `src/bookkit/web/templates/account/page.html`
- Test: `tests/test_web_open_items.py`

**Interfaces:**
- Consumes: `bookkit.forms.entities.{task_form, apply_task}`, `bookkit.repo.tasks.{open_tasks_for_client, get, complete}`
- Produces: routes `GET /accounts/{ref}/open-items`, `GET/POST …/tasks/new`, `GET/POST …/tasks/{task_id}/edit`, `POST …/tasks/{task_id}/done`

**Scope note:** this is the task list only. The TUI's `x` export (an XLSX workbook via `services/export_open_items.py`) is **deferred** — a file-download response is a mechanism the spec does not cover. It stays in `PENDING` with that reason.

Confirm `task_form`'s and `apply_task`'s real signatures before wiring, the same way Task 11 pinned the request ones:

```bash
grep -n -A 12 "^def task_form\|^def apply_task" src/bookkit/forms/entities.py
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_open_items.py`:

```python
"""Open items — what is still to do on this account."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, tasks as tasks_repo

    org = next(
        (o for o in orgs.list_orgs(app.state.conn, kind="client")
         if tasks_repo.open_tasks_for_client(app.state.conn, o.id)),
        None,
    )
    assert org is not None, "seed has no client with open tasks"
    with TestClient(app) as client:
        yield client, org


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, limit=1)
    return found[0] if found else None


def test_open_items_lists_the_tasks(app_and_org):
    client, org = app_and_org
    from bookkit.repo import tasks as tasks_repo

    tasks = tasks_repo.open_tasks_for_client(client.app.state.conn, org.id)
    response = client.get(f"/accounts/{org.ref}/open-items")
    assert response.status_code == 200
    assert tasks[0].title in response.text


def test_completing_a_task_is_one_web_batch_and_reverts(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo
    from bookkit.services import batches as batches_svc

    task = tasks_repo.open_tasks_for_client(conn, org.id)[0]
    response = client.post(f"/accounts/{org.ref}/tasks/{task.id}/done")
    assert response.status_code == 200
    assert tasks_repo.get(conn, task.id).status != task.status

    batch = _latest_batch(conn)
    assert batch is not None, "completion wrote outside any batch"
    assert batch.source == "web"

    batches_svc.revert(conn, batch)
    assert tasks_repo.get(conn, task.id).status == task.status


def test_adding_a_task_writes_one_web_batch(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    before = len(tasks_repo.open_tasks_for_client(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/tasks/new",
        data={"title": "Chase the loss runs", "due_on": "2026-09-01",
              "description": "", "detail": "", "priority": "2"},
    )
    assert response.status_code == 200
    assert len(tasks_repo.open_tasks_for_client(conn, org.id)) == before + 1

    batch = _latest_batch(conn)
    assert batch is not None and batch.source == "web"


def test_a_task_with_a_bare_number_due_date_is_refused(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import tasks as tasks_repo

    before = len(tasks_repo.open_tasks_for_client(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/tasks/new",
        data={"title": "Chase the loss runs", "due_on": "5",
              "description": "", "detail": "", "priority": "2"},
    )
    assert "cannot read a date" in response.text
    assert "Chase the loss runs" in response.text
    assert len(tasks_repo.open_tasks_for_client(conn, org.id)) == before
```

The POST bodies above assume `task_form`'s field keys. **Correct them against the real spec** from the grep in the interfaces block before running — a test posting keys the form does not declare passes for the wrong reason, because `parse_values` reads only what the spec names.

- [ ] **Step 2: Run and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_open_items.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Expected: 404s.

- [ ] **Step 3: Add the routes**

Follow the exact shape of Task 8's contacts routes: a `GET` tab route rendering `open_items.html`, a `_open_items_panel` helper, `new`/`edit` form routes using `_save`, and a `done` POST wrapping `tasks_repo.complete` in `open_batch(source="web", tool="task_done", summary=f"completed {task.title}", org_id=org.id)`.

Add an Open items link to `page.html`'s tab nav.

- [ ] **Step 4: Write the templates**

`_open_items_panel.html` is a ruled table per the visual direction: title in sans, due date in mono, an overdue due date carrying the word "over" as well as `--bk-red`. A `Mark done` button per row and an `Add task` button beneath. Empty state: "Nothing open."

`open_items.html` extends `account/page.html` and includes the panel.

- [ ] **Step 5: Run, verify the batch assertion can fail, flip the ledger, gates, commit**

```bash
uv run --no-sync python -m pytest tests/test_web_open_items.py -q > "$GATE/out.txt" 2>&1; tail -10 "$GATE/out.txt"
```

Then temporarily move `tasks_repo.complete` outside its batch and confirm `test_completing_a_task_is_one_web_batch_and_reverts` fails. Restore. Flip the task actions in `parity.py` to `IMPLEMENTED`, leaving `export_open_items` in `PENDING` with its deferral reason. Full gates, then:

```bash
git add -A
git commit -m "web: open items — the account's task list, add, edit, complete

The XLSX export stays deferred in the parity ledger: a file-download response
is a mechanism the spec does not cover yet."
```

---

### Task 14: One palette, and the wheelhouse

**Files:**
- Create: `src/bookkit/web/theme_css.py`
- Modify: `src/bookkit/web/static/app.css`, `src/bookkit/web/templates/base.html`, `src/bookkit/web/app.py`, `changelog.md`
- Test: `tests/test_web_shell.py` (append)

**Interfaces:**
- Consumes: `bookkit.tui.theme` colour constants `BG, SURFACE, PANEL, RULE, FG, DIM, GOLD, RED, AMBER, GREEN, BLUE`
- Produces: `bookkit.web.theme_css.css_variables() -> str`, and route `GET /static/theme.css`

Note: `theme_css.py` imports `bookkit.tui.theme`, which the Task 4 convention test forbids. **Resolve this by moving the colour constants**, not by weakening the rule: move the eleven constants from `src/bookkit/tui/theme.py` into a new `src/bookkit/palette.py`, and have `tui/theme.py` import them from there. The rule stands, and colour genuinely has one home.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_shell.py`:

```python
def test_theme_css_comes_from_the_one_palette(client):
    """Colour is signal. A second palette in a stylesheet is how the two
    surfaces come to disagree about what red means."""
    from bookkit import palette

    response = client.get("/static/theme.css")
    assert response.status_code == 200
    for name in ("BG", "SURFACE", "RULE", "FG", "GOLD", "RED", "AMBER", "GREEN", "BLUE"):
        assert getattr(palette, name) in response.text, f"{name} missing from theme.css"
```

The boundary itself is already guarded by `test_web_and_tui_never_import_each_other`
in `tests/test_conventions.py` (Task 4), which was confirmed capable of failing. Do
not add a second, looser substring check here — a bare `"tui" not in source` fires on
any word containing those letters.

- [ ] **Step 2: Run and confirm it fails**

```bash
uv run --no-sync python -m pytest tests/test_web_shell.py -q > "$GATE/out.txt" 2>&1; tail -8 "$GATE/out.txt"
```

Expected: `ModuleNotFoundError: No module named 'bookkit.palette'`.

- [ ] **Step 3: Move the palette**

Create `src/bookkit/palette.py` containing the eleven constants moved verbatim out of `src/bookkit/tui/theme.py` (with their trailing comments — they say what each colour *means*, which is the point):

```python
"""The one palette. Both surfaces read these; neither redefines them.

Colour is signal, not decoration — every coloured state also carries a glyph
or a word, so meaning survives without it."""

BG = "#15171c"  # screen
SURFACE = "#1a1d23"  # panes
PANEL = "#232733"  # bars, cards, modals
RULE = "#3a4150"  # borders, separators
FG = "#d5d2c9"  # primary text
DIM = "#8a8577"  # secondary text
GOLD = "#d6b35a"  # focus, selection, accent
RED = "#d57367"  # overdue, error
AMBER = "#d9a441"  # due soon, warning
GREEN = "#84a98c"  # bound, done, success
BLUE = "#7f9cc4"  # in flight (submitted, quoted, out)
```

In `src/bookkit/tui/theme.py`, replace the definitions with `from ..palette import (AMBER, BG, BLUE, DIM, FG, GOLD, GREEN, PANEL, RED, RULE, SURFACE)  # noqa: F401`.

- [ ] **Step 4: Write the CSS generator and serve it**

Create `src/bookkit/web/theme_css.py`:

```python
"""CSS custom properties derived from the one palette."""

from __future__ import annotations

from .. import palette

_NAMES = ("BG", "SURFACE", "PANEL", "RULE", "FG", "DIM",
          "GOLD", "RED", "AMBER", "GREEN", "BLUE")


def css_variables() -> str:
    lines = [f"  --bk-{name.lower()}: {getattr(palette, name)};" for name in _NAMES]
    return ":root {\n" + "\n".join(lines) + "\n}\n"
```

In `src/bookkit/web/app.py`, add the route above the router include:

```python
    @app.get("/static/theme.css")
    def theme_css() -> Response:
        from .theme_css import css_variables

        return Response(content=css_variables(), media_type="text/css")
```

Import `Response` from `fastapi`. Register this **before** the `StaticFiles` mount, or the mount will shadow it — verify by running the test.

Add to `base.html`, before `app.css`:

```html
  <link rel="stylesheet" href="/static/theme.css">
```

- [ ] **Step 5: Write the stylesheet**

Replace `src/bookkit/web/static/app.css`, following `docs/superpowers/specs/2026-08-17-web-visual-direction.md` exactly. **Read that document before writing a line of this file.** Every colour comes from a `--bk-*` variable; no literal hex appears anywhere in the stylesheet.

Add the two type tokens and the scale to the top of the file (the palette variables arrive from `/static/theme.css`):

```css
:root {
  --bk-sans: ui-sans-serif, system-ui, -apple-system, "SF Pro Text", "Segoe UI", sans-serif;
  --bk-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
}
```

Cover, at minimum: `body` (explicit `background` and `color` — the page is
single-theme by choice and must not inherit), `.account-header`, `.status-pill`,
the rail (`.rail`, `.rail-track`, `.rail-marker`, `.rail-overrun`, `.rail-tick`,
`.rail-scale`, `.rail-date`, `.rail-count`, `.rail-days`, `.rail-unit`,
`.rail-lines`), `.tabs a` and `.tabs a.is-current`, `.card`, `table.rows`,
the ledger (`.log`, `.log-head`, `.log-type`, `.log-subject`, `.log-body`,
`.log-actions`), `.entity-form .field`, `.form-error`, `.confirm .danger`,
`.empty`, and `:focus-visible`.

Load-bearing details from the visual direction, restated so they are not missed:

- `.rail-days` is `2.75rem`, `--bk-mono`, `font-variant-numeric: tabular-nums`.
- `.rail-date` and every date, money value, count, and ref use `--bk-mono`.
  Names, titles, subjects, and note bodies use `--bk-sans`.
- `.is-overdue` uses `--bk-red` **and** the template already prints the word
  "over" — colour never carries the meaning alone.
- `.log` draws one continuous `1px solid var(--bk-rule)` down its left edge, with
  `.log-head time` hanging in that margin in mono and `.log-body` indented past it.
- `:focus-visible` is `2px solid var(--bk-gold)` at `2px` offset, on every
  interactive element.
- Rows sit at roughly 32px. Radius is `2px` on controls, `0` elsewhere. No
  shadows, no gradients.
- Narrow window: `table.rows` lives in an `overflow-x: auto` container and the
  rail collapses to date plus count. The body never scrolls sideways.
- `@media (prefers-reduced-motion: reduce)` disables the panel settle transition.

- [ ] **Step 6: Run the tests and confirm they pass**

```bash
uv run --no-sync python -m pytest tests/test_web_shell.py tests/test_conventions.py -q > "$GATE/out.txt" 2>&1; tail -8 "$GATE/out.txt"
```

Expected: all pass, including the convention rule that `web/` never imports the TUI.

- [ ] **Step 7: Full gates**

```bash
uv run --no-sync python -m pytest -q > "$GATE/out.txt" 2>&1; tail -20 "$GATE/out.txt"
uv run --no-sync python -m mypy src > "$GATE/mypy.txt" 2>&1; tail -3 "$GATE/mypy.txt"
uv run --no-sync python -m ruff check src tests > "$GATE/ruff.txt" 2>&1; tail -3 "$GATE/ruff.txt"
```

All three must be clean.

- [ ] **Step 8: Look at it**

```bash
make demo
```

In the demo shell, run `bookctl web` and open an account. Automated tests cover behaviour, not legibility — check the header at a narrow window, a long account name, and that an overdue renewal reads as overdue without relying on colour alone. Fix what you find before the final commit.

- [ ] **Step 9: Refresh the wheelhouse**

This branch adds four runtime dependencies, so the wheelhouse is stale. Follow the drill on the Makefile's `wheelhouse` target: rebuild, upload, and take `WHEELHOUSE_SHA256` from the **uploaded** asset in the same commit as the upload — a stale hash aborts the installer with "altered in transit," a tamper warning about an untampered file. The wheelhouse is arm64-only.

```bash
make wheelhouse
```

- [ ] **Step 10: Update the changelog and commit**

Add an entry to `changelog.md` per the prompt at its own bottom.

```bash
git add -A
git commit -m "web: one palette for both surfaces, plus the stylesheet

The colour constants move to bookkit.palette so the web layer can read them
without importing the TUI — the boundary rule stands and colour gets one home."
```

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: stack and runtime → Task 4; the seam (`forms/spec.py`, `forms/entities.py`, killing the duplicate) → Tasks 1–3; convention enforcement → Task 4; parity ledger → Task 5 (flipped in 8 and 10); URLs and Overview → Task 7; the edit round-trip → Task 8; refusals → Task 9; money/date/colour input rules → Tasks 6 and 11; confirmations → Task 10; the four named tests → Tasks 6, 7, 8, 9; verifying tests can fail → Tasks 4, 5, 6, 7, 8, 9, 10; wheelhouse → Task 11.

**Not covered, by design** (recorded in the spec): Playwright and visual-regression testing, deferred to stage 3; the TUI/web concurrent-DB staleness, accepted as a known rough edge; `bookctl web` port selection, defaulted to 8931 in Task 4 rather than left open.

**Repo function names are verified**, not guessed: `tasks.open_tasks_for_client`, `opportunities.for_org(…, open_only=)`, `contacts.for_org(…, active_only=)`, `interactions.for_org(…, limit=)`, `batches.recent(…, limit=)`, `orgs.find`, `renewals.next_for_org`. Note `Opportunity.title` — there is no `.name`.

**One thing the implementer must resolve against real data**, flagged inline rather than guessed: whether the seeded account the fixtures pick actually has a live renewal where `renewal_on != period_to` (Task 7 Steps 5–6). If it does not, the renewal-date test passes trivially and protects nothing — the fixture must then select an account where the two differ, which is the whole point of that assertion.

**Ordering risk.** Task 11 moves the palette out of `tui/theme.py`, which the Task 4 convention test forces. That move is safe to do late only because nothing before Task 11 imports colour into `web/`; if an earlier task needs a colour, do the palette move then instead.
