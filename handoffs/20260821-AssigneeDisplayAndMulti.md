# Open items: the assignee cell — Grant, 2026-08-21

Logged, not built. Two separate asks that arrived together; the first is small
and the second is a schema change.

## 1. Drop the " — our team" suffix from the DISPLAY

**What he said:** "the assignee field on open items — i do not like the
' -- our team' suffix that is added. It should just be the person or
person(s)."

**Where it comes from.** `repo/assignees.py` offers every candidate QUALIFIED
(`Candidate.label` → `"Sam Garcia — our team"`, `"Dana Ruiz — Atomic
Industries"`). That qualification is load-bearing at PICK time and must stay:
two people called Sam Garcia resolve to neither without it, and the module's
docstring argues at length that a collision must degrade to "ours" rather than
to "yours", because telling a client they owe us something we own is a false
demand on a document they read.

The bug is that ONE function serves TWO jobs. `assignees.label_of` is
documented as "the QUALIFIED label, **for prefilling an editor**" — and the
round-trip rule is real: a form that pre-fills a value its own resolver will
not accept back unchanged silently downgrades a resolved assignee to freeform
on the next save (the same rule as ENTRY ACCEPTS CENTS). But
`routes/work.py::_task_cell_value` returns `label_of` for the DISPLAY too, so
the qualifier — which exists to disambiguate a choice — is printed as though it
were part of the person's name.

**The shape of the fix.** Split the two jobs, do not weaken either:

- `label_of` stays exactly as it is, and stays what the EDITOR pre-fills.
- add `display_of` (plain name; `task.assignee_name` when unresolved) and use
  it for the cell's rendered value only.
- `_task_cell_value` currently feeds both paths — see `_task_display_cell` vs
  `_task_editor_cell` in `routes/work.py` — so the split happens there, not in
  the repo.

**Do not** strip the qualifier by string-munging on `" — "`: a person's name
can contain an em dash, and the separator is `_QUALIFIER_SEP` in
repo/assignees.py, not a literal anywhere else.

**Callers to check, all four:** `routes/work.py:150` (web cell),
`tui/screens/account.py:1418`, `tui/screens/navigator.py:1127`, and
`forms/entities.py:226` (the whole-record form's `initial` — this one KEEPS the
qualified label).

**Test it can fail:** two candidates with the same name, one on our team and
one at a market. The display must show the bare name; the editor must still
pre-fill the qualified one; saving the pre-filled value unchanged must leave
`assignee_kind`/`assignee_id` untouched.

## 2. Multiple assignees — bigger, and it is a schema change

**What he said:** "Need ability to select multiple."

`task` carries ONE assignee by construction: `assignee_kind` + `assignee_id`,
or `assignee_name` when unresolved, with "exactly one of (kind + id) or name is
ever set" enforced in `repo/assignees.py` (models.py:220 says so). Several
people means a join table (`task_assignee`), not a wider column, and it touches:

- the model and a migration (additive; backfill the existing single value);
- `repo/assignees.py`'s resolver and `set_on_task`;
- the inline cell — one text field with completion becomes a multi-select, and
  per the data-entry rules that control must still offer only what is storable
  AND be checkable server-side;
- `services/export_open_items.py`, which decides whose row it is on the CLIENT
  workbook from the single assignee — "everyone else is ours" (line 137). With
  several assignees, a row split between us and them needs a rule, and that
  rule is Grant's to make, not mine. **This is the open question to put to him
  before building anything.**
- the TUI's own assignee cell, which is the same field.

**Recommendation:** ship (1) on its own — it is a display split with no data
implication — and spec (2) separately, leading with the export question.

## Related

- `.claude/skills/data-entry-integrity/SKILL.md` governs the picker in (2):
  constrained input, a blank option, checked server-side.
- CLAUDE.md's assignee notes and `repo/assignees.py`'s module docstring carry
  the collision reasoning that must survive both changes.
