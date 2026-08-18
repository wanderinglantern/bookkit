<!-- DRAFT — NOT APPROVED. Read the verification report at the bottom before building from this. -->

> **Status: DRAFT — NEEDS REVISION** (2026-08-18).
> Produced by a drafting pass, then checked by an independent adversarial pass that
> opened every citation and challenged the load-bearing claims.
> **62 citations checked · 4 failed · 10 claims challenged.**
> Kind: `task-brief`.
> The verification report at the bottom is PART OF THIS DOCUMENT — some of its findings
> would break an implementation built from the body above it.

---

# Item F — one vocabulary for deleting an interaction

Date: 2026-08-18
Status: ready to build. No schema change, no migration, no data touched —
`bookctl backup` is not required for this one (the write it changes is already
a soft delete, `repo/interactions.py:117-118`).

## Goal

`mcpserver._activity_delete` is the last caller that deletes an interaction on
its own terms. Route it through `services.interactions.delete` so the changes
rail, `R`, and the web rail describe one write one way, whoever asked.

## What is true today (checked, not quoted)

| Claim | Verified |
|---|---|
| `_activity_delete` opens its own batch, `tool="activity_delete"` | `src/bookkit/mcpserver.py:867-870` |
| its summary is `f"deleted activity: {subject}"` | `src/bookkit/mcpserver.py:869` |
| the service writes `tool="interaction_delete"` and `deleted <subject> from <org>` | `src/bookkit/services/interactions.py:38,124` |
| MCP raises `KeyError` from `interactions.get` | `src/bookkit/mcpserver.py:866` |
| MCP calls `repo.interactions.delete` directly — the only non-service caller | `src/bookkit/mcpserver.py:871` |
| `_provenance` is stamped INSIDE the batch | `src/bookkit/mcpserver.py:872` |
| the TUI calls the service | `src/bookkit/tui/screens/account.py:1376` (`source="tui"`) |
| the web calls the service | `src/bookkit/web/routes/relationship.py:359` (`source="web"`) |
| `contacts_svc.remove` IS wired to MCP — this is the odd one out | `src/bookkit/mcpserver.py:1053`, and `_contact_remove` opens no batch and stamps no provenance (`:1035-1057`) |
| `db.transaction` nests by JOINING; an inner `batch=` is ignored | `src/bookkit/db.py:189-207` — at depth > 0 it never rebinds `_current_batch` |
| the TUI's `R` table prints `batch.tool` beside the summary | `src/bookkit/tui/screens/navigator.py:570` |
| the web rail prints `batch.summary` | `src/bookkit/web/routes/account.py:355-365` |

Two things the entry does not say, both load-bearing:

**The empty batch would be the SERVICE's, not the wrapper's.** If MCP kept an
outer `_open_batch`, the inner `open_batch` still INSERTs its own `event_batch`
row (`services/batches.py:141-145` → `repo/batches.py:29-35`) while every event
is stamped with the OUTER `BatchState` (`repo/base.py:110-126` reads
`db.current_batch()`, which the joining call leaves alone). So the
MCP-named batch keeps the events and the shared `interaction_delete` row is the
dead one — the divergence would survive the "fix".

**An empty batch is not cosmetic; its Revert button refuses in silence.**
`batches_repo.recent` does not filter empty batches (`repo/batches.py:57-67`),
so it renders in the account rail (`web/routes/account.py:393-396`) and in the
TUI's `R` table. Clicking Revert on it returns `applied=False` with zero
conflicts (`services/batches.py:355-362`), which the route reports as
`outcome=refused` (`web/routes/changes.py:149`) and the toast renders as
`"MCP-42 refused — "` with nothing after the dash (`web/routes/changes.py:165-176`).
That is CLAUDE.md's "A REFUSAL SAYS SOMETHING" broken by construction.

**A fourth divergence the entry did not name.** MCP tells the model `u` restores
this delete — in the tool docstring (`mcpserver.py:257`), in the implementation
docstring (`:857`) and in the returned dict (`:874`). It cannot: `undo.undo_last`
is scoped to `source="tui"` (`services/undo.py:32,49`), which
`tests/test_mcpserver.py:797-800` already asserts. `_contact_remove` says the
true thing — `"undo": "revert_batch puts them back"` (`mcpserver.py:1056`).

## Decisions

**1. MCP keeps a thin `_activity_delete` that opens NO batch and calls the
service.** The service already owns the batch (`services/interactions.py:17-21`),
so the MCP side has no third option: `db.transaction` joins, and an outer batch
produces the empty row above.

*Rejected — the service grows an optional provenance hook.* A callback whose
only caller is MCP puts a surface's concern inside a shared rule and makes the
service's behaviour conditional on who called it. `services/contacts.py:25-27`
already ruled this exact question the other way, on 2026-08-18, for the same
write shape.
*Rejected — MCP wraps the service in `_open_batch`.* Produces the empty row and
the blank refusal toast above.

**Cost if wrong:** near zero to reverse — `_activity_delete` is ten lines and
has no other caller (`mcpserver.py:258` is the only one).

**2. The per-entity `_provenance` stamp is DROPPED for this write, not moved.**
This matches `_contact_remove`, which stamps none. Nothing is lost that cannot
be reconstructed: the batch row carries `source` (`repo/batches.py:31`), every
event carries `batch_id` (`repo/base.py:113-127`), and no surface reads
per-entity `source` events at all — `events.history` has no caller outside
`repo/`, and the only `field_history` consumer is `services/pipeline.py:110` on
`stage`. The revert planner skips `source` (`services/batches.py:24`) and
`external_change_count` excludes it (`repo/batches.py:89`), so behaviour there
is unchanged either way.

**Cost if wrong:** if per-entity provenance is later wanted, it is a join from
`event_log.batch_id` to `event_batch.source` — recoverable, not lost.

**3. `delete()` returns a frozen `Deletion`, not a bare `str`.** The MCP tool
reports `batch.ref` to the model and the service must supply it; `contacts.Removal`
(`services/contacts.py:62-91`, built with `batch=batch.ref` at `:206-209`) is the
shape. Fields: `interaction_id`, `subject`, `org_id`, `org_name`, `batch`, plus a
`summary` property delegating to the existing `_summary` (`interactions.py:34-38`)
so the one home for that sentence stays one home.

*Rejected — MCP re-reads `batches_repo.most_recent` after the call.* It orders
by rowid across every source (`repo/batches.py:96-104`), so a concurrent TUI or
web write returns the wrong batch; and it re-creates the "caller reconstructs
what the service already knew" shape this item exists to delete.
*Rejected — return `tuple[str, str]`.* Positional, unnamed, and the MCP dict
also wants `subject` and the org name, which it currently gets from its own
`interactions.get`.

**Cost if wrong:** one TUI call site (`tui/screens/account.py:1376`, which binds
the return to `summary`) and two test edits. The web ignores the return
(`web/routes/relationship.py:359`) and needs no change.

**4. Nothing here touches `events.NON_MUTATION_FIELDS`.** Checked, because
CLAUDE.md says an undeclared name is a landmine that fires days later on `u`.
The only field this write logs is `deleted_at` — a real column
(`repo/base.py:181-187`). `source` is already declared (`repo/events.py:82-88`),
and this change REMOVES a `source` write rather than adding a field. No edit to
`repo/events.py`.

**5. The docstrings are fixed in the same commit, in opposite directions.** See
the corrections section: the module header understates (two surfaces), the
`delete()` docstring overstates (`'mcp' | 'tui' | 'web'`, where `mcp` never
passes). Both become true together.

**6. The false `u` promise goes with it.** `"undo": "revert_batch puts it back"`,
matching `_contact_remove`, and the same correction in the tool docstring
(`mcpserver.py:257`) and the implementation docstring (`:857`). It is the same
defect class as the item itself — the surface describing a write in words the
write does not honour — and this is the commit that touches all three lines.

**Cost if wrong:** none; the current text is provably false.

**7. The MCP tool NAME stays `activity_delete`.** Only the batch's `tool=` string
changes. `activity_delete` is the companion of `log_activity` and
`recent_activity`, and `recent_activity`'s docstring points at it by name
(`mcpserver.py:244`); renaming means three docstrings, the registry test
(`tests/test_mcpserver.py:826-829`) and a model-facing API break, for a
vocabulary the changes rail never shows. See open decisions — this is the one
call worth a second opinion.

## Changes

**`src/bookkit/services/interactions.py`**
- Module docstring: three surfaces, named — the TUI's `ConfirmDeleteInteraction`,
  the web's `_interaction_confirm_delete.html`, and MCP's `_activity_delete`.
  Say that MCP passes `source="mcp"` and stamps no per-entity provenance, with
  the `services/contacts.py:25-27` reason, so the next reader does not "restore"
  it.
- Add `@dataclass(frozen=True) class Deletion` (fields per Decision 3, `summary`
  property → `_summary`).
- `delete()` returns `Deletion(...)` built after the `with` block from
  `batch.ref`; docstring's `source` line is now true as written.

**`src/bookkit/mcpserver.py`**
- `_activity_delete` (`:855-875`) becomes: import the service, call
  `interactions_svc.delete(conn, interaction_ref, source="mcp")`, return
  `{"interaction_ref": ..., "deleted": True, "subject": result.subject,
  "detail": result.summary, "undo": "revert_batch puts it back",
  "batch": result.batch}`. No `_open_batch`, no `_provenance`, no
  `repo.interactions` import.
- Docstring: drop the "the get() first is not redundant" paragraph — that
  reasoning now lives in the service, which does it better with `raw_row`. Say
  instead that the rules belong to `services.interactions.delete`, the way
  `_contact_remove`'s docstring does.
- `:257` tool docstring: `u` → `revert_batch`.

**`src/bookkit/tui/screens/account.py:1371-1382`**
- `result = interactions_svc.delete(...)`; notify `f"{result.summary} — u to undo"`.
  The `except ValueError` branch and the no-`_batched()` comment stay exactly as
  they are.

**No change needed** (stated so nobody "tidies" them): `web/routes/relationship.py`
discards the return value; `web/parity.py:120-139` describes the TUI↔web pair
only and remains true.

## Tests

New file `tests/test_interaction_delete.py`, following the
`tests/test_contact_remove.py` precedent ("the whole slice in one file, because
the thing worth asserting is that the surfaces behave identically"). Its own
small fixture — one client, one interaction — built from seeded/hand-made data,
never Grant's book.

MOVE into it from `tests/test_mcpserver.py`: `test_activity_delete_removes_it_and_stays_undoable`
(`:777`), `test_activity_delete_refuses_an_unknown_ref` (`:803`),
`test_activity_delete_refuses_to_delete_twice` (`:814`) — the last two change
from `pytest.raises(KeyError)` to the service's sentences. LEAVE
`test_activity_delete_is_registered_as_a_write_tool` (`:826`) where it is; it
covers `recent_activity` too.

Each test below names the production edit that must turn it red. Run the
mutation, watch the named test fail, revert — a green suite proves nothing was
broken, not that the new path is taken (CLAUDE.md).

1. **`test_mcp_delete_writes_the_batch_the_other_surfaces_write`** — after
   `_activity_delete`, the newest batch has `tool == "interaction_delete"`,
   `source == "mcp"`, and `summary == f"deleted {subject} from {org.name}"`.
   *Mutation:* put `tool="activity_delete"` back → fails on tool. Put the old
   `f"deleted activity: {subject}"` summary back → fails on summary.
2. **`test_the_mcp_delete_is_exactly_one_batch`** — batch count rises by exactly
   1, and `batches_repo.events_for(conn, batch.id)` is non-empty.
   *Mutation:* wrap the service call in `_open_batch(...)` again → count rises by
   2 and the `interaction_delete` row holds no events → both assertions fail.
3. **`test_an_unknown_ref_is_refused_with_the_services_sentence`** —
   `pytest.raises(ValueError)`, message contains the ref and "read the account's
   timeline".
   *Mutation:* restore `interactions.get(...)` as the first line → `KeyError` →
   fails.
4. **`test_a_second_delete_says_already_deleted_and_writes_no_batch`** — second
   call raises `ValueError` containing "already deleted" and the org name, and
   the batch count is unchanged by the refusal.
   *Mutation:* restore `interactions.get` → wrong exception type. Separately,
   move the `already_deleted` check inside `open_batch` in the service → an empty
   batch appears → the count assertion fails.
5. **`test_the_tool_reports_the_batch_ref_it_wrote`** — `out["batch"]` starts
   `MCP-` and `batches_repo.get_by_ref(conn, out["batch"]).tool ==
   "interaction_delete"`.
   *Mutation:* drop `batch` from `Deletion` or from the MCP dict → `KeyError` in
   the test.
6. **`test_the_tool_does_not_promise_u`** — after an MCP delete,
   `undo.undo_last(conn) is None` AND `"revert_batch" in out["undo"]`, tying the
   sentence to the behaviour rather than to itself.
   *Mutation:* restore `"u in the TUI restores it"` → fails.
7. **`test_only_the_service_deletes_an_interaction`** — source scan in the
   `tests/test_conventions.py` style: no `interactions.delete(` /
   `interactions_repo.delete(` anywhere under `src/bookkit` except
   `services/interactions.py` and `repo/`.
   *Mutation:* restore `interactions.delete(conn, interaction.id)` in
   `mcpserver.py` → fails. This is the seam assertion; without it the batching
   could be bypassed again and stay green.

Also update `tests/test_web_writes.py:941`'s comment, which says
`services.interactions._summary` "is shared with the TUI" — after this it is
shared with all three. The assertion itself is unchanged and still passes.

## Gates

`uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests` — in a
worktree, `uv sync --group dev` first and `uv run --no-sync python -m pytest`.
Redirect output to the scratchpad, gate on the command, tail the file after;
never pipe before the `&&`.

## Out of scope

- Generalising the provenance question — `_member_deactivate` and the other 20+
  MCP writes keep their surface-side batch and their `_provenance` stamp. That
  the stamp may be redundant everywhere now that every MCP write is batched is a
  separate observation, not this change.
- `repo/batches.py:89` hard-codes `field NOT IN ('created', 'source')` instead of
  deriving from `NON_MUTATION_FIELDS`, so `import` / `carrier_alias` /
  `merged_from` count as external changes there. Real, unrelated, unaffected by
  this change.



---

## Verification report (independent adversarial pass, 2026-08-18)

**Verdict: needs-revision.** The document is broadly safe to build from — 58 of 62 citations resolve to the exact line and say what is claimed, and all three of its corrections to the ROADMAP entry are verified correct, including the strong one: under a wrapper shape the empty row really would be the SERVICE's `interaction_delete` batch while the MCP-named batch keeps the events (db.py:201-207 never rebinds `_current_batch` at depth > 0; services/batches.py:141-145 INSERTs regardless), and the empty-batch → `applied=False, refused=[]` → `"MCP-42 refused — "` blank-toast chain checks out end to end. Its weakest point is the Tests section, which promises "each test names the production edit that must turn it red" and then names one that provably cannot: moving the `already_deleted` check inside `open_batch` rolls the batch INSERT back with the ValueError, so test 4's count assertion still passes. Two more test assertions are decoration presented as proof — `out["batch"].startswith("MCP-")` is true of every batch in the database (repo/batches.py:12), and test 6's `undo_last is None` half duplicates an existing assertion no mutation here can touch — and the doc does not rule on the one substantive regression it creates: the model now gets a refusal telling it to "read the account's timeline", which is not a thing MCP has.


### Citations that did not check out

- **`src/bookkit/mcpserver.py:1056`** — claimed: _contact_remove's undo sentence correctly names revert_batch, not `u` (cited in the draft body as "(mcpserver.py:1056)" and in citation 14)
  
  *Actually:* Line 1056 is `"interactions": removed.interactions, "detail": removed.message,`. The undo sentence `"undo": "revert_batch puts them back", "batch": removed.batch}` is line 1057. Off by one; the claim itself is true of :1057.

- **`src/bookkit/web/routes/changes.py:149`** — claimed: a non-applied revert redirects with outcome=refused and len(result.refused)
  
  *Actually:* Line 148 is `if result.applied:` and line 149 is the SUCCESS branch — `return _redirect(ref, tab, batch_ref, "reverted", len(result.reverted))`. The refused redirect is line 150. The citation resolves to the opposite branch of the very if-statement it describes. The claim is true of :150.

- **`src/bookkit/repo/events.py:11-31`** — claimed: history/field_history are the only per-entity event_log reads
  
  *Actually:* events.py:11-31 does define history and field_history, but they are not the only per-entity reads: `repo/batches.py:87` (`external_change_count`) issues `SELECT COUNT(*) FROM event_log WHERE entity_type = ? AND entity_id = ?`, filtered per entity. The draft's Decision 2 happens to handle external_change_count separately, so the decision survives — but the citation as stated is false, and 'the only X' is exactly the shape this build has been burned by.

- **`tests/test_mcpserver.py:797-800`** — claimed: already asserts that `u` cannot reach an MCP batch (draft body: "which tests/test_mcpserver.py:797-800 already asserts")
  
  *Actually:* The assertion that `u` cannot reach it is line 796: `assert undo.undo_last(rw) is None`. Lines 797-800 assert the opposite side — that `batches_repo.last_undoable(rw, source="mcp")` exists and `batches_svc.revert` restores the row. The cited range starts one line after the load-bearing assertion. (Citation 55's wider range 777-800 does contain it.)


### Claims challenged (even where the citation resolved)

- **[CRITICAL]** Test 4's second named mutation: "move the `already_deleted` check inside `open_batch` in the service → an empty batch appears → the count assertion fails."
  
  *Evidence:* This mutation cannot turn the test red. `services/batches.py:91` decorates `open_batch` with `@contextmanager`, and at `:141-145` the `yield batches_repo.create(...)` sits INSIDE `with db.transaction(conn, batch=...)`. A ValueError raised in the with-body propagates back through the generator into `db.transaction`, which at `db.py:217-219` does `except BaseException: conn.execute("ROLLBACK"); raise`. The INSERT of the event_batch row is rolled back with it, so the batch count is unchanged and `assert batch count unchanged by the refusal` still PASSES. The document's own framing — "Each test below names the production edit that must turn it red. Run the mutation, watch the named test fail" — makes this a false promise, and it is the second time in the same doc that an empty-batch claim is asserted without checking the transaction boundary. Test 4's FIRST mutation (restore `interactions.get` → KeyError not ValueError) is sound.

- **[IMPORTANT]** Test 5 (`test_the_tool_reports_the_batch_ref_it_wrote`): "`out["batch"]` starts `MCP-`", presented as evidence the tool reports the batch IT wrote.
  
  *Evidence:* `repo/batches.py:12` sets `BATCH_REF = "MCP"` as a module constant, and `create()` at `:33` calls `next_ref(conn, BATCH_REF)` with no reference to `source`. `ids.next_ref` at `ids.py:44` returns `f"{kind}-{n:04d}"`. EVERY event_batch ref begins `MCP-`, including every TUI and web batch. The assertion is true of a web-sourced delete and cannot distinguish MCP from anything else — it is decoration in the position the doc uses it. (The existing precedent it copies, `tests/test_contact_remove.py:183`, has the same weakness.) The second half — `get_by_ref(conn, out["batch"]).tool == "interaction_delete"` — is real, and the named mutation (drop `batch` from `Deletion` or the dict → KeyError) does work.

- **[IMPORTANT]** Test 1's mutation: "put `tool="activity_delete"` back → fails on tool. Put the old `f"deleted activity: {subject}"` summary back → fails on summary."
  
  *Evidence:* After Decision 1 there is no MCP-side tool or summary string left to put back — both are owned by `services/interactions.py:124` and `:38`, shared with the TUI and web. Mutating them there also reds `tests/test_web_writes.py:945` and any TUI equivalent, so the named mutation does not isolate the seam the test claims to protect. The test itself IS load-bearing (asserting `tool == "interaction_delete"` AND `source == "mcp"` after an MCP call does prove routing), but the correct mutation is "restore the old `_activity_delete` body" — the doc names the wrong edit.

- **[IMPORTANT]** The service's refusal sentences are the right thing to hand the model (test 3 pins "read the account's timeline" as the MCP-facing refusal).
  
  *Evidence:* `services/interactions.py:115` raises `f"no interaction {id!r} — read the account's timeline for exact ids"`. "The account's timeline" is a UI affordance; the MCP model has no timeline, it has `recent_activity`, which `mcpserver.py:243-248` documents as the ONLY way to find an interaction_ref ("`search` returns no refs, so it cannot name one for you"). Routing MCP through the service replaces a KeyError with a sentence naming a remedy the caller cannot follow. Under CLAUDE.md's "A REFUSAL SAYS SOMETHING" that is a regression in kind, not just a change in type, and the doc neither names it nor rules on it. It is also a live question for the spec: does the sentence get a surface-neutral rewrite, or does MCP wrap it?

- **[MINOR]** Decision 6: the false `u` promise lives in three places and "this is the commit that touches all three lines" (`mcpserver.py:257`, `:857`, `:874`).
  
  *Evidence:* There is a fourth, and the doc moves it without noticing: `tests/test_mcpserver.py:778-779` — the docstring of `test_activity_delete_removes_it_and_stays_undoable`, which the doc explicitly MOVES into the new file — reads "The correction path for an MCP mistake: soft delete, event-logged, so `u` in the TUI puts it back." That is contradicted by the test's own body at line 796 (`assert undo.undo_last(rw) is None`). Moving a test carries its docstring; the false sentence survives the commit that claims to delete the whole class.

- **[MINOR]** Citation 28 / the empty-batch argument: "open_batch ALWAYS INSERTs its own event_batch row via batches_repo.create, even when its transaction joins an outer one."
  
  *Evidence:* True for the path the interaction delete takes, false as stated. `services/batches.py:128-138` is a join path: when `entity_id` is passed and `_joinable(...)` holds, open_batch yields the existing candidate batch and never calls `create`. `services/interactions.py:123-125` passes no `entity_id`, so the doc's conclusion holds today — but the load-bearing sentence in the doc is a universal that the code does not support, and the guard against it is a keyword argument nobody has declared anywhere.

- **[MINOR]** Decision 5: `delete()`'s docstring "overstates" and the current text is "provably false" (`'mcp' | 'tui' | 'web'`).
  
  *Evidence:* `services/interactions.py:110` reads "`source` is the surface: 'mcp' | 'tui' | 'web'." `source` is an unvalidated free `str` parameter (`:101`) with no enum behind it, so the line documents the accepted vocabulary, not a claim about who calls it today. Calling it "provably false" is the doc reaching for symmetry with the module header. Harmless — the edit makes it true either way — but it is the kind of rhetorical tightening this ledger asks people not to do.

- **[MINOR]** Decision 5's "both become true together" — the two surface-count docstrings are the only stale ones this commit meets.
  
  *Evidence:* `services/batches.py:105-106` — the docstring of `open_batch`, the function BOTH edited surfaces call — still says "`source` says which surface wrote it ('mcp' or 'tui') — the only reason both callers are not identical." Web has been a third source since the web writes shipped (`web/routes/relationship.py:359` passes source="web", asserted at `tests/test_web_writes.py:940`). A third stale surface-count docstring, in the function at the centre of this change, unnamed by the doc.

- **[MINOR]** Decision 1: "the MCP side has no third option".
  
  *Evidence:* There is a third, and the house format ("name what was REJECTED and why") wants it named: `delete()` could take an optional caller-supplied batch and skip opening one, letting MCP keep `_open_batch` and its provenance stamp with no empty row. `services/contacts.py:18-27` rules against that shape for the same write class, so the recommendation stands — but the doc asserts non-existence rather than rejecting the option, and asserting non-existence is what got caught nine times here.

- **[MINOR]** Test 6's `undo.undo_last(conn) is None` ties the sentence to the behaviour.
  
  *Evidence:* That half of the assertion is insensitive to every mutation in this change: `services/undo.py:32` (`SOURCE = "tui"`) and `:49` (`last_undoable(conn, source=SOURCE)`) are untouched by the spec, and the identical assertion already exists at `tests/test_mcpserver.py:796` in a test the doc is moving into the same file. Only the `"revert_batch" in out["undo"]` half is load-bearing, and the doc's own named mutation confirms that. Duplicated, not wrong.


### Decisions the draft left open

- **Does the MCP tool keep the name `activity_delete`, or become `interaction_delete` so the model-facing vocabulary matches the batch's?**
  - Recommendation: Keep `activity_delete`. It is the companion of `log_activity` and `recent_activity`, and `recent_activity`'s docstring points at it by name (mcpserver.py:244). The item is about the changes rail, which never shows a tool NAME to the model. Renaming costs three docstrings, the registry assertion (tests/test_mcpserver.py:826-829), and a break in an API a model has been taught.
  - Cost if wrong: If kept and it should have been renamed: the model still sees one write under two names (tool `activity_delete` → batch `interaction_delete`), a smaller version of the same complaint, fixable later in one commit. If renamed and it should have been kept: an existing MCP client calling `activity_delete` gets 'no such tool' with no deprecation path.

- **Do the TUI and web halves of this slice also move into tests/test_interaction_delete.py, matching test_contact_remove.py's 'whole slice in one file'?**
  - Recommendation: No — move only the three MCP tests. The TUI test rides `seeded_db` and a Textual pilot (tests/test_tui.py:1777) and the web test rides a `timeline` fixture (tests/test_web_writes.py:913); porting both is churn that adds no coverage. test_contact_remove.py could do it because those tests were written new with the feature.
  - Cost if wrong: The cross-surface contract stays asserted in three files instead of one, so a future reader has to be pointed at the other two — mitigated by naming them in the new file's docstring.


### Corrections this draft makes to the ROADMAP entry

- ROADMAP said: The `services/interactions.py` docstring currently claims three surfaces read these rules.
  - Code says: The MODULE docstring claims TWO — 'the rules both surfaces ask', and it names only the TUI's ConfirmDeleteInteraction and the web's _interaction_confirm_delete.html. That is accurate today. The false three-surface claim is one function down, in delete()'s own docstring: "`source` is the surface: 'mcp' | 'tui' | 'web'", where nothing passes 'mcp'. The two edits therefore go in OPPOSITE directions: the header must be widened to three, the delete() line is already right and becomes true. (`src/bookkit/services/interactions.py:1, :11-15, :110`)

- ROADMAP said: it cannot open one, because `db.transaction` nests by JOINING and an outer batch would leave a second, permanently empty row in the changes list
  - Code says: True in count, wrong about which row. At depth > 0, transaction() never rebinds `_current_batch` (db.py:201-207), so every event is stamped with the OUTER BatchState while the inner open_batch still INSERTs its own event_batch row (services/batches.py:141-145). The empty row would be the SERVICE's `interaction_delete` batch; the MCP-named `activity_delete` batch would keep the events and stay the revertible one — i.e. the wrapper shape would leave the divergence exactly as it is and add a dead row. Worse than the entry implies, and it strengthens the ruling. (`src/bookkit/db.py:201-207, src/bookkit/services/batches.py:141-145`)

- ROADMAP said: the divergence is the batch tool, the summary, and the KeyError
  - Code says: There is a fourth, and it is a false statement made to the model rather than a cosmetic one: `_activity_delete` returns "undo": "u in the TUI restores it" (mcpserver.py:874), and the tool and implementation docstrings say the same (:257, :857). `u` cannot reach an MCP batch — undo.undo_last is scoped to source='tui' (services/undo.py:32,49), which tests/test_mcpserver.py:797-800 already asserts. `_contact_remove` says the true thing, 'revert_batch puts them back' (mcpserver.py:1056). (`src/bookkit/mcpserver.py:257, :857, :874; src/bookkit/services/undo.py:32,49`)
