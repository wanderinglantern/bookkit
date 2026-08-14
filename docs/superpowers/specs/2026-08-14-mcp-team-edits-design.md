# MCP team edits — design

Date: 2026-08-14
Status: approved (Grant, 2026-08-14 — design approved in chat, spec to follow)

## Goal

Close the two team gaps the MCP write expansion left behind: an existing
team assignment cannot be corrected in place, and a team member can be
neither renamed nor retired. Both surfaced as a real error at work —
"the current MCP do not include an endpoint to edit client team members."

Re-scoping an assignment (moving it between clients, or account-level ↔
deal-level) is explicitly NOT here: it stays `team_unassign` +
`team_assign`. Reasoning in Decision 4.

## What exists today

`team_roster` (read), `member_create`, `team_assign`, `team_unassign`, and
`edit_field(kind="team_member")` over title / specialty / email / phone /
notes. What is missing:

- **No in-place edit of an assignment.** `team_assignment` is not a `kind`
  in `_EDITABLE` (`mcpserver.py:1497` has `team_member` only) and there is
  no `team_reassign` tool. Changing a role means `team_unassign` +
  `team_assign` — two batches, so two undo units, and the assignment loses
  its original `created_at`.
- **`name` and `active` are unreachable.** `name` is the lookup key and is
  not in the registry; `active` is not either, and no deactivate tool
  exists. A misspelled colleague cannot be corrected and a departed one
  cannot be retired.

## Decisions (Grant, 2026-08-14)

1. **Deactivation is its own tool, not a field edit.** `member_deactivate`
   / `member_reactivate`; `edit_field` keeps refusing `active`. This is the
   convention every state transition in this codebase already follows —
   `opportunity_stage` ("the ONLY way stage changes"), `task_complete` /
   `task_reopen`, `team_unassign`, `request_item_waive`. Retiring someone
   drops them out of `list_members(active_only=True)` and out of every
   picker; the batch summary should read "deactivated Sarah Chen", not
   "edited team_member.active on Sarah Chen". Costs two tools (39 → 41).
   (Rejected: a new `bool` value type on `edit_field` — no new tools, but
   it needs a human form pinned for yes/no, `expecting=None` is dead
   because `active` is `NOT NULL DEFAULT 1` and therefore never blank, and
   it reads as a field edit rather than the roster change it is.)
2. **Deactivating someone who still holds assignments refuses and names
   the clients; `cascade=True` does the whole thing as one batch.**
   "Surface, don't guess" — the same instinct as `revert_batch` refusing
   when a field changed since. The escape hatch matters because a departing
   colleague with a dozen accounts is a common event, and twelve separate
   `team_unassign` calls is twelve separate undo units. (Named as the
   option I would recommend against: leaving assignments alone entirely —
   simplest, but a client roster would keep listing someone who left, and
   attention routing would keep pointing at them. Also rejected: refuse
   with no cascade — strictest, but it makes the common case expensive
   and the result is not revertible as one unit.)
3. **Rename rides `edit_field`, with a duplicate guard.** No new tool; the
   guard is not optional. Both `_find_member` (`mcpserver.py:1280`) and
   `_edit_target` (`mcpserver.py:1583`) resolve members with
   `next((m for m in members if m.name.lower() == ref.lower()), None)`.
   Two members sharing a name makes that silently pick whichever sorts
   first, so the guard prevents a wrong-record write — it is not tidiness.
4. **Assignment edit covers role, lines, notes — never `org_id` /
   `placement_id`.** Re-scoping changes attention routing and the `org_id`
   stamped on the batch, and the DDL holds
   `CHECK ((org_id IS NULL) != (placement_id IS NULL))`, which requires
   both columns to move together. `edit_field` writes one field per call,
   so single-field compare-and-set structurally cannot perform that move.
   Re-scoping stays `team_unassign` + `team_assign`. (Rejected: role and
   lines only — `team_assignment.notes` is a real column that
   `team_assign` never sets, so leaving it out makes it dead weight.)

## Changes

### `edit_field` — new kind `team_assignment`

```python
"team_assignment": {"role": TEAM_ROLES, "lines": "text", "notes": "text"},
```

**No new value type.** `_clean_typed` already treats a tuple as a closed
vocabulary and refusals list it (`mcpserver.py:1530`); `"status":
PROJECT_STATUSES` at `mcpserver.py:1484` is the precedent. `TEAM_ROLES`
drops in and gets the same validation `team_assign` applies at
`mcpserver.py:1347`, so the two paths cannot drift.

One new `_edit_target` branch:

- `ref` is the `assignment_id` `team_roster` returned. Resolve with
  `base.get(conn, "team_assignment", ref)`; on a miss, refuse with
  `_team_unassign`'s existing wording — "read team_roster for exact ids".
- Batch `org_id` is `row["org_id"]` for an account-level assignment, or
  the placement's `org_id` for a deal-level one.
- The write path needs no change: `_edit_field` already calls
  `base.update(conn, kind, entity_id, …)` with `kind` as the table name,
  and `team_assignment` *is* the table.

### `team_roster` — emit `notes`

Compare-and-set requires `expecting` to be the value a read returned.
`team_roster` currently emits only `assignment_id`, `account`,
`placement`, `role`, `lines` (`mcpserver.py:1298`). Without `notes` in
that payload, `notes` would be advertised as editable and be unreachable
in practice. Add the key.

### `edit_field` — `team_member.name`

`"name": "text"` joins `_EDITABLE["team_member"]`. `ref` is the **old**
name; resolution runs before the write, so the existing flow works.

Guard: refuse when another member — active or inactive, case-insensitive —
already holds the target name, mirroring `_member_create`'s duplicate
check at `mcpserver.py:1319`. The refusal names the colliding member.

### New tool: `member_deactivate(name, cascade=False)`

Returns `{name, active, unassigned, batch}`.

- "Live assignments" means every undeleted row in `team_assignment` for
  that member — **account-level and deal-level both**. `team.for_member`
  already returns both and resolves each to an org name via
  `COALESCE(ta.org_id, p.org_id)`, so the refusal can name the client
  either way and deal-level rows are labelled with their placement ref.
- Bare call with live assignments → refuse, naming them:
  "Sarah Chen is still on 12 assignments: Acme, Borealis, Cascade
  (PLC-0142), … Unassign them, or pass cascade=True to remove all 12 and
  deactivate as one revertible batch."
- `cascade=True` → soft-delete every live assignment and set `active = 0`
  inside **one** `_open_batch`, so `revert_batch` restores all N+1 rows
  together.
- The cascade spans clients, so the batch's `org_id` is `None` (batches
  allow it — `mcpserver.py:722`) and the client names go in the summary.
- Over-cap cascades raise `BlastRadiusExceeded` and roll back to nothing.
  That is already correct at `BLAST_CAP = 250` and needs no special case.
- Already inactive → refuse rather than no-op, so the caller learns the
  state instead of assuming it acted.

### New tool: `member_reactivate(name)`

Returns `{name, active, batch}`. Flips `active` only. Assignments removed
by a cascade do **not** come back — `revert_batch` is the undo for that.
Saying so plainly beats half-restoring. Already active → refuse.

### `edit_field` — redirect on `active`

`active` stays out of `_EDITABLE`. The generic refusal
(`f"{field!r} is not editable on a {kind}; allowed: …"`) gets a redirect
for `team_member.active` pointing at the two tools, the same courtesy
`opportunity_stage` gets in its docstring.

## Testing

Assignment edit:
- role change happy path, by `assignment_id` from `team_roster`
- a role outside `TEAM_ROLES` is refused and the refusal lists the vocabulary
- compare-and-set mismatch refuses and writes nothing
- `notes` round-trips: read it from the `team_roster` payload, pass it as
  `expecting`, write
- `org_id` is refused as not editable (re-scoping stays unassign+assign)

Rename:
- happy path resolving by the old name
- collision refused against an active holder, and against an inactive one

Deactivate / reactivate:
- refusal names the clients and writes nothing
- `cascade=True` is ONE batch; `revert_batch` restores every assignment
  and `active`
- deactivating a member with no assignments needs no cascade flag
- `edit_field(kind="team_member", field="active")` returns the redirect
- reactivate does not resurrect cascaded assignments

Existing convention tests (no raw SQL outside `repo/`, blast cap) cover
the rest.

## Out of scope

- Re-scoping an assignment between clients or between account and deal
  level (Decision 4).
- Hard-deleting a team member. `member_deactivate` is the retirement path;
  removing long-lived records stays TUI work with eyes on it, per the
  write-expansion spec's Decision 2.
- TUI changes. This is MCP surface only; the TUI already edits members and
  assignments through its own forms.
