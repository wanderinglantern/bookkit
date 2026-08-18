# ROADMAP — agreed but not built

Features Grant has asked for that are not scheduled into a current plan.
Each entry says what it is, why, and what it touches — enough to spec from
cold. Dated on the day he raised it.

---

## Internal-only tasks, excluded from the client export (2026-08-18)

**What.** A flag on a task marking it internal, so it never appears in the
client-facing open items `.xlsx` export.

**Why.** `services/export_open_items.py` is described in its own docstring as
"the client-facing export" — sheet 1 is org-level tasks by category. Today
every open task on the account goes to the client. There is no way to keep a
task on the book that the client should not read (chase our own underwriter,
internal review, a note about the relationship), so the export forces a choice
between tracking the work and not showing it.

**Shape.**
- Additive column on `task`: a boolean `internal_only`, defaulting to false —
  existing tasks stay client-visible, which is the current behaviour.
  Additive-only migration, no rewrite of existing rows.
- NOT a `ListDefinition.WellKnown` list: the global rule covers
  stage/status/type classification fields, and this is a boolean, not a
  vocabulary. `status` stays what it is.
- `models.Task` gains the field; `forms/entities.task_form` gains a checkbox —
  note `forms/spec.py` has no `checkbox` kind today, so either add one (it
  renders on both surfaces at once, which is the point of that module) or
  model it as a two-value select.
- `services/export_open_items.py` filters it out. Check `export_rfi.py` too —
  the same argument applies to an information request the client should not
  see, but Grant asked about tasks; do not widen scope without asking.
- Both surfaces show it: the TUI open-items table and the web Work tab. It must
  be visible on the row, not only in the form — a task that quietly behaves
  differently in the export needs to say so where the user reads it.

**Watch.** The export is composed purely and rendered by towerkit; the filter
belongs in bookkit's composition, not in towerkit's renderer.

**Scope: sheet 1 only** (Grant, 2026-08-18). Open Items is the sheet that
carries org-level tasks, so it is the only one the flag applies to. Leave
Information Requests, Projects and Schedule of Insurance alone — do not
generalise the filter across sheets on the grounds that it would be tidier.
