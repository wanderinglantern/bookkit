# Handoff — bookkit + towerkit, after the overnight review run

Written 2026-08-19, ~07:40. Assumes you have read `CLAUDE.md` in the repo root and **nothing
else**. Everything below is verified state, not intention — where I say a line number, I checked it
at the HEAD named here.

**Read `ROADMAP.md` next.** It has grown to 21 entries and is now the real backlog; this file tells
you where the code is, that file tells you what is left and why.

---

## 1. Where things stand

| repo | main | remote | suite |
|---|---|---|---|
| bookkit `/Users/grantgreeson/Developer/bookkit` | **`8257b02`** | identical | **1384 passed**, 38 snapshots, mypy clean (119 files), ruff clean |
| towerkit `/Users/grantgreeson/Developer/towerkit` | **`0405cca`** | identical | **766 passed** + 1 known environmental failure |

`git branch --no-merged main` is **empty in both**. Working trees clean.

**The one towerkit failure is not yours**: `tests/test_connector.py::test_roots_fall_back_to_bookkits_configuration`
fails on `main` too (no `bookctl` on PATH from that checkout). It does **not** fail inside a fresh
`git worktree`, which is a quirk worth knowing when a number looks better than expected.

### Stale worktrees — safe to remove

All five bookkit worktrees hold branches that are **already merged**:

```
.claude/worktrees/web-account     test-truth              (merged)
.claude/worktrees/web-batch-join  soi-client-safety       (merged)
.claude/worktrees/web-defects     data-safety-fixes       (merged)
.claude/worktrees/web-forms       form-validation-and-rfi (merged)
.claude/worktrees/web-work        boundary-fixes          (merged)
```

Do not delete `.claude/worktrees/towerkit` — it is a symlink to the real towerkit and is what makes
the `path = "../towerkit"` dependency resolve from inside a worktree.

---

## 2. The goal, and what actually happened

The night began as a feature queue (quotes, assignee, MCP derivation, fonts, editor friction) and
turned into something else once an **eight-reviewer expert panel** ran against both repos. It
returned **thirteen reproduced criticals** — not one theoretical — and most of the night's later
work was fixing them. All thirteen are fixed, gated and merged.

**The finding that recontextualises everything**: `test_every_write_tool_returns_a_batch_ref`
checked the *receipt*, not the undo unit. Make `db.current_batch()` return `None` — every event
written with a NULL `batch_id`, the undo spine completely dead — and **26 tests passed**. Fifteen
write tools, including all four `program_*` ones, had no revert coverage anywhere in the suite.

So: **for most of this build, "the suite is green" was a weaker statement than anyone was treating
it as.** That is now fixed (`test_every_write_tool_stamps_its_events_with_that_batch`), but treat it
as the standing lesson rather than a closed ticket.

---

## 3. What shipped, with where it lives

### New modules (bookkit)
- `src/bookkit/mcpsurface.py` — the MCP editable field table, **derived** from the form builders
  behind an explicit denylist. `DENIED`, `NOT_A_COLUMN`, `SYSTEM_COLUMNS`, `UNMAPPED_BUILDERS`,
  `ALSO_EDITABLE`, `VALUE_RULES`. Published for Grant at
  https://claude.ai/code/artifact/bb7504bf-2840-470a-ad46-2cae3d98bbd0
- `src/bookkit/mcpparity.py` — entity × verb ledger; fails the suite in **both** directions.
- `src/bookkit/web/origin.py` — `OriginGuard` ASGI middleware. Host check on every request, Origin
  check on writes.
- `src/bookkit/services/quotes.py` — quote expiry vocabulary (`expiry_state`, `expiry_word`).
- `src/bookkit/repo/assignees.py` — the only writer of the three assignee columns.

### New module (towerkit)
- `src/towerkit/render/web.py` — pure, plotting-free tower renderer for the web panel (slice 1
  phase A). **Phase B — the bookkit route and template that consume it — is NOT built.**

### Migrations added
`migrations/012_quote_terms.sql` (quote expiry + `submission_subjectivity`) and
`migrations/013_task_assignee.sql` (three nullable columns + index). Both additive.

### Key functions you will want
| what | where |
|---|---|
| org rename duplicate guard | `repo/orgs.py:13 guard_name` + `mcpserver.py:1992 _RENAME_GUARDS` |
| backup + snapshot | `db.py:288 snapshot_before_migrations`, `db.py:477 backup`, `db.py:391 check_migration` |
| SOI per-row expiry / wrong-file guard | `services/export_open_items.py:635 _run_off`, `:777 _wrong_account`, `:928 soi_problems` |
| select validation | `forms/spec.py:132 checked_option` |
| follows healing | towerkit `edit.py:157 heal_follows`, called from bookkit `sync.write_through` |

---

## 4. Decisions made, and why — the ones you must not silently reverse

- **A NULL `source_sha256` may NOT short-circuit the write-conflict guard.** A mismatched sha means
  we saw the file and it moved on; a missing one means we never verified it at all — strictly less
  knowledge, so it is the case to refuse *hardest*. `seed()` now projects properly.
- **A name collision on the assignee degrades to OURS, never yours.** A task wrongly marked ours is
  chased twice; wrongly marked theirs is chased by nobody.
- **The client's You/Us derives from `assignee_kind`, never by matching a name.** Typing "Sam" for
  "Sam Garcia" would otherwise silently flip a client-facing column.
- **A mislinked program file refuses the FILE, never the export**, and is compared by **org id**
  through `program_link` — not by name. Losing on a name costs layer detail; being wrong ships a
  competitor's tower.
- **Prose still beats structure on the SOI**, but a layer carrying both prose limits and
  `namedLimits` is now **refused** — otherwise the structured data is discarded in silence.
- **The Internal-heading suppression is a PREFIX match** (exact match is what withholds), and the
  de-labelled rows go to **General**. My original ruling said a headerless section was not
  expressible; that was **wrong** — the renderer supports it. The reason to choose General is
  different and better: sections render back-to-back with no separator and the zebra banding
  restarts, so rows under no banner read as belonging to the section above.
- **Origin checking, not CSRF tokens.** There is no session, cookie or signing key to build on, for
  one person on one machine. The Host check is what closes DNS rebinding — a rebinding page *is*
  same-origin, only the name is wrong.
- **towerkit is never taught CRM concepts.** Two SOI items were refused as data questions rather
  than faked as strings, and that refusal was right both times.
- **`Partially bound` stays** in the SOI status vocabulary. Collapse it into `Bound` and a
  60%-placed layer's whole premium re-enters the bound subtotal — the defect the change exists to
  remove.

---

## 5. What is next, in order

Nothing is blocked on Grant. He has answered every open question except two, both optional (see §7).

1. **towerkit slice 1 phase B** — the bookkit Program tab consuming `render/web.py`. The Program tab
   is still the stub at `src/bookkit/web/templates/account/program.html`. **Before writing code,
   read `docs/superpowers/specs/2026-08-17-towerkit-web-conversion.md` D2.1** (the R66 agreement
   rule) and note that the agreement test's *mechanics* were deliberately left unspecified — two
   rounds of specifying it against a module that did not exist both produced a test that conflated
   *wrapping* (a permitted fit difference) with *fact divergence*.
2. **The web's dead nav (A4).** Grant chose "wire them". Those six items — Today, Navigator,
   Pipeline, Calendar, Markets, Towers — have **nowhere to point**: no route exists outside `/book`
   and the account page. So this is six screens, not six links. My sequencing: **Today** first
   (attention surface, TUI composition reusable), then **Towers**. Nothing ships pointing at an
   empty shell.
3. **Post-bind through issued** (A1, "queue it") — binder date, `issued` status, policy number. The
   AE's words: *"this is where E&O comes from."*
4. **`ROADMAP.md:9`** — two attention tables paint past their own right edge. The navigator's
   renewals table and Today's *tasks* pane (`container 65 / virtual 79`, truncating account names).
   `tests/test_reachable.py` asserts `virtual <= container` for Today's *renewals* pane only;
   extend it.
5. The remaining ROADMAP entries — deactivating records generally, the program-identity modelling
   question (that one wants a spec, not a task), the stale-Edit sweep, the revert enumeration
   oracle.

---

## 6. Things tried that did NOT work — do not repeat them

- **`git checkout -- <file>` to restore after a mutation destroys your own uncommitted work.** It
  cost me a set of mutation proofs. Commit first, or restore from a saved copy.
- **Asserting that a mutation's file changed is not enough** — the test process can load stale
  `__pycache__` bytecode for a module you just rewrote and report a false PASS. Run with
  `PYTHONDONTWRITEBYTECODE=1` and purge `__pycache__` between runs.
- **A `str.replace` mutation that matches nothing gives a green run that proves nothing.** Assert
  the anchor is unique *and* that the text is present afterwards.
- **Gating only the branch is not enough.** Three times two branches that each passed were wrong
  together — a guard one added governing a rule another wrote, a new entity the MCP ledger did not
  know, and a test speaking from a Host the new origin guard refuses. **Gate the merge commit.**
- **Citing line numbers in a brief goes stale fast** when many branches land in a night. I gave
  wrong line numbers three times, and once gave the wrong *file* twice in a row
  (`web/routes/account.py:620` when the survivor was `tui/screens/account.py:620` — same line,
  different file). Cite function names.
- **A magnitude cap on money was considered and rejected**: it would not have caught the 100× bug
  (a $5M value landing at $500M is inside any plausible cap), and a refusal in a shared parser makes
  records unsaveable.

---

## 7. Gotchas that will bite you

- **Gates are `uv run --no-sync python -m pytest -q`.** A bare `uv run pytest` falls through to
  Anaconda's and reports a bogus `ModuleNotFoundError: No module named 'bookkit'`.
- **Background-task notifications report the wrapping shell's exit code, not pytest's.** Redirect to
  a file and read the file.
- **towerkit work goes in its own `git worktree`.** Every bookkit worktree compiles against whatever
  branch is checked out in `/Users/grantgreeson/Developer/towerkit`, and a branch left there twice
  made clean bookkit branches gate red. To tell a contaminated gate from a real one:
  `uv run --no-sync python -c "from towerkit import soi; print(hasattr(soi,'SoiStatus'))"` from
  bookkit, plus `git -C /Users/grantgreeson/Developer/towerkit branch --show-current`.
  A fresh worktree needs `uv sync --group dev`.
- **Never `git add -A`.** `snapshot_report.html` regenerates on every snapshot run and is
  deliberately tracked; `.playwright-mcp/` is now gitignored.
- **`web/origin.py` means every `TestClient` needs `base_url="http://127.0.0.1"`.** TestClient's
  default Host is `testserver`, which is exactly the forged name the guard refuses.
- **towerkit's schema exists TWICE** (`schema/program.schema.json` and
  `src/towerkit/schema/program.schema.json`), kept identical by a test — and the canonical round
  trip never goes through jsonschema, so a field added to `model.py` alone passes every round-trip
  test and fails at runtime on a real file.
- **Adding a key to a towerkit hint line can overflow it silently.** `#key-hint` is one row with no
  scrolling; overflow is *gone*, starting from the right. It happened at 146 columns against a
  138-column box.
- **A name imported into a module that already uses it as a local is a landmine** — the assignment
  makes it local for the whole function, so the call raises `UnboundLocalError`. Cost 33 red tests.
  Module-qualify instead.
- **Every batch ref is still prefixed `MCP-`** regardless of source (`repo/batches.py`). A web-made
  change shows as `MCP-0001` with `source='web'`. Cosmetic, user-visible, deliberately unfixed —
  refs are already written into Grant's real book.

### One production hole flagged and NOT patched

`repo/orgs.py set_parent` walks the parent chain without an `alive()` filter. It is on a documented,
staleness-checked allowlist at `tests/test_repo.py:70 _ALIVE_EXEMPT` **on purpose**: adding
`alive()` would make the cycle walk *stop* at a soft-deleted ancestor and let a cycle through it
pass, and a soft-deleted org can be undeleted. That is a judgement call on the data model. If you
disagree, it is one dict entry.

---

## 8. Open for Grant — neither blocking

- **The denylist** — published, with a strike control on every entry. He has seen it; nothing has
  come back.
- **C14** — anything the client-side CFO reviewer got wrong. Blank is a fine answer.

The build log he reviews from is kept current at
https://claude.ai/code/artifact/18901ee0-3530-4ff6-9b2d-96325ee550f5 — every section has a feedback
line, and answers arrive pasted into chat as `Build log answers: …`.
