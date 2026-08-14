# MCP write expansion — design

Date: 2026-08-13
Status: approved (Grant, 2026-08-13, blanket approval before overnight run:
"I am going to approve spec/plan - proceed to code")

## Goal

MCP manages the whole book side: edit existing values deliberately, create
contacts / opportunities / projects / needs / team members, assign the team,
and move pipeline stages — every write a revertible batch under the blast
cap. Placements and towerkit policy records are explicitly NOT here (own
spec; the file-write cycle is a different risk class).

## Decisions (Grant, 2026-08-13)

1. **Compare-and-set on every overwrite.** `edit_field` requires
   `expecting` — the value the model believes is current, from a read it
   just did. Mismatch → refusal naming the actual value, nothing written.
   A stale or hallucinated read can never blind-overwrite; batch revert is
   the net underneath, not the gate. (Rejected: free overwrite — the bad
   edit lands first and is caught later; reason-string audit — the model
   writes the reason too, so it gates nothing.)
2. **No new delete tools this phase.** revert_batch unwinds anything MCP
   creates wrongly, which is the real delete need; removing long-lived
   records is data hygiene done in the TUI with eyes on it. Additive later.
3. **Team scope: members AND assignments.** member_create, team_assign,
   team_unassign; member field edits ride edit_field. Assignments drive
   attention routing and the queued needs→pipeline auto-assign, and a
   mistaken assignment is one revert away.
4. **Named per-entity tools, not a generic entity_write.** Per-entity
   docstrings are how the model learns each contract; validation stays
   flat. (Named as the option I'd recommend against: one generic tool —
   smaller surface, conditional-soup validation, no per-entity guidance.)

## Tools

### edit_field (new; enrich_field stays as the fill-blanks reflex)

`edit_field(kind, ref, field, value, expecting, client=None)`

- `kind` ∈ org | contact | opportunity | project | project_need | task |
  team_member | rfi_request | rfi_item. `ref` resolves per kind: orgs by
  exact name/ref (the `_resolve_client` discipline), contacts by exact name
  within `client`, everything else by the exact ref/id a read returned —
  never fuzzy.
- Allowlists per kind in one `_EDITABLE` table (extends `_ENRICHABLE_*`):
  - org: the `_ENRICHABLE_ORG` set
  - contact: the `_ENRICHABLE_CONTACT` set + first_name, last_name
  - opportunity: title, lines, target_premium, target_effective,
    probability_pct, source, incumbent_broker, notes —
    **never stage / outcome / closed_at / loss_reason** (transitions own
    those)
  - project: name, site, status, start_on, end_on, notes
  - project_need: line, needed_by, notes — status deferred to the
    needs→pipeline sync spec (its reconciler owns need-status semantics)
  - task: title, description, detail, category, due_on
  - team_member: role, specialty, email, phone
  - rfi_request: title, due_on, notes
  - rfi_item: prompt, category, due_on, response
- Values route through the SAME cleaners the forms use (money → cents,
  human dates via parse_human_date, email/phone/url/domain normalizers).
- `expecting` is cleaned by the same cleaner before comparing, so the model
  echoes what it read in human form. `expecting=null` asserts the field is
  blank (enrich semantics, made explicit).
- Vocabulary fields (project.status, …) validate against the models.py
  tuples; a refusal lists the legal values.

### Creates (all batched, all return the batch ref)

- `contact_add(client, first_name, last_name, …, make_primary=False)` —
  duplicate guard: exact name match on the same client refuses.
- `opportunity_create(client, title, lines, target_premium=None,
  target_effective=None, …)` — rapidfuzz dup guard against OPEN
  opportunities on the same client (same discipline and cutoff style as
  client_create). Needs-born opportunities remain the queued
  needs→pipeline service's job; this tool is for standalone deals.
- `project_create(client, name, site=None, start_on=None, end_on=None)`
- `need_add(project_ref, line, needed_by=None, notes=None)`
- `member_create(name, role=None, specialty=None, email=None, phone=None)`
  — duplicate guard on exact name.
- `team_assign(member, client=None, lines=None, role=None)` — member by
  exact name; client omitted = book-wide assignment (matches repo.assign).
- `team_unassign(assignment_id)` — exact id from a team read.
  (Team reads: `team_roster()` — members + assignments with ids — added so
  refs are readable; today no MCP read exposes them.)

### Transitions

- `opportunity_stage(ref, to, note=None, loss_reason=None)` →
  services.pipeline.move_stage. A refused move surfaces allowed_next(stage)
  so the model learns the ladder instead of retrying blind.
- `task_reopen(task_ref)` — repo.tasks.reopen, the loop-closer for
  task_complete.
- `request_item_waive(item_ref)` — completes the RFI item vocabulary next
  to request_item_received.

## Cross-cutting

- Every tool writes through `_open_batch` (one call = one revertible unit,
  blast cap 50 enforced under log_event).
- Money integer cents; dates ISO after parse; two-digit years 20xx; never
  dateparser century-bumps (towerkit fast path via bookkit.dates).
- Errors follow the house shape: name the miss, list the candidates or the
  legal values, never guess.

## Testing

- Per tool: returns a batch ref; its batch reverts cleanly (create tools:
  the whole bundle vanishes; assignment: the row is gone).
- Compare-and-set: mismatch refusal leaves the row byte-identical (assert
  the value, not just the error); expecting=null on a non-blank field
  refuses; cleaned-form comparison (money entered as "1.2m" matches a
  120000000-cent stored value).
- Cleaner parity: an MCP-edited email/phone/date/money lands identical to
  the same edit through the TUI form.
- Stage: illegal jump refused, error carries allowed_next; won sets
  probability 100/outcome/closed_at (asserting move_stage is really the
  path).
- Dup guards: contact same-name, opportunity fuzzy, member same-name.
- Protocol round-trip for edit_field (the compare-and-set arg shape crosses
  the wire).
- MCP tool-floor test rises to the new count; convention gates as always.

## Out of scope (v1)

Deletes; placements and towerkit policy records (next spec); needs→pipeline
auto-create (queued spec owns it, including need-status semantics);
enrich_field removal; bulk/multi-record edit tools.
