# Forms commit-in-place Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No form dismisses until its save succeeded; `l` edits the visible layer directly; every client table shows what's expiring.

**Architecture:** `FormModal` gains a `commit` callback (save-in-place, error string keeps the form open). All 17 call sites move their save into `commit` — three shapes: the account `_push_form` helper (fix once), plain repo saves, write-through saves returning Diagnostics. Layer editing resolves from the focused carriers row before falling back to single-layer skip, then picker. The placements table splits `period` into effective/expires/days with soonest-first sort and warning/error styling; the sync-state line gains the deal team.

**Tech Stack:** Textual (existing), pytest + run_test pilots.

## Global Constraints

- Branch `forms-commit`, repo bookkit. mypy strict outside `tui.*`; ruff clean; `uv run pytest -q` green before every commit.
- Values contract of FormModal is unchanged: dismiss still delivers `{key: parsed}` or `None`.
- Commit callbacks must never raise into Textual — exceptions become the error string.

---

### Task 1: `FormModal(spec, commit=...)`

**Files:** Modify `src/bookkit/tui/widgets/forms.py:58-133`; test `tests/test_tui_forms.py` (append).

**Interfaces — Produces:** `FormModal(spec, commit: Callable[[dict[str, Any]], str | None] | None = None)`. In `action_save`, after the existing parse loop succeeds: if `commit` is set, run it inside try/except; a returned string or a raised exception → `self.notify(msg, severity="error")` and return (form stays, input intact); `None` → `self.dismiss(values)`.

- [ ] Test (append to tests/test_tui_forms.py, following its existing pilot style):

```python
async def test_form_commit_refusal_keeps_form_open(tmp_path) -> None:
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.forms import Field, FormModal, FormSpec

    app = BookkitApp(tmp_path / "t.db")
    outcomes: list[str | None] = ["refused: gap under layer", None]
    committed: list[dict] = []

    def commit(values: dict) -> str | None:
        committed.append(values)
        return outcomes.pop(0)

    spec = FormSpec("edit layer", [Field("name", "name", required=True)])
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(FormModal(spec, commit=commit))
        await pilot.pause()
        app.screen.query_one("#form-name").value = "Primary"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)      # refused → still open
        assert app.screen.query_one("#form-name").value == "Primary"  # input intact
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, FormModal)  # success → dismissed
        assert len(committed) == 2


async def test_form_commit_exception_is_an_error_not_a_crash(tmp_path) -> None:
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.forms import Field, FormModal, FormSpec

    app = BookkitApp(tmp_path / "t.db")

    def commit(values: dict) -> str | None:
        raise RuntimeError("db locked")

    spec = FormSpec("x", [Field("name", "name")])
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(FormModal(spec, commit=commit))
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, FormModal)  # still alive
```

- [ ] Implement in forms.py: `__init__` stores `self._commit = commit`; end of `action_save` becomes:

```python
        if self._commit is not None:
            try:
                error = self._commit(values)
            except Exception as exc:  # a failed save must never crash the TUI
                error = str(exc)
            if error is not None:
                self.notify(error, severity="error")
                return
        self.dismiss(values)
```

- [ ] `uv run pytest tests/test_tui_forms.py -q` green; mypy/ruff clean; commit `forms: commit-in-place — a form never closes on a failed save`.

### Task 2: migrate account.py (helper + write-through forms)

**Files:** Modify `src/bookkit/tui/screens/account.py` (sites at lines ~425, 716, 772, 857, 913, 988, 1038 — line numbers drift; find by `FormModal(`).

Three sub-shapes, exact transformations:

1. `_push_form` (covers all entity add/edit tabs): move `on_save` into commit:

```python
    def _push_form(self, spec, on_save) -> None:
        from ..widgets.forms import FormModal

        def commit(values: dict) -> str | None:
            on_save(values)  # raises → FormModal shows it and stays open
            return None

        def done(values) -> None:
            if values is not None:
                self.refresh_data()

        self.app.push_screen(FormModal(spec, commit=commit), done)
```

(on_save lambdas already notify; keep them as-is.)

2. Write-through forms (edit layer, add layer, linked placement edit, bind share, scaffold): commit runs the sync call and converts diagnostics to the error string; the dismiss callback keeps success notify + refresh. Representative (edit layer):

```python
            def commit(values: dict) -> str | None:
                diags = sync.update_layer(
                    self.app.conn, placement.id, layer_id,
                    name=values.get("name"),
                    policy_number=values.get("policy_number"),
                    attach_cents=values.get("attach"),
                    limit_cents=values.get("limit"),
                    premium_cents=values.get("premium"),
                    period_from=values.get("period_from"),
                    period_to=values.get("period_to"),
                )
                return f"refused: {diags.errors[0]}" if not diags.ok else None

            def done(values) -> None:
                if values is not None:
                    self.notify(f"updated {layer['name']}")
                    self.refresh_data()

            self.app.push_screen(FormModal(spec, commit=commit), done)
```

Apply the same split to every `saved()` that calls `sync.*` + `_apply_write_through`; `_apply_write_through` then has no error path left — inline the success half and delete it if unused.

3. `assignment_form` site (team assign) and any remaining `saved` doing repo work: same commit/done split as shape 2 minus diagnostics (repo raises → caught by Task 1's wrapper).

- [ ] Migrate every site; grep `FormModal(` in account.py must show `commit=` on all.
- [ ] Full suite + pilot snapshot tests green; commit `account: all saves commit in place`.

### Task 3: migrate today.py, team.py, markets.py, book.py

Same commit/done split for the 10 sites listed by `grep -rn "FormModal(" src/bookkit/tui`. Each `saved(values)` body that does repo work becomes `commit` (return None); notify/refresh stay in `done`. No site may keep repo/sync calls in the dismiss callback.

- [ ] Migrate; grep check: every `FormModal(` in tui/ carries `commit=`.
- [ ] Full suite green; commit `tui: commit-in-place is the default for every form`.

### Task 4: smart `l`

**Files:** Modify `src/bookkit/tui/screens/account.py` (`_refresh_placements` carriers-table build, `action_edit_layer`); test `tests/test_tui.py` (append pilot).

- [ ] carriers-table rows: `key=<layer_id>` (first row per layer keeps it; participant rows of the same layer reuse the layer id key — DataTable requires unique keys, so key participant rows `f"{layer_id}:{carrier}"` and store a `dict` row→layer via key prefix; resolution = key.split(":")[0]). Layers with no participants get a placeholder row `("— to be placed —", layer_name, "", "")` keyed `layer_id`.
- [ ] `action_edit_layer` resolution order, replacing the unconditional picker:

```python
        focused = self.focused
        carriers_table = self.query_one("#carriers-table", ListTable)
        if focused is carriers_table and carriers_table.row_count:
            row_key = carriers_table.selected_key()          # existing helper; else coordinate_to_cell_key
            if row_key:
                picked(str(row_key).partition(":")[0])
                return
        if len(layers) == 1:
            picked(str(layers[0]["id"]))
            return
        # fall through to the existing Picker
```

(Check `ListTable` in `tui/widgets/tables.py` for the selected-key helper's real name before coding.)

- [ ] Pilot test: seeded account → placements tab → focus carriers table → `l` opens FormModal titled with that layer's name; a single-layer program opens the form without a Picker on the screen stack.
- [ ] Suite green; commit `account: l edits the layer under the cursor; single-layer programs skip the picker`.

### Task 5: expiry columns + deal team line

**Files:** Modify `src/bookkit/tui/screens/account.py` (`_refresh_placements`, `show_placement`); test `tests/test_tui.py` (append).

- [ ] Placements table:

```python
        table.add_columns("ref", "program", "effective", "expires", "d", "status", "premium")
        rows = sorted(placements.for_org(conn, org_id), key=lambda p: p.period_to)
        today_iso = date.today()
        for p in rows:
            days = days_until(p.period_to, today_iso)
            expires = p.period_to
            if days < 0:
                expires = f"[red]{p.period_to}[/red]"
            elif days <= 60:
                expires = f"[yellow]{p.period_to}[/yellow]"
            table.add_row(
                p.ref, p.program_name, p.period_from, expires, str(days), p.status,
                format_cents_compact(p.total_premium) if p.total_premium else "—",
                key=p.id,
            )
```

- [ ] `show_placement` (the sync-state updater): append the deal team from `team_repo.for_org` rows where `placement_id == placement.id` (already fetched shape: member_name, role): `team: Rosa Silva (placement_specialist), Ken Ito (analyst)`; omit the segment when empty.
- [ ] Tests: pilot asserts the placements table has an `expires` column and rows sorted ascending by expiry; a placement-scoped assignment shows in the sync-state text.
- [ ] Suite green; commit `account: expiry columns + deal team where the deal lives`.

### Task 6: numeric-date fast path (towerkit dates.py) — fixes 6/24/26 → 2126

**Repro:** `parse_flexible_date("6/24/26") == date(2126, 6, 24)` — dateparser
resolves the 2-digit year to 2026, sees the date is past, and PREFER_DATES_FROM
"future" bumps a century. Every slashed 2-digit-year date earlier in the year
is silently 100 years off.

**Files:** Modify `/Users/grantgreeson/Developer/towerkit/src/towerkit/dates.py`; test `tests/test_dates.py` (towerkit). Commits go to the towerkit repo.

- [ ] Tests (append to towerkit tests/test_dates.py):

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6/24/26", date(2026, 6, 24)),     # MDY, 2-digit year — Grant's habit
        ("06/24/26", date(2026, 6, 24)),
        ("6/24/2026", date(2026, 6, 24)),
        ("6-24-26", date(2026, 6, 24)),
        ("12/31/25", date(2025, 12, 31)),   # past date must NOT jump a century
        ("24/6/26", date(2026, 6, 24)),     # month>12 → the only valid reading is DMY
        ("2/3/26", date(2026, 2, 3)),       # ambiguous → MDY wins, consistently
    ],
)
def test_numeric_dates_are_mdy_with_20xx_years(text: str, expected: date) -> None:
    assert parse_flexible_date(text) == expected


def test_flexible_forms_still_parse() -> None:
    assert parse_flexible_date("Jan 15 2026") == date(2026, 1, 15)
    assert parse_flexible_date("2026-10-15") == date(2026, 10, 15)
```

- [ ] Implement in towerkit dates.py, before the dateparser call:

```python
_NUMERIC_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})$")


def _parse_numeric(text: str) -> date | None:
    """M/D/YY(YY) fast path: MDY by convention, 2-digit years are 20xx —
    dateparser's future-preference turns past 2-digit years into 21xx."""
    match = _NUMERIC_RE.match(text)
    if match is None:
        return None
    first, second, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    month, day = first, second
    if month > 12 and day <= 12:  # 24/6 can only be day-first
        month, day = day, month
    try:
        return date(year, month, day)
    except ValueError:
        return None
```

Call it in `parse_flexible_date` after the ISO fast path; on None, fall through to dateparser unchanged (keeps "fri", "15 Oct", month names robust). Add `import re` at top.

- [ ] towerkit suite + mypy + ruff green; commit in towerkit `dates: numeric M/D/YY fast path — no more century jumps`; then run bookkit's suite (path dep) to confirm nothing downstream shifted.

### Task 7: task from anywhere, associated with a client

**Today:** `task_form` has no account field (Today's `a` creates org-less
tasks); QuickCapture only OFFERS a task when the note contains a follow-up
phrase. No path exists to "task for client X" from an arbitrary screen.

**Files:** Modify `src/bookkit/tui/widgets/entity_forms.py:137-150` (task_form),
`src/bookkit/tui/app.py` (global binding), `tests/test_tui.py` (append).

- [ ] `task_form(existing, *, conn, default_org_id=None)` gains an account
  Select: options `[(org.name, org.id) for org in orgs.list_orgs(conn, kind="client")]`,
  `optional_select=True`, key `org_id`, initial = existing.org_id or
  default_org_id. `apply_task` passes `values["org_id"]` through (it already
  accepts org_id — reconcile the two paths so the form value wins when set).
  Update the two Today call sites and the account-tab call site (which keeps
  its own org as the default).
- [ ] App-level `Binding("ctrl+t", "new_task", "Task")` on BookkitApp:
  `action_new_task` pushes `FormModal(task_form(conn=self.conn,
  default_org_id=getattr(self.screen, "current_org_id", None)), commit=...)`
  (commit-in-place per Task 1), refusing to open when a modal is already up
  (same `_modal_open()` guard as quick capture).
- [ ] Pilot test: from a seeded account screen, `ctrl+t` → form shows with the
  account pre-selected → fill title → ctrl+s → task exists with that org_id;
  from Today with no org context, the select is blank and the task saves
  org-less.
- [ ] Suite green; commit `tasks: ctrl+t from anywhere, client attached by default`.

### Task 8: towerkit editor — esc with changes offers Save

**Today:** towerkit's editor exit-confirm offers only keep-editing/abandon;
there is no save-and-exit choice at that prompt.

**Files:** towerkit repo — find the dirty-exit confirm in
`src/towerkit/tui/` (grep `abandon|discard|unsaved|dirty` across tui/*.py);
modify that modal + its caller; test in towerkit `tests/test_tui.py`
following its existing pilot style.

- [ ] Read the existing modal first; extend it to three explicit choices:
  `s` **Save and exit** (runs the editor's existing save action, then exits;
  if the save is refused — validation errors — stay in the editor with the
  refusal shown, do NOT exit or discard), `d` **Discard and exit** (current
  abandon behavior), `esc` **Keep editing**. Bindings + buttons for all three.
- [ ] Pilot test: edit a field → esc → press `s` → file on disk carries the
  edit and the app exited the editor screen; edit → esc → `d` → file
  unchanged; edit that fails validation → esc → `s` → still in editor,
  error shown, file unchanged.
- [ ] towerkit suite/mypy/ruff green; commit in towerkit
  `tui: esc with changes offers save-and-exit`.

## Self-review notes

- Spec coverage: FormModal default (T1–T3), smart `l` (T4), expiry visibility + team line (T5). Book/Today expiry untouched per spec.
- T4's selected-key helper name is verified in-code before writing (tables.py is 694 bytes).
