# Forms commit in place + direct layer editing

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

## Out of scope

- The import surfaces (PasteImportModal, ImportScreen) — they already stage
  and gate in place.
- Inline table cell editing; the form modal remains the edit surface.
