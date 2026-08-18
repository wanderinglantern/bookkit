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
