<!-- ROUND 2 DRAFT. Research verified; DESIGN NOT APPROVED. Read the bottom before building. -->

> **Status: research verified — design NOT approved** (2026-08-18, round 2 of 2).
>
> Drafted against the code, rejected by an adversarial pass, revised, and rejected again.
> **34 citations re-checked · 7 still failed ·
> 7 claims challenged · 6 regressions ·
> 11 decisions reversed from round 1.**
>
> **Iteration was stopped here deliberately, and that is a ruling, not an omission.** Round 2 fixed
> most of what round 1 got wrong and then committed the same defect class again — in one case, in
> the fixes themselves. These documents specify code that does not exist yet, and every added
> specificity is a fresh opportunity for a confident false claim. The build's own record is that
> the runtime reasoning holds and the speculative citations do not.
>
> **So use this for its RESEARCH, not its conclusions.** The verified findings about how the code
> actually behaves are the valuable part and were reproduced by two independent passes. Re-decide
> the design at build time, against the real code, and treat every design decision below as a
> starting proposal carrying a named cost — not a settled call.
>
> Kind: `task-brief`.

---


# Item F — one vocabulary for deleting an interaction

Date: 2026-08-18 (revised after adversarial verification)
Status: ready to build. No schema change, no migration, no data touched — the
write it changes is already a soft delete (`repo/interactions.py:117-118` →
`repo/base.py:181-187`), so `bookctl backup` is not required for this one.

## Goal

`mcpserver._activity_delete` is the last caller that deletes an interaction on
its own terms. Route it through `services.interactions.delete` so the changes
rail, `R`, `list_batches` and the web rail describe one write one way, whoever
asked.

## What is true today (re-opened, line by line)

| Claim | Where |
|---|---|
| `_activity_delete` opens its own batch, `tool="activity_delete"` | `src/bookkit/mcpserver.py:867-870` |
| its summary is `f"deleted activity: {subject}"` | `src/bookkit/mcpserver.py:869` |
| the service writes `tool="interaction_delete"` and `deleted <subject> from <org>` | `src/bookkit/services/interactions.py:124`, `:34-38` |
| MCP raises `KeyError` from `interactions.get` | `src/bookkit/mcpserver.py:866` |
| MCP calls `repo.interactions.delete` directly — the only non-service call site | `src/bookkit/mcpserver.py:871` (the other three text hits — `tui/screens/account.py:1367`, `web/parity.py:130`, `web/routes/relationship.py:346` — are prose, no call parens) |
| `_provenance` is stamped INSIDE the batch | `src/bookkit/mcpserver.py:872` → `:796-799` |
| the TUI calls the service, `source="tui"` | `src/bookkit/tui/screens/account.py:1376` |
| the web calls the service, `source="web"` | `src/bookkit/web/routes/relationship.py:359` |
| `contacts_svc.remove` IS wired to MCP — this is the odd one out | `src/bookkit/mcpserver.py:1053`; `_contact_remove` (`:1035-1057`) opens no batch and stamps no provenance |
| `db.transaction` nests by JOINING; an inner `batch=` is ignored | `src/bookkit/db.py:189-207` — at depth > 0 (`:200-207`) it never rebinds `_current_batch` |
| the TUI's `R` table prints `batch.tool` beside the summary | `src/bookkit/tui/screens/navigator.py:570-571` |
| the web rail prints `batch.summary` and `batch.source` | `src/bookkit/web/routes/account.py:497-507` (`"what": batch.summary` at `:504`), rendered by `web/templates/account/page.html:134-161` |
| the MCP `list_batches` tool reports `tool` and `summary` **to the model** | `src/bookkit/mcpserver.py:2062-2066` (`"tool": batch.tool` at `:2063`) |

### Three things the ROADMAP entry does not say, all load-bearing

**1. The empty batch would be the SERVICE's, not the wrapper's.** If MCP kept an
outer `_open_batch`, the inner `open_batch` still INSERTs its own `event_batch`
row (`services/batches.py:140-145` → `repo/batches.py:30-35`) while every event
is stamped with the OUTER `BatchState` (`repo/base.py:110-127` reads
`db.current_batch()`, which the joining call at `db.py:200-207` leaves alone).
Both INSERTs commit together at the outermost `COMMIT` (`db.py:220`). So the
MCP-named `activity_delete` batch keeps the events and stays the revertible one,
and the shared `interaction_delete` row is the dead one — the wrapper shape would
leave the divergence exactly as it is *and* add a dead row.

**2. An empty batch is not cosmetic; its Revert button refuses in silence.**
`batches_repo.recent` does not filter empty batches (`repo/batches.py:57-67`), so
it renders in the web account rail (`web/routes/account.py:545-549`, top 8) and in
the TUI's `R` table (`tui/screens/navigator.py:350-352, :556-573`). Reverting it
returns `applied=False` with `refused=[]` (`services/batches.py:357-362`), which
the route reports as `outcome=refused` (`web/routes/changes.py:150`) and the toast
renders as `"MCP-42 refused — "` with nothing after the dash
(`web/routes/changes.py:153-176`, the f-string at `:176` with an empty `named`).
That is CLAUDE.md's "A REFUSAL SAYS SOMETHING" broken by construction.

**3. A fourth divergence the entry did not name: MCP tells the model something
false.** It says `u` restores this delete — in the tool docstring
(`mcpserver.py:257`), in the implementation docstring (`:857`) and in the returned
dict (`:874`). It cannot: `undo.undo_last` is scoped to `source="tui"`
(`services/undo.py:32, :49`), which `tests/test_mcpserver.py:796` already asserts.
`_contact_remove` says the true thing — `"undo": "revert_batch puts them back"`
(`mcpserver.py:1057`). There is a **fifth** copy of the false sentence, in a test
docstring this brief moves: `tests/test_mcpserver.py:778-779`, contradicted by
that test's own body at `:796`.

## Decisions

### 1. MCP keeps a thin `_activity_delete` that opens NO batch and calls the service

The service already owns the batch (`services/interactions.py:17-21`,
`:123-126`), and `db.transaction` joins, so any MCP-side batch is either the
dead-row shape above or a second vocabulary again.

*Rejected — the service grows an optional provenance hook.* A callback whose only
caller is MCP puts a surface's concern inside a shared rule and makes the
service's behaviour conditional on who called it. `services/contacts.py:18-27`
ruled this exact question the other way, for the same write shape, on 2026-08-18.

*Rejected — MCP wraps the service in `_open_batch`.* Produces the dead
`interaction_delete` row and the blank refusal toast above.

*Rejected — `delete()` grows a `batch:` parameter and skips opening one, so MCP
can keep `_open_batch` + `_provenance` with no dead row.* This is a real third
option (the draft wrongly asserted none existed), and it fails on its own terms:
MCP's outer batch carries `tool="activity_delete"`, so the divergence survives —
unless MCP is handed the service's tool string and summary to pass in, which is
exactly the "caller reconstructs what the service already knows" shape this item
exists to delete. It also puts a second, caller-controlled batching path into a
service that today has one.

**Cost if wrong:** near zero to reverse. `_activity_delete` is ten lines with one
caller (`mcpserver.py:258`). If per-surface provenance turns out to matter, the
hook can be added later without unpicking anything, because the batch row already
carries `source` (`repo/batches.py:31`).

### 2. The per-entity `_provenance` stamp is DROPPED for this write, not moved

This matches `_contact_remove`, which stamps none. Nothing readable is lost:

- the batch row carries `source` (`repo/batches.py:31`), and it is what the web
  rail prints as *who* (`web/routes/account.py:505`);
- every event carries `batch_id` (`repo/base.py:113-127`);
- **no surface reads per-entity `source` events.** `events.history` has no caller
  outside `repo/`, and the only `field_history` consumer is
  `services/pipeline.py:110`, on `stage` (verified by grep across `src/bookkit`);
- the revert planner skips `source` (`services/batches.py:24` ←
  `repo/events.py:82-88`), and `repo/batches.py:79-93` (`external_change_count`,
  the *other* per-entity `event_log` read — the draft wrongly called
  `repo/events.py:11-31` the only one) excludes `field = 'source'` at `:89`.

Worth noting for the common case: when MCP logged the interaction itself, the
entity already carries a `source=mcp` event from `_log_activity`
(`tests/test_mcpserver.py:288-291` pins it), so the delete's second stamp is
redundant even as provenance.

**Cost if wrong:** if per-entity provenance is later wanted, it is a join from
`event_log.batch_id` to `event_batch.source`. Recoverable, not lost.

### 3. `delete()` returns a frozen `Deletion`, not a bare `str`

This answers the ROADMAP's "check the MCP tool's return shape — it reports
`batch.ref` back to the model, which the service currently does not return."

Shape mirrors `contacts.Removal` (`services/contacts.py:62-91`, built with
`batch=batch.ref` at `:206-209`). Fields: `interaction_id`, `subject`, `org_id`,
`org_name`, `batch`; plus a `summary` property that returns
`_summary(self.subject, self.org_name)` so the sentence keeps one home
(`services/interactions.py:34-38`).

One deliberate divergence from `contacts.remove`: that function builds a
provisional `Removal(batch="")` at `contacts.py:186-191` to get its `summary`
before the batch exists, because `Removal.message` composes several clauses.
`Deletion` needs no provisional — `delete()` already computes the summary at
`interactions.py:122` before opening the batch, and the property recomputes the
identical string afterwards.

*Rejected — MCP re-reads `batches_repo.most_recent` after the call.* It orders by
rowid across every source (`repo/batches.py:96-103`), so a concurrent TUI or web
write returns the wrong batch.
*Rejected — return `tuple[str, str]`.* Positional, unnamed, and the MCP dict also
wants `subject`.

**Cost if wrong:** one TUI call site (`tui/screens/account.py:1376`, which binds
the return to `summary`) and the moved tests. The web ignores the return
(`web/routes/relationship.py:359`) and needs no change. mypy catches every miss.

### 4. Nothing here touches `events.NON_MUTATION_FIELDS`

Checked, because CLAUDE.md says an undeclared name is a landmine that fires days
later on `u`. The only field this write logs is `deleted_at`, a real column
(`repo/base.py:186-187`). `source` is already declared (`repo/events.py:82-88`),
and this change REMOVES a `source` write rather than adding a field. No edit to
`repo/events.py`.

### 5. The "no interaction" refusal is reworded to name data, not a UI affordance

`services/interactions.py:114-116` raises
`f"no interaction {interaction_id!r} — read the account's timeline for exact ids"`.
Routing MCP through the service hands that sentence to a caller that has no
timeline. It is also, after this change, effectively the MCP-only branch: the web
404s an unknown id at `_owns_raw_row` (`web/routes/account.py:200-215`) before
`delete` is reached, and the TUI passes an id taken from the focused row
(`tui/screens/account.py:1365-1376`).

**Ruling:** reword in the service, once, so it is true for every caller — name the
*record*, not a screen. e.g. `f"no interaction {interaction_id!r} — it may already
be gone; ids come from the account's own interaction list"`. No test pins the
current string (grepped). MCP needs no wrapper: its tool docstring already tells
the model where refs come from — `activity_delete`'s at `mcpserver.py:254-256`
("an exact ref read from `recent_activity`") and `recent_activity`'s at `:243-248`
("`search` returns no refs, so it cannot name one for you").

*Rejected — MCP catches the ValueError and re-words it.* Two sentences for one
refusal, in the surface, is the defect this item is removing.
*Rejected — leave it.* It tells the model to do something it cannot do, which
CLAUDE.md's refusal rule treats as worse than a bare traceback.

**Cost if wrong:** if a model still flounders on the refusal, the fix is one line
in the `activity_delete` docstring, not a code path. If a TUI/web user ever sees
it, "the account's interaction list" is the same object the old sentence named.

### 6. The false `u` promise goes with it — in all FIVE places

`"undo": "revert_batch puts it back"` in the returned dict (`mcpserver.py:874`),
matching `_contact_remove` (`:1057`); the same correction in the tool docstring
(`:257`) and the implementation docstring (`:857`); and — the draft missed this —
the docstring of the test being moved (`tests/test_mcpserver.py:778-779`), which
is contradicted by its own body at `:796`. Fifth: nothing else; grepped.

**Cost if wrong:** none. The current text is false against
`services/undo.py:32, :49`.

### 7. Three docstrings are corrected in this commit, in different directions

- `services/interactions.py:1` and `:9-21` — the MODULE docstring says "the rules
  both surfaces ask" and names two (`ConfirmDeleteInteraction`,
  `_interaction_confirm_delete.html`). That is accurate today and becomes wrong
  the moment MCP calls in. Widen to three, name `mcpserver._activity_delete`, and
  say MCP passes `source="mcp"` and stamps no per-entity provenance, with the
  `services/contacts.py:25-27` reason, so the next reader does not "restore" it.
- `services/interactions.py:110` — "`source` is the surface: 'mcp' | 'tui' | 'web'"
  documents the accepted vocabulary; it becomes true of the callers as well. (The
  draft called it "provably false". Withdrawn — it was reaching for symmetry.)
- `services/batches.py:105-106` — `open_batch`'s own docstring still says
  "`source` says which surface wrote it ('mcp' or 'tui')". Web has been a third
  source since the web writes shipped (`web/routes/relationship.py:359`, asserted
  at `tests/test_web_writes.py:1018`). Same defect class, one line, in the
  function at the centre of this change. Fix it here.

**Cost if wrong:** zero — comments. The risk of *not* doing it is the one this
item is about: a docstring that claims a shared rule the code no longer matches.

### 8. `delete()`'s KeyError on a soft-deleted org is a known limit, not a new guard

`services/interactions.py:121` calls `orgs_repo.get`, which is alive-filtered
(`repo/orgs.py:19-23` → `repo/base.py:148-153`) and raises `KeyError` when the
account has been deleted or merged away. That is today's behaviour for TUI and web
too; routing MCP through the service makes it reachable from a model holding a
stale ref. Named, not fixed: a guard here would be a second refusal vocabulary
invented in the commit that removes one.

**Cost if wrong:** a model deleting an interaction on a merged account gets a
traceback instead of a sentence. One `try/except KeyError` in the service, later,
if it is ever seen.

### 9. The MCP tool NAME stays `activity_delete` — but not for the reason the draft gave

The draft justified this with "the changes rail never shows a tool NAME to the
model." **That is false**: `_list_batches` returns `"tool": batch.tool`
(`mcpserver.py:2063`), so a model that calls `activity_delete` and then
`list_batches` sees `tool: interaction_delete`. The mismatch is visible.

The recommendation survives on the remaining grounds — `activity_delete` is the
companion of `log_activity` and `recent_activity`, `recent_activity`'s docstring
points at it by name (`mcpserver.py:244`), and renaming is a model-facing API break
with no deprecation path — but this is now a genuine product call. See
**Open for Grant**.

## Changes

**`src/bookkit/services/interactions.py`**
- Module docstring per Decision 7.
- Add `@dataclass(frozen=True) class Deletion` (fields and `summary` property per
  Decision 3).
- Reword the "no interaction" refusal at `:114-116` per Decision 5.
- `delete()` returns `Deletion(...)`, built after the `with` block from
  `batch.ref` (the `with` at `:123-126` must bind `as batch`).

**`src/bookkit/mcpserver.py`**
- `_activity_delete` (`:855-875`) becomes: `from .services import interactions as
  interactions_svc`, call `interactions_svc.delete(conn, interaction_ref,
  source="mcp")`, return `{"interaction_ref": result.interaction_id, "deleted":
  True, "subject": result.subject, "detail": result.summary, "undo":
  "revert_batch puts it back", "batch": result.batch}`. No `_open_batch`, no
  `_provenance`, no `from .repo import interactions`.
- Docstring: drop the "the get() first is not redundant" paragraph (`:860-863`) —
  that reasoning now lives in the service and does it better with `raw_row`. Say
  instead that the rules belong to `services.interactions.delete`, the way
  `_contact_remove`'s docstring does (`:1038-1040`).
- `:257` tool docstring: `u` → `revert_batch`.

**`src/bookkit/services/batches.py:105-106`** — docstring: three sources.

**`src/bookkit/tui/screens/account.py:1371-1382`**
- `result = interactions_svc.delete(...)`; notify `f"{result.summary} — u to undo"`.
  The `except ValueError` branch (`:1377-1380`) and the no-`_batched()` docstring
  (`:1366-1370`) stay exactly as they are — the latter is still true and still the
  best statement of why.

**No change needed** (stated so nobody "tidies" them): `web/routes/relationship.py`
discards the return value; `web/parity.py:121-139` describes the TUI↔web pair only
and remains true.

## Tests

New file `tests/test_interaction_delete.py`, following `tests/test_contact_remove.py`
("the whole slice in one file"). Its own small fixture — one client, one
interaction — hand-made, never Grant's book. Its module docstring must point at the
two tests that stay put: `tests/test_tui.py:1777` (pilot) and
`tests/test_web_writes.py:981` (timeline fixture).

MOVE from `tests/test_mcpserver.py`:
`test_activity_delete_removes_it_and_stays_undoable` (`:777`, and **rewrite its
docstring** — `:778-779` carries the false `u` sentence its own `:796` refutes),
`test_activity_delete_refuses_an_unknown_ref` (`:803`),
`test_activity_delete_refuses_to_delete_twice` (`:814`) — the last two change from
`pytest.raises(KeyError)` to the service's sentences. LEAVE
`test_activity_delete_is_registered_as_a_write_tool` (`:826`) where it is; it
covers `recent_activity` too.

Every mutation below was checked against the code before being written down. Run
it, watch the named test fail, revert.

1. **`test_mcp_delete_writes_the_batch_the_other_surfaces_write`** — after
   `_activity_delete`, the newest batch has `tool == "interaction_delete"`,
   `source == "mcp"`, `summary == f"deleted {subject} from {org.name}"`.
   *Mutation:* **restore the old `_activity_delete` body** (its own `_open_batch(…,
   tool="activity_delete", summary=f"deleted activity: {subject}")` around
   `interactions.delete`) → tool and summary both wrong → red.
   *Mutation NOT to use:* editing `services/interactions.py:38` or `:124`. It reds
   this test, but it also reds `tests/test_web_writes.py:1023`, so it proves the
   shared string is shared — not that MCP routes through it.

2. **`test_the_mcp_delete_is_exactly_one_batch`** — batch count rises by exactly 1,
   and `batches_repo.events_for(conn, batch.id)` is non-empty.
   *Mutation:* wrap the service call in `_open_batch(conn, tool="activity_delete",
   summary="x")` again → the outer `open_batch` INSERTs (`services/batches.py:142`),
   the inner one joins and INSERTs again, both commit at `db.py:220`, events land on
   the outer `BatchState` → count rises by 2 AND the `interaction_delete` row holds
   no events → both assertions red. This is the test that pins the empty-batch
   argument.

3. **`test_an_unknown_ref_is_refused_with_the_services_sentence`** —
   `pytest.raises(ValueError)`, message contains the ref.
   *Mutation:* restore `interactions.get(...)` as the first line of
   `_activity_delete` → `KeyError` → `pytest.raises(ValueError)` red.
   Assert on the ref, not on the remedy clause (Decision 5 rewords it; a test that
   pins the prose makes the wording unchangeable without a red suite).

4. **`test_a_second_delete_says_already_deleted_and_writes_no_batch`** — second call
   raises `ValueError` containing "already deleted" and the org name; batch count
   unchanged by the refusal.
   *Mutation A:* restore `interactions.get` in `_activity_delete` → `KeyError`, not
   `ValueError` → red.
   *Mutation B:* **delete the `already_deleted` guard** (`services/interactions.py:117-119`).
   `raw_row` still returns the dead row, so no refusal is raised;
   `base.soft_delete` (`repo/base.py:181-187`) re-stamps `deleted_at` and logs an
   event unconditionally → a second batch commits → both the raises assertion and
   the count assertion go red.
   *Mutation the draft named, WITHDRAWN — it cannot fail:* "move the
   `already_deleted` check inside `open_batch`". `open_batch` is a
   `@contextmanager` (`services/batches.py:91`) whose `yield batches_repo.create(...)`
   sits inside `with db.transaction(...)` (`:141-145`); a `ValueError` from the
   with-body is thrown back into the generator and reaches `db.py:217-219`, which
   `ROLLBACK`s the batch INSERT and re-raises. The count is unchanged and the test
   stays green. Verified.

5. **`test_the_tool_reports_the_batch_ref_it_wrote`** —
   `batches_repo.get_by_ref(conn, out["batch"]).tool == "interaction_delete"` AND
   that batch's `events_for` contains an event whose `entity_id` is the deleted
   interaction.
   *Mutation:* drop `batch` from `Deletion` or from the MCP dict → `KeyError` in the
   test. Also red under test 2's wrapper mutation: `out["batch"]` would then name the
   `activity_delete` batch.
   *Assertion DROPPED from the draft:* `out["batch"].startswith("MCP-")`. Every
   batch ref in the database begins `MCP-` — `repo/batches.py:12` sets
   `BATCH_REF = "MCP"`, `create()` at `:33` passes it with no reference to `source`,
   and `ids.next_ref` (`ids.py:44`) formats `f"{kind}-{n:04d}"`. It is true of a
   web-sourced delete and no mutation in this change can red it. (The precedent it
   copies, `tests/test_contact_remove.py:183`, has the same weakness — not this
   commit's to fix.)

6. **`test_the_tool_does_not_promise_u`** — `"revert_batch" in out["undo"]` and
   `"u in the TUI" not in out["undo"]`.
   *Mutation:* restore `"u in the TUI restores it"` at `mcpserver.py:874` → red.
   This is a string test for a string defect, and that is all it is; say so in the
   docstring. *Assertion DROPPED from the draft:* `undo.undo_last(conn) is None`.
   `services/undo.py:32, :49` are untouched by this change, so no mutation here can
   red it, and the identical assertion already rides along in the moved
   `test_activity_delete_removes_it_and_stays_undoable` (`:796`) in the same file —
   which is where the sentence's truth-condition is covered.

7. **`test_the_tools_own_description_does_not_promise_u`** — build the server and
   assert `"u in the TUI" not in` the `activity_delete` tool's `description`.
   `Tool.description` is a real field (`mcp` 2.0.0,
   `.venv/lib/python3.13/site-packages/mcp/server/mcpserver/tools/base.py:33`) and
   `server._tool_manager.list_tools()` is already used this way at
   `tests/test_mcpserver.py:826-829`.
   *Mutation:* restore the last sentence of `mcpserver.py:257` → red. Without this
   test the docstring half of Decision 6 has no failure mode at all — which is why
   the same sentence survived in three places for months.

8. **`test_only_the_service_deletes_an_interaction`** — source scan in the
   `tests/test_conventions.py:9-35` style: neither `"interactions.delete("` nor
   `"interactions_repo.delete("` appears in any `src/bookkit/**/*.py` except
   `services/interactions.py` and `repo/`.
   *Mutation:* restore `interactions.delete(conn, interaction.id)` in
   `mcpserver.py` → red. Verified there are no false positives today: the three
   prose mentions (`tui/screens/account.py:1367`, `web/parity.py:130`,
   `web/routes/relationship.py:346`) carry no call parens. This is the seam
   assertion — without it the batching could be bypassed again and stay green
   (CLAUDE.md, 2026-08-15).

Also update the comment at `tests/test_web_writes.py:1019-1022`, which says
`services.interactions._summary` "is shared with the TUI" — after this it is
shared with all three. The assertion at `:1023` is unchanged and still passes.

## Gates

`uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests` — in a
worktree, `uv sync --group dev` first and `uv run --no-sync python -m pytest`.
Redirect output to the scratchpad, gate on the command, tail the file after; never
pipe before the `&&`.

## Out of scope

- Generalising the provenance question — `_member_deactivate` and the other 20+ MCP
  writes keep their surface-side batch and their `_provenance` stamp. That the
  stamp may be redundant everywhere now that every MCP write is batched is a
  separate observation.
- `repo/batches.py:89` hard-codes `field NOT IN ('created', 'source')` instead of
  deriving from `NON_MUTATION_FIELDS`, so `import` / `carrier_alias` / `merged_from`
  count as external changes there. Real, unrelated, unaffected by this change.
- `tests/test_contact_remove.py:183`'s `startswith("MCP-")` assertion is decoration
  for the same reason test 5's dropped half was. Not this commit's file.
- The TUI and web halves of this slice stay in `tests/test_tui.py:1777` and
  `tests/test_web_writes.py:981`. They ride a Textual pilot and a `timeline`
  fixture respectively; porting them is churn with no new coverage. The new file's
  docstring names them.

## Open for Grant

**Does the MCP tool keep the name `activity_delete`, or become `interaction_delete`
so the model-facing vocabulary matches the batch's?**

New evidence since the draft: the model *does* see the mismatch —
`_list_batches` returns `"tool": batch.tool` (`mcpserver.py:2063`), so a model that
calls `activity_delete` and then `list_batches` reads back `interaction_delete`.

- **Recommendation: keep `activity_delete`.** It is the companion of
  `log_activity` and `recent_activity`, and `recent_activity`'s docstring points at
  it by name (`mcpserver.py:244`). Renaming costs three docstrings, the registry
  assertion (`tests/test_mcpserver.py:826-829`), and breaks an API a model has been
  taught, with no deprecation path.
- **Cost if wrong (kept):** the model sees one write under two names in
  `list_batches` — a smaller version of the complaint this item fixes, fixable later
  in one commit.
- **Cost if wrong (renamed):** an existing MCP client calling `activity_delete` gets
  "no such tool", silently, mid-conversation.



---

## Verification report — round 2 (independent adversarial pass)

**Verdict: needs-revision.** The architecture is right and now well-evidenced. Decision 1 (MCP opens no batch, calls the service), Decision 2 (drop _provenance) and Decision 3 (return a frozen Deletion) all survive re-opening: db.py:200-207 joins without rebinding _current_batch, services/batches.py:141-145 always INSERTs on the non-join path, repo/base.py:110-127 stamps the outer batch — so the wrapper shape really does produce a dead interaction_delete row, and services/batches.py:357-362 + web/routes/changes.py:150,176 really do render it as "MCP-42 refused — " with nothing after the dash. Decision 2's "no surface reads per-entity source events" checks out: repo/events.py:11 history has no caller in src, field_history's only consumer is services/pipeline.py:110 on "stage", and repo/batches.py:89 excludes 'source'. All four withdrawn/replaced mutations were the right calls, verified independently: the open_batch-rollback mutation genuinely cannot fire (db.py:217-219), BATCH_REF="MCP" (repo/batches.py:12) genuinely makes startswith("MCP-") vacuous, undo.py:32/:49 are genuinely untouched, and Decision 5's withdrawal of "provably false" is correct (services/contacts.py:167 carries the identical three-surface line for a service that genuinely has three). The rejections are now real: the third option in Decision 1 exists and fails for the reason given. But the revision commits its own defect class three times. (1) It sweeps stale two-surface docstrings at services/interactions.py:1, :110 and services/batches.py:105-106, then explicitly freezes a FOURTH — tui/screens/account.py:1367 "so both surfaces land one identical undo unit" — with the false line "still true". (2) Decision 6 claims FIVE copies of the false `u` promise; grep returns four (mcpserver.py:257, :857, :874, tests/test_mcpserver.py:779) and the document's own body enumerates four then counts the absence of a fifth as the fifth. Round 1 undercounted at three; round 2 overcounts. (3) The new Decision 5 wording "it may already be gone" reintroduces the not-found/already-deleted blur that services/interactions.py:105-108 and repo/base.py:52 exist to forbid — in the commit whose purpose is deleting a second refusal vocabulary. Plus two false supporting claims: Test 5's replacement fires, but not for the stated reason (the service still returns its own batch ref under the wrapper mutation — it reds on events_for, not on naming), and Test 1's excluded mutation on :124 does NOT red test_web_writes.py:1023 because no test anywhere pins "interaction_delete". Fixable without touching the build: correct the count to four, add account.py:1367 to Decision 7, drop the "may already be gone" clause, restate Test 5's mechanism, and hand Grant the alias deprecation path (server.tool(name=...) exists) alongside the rename question.


### Decisions round 2 reversed from round 1

- **Was:** Test 4 is protected by two mutations, the second being "move the `already_deleted` check inside `open_batch` in the service → an empty batch appears → the count assertion fails."
  
  **Now:** That mutation is withdrawn as unable to fail, and named as such in the document. Replaced with "delete the `already_deleted` guard at services/interactions.py:117-119", which lets base.soft_delete re-stamp a dead row and log an event, committing a second batch and reddening both halves of the test.
  
  **Why:** services/batches.py:91 decorates open_batch with @contextmanager and :141-145 puts `yield batches_repo.create(...)` inside `with db.transaction(...)`. A ValueError from the with-body is thrown back into the generator and reaches db.py:217-219, which ROLLBACKs the batch INSERT and re-raises. The count is unchanged; the test stays green. Verified directly.

- **Was:** Test 5 asserts `out["batch"].startswith("MCP-")` as evidence the tool reports the batch it wrote.
  
  **Now:** That assertion is dropped and replaced by `events_for(batch.id)` containing an event for the deleted interaction, alongside the tool check.
  
  **Why:** repo/batches.py:12 sets BATCH_REF = "MCP" as a module constant, create() at :33 passes it with no reference to source, and ids.py:44 formats f"{kind}-{n:04d}". Every ref in the database begins MCP-, including TUI and web ones. No mutation in this change can red it.

- **Was:** Test 6 asserts `undo.undo_last(conn) is None` AND `"revert_batch" in out["undo"]`, "tying the sentence to the behaviour".
  
  **Now:** Only the string half is kept, and the document says plainly it is a string test for a string defect. The behaviour half is covered by the moved test that already asserts it (tests/test_mcpserver.py:796).
  
  **Why:** services/undo.py:32 and :49 are untouched by this change, so the undo_last half is insensitive to every mutation in it, and the same assertion arrives in the same new file with the moved test.

- **Was:** Test 1's mutation is "put tool=\"activity_delete\" back / put the old summary back".
  
  **Now:** The mutation is "restore the old `_activity_delete` body", and the document explicitly names editing services/interactions.py:38/:124 as the mutation NOT to use.
  
  **Why:** After Decision 1 there is no MCP-side tool or summary string left to put back. Mutating the shared strings also reds tests/test_web_writes.py:1023, so it proves the string is shared rather than that MCP routes through it — it does not isolate the seam the test claims to protect.

- **Was:** Decision 7 kept the tool name because "the changes rail never shows a tool NAME to the model".
  
  **Now:** The premise is struck as false. The recommendation (keep the name) survives on the remaining grounds, but the item is moved squarely into blocked_on_grant with the corrected evidence and a corrected cost-if-wrong.
  
  **Why:** mcpserver.py:2062-2066 — `_list_batches` returns `"tool": batch.tool` to the model. A model calling activity_delete then list_batches reads back interaction_delete. The mismatch is model-visible. Neither the draft nor the verifier caught this.

- **Was:** Decision 1 asserted "the MCP side has no third option".
  
  **Now:** A third option is named and rejected on its merits: `delete()` grows a `batch:` parameter and skips opening one, letting MCP keep _open_batch and _provenance with no dead row.
  
  **Why:** The verifier is right that asserting non-existence is the shape this build keeps getting caught by. The option is real but fails independently: MCP's outer batch carries tool="activity_delete", so the divergence survives unless MCP is handed the service's own strings — the exact shape this item exists to delete.

- **Was:** The draft did not rule on the refusal sentence at all; the verifier flagged it as an unaddressed regression in kind.
  
  **Now:** New Decision 5: reword the service's "no interaction …" refusal to name the record rather than a screen ("ids come from the account's own interaction list"), once, in the service. MCP wraps nothing. Test 3 asserts on the ref, not on the remedy clause.
  
  **Why:** services/interactions.py:114-116 tells the caller to "read the account's timeline", which the model does not have. It is also effectively the MCP-only branch after this change: the web 404s an unknown id at _owns_raw_row (web/routes/account.py:200-215) before delete is reached, and the TUI passes an id from the focused row. No test pins the string.

- **Was:** Decision 5 called delete()'s `'mcp' | 'tui' | 'web'` docstring line "provably false" and "overstating".
  
  **Now:** Withdrawn. The line documents the accepted vocabulary of an unvalidated free-str parameter; it simply becomes true of the callers too.
  
  **Why:** services/interactions.py:101 types source as a plain str with no enum behind it. The verifier's minor point is correct and the rhetorical tightening is exactly what this ledger asks people not to do.

- **Was:** Decision 5's "both become true together" — two stale surface-count docstrings.
  
  **Now:** Three. services/batches.py:105-106 — open_batch's own docstring, the function both edited surfaces call — still says source is 'mcp' or 'tui'. Fixed in the same commit.
  
  **Why:** Web has been a third source since the web writes shipped (web/routes/relationship.py:359, asserted at tests/test_web_writes.py:1018). Same defect class, one line, in the function at the centre of this change.

- **Was:** Decision 6: the false `u` promise lives in three places and this commit touches all three.
  
  **Now:** Five. The fourth is tests/test_mcpserver.py:778-779, the docstring of a test the brief itself MOVES; the fifth check confirmed there are no others. The moved test's docstring is rewritten as part of the move.
  
  **Why:** That docstring says "`u` in the TUI puts it back" and is contradicted by its own body at :796. Moving a test carries its docstring, so the false sentence would have survived the commit that claims to delete the class.

- **Was:** Nothing was said about what happens when the interaction's org has been soft-deleted or merged away.
  
  **Now:** New Decision 8: named as a known limit, not guarded. services/interactions.py:121 calls the alive-filtered orgs_repo.get, which raises KeyError for a dead org — reachable from a model holding a stale ref.
  
  **Why:** repo/orgs.py:19-23 → repo/base.py:148-153. It is pre-existing behaviour for the TUI and web; adding a guard here would invent a second refusal vocabulary in the commit that removes one. Costed instead.


### Regressions the revision introduced

*This is the list that stopped the iteration: a fix reproducing its own defect class one level down.*

- Decision 7 widens three docstrings from two surfaces to three and then explicitly instructs that tui/screens/account.py:1366-1370 "stay exactly as they are — the latter is still true". Line 1367-1368 says "so both surfaces land one identical undo unit — tool AND summary", which this commit makes three. The commit's own stated rule ("a docstring that claims a shared rule the code no longer matches") is applied to services/batches.py:105 and refused one file away, with a false justification attached.

- Decision 6's count went from three (round 1, undercount) to five (round 2, overcount). The true number is four: grep -rn "u in the TUI" src/ tests/ returns mcpserver.py:257, :857, :874 and tests/test_mcpserver.py:779. The document lists four and then writes "Fifth: nothing else; grepped", counting the confirmed absence of a fifth as the fifth. A fix that closes a coverage finding by inflating the coverage count.

- New Decision 5 fixes the UI-affordance problem in the refusal and introduces a truth problem: "it may already be gone" contradicts services/interactions.py:105-108, which is the documented reason the branch reads raw_row rather than get — "'already deleted' rather than 'not found', which is a different and less true thing to tell". The already-deleted case is handled by a separate, better sentence at :98; the not-found branch should not hedge toward it.

- Test 5's dropped startswith("MCP-") assertion was correctly withdrawn, but the replacement's rationale is a new false claim: "out[\"batch\"] would then name the activity_delete batch" under Test 2's wrapper mutation. It would not — Deletion.batch is built from the service's own batch.ref. The assertion still fires (via events_for on an empty inner batch), so coverage is real; the traced mechanism is not. Round 1 was rejected in this exact test for an untraceable mutation claim.

- Test 1's "Mutation NOT to use" names services/interactions.py:38 or :124 on the grounds that both also red tests/test_web_writes.py:1023. Only :38 does. "interaction_delete" appears in no test in the repo, so :124 is pinned by nothing today — the exclusion may still be right, but half its evidence is fabricated.

- Decision 1 correctly struck "the MCP side has no third option" as an unchecked non-existence claim. Decision 9 then asserts renaming has "no deprecation path" — equally unchecked and equally false: mcp/server/mcpserver/server.py:621-630 exposes tool(name=...), so both names can be registered over one implementation. The same shape, two decisions later, in the item the document routes to Grant.


### Citations that still did not check out

- **`Citation 26 — .venv/lib/python3.13/site-packages/mcp/server/mcpserver/tools/base.py:33`** — claimed: Tool.description is a real field on the registered tool, so a test can assert the activity_delete tool's docstring does not promise `u` (mcp 2.0.0).
  
  *Actually:* Line 33 is `title: str | None = Field(None, description="Human-readable title of the tool")`. `description: str = Field(...)` is at :34. The underlying claim HOLDS (the field exists, mcp is 2.0.0 per the dist-info METADATA, and server._tool_manager.list_tools() is already used synchronously at tests/test_mcpserver.py:828). Off-by-one reference only — fix the line, keep the test.

- **`Decision 6 heading + "What is true today" point 3 — "in all FIVE places"`** — claimed: The false `u` promise lives in FIVE places; the fourth is tests/test_mcpserver.py:778-779 and the fifth check confirmed there are no others.
  
  *Actually:* There are FOUR. `grep -rn "u in the TUI" src/ tests/` returns exactly: src/bookkit/mcpserver.py:257, :857, :874, tests/test_mcpserver.py:779. The document's own body enumerates four and then writes "Fifth: nothing else" — i.e. it counts the absence of a fifth as the fifth. Round 1 said three; the fix should say four. Over-claiming a coverage count is the exact failure the revision was supposed to close, reproduced one level down.

- **`Test 5 — "Also red under test 2's wrapper mutation: out[\"batch\"] would then name the activity_delete batch."`** — claimed: Under the wrapper mutation, out["batch"] names the wrapper's activity_delete batch, so the tool assertion reds.
  
  *Actually:* False. Under the mutation as stated ("wrap the service call in _open_batch(...) again"), `Deletion.batch` is still built inside services/interactions.py from its OWN batch.ref (the interaction_delete row), so out["batch"].tool == "interaction_delete" still passes. The test does red — but via the NEW events_for half, because services/batches.py:141-145 creates the inner row while repo/base.py:110-127 stamps every event with the OUTER BatchState (db.py:200-207 never rebinds _current_batch), leaving the interaction_delete row empty. The assertion is sound; the stated mechanism is wrong. This is a new false claim introduced by the revision, in the very test round 1 was rejected over.

- **`Test 1 — "Mutation NOT to use: editing services/interactions.py:38 or :124. It reds this test, but it also reds tests/test_web_writes.py:1023"`** — claimed: Mutating either :38 (_summary) or :124 (tool="interaction_delete") also reds tests/test_web_writes.py:1023.
  
  *Actually:* True for :38 (test_web_writes.py:1023 asserts entry.subject and org.name are in batch.summary). FALSE for :124: `grep -rn interaction_delete tests/` returns NOTHING — the string "interaction_delete" appears only at src/bookkit/services/interactions.py:124 and in two web route function names. No existing test pins the tool value at all. The exclusion may still be right on the seam-isolation argument, but the evidence offered for half of it does not exist.

- **`Citation 28 / point 2 — tui/screens/navigator.py:350-352 "reads batches_repo.recent unfiltered"`** — claimed: The R table reads batches_repo.recent unfiltered.
  
  *Actually:* navigator.py:350-352 passes `since=(today - timedelta(days=14)).isoformat()` — a 14-day cutoff. It is unfiltered by ORG and by emptiness, which is all the argument needs, so the conclusion (a dead batch shows in R) holds. "Unfiltered" as written is imprecise.

- **`Decision 8 — "services/interactions.py:121 calls the alive-filtered orgs_repo.get"`** — claimed: :121 is where delete() raises KeyError on a soft-deleted/merged org.
  
  *Actually:* :121 does call orgs_repo.get (repo/orgs.py:19-23 → repo/base.py:148-153, KeyError). But it is not the only exposure inside delete(): already_deleted() calls the same alive-filtered orgs_repo.get at :97, and it runs FIRST (:117). For an already-deleted interaction on a dead org the KeyError comes from :97, not :121, and it pre-empts the already_deleted refusal the commit is otherwise careful about. The limit is real; the line is one of two.

- **`Table row — "rendered by web/templates/account/page.html:134-161"`** — claimed: The Recent changes rail is rendered at page.html:134-161.
  
  *Actually:* The section opens at :133 (`<section class="rail-section">`), the label is :134, and the loop body runs past :161 (:161 is the hx-confirm attribute; the reverted branch is :163-165). Off-by-a-few; `{{ c.what }}` at :140 and `{{ c.who }}` at :143 confirm the claim.


### Claims challenged

- **[CRITICAL]** Changes / TUI: "The except ValueError branch (:1377-1380) and the no-_batched() docstring (:1366-1370) stay exactly as they are — the latter is still true and still the best statement of why."
  
  *Evidence:* tui/screens/account.py:1367-1368 reads "services.interactions.delete opens its own batch so BOTH SURFACES land one identical undo unit — tool AND summary". After this commit there are THREE. That is precisely the stale-surface-count defect Decision 7 sweeps at services/interactions.py:1, services/interactions.py:110 and services/batches.py:105-106 — and the document freezes a fourth instance with an explicit false justification ("still true"). A commit that widens two docstrings from two surfaces to three, in the name of "a docstring that claims a shared rule the code no longer matches", and leaves a fourth saying "both surfaces" one file away, is the defect class reproduced one level down.

- **[CRITICAL]** Decision 5's proposed wording: f"no interaction {interaction_id!r} — it may already be gone; ids come from the account's own interaction list"
  
  *Evidence:* services/interactions.py:105-108 states the rule this branch exists to enforce: "`raw_row`, not `get`, so an entry deleted a minute ago gets 'already deleted' rather than 'not found', which is a different and LESS TRUE thing to tell". repo/base.py:52 repeats it. The whole point of the two-branch split at :112-119 is that "no interaction" means the row NEVER existed (interactions are soft-deleted, so a gone row is still in the table and already_deleted() at :94-98 catches it). "it may already be gone" reintroduces exactly the blur the service documents itself as removing — in the commit whose stated purpose is deleting a second refusal vocabulary. The UI-affordance half of the fix is right; the hedge is a regression. Recommend: f"no interaction {interaction_id!r} — ids come from the account's own interaction list", nothing more.

- **[IMPORTANT]** Decision 9 / Open for Grant: renaming "breaks an API a model has been taught, with no deprecation path"; cost-if-wrong (renamed) is "'no such tool', silently, mid-conversation".
  
  *Evidence:* .venv/lib/python3.13/site-packages/mcp/server/mcpserver/server.py:621-630 — `def tool(self, name: str | None = None, title=None, description=None, ...)`. A deprecation path is five lines: register `interaction_delete` and keep `activity_delete` as a second decorated wrapper (or `@server.tool(name="activity_delete")`) over the same `_activity_delete`. The document asserts a non-existence it did not check — the identical shape it struck from Decision 1 ("the MCP side has no third option") two decisions earlier. The cost-if-wrong for the rename is therefore inflated, which biases the recommendation it hands Grant. The question should go to him with the alias option costed.

- **[MINOR]** Test 2: "→ count rises by 2 AND the interaction_delete row holds no events → BOTH assertions red."
  
  *Evidence:* Traced: MCP _open_batch → batches_svc.open_batch (entity_id None) → create path at services/batches.py:140-145 INSERTs row 1 under `with db.transaction(conn, batch=...)`; the inner service call re-enters db.transaction at depth 1 (db.py:200-207, joins, never rebinds _current_batch) and INSERTs row 2; repo/base.py:110-127 stamps the deleted_at event with the OUTER batch_id; db.py:220 COMMITs both. Count +2 — that half reds. The events_for half only reds if the test selects the NEWEST batch (batches_repo.most_recent, rowid DESC → the interaction_delete row). If it selects by out["batch"], that is the service's own ref and — see the Test 5 failure above — the service still returns its own ref, so the row is the empty one and it reds too. Either way it reds, but the brief should say WHICH batch the test binds; as written the second assertion's failure depends on an unspecified selection.

- **[MINOR]** Test 4 Mutation B is offered as the replacement, while Test 1's shared-string mutation is rejected precisely BECAUSE it lives in shared code and "does not isolate the seam the test claims to protect".
  
  *Evidence:* Mutation B (delete services/interactions.py:117-119) does fire — verified: repo/base.py:186 UPDATEs with no aliveness filter and :187 logs unconditionally, so a second batch commits and both the raises and the count assertion red. And it happens to be isolated: `grep -rn "already deleted\|already_deleted" tests/` shows NO existing test covers this guard on any surface. So B is acceptable. But it is a mutation in the shared service, judged by a standard the document applies in the opposite direction one page earlier. Say why B is admissible (it is the guard's own failure mode, and nothing else covers it) rather than leaving two contradictory rules for what counts as a legitimate mutation.

- **[MINOR]** Test 7: "Without this test the docstring half of Decision 6 has no failure mode at all."
  
  *Evidence:* Test 7 covers ONLY mcpserver.py:257, because Tool.description is built from the @server.tool()-decorated `async def activity_delete` docstring (:252-257), not from `_activity_delete`'s implementation docstring (:857) or from the moved test's own docstring (:778-779). Two of the four copies still have no failure mode after this commit. The singular "the docstring half" papers over that; state it, since the whole point of Decision 6 is that this sentence survived unnoticed for months.

- **[MINOR]** Test 8: "This is the seam assertion — without it the batching could be bypassed again and stay green."
  
  *Evidence:* Verified there are no false positives today — `grep -rn "interactions\.delete(" src/bookkit/` returns only mcpserver.py:871, and the three prose hits (tui/screens/account.py:1367, web/parity.py:130, web/routes/relationship.py:346) carry no parens; `interactions_svc.delete(` does not contain the substring `interactions.delete(`. So the scan works as described. But it is a substring scan: `from .repo.interactions import delete` + a bare `delete(conn, id)`, or any other alias, walks straight past it. The same weakness as tests/test_conventions.py:9-35, which is the acknowledged precedent — just do not call it THE seam assertion without that caveat, given CLAUDE.md 2026-08-15 is about a seam that looked asserted and was not.


### Needs Grant

- MCP tool NAME: keep `activity_delete`, or rename to `interaction_delete` so the model-facing vocabulary matches the batch's? NEW EVIDENCE the draft got backwards — the draft justified keeping it with "the changes rail never shows a tool NAME to the model", which is false: `_list_batches` returns `"tool": batch.tool` to the model (mcpserver.py:2063), so a model that calls activity_delete then list_batches reads back interaction_delete. RECOMMENDATION: still keep `activity_delete` — it is the companion of log_activity/recent_activity, recent_activity's docstring names it (mcpserver.py:244), and renaming is a model-facing API break with no deprecation path, costing three docstrings and the registry assertion (tests/test_mcpserver.py:826-829). COST IF KEPT AND WRONG: the model sees one write under two names in list_batches — a smaller version of the complaint this item fixes, fixable later in one commit. COST IF RENAMED AND WRONG: an existing MCP client calling activity_delete gets 'no such tool', silently, mid-conversation.


### Deliberately not settled

- Whether the reworded 'no interaction …' refusal (Decision 5) reads well to a model in practice. It is deliberately untested on its prose (test 3 asserts the ref only) so the wording stays changeable without a red suite; if a model flounders, the fix is one line in the activity_delete docstring, not a code path.

- Whether the per-entity `_provenance` stamp is redundant across the other 20+ MCP writes now that every MCP write is batched. Named as an observation, deliberately not acted on here.

- tests/test_contact_remove.py:183's `startswith("MCP-")` assertion has the same weakness as the one dropped from test 5. Left alone — not this commit's file.

- repo/batches.py:89 hard-codes `field NOT IN ('created','source')` instead of deriving from NON_MUTATION_FIELDS, so import/carrier_alias/merged_from count as external changes there. Real, unrelated, unaffected.

- delete() raises KeyError (not a sentence) when the interaction's account has been soft-deleted or merged away — pre-existing for all three surfaces, newly reachable from a stale model-held ref. Named and costed in Decision 8, not guarded.
