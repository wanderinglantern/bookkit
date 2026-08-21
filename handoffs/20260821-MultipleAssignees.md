# More than one assignee on a task — Grant, 2026-08-21

> "It should just be the person or person(s). Need ability to select multiple."

The first half shipped (feat/assignee-display): the row shows the plain name and
the editor keeps the qualified one. **This is the second half, and it is a
schema change**, so it owes the full chain in CLAUDE.md's "a schema change is
not done until an agent can see it".

## Why it is not a small change

A task holds an assignee in THREE columns today — `assignee_kind`,
`assignee_id`, `assignee_name` — and `repo/assignees.py` turns one typed string
into all three. Every consumer assumes exactly one:

- `assignees.name_of` / `label_of` / `_resolved` return a single Candidate.
- `services/export_open_items.owner_of` returns one string for the client's
  Owner column.
- The inline cell is one text input with one datalist.
- MCP reads and writes a single assignee.

Many-to-one means a join table (`task_assignee`), and the three columns become
either a view or dead weight.

## The decisions to take before building

1. **Does the freeform name survive?** Today an unresolvable name is kept as
   `assignee_name` so nothing is lost. With N assignees, is the list
   "resolved people + freeform leftovers", or does multi-select imply everyone
   must resolve? Recommend keeping freeform — a name we cannot resolve is still
   a fact, and refusing it would make the field worse than the one it replaces.
2. **What does the client's Owner column say for three people?** It now names
   the individual (Grant's call the same day). Three names in one cell is
   unreadable, and "3 people" is not an owner. Recommend: name up to two, then
   "and N others" — and note the export column was widened to 22 for one name.
3. **What does an EMPTY list mean?** Today unassigned reads "Us" on the client's
   copy. That should not change.
4. **Ordering.** Is there a primary assignee (the person accountable) or is the
   list flat? A flat list means no surface can answer "whose is this", which is
   the question the Owner column was built to answer in the first place. Ask him
   — this is the one that changes the schema shape.

## The chain it owes

1. Migration adding `task_assignee` — additive, but it MOVES existing rows out
   of the three columns, so the backup/rollback story must be stated and the
   old columns kept until the read path is proven.
2. `repo/assignees.py` — the single seam. Every other module reads through it,
   so a plural API there is most of the work.
3. `services/export_open_items.owner_of` — decision 2 above.
4. The inline cell becomes a multi-value control. Data-entry rules bind: it is
   CONSTRAINED INPUT with a knowable set, so a picker, checked server-side; and
   BLUR COMMITS / ESCAPE DISCARDS still applies.
5. **MCP** — `mcpserver.py` must read AND write the list, or the assistant
   cannot use the field. This is the rule that exists because a field once
   landed on the web and not on MCP.
6. `web/parity.py` if any towerkit-side field is touched (it should not be —
   assignees are bookkit's own).

## Related

- The display/edit split that shipped: `routes/work.py::_task_cell_value`, whose
  `qualified` flag is the seam any plural version has to keep.
- handoffs/20260821-AssigneeDisplayAndMulti.md — the original log of both halves.
