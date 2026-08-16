# Handoff — 2026-08-15 — TUI batch spine, undo sweep, and the audit fixes

## Goal

An 11-agent usability audit drove the live app and found 20 CRITICAL and 64
IMPORTANT issues. Grant approved fixing them, starting with the one
architectural cause behind a whole cluster: **TUI writes never went through
`db.transaction(batch=)`**, so `R` could not reach them and `u` was carrying
weight it was never designed for.

- Audit report: https://claude.ai/code/artifact/8ad54adc-9a2c-43b6-b363-583d1dc798d0
- Build log:    https://claude.ai/code/artifact/b358c736-94f8-444b-857a-26440a70942a

**Every CRITICAL is now closed**, plus the silent-no-op, dead-key and
market-numbers clusters from IMPORTANT.

## State

- Branch `fix/tui-batches-undo-sweep`, in worktree
  `.claude/worktrees/batch-undo`, branched off `0607582`.
- **Nothing is committed on the branch.** All 89 changed/added files are
  uncommitted, deliberately — it is Grant's to review. `main` has since moved
  on with docs-only commits (this handoff, `changelog.md`, CLAUDE.md); the
  branch does not need rebasing.
- `main` also carries ~9 files of **someone else's uncommitted RFI-state
  work** (`rfi_state_cell` in `tui/widgets/tables.py` and friends). Untouched
  throughout. Do not disturb it.
- Gates on the branch: `mypy` 0, `ruff` 0, **`pytest` 741 passed against 741
  collected** (612 at branch point, +129 new), 38 snapshots.

### Running the gates

```
cd .claude/worktrees/batch-undo
uv sync --group dev                       # ONCE per fresh worktree — see gotchas
uv run --no-sync python -m pytest -q      # NOT `uv run pytest`
uv run --no-sync mypy src
uv run --no-sync ruff check src tests
```

Redirect output to the scratchpad and gate on the command, never pipe before
the `&&`. A full run is ~3 minutes; run it with `run_in_background` and wait
rather than fighting a foreground timeout.

## What landed, and where

### The batch spine (everything else sits on this)

| Where | What |
|---|---|
| `db.py:72` `_tx_depth`, `db.py:123` `transaction()` | Nesting JOINS rather than raising — SQLite has no nested BEGIN. An inner `batch=` is deliberately ignored; the outermost writer action owns the undo unit. |
| `services/batches.py:27` `open_batch()` | One implementation, two surfaces, taking `source='mcp'` or `'tui'`. `mcpserver._open_batch` delegates to it. |
| `tui/widgets/forms.py:63` `BatchSpec`, `:274` `_run_commit` | **Batching is ON BY DEFAULT** in `FormModal`; `batch=False` opts out. A commit returning an error string raises `_Refused` inside the transaction, so a refused save rolls back — the form still stays open with input intact. |
| `tui/widgets/entity_actions.py:30` `batched_write()` | The keystroke equivalent, for direct writes: d done, D drop, delete interaction, inline edits, appetite delete, merges, stage moves. |
| `repo/base.py:69` `_assert_known_field` | An `event_log` field must be a real column or declared in `events.NON_MUTATION_FIELDS`. Refuses at write time rather than exploding inside `u` days later. |

### Undo

`services/undo.py:44` `undo_last` finds the newest un-reverted `event_batch`
with `source='tui'` (`repo/batches.py:92` `last_undoable`) and reverts it
through `services.batches.revert`. `u` and `R` are now one code path.
`services/undo.py:32` `SOURCE = "tui"` is the scoping Grant approved.

### The rest, by cluster

- **Crash class** — `search.py`, `team.py` and `markets.py` keyed OptionList
  options / DataTable rows on a non-unique `org_id`; all three now key on the
  entity's own id. `tui/app.py:274` `_guard_message_dispatch()` patches
  `MessagePump._dispatch_message` so a raising message handler is no longer
  fatal. `account.py:1237` `_acting_key` gates row actions on focus.
- **Wrong dates** — Today, Book, the account header and the calendar print
  `RenewalItem.renewal_on`, not `placement.period_to`. The calendar gained an
  `overdue` column and drives `◆` off `days_remaining < 0`, not grid position.
- **CLI safety** — the `cli.py` seed branch guards a non-empty book (`--force`
  takes a `db.snapshot` first); `cli.py:143` `_refuse_a_missing_book` stops
  read commands creating a database at a typo; `cli.py:156` `main()` wraps
  everything below argparse. `.resolve()` added in `cli.py` roots and
  `connector.py fields()`.
- **Form entry** — `money.py parse_money_cents` accepts `1,234.56`;
  `dates.py parse_human_date` refuses bare 1–2 digit input; the team name
  guard moved to `repo/team.py::_guard_name`.
- **Layout** — `bookkit.tcss` exempts `Footer` from the global scrollbar rule;
  `forms.py DEFAULT_CSS` gives `.modal-fields` `height: 1fr`; both import
  previews are `VerticalScroll` with the verdict pinned outside
  (`imports/staging.py::verdict`).
- **Market numbers** — `projection.carrier_exposure` carries `status`;
  `submissions.market_counts` adds `decided`; `hit_rate` divides by decided
  and returns `None` (not 0.0) when nothing has come back.

### New tests (all mutation-verified)

`tests/test_batch_spine.py`, `test_undo_batch.py`,
`test_merge_and_stage_batches.py`, `test_crash_class.py`,
`test_renewal_dates.py`, `test_cli_safety.py`, `test_form_entry.py`,
`test_layout.py`, `test_dead_keys.py`, `test_market_numbers.py`.

Two are **guards against recurrence** and will fail future work on purpose:

- `test_layout.py` — every screen's `Footer.virtual_size.width <=
  container_size.width` at 140x45. When a new `show=True` binding fails it,
  demote one; do not raise the ceiling.
- `test_dead_keys.py` — every key named in a hint line resolves to a live
  binding, per screen and per account tab.

## Next step

Two remaining audit items, both circling the same gap: the app takes backups,
and it cannot tell you what it changed.

1. **`bookctl restore` (audit G1).** The backup half is solid — VACUUM INTO,
   integrity-checked, 0600, taken before the first row changes, now through
   the single `db.snapshot` used by both importers and `seed --force`. There
   is no restore in the CLI or the docs, so recovery means quitting and
   `cp`-ing a `.bak` over the live DB with its `-wal`/`-shm` sidecars.
   Nothing prunes `backups/` either — every pasted email signature writes a
   full copy, forever.
2. **The audit trail (audit G2).** `repo/events.history` and `field_history`
   have **zero consumers** anywhere in TUI, CLI or MCP. The only change
   surface is the navigator's MCP CHANGES section: assistant batches, 14
   days. Every TUI write is now a batch carrying `tool`, `summary` and
   `org_id`, so "what did I change today?" is close to a query plus a table —
   and it is the natural companion to `u` being batch-granular.

Then smaller copy work from the audit: `SUBMISSIONS PAST SLA` never states
the threshold; Markets, Pipeline, Calendar and search results have no empty
states; the `opp`/`plc` codes in the needs table have no legend.

## Decisions made this session

- **Work in a worktree.** `main` had uncommitted peer work, and CLAUDE.md
  documents a 2026-08-13 incident where a `git checkout` on a shared working
  dir put a commit on the wrong branch. Rejected: branching in place, and
  working in the dirty tree.
- **`db.transaction` nests by joining.** Rejected auditing and unwrapping
  every inner transaction first — far larger blast radius for the same
  result. Consequence: an inner `batch=` is silently ignored, documented on
  the function and covered by a test.
- **Batching defaults ON.** Opt-in would leave whichever call site was missed
  silently unbatched — precisely the failure being fixed.
- **A refused save rolls back.** Real behaviour change: previously a commit
  that wrote two rows and then returned an error left those rows behind.
- **`u` scoped to `source='tui'`** (Grant, 2026-08-15), superseding CLAUDE.md's
  earlier "single-step / field-granular" line, which has been amended.
- **Exposure carries status rather than filtering to bound.** A quoted tower
  is real exposure worth seeing; filtering would also have emptied the screen
  on seeded data, hiding the problem rather than fixing it.
- **Hit rates return `None`, not 0.0, when nothing is decided.** "Nobody has
  answered" and "everybody declined" are different facts.
- **`revert(force=True)` that applies nothing is now `applied=False`** and no
  longer marks the batch reverted. This changed a deliberately-tested
  contract — see open questions.

## Things that did not work

- **Batching `entity_actions.push_form` alone.** CLAUDE.md points new screens
  at that wrapper, so it looked like the single chokepoint, and the suite went
  green. The end-to-end test then failed: **33 call sites construct
  `FormModal` directly**, including `ctrl+t`. The default moved into
  `FormModal.__init__`.
- **Hooking `App._handle_exception` for the message-handler net.** By the time
  Textual calls it, the pump that raised has already stopped, so the app
  "survives" with a widget that silently never processes another message —
  worse than crashing, because it looks fine. It presented as a test hang, not
  a pass. The catch must sit inside `_dispatch_message`.
- **A focus gate requiring THIS table to have focus.** It broke `l`, which
  deliberately edits the shown placement's layer while the carriers table has
  focus. The gate is "focus is on a table", which refuses chrome without
  breaking sibling-table flows.
- **Trusting the audit on `i paste import`.** Two independent reviewers
  reported it dead on three account tabs, citing a comment about the binding
  moving to `I`. **Both were wrong** — that comment is about `D` and `P`
  (paste RFI items); `i` is `import_here` on every tab. I edited the hints
  before the new guard test showed they had been right all along. Reverted.
  Two agents agreeing is not verification.
- **`Footer.render_lines(footer.region)`** returns empty regardless of state,
  so a test built on it passes and fails for the wrong reasons. Measure the
  composited row instead.
- **`show_horizontal_scrollbar` as a "footer fits" assertion.** Once the
  overflow is hidden it is always False, so the test would have passed
  forever. Compare content width against container width.

## Gotchas

- **A fresh worktree needs `uv sync --group dev`.** Without it `uv run pytest`
  silently falls through to Anaconda's pytest, which cannot import bookkit and
  reports `ModuleNotFoundError` from `conftest.py` — which looks exactly like
  a broken editable install and is not. Use
  `uv run --no-sync python -m pytest`.
- **`.claude/worktrees/towerkit` is a symlink** to `../../towerkit`, and it is
  what makes the `path = "../towerkit"` dependency resolve from inside a
  worktree. Don't delete it.
- **A green exit code does not prove the whole suite ran.** One run reported
  621 passed with exit 0 and no skip or error line — 43 short. It never
  reproduced across four later runs; cause unconfirmed. Compare the number
  against `--collect-only`.
- **`run_test()` disables notifications** unless `notifications=True`, so
  toast assertions otherwise see an empty list and a reviewer concludes the
  app shows no error messages at all.
- **`BLAST_CAP` (250) now applies to TUI writes**, including merges — whose
  bulk moves are event-logged row by row so a merge can actually be reverted.
  A merge touching more than 250 entities will be refused. Nothing in the seed
  comes close; a large carrier merge on real data might. `BatchState(cap=)`
  already exists to raise it per batch.
- **Imports stay unbatched on purpose** (their snapshot is the rollback), so
  `u` after an import correctly reports "nothing to undo".
- **38 snapshot baselines were re-baselined across three phases**, each render
  read before being accepted. They no longer encode any known bug — which was
  not true at the start of this branch.
- Re-baselining surfaced a genuine flake: `interactions.attendees` ordered by
  `last_name` alone, which ties for two contacts sharing a surname and then
  falls back to random-tailed ids. Stable within a process, unstable across
  them. Fixed with a `last_name, first_name, rowid` tiebreaker.

## Open questions for Grant

- **The `revert(force=True)` contract change.** It used to return
  `applied=True` with an empty `reverted` list and mark the batch reverted —
  burning it, so the user could never put it back even after undoing their own
  edit. `applied` now means "the book moved". That required editing
  `test_batches_service.py::test_user_edits_to_a_batch_created_row_block_its_revert`,
  which asserted the old behaviour on purpose. I believe the change is right
  (`revert_batch`'s own docstring says "applied: false means nothing was
  written"), but it is yours to overrule.
- **Does `bookctl restore` belong on this branch or its own?** Open since the
  start of the session.
- **How should this branch land?** 89 uncommitted files. The phases are
  cleanly separable and each was gated independently, so one commit per phase
  is available if you'd rather not take it as a single lump.
