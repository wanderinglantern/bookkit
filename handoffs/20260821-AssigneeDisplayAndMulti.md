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

---

# Queue item: renewal drift — when a program becomes next year's, a policy at a time

Grant, 2026-08-21, flagged for BRAINSTORM (not build):

> "go through the renewal process, when a policy renews within a program and
> starts to become a new program, especially prevalent where a new client has
> multiple renewal dates that are not aligned and slowly the 2025-2026 program
> becomes a 2026-2027 program but not all at once..."

This is the biggest modelling question raised today and it deserves its own
brainstorm before any code. Recording the shape while it is fresh.

## Why the current model strains

`sync.renew` clones a placement wholesale — one program in, one program out,
with `_bump_years` moving the name. That is right for an ALIGNED program, where
every line renews on the same date. It is the wrong shape for the case he
describes, which is the normal case for a newly-won account:

- the program file is the sole authority for structure, and it carries ONE
  `period` plus per-layer `period` overrides;
- `renewals.upcoming` already understands that lines expire apart — as of today
  it emits one row per DATE something runs out (that work is on main);
- but there is no way to say "this program is HALF renewed". A layer renewed
  into next year lives in a file named for last year, or the whole program gets
  cloned early and the un-renewed layers are wrong in the new one.

So the drift is currently modelled by a human's judgement about when to press
Renew, and whichever call they make, some layers are misfiled until the rest
catch up.

## The questions to put to him

1. **Is the 2026-2027 program a NEW FILE, or the same file whose layers move
   over one at a time?** Both are defensible and they lead to different tools.
   A new file per program-year is what the naming convention implies
   (`<slug>-<period year>.json`) and what `_bump_years` assumes.
2. **What does the SOI show mid-drift?** A client schedule during the transition
   has to say what is in force TODAY, which may be four layers from the old
   program and two from the new. That is the output that decides the model.
3. **Is there a point where the old program is "done"?** Or does it simply stop
   having live layers? A status, or an emergent state.
4. **What does Today show?** Presumably each un-renewed line, on its own date —
   which the per-policy renewal rows shipped today already do. That may be most
   of the answer for the attention half, leaving only the FILE half open.

## What is already in place that helps

- per-date renewal rows (shipped 2026-08-21) — the attention surface already
  speaks in policies rather than programs;
- per-layer `period` on towerkit's Layer, so a file can already hold layers on
  different terms;
- `policy_group` (shipped 2026-08-21), which can say two layers are one policy
  — likely useful for saying which layers renew together.

## Recommendation

Brainstorm this one properly with the SOI output as the forcing question — what
the client's schedule must say mid-drift decides the model, and every other
answer follows from it. Do not start with the file layout.

---

# Bug for the queue: an inline cell does not say when it saved

Grant, 2026-08-21:

> "understand that when i click something in line to edit it highlights in
> blue, but unclear when changes are saved as it just stays blue... sometimes i
> need to hit enter, other times not.... need consistency and save by blur"

## This is a rule the project already made, not a new decision

CLAUDE.md, 2026-08-20: **BLUR COMMITS, ESCAPE DISCARDS — everywhere a value is
edited IN PLACE, on both surfaces.** Enter commits and closes, Tab commits and
hops, clicking or tabbing away COMMITS, Escape is the single discard. Two
guards are named as load-bearing: Escape's own close must not then commit what
Escape discarded, and an UNCHANGED value closes WITHOUT writing.

So "sometimes I need to hit enter, other times not" is the rule not holding
uniformly, and "unclear when changes are saved" is a second, separate problem:
even a correct commit is invisible if the cell looks identical afterwards.

## Two things to investigate, and they are not the same bug

1. **Does blur actually commit, on every cell kind?** `web/static/inline-cell.js`
   owns this and `tui/widgets/inline_edit.py` is its twin — CLAUDE.md says they
   must agree. Suspect the SELECT cells first: a `<select>`'s blur/change
   sequence is not a text input's, and the Projects tab, the layer details row
   and the task rows all now carry selects. A select that commits on `change`
   but not on blur, sitting beside inputs that commit on blur, produces exactly
   "sometimes I need to hit enter".
2. **What says it saved?** Today the cell comes back rendered as a display cell
   — which IS the signal, but it is a quiet one and it looks like the blue
   editor if the swap did not happen. The honest reading of his report is that
   he cannot tell a saved cell from an open one. Options, cheapest first: keep
   the editor's blue for FOCUS only and drop it the instant the value is
   written; a brief "saved" flash on the swapped-in cell; or an explicit
   affordance. The data-entry research is against noisy confirmation but
   silence is not the alternative.

## Related, and probably the same root

The research also records that the web's `.cell-error` PERSISTS until the next
POST instead of clearing on keystroke — the mirror-image failure, where a
message survives its own correction. Both are "the cell's visual state does not
track what actually happened". Worth fixing together, in inline-cell.js.

## Do not fix by adding a Save button

FormModal keeps an explicit Save because a multi-field modal blurs every time
you tab between its own fields. A single cell is the case blur-commits was
decided FOR, and reversing it here would split the rule the CLAUDE.md entry
exists to unify.
