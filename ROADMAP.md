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

## Remove a contact from an account (2026-08-18) — SHIPPED

Built on branch `contact-remove`, 2026-08-18. Left here as a pointer because
the entry below refers to it; the file is otherwise "agreed but not built".

`services/contacts.py` `remove()` owns the rules — clear `is_primary` FIRST
(nobody is promoted in their place), then soft-delete, all in ONE batch that
`revert_batch` / `u` / `R` put back. Nothing cascades: `interactions.attendees`
is alive-filtered, so the person drops off attendee lists while the
interactions and the `interaction_contact` rows survive for the undelete.
All three surfaces call that one service — MCP `contact_remove`, the account
screen's `D` on the Contacts tab (confirm first), and the Relationship tab's
per-card Remove → confirm step (`GET`, writes nothing) → `POST`.

**Grant's real book is untouched.** Apply it yourself: `./bookctl backup`,
then ask the MCP server to `contact_remove` the wholesaler off that client, or
press `D` on the Contacts tab. Both are revertible.

---

## Deactivating a record, generally (2026-08-18)

**What.** A consistent way to retire a record from the working view without
deleting it — for contacts first, but Grant's point is that it "likely should
exist for records more broadly".

**Why.** Removal and retirement are different facts. A wholesaler filed as a
client contact should be *removed* — it was never true. A contact who left the
company, a lapsed market, a colleague who moved on: those are *retired*. They
stay attached to their history and drop out of the lists you work from. Today
only one of the two has any surface at all, and only for team members.

**The precedent already exists and should be copied, not reinvented.**
`member_deactivate` (see CLAUDE.md): it refuses while assignments are live,
`cascade=True` removes them all in one revertible batch, and it is deliberately
NOT a field edit. Whatever this becomes should feel like that.

**Also already present, unexposed:** `contact.active` — `contacts.for_org`
takes `active_only=True` and defaults to it, so the filtering half is built and
nothing can set the flag.

**Open, needs a decision before it is specced.**
- *Which records?* Contacts, markets, projects, placements, opportunities all
  plausibly want it; each has different downstream references.
- *One mechanism or several?* A shared `active` convention across tables reads
  cleanly, but per CLAUDE.md every stage/status/type classification is supposed
  to be a `ListDefinition.WellKnown` list — so decide early whether "retired" is
  a status value or a separate boolean, because the answer differs per record
  and changing it later is a migration.
- *How does it show?* A retired row must be visibly retired wherever it still
  appears (history, attendee lists), not silently absent.

---

## The revert control tells you which change refs exist book-wide (2026-08-18)

**What.** `POST /accounts/{ref}/changes/{batch_ref}/revert` answers an unknown
`batch_ref` with a redirect and a "gone" toast, and another account's
`batch_ref` with a silent 404 (`web/routes/changes.py:100-111`). Two different
answers to two different misses is exactly the distinction `_owned`'s
`_not_here` erases everywhere else on purpose: from one account's url you can
tell whether a change ref exists somewhere else in the book. Refs are
sequential and guessable (`next_ref(conn, BATCH_REF)`), so the whole space
enumerates.

**Why it is a roadmap line and not a fix.** Exposure here is zero in practice —
loopback-only, single-user, and the thing leaked is "a change with this ref
exists", not its content. It was found while auditing the ownership guard (fix
round 2, 2026-08-18) and deliberately left alone rather than patched in a fix
round that was closing an unrelated defect.

**The wrinkle to decide before building.** The two branches answer in different
SHAPES, and that is the whole difficulty: the unknown-ref branch redirects with
a toast because a stale ref is a stale page and the user should land somewhere
useful, while the foreign-ref branch raises a flat 404. Collapsing them means
choosing which shape both misses get — and the redirect+toast is the better
user experience for the far more common case (a stale tab), so the answer is
probably "both redirect with the same toast", not "both 404". Check what the
changes rail does with the redirect target first.

**Touches.** `web/routes/changes.py` only; `tests/test_web_scoping.py` is where
the assertion belongs (it already owns "an unknown id and someone else's id
answer the same").
