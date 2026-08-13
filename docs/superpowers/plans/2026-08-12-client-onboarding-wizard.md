# Client Onboarding Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Step-by-step guided capture for new clients where each step commits immediately, skip is first-class, resume derives from the data itself, and incomplete onboarding sits in the Navigator's attention tree until finished. Plus: inline team-member creation from the assignment form.

**Architecture:** `services/onboarding.py` declares the flow as data (ordered steps + completeness rules); `OnboardingScreen` renders it, reusing every existing `entity_forms`/`entity_actions` form. There is NO wizard-state table — `completeness()` recomputes from real rows, so out-of-band edits count. `FormModal` gains optional draft persistence so a half-typed step survives esc/crash.

**Tech Stack:** Textual, SQLite via existing repo/services layers, pytest with Textual pilot.

## Global Constraints

- Gates before EVERY commit: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`. Never pipe test output before the `&&` gate.
- tui/ contains zero raw SQL; all data access through repo/ and services/.
- Forms commit in place (`commit=`); a refused save keeps input intact. New screens call shared flows in `widgets/entity_actions.py`, never fork form wiring.
- Color is signal, not decoration: every colored state carries a glyph or word (`tui/theme.py` helpers).
- Vocabulary fields complete from existing records (`Field.suggestions`).
- No schema changes in this plan (the Task `description` column lands in the export plan, which runs first).
- Read the `textual-modal-forms` skill before the TUI tasks (documented pitfalls: ctrl+p palette, Rich markup in Static, autocomplete swallowing Enter, stale DataTable keys).

---

### Task 1: `services/onboarding.py` — steps, completeness, resume targets

**Files:**
- Create: `src/bookkit/services/onboarding.py`
- Test: `tests/test_services.py` (append)

**Interfaces:**
- Produces (consumed by Tasks 3-5):
  - `STEPS: tuple[Step, ...]` — `Step(key: str, label: str, required: bool)`; keys in order: `org`, `contacts`, `program`, `projects`, `followups`
  - `COMPLETE = "complete"`, `PARTIAL = "partial"`, `UNTOUCHED = "untouched"`
  - `StepStatus(step: Step, state: str, summary: str)`
  - `completeness(conn, org_id) -> list[StepStatus]` (always len(STEPS), STEPS order)
  - `first_incomplete(conn, org_id) -> str | None` (step key; required steps first, then optional; None when everything is complete)
  - `is_complete(conn, org_id) -> bool` (all REQUIRED steps complete)
  - `incomplete_clients(conn, today: date) -> list[tuple[Org, str]]` (org + comma-joined missing required step labels)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_services.py`:

```python
def test_onboarding_completeness_derives_from_data(conn):
    from bookkit.services import onboarding

    org = orgs.create(conn, name="Newco", kind="client")
    states = {s.step.key: s.state for s in onboarding.completeness(conn, org.id)}
    assert states == {
        "org": onboarding.PARTIAL,       # name/kind exist by construction
        "contacts": onboarding.UNTOUCHED,
        "program": onboarding.UNTOUCHED,
        "projects": onboarding.UNTOUCHED,
        "followups": onboarding.UNTOUCHED,
    }
    assert onboarding.first_incomplete(conn, org.id) == "org"
    assert not onboarding.is_complete(conn, org.id)

    orgs.update(conn, org.id, owner="grant", industry="construction")
    contacts.create(conn, org.id, first_name="Ann", last_name="Lee",
                    email="ann@newco.com")
    placements.create(conn, org_id=org.id, program_name="Newco Package 26-27",
                      period_from="2026-09-01", period_to="2027-09-01")
    states = {s.step.key: s.state for s in onboarding.completeness(conn, org.id)}
    assert states["org"] == states["contacts"] == states["program"] == onboarding.COMPLETE
    assert onboarding.is_complete(conn, org.id)  # optional steps don't gate
    assert onboarding.first_incomplete(conn, org.id) == "projects"


def test_onboarding_contact_without_reach_is_partial(conn):
    from bookkit.services import onboarding

    org = orgs.create(conn, name="Newco2", kind="client")
    contacts.create(conn, org.id, first_name="Bo", last_name="Nil")  # no email/phone
    status = {s.step.key: s for s in onboarding.completeness(conn, org.id)}
    assert status["contacts"].state == onboarding.PARTIAL
    assert "email or phone" in status["contacts"].summary


def test_incomplete_clients_lists_missing_labels(conn):
    from bookkit.services import onboarding

    org = orgs.create(conn, name="Fresh LLC", kind="client")  # status defaults to prospect
    got = onboarding.incomplete_clients(conn, date(2026, 8, 12))
    assert [o.id for o, _ in got] == [org.id]
    _, missing = got[0]
    assert "contacts" in missing and "program" in missing
```

(Match the file's existing imports; `placements.create` — copy the signature usage from existing tests in the file.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_services.py -k onboarding -v 2>&1 | tail -3`
Expected: FAIL — no module `bookkit.services.onboarding`.

- [ ] **Step 3: Implement**

```python
"""Client onboarding: the flow is data, and the data is the state.

Steps are declared once here; the TUI wizard renders them and a future MCP
front-end will walk the same list. There is no wizard-state table —
completeness() derives per-step status from what's actually in the book, so
resuming (or filling a gap out-of-band) needs no bookkeeping.

Attention scope: a client nags in incomplete_clients() only while it's
plausibly still being onboarded — status 'prospect', or created within the
last 60 days. Without that fence every legacy client missing an owner would
flood attention forever.  ← REVIEW POINT (Grant): is 60 days right?"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..models import Org
from ..repo import contacts, orgs, placements
from ..repo import projects as projects_repo
from ..repo import tasks as tasks_repo

COMPLETE = "complete"
PARTIAL = "partial"
UNTOUCHED = "untouched"

ONBOARDING_WINDOW_DAYS = 60


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    required: bool  # required steps gate is_complete(); optional ones inform


STEPS: tuple[Step, ...] = (
    Step("org", "account basics", True),
    Step("contacts", "contacts", True),
    Step("program", "program & lines", True),
    Step("projects", "projects & needs", False),
    Step("followups", "follow-ups", False),
)


@dataclass(frozen=True)
class StepStatus:
    step: Step
    state: str
    summary: str  # one line for the wizard pane


def _org_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    missing = [f for f in ("owner", "industry") if not getattr(org, f)]
    if not missing:
        return COMPLETE, f"owner {org.owner} · {org.industry}"
    return PARTIAL, "missing " + " and ".join(missing)


def _contacts_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    people = contacts.for_org(conn, org.id)
    if not people:
        return UNTOUCHED, "no contacts yet"
    reachable = [c for c in people if c.email or c.phone or c.mobile]
    if reachable:
        primary = "★ primary set" if any(c.is_primary for c in people) else "no primary"
        return COMPLETE, f"{len(people)} contact(s) · {primary}"
    return PARTIAL, f"{len(people)} contact(s), none with email or phone"


def _program_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    pls = placements.for_org(conn, org.id)
    if not pls:
        return UNTOUCHED, "no program yet"
    return COMPLETE, " · ".join(p.program_name for p in pls[:3])


def _projects_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    projs = projects_repo.projects_for_org(conn, org.id)
    if not projs:
        return UNTOUCHED, "none recorded (optional)"
    bare = [p for p in projs if not projects_repo.needs_for_project(conn, p.id)]
    if bare:
        return PARTIAL, f"{len(bare)} project(s) with no needs listed"
    return COMPLETE, f"{len(projs)} project(s), needs listed"


def _followups_state(conn: sqlite3.Connection, org: Org) -> tuple[str, str]:
    open_tasks = tasks_repo.open_tasks(conn, org_id=org.id)
    if open_tasks:
        return COMPLETE, f"{len(open_tasks)} open task(s)"
    return UNTOUCHED, "no follow-up task (optional)"


_STATE_FNS = {
    "org": _org_state,
    "contacts": _contacts_state,
    "program": _program_state,
    "projects": _projects_state,
    "followups": _followups_state,
}


def completeness(conn: sqlite3.Connection, org_id: str) -> list[StepStatus]:
    org = orgs.get(conn, org_id)
    out: list[StepStatus] = []
    for step in STEPS:
        state, summary = _STATE_FNS[step.key](conn, org)
        out.append(StepStatus(step, state, summary))
    return out


def first_incomplete(conn: sqlite3.Connection, org_id: str) -> str | None:
    statuses = completeness(conn, org_id)
    for required in (True, False):
        for status in statuses:
            if status.step.required is required and status.state != COMPLETE:
                return status.step.key
    return None


def is_complete(conn: sqlite3.Connection, org_id: str) -> bool:
    return all(
        s.state == COMPLETE for s in completeness(conn, org_id) if s.step.required
    )


def incomplete_clients(
    conn: sqlite3.Connection, today: date
) -> list[tuple[Org, str]]:
    """Clients still inside the onboarding window with required steps open,
    oldest first — the attention feed."""
    floor = (today - timedelta(days=ONBOARDING_WINDOW_DAYS)).isoformat()
    out: list[tuple[Org, str]] = []
    for org in orgs.list_orgs(conn, kind="client"):
        if org.status != "prospect" and org.created_at[:10] < floor:
            continue
        missing = [
            s.step.label
            for s in completeness(conn, org.id)
            if s.step.required and s.state != COMPLETE
        ]
        if missing:
            out.append((org, ", ".join(missing)))
    return sorted(out, key=lambda pair: pair[0].created_at)
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_services.py -k onboarding -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git add src/bookkit/services/onboarding.py tests/test_services.py && git commit -m "onboarding: flow-as-data completeness service — the data is the state"`

---

### Task 2: FormModal draft persistence (`draft_key`)

Half-typed step forms must survive esc and crashes. `repo/drafts.py` already persists per-screen scratch payloads (`save/load/clear(conn, screen, payload)`); FormModal gains an optional hook into it.

**Files:**
- Modify: `src/bookkit/tui/widgets/forms.py`
- Test: `tests/test_tui_forms.py` (append)

**Interfaces:**
- Produces: `FormModal(spec, commit=None, draft_key: str | None = None)` — on cancel, drained raw text is saved under `draft_key`; on next open with the same key, empty widgets are prefilled from it; a successful save clears it. `draft_key=None` (the default everywhere today) changes nothing.

- [ ] **Step 1: Failing test**

```python
async def test_form_draft_survives_esc_and_clears_on_save(empty_db: Path) -> None:
    from bookkit.repo import drafts
    from bookkit.tui.widgets.forms import Field, FormModal, FormSpec

    app = BookkitApp(empty_db)
    spec = lambda: FormSpec("t", [Field("title", "title", required=True),
                                  Field("notes", "notes", "textarea")])
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(FormModal(spec(), draft_key="test:draft"))
        await pilot.pause()
        await _fill(pilot, app, "title", "half-typed thought")
        await pilot.press("escape")
        await pilot.pause()
        assert drafts.load(app.conn, "test:draft") is not None

        # reopen: the half-typed value is back
        app.push_screen(FormModal(spec(), draft_key="test:draft"))
        await pilot.pause()
        assert app.screen.query_one("#form-title", Input).value == "half-typed thought"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert drafts.load(app.conn, "test:draft") is None
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_tui_forms.py -k draft -v 2>&1 | tail -3`

- [ ] **Step 3: Implement**

In `forms.py`:

```python
    def __init__(
        self,
        spec: FormSpec,
        commit: Callable[[dict[str, Any]], str | None] | None = None,
        draft_key: str | None = None,
    ) -> None:
        super().__init__()
        self.spec = spec
        self._commit = commit
        self._draft_key = draft_key
```

Extend `on_mount` (before the focus call):

```python
    def on_mount(self) -> None:
        if self._draft_key:
            self._restore_draft()
        first = self.spec.fields[0]
        self.query_one(f"#form-{first.key}").focus()

    def _restore_draft(self) -> None:
        import json

        from ...repo import drafts

        payload = drafts.load(self.app.conn, self._draft_key)
        if not payload:
            return
        try:
            saved: dict[str, str] = json.loads(payload)
        except ValueError:
            return  # unreadable scratch is not worth an error
        for f in self.spec.fields:
            raw = saved.get(f.key)
            if not raw:
                continue
            widget = self.query_one(f"#form-{f.key}")
            if isinstance(widget, Input) and not widget.value:
                widget.value = raw
            elif isinstance(widget, TextArea) and not widget.text:
                widget.text = raw
            elif isinstance(widget, Select) and widget.value == Select.NULL:
                try:
                    widget.value = raw
                except Exception:  # option list changed since the draft — skip
                    pass
```

Extend `action_cancel` and the success path of `action_save`:

```python
    def action_cancel(self) -> None:
        if self._draft_key:
            import json

            from ...repo import drafts

            raw = {f.key: (self._drain(f) or "") for f in self.spec.fields}
            if any(raw.values()):
                drafts.save(self.app.conn, self._draft_key, json.dumps(raw))
            else:
                drafts.clear(self.app.conn, self._draft_key)
        self.dismiss(None)
```

and in `action_save`, immediately before `self.dismiss(values)`:

```python
        if self._draft_key:
            from ...repo import drafts

            drafts.clear(self.app.conn, self._draft_key)
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui_forms.py -q 2>&1 | tail -3` → PASS (all existing tests too: default behavior is untouched).

- [ ] **Step 5: Commit** — `git commit -m "forms: optional draft_key — half-typed modals survive esc via repo/drafts"`

---

### Task 3: `OnboardingScreen` — step list, pane, navigation

**Files:**
- Create: `src/bookkit/tui/screens/onboarding.py`
- Modify: `src/bookkit/tui/bookkit.tcss` (layout rules for the new ids)
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Consumes: `services.onboarding` (Task 1).
- Produces: `OnboardingScreen(org_id: str)` pushed via `app.push_screen`. Step actions land in Task 4; this task renders and navigates.

- [ ] **Step 1: Failing pilot test**

```python
async def test_onboarding_screen_lists_steps_with_state(empty_db: Path) -> None:
    from bookkit.tui.screens.onboarding import OnboardingScreen

    app = BookkitApp(empty_db)
    org = orgs.create(app.conn, name="Newco", kind="client")
    async with app.run_test(size=(130, 42)) as pilot:
        app.push_screen(OnboardingScreen(org.id))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        labels = [str(item.query_one(Label).renderable) for item in
                  screen.query_one("#onboard-steps", ListView).children]
        assert len(labels) == 5
        assert "account basics" in labels[0]
        # highlight starts on the first incomplete step
        assert screen.query_one("#onboard-steps", ListView).index == 0
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement the screen**

```python
"""Client onboarding wizard: steps left, current step's summary right.

Every step commits immediately through the same forms the rest of the app
uses; esc leaves at any time and loses nothing (the data IS the state —
reopening derives position from services.onboarding.completeness)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from ...services import onboarding
from .. import theme

if TYPE_CHECKING:
    from ..app import BookkitApp

_GLYPHS = {
    onboarding.COMPLETE: (f"[{theme.GREEN}]✓[/]", "done"),
    onboarding.PARTIAL: (f"[{theme.AMBER}]◐[/]", "partial"),
    onboarding.UNTOUCHED: (f"[{theme.DIM}]○[/]", "open"),
}


class OnboardingScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("enter", "do_step", "Fill in", priority=True),
        Binding("s", "skip_step", "Skip for now"),
        Binding("escape", "close", "Done for now"),
    ]

    def __init__(self, org_id: str) -> None:
        super().__init__()
        self.org_id = org_id
        self._statuses: list[onboarding.StepStatus] = []

    def compose(self) -> ComposeResult:
        yield Static(id="onboard-title")
        with Horizontal(id="onboard-split"):
            yield ListView(id="onboard-steps")
            with Vertical(id="onboard-pane"):
                yield Static(id="onboard-summary")
                yield Static(
                    "[b]enter[/b] fill in · [b]s[/b] skip · [b]esc[/b] done for now",
                    classes="hint",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data(jump_to_first_incomplete=True)

    def refresh_data(self, jump_to_first_incomplete: bool = False) -> None:
        conn = self.app.conn
        from ...repo import orgs

        org = orgs.get(conn, self.org_id)
        self._statuses = onboarding.completeness(conn, self.org_id)
        done = "all set" if onboarding.is_complete(conn, self.org_id) else "in progress"
        self.query_one("#onboard-title", Static).update(
            f"[b]onboarding — {org.name}[/b]  [{theme.DIM}]({done})[/]"
        )
        steps = self.query_one("#onboard-steps", ListView)
        keep = steps.index or 0
        steps.clear()
        for status in self._statuses:
            glyph, word = _GLYPHS[status.state]
            required = "" if status.step.required else f" [{theme.DIM}](optional)[/]"
            steps.append(ListItem(Label(f"{glyph} {status.step.label}{required} · {word}")))
        if jump_to_first_incomplete:
            target = onboarding.first_incomplete(conn, self.org_id)
            keys = [s.step.key for s in self._statuses]
            steps.index = keys.index(target) if target else 0
        else:
            steps.index = min(keep, len(self._statuses) - 1)
        self._render_summary()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._render_summary()

    def _current_status(self) -> onboarding.StepStatus:
        index = self.query_one("#onboard-steps", ListView).index or 0
        return self._statuses[index]

    def _render_summary(self) -> None:
        status = self._current_status()
        self.query_one("#onboard-summary", Static).update(
            f"[b]{status.step.label}[/b]\n\n{status.summary}"
        )

    def action_skip_step(self) -> None:
        steps = self.query_one("#onboard-steps", ListView)
        if steps.index is not None and steps.index < len(self._statuses) - 1:
            steps.index += 1

    def action_close(self) -> None:
        self.dismiss(None)

    def action_do_step(self) -> None:
        pass  # Task 4
```

TCSS (append to `bookkit.tcss`, matching the file's existing id-based conventions — check how `#nav-split` sizes its children and mirror):

```css
#onboard-split { height: 1fr; }
#onboard-steps { width: 38; }
#onboard-pane { padding: 1 2; }
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui.py -k onboarding -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "onboarding: wizard screen skeleton — completeness-driven step list"`

---

### Task 4: Step actions — every step opens the existing form, saves refresh

**Files:**
- Modify: `src/bookkit/tui/screens/onboarding.py`
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Consumes: `entity_forms` (`org_form_initial_profile`/`apply_org`, `contact_form`/`apply_contact`, `placement_form`/`apply_placement`, `project_form`/`apply_project`, `need_form`/`apply_need`, `task_form`/`apply_task`), `entity_actions.push_form` wrapper semantics, `FormModal(draft_key=...)` from Task 2.

- [ ] **Step 1: Failing pilot test**

```python
async def test_onboarding_enter_opens_step_form_and_save_advances(empty_db: Path) -> None:
    app = BookkitApp(empty_db)
    org = orgs.create(app.conn, name="Newco", kind="client")
    async with app.run_test(size=(130, 42)) as pilot:
        app.push_screen(OnboardingScreen(org.id))
        await pilot.pause()
        await pilot.press("enter")          # org basics step
        await pilot.pause()
        assert isinstance(app.screen, FormModal)
        await _fill(pilot, app, "owner", "grant")
        await _fill(pilot, app, "industry", "construction")
        await pilot.press("ctrl+s")
        await pilot.pause()
        # back on the wizard, org step now complete, highlight advanced
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        assert orgs.get(app.conn, org.id).owner == "grant"
        assert screen.query_one("#onboard-steps", ListView).index == 1  # contacts
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement `action_do_step`**

Replace the Task-3 stub. Each branch pushes the SAME form the rest of the app uses, with `draft_key=f"onboarding:{self.org_id}:{key}"`, commit-in-place, and a done-callback that refreshes and re-derives position:

```python
    def action_do_step(self) -> None:
        from ...repo import orgs
        from ..widgets import entity_forms as ef
        from ..widgets.forms import FormModal

        conn = self.app.conn
        key = self._current_status().step.key
        org = orgs.get(conn, self.org_id)
        draft = f"onboarding:{self.org_id}:{key}"

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data(jump_to_first_incomplete=True)

        if key == "org":
            spec = ef.org_form_initial_profile(conn, org)
            spec.title = "account basics"
            self.app.push_screen(
                FormModal(spec, commit=lambda v: _none(ef.apply_org(conn, v, org)),
                          draft_key=draft),
                done,
            )
        elif key == "contacts":
            self.app.push_screen(
                FormModal(ef.contact_form(),
                          commit=lambda v: _none(ef.apply_contact(conn, org.id, v)),
                          draft_key=draft),
                done,
            )
        elif key == "program":
            self.app.push_screen(
                FormModal(ef.placement_form(conn=conn),
                          commit=lambda v: _none(ef.apply_placement(conn, v, org.id)),
                          draft_key=draft),
                done,
            )
        elif key == "projects":
            self._project_then_need(done, draft)
        elif key == "followups":
            self.app.push_screen(
                FormModal(ef.task_form(conn=conn, default_org_id=org.id),
                          commit=lambda v: _none(ef.apply_task(conn, v, org_id=org.id)),
                          draft_key=draft),
                done,
            )
```

with a module-level helper and the two-stage projects flow:

```python
def _none(result: object) -> None:
    """Adapt apply_* return values to the commit contract (None = success)."""
    return None
```

```python
    def _project_then_need(self, done, draft: str) -> None:
        """Project first; on save, chain straight into its first need —
        'thorough capture' means a project never lands needless silently."""
        from ...repo import projects as projects_repo
        from ..widgets import entity_forms as ef
        from ..widgets.forms import FormModal

        conn = self.app.conn

        def project_saved(values: dict | None) -> None:
            if values is None:
                return done(None)
            project = projects_repo.projects_for_org(conn, self.org_id)[0]
            self.app.push_screen(
                FormModal(ef.need_form(conn=conn),
                          commit=lambda v: _none(ef.apply_need(conn, project.id, v)),
                          draft_key=draft + ":need"),
                done,
            )

        self.app.push_screen(
            FormModal(ef.project_form(),
                      commit=lambda v: _none(
                          ef.apply_project(conn, v, org_id=self.org_id)),
                      draft_key=draft),
            project_saved,
        )
```

NOTE for the implementer: check the ACTUAL signatures in `entity_forms.py` before wiring — `apply_placement(conn, values, org_id, placement)` parameter order, `need_form(...)`/`apply_need(...)` argument shapes, and whether `project_form` needs `conn`. The lambdas above must mirror the real signatures (the file is the source of truth; AccountScreen's add-flows around `account.py:760` and `:997` show every correct call). `apply_project`'s newest-project retrieval in `project_saved` must match how `projects_for_org` orders (active first) — safer: capture the created project from `apply_project`'s return value by assigning inside the commit closure:

```python
        created: list = []
        commit=lambda v: _none(created.append(ef.apply_project(conn, v, org_id=self.org_id)))
        # then project_saved uses created[-1]
```

Prefer that pattern; it's exact.

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui.py -k onboarding -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "onboarding: every step drives the shared entity forms, commits in place"`

---

### Task 5: Navigator integration — attention section, `o` binding, resume from rows

**Files:**
- Modify: `src/bookkit/tui/screens/navigator.py` (`refresh_data` :141-167, `_fill_attention_table` :393, `ROW_HINTS` :40, BINDINGS :93, and the row-selected/enter handler below :522)
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Consumes: `onboarding.incomplete_clients` (Task 1), `OnboardingScreen` (Task 3).

- [ ] **Step 1: Failing pilot test** — seed a fresh client with no contacts; assert the attention tree contains an "onboarding" leaf with count 1, and that pressing `o` on the Navigator with that account selected pushes `OnboardingScreen`.

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

In `refresh_data`, after the `late = sla.past_sla(...)` line, add:

```python
        from ...services import onboarding as onboarding_svc

        pending_onboarding = onboarding_svc.incomplete_clients(conn, today)
```

extend `self._attention` with `"onboarding": pending_onboarding`, and add to the attention-leaf tuple:

```python
            ("onboarding", "onboarding incomplete", len(pending_onboarding)),
```

In `_fill_attention_table`, new branch:

```python
        elif which == "onboarding":
            table.add_columns("account", "missing", "since")
            for org, missing in self._attention["onboarding"]:
                key = f"org:{org.id}"
                self._row_org[key] = org.id
                table.add_row(
                    org.name, Text(missing, style=theme.AMBER),
                    org.created_at[:10], key=key,
                )
```

`ROW_HINTS["onboarding"] = "[b]enter[/b] resume onboarding"`.

New binding: `Binding("o", "onboard", "Onboard client", show=False),` and:

```python
    def action_onboard(self) -> None:
        """o — resume onboarding for the selected client, or start a new one."""
        from ..screens.onboarding import OnboardingScreen
        from ..widgets import entity_forms as ef
        from ..widgets.forms import FormModal

        conn = self.app.conn
        kind, payload = self._current
        if kind == "account":
            self.app.push_screen(OnboardingScreen(payload))
            return
        created: list = []

        def done(values: dict | None) -> None:
            if values is not None and created:
                self.app.push_screen(OnboardingScreen(created[-1].id))

        def commit(values: dict) -> str | None:
            # spec's duplicate guard: refuse a near-duplicate name in place —
            # the form stays open so Grant can rename, or esc and resume the
            # existing client from the onboarding attention list instead
            from rapidfuzz import fuzz, process

            existing = {o.name: o for o in orgs.list_orgs(conn, kind="client")}
            match = process.extractOne(
                values["name"], list(existing), scorer=fuzz.WRatio, score_cutoff=87)
            if match:
                dup = existing[match[0]]
                return f"looks like {dup.name} ({dup.ref}) — rename, or esc and resume it"
            created.append(ef.apply_org(conn, v=values))
            return None

        self.app.push_screen(FormModal(ef.org_form(conn=conn), commit=commit), done)
```

(`apply_org(conn, values)` — positional, match the real signature; the lambda-free
form above is clearer anyway. `orgs` is already imported at navigator.py's top.)

Row-level resume: find the enter/row-selected handler for the attention table (below :522 — the same path that opens accounts from `self._row_org`). Where it resolves a row key, add: when the key starts with `"org:"` AND the current attention list is `"onboarding"`, push `OnboardingScreen(org_id)` instead of `open_account`.

On return from the wizard, `on_screen_resume` already calls `refresh_data()` — counts update for free.

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "navigator: onboarding attention feed + o to start/resume the wizard"`

---

### Task 6: Inline team-member creation from the assignment form

Grant's ask: assigning someone who isn't in the team list yet must not dead-end — create them inline and complete the assignment.

**Files:**
- Modify: `src/bookkit/tui/widgets/entity_forms.py` (`assignment_form` :369)
- Modify: `src/bookkit/tui/screens/account.py` (the assign flow ending at :1274)
- Test: `tests/test_tui_forms.py` (append)

**Interfaces:**
- Produces: `assignment_form(member_options, ...)` appends a `("+ new team member…", "__new__")` option; `NEW_MEMBER = "__new__"` exported from `entity_forms`.

- [ ] **Step 1: Failing pilot test**

```python
async def test_assign_unknown_member_creates_inline(seeded_db: Path) -> None:
    """Choosing '+ new team member…' in the who-select chains into the member
    form; saving it creates the member AND completes the assignment."""
    # open an account's team tab, press the assign key, pick "__new__",
    # ctrl+s, fill the member form (name), ctrl+s, then assert:
    #   team.list_members contains the new name
    #   team.for_org(org) shows the assignment
```

Write it against the real key path: read `account.py`'s team-tab bindings first (the file's tab-action wiring around the `"tab-overview"` map at :84 and the assign flow at :1274) and mirror how `test_tui_forms.py` drives tabs (`TabbedContent` is already imported there).

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

`entity_forms.py`:

```python
NEW_MEMBER = "__new__"
```

and in `assignment_form`, replace the member_options branch:

```python
    if member_options:
        fields.append(
            Field(
                "team_member_id", "who", "select",
                (*member_options, ("+ new team member…", NEW_MEMBER)),
                required=True,
            )
        )
```

`account.py` assign flow (:1274 area): the current `commit` calls `team.assign(...)` directly. Change it to pass through when the sentinel is chosen, and let `done` chain into the member form — the member form's own commit then creates AND assigns in one transaction, so a refused member save keeps that form open with input intact:

```python
        def commit(values: dict) -> str | None:
            if values["team_member_id"] == ef.NEW_MEMBER:
                return None  # nothing to write yet — done() chains to the member form
            # ... existing assign call unchanged ...

        def done(values: dict | None) -> None:
            if values is None:
                return
            if values["team_member_id"] == ef.NEW_MEMBER:
                self._create_member_then_assign(values)
                return
            # ... existing success notify/refresh unchanged ...

    def _create_member_then_assign(self, assignment: dict) -> None:
        from ...repo import team
        from ...db import transaction
        from ..widgets import entity_forms as ef
        from ..widgets.forms import FormModal

        conn = self.app.conn

        def commit(values: dict) -> str | None:
            from ..widgets.forms import dropped

            core = dropped(values)
            name = core.pop("name")
            with transaction(conn):
                member = team.create_member(conn, name, **core)
                team.assign(
                    conn, member.id, self.org_id,
                    role=assignment.get("role"),
                    lines=assignment.get("lines"),
                    notes=assignment.get("notes"),
                )
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.notify("member created and assigned")
                self.refresh_data()

        self.app.push_screen(
            FormModal(ef.member_form(conn=conn), commit=commit), done
        )
```

NOTE for the implementer: `team.assign`'s real signature is at `repo/team.py:48` — mirror its parameter names exactly (the existing commit at account.py:1274 shows the correct call shape; reuse it verbatim inside the transaction). `self.org_id` — use whatever attribute the surrounding flow uses for the account's org id.

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui_forms.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "team: '+ new team member' inline from the assignment form — create and assign in one transaction"`

---

### Task 7: Help screen + final gates

**Files:**
- Modify: `src/bookkit/tui/screens/help.py` (add `o` onboard + `x` export to whatever key tables it renders — read the file, follow its format)

- [ ] **Step 1: Add the new keys to help** (`o` onboard client — Navigator; `x` export open items — Navigator; the wizard's own keys are in its footer/hints already).

- [ ] **Step 2: Full gates**

```bash
uv run pytest -q && uv run mypy src && uv run ruff check src tests
```

- [ ] **Step 3: Commit** — `git commit -m "help: document o (onboard) and x (export) navigator keys"`

- [ ] **Step 4: Fresh-eyes review** — per process, run a review pass (fresh-eyes-review skill) over the whole branch before declaring done. Specific things to re-check: the 60-day `ONBOARDING_WINDOW_DAYS` fence (flagged for Grant), draft restore vs Select option drift, and that no wizard path forked form wiring instead of reusing entity_forms.

---

### Task 8: shared export flow in entity_actions (feature add 2026-08-13)

The client open-items tab (Task 9) and the Navigator both export the same workbook; the flow moves to the shared home BEFORE the second caller exists.

**Files:**
- Modify: `src/bookkit/tui/widgets/entity_actions.py`, `src/bookkit/tui/screens/navigator.py` (action_export_row)
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Produces: `entity_actions.export_open_items_flow(screen: Screen, org_id: str) -> None` — resolves the org (KeyError → notify error), writes `<ref>-open-items-<today>.xlsx` to CWD via `services.export_open_items.write` (OSError → notify error), notifies the path on success, calls `_refresh(screen)` is NOT needed (export mutates nothing).

- [ ] **Step 1: Failing test** — a pilot test that calls the new flow directly on a mounted NavigatorScreen with a seeded client (monkeypatch.chdir(tmp_path)) and asserts the file exists + a soft-deleted org notifies instead of raising. Mirror the existing Task-9 export test in tests/test_tui.py.

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement** — move the body of `NavigatorScreen.action_export_row`'s guarded org-lookup + write into `entity_actions.export_open_items_flow` (module style: lazy imports inside the function, `_app(screen)` for conn, `screen.notify` for messages — read the file's existing flows and match). `action_export_row` becomes: resolve org id from the current node/row (unchanged logic), then `entity_actions.export_open_items_flow(self, org_id)`.

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "tui: export-open-items flow shared via entity_actions"`

---

### Task 9: AccountScreen "Open items" tab — the datasheet

**Files:**
- Modify: `src/bookkit/tui/screens/account.py` (TAB_HINTS :55, TAB_TABLES :83, tab bindings :207-213, TabbedContent compose :222, the per-tab add/edit/done routing around :760 and :828, refresh_data)
- Test: `tests/test_tui.py` (append)

**Interfaces:**
- Consumes: `tasks.open_tasks_for_client` (landed on the export branch), `grouped_by_category`/`task_detail_cell` from `widgets/tables.py`, `projects_repo.projects_for_org`+`needs_for_project`, `submissions.outstanding_for_org`, `entity_actions.export_open_items_flow` (Task 8), TASK_INLINE pattern from navigator.py.

- [ ] **Step 1: Failing pilot test**

```python
async def test_open_items_tab_datasheet(seeded_db: Path, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = BookkitApp(seeded_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    p = placements.for_org(app.conn, org.id)[0]
    tasks_repo.create(app.conn, "placement task", placement_id=p.id, category="Renewal")
    async with app.run_test(size=(150, 44)) as pilot:
        app.push_screen(AccountScreen(org.id))
        await pilot.pause()
        await pilot.press("8")
        await pilot.pause()
        table = app.screen.query_one("#open-items-table")
        assert table.has_focus                      # focus lands IN the datasheet
        titles = [str(table.get_row_at(i)[1]) for i in range(table.row_count)]
        assert "placement task" in titles           # placement-owned included
        await pilot.press("x")
        await pilot.pause()
        assert list(tmp_path.glob("*-open-items-*.xlsx"))
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

1. `TAB_HINTS["tab-open-items"] = ("[b]i[/b] edit in cell · [b]a[/b] task · [b]e[/b] edit form · [b]d[/b] done · [b]x[/b] export · needs/submissions edit in their tabs")`; `TAB_TABLES["tab-open-items"] = "open-items-table"`; `Binding("8", "show_tab('tab-open-items')", "Open items", show=False)`.
2. Compose: new `TabPane("Open items", id="tab-open-items")` after Documents containing `InlineTable(id="open-items-table")` and `ListTable(id="open-items-context")` with a dim label between ("other open items — edit in their tabs").
3. Fill (called from refresh_data like every other tab): datasheet columns `("due", "task", "category", "description", "detail", "status")`, rows from `grouped_by_category(tasks.open_tasks_for_client(conn, org.id))`, cells rendered exactly like navigator's group-tasks branch (date_text/days, title, category amber, description, task_detail_cell, status_text); `inline_fields` = the TASK_INLINE mapping shifted to this column order. Context table: needs via `projects_for_org`+`needs_for_project` filtered to `ATTENTION_STATUSES`, submissions via `outstanding_for_org`; columns `("kind", "item", "due / needed", "status", right("days"))`.
4. Route the existing per-tab verbs: in the `action_add` tab dispatch, `tab-open-items` → same task_form flow as overview (org pre-attached); `e`/`d` operate on the datasheet's selected task via the same `_selected_key` pattern the overview uses (read :828 and mirror). Add `Binding("x", ...)` scoped in the screen's action to fire only when the active tab is open-items (mirror how other per-tab keys guard) → `entity_actions.export_open_items_flow(self, self.org_id)`.
5. Focus: in the tab-activated handler (find where tab switches focus the mapped table — TAB_TABLES already drives this), ensure open-items focuses the datasheet.

- [ ] **Step 4: Run** — `uv run pytest tests/test_tui.py tests/test_tui_forms.py -q 2>&1 | tail -3` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "account: open-items tab — client task datasheet + context items + export"`

---

### Task 10: open-items tab — inline edit coverage, help, gates

**Files:**
- Modify: `src/bookkit/tui/screens/help.py`
- Test: `tests/test_tui.py` (append)

- [ ] **Step 1: Failing pilot test** — on the open-items tab, `i` on the category cell of the first task row, type a new category, enter; assert the task's category persisted and the row regrouped. Mirror navigator's existing inline-edit test if one exists (check test_tui.py); otherwise drive via keys: focus row, `i`, type, enter.

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement** — whatever wiring gap the test exposes (expected: none beyond Task 9's inline_fields; this test is the guard). Add the open-items tab keys to help.py in its existing format ("8 open items · i in-cell edit · x export").

- [ ] **Step 4: Full gates** — `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests` → all green.

- [ ] **Step 5: Commit** — `git commit -m "account: open-items inline-edit guard + help"`
