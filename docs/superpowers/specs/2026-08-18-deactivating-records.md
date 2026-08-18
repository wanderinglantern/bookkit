<!-- ROUND 2 DRAFT. Research verified; DESIGN NOT APPROVED. Read the bottom before building. -->

> **Status: research verified — design NOT approved** (2026-08-18, round 2 of 2).
>
> Drafted against the code, rejected by an adversarial pass, revised, and rejected again.
> **41 citations re-checked · 4 still failed ·
> 8 claims challenged · 5 regressions ·
> 7 decisions reversed from round 1.**
>
> **Iteration was stopped here deliberately, and that is a ruling, not an omission.** Round 2 fixed
> most of what round 1 got wrong and then committed the same defect class again — in one case, in
> the fixes themselves. These documents specify code that does not exist yet, and every added
> specificity is a fresh opportunity for a confident false claim. The build's own record is that
> the runtime reasoning holds and the speculative citations do not.
>
> **So use this for its RESEARCH, not its conclusions.** The verified findings about how the code
> actually behaves are the valuable part and were reproduced by two independent passes. Re-decide
> the design at build time, against the real code, and treat every design decision below as a
> starting proposal carrying a named cost — not a settled call.
>
> Kind: `spec`.

---


# Retiring a record — design

Date: 2026-08-18 (revised after adversarial verification)
Status: **draft, needs Grant on two items** (D10 and the first-slice scope).
Nothing here is implemented. It answers the three open questions in
`ROADMAP.md:97-106` ("Deactivating a record, generally") and is bound by
`CLAUDE.md` and by the shipped removal design in `services/contacts.py:1-28`.
Where this and either of those disagree unintentionally, they win.

**Three decisions changed in revision.** D2 keeps its answer but its stated
"decisive fact" was false and is withdrawn. D4's `is_primary` reasoning was
refuted by the line it cited, and the resulting behaviour changed. D8's key
changed from `R` to `T`. Each is marked ⟲ below.

---

## What the code actually says

The ROADMAP entry is right in the main and wrong in three places worth naming
before anything is built on it.

**Right, verified.** `contact.active` exists (`migrations/001_initial.sql:73`,
`models.py:167`), `contacts.for_org` takes `active_only` and defaults it to
`True` (`repo/contacts.py:24-27`), and **nothing anywhere writes the flag**.
`contact_form` does not declare it (`forms/entities.py:136-152`), MCP's
`edit_field` does not list it (`mcpserver.py:1651-1654`, and
`_ENRICHABLE_CONTACT` at `:91-94`), and every other reference is a read passing
`active_only=False`. A grep for writes of `active` across `src/` finds exactly
two, both on `team_member` (`mcpserver.py:1551`, `:1569`). The filtering half is
built; for contacts the flag is unreachable.

**Wrong 1 — the precedent is MCP-only.** `member_deactivate` is
`mcpserver._member_deactivate` (`mcpserver.py:1521-1555`), `member_reactivate`
is `mcpserver._member_reactivate` (`:1558-1572`). Neither is in `services/` or
`repo/`, and there is no deactivate/reactivate control anywhere in `tui/`,
`web/` or `forms/`. Copying "the precedent" literally means writing the new
rules in `mcpserver.py`, where the TUI and the web cannot reach them — the
mistake `services/contacts.py:13-16` was written the same day to avoid, and the
one `repo/team.py:12-24` records having already been paid for once. **Copy the
shape, not the location.**

**Wrong 2 — it is not five candidate records, it is two.** Three of the five
already retire, through a lifecycle field the pipeline and the attention
windows already read:

| record | already retires via | evidence |
|---|---|---|
| placement | `status` includes `'lapsed'`, CHECK-enforced | `migrations/001_initial.sql:110-111` |
| opportunity | `stage` `'won'`/`'lost'`, CHECK-enforced, plus `closed_at`/`outcome` | `migrations/001_initial.sql:151-153`; `closed_at` `:160`, `outcome` `:161` |
| project | `status` `'completed'`/`'cancelled'` — **comment only, no CHECK**; enforced in Python | `migrations/006_projects.sql:14`; `models.py:243` |
| market (org) | `status` `'dormant'`, CHECK-enforced | `migrations/001_initial.sql:18-19` |
| contact | `active` column, no writer | `migrations/001_initial.sql:73` |
| team member | `active` column, MCP-only writer | `migrations/005_team.sql:13` |

Only the two **person** records lack the concept. The market is a different
problem again — see D1.

**Wrong 3 — the precedent already fails the third question.** The entry ends
with "a retired row must be visibly retired wherever it still appears, not
silently absent." The shipped `member_deactivate` produces exactly that
silence: `tui/screens/team.py:130` calls `team.list_members(conn)` with
`active_only` defaulting to `True` (`repo/team.py:45-48`), so a retired
colleague **disappears from the only screen that lists colleagues**, with no
toggle, no marker, no count, and no way back — `member_reactivate` exists on
MCP alone. `services/team.py:27` drops them from the specialist search too. And
`repo/team.py:28-29` refuses a duplicate name with "rename or deactivate them
first", naming a remedy no TUI user can perform. Copying the precedent
uncritically copies all of that.

## Remove versus retire, in one sentence

**Remove says it was never true. Retire says it was true and no longer is.**

The wholesaler filed as a client contact should be *removed* — that row should
never have existed, so the answer is the soft delete, and
`services.contacts.remove` (`services/contacts.py:158-209`) already ships it on
all three surfaces. The contact who left the company should be *retired* — they
stay on every meeting they attended and drop off the list you work from.

The schema already carries the two as independent axes, and they behave
differently in code today:

- `deleted_at` → removal. `contacts.for_org` applies `base.alive()`
  unconditionally (`repo/contacts.py:25`), so a removed contact is invisible
  even at `active_only=False`. `interactions.attendees` filters on
  `base.alive('c')` (`repo/interactions.py:74-84`, the WHERE at `:79`), so they
  drop off attendee lists.
- `active` → retirement. A retired contact is still returned by
  `for_org(active_only=False)` and **still appears on attendee lists**, because
  `attendees()` does not look at `active` at all. That is not an oversight to
  fix; it is the behaviour retirement wants, for free.

One consequence, already load-bearing: `imports.matcher.match_contact` reads
with `active_only=False` (`imports/matcher.py:36-41`, the call at `:38`), so a
paste-import naming a retired person **matches them** instead of creating a
duplicate. A removed person is not matched and would be recreated. The two axes
are already telling the importer different, correct things.

---

## Decisions

### D1 — Which records get this

**Contacts and team members get a retirement mechanism. Markets get a filter,
not a mechanism. Placements, opportunities and projects get nothing.**

Contacts and team members are the only records whose tables already carry
`active`, and the only ones with no lifecycle field for retirement to live
inside: `Contact` carries `role` and `title` (`models.py:155-174`),
`TeamMember` carries `title`/`specialty` (`models.py:354-365`). Neither carries
a state a record moves through.

Markets are the interesting case and the reason not to generalise by reflex.
`org.status` already accepts `'dormant'` (`migrations/001_initial.sql:18-19`),
`org_form` already offers it as a select (`forms/entities.py:50` for the tuple,
`:82` for the field), and the markets screen's `e` opens that form. **A broker
can mark a market dormant today and absolutely nothing happens.** The missing
half for markets is a filter — see step 5 of the build order, where the call
sites are enumerated honestly.

Placements, opportunities and projects are refused outright. Each already
encodes "this is over" in a field the pipeline, the attention windows and the
book all read. A second boolean saying the same thing would let a placement be
`status='bound'` and `active=0` at once, and no reader would know which wins.

**Rejected: one shared `active` convention across every entity table.** It
reads tidier and is what the ROADMAP entry gravitates toward. It is wrong on
the evidence above: it adds a contradictory second lifecycle to three tables
that have one, and adds nothing to the market case, which is already flagged
and merely unfiltered.

**Cost if wrong.** If Grant later wants placements retired independently of
`status`, that is one additive column on one table (`ALTER TABLE … ADD COLUMN`,
which this repo has done five times — `migrations/002`, `007`, `008`, `009`,
`011`) and this document's mechanism transfers unchanged. Adding `active` to
all five now costs five dead columns and a "which field wins" question at every
read.

### ⟲ D2 — Boolean `active`, not a status value

**Retirement stays a boolean `active` column on `contact` and `team_member`,
both of which already have it. No new column, no new vocabulary, no
migration.**

**The previous draft's stated decisive reason was false and is withdrawn.** It
claimed a status field forces a `CHECK`-constraint change and therefore a
SQLite table rebuild, which would reassign rowids and corrupt the
external-content FTS5 indexes on `org` and `contact`
(`migrations/001_initial.sql:272-312`, and the schema's own warning at
`:275-276`). No alternative on the table requires that. Putting retirement in a
contact *status* means a **new** column on `contact` —
`ALTER TABLE contact ADD COLUMN status TEXT NOT NULL DEFAULT 'current' CHECK
(status IN ('current','retired'))` — which SQLite 3.45.3 accepts, does not
rebuild, does not touch rowids, does not fire the FTS triggers, and enforces
the CHECK on subsequent updates (verified directly against a scratch in-memory
database). The org case the argument actually described (`'dormant'`) needs no
new value at all, because D1 already ruled markets need a filter, not a
vocabulary. The reasoning below is what the decision now rests on.

**1. "Retired" is a status VALUE wherever a status exists, and neither of these
two records has one.** Run the mutual-exclusion test on every lifecycle field
in the book: a placement cannot be `'bound'` and `'lapsed'`
(`001_initial.sql:110-111`); an opportunity cannot be `'quoted'` and `'lost'`
(`:151-153`); a project cannot be `'active'` and `'cancelled'`
(`006_projects.sql:14`); a market cannot be `'active'` and `'dormant'`
(`:18-19`). Retirement is the terminal value of each. Now look at the two
records that need a mechanism: a contact carries `role` and `title`, a team
member carries `title` and `specialty` — descriptions of what a person *does*,
not states they pass through, and the team member's role lives on the
assignment (`migrations/005_team.sql:25`), not the person. A contact `status`
column would be a two-valued enum invented solely to host retirement. That is a
boolean with a vocabulary tax.

The finding is not a coincidence to argue around: **every record with a status
already retires inside it, and the only records needing a mechanism are exactly
the ones with no status.** That symmetry is the whole answer to the ROADMAP's
"one mechanism or several".

**2. How this repo actually stores status, checked.** Raw `TEXT` columns, with
a CHECK where 001 wrote one (`org.status`, `placement.status`,
`opportunity.stage`, `opportunity.outcome`) and a Python tuple where it did not
(`models.PROJECT_STATUSES:243`, `RFI_ITEM_STATUSES:280`). Pickers read
module-level tuples in `forms/entities.py:50-59`. Colour comes from
`theme.STATUS_STYLES` keyed on the raw value (`tui/theme.py:53-89`) and is
applied by `theme.status_text` (`:91`). That is what `CLAUDE.md:129-130`
codifies: "controlled-but-extensible tuples in models.py (TEAM_ROLES pattern)".

**3. The global `ListDefinition.WellKnown` rule is real, and does not reach
here.** The user's global instruction requires stage/status/type classification
fields to be backed by a `ListDefinition.WellKnown` list, rendered through
`ListValuePicker` and `StatusPill(text:, tint:)`. Those are SwiftUI types;
nothing named `ListDefinition`, `WellKnown`, `ListValuePicker` or `StatusPill`
exists anywhere in `src/` (grep across `src/` and `tests/` returns zero hits),
and this repo ruled it inapplicable in writing at
`docs/superpowers/specs/2026-08-13-rfi-tracking-design.md:110-115`. Two things
follow, and only the second is an argument for the boolean. First, if we DID
choose a status field, satisfying the rule would mean building the
ListDefinition storage, the user-editable label/colour table and the picker —
a feature, not a fix. Second and decisively: the rule governs **classification
fields**, and the case above is that retirement is not a classification for
these two records. The rule is not being dodged; its precondition is absent.
The one part of it this design does honour is the storage instruction — the
values live in a raw column and are queryable, which is what `active` already
is.

**4. It reverts through the existing machinery with zero new code.**
`base.log_event` stringifies both values (`repo/base.py:120-121`);
`services.batches._cell` stringifies the current cell before comparing
(`services/batches.py:188-190`, used at `:253-255`), so `"0" == "0"` compares
clean; `revert` writes the old value back through `base.update`
(`services/batches.py:369-373`); SQLite INTEGER affinity converts the stored
`"0"`/`"1"` back to an integer on write (verified: `typeof` reads `integer`).
And because `active` is a real column, `base.log_event`'s guard passes with no
`events.NON_MUTATION_FIELDS` declaration (`repo/base.py:98-113`,
`_assert_known_field` at `:78-95`) — the landmine class `CLAUDE.md:51-57`
describes does not apply. This is the same round-trip `is_primary` already
takes on every removal, so it is proven in production code, not just in theory.

**Rejected: a `contact.status` / `team_member.status` TEXT column.** Cheap to
add (verified above) and superficially consistent with the rest of the book.
Rejected because it invents a two-valued vocabulary for records with no
lifecycle, and because it would sit beside the `active` column that already
exists and is already read by `for_org(active_only=True)` and
`list_members(active_only=True)` — leaving two lifecycle fields on the exact
tables this document is trying to give one. Removing `active` afterwards is a
`DROP COLUMN` plus a rewrite of both repo filters and `member_deactivate`, for
a naming preference.

**Rejected: a `retired_at TEXT` timestamp instead of a boolean.** It carries
"when" for free and reads like `deleted_at`. Rejected because a second
timestamp beside `deleted_at` invites exactly the confusion between the two
axes this document is drawing a line through, and "when" is already recoverable
from `event_log`.

**Cost if wrong.** If Grant later wants retirement *reasons* — left the
company / no longer our contact / moved accounts — the fix is one additive
`retired_reason TEXT` column beside `active`, vocabulary-completed via
`Field.suggestions` (`CLAUDE.md:93-96`) the way `task.category` is. If he wants
a full contact lifecycle vocabulary, that is the `ADD COLUMN status` above:
additive, non-destructive, no FTS rebuild, and cheap — the migration cost is
**not** what makes the boolean right, and pretending it was made this decision
look more forced than it is.

### D3 — Where the rules live

**`services/contacts.py` gains `retire()` / `restore()` beside `remove()`.
`services/team.py` gains `retire_member()` / `restore_member()`, lifted out of
`mcpserver._member_deactivate` / `_member_reactivate`.**

The module docstring at `services/contacts.py:1-28` already contrasts the two
verbs and points at this ROADMAP entry; retirement belongs in that file, not a
new one. The service opens its own batch, for the two reasons written down
there (`:18-27`): the write must be one undo unit whatever surface asked, and
`db.transaction` nests by joining, so a surface wrapping it would leave a
second, permanently empty batch row in the changes list.

Lifting the team half is what makes this a mechanism rather than a
contact-shaped one-off, and it is what fixes Wrong 1 and Wrong 3: once the
rules are in `services/team.py`, a TUI control on the team screen inherits them
instead of forking them, and MCP keeps a thin resolve-and-report wrapper —
exactly what `_contact_remove` already is (`mcpserver.py:1035-1057`; its
docstring at `:1038-1040` states the split, the resolve at `:1044-1052`, the
service call at `:1053`). *(The previous draft cited `mcpserver.py:1534-1556`
for this shape; that range is inside `_member_deactivate`, the function this
document argues must not be copied verbatim.)*

**Rejected: a shared `services/lifecycle.py` with `retire(table, id)`.** The
two records differ in the only part that matters — nothing blocks retiring a
contact, while retiring a colleague is refused while assignments are live
(`mcpserver.py:1534-1540`) and optionally cascades over them (`:1548-1550`). A
generic helper would take a policy callback for the block and another for the
cascade: more machinery than the thirty lines it saves, and `CLAUDE.md`'s
convention is a service per record.

**Cost if wrong.** If a third record wants this later and the two
implementations have drifted, extracting the common half then is a refactor
with two green suites pinning the behaviour. Extracting it now is a guess at
what the third record needs.

### ⟲ D4 — What retiring actually does

`retire(conn, contact_id, *, source)` → `Retirement`, mirroring `Removal`
(`services/contacts.py:62-91`):

1. Read through `base.raw_row` (dead-or-alive, `repo/base.py:42-59`), so
   "already removed" and "already retired" are distinguishable refusals rather
   than "not found".
2. Refuse by name if already retired, in one sentence, exported as a read-only
   `already_retired()` the way `already_removed()` is
   (`services/contacts.py:137-155`) — the web's confirm step is a `GET` and
   cannot find out by attempting the write.
3. Set `active = 0` through `contacts_repo.update`, so the change is
   event-logged and revertible.
4. **Clear `is_primary`, inside the same batch, promoting nobody** — on display
   grounds, not invariant grounds. See the correction below.
5. Nothing cascades. `attendees()` never looked at `active`
   (`repo/interactions.py:79`), so the person stays on every interaction they
   attended with no rows touched.

**⟲ The `is_primary` argument, re-derived.** The previous draft said leaving
`is_primary=1` on a retired row "quietly falsifies `set_primary`'s exactly-one
invariant (`repo/contacts.py:58-64`)". The cited line refutes it:
`set_primary` iterates `for_org(conn, contact.org_id, active_only=False)`
(`repo/contacts.py:61`), so a retired row is still returned and its flag IS
cleared (`:62-63`). Two primaries are impossible. The removal case is different
for a reason that does not transfer — a soft-deleted row is filtered by
`base.alive()` unconditionally (`repo/contacts.py:25`) and `base.update` is
alive-gated through `base.get` (`repo/base.py:148-153`, `:164-167`), so it is
genuinely unreachable, which is what `services/contacts.py:199-202` says. A
retired row is alive and fully reachable.

What actually goes wrong is display, and it is worth fixing anyway. Every
reader of `is_primary` reads it off an `active_only=True` list:
`tui/screens/account.py:630` → `:637`, `tui/screens/navigator.py:770` → `:774`,
`web/routes/relationship.py:121` → `:111` → `_contacts_panel.html:55,57`, and
`services/onboarding.py:65` → `:70`. So a retired primary produces an account
that reads "no primary" while the flag sits on a hidden row. And the moment
D7's toggle renders retired rows, that hidden ★ appears beside the live one —
two stars in one list, in three separate renderers. Fixing it in the renderers
means remembering it three times; fixing it in the service means once. The
guard belongs where `repo/team.py:12-24` says guards belong.

**⟲ `restore()` returns the star when the slot is vacant.** This reverses the
previous draft. `restore()` sets `active = 1`, then — in the same batch — sets
`is_primary = 1` **only if no other active contact on that org holds it**, and
says which happened. Retiring and restoring the same person should be lossless
in the common case; the previous "press `p`" answer made it silently lossy,
and the batch-revert escape hatch is not a real one for a months-old batch
(`u` only reaches the most recent TUI batch — `services/undo.py:44-51`,
`SOURCE = "tui"` at `:32` — and `list_batches` shows 20).

- Rejected: **leave `is_primary` alone on retire.** Simplest, breaks no
  invariant, and makes restore trivially lossless. Rejected for the two-star
  problem above: the toggle would render a retired ★ beside the live one in
  three renderers, and suppressing it there is the guard-in-the-caller pattern
  this codebase has already paid for twice.
- Rejected: **clear on retire, never restore.** Consistent with removal's
  "promoting someone is a judgment" (`services/contacts.py:112-113`), but
  returning a flag to the person it was taken from is restoration, not
  judgment, and the vacancy check makes two primaries impossible.

**Cost if wrong.** If the vacancy rule is wrong, a user retires and restores
their primary and gets the star back when they wanted to re-choose: one press
of `p`. If the clear is wrong, they press `p` once after a restore that found
the slot filled. Both trivial; both must be *said*, in `retire_consequences()`
and in the restore message, not only here. One real wrinkle to state in the
consequences text: after retire → restore, pressing `R` on the original retire
batch now conflicts on `is_primary` (`services/batches.py:253-255`) and refuses
with the current value. That is the house rule working — surface, don't guess —
but it must be written where the user reads it, the way the removal design
already qualifies its own undo promise (`services/contacts.py:118-133`).

Refusals both ways say what to do instead, per `CLAUDE.md:100-105`.

### D5 — One writer action is one undo unit; the cap is not at risk here

**Yes, retirement is one batch.** `services.batches.open_batch(source=…)`
(`services/batches.py:92-100`), `tool="contact_retire"`, summary per D10. `u`,
`R` and the web changes rail all put it back through machinery that exists
(`services/undo.py:44-67`, `services/batches.py:316-386`).

**`db.BLAST_CAP` is nowhere near it.** Retiring a contact touches **one**
entity — `is_primary` and `active` on the same id — and `BatchState.touch`
counts distinct entities (`db.py:54-66`). The cap is 250 (`db.py:27-29`).

The cap does matter for the team cascade **that already ships**:
`_member_deactivate(cascade=True)` touches `1 + N` entities, one per assignment
(`mcpserver.py:1548-1553`, assignments from `team.for_member` at
`repo/team.py:134-146`). At `N ≥ 250` `touch` raises `BlastRadiusExceeded` with
"this action would touch more than 250 records; **narrow it and try again**"
(`db.py:62-65`) — advice the caller cannot follow, because a cascade has no
narrowing knob. When the team half moves into `services/team.py`, give it a
pre-check that counts `team.for_member` first and refuses with the real move:
"unassign some of them first, or retire without cascade and unassign by hand."

**Rejected: raise `BLAST_CAP` for cascades.** It is Grant's call at 250 after
two revisions (`db.py:28-29`) and it is enforced under `log_event` precisely so
no tool can opt out (`repo/base.py:111-113`, `CLAUDE.md:35-40`). The fix is a
better refusal, not a higher ceiling.

**Cost if wrong.** Skipping the pre-check costs a rollback plus a misleading
sentence, at a scale nobody in this book will reach. Low. Worth one guard, not
a redesign.

### ⟲ D6 — How a retired row shows

The rule: **filtered out of the lists you pick and work from; kept and marked
everywhere it is history or identity; never silently absent from a list that
claims to be complete.**

**Filtered out** — `active_only=True` is already the default at all of these,
so they need no change beyond the count line in D7:

- TUI account Contacts tab and Overview — `tui/screens/account.py:630-641`
- TUI navigator contacts group and the tree count —
  `tui/screens/navigator.py:770`, `:423`; the glance card at `:858`
- TUI market detail contacts — `tui/screens/markets.py:587`
- web Relationship "People" panel — `web/routes/relationship.py:121`
- web tab count badge — `web/routes/account.py:310`, summed at `:318`
- onboarding coverage — `services/onboarding.py:65` (a retired contact must not
  count as the account being reachable)

**Kept, and must be marked.** These are the surfaces where a retired person
still appears and would appear as though nothing had changed:

- **Attendee lists.** `interactions.attendees` filters on `base.alive('c')`
  only (`repo/interactions.py:79`). Rendered at `tui/screens/account.py:650`
  and `web/routes/relationship.py:190`. Render as `Name (retired)`, styled
  `theme.DIM`.
- **Global search.** `repo/search.py:52` filters on `c.deleted_at IS NULL`
  only, so a retired contact still hits. Correct — they are still in the record
  — but the marker must be built into `SearchHit.title` where the title is
  built (`repo/search.py:58-61`).
- **MCP.** ⟲ The previous draft named `mcpserver.py:1007`, `:1047`, `:1756`,
  `:1894`. All four resolve, but all four are name-resolution paths inside
  **write** tools (`_contact_add`'s duplicate guard, `_contact_remove`'s target
  lookup, `_edit_target`, `enrich_field`); three expose names only inside a
  refusal string, and none returns a contact record in a success payload. There
  is no MCP tool that lists an account's contacts at all — the full tool set is
  `mcpserver.py:119-608`. The named harm ("the assistant confidently emails
  someone who left") reaches the model through exactly one door: `search`
  (`mcpserver.py:136` → `_search` at `:698-704`, which returns
  `{kind, title, snippet}`). So the search-title marker above **is** the MCP
  fix; there is nothing separate to do at those four sites beyond D9's refusal
  branch. The shape to generalise from is `team_roster`, which already reports
  `"active": member.active` (`mcpserver.py:1392`) off an `active_only=False`
  read (`:1380`) — any future MCP read that returns a person carries the flag.

**The marker is a word, not only a colour** (`CLAUDE.md:97-99`). The TUI
contacts tables already have a leading glyph column carrying `★` for primary
(`tui/screens/account.py:637`, `tui/screens/navigator.py:774`); a retired row,
when shown, takes that column with a dim `·` **and** renders the name dimmed
with a trailing `(retired)`. On the web, `_contacts_panel.html:57` already has
a `.contact-star` slot inside `.contact-head`; a retired card gets a `retired`
chip there and an `is-retired` class on `.contact-card` (`:55`).

### D7 — Nothing is silently absent

Every list that filters retired rows out states the count and offers the way
back.

**TUI, account Contacts tab — this is cheaper than it looks.** `#tab-hint` is
already a dynamic `Static` (`tui/screens/account.py:534`) updated per tab at
`:1084-1095`, and it already rewrites itself conditionally for an empty table
(`:1089-1094`). The retired count is one more condition at `:1085`:
`12 contacts · 2 retired — T shows them`.

**TUI, team screen.** `tui/screens/team.py:142-151` already builds its hint
with a live count. Adding "· 2 retired" and the key is the same one-line
change, and fixing `:130` to offer them is part of this work, not a follow-on.
`member_reactivate` existing only on MCP is the bug this clause exists to kill.

**Web.** `_contacts_panel.html:45` already renders `{{ count }}`; the retired
count joins it, with the toggle as a `hx-get` on the same panel route.

### ⟲ D8 — Surfaces and keys

- **TUI key: `T` (retire/restore), not `R`.** ⟲ The previous draft chose `R` on
  the grounds that it is unbound on `AccountScreen` (`tui/screens/account.py:423-470`
  — true, no `R` there). The premise holds; the conclusion does not. `R` is
  taken app-wide with a settled meaning: `tui/screens/navigator.py:256` binds
  it, `:1239-1247` makes it dual-role — its own docstring reads "R is
  dual-role… revert on a focused MCP CHANGES table, plain refresh everywhere
  else" — and the hint at `:62` reads "**R** revert this change". `CLAUDE.md:43`
  uses `R` the same way, and **D5 of this very document depends on it**. One
  shift key with three meanings, one of them the undo path this feature rides,
  is not a key choice. `T` is free on `AccountScreen` and on `TeamScreen`
  (`tui/screens/team.py:38-44`), reads as "reTire", and takes the shift key the
  way every heavier flow on this screen does (`D` delete, `L` add layer, `P`
  paste items, `I` import — `account.py:449-458`). The unrelated-shift-pair
  precedent is already set by `p` mark_primary / `P` paste_items (`:456-458`).
  `show=False`, because the footer must fit 140 columns (`CLAUDE.md:113-118`)
  and the per-tab hint names it.
  - Rejected: **a chooser on `D`.** One key offering remove-or-retire blurs the
    two, which `services/contacts.py:7-11` explicitly forbids ("do not blur the
    two behind one control").
  - Rejected: **a free lowercase key** (`b`, `f`, `h`, `v`, `z`). Lowercase
    would match "retire is the gentler sibling of `D`", but none is mnemonic,
    and an arbitrary letter in a hint line is how keys get forgotten.
  - **Cost if wrong:** one `Binding` key string and two hint strings, plus
    their tests. Trivial now, less trivial once the suite pins the sentences.
- **Confirm step**, both surfaces, sharing one `retire_consequences()` the way
  `consequences()` is shared today (`services/contacts.py:94-134`) — the
  primary-contact consequence, "they stay on N interactions", and D4's
  restore/revert qualification are exactly the things invisible from the row.
- **Web**: `GET`/`POST /accounts/{ref}/contacts/{contact_id}/retire`, the same
  confirm-GET-writes-nothing shape as remove (`web/routes/relationship.py:395-396`
  and `:436-437`, template `_contact_confirm_remove.html`).
- **MCP**: `contact_retire` / `contact_restore`, thin wrappers that resolve
  names and report, modelled on `_contact_remove` (`mcpserver.py:1035-1057`).
- **`_EDIT_REDIRECTS` gains `("contact", "active")`** (`mcpserver.py:1706-1713`)
  — the same entry `team_member` already has at `:1707`. The redirect fires in
  the `field not in allowed` branch (`mcpserver.py:1842-1851`), so `edit_field`
  refuses with a destination instead of the generic allowed-list.
- **`web/parity.py`**: a new `AccountScreen` binding turns
  `tests/test_web_parity.py` red until it is listed in `IMPLEMENTED` (`:19`) or
  `PENDING` (`:66`); the guard is `tests/test_web_parity.py:49-57` and the
  ledger's own docstring states the contract (`web/parity.py:1-15`). Land the
  web route in the same slice and put it in `IMPLEMENTED`.

### D9 — Two refusals that become wrong the day this ships

- `_contact_add` refuses a duplicate name using `active_only=False` and advises
  "edit them with `edit_field`" (`mcpserver.py:1007-1013`). For a *retired*
  person the right advice is "restore them". The refusal must branch on
  `dup.active`.
- `repo/team.py:28-29` refuses a duplicate name with "rename or deactivate them
  first" — today that names an action no TUI user can take. It becomes true
  once D3 and D7 land; until then it is a refusal pointing at nothing.

### D10 — Vocabulary — **this one needs Grant**

⟲ The previous draft settled this itself. It should not have:
`docs/superpowers/specs/2026-08-14-mcp-team-edits-design.md:36-43`, inside a
block headed **"## Decisions (Grant, 2026-08-14)"** (`:34`), records Grant's own
ruling that "the batch summary should read *deactivated Sarah Chen*, not
*edited team_member.active on Sarah Chen*". Changing that summary to "retired
Sarah Chen" reverses a written Grant decision, so it is his call, not a
drafter's. See `blocked_on_grant`.

Two things the code decides regardless of which word he picks:

- **The MCP tool names `member_deactivate` / `member_reactivate` stay.**
  Renaming a tool the assistant has already learned costs vocabulary and buys
  nothing a description cannot. New tools are `contact_retire` /
  `contact_restore` — or `contact_deactivate` / `contact_reactivate` if Grant
  keeps "deactivate", in which case they match the shipped pair.
- Whatever word wins is used **in every summary, hint, chip, confirm and
  refusal, on all three surfaces**. One word, not two. This matters because
  `ROADMAP.md:269-294` is already tracking a live instance of exactly this
  drift — `_activity_delete` writing a different tool name and summary than the
  service it should call, so the changes rail describes one write two ways.

**Cost if wrong.** A string sweep across summaries, hints and templates plus
their tests. Cheap now; more expensive once the retirement suite pins the
sentences — which is the argument for asking before build step 1, not after.

---

## Data safety

No migration. No `ALTER`, no backfill, no rewrite, no `CHECK` change, no FTS
rebuild, no rowid movement. Every write goes through `base.update`, so every
change lands in `event_log` and reverts through `u` / `R` / the web rail. The
only on-disk change is `contact.active`, `contact.is_primary` and
`team_member.active` moving between `1` and `0` on rows the user names one at a
time, each in its own revertible batch. Nothing here needs a backup taken
first.

The one write that is not a flag flip is the team cascade, which already ships
and already snapshots nothing — it is revertible as one batch
(`mcpserver.py:1545-1553`) and the pre-check in D5 is what stops it hitting the
cap and rolling back with unusable advice.

## Tests, and the mutation that makes each one fail

`CLAUDE.md` records that a green suite proves nothing broke, not that the new
path is taken. Each test below names the production mutation that reddens it.
Where a test cannot name one, it is decoration and is marked so.

| test | mutation that makes it fail |
|---|---|
| retired contact drops off `for_org(conn, org.id)` (default) | delete the `active = 0` write from `retire()` |
| retired contact is still returned by `for_org(…, active_only=False)` | make `retire()` call `contacts_repo.delete` instead |
| **paired in one test:** absent from `for_org` default **and** present in `attendees()` | the pairing is the point. Alone, the attendee half is decoration: it passes for a `retire()` that never writes `active=0`, because `attendees` filters only on `base.alive('c')` (`repo/interactions.py:79`). Its own mutation is adding `AND c.active = 1` to that WHERE. Model on `tests/test_contact_remove.py:64-65`, which pairs exactly this way |
| retiring the primary clears `is_primary` | delete the `is_primary=0` write; assert via `for_org(…, active_only=False)` so the row is visible |
| `restore()` returns the star when no other active contact holds it | delete the vacancy branch from `restore()` |
| `restore()` does NOT return the star when someone else holds it | invert the vacancy check |
| retire is ONE batch: both field events carry the same `batch_id`, and `batches_svc.revert` puts both back | replace `open_batch` with a bare `db.transaction` — events lose `batch_id` and the revert finds nothing |
| second `retire()` on the same contact refuses by name; `already_retired()` returns the same sentence as a read | make `retire()` no-op on an already-retired row |
| `SearchHit.title` carries `(retired)` | drop the marker from `repo/search.py:58-61` |
| team cascade over `BLAST_CAP` refuses with the actionable sentence, not "narrow it and try again" | delete the pre-check. **Cost noted:** this test must seed 250+ assignments; if that is too slow, parametrise the cap rather than skipping the test |
| `TAB_HINTS["tab-contacts"]` names no dead key | add `[b]T[/b] retire` to `account.py:80-84` without the `Binding` → `tests/test_dead_keys.py:105-106` reddens |
| **gap, stated honestly** | `tests/test_dead_keys.py` arbitrates one direction only: `_advertised(hint) - _live_keys(app)`. Binding `T` and forgetting the hint fails nothing, and `T` shadowing another meaning fails nothing. The team screen's hint is in no `TAB_HINTS` dict and is covered by no test at all (the parametrize at `tests/test_dead_keys.py:74-82` lists four account tabs). Extending it to the team screen's dynamic hint (`team.py:147-151`) is part of build step 4 |
| web route exists or is consciously deferred | add the `T` binding without a `web/parity.py` entry → `tests/test_web_parity.py:49-57` reddens. This is the stronger guard of the two |

## Build order

1. `services/contacts.py`: `retire`, `restore`, `already_retired`,
   `retire_consequences`, `Retirement`. Tests per the table above, modelled on
   `tests/test_contact_remove.py`.
2. Display: the marker on attendee lists and search titles (D6); the count line
   and toggle (D7).
3. Surfaces: TUI `T` + hint + `test_dead_keys`; web route + parity entry; MCP
   `contact_retire`/`contact_restore` + `_EDIT_REDIRECTS`; D9's refusal branch.
4. Lift the team half into `services/team.py`, add the cascade pre-check (D5),
   fix the team screen's silent absence (D7), and extend `test_dead_keys` to
   the team hint.
5. **Markets — a separate bug this design found.** The previous draft called it
   "four call sites"; it undercounts and would be wrong applied uniformly. The
   honest split:
   - **Filter (offer only non-dormant, with count and toggle):** the markets
     list, which goes through `orgs.market_families` (`repo/orgs.py:240-252`,
     `list_orgs` at `:243`) rendered at `tui/screens/markets.py:183` — *not* the
     screen, as previously cited; the submission form select
     (`forms/entities.py:308`); the RFI form select (`forms/entities.py:552`).
   - **Must NOT filter:** `sync.carrier_suggestions` (`sync.py:397`) — hiding a
     dormant market from the alias queue invites `create_market_for_carrier`
     (`sync.py:411-414`) to make a duplicate org under the same name, which is
     the same argument this document makes for leaving
     `imports/matcher.py:38` at `active_only=False`; `services/book.py:46`, an
     id→name lookup where filtering renders the raw carrier string instead of
     the market's name; `mcpserver._resolve_market` (`:1968`), where a dormant
     market must still resolve by name and appear in the nearest-match hint.
   - **Leave alone:** the parent picker (`markets.py:94`) and merge-target
     picker (`markets.py:234`) — a dormant market is a legitimate merge source
     and target; and `account.py:2350`, an emptiness guard.

Steps 1-3 make retirement real for contacts. Step 4 makes it a mechanism rather
than a one-off and repays the precedent's debt. Step 5 is separable and is
argued for splitting into its own ROADMAP entry.

## What this document deliberately does not settle

- The user-facing word (D10) — Grant's, because it reverses his written ruling.
- Whether step 4 ships in the first slice — scope-versus-time, Grant's.
- Whether markets get the dormant filter here or as their own ROADMAP entry.
- Retirement reasons. Deliberately out: the escape hatch is an additive column
  (D2 cost-if-wrong), and inventing the vocabulary now is the failure D1 warns
  against.
- Retiring an *org* (client), as opposed to a market. `org.status` already has
  `'lost'` and `'dormant'`; nothing in this design touches client accounts, and
  the filter question for them is a different one (a lost client still owes
  history to the book).



---

## Verification report — round 2 (independent adversarial pass)

**Verdict: needs-revision.** The four decision reversals are correct and, where I could, I re-derived them from the code and from sqlite itself rather than trusting the draft or the verifier — D2's withdrawn FTS/rowid argument, D4's withdrawn set_primary invariant, D6's collapse of four MCP sites to one search door, and the four-to-nine market call-site correction all survive. The document is materially more honest than round 1.

It fails on the layer built on top of those reversals: every enumeration the revision widened, it widened by one step and stopped. Filtering dormant markets inside orgs.market_families silently empties the navigator's MARKETS tree (navigator.py:343, a second caller of the very function the revision chose as the fix point) — the same defect class the market undercount was, one level down. D6's marked-surfaces list misses a third attendee renderer (account.py:855) and misses the entire team-assignment surface, where mcpserver._team_assign will happily assign a retired colleague (via _find_member's active_only=False read) and team.for_org renders them unmarked on four screens because it filters alive(tm), never tm.active. D7's "one more condition at account.py:1085" is defeated by the empty-table branch at :1089-1094, which throws the retired count away in exactly the case that needs it.

Two coverage claims are weaker than stated. The TAB_HINTS test only fires if `T` is advertised in the STATIC dict, while D7 puts the advertisement in the dynamic string — the document must pick one and say so. And the cascade-cap test's escape hatch ("parametrise the cap") cannot be done: BatchState.cap binds BLAST_CAP at class-definition time and open_batch exposes no cap parameter.

Fix those and the design is buildable; the decisions themselves do not need re-deciding.


### Decisions round 2 reversed from round 1

- **Was:** D2's stated "decisive fact": choosing a status field over the boolean forces a CHECK-constraint change and therefore a SQLite table rebuild, which reassigns rowids and corrupts the external-content FTS5 indexes — a destructive migration in exchange for a naming preference.
  
  **Now:** The answer (boolean `active`) is UNCHANGED; the reason is withdrawn as false and replaced. `ALTER TABLE contact ADD COLUMN status TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current','retired'))` is accepted by sqlite 3.45.3, is additive, does not rebuild, does not move rowids, and does not fire the FTS triggers. The decision now rests on: (1) retirement is a status VALUE wherever a status exists, and the only two records needing a mechanism are exactly the ones with no status field; (2) how this repo actually stores status (raw TEXT + CHECK or + a models.py tuple, theme.STATUS_STYLES for colour); (3) the ListDefinition.WellKnown rule's precondition — a classification field — is absent, so it is not being dodged; (4) the revert round-trip, which is verified and already shipped for is_primary.
  
  **Why:** I re-ran the migration myself against sqlite 3.45.3 rather than trusting either the draft or the verifier. The verifier was right. The repo already ships five ALTER TABLE ADD COLUMN migrations (002, 007, 008, 009, 011), one with NOT NULL DEFAULT. Keeping the conclusion and quietly fixing the footnote would have made a load-bearing argument look checked when it was false.

- **Was:** D4 step 3: clear is_primary on retire because otherwise it 'quietly falsifies set_primary's exactly-one-primary-per-org invariant (repo/contacts.py:58-64)', with cost-if-wrong 'the next set_primary appears to work and leaves two'.
  
  **Now:** The invariant claim is withdrawn — repo/contacts.py:61 iterates for_org(active_only=False), so a retired row IS cleared and two primaries are impossible; base.update is also not alive-gated out, because a retired row is alive. Clearing is_primary is KEPT, on display grounds: every reader (account.py:637, navigator.py:774, relationship.py:111 → _contacts_panel.html:57, onboarding.py:70) reads the flag off an active_only=True list, so the account reads 'no primary' while a hidden row holds the star — and D7's toggle would then render two stars in three separate renderers.
  
  **Why:** The verifier was right that the cited line refutes the claim. But 'the reason was wrong' is not 'the behaviour was wrong': re-deriving from the readers gives a different and weaker-but-real justification, and the toggle interaction (which neither draft nor verifier noticed) is what makes it worth a service-level write rather than three renderer conditions.

- **Was:** D4: restore() sets active=1 only and does NOT put is_primary back — 'a primary is a judgment, and the batch revert is the path that puts the star back.'
  
  **Now:** restore() sets active=1 and then sets is_primary=1 only if no other active contact on that org currently holds it, saying which happened.
  
  **Why:** Follows from the reversal above. Once the retire clears the flag on display grounds rather than invariant grounds, 'the batch revert puts it back' has to be checked — and it does not, practically: services/undo.py:44-51 reverts only the most recent TUI batch (SOURCE='tui', :32) and list_batches shows 20, so a months-old retire batch is unreachable. That left 'press p' as the only real path, i.e. retire+restore is silently lossy. The vacancy check makes two primaries impossible, so returning the star to the person it was taken from is restoration, not the promotion-is-a-judgment case removal argues.

- **Was:** D8: bind `R` to retire/restore on AccountScreen, on the grounds that R is unbound there and D is already remove.
  
  **Now:** Bind `T` (reTire), show=False, on AccountScreen and TeamScreen.
  
  **Why:** The premise resolves (no R in account.py:423-470) but R is taken app-wide with a settled meaning: navigator.py:256 binds it, :1239-1247 makes it dual-role revert/refresh with that docstring, the hint at :62 reads 'R revert this change', CLAUDE.md:43 uses it that way, and D5 of this very document depends on R meaning revert. T is free on both screens and joins the established shift-for-heavier-flow family (D/L/P/I), with the unrelated-shift-pair precedent already set by p/P at account.py:456-458.

- **Was:** D6: MCP reads at mcpserver.py:1007, :1047, :1756, :1894 'must include "active": false, or the assistant will confidently email someone who left.'
  
  **Now:** That prescription is dropped. All four are name-resolution paths inside write tools, three exposing names only inside refusal strings, none returning a contact record in a success payload; there is no MCP tool that lists an account's contacts (full tool set, mcpserver.py:119-608). The named harm reaches the model through exactly one door — `search` (:136 → _search at :698-704, returning {kind,title,snippet}) — so D6's search-title marker IS the MCP fix. team_roster (:1392, off an active_only=False read at :1380) is named as the shape any future person-returning read follows.
  
  **Why:** The verifier was right and I confirmed it by enumerating every `async def` tool. A prescription aimed at four call sites that cannot cause the harm is worse than none: it would have been implemented, passed review, and left the actual exposure open.

- **Was:** D10 settled the user-facing vocabulary ('retire'/'restore' everywhere, member_deactivate's summary changes to 'retired <name>') as a drafter decision, 'recorded here so nobody fixes it later'.
  
  **Now:** Moved to blocked_on_grant. The document still fixes the two parts the code decides regardless (tool names stay; one word on all three surfaces), and states the cost of asking late.
  
  **Why:** docs/superpowers/specs/2026-08-14-mcp-team-edits-design.md:36-43 sits inside a block headed '## Decisions (Grant, 2026-08-14)' (:34) and rules that the summary should read 'deactivated Sarah Chen'. Changing it reverses a written Grant decision. Under this repo's convention that is his call — and the draft's own cost-if-wrong ('cheaper before the tests are written') is the argument for asking now rather than deciding for him.

- **Was:** Build step 5 / D1: the market dormant filter is 'four call sites, not a new column' — tui/screens/markets.py:157-180, forms/entities.py:308, forms/entities.py:551-553.
  
  **Now:** Nine call sites, split three ways. FILTER: repo/orgs.market_families (orgs.py:243, rendered at markets.py:183 — the screen does not call list_orgs at all), forms/entities.py:308, forms/entities.py:552. MUST NOT FILTER: sync.py:397, services/book.py:46, mcpserver.py:1968. LEAVE: markets.py:94, markets.py:234, account.py:2350.
  
  **Why:** The verifier flagged the undercount; I enumerated every list_orgs call site and worked out what each one does. sync.py:397 is carrier_suggestions, the alias review queue — hiding a dormant market there invites create_market_for_carrier (sync.py:411-414) to create a duplicate org under the same name, which is precisely the argument this document makes for imports/matcher.py:38. 'Four call sites' applied uniformly would have shipped that bug.


### Regressions the revision introduced

*This is the list that stopped the iteration: a fix reproducing its own defect class one level down.*

- ONE LEVEL DOWN #1 — market call sites. Round 1 said "four call sites"; the revision correctly enumerated nine list_orgs callers and split them three ways, then nominated repo/orgs.market_families as the filter point WITHOUT enumerating that function's callers. It has two (tui/screens/markets.py:183 and tui/screens/navigator.py:343). Shipping the prescription as written makes dormant markets vanish from the navigator tree with no count and no toggle — the identical silent-absence bug, now in the fix.

- ONE LEVEL DOWN #2 — the count line. D7's whole purpose is "nothing is silently absent", and its TUI implementation is prescribed at account.py:1085, which account.py:1087-1094 overwrites entirely whenever the table is empty. The all-contacts-retired account therefore reads "empty — a adds the first row": the count that D7 exists to print is suppressed precisely where its absence is most misleading.

- ONE LEVEL DOWN #3 — the marker location. D4 argues, correctly, that fixing is_primary in three renderers is the guard-in-the-caller pattern this codebase has paid for twice, and moves it to the service. D6 then prescribes the retired marker as a per-renderer change in three-plus renderers (two of which it names, missing account.py:855) — reproducing the pattern it just rejected, in the same document, for the same records.

- NEW FALSE COVERAGE CLAIM — the TAB_HINTS row. The revision added a mutation for every test, which is the right discipline, but this one's mutation ("add [b]T[/b] to account.py:80-84") only fires against the static dict that tests/test_dead_keys.py:105 reads, while D7 puts the T advertisement in the dynamically built hint at :1085. As written the row claims coverage that the design's own implementation plan removes.

- NEW UNBUILDABLE FALLBACK — the cap test. "Parametrise the cap rather than skipping the test" was added to close the round-1 objection about untestable-because-slow, but db.py:51 binds cap=BLAST_CAP at class-definition time and services/batches.py:92-100 has no cap parameter, so the fallback requires an unnamed production change. A closed finding with an escape hatch that does not exist.


### Citations that still did not check out

- **`build step 5: "the markets list, which goes through orgs.market_families (repo/orgs.py:240-252, list_orgs at :243) rendered at tui/screens/markets.py:183 — *not* the screen, as previously cited; ... the screen does not call list_orgs at all"`** — claimed: MarketsScreen does not call list_orgs
  
  *Actually:* FALSE, and self-contradicting. MarketsScreen (class at markets.py:57) calls orgs.list_orgs(kind="market") twice: markets.py:94 (action_nest_market's parent picker) and markets.py:234 (action_merge_market's target picker). The same paragraph lists both under "Leave alone". The true statement is narrower — the markets TABLE RENDER path goes through market_families, not list_orgs. As written it is a false claim inserted while correcting another false claim.

- **`D4: "the batch-revert escape hatch is not a real one for a months-old batch (`u` only reaches the most recent TUI batch — services/undo.py:44-51, SOURCE = "tui" at :32 — and list_batches shows 20)"`** — claimed: the cited lines establish that a months-old retire batch is unreachable by revert
  
  *Actually:* The undo.py citations resolve exactly (:32 SOURCE="tui", :49 last_undoable, :53 revert). But they only close the `u` door. The doors the argument actually needs closed are elsewhere and are uncited: tui/screens/navigator.py:350-352 caps the changes node at batches created in the last 14 days (repo/batches.py:57-67, no source filter — so `R` DOES revert TUI batches, just not old ones), and web/routes/account.py:545-549 shows the 8 most recent for that account. Against it: mcpserver.py:608 revert_batch(ref) accepts ANY ref and list_batches' limit=20 (:601) is a caller-settable default, so via the assistant an old batch is reachable. The conclusion survives for a TUI/web user; the evidence offered does not carry it.

- **`D6: "the marker must be built into SearchHit.title where the title is built (repo/search.py:58-61)" + the test row "SearchHit.title carries (retired) | drop the marker from repo/search.py:58-61"`** — claimed: the marker is a change confined to :58-61
  
  *Actually:* repo/search.py:49-52 selects c.id, c.org_id, c.first_name, c.last_name, c.title AS job_title, f.rank, snippet — it does NOT select c.active, and fts_contact (migrations/001_initial.sql:295-297) indexes first_name/last_name/title/notes only. The marker cannot be built at :58-61 without also amending the SELECT at :49. Incomplete prescription, not a wrong line.

- **`citation 18: repo/base.py:78-95, 112-113, 120-121`** — claimed: _assert_known_field at 78-95; batch.touch at 112-113; old/new stringified at 120-121
  
  *Actually:* Off by one to two lines each: _assert_known_field's def opens at :76 (:78 is the `) -> None:`); batch.touch is at :112 alone (:113 is conn.execute); the str() coercions are at :121-122 (:120 is `field`). Every underlying claim holds. Fix the references and move on.


### Claims challenged

- **[CRITICAL]** Build step 5: put the dormant filter in repo/orgs.market_families (orgs.py:243), "rendered at markets.py:183".
  
  *Evidence:* market_families has TWO callers, not one: tui/screens/markets.py:183 and tui/screens/navigator.py:343, where it builds the navigator's MARKETS tree (markets_root at :342, families at :344-349). Filtering inside market_families silently removes dormant markets from the navigator tree — no count, no toggle, no marker — which is exactly the "silently absent from a list that claims to be complete" failure D6 forbids and D7 exists to prevent. The revision fixed the four-to-nine undercount by enumerating list_orgs callers, then picked a different function as the filter point and did not enumerate ITS callers. Either filter behind a parameter (market_families(conn, include_dormant=False)) and change only markets.py:183, or accept navigator.py:343 as a tenth surface owing a count line.

- **[CRITICAL]** D6: the surfaces "where a retired person still appears and would appear as though nothing had changed" are attendee lists, global search, and MCP. D9: "Two refusals that become wrong the day this ships."
  
  *Evidence:* A retired TEAM MEMBER renders as live team on four surfaces, unmarked, and the write that puts them there is unguarded. mcpserver._team_assign (:1428) resolves through _find_member (:1350-1357), which reads team.list_members(conn, active_only=False) at :1353 and checks nothing else — a retired colleague can be assigned today. repo/team.for_org (repo/team.py:116-131) filters on base.alive('ta') AND base.alive('tm') at :127 and never on tm.active, so that assignment renders at tui/screens/account.py:618, :1128, :1422 and web/routes/account.py:574 with no marker. This is the same precedent debt "Wrong 3" says this design repays, on the record the design says it is fixing, and it is missing from both the marked-surfaces list and the refusals list.

- **[IMPORTANT]** D7: "this is cheaper than it looks... The retired count is one more condition at [account.py]:1085: `12 contacts · 2 retired — T shows them`."
  
  *Evidence:* account.py:1084-1095 computes `hint = TAB_HINTS.get(tab, "")` at :1085 and then, at :1087-1094, REPLACES it wholesale when the tab's table has zero rows: `hint = "empty — [b]a[/b] adds the first row" if tab in ADDABLE_TABS else "nothing here — that's good"`. An account whose contacts are all retired renders an empty table, so the retired count computed at :1085 is discarded and the screen says "empty" — the single case where "2 retired — T shows them" is load-bearing. The fix has to touch the empty branch, not only :1085, and the design's cost estimate ("one more condition") is wrong.

- **[IMPORTANT]** D6: attendee lists are "Rendered at tui/screens/account.py:650 and web/routes/relationship.py:190."
  
  *Evidence:* interactions.attendees has THREE render sites: tui/screens/account.py:650 (the interactions table's `who` column), tui/screens/account.py:854-856 (the interaction DETAIL pane, `who = ", ".join(c.name for c in interactions.attendees(...))`), and web/routes/relationship.py:190. Marking two of three leaves a retired attendee unmarked in the pane a user opens to actually read the interaction. Separately: prescribing the marker per-renderer in three places is the guard-in-the-caller shape D4 rejects two pages earlier — if three renderers need it, it belongs in one helper (or on Contact.name's display form), which is also the only way the fourth renderer added next month inherits it.

- **[IMPORTANT]** Test row: "TAB_HINTS['tab-contacts'] names no dead key | mutation: add [b]T[/b] retire to account.py:80-84 without the Binding → tests/test_dead_keys.py:105-106 reddens."
  
  *Evidence:* The mutation fires ONLY if `T` is advertised in the static dict. tests/test_dead_keys.py:105 reads `_advertised(TAB_HINTS[tab]) - _live_keys(app)` — the module-level dict at account.py:74-84, never the rendered `#tab-hint` Static. D7 prescribes the retired count and "T shows them" as a DYNAMIC string built at account.py:1085. If the key is advertised only in the dynamic half, the cited test sees nothing, the named mutation is never made, and the row is decoration. The document must state that `[b]T[/b] retire` goes in the static TAB_HINTS["tab-contacts"] entry. (The web-parity row IS sound: web/parity.py's ledger is keyed on Binding.action names collected at tests/test_web_parity.py:40-46, so a new Binding("T", "retire_row") with no ledger entry reddens :49-57.)

- **[IMPORTANT]** Test row: "if that is too slow, parametrise the cap rather than skipping the test."
  
  *Evidence:* There is nothing to parametrise. db.py:51 declares `cap: int = BLAST_CAP` as a dataclass field default, bound at class-definition time — monkeypatching db.BLAST_CAP does not change the cap of any BatchState built afterwards. And services/batches.py:92-100 (open_batch) takes source/tool/summary/org_id/entity_id and no cap, so a batch opened by the team service cannot be given a small one. tests/test_db.py:198,213 get away with it only because they construct db.BatchState(cap=…) by hand and pass it to db.transaction directly, which the service path does not do. Parametrising requires a production signature change to open_batch that the document neither names nor budgets; the honest cost is seeding 250+ assignments across 250 orgs.

- **[IMPORTANT]** Build step 4 / D3: "Lift the team half into services/team.py" — presented as a mechanical move that repays the precedent's debt.
  
  *Evidence:* The identical move is already on the ROADMAP as explicitly NOT mechanical, and this document cites that entry for a different purpose. ROADMAP.md:279-289 says of _activity_delete: "the move is NOT mechanical: _activity_delete calls _provenance(...) inside its batch and the service does not... so either the service grows an optional hook or MCP keeps a thin wrapper that opens no batch of its own — and it cannot open one, because db.transaction nests by JOINING". _member_deactivate has the same shape and worse: _provenance fires twice per assignment plus once on the member (mcpserver.py:1550, :1553). services/contacts.py:24-27 resolved it by dropping provenance entirely; doing the same for team stops writing event_log 'source' rows for deactivate/reactivate — a behaviour change that needs to be named and costed, not discovered in build step 4.

- **[MINOR]** D2's reversal, D4's is_primary reversal, D6's "exactly one door", and the nine market call sites.
  
  *Evidence:* These hold, and I verified them independently rather than trusting either draft. sqlite 3.45.3 locally: ALTER TABLE t ADD COLUMN status TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current','retired')) succeeds, the existing row reads 'current', a subsequent UPDATE to 'bogus' raises "CHECK constraint failed", and UPDATE t SET active='0' stores typeof 'integer' — so D2's withdrawal of the FTS/rowid argument is correct and the boolean now rests on true reasons. repo/contacts.py:61 does iterate for_org(active_only=False) and :62-63 clears the other holder, so D4's invariant withdrawal is correct; services/contacts.py:198-202's comment is still right for its own case because base.alive() (repo/contacts.py:25) hides the DEAD row from that same loop. D6's "one door": contacts_repo is imported in mcpserver at exactly five sites (941, 1003, 1041, 1747, 1888) and no read tool returns a contact record — search (:136 → _search :698-704) is the only path, confirmed against the full tool set at :119-608. Nine market list_orgs sites, split exactly as the document splits them. And "nothing writes contact.active" is right: the only two writes in src/ are mcpserver.py:1551 and :1569, both team_member.


### Needs Grant

- **The user-facing word: 'retire/restore' or 'deactivate/reactivate'?** This is not a drafter's call — docs/superpowers/specs/2026-08-14-mcp-team-edits-design.md:36-43, inside a block headed '## Decisions (Grant, 2026-08-14)' (:34), rules that the batch summary should read 'deactivated Sarah Chen'. The previous draft reversed that silently. OPTIONS: (a) 'retire/restore' everywhere the user reads it — summaries, hints, chips, confirms, refusals — with the shipped MCP tool NAMES member_deactivate/member_reactivate left alone, and new tools contact_retire/contact_restore; the shipped summary changes to 'retired <name>'. (b) 'deactivate/reactivate' everywhere, new tools contact_deactivate/contact_reactivate, nothing shipped changes. RECOMMENDATION: (a). It is the word the ROADMAP entry uses (ROADMAP.md:78, :84), it is the word Grant's own prose in that very decision uses ('Retiring someone drops them out of list_members...', :40-41), and it names what happened to a person rather than what happened to a column. The 2026-08-14 ruling's actual load was the contrast with 'edited team_member.active on Sarah Chen' — a state-transition summary, not a field-edit summary — and (a) preserves that entirely. COST IF WRONG: a string sweep across summaries, hint lines and templates plus their tests. Cheap now, more expensive once the retirement suite pins the sentences — which is why this needs answering before build step 1, not after.

- **Scope of the first slice: contacts only (build steps 1-3), or contacts plus lifting member_deactivate/member_reactivate out of mcpserver.py into services/team.py with a TUI control (step 4)?** The mechanism is proven against team_member either way; the question is whether the team half ships now. RECOMMENDATION: both. Leaving it means the precedent this design copies stays MCP-only and keeps failing its own 'never silently absent' rule — tui/screens/team.py:130 hides retired colleagues with no toggle, no count and no marker; services/team.py:27 drops them from the specialist search; and repo/team.py:28-29 refuses a duplicate name by naming a remedy no TUI user can perform. But it is roughly double the work and it is a scope-versus-time call, not a technical one. COST IF WRONG: if step 4 slips, the two implementations may drift before they are unified, and the refusal at repo/team.py:28-29 stays a dead end for however long that is.

- **The market dormant filter (build step 5): fold in, or its own ROADMAP entry?** This is a separate bug this design found, not part of retirement. org.status already accepts 'dormant' (migrations/001_initial.sql:18-19), org_form already writes it (forms/entities.py:50, :82), and nothing filters on it. Three call sites should filter (repo/orgs.market_families at orgs.py:243, forms/entities.py:308, forms/entities.py:552), three must NOT (sync.py:397, services/book.py:46, mcpserver.py:1968), three are left alone (markets.py:94, markets.py:234, account.py:2350). RECOMMENDATION: its own ROADMAP entry. It shares a sentence with retirement ('a marked-as-over record should drop out of the pickers') and nothing else — no shared code, no shared migration, no shared service — and folding it in doubles the review surface of a slice whose value is the contact mechanism. COST IF WRONG: a broker keeps being offered dormant markets in the submission and RFI forms for however long the separate entry waits. Low and visible, not silent.


### Deliberately not settled

- The user-facing word (D10) and whether the team lift ships in the first slice — both in blocked_on_grant, both deliberately not decided here.

- Whether the market dormant filter belongs in this work or its own ROADMAP entry — recommended split, Grant's call.

- Retirement REASONS ('left the company' / 'no longer our contact' / 'moved accounts'). Deliberately out of scope: the escape hatch is an additive `retired_reason TEXT` column completed via Field.suggestions (D2 cost-if-wrong), and inventing the vocabulary before anyone has asked for it is the generalise-by-reflex failure D1 is written against. Note this, and not the two-value current/retired enum, is the case where the global ListDefinition.WellKnown instruction would genuinely bite.

- Retiring a CLIENT org, as opposed to a market. org.status already carries 'lost' and 'dormant' and nothing in this design touches client accounts — but the filter question for them is a different one, because a lost client still owes history to the book (services/book.py:87 already counts only status='active' clients).

- Whether the D5 cascade pre-check test is worth its runtime. It needs 250+ seeded assignments to redden. If that is too slow, the alternative is parametrising db.BLAST_CAP for the test rather than skipping it — but that is a production-code change made for a test's convenience, and I have not decided it.

- Whether the TUI retired-rows toggle is per-screen state or a session preference. This design assumes per-screen and transient (press T with no row focus, or a separate key), which is the cheapest thing that satisfies D7; a sticky preference would need somewhere to live and nothing in tui/ currently persists view state of this kind.
