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

**Shape (Grant's call, 2026-08-18 — no schema change).** A task whose
`category` is "Internal" is simply left out of the export. `Task.category` is
already a freeform, vocab-completed string (`repo/vocab.py` completes it from
existing records), so this needs no migration, no new column, no `checkbox`
kind in `forms/spec.py`, and no new form field on either surface. It is one
filter in the composition.

Pin when building:
- **Match rule.** Case-insensitive and trimmed — "internal", "Internal ",
  "INTERNAL" must all count, or the flag silently fails for the person who
  typed it in a hurry. Decide whether anything else counts ("Internal note"?)
  and write the answer down; a prefix match and an equality match behave very
  differently the first time someone types "Internal Review".
- **Category is also the section grouping** on sheet 1 (tasks split by
  category, alphabetical). So the filter removes the whole Internal section,
  not just its rows — which is the intent, but check the empty-section
  handling: sections are "always present, even when empty" per the module
  docstring, and an Internal header with nothing under it would defeat the
  point.
- **The vocabulary should offer it.** "Internal" needs to appear in the
  category suggestions, or nobody discovers the feature exists.
- **Say so on the row.** Both surfaces should show that an Internal task is
  export-excluded. A category that quietly changes what leaves the building is
  exactly the kind of hidden behaviour this project keeps getting bitten by.

**Watch.** The export is composed purely and rendered by towerkit; the filter
belongs in bookkit's composition, not in towerkit's renderer.

**Scope: sheet 1 only** (Grant, 2026-08-18). Open Items is the sheet that
carries org-level tasks, so it is the only one the flag applies to. Leave
Information Requests, Projects and Schedule of Insurance alone — do not
generalise the filter across sheets on the grounds that it would be tidier.

---

## Remove a contact from an account (2026-08-18) — LIVE DATA PROBLEM

**What.** A way to remove a contact from an account, on all three surfaces.

**Why.** Grant, 2026-08-18: the MCP server added a wholesaler as a *client*
contact, and it cannot be fixed from MCP, the TUI, or the web. There is bad
data in the real book right now with no path to correct it on any surface.
This is not a missing convenience — it is a write that has no inverse.

**The plumbing already exists.** `repo/contacts.py:67` `delete()` is a soft
delete (`base.soft_delete`), and `for_org(active_only=True)` already hides a
contact whose `active` flag is 0. Neither is reachable: no MCP tool, no TUI
binding, no web route calls either. So this is wiring, not new machinery.

**Decide first: delete or deactivate — they are different answers.**
- *Soft delete* suits this case: the row should never have existed on this
  account. It stays recoverable via `base.undelete`.
- *Deactivate* suits a contact who left the company: keep them attached to the
  history, drop them out of the working list.
  Both probably want surfacing eventually; do not conflate them behind one
  control, and do not label a delete "remove" on one surface and "delete" on
  another.

**Watch — the reason this is not a one-liner.**
- **Interactions reference contacts** (`interaction_contact`, and
  `interactions.attendees`). Decide what a removed contact does to an
  interaction's attendee list before writing the delete, not after. A soft
  delete that leaves attendees dangling makes the timeline lie.
- **`is_primary`.** Removing the primary contact must promote or clear, never
  leave an account whose primary points at a deleted row.
- **It must be one revertible batch**, like `member_deactivate` — which is the
  precedent worth copying: it refuses while assignments are live, and
  `cascade=True` removes them all in one revertible unit.
- **The MCP tool needs it most**, since MCP is what created the bad row. A
  surface that can add but not remove is how this happened.

**Interim, if the real book needs fixing before this ships.** `./bookctl backup`
first, then call `repo.contacts.delete` directly against the DB. Soft, and
reversible with `base.undelete`. Ask before running it — it touches the real
book.
