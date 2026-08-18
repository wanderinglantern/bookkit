<!-- DRAFT — NOT APPROVED. Read the verification report at the bottom before building from this. -->

> **Status: DRAFT — NEEDS REVISION** (2026-08-18).
> Produced by a drafting pass, then checked by an independent adversarial pass that
> opened every citation and challenged the load-bearing claims.
> **101 citations checked · 9 failed · 9 claims challenged.**
> Kind: `spec`.
> The verification report at the bottom is PART OF THIS DOCUMENT — some of its findings
> would break an implementation built from the body above it.

---

# Retiring a record — design

Date: 2026-08-18
Status: draft, for review. Nothing here is implemented. It answers the three
open questions in `ROADMAP.md:99-107` ("Deactivating a record, generally") and
is bound by `CLAUDE.md` and by the shipped removal design in
`services/contacts.py:1-28`. Where this document and either of those disagree
unintentionally, they win and this is wrong.

## What the code actually says

The ROADMAP entry is mostly right and wrong in three places worth naming
before any of it is built on.

**Right, verified.** `contact.active` exists (`migrations/001_initial.sql:73`,
`models.py:167`), `contacts.for_org` takes `active_only` and defaults it to
`True` (`repo/contacts.py:24-32`), and **nothing anywhere writes the flag**.
No form declares it (`forms/entities.py:136-152`), MCP's `edit_field` does not
list it as editable (`mcpserver.py:1651-1654`), and the only other references
are reads passing `active_only=False`. The filtering half is built; the flag
is unreachable. Exactly as claimed.

**Wrong 1 — the precedent is MCP-only.** `member_deactivate` is
`mcpserver._member_deactivate` (`mcpserver.py:1521-1555`). It is not in
`services/`, not in `repo/`, and there is no `deactivate` or `reactivate`
control anywhere in `tui/`, `web/` or `forms/`. `member_reactivate`
(`mcpserver.py:1558-1572`) is likewise an MCP tool and nothing else. Copying
"the precedent" literally means writing the new rules in `mcpserver.py`, where
the TUI and the web cannot reach them — which is the mistake
`services/contacts.py:13-16` was written the same day to avoid, and the one
`repo/team.py:15-23` records having already been paid for once. **Copy the
shape, not the location.**

**Wrong 2 — it is not five candidate records, it is two.** Three of the five
already retire, through their own lifecycle field:

| record | already has | evidence |
|---|---|---|
| placement | `status` includes `'lapsed'` | `migrations/001_initial.sql:110-111` |
| opportunity | `stage` `'won'`/`'lost'`, plus `closed_at` + `outcome` | `migrations/001_initial.sql:151-153,164-166` |
| project | `status` `'completed'`/`'cancelled'` | `migrations/006_projects.sql:14`, `models.py:243` |
| market (org) | `status` `'dormant'` | `migrations/001_initial.sql:18-19` |
| contact | `active` column, unwired | `migrations/001_initial.sql:73` |
| team member | `active` column, MCP-only writer | `migrations/005_team.sql:13` |

Only the two **person** records lack the concept. The market is a different
problem again — see D1.

**Wrong 3 — the precedent already fails the third question.** The entry ends
with "a retired row must be visibly retired wherever it still appears, not
silently absent." The shipped `member_deactivate` produces exactly the
silence it warns about: `tui/screens/team.py:130` calls `team.list_members(conn)`
with `active_only` defaulting to `True` (`repo/team.py:45-48`), so a retired
colleague **disappears from the only screen that lists colleagues**, with no
toggle, no marker, and no way back — `member_reactivate` exists on MCP alone.
`services/team.py:27` drops them from the "who do I go to for…" search too.
And `repo/team.py:26-28` refuses a duplicate name with "rename or deactivate
them first" — naming a remedy the TUI user cannot perform. Copying the
precedent uncritically copies all of that.

## Remove versus retire, in one sentence

**Remove says it was never true. Retire says it was true and no longer is.**

The wholesaler filed as a client contact should be *removed* — that row should
never have existed, so the answer is the soft delete, and
`services.contacts.remove` (`services/contacts.py:158-209`) already ships it on
all three surfaces. The contact who left the company should be *retired* —
they stay on every meeting they attended and drop off the list you work from.

The schema already carries the two as independent axes, and they behave
differently in code today:

- `deleted_at` → removal. `contacts.for_org` applies `base.alive()`
  unconditionally (`repo/contacts.py:25`), so a removed contact is invisible
  even at `active_only=False`. `interactions.attendees` filters on `alive(c)`
  (`repo/interactions.py:74-84`), so they drop off attendee lists.
- `active` → retirement. A retired contact is still returned by
  `for_org(active_only=False)`, and **still appears on attendee lists**,
  because `attendees()` does not look at `active` at all. That is not an
  oversight to fix; it is the behaviour retirement wants, for free.

One consequence, already load-bearing: `imports/matcher.match_contact` reads
with `active_only=False` (`imports/matcher.py:36-40`), so a paste-import that
mentions a retired person **matches them** instead of creating a duplicate. A
removed person is not matched and would be recreated. The two axes are already
telling the importer different, correct things.

## Decisions

### D1 — Which records get this

**Contacts and team members get a retirement mechanism. Markets get a
filter, not a mechanism. Placements, opportunities and projects get
nothing.**

Contacts and team members are the only records whose tables already carry
`active`, and the only ones with no lifecycle status field for retirement to
live inside: `Contact` carries `role` (`models.py:155-174`), `TeamMember`
carries `role`/`specialty` (`models.py:354-365`). Neither has a status.

Markets are the interesting case and the reason not to generalise by reflex.
`org.status` already accepts `'dormant'`, `org_form` already offers it as a
select (`forms/entities.py:50,79`), and the markets screen's `e` already opens
that form (`tui/screens/markets.py:288-306`). **A broker can mark a market
dormant today and absolutely nothing happens.** The markets list renders every
market regardless (`tui/screens/markets.py:157-180`), and both market pickers
offer every market — the submission form (`forms/entities.py:308`) and the RFI
form (`forms/entities.py:551-553`). The missing half for markets is the
filter, which is four call sites, not a new column.

Placements, opportunities and projects are refused outright. Each already
encodes "this is over" in a field the pipeline, the attention windows and the
book all read. A second boolean saying the same thing would let a placement be
`status='bound'` and `active=0` at once, and no reader would know which wins.

**Rejected: one shared `active` convention across every entity table.** It
reads tidier and is what the ROADMAP entry gravitates toward. It is wrong on
the evidence above: it would add a contradictory second lifecycle to three
tables that have one, and add nothing at all to the market case, which is
already flagged and merely unfiltered.

**Cost if wrong.** If Grant later wants placements retired independently of
`status`, that is one additive column on one table, non-destructive, and this
document's mechanism transfers unchanged. If we add `active` to all five now
and it turns out `status` was the right home, we have five columns of dead
weight and every read has to decide which field wins.

### D2 — Boolean `active`, not a status value

**Retirement is a boolean `active` column on `contact` and `team_member`, both
of which already have it. No new column, no new vocabulary, no migration.**

Four reasons, in order of force.

1. **It is orthogonal to status, not a value of it.** The proof is in the
   table above: every record that *has* a status already encodes retirement in
   it, and both records that *need* retirement have no status field at all.
   Making "retired" a status value for contacts means inventing an entire
   status vocabulary for contacts whose only two values are `current` and
   `retired` — a boolean with extra syntax.

2. **The `ListDefinition.WellKnown` rule does not bind here, and this repo has
   already ruled so.** `docs/superpowers/specs/2026-08-13-rfi-tracking-design.md:110-115`
   settles it in writing: that is a Swift-project rule, bookkit's own
   convention is controlled-but-extensible tuples in `models.py` (the
   `TEAM_ROLES` pattern, `CLAUDE.md:129-130`). Nothing named
   `ListDefinition`, `WellKnown`, `ListValuePicker` or `StatusPill` exists
   anywhere in `src/`. The rule would only be *reachable* if we chose a status
   field, which is the thing being rejected.

3. **The migration cost is asymmetric and it is the decisive fact.** `active`
   on both tables already exists — the migration is empty. Putting retirement
   into `org.status` instead means changing a `CHECK` constraint
   (`migrations/001_initial.sql:18-19`), which SQLite can only do by rebuilding
   the table. `org` and `contact` both carry **external-content FTS5 indexes
   keyed on `rowid`**, maintained by insert/delete/update triggers
   (`migrations/001_initial.sql:272-312`), and the schema's own comment says
   "every 'delete' command must match an earlier insert exactly or FTS5
   external content corrupts" (`:275-276`). A table rebuild reassigns rowids.
   `CLAUDE.md:65-67` says migrations are additive-only so far and that anything
   destructive gets called out first. This one would be destructive in exchange
   for a naming preference.

4. **The boolean reverts through the existing machinery with zero new code.**
   `base.log_event` stores values as text (`repo/base.py:121-122`);
   `services.batches.revert` writes the old value back through `base.update`
   (`services/batches.py:369-374`); SQLite INTEGER affinity converts the
   stored `"0"`/`"1"` back to integers on write (verified directly against an
   in-memory table). And because `active` is a real column, `base.log_event`'s
   guard passes without any `events.NON_MUTATION_FIELDS` declaration
   (`repo/base.py:76-95`, `repo/events.py:82-88`) — the landmine class
   `CLAUDE.md:51-57` describes does not apply.

**Rejected: a `retired_at TEXT` timestamp instead of a boolean.** It carries
"when" for free and reads like `deleted_at`. Rejected because the column that
exists is the boolean, on both tables, and because a second timestamp column
sitting beside `deleted_at` invites exactly the confusion between the two axes
this whole document is drawing a line through. "When" is already recoverable
from `event_log`.

**Cost if wrong.** If Grant later wants retirement *reasons* — left the
company / no longer our contact / moved to another account — the fix is one
additive `retired_reason TEXT` column beside `active`, vocabulary-completed via
`Field.suggestions` the way `task.category` is. Additive, non-destructive, no
FTS rebuild. That escape hatch is why the boolean is the safe choice and not
merely the cheap one.

### D3 — Where the rules live

**`services/contacts.py` gains `retire()` / `restore()` beside `remove()`.
`services/team.py` gains `retire_member()` / `restore_member()`, lifted out of
`mcpserver._member_deactivate` / `_member_reactivate`.**

The module docstring at `services/contacts.py:1-28` already contrasts the two
verbs and points at this ROADMAP entry; retirement belongs in that file, not a
new one. The service opens its own batch, for the two reasons already written
down there (`:18-27`): the write must be one undo unit whatever surface asked,
and `db.transaction` nests by joining, so a surface wrapping it would leave a
second permanently-empty batch row in the changes list.

Lifting the team half is what makes this a mechanism rather than a
contact-shaped one-off, and it is what fixes "Wrong 1" and "Wrong 3" above:
once the rules are in `services/team.py`, a TUI control on the team screen
inherits them instead of forking them, and MCP keeps a thin resolve-and-report
wrapper exactly as `_contact_remove` already is (`mcpserver.py:1534-1556`).

**Rejected: a shared `services/lifecycle.py` with `retire(table, id)`.** The
two records differ in the only part that matters — nothing blocks retiring a
contact, while retiring a colleague is refused while assignments are live and
optionally cascades over them. A generic helper would take a policy callback
for the block and another for the cascade, which is more machinery than the
thirty lines it saves, and `CLAUDE.md`'s convention is a service per record.

**Cost if wrong.** If a third record wants this later and the two
implementations have drifted, extracting the common half then is a refactor
with two green test suites pinning the behaviour. Extracting it now is a
guess at what the third record needs.

### D4 — What retiring actually does

`retire(conn, contact_id, *, source)` → `Retirement`, mirroring `Removal`
(`services/contacts.py:62-91`):

1. Read through `base.raw_row` (dead-or-alive), so "already removed" and
   "already retired" are distinguishable refusals rather than "not found" —
   the reason `base.raw_row` exists (`repo/base.py:42-54`).
2. Refuse by name if already retired, in one sentence, exported as a read-only
   `already_retired()` the way `already_removed()` is
   (`services/contacts.py:137-155`) — the web's confirm step is a `GET` and
   cannot find out by attempting the write.
3. **Clear `is_primary` first, inside the same batch, promoting nobody.**
   Identical reasoning to removal (`services/contacts.py:197-204`): otherwise
   `for_org(active_only=True)` shows the account with no primary while the
   retired row still holds `is_primary = 1`, quietly falsifying
   `set_primary`'s "exactly one primary per org" invariant
   (`repo/contacts.py:58-64`).
4. Set `active = 0` through `contacts_repo.update`, so the change is
   event-logged and revertible.
5. Nothing cascades. `attendees()` never looked at `active`, so the person
   stays on every interaction they attended with no rows touched.

`restore()` is the inverse and sets `active = 1`. It does **not** restore
`is_primary` — a primary is a judgment, and the batch revert is the path that
puts the star back.

Refusals both ways say what to do instead, per `CLAUDE.md:100-105`.

### D5 — One writer action is one undo unit; the cap is not at risk here

**Yes, retirement is one batch.** `services.batches.open_batch(source=…)`,
`tool="contact_retire"`, summary `retired <name> at <org>`. `u`, `R` and the
web changes rail all put it back through the machinery that already exists
(`services/undo.py:44-67`, `services/batches.py:316-386`).

**`db.BLAST_CAP` is nowhere near it.** Retiring a contact touches **one**
entity — the contact row — clearing `is_primary` and setting `active` on the
same id, and `BatchState.touch` counts distinct entities
(`db.py:46-64`). The cap is 250 (`db.py:27`).

The cap does matter for the team cascade **that already ships**:
`_member_deactivate(cascade=True)` touches `1 + N` entities, one per assignment
(`mcpserver.py:1546-1553`). At `N ≥ 250` it raises `BlastRadiusExceeded` with
"this action would touch more than 250 records; **narrow it and try again**"
(`db.py:60-64`) — advice the caller cannot follow, because a cascade has no
narrowing knob. When the team half is lifted into `services/team.py`, give it
a pre-check that counts `team.for_member` first and refuses with the real move:
"unassign some of them first, or retire without cascade and unassign by hand."

**Rejected: raise `BLAST_CAP` for cascades.** It is Grant's call at 250 after
two revisions (`db.py:28-29`) and it is enforced under `log_event` precisely so
no tool can opt out (`CLAUDE.md:35-40`). The fix is a better refusal, not a
higher ceiling.

**Cost if wrong.** If the pre-check is skipped, the failure mode is a rollback
plus a misleading sentence, at a scale nobody in this book will reach. Low.
Worth one guard, not a redesign.

### D6 — How a retired row shows

The rule: **filtered out of the lists you pick and work from; kept and marked
everywhere it is history or identity; never silently absent from a list that
claims to be complete.**

**Filtered out** (`active_only=True` is already the default, so these need no
change beyond a count line — D7):

- TUI account Contacts tab and Overview — `tui/screens/account.py:630-641`
- TUI navigator contacts group and the account counts —
  `tui/screens/navigator.py:423`, `:770`
- TUI market detail contacts — `tui/screens/markets.py:587`
- web Relationship "People" panel — `web/routes/relationship.py:121`
- web tab count badge — `web/routes/account.py:293`
- onboarding coverage — `services/onboarding.py:65` (a retired contact should
  not count as the account being reachable)

**Kept, and must be marked.** These are the surfaces where a retired person
still appears today and would appear as though nothing had changed:

- **Attendee lists.** `interactions.attendees` filters on `alive(c)` only
  (`repo/interactions.py:74-84`). Rendered at `tui/screens/account.py:650`
  and `:855`, and `web/routes/relationship.py:190`. Render the name as
  `Name (retired)`, styled `theme.DIM` in the TUI.
- **Global search.** `repo/search.py:47-61` filters on `c.deleted_at IS NULL`
  only, so a retired contact still hits. Correct — they are still in the
  record — but `SearchHit.title` must carry the marker, built where the title
  is built (`repo/search.py:58-60`).
- **MCP reads.** `mcpserver.py:1007`, `:1047`, `:1756`, `:1894` all read with
  `active_only=False` and none of them reports the flag. They must include
  `"active": false`, or the assistant will confidently email someone who left.

**The marker is a word, not only a colour** — `CLAUDE.md:97-99`. In the TUI the
contacts tables already have a leading glyph column carrying `★` for primary
(`tui/screens/account.py:636`, `tui/screens/navigator.py:425`); retired rows,
when shown, take that column with a dim `·` **and** render the name dimmed with
a trailing `(retired)`. On the web, `_contacts_panel.html:56-59` already has
a `.contact-star` slot in `.contact-head`; a retired card gets a `retired` chip
there and a `is-retired` class on `.contact-card`.

### D7 — Nothing is silently absent

Every list that filters retired rows out states the count and offers the way
back. Concretely: the contacts panel count line reads
`12 contacts · 2 retired` with the retired half as a control that toggles them
into view, and the TUI tab hint gains the key that does it.

This is the clause that makes the difference between this and the shipped
team precedent, and it applies to the team screen too: fixing
`tui/screens/team.py:130` to show a retired count and a way to see and restore
them is part of this work, not a follow-on. `member_reactivate` existing only
on MCP is the bug.

### D8 — Surfaces and keys

- **TUI**, account Contacts tab: `R` for retire/restore. `R` is unbound on
  `AccountScreen` (`tui/screens/account.py:424-470`) and `D` is already
  "remove" — the two must not share a key. Add it to `TAB_HINTS["tab-contacts"]`
  (`tui/screens/account.py:78-82`) and to the team screen's hint
  (`tui/screens/team.py:147-151`). `tests/test_dead_keys.py` is the arbiter
  that hint and binding agree (`CLAUDE.md:106-111`).
- **Confirm step**, both surfaces, sharing one `retire_consequences()` the way
  `consequences()` is shared today (`services/contacts.py:94-134`) — the
  primary-contact consequence and "they stay on N interactions" are exactly the
  things invisible from the row.
- **Web**: `GET`/`POST /accounts/{ref}/contacts/{contact_id}/retire`, the same
  confirm-GET-writes-nothing shape as remove
  (`web/routes/relationship.py`, `_contact_confirm_remove.html`).
- **MCP**: `contact_retire` / `contact_restore`, thin wrappers that resolve
  names and report, like `_contact_remove` (`mcpserver.py:1534-1556`).
- **`_EDIT_REDIRECTS` gains `("contact", "active")`**
  (`mcpserver.py:1706-1712`) — the same entry `team_member` already has, so
  `edit_field` refuses with a destination instead of a generic list.
- **`web/parity.py`**: a new `AccountScreen` binding turns
  `tests/test_web_parity.py` red until it is listed in `IMPLEMENTED` or
  `PENDING` (`web/parity.py:6-8,19,66`). Land the web route in the same slice
  and put it in `IMPLEMENTED`.

### D9 — Two refusals that become wrong the day this ships

- `_contact_add` refuses a duplicate name using `active_only=False` and advises
  "edit them with `edit_field`" (`mcpserver.py:1007-1013`). For a *retired*
  person the right advice is "restore them". The refusal must branch.
- `repo/team.py:26-28` refuses a duplicate name with "rename or deactivate
  them first" — today that names an action no TUI user can take. It becomes
  true once D3 and D7 land; until then it is a refusal pointing at nothing.

### D10 — Vocabulary

**The user-facing word is "retire" / "restore", in every batch summary, hint
line, chip, confirm and refusal, on all three surfaces.** It is the word the
ROADMAP entry uses and it says what happened; "deactivate" describes a column.

The shipped MCP tools keep the names `member_deactivate` / `member_reactivate`.
Renaming a tool the assistant has already learned costs vocabulary and buys
nothing a description cannot. Their **summaries** change to `retired <name>`,
because the summary is what the user reads in the changes rail and under `u`.
New tools are `contact_retire` / `contact_restore`.

This is the one place where a tool name and the user's word deliberately
differ. Recorded here so nobody "fixes" it later — and note that
`ROADMAP.md:269-294` is already tracking a real instance of this drift
(`_activity_delete` writing a different tool name and summary than the service
it should be calling), which is the failure this rule is written against.

**Cost if wrong.** If Grant prefers "deactivate" as the user word, it is a
string sweep across summaries, hints and templates plus their tests — cheap,
and cheaper before the tests are written than after.

## Data safety

No migration. No `ALTER`, no backfill, no rewrite, no `CHECK` change, no FTS
rebuild. Every write goes through `base.update`, so every change lands in
`event_log` and reverts through `u` / `R` / the web rail. The only on-disk
change is `contact.active` and `team_member.active` moving from `1` to `0` on
rows the user names one at a time, each in its own revertible batch.

There is nothing here that needs a backup taken first, which is itself a
reason to prefer D2's answer over any of the alternatives.

## Build order

1. `services/contacts.py`: `retire`, `restore`, `already_retired`,
   `retire_consequences`, `Retirement`. Tests modelled on
   `tests/test_contact_remove.py` — the batch, the `is_primary` clear, the
   refusals, the attendee list *keeping* the person.
2. Display: the marker on attendee lists, search titles and MCP reads (D6);
   the count line and toggle (D7).
3. Surfaces: TUI `R` + hint + `test_dead_keys`; web route + parity entry;
   MCP tools + `_EDIT_REDIRECTS`; the two refusals in D9.
4. Lift the team half into `services/team.py`, add the cascade pre-check
   (D5), and fix the team screen's silent absence (D7).
5. Markets: pass a status filter at `tui/screens/markets.py:157-180`,
   `forms/entities.py:308` and `forms/entities.py:551-553`, with the same
   count-and-toggle treatment. Separable from 1-4 — see the open decisions.

Steps 1-3 are the slice that makes retirement real for contacts. Step 4 is what
makes it a mechanism rather than a one-off, and repays the precedent's debt.
Step 5 is a different bug that this document happens to have found.



---

## Verification report (independent adversarial pass, 2026-08-18)

**Verdict: needs-revision.** The document's research is unusually good — 92 of 101 citations resolve to the right line saying the right thing, all nine CLAUDE.md rulings check out, and its three corrections to the ROADMAP entry are all verified true (member_deactivate really is MCP-only, only contact and team_member carry an `active` column, and the team screen really does make a deactivated colleague vanish with no way back). It is not safe to build from as-is: the reference the doc names twice as "the shape contact_retire should copy" (mcpserver.py:1534-1556) points at _member_deactivate's body rather than _contact_remove (1035-1057), and two load-bearing arguments are contradicted by the code they cite — the is_primary invariant claim is refuted by repo/contacts.py:58-64 (set_primary reads active_only=False, so a retired primary IS cleared and two primaries cannot occur), and D2's self-declared "decisive fact" about CHECK-constraint rebuilds and FTS corruption argues against an alternative nobody proposed (an added contact.status column is a plain ALTER TABLE ADD COLUMN, CHECK included, verified against sqlite 3.45.3). The weakest point overall is D8's key choice: `R` already means "revert this change / refresh" app-wide (navigator.py:62, :256, :1239-1247) and the document itself uses `R` that way in D5.


### Citations that did not check out

- **`mcpserver.py:1534-1556`** — claimed: _contact_remove is a thin MCP wrapper that resolves names and reports, delegating the rules to services.contacts.remove — the shape contact_retire should copy (cited twice, in D3 and D8)
  
  *Actually:* That range is inside _member_deactivate (def at 1521): 1534-1540 is the still-on-assignments refusal, 1541-1543 builds the 'deactivated <name>' summary, 1545-1555 is the cascade batch. _contact_remove is at mcpserver.py:1035-1057. The 'shape to copy' reference points at the body of the very function the document elsewhere argues must NOT be copied verbatim.

- **`ROADMAP.md:100-102`** — claimed: The entry lists contacts, markets, projects, placements and opportunities as five plausible candidates
  
  *Actually:* 100-102 is the second open question ('One mechanism or several? A shared active convention... per CLAUDE.md every stage/status/type classification is supposed to be a ListDefinition.WellKnown list'). The five candidate records are at ROADMAP.md:98-99.

- **`migrations/001_initial.sql:164-166`** — claimed: opportunity carries closed_at and outcome alongside stage — a complete close-out already modelled
  
  *Actually:* 164-166 is 'updated_at TEXT NOT NULL,' / 'deleted_at TEXT' / ');'. closed_at is line 160, outcome (with its own CHECK) line 161, loss_reason 162. The claim is true; the cited lines do not show it.

- **`forms/entities.py:79`** — claimed: org_form declares Field('status','status','select',_STATUS) — so a market can be set dormant today
  
  *Actually:* Line 79 is Field("owner", "owner", suggestions=owner_sugg). The status select is line 82. (The underlying claim holds: org_form spans 65-101 and org_form_initial_profile at 123-130 delegates to it.)

- **`tui/screens/navigator.py:425`** — claimed: the contacts tables already have a leading glyph column carrying ★ for primary (account.py:636, navigator.py:425)
  
  *Actually:* navigator.py:425 is ('tasks', len(tasks_repo.open_tasks(conn, org_id=org_id))) inside the per-account count tuple. The ★ glyph in the navigator contacts group is at navigator.py:774. account.py:636 is 'table.add_row(' — the ★ is 637.

- **`tui/screens/account.py:78-82`** — claimed: TAB_HINTS['tab-contacts'] names a/e/p/D/I/w/u — the hint line a retire key must be added to
  
  *Actually:* 78-79 are the last two lines of TAB_HINTS['tab-overview']. tab-contacts is 80-84, and the cited range stops before line 83-84 ('[b]u[/b] undo'). Content claim is correct; the range is wrong at both ends.

- **`ROADMAP.md:99-107 (document header)`** — claimed: It answers the three open questions in ROADMAP.md:99-107
  
  *Actually:* The 'Open, needs a decision before it is specced' header is line 97; the three bullets run 98-106. Line 99 is a mid-bullet continuation and 107 is blank. The correct range is 97-106.

- **`mcpserver.py:1540 (open-decisions list)`** — claimed: The shipped MCP summary says 'deactivated' (mcpserver.py:1540)
  
  *Actually:* 1540 is the closing paren of the raise ValueError. The summary is line 1541: summary = f"deactivated {member.name}".

- **`migrations/006_projects.sql:14`** — claimed: project.status already includes 'completed' and 'cancelled' (rendered in the table as schema evidence, in parallel with placement's and opportunity's CHECK constraints)
  
  *Actually:* Line 14 is: status TEXT NOT NULL DEFAULT 'planned',  -- planned/active/completed/cancelled. There is NO CHECK constraint on project.status — the vocabulary is a comment, enforced only by models.PROJECT_STATUSES (models.py:243). The table presents it as equivalent evidence to placement (:110-111 CHECK) and opportunity (:151-153 CHECK); it is not, and D2's CHECK-rebuild cost argument does not apply to project at all.


### Claims challenged (even where the citation resolved)

- **[CRITICAL]** D4 step 3 and the open-decision on is_primary: leaving is_primary=1 on a retired row 'quietly falsifies set_primary's exactly one primary per org invariant (repo/contacts.py:58-64)', with cost-if-wrong 'the next set_primary appears to work and leaves two.'
  
  *Evidence:* repo/contacts.py:58-64 — the line the claim cites — is what refutes it. set_primary iterates for_org(conn, contact.org_id, active_only=False), so a RETIRED row (alive, active=0) is still returned and its is_primary IS cleared. Exactly one primary survives; two primaries are impossible. The removal case is different for a reason that does not transfer: a soft-deleted row is filtered by base.alive() unconditionally (repo/contacts.py:25) and base.update is alive-gated via base.get (repo/base.py:165-167), so it is genuinely unreachable — which is precisely what services/contacts.py:198-201 says. The decision to clear is_primary on retire is still defensible, but on display grounds only: services/onboarding.py:70 and tui/screens/account.py:630-637 both read is_primary off an active_only=True list, so the account would show 'no primary' while a retired row holds the star. The document states a broken-invariant justification twice and both statements are contradicted by the code.

- **[CRITICAL]** D2 reason 3, explicitly labelled 'the decisive fact': choosing a status field instead of the boolean means changing a CHECK constraint (001_initial.sql:18-19), which SQLite can only do by rebuilding the table, which reassigns rowids and corrupts the external-content FTS5 indexes (:272-312) — a destructive migration in exchange for a naming preference.
  
  *Evidence:* No alternative on the table requires a CHECK change. (a) For contacts, 'retirement as a status value' means a NEW column on contact — ALTER TABLE ADD COLUMN, additive, no rebuild, no rowid change, and SQLite accepts a CHECK on an added column (verified: sqlite 3.45.3 accepts ALTER TABLE t ADD COLUMN status TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current','retired'))). (b) For markets, D1 has already ruled that 'dormant' exists at 001_initial.sql:19 and no new value is needed. The only scenario the FTS/rebuild argument covers is adding a new value to org.status, which the document itself has excluded. The decision is probably right, but its self-declared decisive reason argues against nothing that was proposed; reasons 1 (orthogonality) and 4 (revert machinery, which I verified — services/batches.py:190 _cell stringifies the current cell so "0" == "0" compares clean, and SQLite INTEGER affinity converts the written "0" back to integer 0) are what actually carry it.

- **[IMPORTANT]** D10 settles the vocabulary question itself ('retired' in every summary the user reads; member_deactivate's summary changes to 'retired <name>'), files it under decisions the drafter made, and says it is 'recorded here so nobody fixes it later.'
  
  *Evidence:* docs/superpowers/specs/2026-08-14-mcp-team-edits-design.md:36-43 records this as Grant's own call, in a Decisions (Grant, 2026-08-14) block: 'the batch summary should read "deactivated Sarah Chen", not "edited team_member.active on Sarah Chen"'. D10 reverses a written Grant ruling and never names it. Under this repo's convention that is a blocked_on_grant item, not a drafter decision — and the draft's own cost-if-wrong ('a string sweep… cheaper before the tests are written') is the argument for asking now.

- **[IMPORTANT]** D8: 'TUI, account Contacts tab: R for retire/restore. R is unbound on AccountScreen (tui/screens/account.py:424-470) and D is already remove — the two must not share a key.'
  
  *Evidence:* The premise resolves (no R in AccountScreen.BINDINGS) but the conclusion does not follow. R is already taken app-wide with a settled meaning: navigator.py:256 binds it, and navigator.py:1239-1247 makes it dual-role — its own docstring reads 'R is dual-role… revert on a focused MCP CHANGES table, plain refresh everywhere else' — with the hint at navigator.py:62 reading '[b]R[/b] revert this change'. CLAUDE.md:43 and the draft's own D5 both use R to mean 'revert the batch'. Binding R to retire on the account screen gives one shift key three meanings, one of which is the undo path this very feature depends on.

- **[IMPORTANT]** D6: 'MCP reads. mcpserver.py:1007, :1047, :1756, :1894 all read with active_only=False and none of them reports the flag. They must include "active": false, or the assistant will confidently email someone who left.'
  
  *Evidence:* All four citations resolve, but all four are name-resolution paths inside WRITE tools: 1007 is _contact_add's duplicate guard, 1047 _contact_remove's target lookup, 1756 _edit_target's, 1894 enrich_field's. Three of the four expose names only inside a refusal string ('have: [names]'); none returns a contact record in a success payload. There is no MCP tool that lists an account's contacts at all — the full tool set is mcpserver.py:119-608 and contains none (team_roster at :219 does report member.active, mcpserver.py:1392, which is the shape the draft is generalising from). The named harm reaches the assistant through `search` (mcpserver.py:136 → repo/search.py:47-61), which the draft covers in the bullet above. As written the prescription is aimed at four call sites that cannot cause it.

- **[IMPORTANT]** D1 and build step 5: 'both market pickers offer every market — the submission form (forms/entities.py:308) and the RFI form (:551-553). The missing half for markets is the filter, which is four call sites, not a new column.'
  
  *Evidence:* orgs.list_orgs(conn, kind='market') has more unfiltered call sites than that: tui/screens/markets.py:94 (the parent/master picker), markets.py:234 (the merge-target picker), mcpserver.py:1968 (_resolve_market's nearest-market hint), services/book.py:46, tui/screens/account.py:2350, and sync.py:397. sync.py:397 is the carrier→market matcher — filtering dormant markets there would fail to match and create a duplicate market org, which is exactly the argument the document itself makes for leaving imports/matcher.py:36-40 at active_only=False. 'Four call sites' both undercounts and would be wrong if applied uniformly.

- **[IMPORTANT]** Build order step 1: tests modelled on tests/test_contact_remove.py, including 'the attendee list keeping the person'.
  
  *Evidence:* That test passes for an adjacent reason. Asserting the retired contact still appears in interactions_repo.attendees() is satisfied by a retire() that never writes active=0 at all — attendees (repo/interactions.py:74-84) filters only on base.alive('c'), which retirement never touches. Named mutation that makes the test fail: add 'AND c.active = 1' to the WHERE at repo/interactions.py:79. Named mutation it will NOT catch: deleting the active=0 write from retire(). It is load-bearing only when paired with an assertion in the same test that the contact is absent from contacts_repo.for_org(conn, org.id) at the default active_only=True — the removal suite does exactly this pairing at tests/test_contact_remove.py:64-65.

- **[MINOR]** D8: 'tests/test_dead_keys.py is the arbiter that hint and binding agree.'
  
  *Evidence:* It arbitrates one direction. tests/test_dead_keys.py:105-106 asserts _advertised(TAB_HINTS[tab]) - _live_keys(app) is empty — every key a hint NAMES must be bound. There is no reverse assertion, so it cannot fail on a bound R that does nothing, on R shadowing the navigator's revert/refresh meaning, or on a retire binding missing from the hint. The only mutation that reddens it is adding R to TAB_HINTS['tab-contacts'] without a Binding. tests/test_web_parity.py is the stronger guard here and the draft cites it correctly (web/parity.py:6-8 and the test at tests/test_web_parity.py:49-57 confirm a new AccountScreen action goes red until listed).

- **[MINOR]** D4: 'restore() is the inverse and sets active = 1. It does not restore is_primary — the batch revert is the path that puts the star back.'
  
  *Evidence:* The escape hatch is narrower than stated. services/batches.py:253-255 refuses a per-field revert when the current value differs from what the batch wrote, and 247-252 conflicts the whole entity if it was deleted since. So retire → restore → (any later write to is_primary on that org) leaves the star unrecoverable by R, and the user must press p. The draft's own removal precedent already documents this qualification for undo (services/contacts.py:118-133); a retirement whose forward path deliberately clears the flag should carry the same qualified sentence in retire_consequences(), not only in the design doc.


### Decisions the draft left open

- **Does the batch summary read "retired Dana Cruz" or "deactivated Dana Cruz"? The shipped MCP summary says "deactivated" (mcpserver.py:1540), and it is what the user sees in the changes rail, under `u` and under `R`.**
  - Recommendation: "retired" everywhere the user reads it; the MCP tool NAMES member_deactivate / member_reactivate stay as they are, because renaming a tool the assistant has learned costs vocabulary and buys nothing a description cannot. New tools are contact_retire / contact_restore. D10 records the deliberate mismatch so nobody "fixes" it later.
  - Cost if wrong: A string sweep across summaries, hint lines and templates plus their tests. Cheap now, more expensive once the retirement test suite pins the sentences.

- **Does retiring a contact clear is_primary, the way removing one does?**
  - Recommendation: Yes, first and inside the same batch, promoting nobody — identical to services/contacts.py:197-204. Otherwise for_org(active_only=True) shows the account with no primary while the retired row still holds is_primary=1, quietly falsifying set_primary's "exactly one primary per org" (repo/contacts.py:58-64).
  - Cost if wrong: The account silently shows no primary contact while one exists in the data; the next set_primary appears to work and leaves two. Same class of bug the removal design was written to prevent.

- **Should restore() put is_primary back?**
  - Recommendation: No. Restoring sets active=1 only. Promoting someone is a judgment, and the batch revert is the path that puts the star back — which is exactly the qualified promise services/contacts.py:118-133 already makes about undo after a removal.
  - Cost if wrong: A user retires and restores the primary contact and has to press `p` once. Low cost, and the alternative risks two primaries after an intervening promotion.

- **Does the cascade pre-check for team retirement land in this work, or is the BLAST_CAP refusal left as-is?**
  - Recommendation: Land it with the team lift (D5). _member_deactivate(cascade=True) at N>=250 assignments raises "narrow it and try again" (db.py:60-64), advice a cascade caller cannot act on. Count team.for_member first and refuse with the real move. Do not raise the cap.
  - Cost if wrong: A rollback plus a misleading sentence at a scale nobody in this book will reach. Genuinely low — this is one guard, not a redesign, and worth having only because it is three lines.


### Needs Grant

- Scope of the first slice: contacts only (build steps 1-3), or contacts plus lifting member_deactivate/member_reactivate out of mcpserver.py into services/team.py with a TUI control (step 4)? The mechanism is proven against team_member either way — the question is whether the team half ships now. I'd do both: leaving it means the precedent this design copies stays MCP-only and keeps failing its own "never silently absent" rule (tui/screens/team.py:130 hides retired colleagues with no toggle and no restore). But it is roughly double the work, and it is a scope-versus-time call.

- The market dormant filter (build step 5) is a separate bug this design found, not part of retirement: org.status already accepts 'dormant', org_form already writes it (forms/entities.py:50,79), and nothing filters on it — the markets list and both market pickers offer dormant markets (tui/screens/markets.py:157-180, forms/entities.py:308, forms/entities.py:551-553). Four call sites plus a count line. Fold it into this work, or split it into its own ROADMAP entry?


### Corrections this draft makes to the ROADMAP entry

- ROADMAP said: "The precedent already exists and should be copied, not reinvented. member_deactivate (see CLAUDE.md): it refuses while assignments are live, cascade=True removes them all in one revertible batch, and it is deliberately NOT a field edit."
  - Code says: Every behavioural claim is accurate, but the precedent exists ONLY on MCP. member_deactivate is mcpserver._member_deactivate; there is no deactivate or reactivate control anywhere in tui/, web/, services/ or repo/ (grep across all four returns only docstring mentions). Copying it as written puts the new rules in mcpserver.py, where the TUI and web cannot reach them — the exact failure repo/team.py:15-23 records having already been paid for once, and the one services/contacts.py:13-16 was written the same day to avoid. Copy the shape, not the location. (`mcpserver.py:1521-1572 (the whole implementation); services/contacts.py:13-16; repo/team.py:15-23`)

- ROADMAP said: "Which records? Contacts, markets, projects, placements, opportunities all plausibly want it; each has different downstream references."
  - Code says: Three of the five already have retirement, in a lifecycle field the pipeline and attention windows already read: placement.status includes 'lapsed' (migrations/001_initial.sql:110-111); opportunity.stage includes 'won'/'lost' and the table carries closed_at and outcome (:151-153,164-166); project.status includes 'completed'/'cancelled' (migrations/006_projects.sql:14). Markets already have org.status='dormant' (:18-19), writable today through org_form (forms/entities.py:50,79) and filtered by nothing. Only contacts and team members lack the concept, and they are the only two tables that already carry an `active` column. It is two records plus one filter bug, not five candidates. (`migrations/001_initial.sql:18-19,110-111,151-153,164-166; migrations/006_projects.sql:14; migrations/001_initial.sql:73; migrations/005_team.sql:13`)

- ROADMAP said: "How does it show? A retired row must be visibly retired wherever it still appears (history, attendee lists), not silently absent."
  - Code says: The precedent the entry says to copy already violates this. tui/screens/team.py:130 calls team.list_members(conn) with active_only defaulting to True (repo/team.py:45-48), so a deactivated colleague disappears entirely from the only TUI screen that lists colleagues — no toggle, no marker, no count. services/team.py:27 drops them from the specialist search too, and member_reactivate exists only as an MCP tool, so there is no way back from any surface a person uses. The requirement is right; the precedent fails it and would carry the failure forward. (`tui/screens/team.py:130; repo/team.py:45-48; services/team.py:22-32; mcpserver.py:1558-1572`)

- ROADMAP said: "per CLAUDE.md every stage/status/type classification is supposed to be a ListDefinition.WellKnown list — so decide early whether 'retired' is a status value or a separate boolean"
  - Code says: That rule is not in CLAUDE.md — it is the user's global instruction, and this repo already ruled it inapplicable in writing: docs/superpowers/specs/2026-08-13-rfi-tracking-design.md:110-115 states it is a Swift-project rule and that bookkit's own convention (controlled-but-extensible tuples in models.py, the TEAM_ROLES pattern) governs. Nothing named ListDefinition, WellKnown, ListValuePicker or StatusPill exists anywhere in src/. The status-versus-boolean question is still real and still worth deciding — but it turns on the FTS5/CHECK-constraint migration cost and on retirement being orthogonal to status, not on that rule. (`docs/superpowers/specs/2026-08-13-rfi-tracking-design.md:110-115; CLAUDE.md:129-130; migrations/001_initial.sql:272-312`)
