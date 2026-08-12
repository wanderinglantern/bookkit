# Forms commit in place, direct layer editing, expiry visibility

**Date:** 2026-08-12
**Status:** Approved design, pre-implementation

## Problem

Two navigation irritations, worst in layer editing:

1. `FormModal` parses field text in-modal, but the actual save runs in the
   dismiss callback — after the form is gone. A write-through refusal
   ("gap under layer…", "period ends before it starts") arrives as a toast
   over a closed form; correcting it means reopening and retyping everything.
2. Editing a layer takes three steps: placements tab → `l` → picker modal →
   form, even when the target is already on screen or is the only layer.

## Decision (Grant, 2026-08-12): commit-in-place is the DEFAULT

Not an opt-in for write-through forms — the standard wiring for every form in
the TUI. A form never dismisses until its save has actually succeeded.

## Design

### FormModal.commit

- `FormModal(spec, commit=fn)` where `commit: Callable[[dict], str | None]`
  performs the save and returns an error message (stay open) or `None`
  (success → dismiss with the values).
- `action_save`: parse fields exactly as today (parse errors already stay
  in-modal and focus the offending field) → `commit(values)` → on error,
  `notify(severity="error")` with input intact → on `None`, `dismiss(values)`.
- Exceptions inside `commit` are caught and shown as the error message —
  a failed save must never crash the TUI (same rule as PasteImportModal).
- The dismiss callback shrinks to success-side effects only: notify +
  `refresh_data()` (and any follow-up prompts, e.g. bind-offer).

### Rollout: every form call site

All `push_screen(FormModal(...), saved)` sites move their repo/sync call into
`commit`; `saved` keeps only success effects. Sites include (find them all —
this list is the expectation, not the limit): account.py (entity add/edit,
layer edit/add, linked-placement/program edit, bind-market share, scaffold
path form), today.py (task forms), markets.py, team.py, book.py, and the
entity_forms.py helpers they share. Forms whose save is a plain repo write
still gain the behavior — repo exceptions (constraint violations, KeyError)
currently crash or half-apply; under `commit` they become a stay-open error.

`forms.dropped()` and the parsed-values contract are unchanged.

### Direct layer editing (smart `l`)

- carriers-table rows get `key=<layer_id>`; layers with zero participants get
  a placeholder row ("— to be placed —") so every layer is reachable.
- `action_edit_layer` resolution order:
  1. carriers-table focused → edit its highlighted row's layer, no picker;
  2. program has exactly one layer → straight to the form;
  3. otherwise → picker, as today.
- Footer binding stays `l Layer`.

## Testing

- FormModal unit (pilot-light): a commit that refuses keeps the modal
  mounted with input intact; a second save with corrected input dismisses.
  A commit that raises notifies and stays open.
- Layer path pilots: `l` with carriers-table focused opens that layer's form;
  single-layer program skips the picker; a gap-creating limit edit shows the
  refusal in-form and the file on disk is unchanged.
- Existing form tests (test_tui_forms.py) keep passing — the values contract
  is unchanged; only failure-path navigation differs.

## Expiry visibility (added 2026-08-12)

Goal: always know what is coming up for expiration, from any table that
shows policies.

- Account placements table: replace the single "period" column
  (`from → to`) with `effective`, `expires`, and `d` (days until expiry,
  negative when past). Rows sort by soonest expiry first. Rows within 60
  days render the expiry cell with the warning style; past-due with error
  style (Rich markup, consistent with existing table styling).
- Book table and Today renewals already carry expiry + days — unchanged.
- Carriers/participant tables show layer economics, not policies — unchanged.

## Team assignments — evaluation outcome (added 2026-08-12)

The internal-team feature built 2026-08-11 already covers the request:
members (`team_member`: name/title/specialty) with assignments
(`team_assignment`) carrying a `role` from the TEAM_ROLES vocabulary
(account_lead, placement_specialist, claims_advocate, analyst,
coverage_counsel, other), free-text `lines`, and a scope of exactly one of
account OR specific placement. "Placement specialist on certain policies" =
placement-scoped assignment; "advisory for lines of cover" = account-scoped
role + lines. Surfaces: `w` on Today (team screen), `w` on an account
(assign), TEAM pane on the account overview.

One visibility delta ships with this spec: the placements tab currently
doesn't show who is on the deal. Add the placement-scoped team members to
the `sync-state` line under the placements table ("team: Rosa (placement
specialist), Ken (analyst)") for the selected placement, so deal staffing
is visible where the deal lives.

## Out of scope

- The import surfaces (PasteImportModal, ImportScreen) — they already stage
  and gate in place.
- Inline table cell editing; the form modal remains the edit surface.
- Line-level (per coverage line) placement assignments — the placement-level
  scope plus the `lines` text covers today's need; revisit if a real case
  demands per-line granularity.
