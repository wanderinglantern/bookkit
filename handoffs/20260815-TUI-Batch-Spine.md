# Handoff — 2026-08-15 — TUI batch spine + undo sweep

## Goal

Route every TUI write through the batch machinery (`db.transaction(batch=)` /
`services/batches.py`), then rewrite `u` on top of it. Grant approved this
after an 11-agent usability audit found that TUI writes were unbatched and
untransacted, which was the single root cause behind a cluster of criticals:
half-applied merges, "undoable with u" promises that were false, a lost deal
that could not be reopened, and `u` reverting sync-projection writes the user
never made.

Audit report: https://claude.ai/code/artifact/8ad54adc-9a2c-43b6-b363-583d1dc798d0
Build log:    https://claude.ai/code/artifact/b358c736-94f8-444b-857a-26440a70942a

## State

- Last commit: `0607582` — chore: wheelhouse that can actually satisfy mcp,
  and the hash the release actually has
- Branch: `fix/tui-batches-undo-sweep`, in worktree
  `.claude/worktrees/batch-undo`, branched off `0607582`. Note `main` has since
  moved to `72383af` (docs only: this handoff, `changelog.md`, CLAUDE.md), so
  the branch is one docs commit behind and does not need rebasing to continue.
- Uncommitted changes: **everything below is uncommitted on that branch.**
  Nothing has been committed. Deliberate — the branch is Grant's to review.
  - `src/bookkit/db.py`
  - `src/bookkit/services/batches.py`
  - `src/bookkit/mcpserver.py`
  - `src/bookkit/repo/base.py`
  - `src/bookkit/repo/events.py`
  - `src/bookkit/tui/widgets/forms.py`
  - `src/bookkit/tui/widgets/entity_actions.py`
  - `tests/test_batch_spine.py` (new)
- Gates green on the branch: `mypy` 0, `ruff` 0, `pytest` **664 passed**
  (baseline at branch point was 612; +52 new tests), 38 snapshots passed.
  Confirmed twice, in both ordered and randomised order, against 664
  collected. (One run mid-phase reported 621 passed with exit 0 and no skip
  or error line — 43 tests short. It did not reproduce; most likely pytest
  collected while a test file was being rewritten. Worth knowing that a
  green exit code here does not by itself prove the whole suite ran.)
- **Six snapshot baselines were deliberately updated** in phase 5 (book,
  calendar, today at both sizes); the other 32 are untouched. Each new render
  was read before being accepted.
- Also modified since first writing: `services/undo.py` (rewritten),
  `services/merge.py`, `services/batches.py`, `repo/batches.py`,
  `tui/screens/{account,navigator,markets,pipeline}.py`, and 8 reassign
  helpers in `repo/{tasks,submissions,documents,contacts,orgs,interactions}.py`.
  New test files: `tests/test_undo_batch.py`,
  `tests/test_merge_and_stage_batches.py`.

## Just finished

**Every audit CRITICAL is closed**, plus the silent-no-op and dead-key
clusters from the IMPORTANT list. There are no unbatched TUI write paths left, the
three keystrokes that used to kill the session no longer can, and every screen
counts down to the date it prints.
Every write — forms, keystroke actions, merges and pipeline stage moves — is
one atomic, `R`-revertible batch. `u` now undoes the last *writer action*
through the same code path `R` uses, scoped to `source='tui'` (Grant's call,
2026-08-15), so it can no longer revert a sync projection or an assistant
write. The recurring `NON_MUTATION_FIELDS` bug class is closed structurally.

Seventeen audit criticals are genuinely fixed and asserted: **C4** (merges ran 4-10
writes with no transaction), **C5** (the MergePicker's "undoable with u" was
false), **C10** (the lost-deal bug), **C2** (three DuplicateID app-kills),
**C16** (`r` renewing from the tab bar), **C18** (a program-batch revert
killing the app), **C11** (four screens printing `period_to` beside a
`renewal_on` countdown), **C12** (the calendar hiding every overdue renewal),
**C13** (the 80x24 countdown truncating `57d` into a plausible `5` — fixed
incidentally by shortening the book's headers; verified by rendering, not
assumed), **C1** (`seed --demo` doubling a book in use), **C19** (two missing `.resolve()` calls breaking the towerctl and Cowork
contracts), **C15** (money fields pre-filling a value their own parser
rejected), **C20** (a bare `5` saving a date nine months out), **C17** (the TUI rename bypassing the duplicate guard), **C3** (the Footer
painting blank on 6 of 9 screens), **C14** (the modal Save button outside its
own box), and **C6** (the import preview unable to show its own verdict).

**All 36 snapshot baselines that moved were read before being accepted** —
six in phase 5, thirty in phase 8. They no longer encode any known bug.

**The 10 tests in `tests/test_batch_spine.py` are mutation-verified**, not just
green. Each mutation was applied to the specific line the test defends, the
failure observed, and the file restored:

| Mutation | Tests that failed |
|---|---|
| Defeat `_tx_depth` reentrancy in `db.transaction` | the 3 nesting tests |
| Revert `FormModal` batching to opt-in | `a_form_save_lands_as_one_revertible_batch` |
| Drop the `_assert_known_field` call from `log_event` | `an_undeclared_event_field_is_refused_at_write_time` |
| Restore the old 2-name `NON_MUTATION_FIELDS` | `declared_bookkeeping_fields_are_allowed`, `undo_steps_past_provenance…` |

**Correction, now resolved.** I first described
`test_multi_field_write_reverts_as_one_unit` as "the lost-deal fix". It was
not — it calls `open_batch` directly, so it proved only the mechanism. The
real fix landed afterwards in `tui/screens/pipeline.py`
(`action_close_lost`, `action_advance_card`), and is asserted end-to-end by
`test_closing_a_deal_from_the_board_is_undoable_end_to_end`, which drives the
real board (`p`, `<`, `u`) rather than opening the batch itself. Unwrapping
the keystroke fails that test.

**The general lesson, applied twice now:** a test that sets up the new seam
itself proves the mechanism, not that production code uses it. Both times the
end-to-end version caught a real gap the mechanism test could not.

## Next step

Two items from the audit remain, both about the same gap — the app can take a
backup and can no longer be told what it changed:

1. **`bookctl restore` (audit G1).** The backup half is solid: VACUUM INTO,
   integrity-checked, 0600, taken before the first row changes by every
   importer and now by `seed --force` too, through the one `db.snapshot`.
   There is still no restore — not in the CLI, not in the docs — so recovery
   means quitting and `cp`-ing a `.bak` over the live DB with its `-wal` and
   `-shm` sidecars. Nothing prunes `backups/` either.
2. **No audit trail (audit G2).** `repo/events.history` and `field_history`
   have zero consumers anywhere in the TUI, CLI or MCP. The only change
   surface is the navigator's MCP CHANGES section: assistant batches, 14
   days. Now that EVERY TUI write is a batch carrying a summary and an
   org_id, "what did I change today?" is close to a query plus a table — and
   it is the natural companion to `u` being batch-granular.

Then the smaller copy work: `SUBMISSIONS PAST SLA` never defines the
threshold, Markets/Pipeline/Calendar/search have no empty states, and the
`opp`/`plc` codes in the needs table have no legend.

## Decisions made this session

- **Work in a worktree, not on `main`.** `main` has ~9 files of uncommitted
  in-flight RFI-state work (`rfi_state_cell` in `tui/widgets/tables.py` and
  friends, with tests and updated snapshots). CLAUDE.md documents a
  2026-08-13 incident where a peer session's `git checkout` on a shared
  working dir landed a commit on the wrong branch. Rejected: branching in
  place (changes HEAD for any peer session in the same dir), and working
  in the dirty tree (my changes and theirs become inseparable).

- **`db.transaction` nests by JOINING rather than raising.** SQLite has no
  nested `BEGIN`. Once a batch wraps a whole writer action, inner helpers that
  already open their own transaction (`entity_actions.py:309` RFI paste,
  `services/merge.py`) would hit "cannot start a transaction within a
  transaction". Implemented with a `_tx_depth` ContextVar in `db.py`: only the
  outermost issues BEGIN/COMMIT. Rejected: auditing and unwrapping every inner
  transaction first — far larger blast radius for the same result.
  **Consequence:** an inner `batch=` is silently ignored. Documented on the
  function, and covered by
  `test_inner_batch_is_ignored_so_the_outer_action_owns_the_undo_unit`.

- **Batching defaults ON in `FormModal`, with `batch=False` to opt out.**
  Opt-in would leave whichever call site I missed silently unbatched — exactly
  the failure being fixed. Opt-out is the safer polarity.

- **A refused save now rolls back.** A `commit` callback returning an error
  string raises `_Refused` inside the transaction, which is unwrapped back to
  that string afterwards. The commit-in-place contract still holds (form stays
  open, input intact) because rollback happens before the error surfaces.
  This IS a behaviour change: previously a commit that wrote two rows then
  returned an error left those rows behind.

- **Bookkeeping fields must be declared or the write fails.** Rejected: adding
  the three missing names and moving on, which is what the previous three
  rounds of this bug did. `base._assert_known_field` makes the next one fail
  loudly at the write that causes it.

- **One `open_batch`, two surfaces.** Moved out of `mcpserver.py` into
  `services/batches.py` with a `source` stamp; the MCP server delegates.
  Rejected: a second TUI-side copy, which is how the two drifted before.

## Anything tried that didn't work

- **Batching `entity_actions.push_form` alone was insufficient.** CLAUDE.md
  points new screens at that shared wrapper, so it looked like the right single
  chokepoint, and the full suite went green after the change. The end-to-end
  test then failed: **33 call sites construct `FormModal(...)` directly and
  bypass `push_form` entirely** — including `ctrl+t` (`tui/app.py:112`), the
  plainest write in the app. Fix was to move the default into
  `FormModal.__init__`. Lesson: a green suite proved only that nothing broke,
  not that the new behaviour engaged.

- **`uv run pytest` in a fresh worktree silently runs Anaconda's pytest.**
  The dev dependency group is not installed by a bare `uv sync`, so `uv run
  pytest` fell through to `/opt/anaconda3/bin/python3`'s pytest, which cannot
  import `bookkit` from the worktree venv. It presents as
  `ModuleNotFoundError: No module named 'bookkit'` in `conftest.py`, which
  reads like a broken editable install and is not. Fix:
  `uv sync --group dev`, then run `uv run --no-sync python -m pytest`.
  (`pyproject.toml:38-42` already carries a comment about a fresh worktree
  going red for a related reason — this is a second, different trap.)

- Two of my own test fixtures were wrong on first run, worth knowing:
  `status="lapsed"` violates the org CHECK constraint (valid: prospect,
  active, dormant, lost, declined), and `batches_repo.recent(conn)` requires a
  `since` argument.

## Gotchas / in-flight

- **`main` has uncommitted RFI-state work. Do not disturb it.** It looks
  complete (tests + updated snapshots) but is not mine to commit.
- **Three worktrees are live**: `batch-undo` (this one), `sync-one-path`,
  `team-crud`. Peer sessions may be active. Redirect all gate output to the
  scratchpad, never `/tmp`.
- **`.claude/worktrees/towerkit` is a symlink** to
  `/Users/grantgreeson/Developer/towerkit`. That is what makes the
  `path = "../towerkit"` dependency resolve from inside a worktree. Don't
  delete it.
- **`BLAST_CAP` (250) now applies to TUI writes too**, because they are
  batched. Any TUI flow that touches more than 250 entities in one save will
  now be refused. Nothing known does, but a cascade-style flow could.
- **Snapshot baselines are NOT re-baselined.** Deferred deliberately until the
  layout fixes (C3 footer, C13 book column order, C14 modal clipping) land, so
  the diff can be read once and in context. Note the baselines currently
  encode four known bugs — the blank footer, the truncated countdown, and the
  missing Save button at 80x24.
- **`forms.py` `DEFAULT_CSS` still has the C14 conflict**: `.modal-fields`
  `max-height: 55vh` fights `.modal-box` `max-height: 80%` from
  `bookkit.tcss:159-166`. Untouched so far.
- The audit found **three `DuplicateID` app-kills** (search, team, markets)
  from `OptionList` options keyed on a non-unique `org_id`, all firing from
  message handlers that `App.run_action`'s crash net does not cover. Not yet
  fixed; `/` then typing `ca` still kills the session on seeded data.

## Open questions for Grant

- **Resolved 2026-08-15:** `u` is scoped to `source='tui'`. CLAUDE.md on the
  branch has been updated to match.
- **`bookctl restore` (audit G1) — this branch or its own?** Still open, and
  now sharper: `u` after an import correctly says "nothing to undo", and the
  only rollback is a `.bak` file the app never tells you how to use.
- **One contract change needs your eye.** `revert(force=True)` on a fully
  conflicted batch used to return `applied=True` with an empty `reverted`
  list and mark the batch reverted, burning it. `applied` now means "the book
  moved". That required editing
  `test_user_edits_to_a_batch_created_row_block_its_revert`, which asserted
  the old behaviour deliberately. I think the change is right —
  `revert_batch`'s own docstring says "applied: false means nothing was
  written" — but it is yours to overrule.
- **Blast cap now applies to merges.** Bulk moves are event-logged row by row
  so a merge can actually be reverted, which means a merge touching more than
  `BLAST_CAP` (250) entities will now be refused. Nothing in the seeded book
  comes close, but a large carrier merge on your real data might. If that
  bites, `BatchState(cap=)` already exists to raise it per batch.
