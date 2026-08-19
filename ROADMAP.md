# ROADMAP — agreed but not built

Features Grant has asked for that are not scheduled into a current plan.
Each entry says what it is, why, and what it touches — enough to spec from
cold. Dated on the day he raised it.

---

## Internal-only tasks, excluded from the client export (2026-08-18) — SHIPPED

Built on branch `internal-tasks-export`, 2026-08-18. No schema change, no
migration, no new form field: a task whose `category` is "Internal" is left out
of sheet 1 of the client `.xlsx`, and every surface says so.

`models.is_internal_category` owns the rule — EXACT equality on the trimmed,
case-folded value, so "internal", "INTERNAL " and "Internal" all count and
"Internal Review" does not. It lives in models.py because four modules ask the
question and a fifth needs the constant, and because the export service imports
towerkit at module scope. `export_open_items.compose(..., include_internal=)`
is the filter, default off; MCP's `open_items` passes True, since hiding
Grant's own work from Grant is the silent failure the feature exists to
prevent, and flags every row `internal:` in both of its branches.

**The pins, answered.**
- *Match rule:* equality, not prefix. The two rules fail in opposite
  directions and only one failure is visible — under equality a mistyped
  "Internal Review" ships under a section header literally naming it; under a
  prefix rule "Internal audit support", a real client-facing broking task,
  vanishes with nothing anywhere saying so. Same call `parse_human_date` makes
  on a bare number.
- *Empty sections:* compose() never emits one, so an account whose only open
  item is Internal composes to nothing and write() falls back to its "No open
  items as of <date>" placeholder row.
- *The vocabulary offers it:* `repo/vocab.task_categories` always includes
  "Internal", so it is suggested before anybody has typed it once — on the
  add/edit modal AND on the inline cell (`forms.inline.task_fields(conn)`),
  which is the primary edit path on both surfaces.
- *Say so on the row:* `theme.category_text` renders "Internal ⊘ not exported"
  on all four task tables; the web mirrors the wording as a `tag-internal`
  badge inside the category cell's own `<td>`.

**And the near miss speaks.** `wrote <path>` on both export surfaces carries
what was withheld AND what looked withheld and was not: `1 task categorised
"Internal Review" WAS exported (only the exact category "Internal" is
withheld)`. That is this entry's own stated worry — "a prefix match and an
equality match behave very differently the first time someone types 'Internal
Review'" — answered without weakening the rule. An absence teaches nobody who
has never seen the presence, and a near-miss row renders byte-identically to
any other category.

**Scope stayed sheet 1.** Information Requests, Projects and Schedule of
Insurance are untouched, and the filter is in bookkit's composition, never in
towerkit's renderer.

**Grant's real book is untouched.** Apply it yourself: `./bookctl backup`,
then set a task's category to "Internal" (press `i` on the Open Items tab, or
click the cell on the web) and export — the line names what it held back.

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

## towerkit: what a program IS across a renewal boundary (2026-08-18)

**Not a feature — a modelling question that has to be answered before the
features around it are built.** Grant, 2026-08-18: a client with multiple
renewal dates across multiple policies. Technically the program renews from a
2025 program to a 2026 one, but he needs to keep seeing it as **one visual**. He
tried to renew a single policy through MCP; it could not renew one line and
ultimately created a new program instead — the right call in the moment, and the
reason this entry exists.

### What the model already supports

Staggered expiry is not a gap — it is already modelled, at the layer level:

- `towerkit.model.Layer.period` is optional and falls back to the program
  period, so **a layer can expire on its own date**.
- `sync.line_ends` (`sync.py:923`) derives each line's end as the earliest
  expiry among the layers covering it — "policies are issued per layer, and a
  line's cover runs out when the first layer that applies to it expires."
- `services.renewals.RenewalItem` carries `line_ends` per line and counts
  `days_remaining` to `renewal_on` — the earliest live line end, capped by the
  program period. CLAUDE.md makes this load-bearing: an IM layer runs out months
  before its program period does.

So **reading is already line-aware**.

### What it does not support

**Writing is program-atomic.** `sync.renew` (`sync.py:618`) takes a
`placement_id`, clones the whole file forward with `clone_as_renewal`, bumps
both period dates by a year, and refuses if next year's file already exists.
There is no line-level or layer-level renewal anywhere. That asymmetry — read
staggered, write whole — is exactly what MCP hit.

`Line` itself has no period (it is a column, by design); dates live on layers.
Any answer here works on layers, not lines.

### The question underneath, which is not "add a renew_line tool"

**What is a program's identity across time?** Today a program is a file, and a
renewal is a *new* file with a new name. Nothing links the two except a naming
convention (`_bump_years` on the stem) and the placement row. That is fine when
everything renews at once. It breaks the moment a client's cover is
half-2025-and-half-2026, because then:

- neither file is "the program" — the true picture spans both;
- the 2025 file still holds live cover, so it is not history;
- the 2026 file is incomplete, so it is not current.

Whatever gets built has to answer whether the continuity thread is a new
concept (a program lineage/identity that files belong to), a link between
files, or a view composed at read time. **That choice is hard to reverse** —
it decides what a file means, what an export is a snapshot of, and what
`revert_batch` can put back.

### Edge cases worth thinking through before specifying

- A layer renewed early, before the rest — does the program show next year's
  limit today, or hold it until it incepts?
- A line **not** renewed: dropped cover has to read as dropped, not as missing
  data. The two look identical in a diagram unless the model distinguishes them.
- Mid-term endorsements versus renewals — both change a layer, only one starts a
  new period.
- A renewal that changes the tower's shape (a layer splits, carriers change
  share, a line is added) — the "one visual" cannot assume the columns match.
- Different renewal dates that then *converge* onto one date, which is often the
  goal of the exercise and should be visible as progress toward it.
- What the attention window counts to while a program is half-renewed —
  `renewal_on` is the earliest live line end, and a just-renewed layer must not
  make an unrenewed one fall off the list.

### Visualisation — internal and client-facing are different problems

- **Internal:** the working view wants to show one program with per-layer
  periods visible, so it is obvious what is renewed, what is pending, and what
  is about to lapse. Time becomes a dimension of the tower diagram, which it is
  not today.
- **Client-facing:** a client should see the cover they have. A schedule that
  silently mixes two policy years is a document that misleads. Whatever is
  rendered for a client needs an explicit as-of date, and probably a way to say
  "these three lines renew in March".

This lands directly on the conversion Grant approved the same day (R66): HTML
for the interface, SVG for the export. A program spanning a renewal boundary is
precisely the case the two renderers must agree about — so this question should
be settled, or at least bounded, before the export half is built. Otherwise the
first client to get a mid-renewal schedule is the test.

### Suggested next step

A spec, not a task — this needs the design settled on paper before any code.
Start from the real client Grant hit it with; a made-up example will miss the
edge cases that matter.

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

---

## A stale Edit renders as silence, on every cell route (2026-08-18)

**What.** Clicking `Edit` on a row another tab (or the TUI, or MCP) deleted a
moment ago produces NOTHING at all — no form, no message, no change. The route
answers 404 (`web/routes/relationship.py:275,:293`, and the same in the
thirteen pre-existing contact/task/request cell routes), htmx swaps neither 4xx
nor 5xx, and nothing listens for `htmx:responseError`, so the click lands in
the floor.

**Why it is a roadmap line and not a fix.** The DESTRUCTIVE control beside it
already handles this correctly and deliberately: `interaction_delete_confirm`
and `contact_remove_confirm` check ownership against the RAW row and answer
staleness with a 200 carrying "already deleted", because the stale click is
most likely to land on the button that destroys something. Extending that
treatment to the read/edit half is a BRANCH-WIDE sweep across fourteen routes
with one shared answer, not a Task 10 fix — doing it for interactions alone
would leave the timeline behaving differently from the contact cards beside it
on the same tab.

**Shape when built.** One decision to make first: a stale Edit has no panel of
its own to refuse into on some routes (a cell editor's target is the cell), so
either the refusal comes back as the re-rendered CELL saying "gone — refresh",
or every cell route grows the panel-level OOB error that the remove control
uses. Pick one and apply it to all fourteen; two shapes is how this drifted.

**Touches.** `web/routes/relationship.py`, `web/routes/work.py`, the cell
macros in `web/templates/macros/`, and `tests/test_web_concurrency.py` (which
already owns "a stale click says so" for the remove control).

---

## MCP owns a third vocabulary for deleting an interaction (2026-08-18)

**What.** `services/interactions.py` says in its own docstring that the surfaces
share this write; two of them do. `mcpserver._activity_delete`
(`mcpserver.py:855`) still opens its own batch with `tool="activity_delete"`
and `summary=f"deleted activity: {subject}"`, while the service writes
`tool="interaction_delete"` and `deleted <subject> from <org>`. So the changes
rail describes one write two ways depending on who asked, and `R`'s list reads
as two different features. It also raises `KeyError` from `interactions.get`
rather than the service's "already deleted" sentence.

**Why it is a roadmap line and not a fix.** It is the odd one out, not the
pattern — `contacts_svc.remove` IS wired to MCP — but the move is NOT
mechanical: `_activity_delete` calls `_provenance(conn, "interaction", id)`
inside its batch and the service does not. Provenance is an MCP-only concern
(who wrote this row and from what), so either the service grows an optional
hook or MCP keeps a thin wrapper that opens no batch of its own — and it cannot
open one, because `db.transaction` nests by JOINING and an outer batch would
leave a second, permanently empty row in the changes list.

**Watch.** Fix the docstring at the same time; it currently claims three
surfaces read these rules. And check the MCP tool's return shape — it reports
`batch.ref` back to the model, which the service currently does not return.

**Touches.** `mcpserver.py` (`_activity_delete`), `services/interactions.py`,
`tests/test_mcp*.py`.

---

## The SOI schematic worksheet crashes on a normal casualty programme (2026-08-18)

**A crash, in a client-facing deliverable, on the commonest program shape there is.**
Found by a review conducted in the persona of a client-side risk manager/CFO reading the
export, and **reproduced independently** before being recorded here.

```
statutory WC Part A alone .................... OK
Part A + Employers Liability (WC only) ....... OK
Part A + a GL primary (no Part B) ............ OK
Part A + Part B + a GL primary ............... AttributeError:
    'MergedCell' object attribute 'value' is read-only
    towerkit/src/towerkit/render/schematic_xlsx.py:610, in _block
```

So it needs a statutory layer, a second layer on the same line, AND a second line of
cover. That is WC Part A + Part B alongside GL — which is close to every US casualty
programme written.

**Why it happens (diagnosis, not yet a fix).** `_block` computes a cell range and does
`ws.cell(row=r0, column=c0, value=...)`. openpyxl returns a read-only `MergedCell` when
that coordinate is *inside* a merge some earlier block already made rather than being its
anchor. So two blocks' quantized ranges overlap. The statutory layer is drawn off-scale
above `y=1.0` (`TowerLayout.chevrons`), which is the likely source of the collision once a
second line forces a column split.

**Watch when fixing:** the repo rule is that rendered output stays byte-identical across
runs, and there are determinism tests on the statutory path already
(`tests/test_render.py::test_statutory_svg_is_byte_identical_across_runs`). A fix that
changes quantization changes every existing schematic. Prefer detecting the overlap and
refusing loudly over silently shifting a boundary.

---

## The client workbook, reviewed from the client's side (2026-08-18)

A reviewer in the persona of a risk manager / CFO generated real workbooks from seeded
data and read them as the recipient. Verdict: **would not forward to a CFO as-is.** The
full review is in the session record; the load-bearing findings, in their order:

**1. Unbound cover sits on the Schedule of Insurance, unmarked and inside subtotals.**
A submitted-not-bound cyber programme prints with a premium and a carrier of "See policy
documents", and a `To be placed` excess layer's premium is included in the section
subtotal. A schedule of insurance asserts that cover exists; two of four things on the
sample did not. **This is the only finding that can cause a real loss.** Needs a per-row
status (Bound / Quoted / Submitted / Expired / To be placed) and unbound rows excluded
from, or subtotalled separately to, bound ones.

**2. The status marker is applied backwards.** A placement WITHOUT a linked program file
gets a status suffix; one WITH a file gets none. So the more we know about a programme,
the less the client can tell about whether it is real.

**3. Open items do not say whose item it is.** "Return signed TRIA form" (client's) and
"Chase Zurich on the Q2 loss run" (ours) render identically. One column, two values —
Owner: You / Us — was rated worth more than every format fix combined.

**4. Sheets are anonymous and undated.** Tabs 2 and 3 carry no client name and no "as of"
date; printed and passed round a table they are unattributable. Document properties list
the tool version as Author and stamp 1980-01-01.

**5. Nothing anywhere says what renews when.** Named as the first question the document is
opened to answer.

**6. Sections are ordered alphabetically**, so the most overdue item sits below a
certificate that is not due for three days.

Format issues worth doing in one pass: the Premium column is narrower than the values it
holds (`$4,140,000.00` at width 12.16 → `########` on exactly the figures that matter);
three date formats across four sheets, none of them real date values on sheets 1-3; no
print setup at all on a sheet ~22 inches wide; no AutoFilter; gridlines left on; tab names
truncated mid-word.

**On withholding Internal items, from the recipient's side:** withhold silently, do NOT
print a count ("it converts a non-event into a standing question on every export"), but
state the scope once in fixed wording on every export so the omission is a published rule
rather than a discovery. **And the sharp one we had not considered:** with exact matching,
a task typed `Internal Review` still ships AND ships as a section BANNER reading
"Internal…" in the client's copy. A heading that says Internal reads as a leak of our
private list whatever the item says. Either suppress client-visible section labels
beginning with "Internal", or stop using raw category text as the section heading.
They also drew a line we should hold: whoever benefits from an item being hidden must not
also decide, per item, in free text, whether it is hidden — anything that changes what the
client pays, what they are covered for, or that involves a conflict, is disclosable
regardless of label.

**On statutory WC presentation:** `Statutory` alone is correct to a broker and ambiguous to
a CFO — write `Statutory — no policy limit (Part A)`, give Part B its three real limits
(each accident / disease each employee / disease policy limit) instead of one unqualified
figure, write `Included with Part A` rather than `$0.00`, name the states (statutory
benefits in a state we are not filed in are worth nothing), and state the captive
retention once rather than on both WC rows. On the tower: give WC its own column with a
deliberately open top edge, and **do not let the statutory block set the vertical scale** —
running off the top is the honest idiom for unbounded, and capping it flat reads as a limit
whose number was left off. Draw Employers Liability as a discrete block so it is
unmistakable that the umbrella drops over Part B, not over Part A.

---

## The client workbook — Grant's answers to the CFO review (2026-08-18)

Twelve of fourteen answered. **C9 (the "Internal…" section banner leaking into the client
copy) and C14 (what the review got wrong) were left blank — blank means "not now", not
"agreed".** C9 in particular is still open and is the one finding about our own conduct.

| | Decision |
|---|---|
| **C1** SOI row status, unbound out of subtotals | **build next** |
| **C2** status suffix applied backwards | **fix with C1** |
| **C3** expired policy years | **exclude them** |
| **C4** Owner column | **build — and see below, he widened it** |
| **C5** sheet header block | **build** |
| **C6** renewals | **renewal date + a calendar tab** |
| **C7** open-item ordering | **overdue first** |
| **C8** withholding scope line | **adopt** |
| **C10** statutory wording | **adopt all — the phrase is `Statutory - State Limits`** |
| **C11** statutory on the tower | **adopt** |
| **C12** format pass | **do it as one pass** |
| **C13** missing content | **named insured schedule only — optional, capturing FEIN** |

### C4 is not a two-value column any more

His words: *"I think this is helpful for the AE as well to formally assign someone. Perhaps
leveraging a list of individuals or ability to freeform if needed. Frequently the AE (me)
will be working with a host of people in placement and needs to know who to chase for
whatever."*

So the client's **Owner: You / Us** is the *export projection* of a richer internal fact:
a named assignee on a task. That is a different feature from the one the CFO asked for,
and it is the more useful one.

Shape to build to (controller's call, 2026-08-18 — say so if wrong):

- **One additive `assignee` column on task**, a freeform string with `Field.suggestions`
  drawn from team members and the account's contacts — the vocabulary pattern CLAUDE.md
  already prescribes, wired on BOTH halves (autocomplete dropdown and ghost text). Freeform
  matters: underwriters, wholesalers and third parties are exactly who the AE chases and
  none of them are records in the book.
- **The export derives You / Us; it does not store it.** An assignee matching a contact on
  *this* account renders `You`; anything else, including empty, renders `Us` — unassigned
  work is ours until someone says otherwise. Deriving rather than storing is what stops the
  two from disagreeing, and it means the column cannot be wrong for a client who was never
  told about it.
- **Watch the ambiguity**: two people sharing a name is precisely what `repo/team.py`'s
  uniqueness guard exists for, and a contact and a team member sharing one would flip the
  derived side. Decide what a collision renders before building it.
- Additive column, no destructive migration. Snapshot before the migration runs regardless.

### C10 — use Grant's phrase, not the reviewer's

The cell reads **`Statutory - State Limits`**, not the reviewer's proposed
"Statutory — no policy limit (Part A)". His vocabulary wins on his deliverable. The rest of
that package still stands: Part B's three real limits instead of one unqualified figure,
`Included with Part A` rather than `$0.00`, the captive retention stated once rather than on
both WC rows. Whether the *states themselves* are still listed alongside the phrase is not
settled — "State Limits" may be intended to carry that implicitly. Ask before assuming.

Lives in `towerkit/src/towerkit/soi.py:38` (`limits_text`), not in bookkit.

### C13 — named insureds, optional, with FEIN

Only the named-insured/subsidiary schedule was wanted; the renewal calendar arrives via C6
and everything else on that list was declined for now. **FEIN is new data about a legal
entity** — it is a schema question, not a formatting one, and it wants its own small spec
before code: where the entity list lives (a program fact in towerkit, or an org fact in
bookkit), whether FEIN is per named insured or per org, and the backup story for the
migration. FEIN is also mildly sensitive; decide whether it belongs on a client-facing sheet
at all before putting it there.

### Where each of these actually lives

Two repos, and the split is not the obvious one:

- **bookkit** (`services/export_open_items.py`): C3 (filter expired placements), C7 (section
  ordering), C8 (the scope line), and the composition half of C1/C4.
- **towerkit**: `SoiRow` is a frozen dataclass in `soi.py:94` with no status field, so
  **C1 needs a field there and a column in the renderer**; C2 falls out of C1 for free,
  because the `(Bound)` suffix is a label hack applied only in bookkit's
  `_book_data_section` for UNLINKED placements — which is exactly why the marker appears on
  the placements we know less about. C10, C5, C11 and C12 are all towerkit renderer work.

---

## C9 and C10, answered 2026-08-18 — and C10 is bigger than it was asked as

### C9 — suppress the "Internal…" section labels from the client copy

**Grant: suppress them.** A task categorised exactly `Internal` is already withheld. A task typed
`Internal Review` still ships — correctly, by the exact-match rule — but ships under a section
**banner** reading "Internal Review". A heading that says Internal reads as a leak of our private
list whatever the item beneath it says.

Lives in bookkit's composition (`services/export_open_items.py`), NOT in the renderer.

**Decide before building — the rows do not disappear, only their heading does:**
- Where do those rows go? Rolled into the uncategorised/General section, given a neutral heading,
  or kept as a section with no label? `compose()` never emits an empty section and the sheet has
  guards for that; a headerless section may not be expressible without changing them.
- Match rule for the SUPPRESSION should be a prefix (case-insensitive, trimmed), not equality —
  that is the whole point, since equality is already handled by the withholding.
- The operator still learns about it: `withheld_note()` already names the near miss at export time
  ("1 task categorised \"Internal Review\" WAS exported"). C9 changes what the CLIENT sees, not
  what we see. Do not let one swallow the other.

**Sequencing:** blocked behind the in-flight `export-composition` branch (C3/C7/C8), which is
editing the same module. Do it next, not concurrently.

### C10 — "yes, list the states". towerkit has no state data at all.

**Grant: the limits cell reads `Statutory - State Limits`, AND the states are listed.**

Checked before recording this as a formatting job, and it is not one: **`towerkit.model` carries no
state information anywhere.** There is no field on `Layer`, `Line` or `Program` for it. So the
second half of C10 is a data addition, not a rendering change.

Two honest routes, and this is Grant's call:

**(a) Use the existing escape hatch.** `soi.limits_text` (`soi.py:38-40`) returns
`layer.limits_detail` verbatim when set, so a broker can type
`Statutory - State Limits: AL, AZ, CA…` today with no code change at all. Cheap, immediate, and
structureless: it cannot be validated, compared across a renewal, or checked against the
monopolistic states.

**(b) Model it.** An optional `states` list on a statutory layer. This is the honest shape —
the CFO's argument for naming states is that *statutory benefits in a state we are not filed in are
worth nothing*, which makes it a coverage fact rather than a presentation string. It also allows the
one check that actually matters: ND, OH, WA and WY are monopolistic and cannot be covered by a
private policy, so a states list containing one of them is an error worth refusing.

**Cost of (b), stated plainly:** it changes the canonical program file format. towerkit's files are
the source of truth, saves must stay canonical, and a **zero-diff round trip is tested** — so the
field must be additive and optional, and `model._ordered`'s hand-written key order has to learn
about it or the round trip breaks. That is a real but bounded change, and it is the kind that is
much cheaper now than after a hundred files carry prose in `limits_detail` instead.

**Recommendation: (b), minimally** — an optional list, rendered after the phrase, with the
monopolistic-state check as the only rule attached to it. If Grant would rather move now and model
later, (a) works and loses nothing except the check — but the two must not both ship, or the same
fact lives in two places and they will disagree.

**Also still standing from C10's first half** (unchanged by this answer): Part B's three real limits
instead of one unqualified figure, `Included with Part A` rather than `$0.00`, and the captive
retention stated once rather than on both WC rows.

---

## MCP as an LLM capability surface — audit, 2026-08-18

Grant asked whether an assistant can do the full range of real work through MCP, and whether the
surface **grows with the platform without hand-coding every item**. Audit run against a seeded
throwaway DB. **Four load-bearing claims re-verified independently before recording them here.**

**Verdict.** An assistant can run a real slice of the day through this — and the write tools are
unusually well built for LLM use: refusals name candidates, writes never fuzzy-match a ref,
`edit_field` is a compare-and-set, and **all 31 mutating tools are batched and revertible** (the
only two unbatched are the two reverts, deliberately). But **the surface does not grow on its own.**
It is 29 hand-written verb tools plus one generic seam (`edit_field`: 10 kinds, 58 fields) whose
field table is a manual restatement of declarations `forms/entities.py` already owns.

### The finding that proves it is decay, not design

**No read tool on the entire surface returns an opportunity ref.** Verified: `pipeline_status`
returns `stage`/`count`/`avg_days_in_stage` only, and opportunities are not in FTS. The only two
places an `OPP-` ref appears in a return value are `_opportunity_create` (`mcpserver.py:1110`) and
`_opportunity_stage` (`:1609`) — both writes. So `opportunity_stage` and
`edit_field(kind="opportunity")` **can only ever act on a deal the assistant created in the same
session.**

That is precisely the bug `_recent_activity`'s own docstring records as having been found and fixed
for interactions — "a mistake found later was unnameable" — still live one entity over, because
nothing enumerates the surface and asks the question a second time.

### Three more verified

- **`_editable()["org"]` is literally `dict(_ENRICHABLE_ORG)`** (`mcpserver.py:1660`) — the
  *deliberate-overwrite* surface was defined as a copy of the *blank-fill* surface. Consequence: an
  assistant **cannot rename an account, or move a prospect to active or lost.** Nothing records that
  as a decision; it is inheritance.
- **`tests/test_mcpserver.py:891` is named `test_every_write_tool_returns_a_batch_ref` and checks
  TWO tools** (`_log_activity`, `_task_create`). ~10 write tools have no batch-ref assertion
  anywhere. They *are* batched — verified mechanically — but nothing holds them there. A test whose
  name answers an auditor's question wrongly is worse than no test.
- **`list_batches`' docstring is false.** It says "changes THIS server made"; `repo/batches.recent`
  issues `SELECT * FROM event_batch WHERE created_at >= ?` with **no source filter**, so it returns
  TUI and web batches too. The tool is more capable than advertised, so a model will not reach for
  it to answer "what changed on this account this week".

### There is no MCP parity ledger, and that is the real gap

`web/parity.py` fails the suite in **both** directions — an unaccounted TUI action turns it red, and
so does a stale entry. Nothing equivalent guards MCP: the nine registration tests are all *subset*
assertions, so a 43rd tool or a deleted tool changes no assertion. Zero tests assert any tool has a
docstring.

The cost is small — the repo has the pattern twice already. The genuine cost is the argument the
ledger forces: `web/parity.py` can say "1:1 with the TUI" because that destination is obvious.
**MCP's destination is not obvious, and the ledger makes someone decide it.** That is a feature.

### What can and cannot be derived

*Generalises:* field name → kind → cleaner/parser (`forms/spec.CLEANERS` already owns it, and
`test_web_forms_spec.py:118` already forbids MCP keeping a second cleaner map — the same
duplication one layer up); select vocabularies; the write itself (`base.update` is already generic
over 15 entity types with uniform event-logging); "what fields does kind X have".

*Cannot, and should not be forced to:* identity resolution (`_edit_target` is 10 resolvers with 10
ref conventions — one generic rule would make writes fuzzy, the thing this surface is most
carefully not); fields owned by a transition (`opportunity.stage`, `rfi_item.status`,
`team_member.active`, assignment re-scoping — these must stay a **denylist**, not be rediscovered);
money and dates; program writes.

**Shape that falls out: derive the allowlist from `FormSpec`, subtract an explicit denylist, keep
the verbs.** The hand-written verb tools are the *good* part of this design — `opportunity_stage`
refusing with the legal ladder, `member_deactivate` refusing while assignments are live,
`client_create`'s duplicate guard — every one encodes a domain rule a generic `create(kind, fields)`
would erase, and several exist because a specific bug happened. **Generalise the field table; keep
the verbs.**

### Needs Grant before it is built

**Deriving `_EDITABLE` WIDENS the write surface.** `org.name`, `org.status`, `contact.role`,
`task.priority`, `rfi_item.detail` become assistant-editable the moment the derivation lands. That
is the intended outcome and it is a real behaviour change — he should say yes to it explicitly
rather than discover it. The denylist is what makes it safe, and it must be built by **walking every
currently-unreachable field and writing down which are deliberate**, not by starting from empty.

### Ranked, with the safe ones first

1. **Emit opportunity refs from a read tool** — highest severity, lowest cost, no widening.
2. **Fix the four bare-`KeyError` refusals** in `_edit_target`/`task_complete` — six kinds already
   name a recovery path; four fall through to `task TSK-9999 not found`. One function, two standards.
3. **`list_batches`: fix the docstring, add an `account` filter and a `days` parameter.**
4. **`log_activity` should take `type` and `occurred_on`** — today the assistant cannot record
   yesterday's call, and cannot correct it afterwards.
5. **Rename or fix the two-tool "every write tool" test.**
6. **The parity ledger + roster test** (needs the destination decision).
7. **Derive `_EDITABLE`** (needs the widening decision).
8. **`describe(kind)`** — turns four capabilities from undiscoverable into discoverable.
9. **`revert_plan(ref)`** exposing `services.batches.plan_revert`, so a model can show conflicts
   before considering `force=true`. Today force's only guardrail is a sentence in a docstring, while
   the web refused force outright *because* it had no way to show the plan.
10. Then by size: an `interaction` kind in `edit_field`; submissions; `contact_reassign_org`
    (`contacts_repo.reassign_org` exists at `:42` with no door onto it); `renew_program` over
    `sync.renew`; `merge_markets`.

### Keep exactly as it is

The batching spine (one call, one undo unit; the cap enforced under `log_event` so no tool can
forget it; revert refusing all-or-nothing and naming conflicts). The hand-written verb tools and
their refusals. `edit_field`'s compare-and-set with `expecting`, including `expecting=None` meaning
"assert blank". `_EDIT_REDIRECTS`. **Writes never fuzzy-matching a ref while every resolution
refusal names candidates** — the single most important safety property here.

---

## CORRECTION to the C4 assignee design (2026-08-18)

**I wrote a false premise into the C4 entry above and an implementer would have built on it.**

The entry says freeform matters because *"underwriters, wholesalers and third parties are exactly
who the AE chases and none of them are records in the book."* **That is wrong.** Verified:
`repo/contacts.for_org(conn, org_id)` does **not** filter by org kind, and
`tui/screens/markets.py:337` binds `w` → `add_underwriter` with `i` → `import_underwriter`
("paste sig"), and `MarketDetailScreen` lists them. **Underwriters are already records — contacts
on market orgs.** The suggestion list I specified was missing its single most important source.

### The storage type was also wrong, and the reason is a rule this project already holds

Deriving `You / Us` by string-matching a name means typing `Sam` instead of `Sam Garcia` silently
flips a **client-facing** column to say our firm owns what the client owes us. That is the
silent-wrong-direction failure `models.is_internal_category` spends fourteen lines refusing, on a
column the client reads.

**Revised shape:** `assignee_kind` + `assignee_id` when the picker resolved the person, and a
freeform `assignee_name` only when it did not. Typing is unchanged — freeform still works — but the
export reads the **kind**, never a name. **Keep the derive-don't-store call**; that part was right.

Suggestion sources are team members, the account's contacts, **and contacts on market orgs**.

### And the scope was too narrow

Putting an assignee only on `task` leaves the biggest chase list unassigned. The book would then
hold **three non-interoperating mechanisms** for "who owes me this": `rfi_request.market_org_id`
(an org, market-only), `team_assignment` (scoped to account/placement), and a freeform string on
task. **Decide the one question first — "show me everything I am waiting on, and from whom" — and
build backwards from it.**

---

## The work surfaces, reviewed by a senior AE (2026-08-18)

Full review: https://claude.ai/code/artifact/f4a87608-52e6-41a2-9d08-206a3d376988

**Verdict.** Would run the book on the **terminal app** tomorrow — the navigator was called the
best renewal-attention surface they had used, and towerkit's renewal comparison "a two-hour
PowerPoint job done in one keystroke". Would **not** run it on the web, and could not hand the
**assistant** the half of the job it exists for.

### The blocker is a missing middle, not a missing feature

**The tool tracks work SENT and work BOUND, and nothing in between.** `repo/submissions.outstanding()`
filters `status='out'`, so **the moment a market answers, the row leaves the past-SLA queue and
enters no other queue anywhere.** The three weeks of comparing terms, chasing subjectivities and
getting a client decision are invisible on every surface. There is no "quotes in hand", no
"presented", and no quote-expiry field at all — `response_form` captures premium, limit and decline
reason only. **Rated the only gap that loses money rather than time.**

### `submission.underwriter_contact_id` is declared and used by nothing

Verified: it exists at `models.py:366` and `migrations/001_initial.sql:175` and appears **nowhere
else in the codebase**. Today reports six submissions past SLA and names only "Travelers" — which
you cannot email. **The column is already there.**

### Unfindable, not missing — a distinction worth keeping separate

- `program_layers`' description promises participants; `sync.layer_details` (`sync.py:879`) returns
  none.
- Search never renders a contact's org, so five identical "Chen" rows; `fts_contact` does not index
  email.
- **Today's renewals table declares 7 columns and renders 4 at 140 cols, dropping `lines`** — the
  exact field CLAUDE.md calls mandatory context ("program name alone is not enough").
- The Overview tab prints "empty — a adds the first row" over five populated tables, because the
  hint derives from the focused table.
- **42 measured keystrokes** to change one carrier's share in towerkit; `v`, the fast sheet, carries
  no participants.
- Quick capture never writes `interaction_contact` (`quick_capture.py:182`), so the participant
  column the web timeline renders will be permanently blank in real use.

### Web: the dead nav is the worst of it

Six of seven topbar sections are inert `<span>`s in `templates/partials/topbar.html` **with no
title**, while every other unwired control on the same page carries `title="Not wired yet — …"`.
That is CLAUDE.md's own "A REFUSAL SAYS SOMETHING" broken on the most prominent element shipped.
Either wire them, mark them pending like their neighbours, or record "account-only, deliberately"
in `parity.py` as a decision rather than an absence.

### Keep exactly as it is

The 120-day attention model and counting to the earliest line end. towerkit's chart and comparison,
unchanged. Unplaced as a legitimate state — hatched, never an error, never blocking a save. Hit
rate as quoted÷decided with `n` printed and `None` rather than 0%. The date parser refusing a bare
number. Every hint line and the `?` screen. **The MCP tool descriptions — long and opinionated;
resist shortening them.** The follow-up-task *offer*.

### Build next, their ranking

1. The in-flight placement view (closes the verdict gap)
2. A person on every chaseable thing, asked as **one** question
3. Post-bind through issued — binder date, `issued` status, policy number. *"This is where E&O
   comes from."*
4. A stewardship composition — every input already exists; assembly, not modelling
5. Finish the web's escalation half, or record the decision
6. Claims as a first-class entity

Conventions rather than defects, flagged as such by the reviewer: quote expiries and subjectivities,
marketing a layer to several carriers at indicative shares before binding, chasing issuance for
months post-bind, stewardship as an annual deliverable.

---

## C15 answered: model the states (2026-08-18)

**Grant: model it — an optional states list.** Not free text in `limits_detail`.

So the Schedule of Insurance's statutory row reads **`Statutory - State Limits`** (his phrase) and
the states are carried as **data**, not prose.

### What this touches, in order

1. **`towerkit.model`** — an optional `states` field on a layer. Additive and optional, because
   the canonical file format is the source of truth and **a zero-diff round trip is tested**.
   `model._ordered`'s hand-written key order has to learn about it or that test breaks — that is
   the one thing that will bite silently.
2. **`towerkit.validate`** — the check that makes modelling worth it over free text: **ND, OH, WA
   and WY are monopolistic** and cannot be covered by a private policy, so a statutory layer
   naming one is an error. Refuse it the way the validator already refuses `statutory-line-shared`.
3. **`towerkit.soi.limits_text`** (`soi.py:38`) — render the phrase plus the states. Note the
   existing first line, `if layer.limits_detail: return layer.limits_detail`, short-circuits
   everything: decide what happens when a file carries BOTH `limits_detail` prose and a `states`
   list, or the two will disagree and the prose will silently win.
4. **bookkit** — nothing, unless the states should reach `sync.layer_details`. They are a rendering
   fact for the SOI, not a tower-geometry fact, so probably not. Check before adding a key.

### Watch

- **Only statutory layers should carry states.** A states list on a dollar-limited layer is
  meaningless and the validator should say so, or the field becomes a general-purpose note by
  accident.
- **This is a file-format change**, so it wants the same care as any migration: a program written
  by the new code must still load in an older checkout, which additive-and-optional gives for free,
  and the round-trip test is the proof.
- The rest of C10's first half is unchanged and still unbuilt: Part B's three real limits instead
  of one unqualified figure, `Included with Part A` rather than `$0.00`, and the captive retention
  stated once rather than on both WC rows.
