<!-- DRAFT — NOT APPROVED. Read the verification report at the bottom before building from this. -->

> **Status: DRAFT — verified sound** (2026-08-18).
> Produced by a drafting pass, then checked by an independent adversarial pass that
> opened every citation and challenged the load-bearing claims.
> **67 citations checked · 4 failed · 6 claims challenged.**
> Kind: `task-brief`.
> The verification report at the bottom is PART OF THIS DOCUMENT — some of its findings
> would break an implementation built from the body above it.

---

# Internal-only tasks, excluded from the client export — task brief

Date: 2026-08-18
Status: buildable as written. Intended path
`docs/superpowers/specs/2026-08-18-internal-only-tasks-brief.md`.
Source: ROADMAP.md, first entry, 2026-08-18. Grant's shape call stands —
**no schema change, no new form field, sheet 1 only**.

This is a task-brief rather than a spec because the only structural
question the ROADMAP left genuinely open (where the filter lives) is
answered by the code: `compose()` is the one place every sheet-1 task row
passes through, and it already receives the whole task list before it
splits it into sections. Everything remaining is a naming call, a match
rule, and four surfaces to tell. One decision is Grant's and is listed at
the bottom; the build does not wait on it.

## What the code actually says

Verified before drafting, because the ROADMAP entry reasons from a
docstring that the code contradicts.

- `compose()` fetches **one** task list —
  `tasks_repo.open_tasks_for_client(conn, org.id)`
  (`services/export_open_items.py:98`) — and then splits it two ways:
  org-level tasks bucketed by category (`:104-111`) and placement-attached
  tasks bucketed by placement (`:112-115`). Both halves land on sheet 1.
- **No section is ever emitted empty.** `by_category` only ever holds
  non-empty lists (`:108-109`); General, the placement sections and the
  project sections are each behind an explicit guard (`:142`, `:157`,
  `:167`). `tests/test_services.py:365-367` asserts `compose(...) == []`
  for an account with nothing open.
- The docstring's "always present, even when empty"
  (`services/export_open_items.py:8`) is about **sheet 1**, not the
  sections: `write()` renders sheet 1 unconditionally, falling back to a
  single `No open items as of <date>` row (`:377-378`,
  asserted by `tests/test_services.py:428-434`), in contrast to sheets
  2/3/4 which are omitted rather than blank.
- `compose()` has a **second caller**: MCP's per-client `open_items`
  (`mcpserver.py:2243`), whose tool docstring claims the result "matches
  what a client would be handed" (`mcpserver.py:171-184`).
- `Task.category` is a freeform nullable string
  (`models.py:197`), suggested from existing rows only
  (`repo/vocab.py:62-63`, wired at `forms/entities.py:187`).
- `ExportRow` already carries a field the workbook deliberately does not
  print: `ref` (`services/export_open_items.py:44-47`), because `write()`
  builds its rows from an explicit six-column tuple (`:373`). That is the
  precedent for adding a display-only flag.
- `task.placement_id` is written by exactly one function today —
  `repo/tasks.py:69-82`, the placement-merge reassignment. No form, no MCP
  tool (`mcpserver.py:878-910`) and no importer sets it. It is
  nevertheless a supported shape: `open_tasks_for_client` exists precisely
  to catch `org_id IS NULL` placement tasks (`repo/tasks.py:44-55`) and
  `tests/test_services.py:340-348` asserts they reach the workbook.

## Corrections to the ROADMAP entry

1. **"check the empty-section handling: sections are 'always present, even
   when empty' per the module docstring, and an Internal header with
   nothing under it would defeat the point."** The risk does not exist.
   Removing every Internal task removes the whole section, because the
   section is only created from a non-empty bucket
   (`services/export_open_items.py:108-109`, `:142`, `:157`, `:167`;
   `tests/test_services.py:365-367`). The docstring phrase describes the
   sheet, which is always rendered with a placeholder row (`:377-378`).
   The real case to pin is the second-order one: an account whose *only*
   open items are Internal now exports the `No open items as of <date>`
   sheet. That is correct and gets a test.
2. **"It is one filter in the composition."** It is one filter plus a
   parameter. `compose()` is shared with MCP's per-client `open_items`
   (`mcpserver.py:2243`), so a bare filter silently changes what Grant's
   own assistant can see about an account — the exact class of hidden
   behaviour this feature exists to avoid.
3. **"sheet 1 is org-level tasks by category."** Sheet 1 also carries a
   section per placement, built from the same task list with category
   ignored (`services/export_open_items.py:112-115`, `:147-160`). If the
   filter is applied inside the category branch, an Internal task that
   carries a `placement_id` still ships to the client. Unreachable through
   today's UI, but supported by the repo and asserted by an existing test —
   so the filter goes on the task list, not on the category branch.

## Decisions

### D1 — The match rule is exact equality on the trimmed, lowercased value

`category.strip().lower() == "internal"`. "internal", "Internal ",
"INTERNAL" all count. **"Internal Review" does not.**

**Rejected: a prefix match (`startswith("internal")`).** The two rules
fail in opposite directions and only one failure is visible:

- Equality, user types "Internal Review": the task **ships**, under a
  section header in the client's workbook literally titled "Internal
  Review". Loud, and caught by D5's row badge before the export is ever
  run — the row shows no badge, so the flag visibly did not take.
- Prefix, user types "Internal audit support" (a real client-facing
  broking task): the task **silently vanishes** from the deliverable.
  Nothing in the workbook says a section was removed, and the row badge
  would say "not exported" on a task that should have been exported —
  which the user has no reason to look at, because they did not think they
  were flagging anything.

A silent wrong exclusion is worse than a loud wrong inclusion, and
prefix-matching a freeform user string is guessing at intent — the same
reasoning `parse_human_date` uses to refuse a bare number rather than
guess a month (CLAUDE.md). The badge in D5 is what makes equality safe; it
is load-bearing, not decoration.

**Cost if wrong:** an internal note reaches a client once, in a section
whose header names it, because someone typed a longer category and did not
look at the row. Recoverable and self-teaching. The prefix alternative's
cost is a client deliverable that is quietly incomplete, with no signal
anywhere.

### D2 — The name and the predicate are declared in `models.py`

```python
# The one category that never leaves the building: a task filed under it is
# excluded from the client-facing export (services/export_open_items).
INTERNAL_CATEGORY = "Internal"


def is_internal_category(category: str | None) -> bool:
    return category is not None and category.strip().lower() == INTERNAL_CATEGORY.lower()
```

Four consumers import that one function: the export composition, the
vocabulary, the TUI theme helper, the web row builder. This is
CLAUDE.md's "declare the name, don't patch the symptom" applied to a
vocabulary instead of an event-log field — and `models.py` is already
where controlled-but-extensible vocabularies live (`models.py:242-244`,
`:381-388`).

**Rejected: owning the constant in `services/export_open_items.py`.** That
module imports towerkit at module scope (`:26-27`), so a TUI row renderer
or a web route importing it for one string drags the workbook stack into
the import graph. **Rejected: `normalize.py`** — that module cleans input
on the way in, it does not classify (`normalize.py:1-12`).
**Rejected: duplicating the literal in `tui/` and `web/`** —
`tests/test_conventions.py:37-45` exists because copied helpers are how the
two surfaces drift.

**Cost if wrong:** the string is defined in one place and used in four; a
wrong home costs one import edit.

### D3 — The filter sits on the task list in `compose()`, behind a client-safe default

```python
def compose(conn, org_id, today, *, include_internal: bool = False) -> list[ExportSection]:
    ...
    org_tasks = tasks_repo.open_tasks_for_client(conn, org.id)
    if not include_internal:
        org_tasks = [t for t in org_tasks if not is_internal_category(t.category)]
```

One statement, immediately after `:98`, before both the category loop and
the placement loop — so it covers org-level rows, General, and placement
sections alike (correction 3). Submissions and project needs have no
category and are untouched. `write()` needs no change: it inherits the
default.

The default is **exclude**, so a future caller that composes anything
client-facing inherits the safe behaviour without knowing this feature
exists. A caller that wanted the internal rows and forgot to ask gets a
visibly missing task; a caller that wanted them excluded and forgot gets a
leak nobody sees.

**Rejected: filtering in `write()` instead.** `write()` only sees
`ExportSection`s, so it would have to match on the rendered label string
`f"{category} — {org.name}"` (`:129-132`) — parsing a display string to
recover a data fact, and it would still miss the placement sections.
**Rejected: filtering in towerkit's renderer** — per the ROADMAP and per
towerkit's own rule that it learns no CRM concepts.

**Cost if wrong:** if the default should have been "include", every
non-export caller must pass the flag; the compiler does not help, but
there is exactly one such caller today (`mcpserver.py:2243`).

### D4 — `ExportRow` gains `internal: bool = False`; the workbook still cannot print it

Set in `_task_row` from `is_internal_category(task.category)`. `write()`'s
explicit column tuple (`:373`) does not include it, so it cannot reach the
client — the same mechanism, and the same guarantee, that `ref` already
documents at `:44-47`. `mcpserver`'s `asdict(r)` (`:2242`) picks it up for
free, so an internal row is labelled wherever it is shown.

**Cost if wrong:** a dataclass field nothing reads. Cheap to remove.

### D5 — Both surfaces say "not exported" on the row

The fact belongs to the task, not to a screen, so it is marked everywhere
the category is rendered.

**TUI** — one helper in `tui/theme.py`, next to `status_text`
(`theme.py:91-92`):

```python
def category_text(category: str | None) -> Text:
    if category is None:
        return dash()
    if is_internal_category(category):
        return Text(f"{category} ⊘", style=DIM)   # ⊘ = not in the client export
    return Text(category, style=AMBER)
```

Replaces the four inline `Text(t.category, style=theme.AMBER) if
t.category else dash()` expressions: `screens/account.py:681`, `:742`,
`screens/navigator.py:702`, `:805`. The legend goes in the Open Items tab
hint (`screens/account.py:103-106`) as **plain text, never `[b]…[/b]`** —
`tests/test_dead_keys.py:21-32` treats any bolded token of two characters
or fewer as an advertised key binding.

Two traps, both real:

- `_ov_detail_width`'s `widest()` measures `t.category` raw
  (`screens/account.py:161`) to size the auto-width columns before dividing
  the remainder into the detail column. It must measure the rendered label
  (`theme.category_text(t.category).plain`) or the overview row overflows
  by the width of the glyph.
- The Open Items table is an `InlineTable` with an editable category column
  (`screens/account.py:733-742`, `forms/inline.py:24-29`). Decorating the
  cell is safe: the editor prefills from `inline_initial`, which reads the
  model (`widgets/inline_edit.py:99-103`,
  `screens/account.py:1209-1213`), never the displayed text.

**Web** — a suffix on the category cell, mirroring `_task_due_suffix`
exactly (`web/routes/work.py:79-83`):

```python
def _task_category_suffix(task: Task) -> str:
    return ('<span class="tag-internal">not exported</span>'
            if is_internal_category(task.category) else "")
```

Passed at `web/routes/work.py:99` and in `_task_display_cell` (`:138-147`)
so the badge appears the moment an inline edit to "Internal" is saved.
`suffix` renders inside the cell's own `<td>` — never as a sibling; the
macro's docstring records what nesting a second `<td>` did on 2026-08-18
(`web/templates/macros/cell.html:20-52`). CSS: `.tag-internal` beside
`.tag-overdue` (`web/static/app.css:930-937`), `color: var(--muted)`.

Not touched: `_item_row`'s `category_cell` (`web/routes/work.py:337`) is
an RFI item's category on sheet 2. Out of scope, deliberately.

**Cost if wrong:** the word is wrong or the glyph is ugly; a text edit. The
cost of *omitting* it is D1's failure mode losing its only visible signal.

### D6 — The vocabulary always offers "Internal"

`repo/vocab.py:62-63` returns `DISTINCT` categories from existing rows, so
on a fresh book nothing offers "Internal" and nobody discovers the
feature. `task_categories` unions the constant in and its docstring says
so:

```python
def task_categories(conn: sqlite3.Connection) -> list[str]:
    """Existing task categories PLUS the well-known Internal category —
    the flag has to be offered before anyone has typed it once."""
    return _dedupe([*_column(conn, "task", "category"), INTERNAL_CATEGORY])
```

`_dedupe` already strips and folds case, first spelling wins
(`repo/vocab.py:13-19`), so an existing "internal" keeps its spelling and
does not gain a sibling.

It goes in `repo/vocab.py`, not in the form, for the same reason the team
name guard lives in `repo/team.py`: every surface inherits it. Today that
is one caller (`forms/entities.py:187`), which feeds both the TUI
autocomplete and the web `<datalist>`
(`web/templates/macros/form.html:19-23`).

**Known limitation, not fixed here:** the *inline* category cell offers no
completion on either surface — `forms/inline.py:24-29` builds
`TASK_FIELDS` as a module constant with no `conn` to query, and Textual's
`CellEditor` is a plain `Input` (`widgets/inline_edit.py:169`). So the
suggestion lands in the add/edit **modal** only. Wiring suggestions into
inline cells is a separate change.

**Cost if wrong:** `tests/test_repo.py:378` asserts the exact list and
must be updated to `["Certificates", "Internal", "Renewal"]`. If the union
is wrong, that test says so immediately.

### D7 — The export itself reports what it withheld

`screen.notify(f"wrote {path}")` (`tui/widgets/entity_actions.py:282`) and
`print(f"wrote {path}")` (`cli.py:426`) gain a suffix when anything was
held back: `wrote ACME-open-items-2026-08-18.xlsx — 2 internal tasks
withheld`. Backed by a pure sibling in the service:

```python
def withheld_internal(conn, org_id) -> list[Task]:
    """The sheet-1 tasks compose() leaves out of the client deliverable."""
    return [t for t in tasks_repo.open_tasks_for_client(conn, org_id)
            if is_internal_category(t.category)]
```

This is beyond the ROADMAP's four pins and is the one addition I am
proposing. Reason: the row badge tells you *before*, this tells you *at
the moment the file leaves the building*, and it is the signal that
catches D1's failure mode — you typed "Internal Review", you export, it
says nothing was withheld. Six lines, no new query surface. CLAUDE.md's
"a refusal says something" applied to an exclusion.

**Cost if wrong:** a slightly longer toast. Delete two lines.

### D8 — Nothing else changes

Not in scope, and each deliberately so:

- **Sheets 2, 3, 4.** `rfi_item` has its own `category` column
  (`models.py:270`) and the generalisation would be tidier — the ROADMAP
  forbids it by name, and the two categories are different vocabularies
  answering to different people.
- **A schema column or a checkbox field.** Grant's call, 2026-08-18.
- **`seed.py`.** Seeded tasks carry no category at all (`seed.py:265-279`),
  so no snapshot moves because of the badge. Adding an Internal task to the
  sample book would re-baseline snapshots for a demo.
- **The bucketing key at `services/export_open_items.py:108` does not
  strip** (`t.category.lower()`), so "Renewal" and "Renewal " are two
  sections today. Pre-existing, unrelated, and harmless to this feature —
  the predicate strips, so both spellings of Internal are excluded either
  way. Noted, not fixed.

## Files

| File | Change |
| --- | --- |
| `src/bookkit/models.py` | `INTERNAL_CATEGORY`, `is_internal_category` (D2) |
| `src/bookkit/services/export_open_items.py` | `include_internal` param + one filter after `:98`; `ExportRow.internal`; `_task_row` sets it; `withheld_internal()`; docstring names the rule |
| `src/bookkit/repo/vocab.py` | `task_categories` unions the constant (D6) |
| `src/bookkit/mcpserver.py` | `compose(..., include_internal=True)` at `:2243`; tool docstring at `:171-184` corrected — it currently promises client parity |
| `src/bookkit/tui/theme.py` | `category_text()` (D5) |
| `src/bookkit/tui/screens/account.py` | four-call-site swap at `:681`, `:742`; `widest` at `:161`; tab hint at `:103-106` |
| `src/bookkit/tui/screens/navigator.py` | swap at `:702`, `:805` |
| `src/bookkit/tui/widgets/entity_actions.py` | withheld count on the notify at `:282` |
| `src/bookkit/cli.py` | withheld count on the print at `:426` |
| `src/bookkit/web/routes/work.py` | `_task_category_suffix`; passed at `:99` and in `_task_display_cell` |
| `src/bookkit/web/static/app.css` | `.tag-internal`, beside `.tag-overdue` at `:930` |

## Tests, and the mutation that must break each one

Written first, each one run against a deliberately broken production path
before it is believed (CLAUDE.md: a green suite proves nothing broke, not
that the new path is taken).

**`tests/test_services.py`**

1. `test_compose_omits_the_internal_category_section` — org with a
   `category="Internal"` task and a `category="Renewal"` task; assert no
   label starts with "Internal" and the internal title appears in no row.
   *Mutation:* delete the filter statement in `compose` → the Internal
   section appears → fails.
2. `test_internal_match_ignores_case_and_surrounding_space` — three tasks
   categorised `" internal "`, `"INTERNAL"`, `"Internal"`, plus one
   `"Renewal"`; assert exactly one section survives.
   *Mutation:* drop `.strip().lower()` from `is_internal_category` → the
   first two leak → fails.
3. `test_internal_prefix_is_not_internal` — a `"Internal Review"` task is
   exported, in its own section. This is D1 written down.
   *Mutation:* change the predicate to `startswith` → the section
   disappears → fails.
4. `test_internal_task_on_a_placement_is_withheld_too` — a placement with
   two tasks, one Internal; assert the placement section exists and holds
   only the other. *Mutation:* move the filter inside the `if t.category:`
   branch at `:107` → the internal placement task ships → fails.
5. `test_all_internal_account_exports_the_no_open_items_sheet` — an org
   whose only open task is Internal; `compose(...) == []` and
   `load_workbook(path).active["A2"].value == "No open items as of …"`
   (idiom from `tests/test_services.py:428-434`). This is the pinned
   empty-section answer. *Mutation:* delete the filter → A2 holds a task
   title → fails.
6. `test_withheld_internal_lists_what_the_client_did_not_get` —
   `withheld_internal` returns the Internal task and nothing else.
   *Mutation:* invert the predicate in `withheld_internal` → fails.

**`tests/test_mcpserver.py`**

7. `test_open_items_still_shows_internal_tasks_flagged` — per-client
   `_open_items` includes the Internal task, its row carries
   `internal: True`, and a normal row carries `internal: False`.
   *Mutation:* drop `include_internal=True` at `mcpserver.py:2243` → the
   row is gone → fails.

**`tests/test_repo.py`**

8. `test_task_categories_offers_internal_on_an_empty_book` — zero tasks,
   `"Internal" in vocab.task_categories(conn)`.
   *Mutation:* remove the union → fails.
   Also update the existing exact-list assertion at
   `tests/test_repo.py:378`.

**`tests/test_web_work.py`**

9. `test_internal_task_row_says_not_exported` — GET the tasks panel for an
   account with one Internal and one normal task; assert `tag-internal`
   and `not exported` appear exactly once, and that the badge is inside the
   category `<td>` (no `<td>` nested in a `<td>`).
   *Mutation:* return `""` from `_task_category_suffix` → fails.
10. `test_saving_a_category_to_internal_returns_the_badge` — POST
    `/accounts/{ref}/tasks/{id}/cell/category` with `Internal`; the
    returned display cell carries the badge.
    *Mutation:* drop the suffix from `_task_display_cell` → fails.

**`tests/test_tui.py`**

11. `test_open_items_marks_internal_tasks` — render the account Open Items
    tab, assert the category cell for the Internal task renders the glyph
    and the normal one does not.
    *Mutation:* make `theme.category_text` ignore the predicate → fails.
12. `test_export_says_how_many_internal_tasks_were_withheld` — run the
    export flow, assert `withheld` appears in `app._notifications` (idiom
    at `tests/test_tui.py:743`).
    *Mutation:* drop the suffix at `entity_actions.py:282` → fails.

**Existing tests to expect movement in:** `tests/test_repo.py:378` (must
change, D6); `tests/test_snapshots.py` only if the tab-hint text changes —
re-baseline deliberately with `--snapshot-update` and read the diff first
(`tests/test_snapshots.py:13-17`). Seeded tasks have no category, so no
snapshot moves because of the badge itself.

## Gates

`uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`, in
its own worktree, output redirected to the scratchpad and gated on the
command — never piped before the `&&`.

## Needs Grant

**Should MCP's per-client `open_items` keep showing Internal tasks?**
Recommendation: **yes** — pass `include_internal=True` at
`mcpserver.py:2243` and let the row carry `internal: true`. It is his own
assistant reading his own book; hiding a task from himself is the silent
behaviour this feature exists to prevent, and `task_complete` needs the
`ref` from that read (`tests/test_mcpserver.py:206-218`). The consequence
is that the tool docstring's promise — "this matches what a client would
be handed" (`mcpserver.py:173-175`) — stops being true and must be
rewritten. If he would rather keep that promise exact, the change is one
keyword argument and test 7 inverts.



---

## Verification report (independent adversarial pass, 2026-08-18)

**Verdict: sound.** Safe to build from. I opened all 67 citations; 63 resolve exactly, and the four misses are off-by-a-few line ranges or a slightly overstated test harness — none changes a decision. More importantly, all three of the drafter's corrections to the ROADMAP check out against the code: no section is ever emitted empty (buckets are non-empty by construction at :107-109, and :142/:157/:167 are explicit guards, with tests/test_services.py:365-367 pinning it), the docstring's "always present, even when empty" describes sheet 1's placeholder fallback at :377-378, compose() genuinely has exactly two callers in src (write() at :376 and mcpserver.py:2243 — I grepped the whole tree), and the placement branch at :112-115 really would leak an Internal placement task if the filter went in the category branch. Every one of the 12 proposed tests has a mutation that makes it fail; none is decoration. The weakest point is D5's coverage claim: `category_text` fires on four TUI call sites but the plan adds the ⊘ legend to only one hint line, leaving an unexplained glyph on the navigator, and D4's "labelled wherever it is shown" is false for MCP's book-wide branch (mcpserver.py:2253-2262), which never touches ExportRow. Both are additive fixes — two hint lines and one dict key — not a rethink.


### Citations that did not check out

- **`src/bookkit/seed.py:265-279`** — claimed: Seeded tasks are created with org_id, due_on and priority only — no category — so the badge moves no snapshot.
  
  *Actually:* Line 265 is inside submissions.create(conn, market.id, _iso(sent), placement_id=...). The task loop is seed.py:270-280, and the cited range truncates it before the kwargs it names (org_id/due_on/priority are on line 279). The underlying claim is TRUE — grep confirms tasks.create appears exactly once in seed.py, at :273, with no category — but the range points at the wrong call.

- **`tests/test_conventions.py:37-45`** — claimed: tui and web must never import each other; shared code lives in bookkit.forms/models or it is not shared.
  
  *Actually:* test_web_and_tui_never_import_each_other is at 38-46; line 37 is blank. Claim is true. Off-by-one only.

- **`tests/test_cli.py:119-140`** — claimed: Existing CLI export tests exercise `export open-items` with capsys — the harness for asserting the withheld line.
  
  *Actually:* test_export_open_items_writes_workbook (119-130) accepts `capsys` as a parameter but never reads it: it asserts only `rc == 0 and out.exists()`. The only test that reads capsys is the *failure* case, test_export_unknown_org_suggests (133-142). The harness exists in the file, but the test the brief points at does not assert on stdout today and will need a readouterr() added, not just an extra assertion.

- **`src/bookkit/web/routes/work.py:79-83 (body) / :79-101 (citation list)`** — claimed: _task_due_suffix renders a badge INSIDE the due cell via render_cell_display's suffix.
  
  *Actually:* `def _task_due_suffix` is at line 77; the docstring the claim leans on runs 78-81 and the return is 83. The cited range starts mid-docstring. Trivial, claim is correct.


### Claims challenged (even where the citation resolved)

- **[IMPORTANT]** D4: "`mcpserver`'s `asdict(r)` (`:2242`) picks it up for free, so an internal row is labelled wherever it is shown."
  
  *Evidence:* False for the view Grant's assistant uses most. `_open_items` has two branches. The per-client branch (mcpserver.py:2239-2246) does asdict ExportRow, so it would gain `internal`. The BOOK-WIDE branch (mcpserver.py:2253-2262) never touches compose or ExportRow — it builds its own dicts straight from `tasks_repo.open_tasks(conn)` with `{"ref", "title", "description", "category", "due"}` and would carry no `internal` key at all. So after this build an Internal task is flagged on the TUI row, the web row, and the per-client MCP read, and unflagged in the book-wide MCP read. Note this cuts BOTH ways and the field is still justified: ExportRow carries no `category` field at all (export_open_items.py:36-47), so in the per-client view an Internal *placement* task's row would otherwise show nothing — the category only survives in the section label, and placement sections are labelled by program name (:158-160). Fix is one key at mcpserver.py:2259-2260 or a sentence narrowing the claim; either way "wherever it is shown" is not true as written.

- **[IMPORTANT]** D5: "Both surfaces say \"not exported\" on the row … The legend goes in the Open Items tab hint (`screens/account.py:103-106`)."
  
  *Evidence:* The legend covers one of the four TUI call sites. `theme.category_text` replaces the expression at account.py:681 (account OVERVIEW tab), account.py:742 (Open Items tab), navigator.py:702 (attention tasks) and navigator.py:805 (per-account tasks) — all four confirmed. Only the Open Items tab hint is in the Files table. The navigator's tasks hint is ROW_HINTS["tasks"] at navigator.py:63-66 and the overview tab has its own entry in the same TAB_HINTS dict as :103-106; neither is listed for change. An implementer following the brief literally ships a bare ⊘ on the navigator — the screen CLAUDE.md:82-84 calls home — with no word anywhere on it, which is the failure CLAUDE.md:98-99 ("every colored state carries a glyph or word too") and :100-105 name. D1's whole safety argument rests on the badge being seen; two hint lines are missing from the plan.

- **[IMPORTANT]** Test 5: "*Mutation:* delete the filter → A2 holds a task title → fails."
  
  *Evidence:* The test still fails, so it is load-bearing — but not for the stated reason, and the stated reason invites a wrong assertion. With the filter deleted, compose returns one section labelled "Internal — <Org>"; render_table_sheet writes the six column headers on row 1 (pinned by tests/test_services.py:420) and the section label as the next merged row, so A2 would hold "Internal — <Org>", not the task title. The test's assertion must be the one the brief's code line shows (`A2 == "No open items as of …"`), never the mutation prose. Given this project's record of tests that pass for a reason adjacent to what they claim (CLAUDE.md:171-175), the prose should be corrected before anyone writes the assertion from it.

- **[MINOR]** D7: `withheld_internal`'s docstring — "The sheet-1 tasks compose() leaves out of the client deliverable."
  
  *Evidence:* It computes "open Internal-category tasks for this client", which is narrower than what compose() leaves out. compose() also silently drops a task whose placement_id names a placement `placements.for_org` does not return: repo/placements.py:51-56 filters org_id AND alive, while repo/tasks.py:56-66 keeps such a task via the ON-clause org_id fallback, so it lands in by_placement (export_open_items.py:112-115) and the placement loop at :147 never visits it. Pre-existing and rare, but the docstring is the load-bearing sentence on a user-facing count, and it claims a completeness the function does not have. Narrow the wording to what it computes.

- **[MINOR]** D2: "Four consumers import that one function: the export composition, the vocabulary, the TUI theme helper, the web row builder."
  
  *Evidence:* The vocabulary consumes the CONSTANT, not the predicate — D6's own snippet is `_dedupe([*_column(conn, "task", "category"), INTERNAL_CATEGORY])`. Three consumers of `is_internal_category`, four of the module. Cosmetic, but the sentence is the justification for the D2 home.

- **[MINOR]** "tests/test_snapshots.py only if the tab-hint text changes — re-baseline deliberately."
  
  *Evidence:* The tab-hint text DOES change under D5, and no snapshot moves anyway: tests/test_snapshots.py:99-135 snapshots the account screen's overview, placements (4), interactions (3), documents (7) and requests (9) tabs — the Open Items tab is not in the set. The overview tab IS snapshotted and does render category (account.py:681), but seeded tasks carry no category (seed.py:273-280), so category_text returns dash() there and the cell is unchanged. The caveat is harmless; the reasoning behind it is wrong in both directions.


### Decisions the draft left open

- **Should MCP's per-client `open_items` keep showing Internal tasks (flagged `internal: true`), or match the client deliverable exactly and hide them?**
  - Recommendation: Keep showing them: pass `include_internal=True` at mcpserver.py:2243 and rewrite the tool docstring's client-parity sentence (mcpserver.py:173-175). It is Grant's own assistant reading his own book, and task_complete needs the ref from that read.
  - Cost if wrong: If he wanted exact client parity, the fix is one keyword argument and test 7 inverts. If we hid them and he wanted them, he loses sight of internal work through the surface he uses most, silently — the failure mode is invisible, which is the worse direction.

- **Is the export-time "N internal tasks withheld" line (D7) wanted? It is beyond the ROADMAP's four pins.**
  - Recommendation: Build it. It is six lines, and it is the signal that catches D1's failure mode at the moment it matters — you typed "Internal Review", you export, and the toast says nothing was withheld.
  - Cost if wrong: A slightly longer toast and print line; deleting it is a two-line revert.


### Needs Grant

- MCP per-client `open_items`: keep Internal tasks visible to his assistant (flagged), or hide them so the tool's "matches what a client would be handed" promise stays literally true? Recommendation: keep them visible and correct the docstring. The build proceeds on that default; flipping it is one keyword argument.


### Corrections this draft makes to the ROADMAP entry

- ROADMAP said: "check the empty-section handling: sections are 'always present, even when empty' per the module docstring, and an Internal header with nothing under it would defeat the point."
  - Code says: compose() never emits an empty section. Category buckets are non-empty by construction, and General, placement and project sections each sit behind an explicit guard; an existing test asserts compose() returns [] for an account with nothing open. The docstring phrase describes sheet 1, which write() always renders — falling back to a single 'No open items as of <date>' row. Filtering the category removes the section entirely. The real case to pin is the second-order one: an account whose only open items are Internal now exports the placeholder sheet. (`src/bookkit/services/export_open_items.py:108-109,142,157,167,377-378; tests/test_services.py:365-367,428-434`)

- ROADMAP said: "It is one filter in the composition."
  - Code says: compose() has a second caller: MCP's per-client open_items, whose tool docstring promises 'the same composition used for the client export deliverable, so this matches what a client would be handed'. A bare filter therefore also changes what Grant's own assistant can see about an account. It is one filter plus an include_internal parameter and a docstring correction. (`src/bookkit/mcpserver.py:2243 and 171-184`)

- ROADMAP said: "Category is also the section grouping on sheet 1 (tasks split by category, alphabetical). So the filter removes the whole Internal section, not just its rows."
  - Code says: Sheet 1 also carries a section per placement, built from the same task list with category ignored. A filter applied inside the category branch would let an Internal task carrying a placement_id ship to the client. Placement-attached tasks are a supported shape — open_tasks_for_client exists to catch them and a test asserts they reach the workbook — so the filter must sit on the task list, before either split. (No UI writes task.placement_id today; only repo/tasks.reassign_placement does, on a placement merge.) (`src/bookkit/services/export_open_items.py:112-115,147-160; src/bookkit/repo/tasks.py:44-55,69-82; tests/test_services.py:340-348`)
