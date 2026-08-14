# bookkit TUI review — Phase 1: orient and propose

**Date** 2026-08-14 · **Textual** 8.2.8 (installed; `pyproject.toml` pins only `>=0.58`)
· **Reviewed** every file under `src/bookkit/tui/` (~7,900 lines), `bookkit.tcss`,
`db.py` connection setup, and the TUI test suite.
**Status** findings only — no application code was changed.

**32 findings — 2 P0, 16 P1, 14 P2** (F2 was withdrawn from P0; see below).

**Batch A is built** on branch `tui-batch-a`: F1, F7, F11, F22, F23, F28 fixed, F2 reduced
and made explicit. 492 tests pass, `mypy --strict` and `ruff` clean. Each fix has a
regression test that was watched failing first.

Evidence: every screen was driven headlessly through `App.run_test()` at 140×45 and
80×24, exported as SVG (`docs/screenshots/`) and as composited plain text
(`docs/screenshots/wide-140x45.txt`, `small-80x24.txt`). Layout claims are backed by
measured `widget.region` values, not by reading CSS. Contrast claims are backed by a
sweep that calls `render_line` on every widget of every screen and computes WCAG contrast
per text segment. `ruff`, `mypy --strict` and `codespell` were run.

**Revision, same day:** F28 was added after Grant reported text disappearing while
editing. It is the only finding in this document that came from use rather than from the
review, and the review had missed it — see the note at the end of F28 for what that says
about the method.

**Second revision, during Batch A:** F2 was **withdrawn**. Writing its test proved the
protection it demanded already exists — Python's sqlite3 driver sets a 5-second
`busy_timeout` by default. It had been ranked #2 and named as one of three findings I said
I would fight for. Full correction under [F2](#f2).

Assumptions I had to make, because the brief left them blank: this is the
insurance-brokerage CRM described in `CLAUDE.md`, primary user is Grant alone, and the
daily terminal is a large one — I reviewed 80×24 as the *degradation* case, not the
common case. If you actually live at 80×24, findings 4/5/6/13 move up a priority band.

---

## 1. Top ten, ranked by (daily annoyance removed) ÷ effort

| # | Change | Finding | Effort |
|---|---|---|---|
| 1 | Focus-gate `e` on Today — today it edits a task the cursor is not on | [F1](#f1) | S |
| 2 | Highlighted pipeline cards lose their dim text at 1.83:1 — **observed in use** | [F28](#f28) | S |
| 3 | Stop the landing card's renewal rows wrapping mid-line | [F6](#f6) | S |
| 4 | A command-palette `Provider` that jumps to any account/market/contact | [F10](#f10) | M |
| 5 | Today: use the theme helpers; stop rendering `-345` as `-` | [F4](#f4) | M |
| 6 | `switch_screen` instead of stacking a new AccountScreen per search jump | [F7](#f7) | S |
| 7 | Empty-state lines on the Account tabs (Navigator already has them) | [F12](#f12) | S |
| 8 | Hide the palette's "change theme" — it half-breaks the app | [F11](#f11) | S |
| 9 | Re-split the placements tab so the premium column is visible | [F5](#f5) | S |
| 10 | Compare the snapshots you already write, instead of only writing them | [F14](#f14) | S |

*(F2 held the #2 slot until it was withdrawn — see [F2](#f2).)*

---

## 2. The map

**Entry** `bookctl` with no subcommand → `BookkitApp` (`tui/app.py`) → pushes
`NavigatorScreen`. One `sqlite3.Connection` lives on the app for the whole session.

**Screens** (10) — Navigator (home), Today, Book, Account, Calendar, Pipeline, Markets,
MarketDetail, Team, Onboarding.
**Modals** (14) — FormModal, QuickCapture, ConfirmTask, SearchModal, Picker, HelpScreen,
SettingsModal, ImportScreen, PasteImportModal, ImportChooser, LinkReview, ConfirmRenew,
ConfirmDeleteInteraction, MergePicker, ConfirmRevertBatch, BatchDetail.
**Custom widgets** (6) — `ListTable` (DataTable + j/k/g/G), `InlineTable` (+ in-cell
edit), `CellEditor` (absolute-positioned Input), `TowerPreview` (Static + towerkit's
ASCII renderer), plus the form/action helper modules.
**CSS** — one shared `bookkit.tcss` (241 lines, no hardcoded hex — it references theme
variables throughout) plus eight small `DEFAULT_CSS` blocks, each with a comment saying
why it must be local. This is genuinely well kept.

**Navigation graph**

```
NavigatorScreen (home)
  t → Today ── b/c/p/m/w → Book/Calendar/Pipeline/Markets/Team   (esc pops)
  b/c/p/m/w → same five directly
  enter on an account node ─┐
  enter on a table row ─────┴→ AccountScreen (9 tabs, 1–9)
  o → Onboarding      , → Settings      x → export      u → undo
  R on MCP CHANGES → ConfirmRevertBatch
anywhere: / search · n quick capture · ctrl+t new task · ? help · ctrl+q quit
```

**Keyboard reachability** — I found nothing that is mouse-only. Every action has a
binding, the hidden ones have a hint line, and both the tree and the tables are
navigable. This part is done well and I have no findings against it.

---

## 3. Findings

Priority: **P0** broken / data-risking / blocks daily use · **P1** daily friction or
visibly unfinished · **P2** nice to have.

### P0

<a id="f1"></a>
**[P0] [defect] `e` on Today edits a task the cursor is not on** — `today.py:170`
Effort **S**

`_selected_task_id()` reads the tasks-table cursor with no focus check, and
`action_edit_task` (`today.py:197`) calls it unconditionally. `action_task_done` right
above it (`today.py:161`) *does* gate on `table.has_focus`. So `d` is safe and `e` is not.

Verified: focus `#renewals-table`, press `e` → `FormModal("edit task")` opens on whatever
row the invisible tasks cursor sits on. This is exactly the class of bug `CLAUDE.md`
records as fixed ("row actions REQUIRE `table.has_focus`") — Today was never backported.

*Why it matters daily:* Today is the four-pane screen where you tab between panes. Editing
a record you never looked at, and saving it, is silent corruption.

*Fix:* give `_selected_task_id` the same three-clause gate `action_task_done` uses.

---

<a id="f2"></a>
**[~~P0~~ → P2] [WITHDRAWN as a defect] `busy_timeout`** — `db.py:132` · Effort **S**

**This finding was wrong, and it was ranked #2 and named as one of the three I would
fight for. Correcting it in full.**

I claimed `connect()` never sets `busy_timeout` and that "SQLite's default is 0 ms", so a
concurrent MCP write would fail instantly. The first half is true of the code and the
second half is true of the **C API** — but not of the driver this code actually uses.
Python's `sqlite3.connect()` takes `timeout=5.0` by default and applies it as
`busy_timeout`. Measured on this repo before any change:

```
bookkit db.connect  -> 5000 ms
bookkit readonly    -> 5000 ms
```

A test that holds the write lock in one thread while a second connection writes now
*passes against the unmodified code*: the second writer waits ~0.4 s and succeeds. There
was never an instant-failure bug. I should have measured this before writing it up, the
same way I measured every layout claim.

*What actually survives, at P2:* the 5-second timeout is **inherited**, not stated. Nothing
in the repo pins it, and `transaction()` takes the write lock up front with BEGIN
IMMEDIATE while the TUI and the MCP server both hold read-write connections. The day
someone passes `timeout=` or swaps the driver, it disappears silently.

*Done in Batch A:* `db.BUSY_TIMEOUT_MS = 5000` set explicitly on both connection factories,
plus the contention test kept as a characterisation test of the guarantee. Two lines of
behaviour-preserving change — not the P0 this document originally claimed.

---

<a id="f3"></a>
**[P0] [defect] An unhandled exception drops the whole session to a traceback** — app-wide
Effort **M**

There is no `App.on_exception` / error screen anywhere in `src/bookkit`. Exception
guarding is ad hoc and per-site: `navigator.py:1042` guards `get_request`,
`navigator.py:925` guards a stale placement key, `account.py:1247` guards the RFI paths —
but `navigator.action_renew_row` (`navigator.py:1064`), `action_edit_row`'s contact and
task branches (`navigator.py:1018`, `1030`) and `account.action_mark_primary`
(`account.py:945`) all call the repo bare. A stale row key after an undo, or the lock
error from [F2](#f2), takes the app down and loses the screen stack, any open form and the
quick-capture draft in progress.

*Fix:* an app-level handler that notifies with the message, logs the traceback to a file,
and returns to Navigator rather than exiting. The per-site guards can then stay as the
*graceful* path and stop being load-bearing.

### P1

<a id="f4"></a>
**[P1] [friction] Today is illegible at 80 columns, and renders `-345` as `-`** —
`today.py:79–148` · Effort **M**

Two problems, one screen:

1. `today.py:100` writes the countdown as `str(item.days_remaining)`. In a 36-cell pane
   the `d` column truncates to a single character, so an overdue `-345` and a blank cell
   look identical. Measured: `#renewals-table` is 36 cells wide at 80×24 for **seven**
   columns.
2. Today is the only screen that does not use `theme.days_text` / `date_text` /
   `money_text` / `status_text`. Everywhere else an overdue date is `◆ 345d over` in bold
   red, right-aligned; here it is a bare left-aligned string with no glyph and no colour.

From `docs/screenshots/small-80x24.txt`:

```
│ TASKS DUE & OVERDUE                 │ │ RENEWALS — NEXT 120 DAYS             │
│  due  task  account                 │ │  expiry  d  account  program  lines  │
│  8d   Send  Boreali                 │ │  2025-0  -  Atomic   2024 Ca  —      │
```

*Fix:* swap in the theme helpers (this alone fixes the `-`, since `days_text` renders
`◆ 345d over`), and collapse `#today-grid` to one column below ~100 cells.

---

<a id="f5"></a>
**[P1] [friction] The placements tab hides the premium column** — `account.py:364`
Effort **S**

`on_mount` widens `#placement-side` to 52% to make room for status and premium. At 140
columns that is 72 cells for seven columns (`ref, program, effective, expires, d, status,
premium`). Measured result: `status` truncates to one letter and `premium` is entirely
off-screen.

From `docs/screenshots/wide-140x45.txt`:

```
 ref       program                effective   expires               d  s╭─────────
 PLC-0001  Casualty Program       2025-09-03  2026-09-03          20d  q│ ████████
 PLC-0002  2024 Casualty Program  2024-09-03  2025-09-03  ◆ 345d over  b│ ████████
```

The tower preview beside it is excellent and worth its space — the fix is the table, not
the preview.

*Fix:* drop `effective` (the previous row's `expires` implies it) and shorten `program`
with the existing `book._program_label`, or make the preview a toggle so the table can
have the full width when you are reading numbers.

---

<a id="f6"></a>
**[P1] [polish] The landing card's renewal rows wrap mid-line, even at 140 columns** —
`navigator.py:604–627` · Effort **S**

`_glance_card` builds each renewal as one long markup string in a `Static`. The pane is
~94 cells; the lines run ~110. This is the first thing seen on every launch.

```
│     2025-09-03   ◆ 345d over  Atomic Industries, Inc. — 2024 Casualty Program  2024 Casualty
│   Program  $7.6M
```

The comment at `navigator.py:609` ("pad the plain text FIRST so markup length can't skew
alignment") shows the alignment was thought about — the total width was not.

*Fix:* render NEXT RENEWALS as a `ListTable` (the widget is already imported), or build
the row with `rich.Text` and fixed column widths plus `no_wrap`.

---

<a id="f7"></a>
**[P1] [defect] Search stacks a new AccountScreen every jump, without bound** —
`app.py:100–103` · Effort **S**

`open_account` always `push_screen`s. Verified: from an account, three `/`-search jumps
leave `['Screen', 'NavigatorScreen', 'BookScreen', 'AccountScreen', 'AccountScreen',
'AccountScreen', 'AccountScreen']`. Escape then walks back through every stale copy, and
each one runs a full `refresh_data()` on `on_screen_resume` (`account.py:386`) as you pass
through it.

*Why it matters daily:* `/` is the fast path between clients. After a morning of it, `esc`
no longer means "back to where I was".

*Fix:* in `open_account`, `switch_screen` when the current screen is already an
`AccountScreen`; otherwise push.

---

<a id="f8"></a>
**[P1] [defect] The Save button is clipped out of every form below 28 rows** —
`forms.py:77–85` · Effort **S**

`.modal-fields { max-height: 55vh }` plus a title row, a hint row, a 3-row `Button` and
2 rows of padding exceeds `.modal-box { max-height: 80% }` at short heights. Measured
(new-task form):

| terminal | box | Save button | inside the box? |
|---|---|---|---|
| 80×24 | `y=2..21` | `y=20..23` | **no** |
| 90×26 | `y=3..23` | `y=22..25` | **no** |
| 80×30 | `y=3..27` | `y=24..27` | yes |
| 120×40 | `y=4..36` | `y=31..34` | yes |

The `^s save · esc cancel` hint stays visible, so the form is still *usable* — this is
friction, not a blocker. But the DEFAULT_CSS comment claims "the title and the '^s save'
hint stay on screen however long the form is", and the button was never counted.

*Fix:* the honest one is to delete the `Button` — this is a keyboard-first app and the
hint line is the affordance. Failing that, `max-height: 40vh` on `.modal-fields`.

---

<a id="f9"></a>
**[P1] [friction] Zero workers: every query and the tower render run on the event loop** —
app-wide · Effort **M**

There is no `@work`, no `run_worker` and no `LoadingIndicator` anywhere in
`src/bookkit/tui`. Two specific costs:

- `_refresh_pipeline` (`account.py:767`, `775`) calls `orgs.get(conn, s.market_org_id)`
  inside a nested loop — one query per submission per placement. A classic N+1 on the tab
  that has the most rows.
- `TowerPreview._render_current` (`tower_preview.py:41–49`) re-reads and re-parses the
  JSON program and re-renders the ASCII tower **on every resize event**.

**I have not measured this on Grant's real book** — on the 20-account seed it is
imperceptible, and I am not going to claim a performance problem I cannot reproduce. The
N+1 is worth fixing on correctness-of-shape grounds regardless; the worker question should
wait for a measurement on the production machine.

*Fix:* batch the market lookup into one query first. Then, if `refresh_data` is measurably
slow on real data, wrap it in `@work(thread=True, exclusive=True)`.

---

<a id="f10"></a>
**[P1] [friction] No command-palette `Provider`** — nothing in `src/bookkit` · Effort **M**

Zero `Provider` subclasses. Textual's palette is already bound and advertised in the
Footer (`^p palette`), and it currently offers only theme-change and quit. For a database
utility this is the single highest-leverage addition: a provider that yields every
account, market and contact by name makes "jump to Cascade Health" a two-keystroke
operation from *any* screen, including from inside a form, without the `/` modal and
without [F7](#f7)'s stacking.

*Fix:* one `Provider` over `repo.search`, plus a second yielding the screen-level actions
(`Today`, `Book`, `Sync programs`, `Setup`, …) so the palette also answers "what can I do
from here".

---

<a id="f11"></a>
**[P1] [defect] Changing the theme from the command palette half-breaks the app** —
`theme.py:18–28` · Effort **S**

The palette advertises theme switching. `bookkit.tcss` is clean (it uses `$primary`,
`$panel`, `$text-muted` throughout — no hardcoded hex), but the eight palette constants
are interpolated into Rich markup across ten modules, and `CellEditor.DEFAULT_CSS`
(`inline_edit.py:172–188`) interpolates them too.

Verified by switching to `textual-light` and exporting
(`docs/screenshots/light-theme.svg`): the background repaints to `#e5e5e5`, while
`#8a8577` (DIM), `#d6b35a` (GOLD), `#d57367` (RED), `#84a98c` (GREEN) and `#3a4150` (RULE)
all survive unchanged. Worse, `STATUS_STYLES["open"]` and the `status_text` fallback are
`theme.FG` = `#d5d2c9`, a light cream — every open task's status cell becomes invisible on
a light background.

*Fix, cheap and correct:* bookkit is deliberately one-theme (`theme.py:1`). Override
`App.get_system_commands` to drop the theme command. Migrating ten files of markup to
theme variables buys a light mode the app does not want.

---

<a id="f12"></a>
**[P1] [friction] Empty tables on the Account screen say nothing** — `account.py:302–352`
Effort **S**

Verified on tabs 5 (Projects), 7 (Documents), 8 (Open items) and 9 (Requests): a header
row over eighteen blank lines. You cannot tell "nothing here" from "it failed to load".

The Navigator already solved this — `_render_hint` (`navigator.py:574–580`) prints
`empty — a adds the first row` for addable lists and `nothing here — that's good` for
attention lists. The Account screen never got it, even though it has `#tab-hint` sitting
right there.

*Fix:* extend `_render_tab_hint` to check the tab's primary table's `row_count` and
substitute the empty-state line, reusing the Navigator's two phrasings.

---

<a id="f13"></a>
**[P1] [friction] Help is a 160-row monolith that wraps into porridge at 80 columns** —
`help.py:16–115` · Effort **M**

One hand-aligned two-column `Static`. Measured body height: **160 rows** at 80 columns
(134 at 100, 99 at 140) inside a 19-row viewport. Its columns only line up if the body is
wider than ~70 cells, which it never is at 80:

```
█    /        search everything          n   log an    █
█  interaction (quick capture)                         █
```

Two content errors while I was in there: `help.py:63` says "1–8 jump straight to a tab"
but there are nine tabs and nine bindings (`account.py:285–293`); and there are two
separate `[b]markets screen[/b]` sections (`help.py:73` and `help.py:94`).

*Fix:* `Markdown` or per-section `Collapsible` so a section is one keypress away, and
generate the per-screen key lists from the actual `BINDINGS` so they cannot drift again.

---

<a id="f14"></a>
**[P1] [friction] The snapshots are written but never compared** — `tests/test_tui.py:52`
Effort **S**

`snapshot()` writes `tests/snapshots/*.svg` and nothing ever reads them.
`pytest-textual-snapshot` is not in the dev group. They are build artifacts, not tests —
every one of the layout findings above would have been caught by a comparison, and none of
them was.

The Pilot coverage itself is good (1,902 + 318 lines, real interaction sequences). It is
only the visual half that is inert.

*Fix:* add `pytest-textual-snapshot`, convert `snapshot(app, name)` call sites to
`snap_compare`, and commit the current SVGs as the baseline. `docs/screenshots/` is that
baseline at two sizes.

---

<a id="f15"></a>
**[P1] [polish] Three chrome idioms for the same one-line bar** — Effort **S**

- Navigator: `#status-bar`, a `Static` (`navigator.py:263`)
- Today / Book / Calendar / Markets / Team / Pipeline: `Header()`
- Account: `#account-header`, a `Static` forced to one row by **inline styles in
  `on_mount`** (`account.py:358–361`) because `bookkit.tcss:108–117` still styles it for a
  two-line layout that no longer exists.

That tcss rule is dead code actively fought by Python. Same story at `account.py:364`,
`367–369`, `381` and `markets.py:410–412` — five places where inline styles exist purely
to out-rank the shared file.

*Fix:* delete `bookkit.tcss:108–117`, pick one bar idiom, and move the height/width
overrides into per-screen `DEFAULT_CSS` with a more specific selector rather than into
`on_mount`.

---

<a id="f16"></a>
**[P1] [defect] `_settle_tables()` drives four private DataTable attributes** —
`account.py:646–660` · Effort **S**

It sets `_require_update_dimensions`, copies and clears `_new_rows`, and calls
`_update_dimensions` and `_clear_caches`. All four still exist in textual 8.2.8
(`_data_table.py:779, 781, 1100, 1408`), so it works today. But `pyproject.toml` declares
`textual>=0.58` — three major versions below what is installed — so `uv lock --upgrade` on
another machine can pull a version where column widths silently go wrong again.

The docstring is honest about what it is. The exposure is the unpinned floor, not the hack.

*Fix:* pin `textual>=8.2,<9`, and add a test that asserts the four attributes exist so an
upgrade fails loudly instead of subtly.

---

<a id="f17"></a>
**[P1] [friction] `i` means two different things one tab apart** — Effort **S**

`InlineTable` binds `i` to in-cell edit (`inline_edit.py:31`); `AccountScreen` binds `i` to
paste-import (`account.py:275`). Widget bindings win when the widget has focus, so on tabs
8 and 9 `i` edits a cell and on tabs 2, 3 and 4 it opens the import chooser. On Today, `i`
is import-a-spreadsheet; on Markets and Team it is paste-a-signature.

The hint lines document each case honestly, which is the only reason this is usable. It is
still the one place the keymap has to be read rather than learned.

*Fix:* move paste-import to `I`, matching the `L` / `P` / `D` convention already
established at `account.py:266`, `279`, `282`.

---

<a id="f18"></a>
**[P1] [defect] Market appetite and underwriters can be created but never edited or
deleted** — `markets.py:311–316` · Effort **M**

`MarketDetailScreen.BINDINGS` has `a` (appetite), `w` (underwriter) and `i` (paste) — no
`e`, no `d`. `appetite_form(existing=…)` exists (`entity_forms.py:427`) and is never called
with an existing record. `repo/orgs.py` has `add_appetite` (line 120) and
`appetite_for_market` (126) but no update or delete. The `#md-appetite`, `#md-contacts` and
`#md-subs` tables are also built without row keys (`markets.py:447`, `457`, `464`), so
`enter` does nothing on them either.

A typo'd appetite row is permanent from the TUI.

*Fix:* add `update_appetite` / `delete_appetite` to the repo, row keys to the three tables,
and `e` / `d` to the screen.

<a id="f28"></a>
**[P1] [defect] Highlighted pipeline cards lose their dim text — 1.83:1** —
`pipeline.py:86–95` · Effort **S**

**Reported from real use (Grant, 2026-08-14): "text disappeared while I was editing."**
Reproduced and measured.

`OptionList` composites the highlight background *behind* the prompt but does **not**
override an explicit foreground baked into a `rich.Text` prompt — where `DataTable`
*does* (measured: a highlighted DataTable row renders every cell at 8.93:1, because
Textual replaces the cell foreground with the cursor foreground). So `Text.assemble(…,
theme.DIM)` survives onto the gold cursor.

Measured on the highlighted card:

| segment | fg / bg | contrast |
|---|---|---|
| `OPP-0002` — the ref | `#8a8577` on `#d6b35a` | **1.83:1** |
| ` · ` separators | `#8a8577` on `#d6b35a` | **1.83:1** |
| ` · 75%` — probability | `#8a8577` on `#d6b35a` | **1.83:1** |
| `marine · eff 2026-10-27` — whole line | `#8a8577` on `#d6b35a` | **1.83:1** |
| org name, title, `$500K` | `#15171c` on `#d6b35a` | 8.93:1 |

The org name, title and target premium survive precisely because they carry *no* explicit
colour, so the cursor foreground applies to them. Everything the code deliberately dimmed
is what vanishes.

This is the only `OptionList` in the app that styles its prompt (every other one — Picker,
SearchModal, ImportChooser, LinkReview, MergePicker, QuickCapture — passes plain strings),
which is why nothing else shows it. It is felt constantly on Pipeline because `h`, `l`,
`↑`, `↓`, `>` and `<` all move that highlight.

*Note on how this was found:* three plausible hypotheses — the cell editor's
select-on-focus, the input cursor, and the DataTable row cursor — all measured **fine**
(3.40:1, 11.86:1, 8.93:1). A brute-force contrast sweep over every rendered line of every
widget on every screen found it in one pass. That sweep is worth keeping (see F31).

*Fix:* drop the explicit `theme.DIM` from the parts that must stay legible, and let the
option's own style carry them; or give `OptionList` an `option-list--option-highlighted`
rule and build the prompt without baked colours. The `Text.assemble` mechanism itself is
correct and must stay — `pipeline.py:85` uses it so account names containing `[brackets]`
render verbatim, which is a real bug it is preventing.

---

### P2

| # | Finding | Where | Effort |
|---|---|---|---|
| F19 | No clipboard anywhere — `App.copy_to_clipboard` is unused, so an email or ref cannot be lifted out of a row | app-wide | S |
| F20 | No CLI deep-link: `bookctl` always lands on Navigator. `bookctl open ACC-0001` would make shell aliases work; `repo/settings.py` already gives a KV store | `cli.py:73` | S |
| F21 | Filters and sort never persist — `BookScreen` and `TeamScreen` rebuild empty on every entry, and there is no "last screen" restore | `book.py:47`, `team.py:111` | M |
| F22 | `subprocess.Popen(["open", doc.path])` is macOS-only, never checks the path exists, and notifies `opening …` regardless — a missing document fails silently | `account.py:897` | S |
| F23 | `notify("no longer exists", severity="error")` has no subject — *what* no longer exists? | `account.py:1248` | S |
| F24 | Terminology drift: headings say "account", messages say "client" (`select a client first`) | `navigator.py:1082` | S |
| F25 | Column truncation at 80: Markets' `bind rate` → `bin`; Team's assignments clipped mid-word; the five-column kanban is unreadable below ~110 cells | `markets.py:137`, `pipeline.py:50` | M |
| F26 | `q` is bound only on `BatchDetail` — a vim-shaped user will press it on every screen and get nothing | app-wide | S |
| F27 | `codespell` is clean over `src/bookkit/tui` (only hits: `opps`, a real abbreviation, and `pre-selects`, a style preference). Worth adding to the gate with a two-word allowlist | — | S |
| F29 | The in-cell editor's placeholder renders at **2.76:1** (`$text-disabled` = `auto 38%` over the editor's `RULE` background), and selected text drops from 4.57:1 to **3.40:1** the instant `i` opens it — `input-selection-foreground` is never set, so it falls back to `FG` under a `GOLD 35%` tint | `inline_edit.py:172`, `theme.py:53` | S |
| F30 | Chrome below the visibility floor: the `·` separators in the status bar and account header measure **1.46:1** (`RULE` on `PANEL`); the tree's `├──` guides measure **1.51:1**. "Chrome should whisper" (`bookkit.tcss:8`) is the right instinct, but 1.46:1 is inaudible rather than quiet | `bookkit.tcss:51`, `theme.py:22` | S |
| F31 | towerkit's retention rule renders at **1.05:1** (`#1c1c1c` on `#15171c`) inside the tower preview — its ANSI theme assumes towerkit's own background, not bookkit's. Not this repo's bug to fix, but worth passing a theme to `load_theme` | `tower_preview.py:47` | S |
| F32 | Keep the contrast sweep as a test. It found F28 in one pass after three hypotheses missed; it is ~40 lines — walk the screens, `render_line` every widget, fail on any text segment below ~2.5:1. Pairs naturally with F14 | `tests/` | S |

**On section 3 of the brief (text quality)** — I ran `codespell` over the TUI, the CLI, the
README and `docs/`, and read every `notify()` string, placeholder, `Binding` description
and hint line. There are no spelling errors in user-visible text. The copy is unusually
consistent: lowercase sentence fragments, and nearly every refusal names the next action
(`no team members yet — press w on Today, then a`). The only gaps are F23 and F24 above.
"Nothing material here" is the honest answer for the rest of that section.

---

## 4. Themes — what the findings trace back to

1. **The Navigator got the polish; the older screens did not.** Focus gates, hint lines,
   empty states, theme helpers, in-cell editing, stale-key guards — all landed on Navigator
   and mostly on Account, and none were backported to Today, Book, Markets or Team. F1,
   F4, F12 and F18 are all "the project rule exists and one screen predates it". This is
   the single most productive lens: for each convention in `CLAUDE.md`, grep for the
   screens that do not follow it.

2. **Layout was tuned against one window and does not degrade.** Percentages (`44%`,
   `52%`, `40%`) and `vh` maxima were chosen at a large size with nothing to catch the
   small case — because the snapshots are never compared (F14). F4, F5, F8, F13 and F25 are
   one root cause seen five times.

3. **The palette is Python data interpolated into Rich markup, not CSS.** That bought a
   real theme file and perfect consistency across tables, and it costs a working light mode
   and any user theming — while the command palette advertises theming anyway (F11). It has
   a second cost the review only found by measuring: a colour baked into a cell cannot be
   *unbaked* by a widget that wants to paint a selection behind it. `DataTable` overrides
   the foreground and is fine; `OptionList` does not, and F28 is the result. Any future
   widget that paints a cursor behind styled text will land in the same trap.

4. **Everything is synchronous and every refresh is total.** `on_screen_resume` rebuilds
   every table on the screen; there is no incremental update and no worker. Correct and
   simple at 20 accounts, unmeasured at 500 (F9, F7).

---

## 5. Proposed sequencing

**Batch A — "don't lie to me"** (F1, F2, F7, F11, F22, F23, **F28** — all S, roughly half a
day) Correctness and honesty: never act on a row you cannot see, never fail instantly on a
lock, never stack screens behind your back, never offer a theme that breaks, never hide the
text on the row the user just selected, never say "opening" for a file that is not there.
*Unlocks:* safe to run beside the MCP server, which is the point of the MCP server.

**Batch B — "works at any size"** (F4, F5, F6, F12, F13, F25 — M)
Everything the 80×24 pass surfaced, plus the empty states and the help screen. Do this
*after* F14 so the snapshots hold the line.
*Unlocks:* usable over SSH and in a tmux split, not just in a full window.

**Batch C — "faster than navigating"** (F10, F19, F20, F21 — M)
Command palette provider, clipboard, `bookctl open <ref>`, persisted filters. These are the
daily-driver features rather than repairs.
*Unlocks:* the shell-alias workflow — land in the right record from a terminal prompt.

**Batch D — "keep it fixed"** (F14, F16, F3, **F32** — S/M)
Snapshot comparison, pin Textual and guard the private-API use, app-level error screen.
Doing F14 first would have caught most of Batch B; doing it now stops Batch B regressing.

---

## 6. Explicitly out of scope, with reasons

- **In-app spell-checking of user prose.** The prose fields are `notes`, `detail` and
  `response`. Their vocabulary is carrier names, line abbreviations, insured names and
  broker jargon — exactly the words a dictionary flags. A custom `TextArea` highlighter
  would be almost entirely false positives on the content that matters, and the
  in-repo dictionary would need constant feeding. If long-form entry ever becomes common,
  add a `ctrl+e` `$EDITOR` handoff from the textarea and let the user's own editor do it.
  Building a spell-checker into the TUI is not worth it.

- **Migrating all Rich markup to theme variables.** Ten files of mechanical change to
  enable a light mode the app explicitly does not want (`theme.py:1`, "one warm dark
  palette"). Suppressing the theme command (F11) buys the same safety for one method.

- **Session restore of scroll position and cursor row.** Navigator already restores tree
  expansion and the selected node (`_restore_tree_place`, `navigator.py:360`), and Account
  already keeps the RFI master selection across refreshes (`account.py:591`). Those are the
  parts that matter. Persisting every table's cursor is a lot of state for very little.

- **`Collapsible` on the Navigator.** The `Tree` *is* the disclosure mechanism; adding a
  second one would duplicate it. `TabbedContent` is already used where it belongs.

- **Replacing the kanban's `Horizontal` + `Vertical` + `OptionList` composition.** There is
  no stock Textual widget for horizontal columns of selectable cards. This is already stock
  widgets composed correctly, not a hand-roll. (Its *width* behaviour is F25; its
  *structure* is fine.)

- **`ContentSwitcher` on the Navigator's right pane.** `_render_pane` toggling `.display`
  between a card and a table (`navigator.py:492–538`) is doing what `ContentSwitcher` does,
  but it also has to reset `inline_fields`, cancel an open cell editor and preserve the
  cursor. Swapping it in would move that logic, not remove it.

- **Confirmation dialogs on more destructive actions.** The app has this right already:
  `d`, in-cell edits and merges are undoable and say so in the toast (`u undoes`), and only
  the genuinely irreversible or wide-blast actions (renew, revert a batch, delete an
  interaction) get a modal. Adding more confirms would trade forgiveness for friction.

---

## 7. What I would fight for

**F1 (the Today focus gate)** — it is four lines, and until it is fixed the app will
occasionally save an edit to a record you never saw. Everything else on this list costs you
time; this one costs you data.

**F28 (the pipeline highlight)** — you found it before I did, and the fix is one
`Text.assemble` call. It is also the only finding here confirmed by someone actually using
the app, which makes it the best evidence in the document.

*(This slot previously held F2, which was withdrawn — see [F2](#f2).)*

**F14 (compare the snapshots)** — you already pay the whole cost of generating them. Every
layout finding in Batch B would have been a failing test instead of a review item, and
without it Batch B will quietly regress.

## 8. Where I think the current design is right and the obvious improvement is wrong

- **Rich markup instead of TCSS classes for colour.** The obvious review note is "move all
  those `f"[{theme.DIM}]…[/]"` strings into stylesheet classes". Do not. Table *cells* are
  `rich.Text`, not widgets — they cannot carry CSS classes, so the palette has to be
  reachable from Python anyway. Having one source (`theme.py`) that both the tcss variables
  and the cell renderers read is why the colour is actually consistent across nine screens.
  The cost is a light mode you do not want. Keep it; just stop advertising themes (F11).

- **`FormModal` draining widget values directly instead of using validators.** The obvious
  note is "use `Input.validators` and `Validator` classes". The current design parses on
  save through one `_parse` (`forms.py:231`), which is exactly what makes the in-cell editor
  share the *identical* parser (`inline_edit.py:118` calls `FormModal._parse`) so `next fri`
  means the same thing in a cell and in a form. Per-widget validators would fork that.
  Validate-on-save is also right for this app: nobody wants a red border while typing
  `2026-10-` on the way to a valid date.

- **No confirmation on `d` / in-cell edits, with `u` instead.** Correct, and the toasts say
  so. Resist any reviewer (including a future me) proposing confirm dialogs here.

- **The full-refresh-on-resume model.** Simple, always correct, and never shows stale data
  after an MCP write landed behind your back. Do not replace it with incremental updates
  until a measurement on the real book says you must.

- **`Static` + towerkit's own ASCII renderer for the tower preview.** The obvious note is
  "render it with Textual widgets". No — the renderer is towerkit's job and its output is
  the same one the user sees in towerkit itself. That is the whole point.

---

## Appendix — baseline screenshots

`docs/screenshots/`

- `wide-140x45/` — 23 SVG exports at 140×45
- `small-80x24/` — the same 23 at 80×24
- `wide-140x45.txt`, `small-80x24.txt` — the same screens as composited plain text
  (greppable; this is what the layout findings were measured against)
- `light-theme.svg` — the F11 evidence

Regenerate with the harness in `App.run_test()`; the screens are driven in the order
Navigator → attention lists → Today → Book → Account tabs 1–9 → Calendar → Pipeline →
Markets → Market detail → Team → Help → Search → Quick capture → New task → Settings.
