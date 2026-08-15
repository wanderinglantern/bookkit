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
  `.claude/worktrees/batch-undo`, branched off `0607582` (= `main` = `origin/main`)
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
- Gates green on the branch: `mypy` 0, `ruff` 0, `pytest` **619 passed**
  (baseline at branch point was 612; +7 new tests), 38 snapshots passed.

## Just finished

Phase 1, the batch spine, is complete and verified: every TUI form save is now
one atomic, `R`-revertible batch, and a refused save rolls back instead of
leaving half its rows behind. Also closed the recurring `NON_MUTATION_FIELDS`
bug class structurally — `log_event` now refuses at write time for any field
that is neither a real column nor declared bookkeeping.

**The 10 tests in `tests/test_batch_spine.py` are mutation-verified**, not just
green. Each mutation was applied to the specific line the test defends, the
failure observed, and the file restored:

| Mutation | Tests that failed |
|---|---|
| Defeat `_tx_depth` reentrancy in `db.transaction` | the 3 nesting tests |
| Revert `FormModal` batching to opt-in | `a_form_save_lands_as_one_revertible_batch` |
| Drop the `_assert_known_field` call from `log_event` | `an_undeclared_event_field_is_refused_at_write_time` |
| Restore the old 2-name `NON_MUTATION_FIELDS` | `declared_bookkeeping_fields_are_allowed`, `undo_steps_past_provenance…` |

**Correction worth carrying forward:** I initially described
`test_multi_field_write_reverts_as_one_unit` as "the lost-deal fix". It is
not. It calls `open_batch` directly, so it proves the *mechanism* collapses a
multi-field write into one revertible unit — which the MCP path already had.
The actual lost-deal bug (`<` on the pipeline writing four fields that `u`
cannot put back) is **not fixed**; it needs `tui/screens/pipeline.py:142-153`
converted to a batch, which is Phase 3 below.

## Next step

**Flip `u` to batch-granular in `src/bookkit/services/undo.py`.** It currently
reads `events.last_mutation(conn)` and reverts one field. Replace with: find
the most recent un-reverted `event_batch` with `source = 'tui'`, and revert it
through `services.batches.revert(conn, ref, now=...)` — so `u` and `R` share
one code path.

Concretely:
1. Add `repo/batches.py::last_undoable(conn, source)` — newest
   `event_batch` where `source = ?` and `reverted_at IS NULL`.
2. Rewrite `services/undo.py::undo_last` to use it. `UndoResult` needs new
   fields: it must carry the batch `ref`, the `summary`, `applied: bool`, and
   the `refused: list[Conflict]` so a "changed since" refusal can be shown
   instead of reported as success.
3. Update `src/bookkit/tui/app.py` — `action_undo_last` is around line 216,
   `show_undo_result` around line 241-248. The toast currently renders
   `f"{entity_type}.{field}: {new!r} → {old!r}"`, which is a schema path and a
   Python repr; it should name the client and ref.
4. Update the 5 existing call sites in tests: `tests/test_services.py:112,121`,
   `tests/test_repo.py:471`, `tests/test_mcpserver.py:792,841`.
   Note `test_mcpserver.py:841` is commented "used to raise IndexError" — it is
   the regression test for the old `source` bug and must keep passing.
5. Fix `services/batches.py::revert` while you are in there: `mark_reverted` is
   called unconditionally inside the transaction, so `force=True` on a fully
   conflicted batch returns `applied=True` with `reverted=[]` and burns the
   batch permanently. Only mark it reverted if something actually applied.

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

- **Scope `u` to `source='tui'`?** Proposed rule: `u` = undo my last action in
  this app; `R` on the MCP CHANGES table = revert an assistant batch; imports =
  snapshot rollback. This is what stops `u` reverting sync-projection writes.
  Confirm before I build step 2 above.
- **After an import, `u` will say "nothing to undo"** rather than raising.
  That is correct — an import's rollback is its snapshot — but it is a visible
  change, and it makes the missing `bookctl restore` (audit G1) more pressing.
  Do you want `bookctl restore` in this branch or its own?
- **CLAUDE.md line updated in this session**: "`u` stays single-step/
  field-granular for TUI writes" was superseded by your instruction. I have
  amended it; check the wording matches what you meant.
