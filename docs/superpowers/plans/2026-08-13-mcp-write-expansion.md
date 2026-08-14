# MCP Write Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MCP manages the whole book side — deliberate edits (compare-and-set), creates for contacts/opportunities/projects/needs/team, assignments, and staged transitions, every write a revertible batch.

**Architecture:** All tools live in `mcpserver.py` delegating to existing `repo/` + `services/` functions through `_open_batch`. One `_EDITABLE` allowlist table + one `_resolve_target` dispatcher back `edit_field`; value cleaning reuses the form cleaners via the `_clean_field_value` seam that `enrich_field` already uses.

**Tech Stack:** Python 3.13, SQLite, MCP SDK, rapidfuzz, pytest/mypy/ruff.

**Spec:** `docs/superpowers/specs/2026-08-13-mcp-write-expansion-design.md`

**Note on granularity:** authored and executed in the same session with the
spec in context (Grant's blanket overnight approval); tasks carry interfaces
and test names rather than full inline code. An executor without this
session's context should read the spec first and follow existing tool
patterns in `mcpserver.py`.

## Global Constraints

- Gates before every commit: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check src tests`; output to the scratchpad, never piped before the gate.
- Every write through `_open_batch`; every return carries `"batch"`.
- Exact refs only — never fuzzy resolution of a write target; misses list candidates.
- Money cents; dates via `bookkit.dates.parse_human_date`; cleaners shared with forms.
- `repo/` owns SQL (services/tui/mcpserver greps enforce).

---

### Task 1: `edit_field` core — org + contact, compare-and-set

**Files:** `src/bookkit/mcpserver.py`, `tests/test_mcpserver.py`
**Produces:** `_edit_field(conn, kind, ref, field, value, expecting, client=None)`; registered `edit_field`; `_EDITABLE` table (org, contact rows); `_clean_for(field, value)` shared cleaner lookup.

Tests (red first):
- `test_edit_field_overwrites_when_expecting_matches` (org.website; batch ref returned; revert restores)
- `test_edit_field_refuses_on_stale_expecting_and_writes_nothing`
- `test_edit_field_expecting_null_means_blank` (non-blank field refused; blank field filled)
- `test_edit_field_cleans_expecting_and_value_alike` (money/date human forms)
- `test_edit_field_rejects_fields_off_the_allowlist`
- `test_edit_field_contact_scope_requires_client_and_exact_name`

### Task 2: `edit_field` remaining kinds + vocabulary validation

**Files:** same
**Produces:** `_EDITABLE` rows for opportunity, project, project_need, task, team_member, rfi_request, rfi_item; `_resolve_target(conn, kind, ref, client)` dispatcher; vocab validation against models.py tuples.

Tests:
- `test_edit_field_moves_a_task_due_date` (exact task ref)
- `test_edit_field_never_touches_opportunity_stage` (allowlist refusal names opportunity_stage)
- `test_edit_field_validates_vocab_fields_and_lists_legal_values` (project.status)
- `test_edit_field_edits_rfi_item_response`
- `test_edit_field_team_member_by_exact_name`

### Task 3: creates — contact_add, opportunity_create, project_create, need_add

**Files:** same
**Produces:** four `_`-helpers + registered tools, each `_open_batch`ed with dup guards per spec.

Tests:
- `test_contact_add_links_and_optionally_primaries`
- `test_contact_add_refuses_exact_duplicate_name`
- `test_opportunity_create_dup_guard_fuzzy_against_open_opps`
- `test_opportunity_create_closed_opps_do_not_block`
- `test_project_create_and_need_add_round_trip`
- `test_create_batches_revert_wholesale` (opportunity_create reverted → gone)

### Task 4: team — roster read, member_create, team_assign, team_unassign

**Files:** same
**Produces:** `team_roster()` (members + assignments with ids), `member_create`, `team_assign`, `team_unassign`; repo.team signatures verified before wiring.

Tests:
- `test_team_roster_exposes_assignment_ids`
- `test_member_create_refuses_duplicate_name`
- `test_team_assign_by_exact_member_name_scopes_org_and_lines`
- `test_team_assign_book_wide_when_client_omitted`
- `test_unassign_takes_exact_id_and_reverts`

### Task 5: transitions — opportunity_stage, task_reopen, request_item_waive

**Files:** same
**Produces:** three tools; `opportunity_stage` delegates to `services.pipeline.move_stage`, refusal carries `allowed_next`.

Tests:
- `test_opportunity_stage_advances_one_gate`
- `test_opportunity_stage_illegal_jump_refused_with_ladder`
- `test_opportunity_stage_won_closes_properly` (probability 100, outcome, closed_at)
- `test_task_reopen_flips_back`
- `test_request_item_waive_sets_status`

### Task 6: wire-level + floor + docs

**Files:** `tests/test_mcp_roundtrip.py`, `tests/test_mcpserver.py`, `README.md`
**Steps:** protocol round-trip for `edit_field` (compare-and-set arg shape crosses the wire); raise the MCP tool-floor count; README connector section gains one line on write scope + revert.

### Task 7: final review + merge

Full gates on tip; live probe: `edit_field` mismatch refusal over stdio; revert of a `team_assign` batch; merge `feat/mcp-write-expansion` → main.
