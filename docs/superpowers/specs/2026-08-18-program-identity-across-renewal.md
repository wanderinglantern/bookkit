<!-- ROUND 2 DRAFT. Research verified; DESIGN NOT APPROVED. Read the bottom before building. -->

> **Status: research verified — design NOT approved** (2026-08-18, round 2 of 2).
>
> Drafted against the code, rejected by an adversarial pass, revised, and rejected again.
> **58 citations re-checked · 8 still failed ·
> 8 claims challenged · 7 regressions ·
> 8 decisions reversed from round 1.**
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


# A program across a renewal boundary — design

Date: 2026-08-18 (revised after independent verification)
Status: **for approval.** Nothing here is implemented. This settles the model
only; it deliberately does **not** contain an implementation plan.

It is bound by `CLAUDE.md` — where this document and `CLAUDE.md` disagree
unintentionally, `CLAUDE.md` wins and this document is wrong.

Scope note: this touches both repos. `towerkit` is a path dependency
(`pyproject.toml:34`), and two decisions below (D6, D8) change towerkit.
Everything else is bookkit-side.

> **Revision note.** A first draft of this spec placed the file-write guard in
> `sync.write_through` and defined dropped cover as "absent from the
> successor". Both premises were false and both are reversed below (D7, D5).
> Three further arguments the draft leaned on have been withdrawn rather than
> re-cited: the "permanently overdue" claim about `renewal_on`, the claim that
> `compare_programs` already provides the composition primitive, and the
> git-diff argument under D1. What survives — and what the whole spec rests on
> — are two findings reproduced by execution: `clone_as_renewal` leaves layer
> periods stale, and a single file cannot hold two policy years of one layer.

---

## Why this exists

Grant, 2026-08-18: a client with multiple renewal dates across multiple
policies. The program technically renews from a 2025 program to a 2026 one, but
he needs to keep seeing it as **one visual**. He tried to renew a single policy
through MCP; it could not renew one line, and he ultimately created a new
program instead.

What is genuinely missing is not a `renew_line` tool. It is an answer to **what
a program's identity is across time**, because without one there is no honest
way to say which of two files is "the program" while cover is half-2025 and
half-2026.

---

## What I verified before writing anything

### Reproduced by execution — the two facts that decide the ruling

1. **`clone_as_renewal` does not bump layer periods, so a renewed file is born
   expired.** It sets `clone.period` only
   (`towerkit/src/towerkit/model.py:174-182`); layers come across by
   `model_copy(deep=True)` with their old dates intact. Executed against the
   shape `seed.py:343-345` calls "the norm" (a 2025-09-01→2026-09-01 program
   with an IM layer ending 2026-06-03): the clone is a 2026-09-01→2027-09-01
   program whose IM layer still reads 2025-09-01→2026-06-03.
   `sync.line_ends` then returns `[('IM', 2026-06-03), ('GL', 2027-09-01)]` for
   the **new** file, `_renewal_on` returns 2026-06-03, and the renewal
   placement lands in the overdue bucket on the day it is created. Nothing
   refuses it: the only layer-period rule is `end > start`
   (`towerkit/src/towerkit/validate.py:212-218`), there is no containment check
   against the program period, and validating that file returns warnings only
   (`layer-unplaced`, `line-no-retention`) with `ok == True`.
2. **One file cannot hold two policy years of the same layer.**
   `_check_line_stack` is dollar-space only
   (`towerkit/src/towerkit/validate.py:278-334`): it requires the base layer to
   attach at $0 and each layer above to attach exactly at the one below's top,
   with no reference to `layer.period`. A file carrying both the expiring and
   the incepting primary GL validates as
   `line-overlap | gl: OVERLAP Primary GL 25→Primary GL 26 at $2,000,000 vs $0`
   — an error, not a warning. Since `write_through` validates before it dumps
   (`src/bookkit/sync.py:1033-1037`), and every other file-creating path
   validates too (see D7's table), that state is unreachable through bookkit.

### Corroborating evidence from the shipped corpus

`towerkit/programs/atomic-2026.json` and `atomic-2027.json` are already a
renewal pair, and already the exact shape this spec is about: a January program
carrying two Property layers on an April anniversary. Three things it tells us:

- The 2027 file's Property layers read `2027-04-01 → 2028-04-01`, i.e. the
  anniversary was carried forward one year. `clone_as_renewal` would have left
  them at `2026-04-01` (finding 1). **Whoever produced that file corrected the
  dates by hand — which is the default D8 proposes, written by a human.**
- Layer ids are identical across both files, and both carry the same six lines.
  Layer identity does survive a renewal in practice.
- Both files carry the **same** `program` string, `"Global Casualty & Financial
  Lines"`, with no year in it. So `_renewed`'s name match
  (`src/bookkit/services/renewals.py:38-49`) carries no information here at all;
  only the file *stem* does, via `_bump_years` (`src/bookkit/sync.py:640`). That
  is the concrete case for D2.

### What the ROADMAP entry got right, and the one thing that needs correcting

Holds, all re-opened:

- `Layer.period` is optional and falls back to the program period
  (`towerkit/src/towerkit/model.py:93`); the docstring gives the reason —
  "programs can carry several policy effective/expiry dates, so the period is
  per-layer, defaulting to the program period when absent" (`model.py:85-88`).
- `sync.line_ends` is at `src/bookkit/sync.py:923`, deriving each line's end as
  `min(layer.period.end if layer.period else program.period.end)` over covering
  layers (`sync.py:941-944`).
- `RenewalItem` carries `line_ends` per line (`renewals.py:32`) and
  `days_remaining` counts to `renewal_on` (`renewals.py:27, 114`).
- `sync.renew` is at `sync.py:618`, clones with `clone_as_renewal`
  (`sync.py:667`), bumps both placement period dates a year
  (`sync.py:651-652`), and refuses when next year's file exists
  (`sync.py:645-649`).
- `Line` has no period — `id`, `name`, `abbr`, `group` only
  (`model.py:47-53`). Any answer works on layers.

**The correction is to the code's own comments, not to the ROADMAP.** The entry
says `renewal_on` is "the earliest **live** line end, capped by the program
period." The word "live" is lifted verbatim from `renewals.py:33-34` ("the
earliest live line end when the placement is file-linked"), and there is **no
liveness filter anywhere**: `_renewal_on` returns `min(ends[0][1],
placement.period_to)` (`renewals.py:79-80`). Two comments are wrong about their
own code and should be fixed as part of this work:

- `renewals.py:33-34` promises a liveness filter that does not exist.
- `renewals.py:77-78` says "capped by the program period end" while the
  expression caps by `placement.period_to`.

For the population that has `line_ends` at all — file-linked, projected
placements — those two *are* the same value: `project()` writes
`period_to=program.period.end.isoformat()` on every projection
(`sync.py:262`), and every bookkit path that changes a placement period writes
through the file first. So the ROADMAP's wording about the cap is accurate;
only the promise of liveness is not.

The real attention defect is narrower and is what D9 fixes: once a line is
renewed in the successor **file**, the predecessor's `line_ends` still reports
that line's old end, so the predecessor keeps counting down to a date that no
longer needs attention. Suppression only arrives once the whole placement is
overdue, and then it is a name guess (`_renewed`, `renewals.py:38-49`).

### Incomplete in the entry, in ways that change the design

- *"There is no line-level or layer-level renewal anywhere."* True of renewal.
  **False of the layer period.** `sync.update_layer` accepts `period_from` /
  `period_to` (`sync.py:794-795`) and writes `layer.period`
  (`sync.py:813-819`); the TUI's layer form exposes both fields and passes them
  (`tui/widgets/entity_actions.py:197-198, 221-222`). The MCP tool
  `program_layer_edit` omits them from its signature (`mcpserver.py:398-412`)
  and `_program_layer_edit` never passes them (`mcpserver.py:1261-1281`). "It
  could not renew one line" is a property of **one surface**, not of the model
  or the service — the same capability-present-on-one-surface shape as the 33
  unbatched `FormModal` call sites (`CLAUDE.md`, 2026-08-15). There is also no
  MCP `renew` tool at all; `sync.renew` is reachable only from
  `entity_actions.py:159`.
- *"Time becomes a dimension of the tower diagram, which it is not today."*
  Per-layer periods are already renderable. `cell_dates` prints `layer.period or
  program.period` into each participant cell
  (`towerkit/src/towerkit/render/mpl_program.py:156-159`), and it is a saved
  render setting (`model.py:139`). A not-yet-real per-layer state also already
  renders dashed, keyed on `signed_bps == 0` (`mpl_program.py:126-128,
  168-194`). Time is not a *state* in the diagram; it is already a *label*.
- *"Dropped cover has to read as dropped, not as missing data."* Correct, and
  they are identical in the **data**, not just the diagram — but the cascade is
  narrower than it looks. `remove_line` strips the id from every `appliesTo`
  and removes only what is left with an **empty** `appliesTo`
  (`towerkit/src/towerkit/edit.py:94-115`, docstring at 95-98). A layer covering
  GL and AL survives the removal of AL with the id stripped out. So there is no
  tombstone **and** "dropped" is indistinguishable from "restructured", not
  only from "never entered". `line_ends` omitting a line with no covering
  layers is a *documented* rule, not an oversight — its own docstring says
  "Lines with no layers (TBD, unplaced) are omitted — there is no policy to
  expire" (`sync.py:927-929`). Someone decided that for unplaced lines; a
  dropped line was not considered.

### Also established, and load-bearing below

- `proj_layer` carries no period columns (`migrations/001_initial.sql:227-237`)
  and the projection never writes one (`sync.py:222-234`). There is no
  `proj_line` table at all. Everything period-aware re-reads the file —
  `layer_details` does exactly that (`sync.py:879-907`, period at 901-902).
- `placement` has no predecessor/successor column
  (`migrations/001_initial.sql:103-122`). `program_link` is keyed on path only
  (`001_initial.sql:219-224`) plus `source` from
  `migrations/002_program_link_source.sql`, which records *that* a link came
  from a renewal but not *from what*.
- `links.confirm` is an UPSERT whose `ON CONFLICT` clause overwrites `org_id`,
  `insured_name`, `confirmed_at` and `source` (`repo/links.py:17-24`), and
  `links.forget` deletes the row outright (`links.py:49-50`).
- The file format is closed: `additionalProperties: false` at every level of
  `towerkit/src/towerkit/schema/program.schema.json` (layer subschema
  included), `extra="forbid"` on the pydantic base (`model.py:43-44`).
  Forward-compatibility already has an established answer — see D6.
- `batches.revert` refuses any batch whose `tool` starts with `program_`
  (`services/batches.py:330-333`), with the reason in a comment at
  `batches.py:331-332`: "reverting the proj_* cache under an untouched file
  would be a lie." The sentence users see is factored into
  `program_file_refusal` (`batches.py:291-305`). `revert`'s own house rule,
  "`now` is a parameter, never the wall clock", is at `batches.py:323`
  (signature `316-318`).
- `program_files.capture` is called from exactly one place,
  `mcpserver.py:1196`, inside `_program_write` (`mcpserver.py:1174-1197`) —
  which reads the pre-image at `:1190` and therefore only works for edits to an
  existing file, never for a creation. `SNAPSHOT_KEEP = 20` per directory
  (`services/program_files.py:22`, pruned at `:67-75`).
- The web Program tab is still a stub
  (`web/templates/account/program.html:1-8`), so this lands before the
  HTML/SVG renderers of R66. The web layer already derives its batch the same
  way the TUI does (`web/routes/account.py:246`), so it will inherit the D7
  defect the moment `edit_layer` ships there — it is listed as planned in
  `web/parity.py:98`.

---

## The ruling

**A program file stays one policy period. The identity across time is an
explicit recorded edge between files, carrying how the successor was minted.
"One program" is a view composed at read time over that chain, always as of a
date.**

In the entry's terms: **(b) plus (c). (a) is rejected.**

### D1 — One file is one policy period, and a renewal never edits the predecessor

**A towerkit program file describes the cover as bound for exactly one policy
period.** A renewal creates a successor file; the predecessor is never mutated
by a renewal operation. Correcting a mistake in last year's placement is still
an edit to last year's file, and that stays legal.

*Reasoning.* This is not a preference; it is what the validator already
enforces, reproduced by execution above: a file holding both years of one layer
is refused as `line-overlap` (`validate.py:278-334`). Making one file span a
renewal boundary means teaching `_check_line_stack` about time — every
gap/overlap/base check becomes "per line, per instant" — and that check is the
thing that catches real placement errors.

**Rejected: one file that spans both years.** The validator cost above is the
whole argument. It also makes `Program.period` meaningless, and `Program.period`
is what `sheet_title` (`towerkit/src/towerkit/soi.py:170-175`),
`default_filename` (`soi.py:178-183`), `_renewal_on`'s cap and the projection's
`period_from`/`period_to` (`sync.py:261-262`) all read.

*(The first draft also argued this from the frozen canonical key order. That
argument is withdrawn: the comment at `model.py:225-226` says key order is
frozen so arbitrary TUI reformatting does not make diffs unreadable — "between
renewal years" is the example, not the purpose, and nothing about key order
stops one file holding two years.)*

**Cost if wrong.** If Grant genuinely works one continuous document per client
rather than a document per year, every read path becomes a filter and the file
grows without bound. Reversing D1 later means a real format migration and a
rewrite of `_check_line_stack`. This is the single hardest decision here to undo.

### D2 — The continuity thread is a recorded edge in bookkit, carrying provenance

**A new additive table:**

```sql
CREATE TABLE program_lineage (
    successor_path   TEXT PRIMARY KEY,
    predecessor_path TEXT NOT NULL,
    minted_by        TEXT NOT NULL,   -- 'renewal' | 'scaffold' | 'import' | 'user'
    recorded_at      TEXT NOT NULL
);
```

`sync.renew` writes it with `minted_by='renewal'` at the moment it creates the
successor — it already calls `links.confirm(..., source="renewal")` right there
(`sync.py:669`) and already knows both paths. Every other way a successor comes
into existence (D7's table) records its own `minted_by`, or records nothing and
is therefore knowably not a lineage.

*Reasoning.* Three properties decide this. It is an **additive migration**, the
only kind this project has done (`CLAUDE.md`). It **touches no JSON**, so every
file on Grant's disk stays valid — which matters because the format is closed
on both fronts (`additionalProperties: false` in the schema, `extra="forbid"` at
`model.py:44`). And it **replaces a guess with a fact**: today the only link is
`_renewed`'s match on `program_name` or `_bump_years(program_name)`
(`renewals.py:44-47`) plus `_bump_years` on the file stem (`sync.py:640`). The
shipped `atomic-2026`/`atomic-2027` pair proves how thin that is — the program
name is identical in both files and carries no year at all, so the name match
succeeds by coincidence, and a rename at renewal (which brokers do) breaks the
stem convention silently: the predecessor never leaves the overdue list and the
successor looks like a brand new placement.

`minted_by` is the half the first draft did not have, and D5 cannot be stated
without it.

**Rejected: two columns on `program_link` instead of a table.** Smaller, and it
was the first draft's choice. Rejected on two mechanics I re-read:
`links.confirm` is an UPSERT whose `ON CONFLICT` set-list overwrites `source`
(`links.py:20-22`), so re-confirming a link would silently rewrite the
provenance the dropped-cover read depends on; and `links.forget`
(`links.py:49-50`) deletes the row, so "this file isn't this org's" would
destroy the lineage as a side effect. Both are fixable with care, and care is
exactly what this project has repeatedly found missing a year later.

**Rejected: (a), a first-class program-lineage entity.** It has two homes and
both are bad. In SQL it becomes an authority the files do not have, which is
what `proj_*` is forbidden from being (`CLAUDE.md`: "towerkit JSON files are
the sole authority for program structure; proj_* tables are a rebuildable
cache"). In the file it is a closed-format change for a fact about the
*relationship between* files. Either way it adds an id, a ref, event_log
fields, undo semantics, and a second thing that can be wrong — a file filed
under the wrong lineage.

*`program_lineage` is not that entity, and the distinction is load-bearing: it
has no identity of its own, nothing is "filed under" it, and it names files by
the same path key `program_link` already uses. It is an edge, in the same
category as `event_log` and `program_link` — audit of how the book came to be,
not authority over program structure. `proj_*` stays rebuildable from the
files.*

**Rejected: keeping the name convention and hardening it.** `_bump_years`
rewrites *every* four-digit year in a string (`sync.py:603-605`); the
convention cannot be made reliable without becoming a parser, and a parser
that is right 95% of the time is worse than an edge that is right always.

**Cost if wrong.** Low and recoverable. If the edge turns out to want more than
one predecessor (two programs merging into one), `successor_path` stops being
the primary key — a second additive migration, no data loss.

### D3 — Every composed read takes an as-of date. There is no default view without one

**No function that composes a chain may be called without an explicit as-of
date.** Internal views default it to today; exports require it to be chosen and
printed. `as_of` is a parameter, never the wall clock — the same rule
`batches.revert`'s `now` already follows (`services/batches.py:323`).

*Reasoning.* "One visual" and "as of when" are the same question. As of
2026-08-18 in Grant's client, GL is 2026 cover and IM is 2025 cover; as of
2026-12-01 both are 2026. There is no single true picture, only a picture at an
instant, and a renderer that does not take the instant will invent one. This is
also the only rule that makes the internal and client-facing views the same
function — see *Visualisation*.

**Cost if wrong.** If Grant wants a "whole chain, all years" overview rather
than a point-in-time one, that is an additional view, not a replacement — D3
costs an unused parameter. Getting it wrong the other way (composing without an
as-of) is the entry's own "document that misleads."

### D4 — The renewal unit is a layer's period, moved forward in the successor file

**"Renewing a line" means: in the successor file, the layer covering that line
gets its real 2026 period and terms.** The predecessor is untouched (D1). The
successor exists from the first renewal onward, not from the last.

*Reasoning.* This falls out of D1 and the existing shape. `Line` has no period
(`model.py:47-53`), so the operation was always going to be on layers. The
successor already exists eagerly — `sync.renew` creates the whole clone up
front (`sync.py:667-668`) — so nothing new has to be invented to hold a
partially renewed year.

**Rejected: editing the expiring layer's period in place.** It is reachable
today (`sync.update_layer`, `sync.py:813-819`) and it is the wrong primitive:
it overwrites the expiring terms, so what the client actually had in 2025-26
becomes unreadable. On the TUI surface it is also unrevertible — see D7.

**Cost if wrong.** If a broker really does want to extend one policy rather
than renew it (a mid-term extension), that is an edit to the predecessor's
layer period, which stays legal under D1. The two operations must be named
differently on every surface or they will be confused; that naming is part of
"what must be true" below.

### D5 — Dropped, renewed and pending are answered by the pair, **only when the lineage says the successor was cloned**

**Dropped cover is: present in the predecessor, absent from a successor whose
`minted_by` is `'renewal'`. Against any other successor the composed read
refuses to answer — it reports "not comparable: this year's file was built
independently" rather than asserting a drop.**

*Reasoning, and what changed.* The first draft defined dropped as "present in
the predecessor, absent from the successor", justified by "the successor is
always born as a full clone". **That is not true of today's code and cannot be
made true.** `sync.py:666` gates the clone on `new_path is not None and program
is not None`, so a placement with no `program_path` renews with no file at all
(`sync.py:630`). Three other paths mint program files from scratch:
`scaffold_program` (`sync.py:711-739`), `imports/commit.py:165` (from a pasted
draft), and towerkit's own editor writing a file bookkit later adopts. Any of
those can produce the file that is in fact next year's program, and against
such a successor "absent" means nothing — which is exactly how a diagram ends
up asserting that a client lost cover they still have.

Making renew the only way to mint a successor was considered and **rejected**:
a client's 2026 program routinely arrives as a pasted schedule, and forbidding
`imports/commit` from producing next year's file would mean the broker cannot
record it at all. The right move is the house rule — **surface, don't guess**,
the same rule `program_files.restore` already follows when the file changed
under it (`services/program_files.py:60-63`) and `batches.revert` follows when
a field moved.

So the distinction is recoverable from the pair **plus** D2's `minted_by`, and
from nothing less. That is why D2's edge carries provenance and why the two
decisions cannot be separated.

**The composition primitive has to be written; it does not exist.** The first
draft claimed `compare_programs` "already does exactly this shape of read" and
"already has the vocabulary". The vocabulary exists — `NEW` / `RENEWED` /
`LAPSED` at `towerkit/src/towerkit/compare.py:15-17` — but the primitive
answers a different question and cannot be reused:

- `DeltaRow` (`compare.py:20-31`) has no line, no `applies_to`, no period. It is
  keyed `(carrier, layer_id)` only, so "this LINE was dropped" is inexpressible.
- `_cells` iterates `layer.participants` (`compare.py:65-83`), so a layer with
  **no participants contributes no rows at all** — and a layer with no
  participants is precisely what towerkit calls pending ("To be placed",
  `soi.py:20-22`; dashed at `mpl_program.py:126-128`) and precisely what an
  unrenewed `PROPOSED` layer will be under D6. Compare a predecessor against a
  successor whose unrenewed lines are still to-be-placed and those lines are
  **invisible, not `LAPSED`.**

The new primitive is keyed on `(line_id, layer_id)` and reads layers whether or
not they carry participants. `compare_programs` stays as it is — it answers a
carrier/premium question and answers it well.

**Cost if wrong.** If `minted_by` is recorded wrongly (a renewal that was
actually hand-built, marked `'renewal'`), a drop is asserted that did not
happen — the diagram tells a client they lost cover. That is why `minted_by` is
written by the code that mints, never by a user, and why the refusal case is
the default rather than the exception.

### D6 — `Layer` gains an optional `placement`, exactly as it has an optional `period`

**`Layer.placement: Placement | None = None`, defaulting to the program's when
absent.** A half-renewed program is then: `Program.placement = PROPOSED`, the
renewed GL layer `BOUND`, the unrenewed IM layer `PROPOSED` (by fallback). This
is the one format change in this document.

*Reasoning.* It cannot be derived. A layer carried forward untouched and a
layer renewed as-expiring with the same carrier at the same share are
byte-identical; only intent separates them, and intent is not in the data. Nor
can the dates carry it — whatever `clone_as_renewal` does to layer periods
(D8), it does uniformly, so the period cannot also mean "someone has worked
this one."

The reason to accept the format change rather than fight it: **this is not a new
kind of field.** `Layer.period` exists for precisely this reason and says so —
"programs can carry several policy effective/expiry dates, so the period is
per-layer, defaulting to the program period when absent" (`model.py:85-88`). A
program can equally carry several placement states. This is `Program.placement`
(`model.py:149`) moved down one level for the same reason `Program.period` was,
and it inherits the same fallback shape and the same optionality.

**The fallback gives D9 its safe default for free.** `clone_as_renewal` sets
`clone.placement = PROPOSED` (`model.py:181`). A cloned layer with
`placement=None` therefore reads `PROPOSED`, and under D9 a `PROPOSED` layer
never suppresses an attention item. A fresh clone suppresses nothing without
any extra rule being written.

**Forward compatibility uses the pattern this codebase already established, not
a schema version.** `program_to_jsonable` emits `soiSchematic` and `statutory`
**only when set**, with the reason stated twice in comments
(`model.py:273-276`, `311-314`): "untouched programs re-save byte-identically,
and older towerkit wheels only reject files that USE the feature." `_ordered`
already drops `None` values (`model.py:246-250`), so a `None` default gets this
for free — **and the default must be `None`, not `PROPOSED`, or every existing
file gains a key on its next save.** *(The first draft proposed versioning
`SCHEMA_ID` alongside. Dropped: no reader branches on it today, so it would be
a version nothing reads, and the emit-when-set pattern is the stronger answer.)*

**The edit surface is four coordinated changes, not one field:**

| Change | Where |
|---|---|
| the field itself | `towerkit/src/towerkit/model.py:82-100` (`Layer`) |
| canonical key order | `model.py:232-236` (`_LAYER_KEYS`) |
| the hand-written layer dict | `model.py:290-330` (`program_to_jsonable`) |
| the layer subschema | `schema/program.schema.json`, layer `properties` (it is `additionalProperties: false`) |

`program_from_jsonable` needs no change: the key is a plain name with no alias
and no share conversion (`model.py:377-394`). Missing `_LAYER_KEYS` fails loudly
rather than silently — `_ordered` raises `RuntimeError` on a model field absent
from the canonical order (`model.py:246-249`).

Derived state stays derived: "pending" already means `signed_bps == 0` and
already renders dashed (`mpl_program.py:126-128`), and `carrier_text` already
prints "To be placed" (`soi.py:20-22`). A `PROPOSED` layer reuses that
treatment; it does not get a new one.

**Rejected: recording renewal state in `Layer.notes`.** Freeform fields that
secretly carry state are the `carrier_alias` / `merged_from` landmine
(`CLAUDE.md`; the guard is `repo/base.py:80-95`). Declare the name.

**Rejected: recording it in bookkit alongside the edge (D2).** It is a fact
about a layer, and the file is the sole authority for layer facts. Splitting it
into SQL makes `proj_*` authoritative for one column, which is the rule this
project protects hardest.

**Cost if wrong.** Highest-cost item here after D1. If `Layer.placement` is the
wrong granularity — if renewal state is really per *participant* — then the
field is in the wrong place and files written with it need rewriting. This is
the strongest candidate in this document for a wrong call and the one I would
most want checked against the real client (see *Open decisions*).

### D7 — The file-write guard goes where the writers converge, and the guard is a recorded fact, not a name

**Reversed from the first draft, which put this in `write_through`. That was
wrong and would have shipped a guard with a hole in it.**

`dump_program` is called from **four** places in bookkit, not one:

| call site | kind | validates first? | batched today? | pre-image today? |
|---|---|---|---|---|
| `sync.py:1037` (`write_through`) | edit | yes, `sync.py:1033-1035` | only via MCP's `_program_write` | only via MCP |
| `sync.py:668` (`renew`) | **create** | source only, `sync.py:635-639` | **no** — `entity_actions.py:159` calls it bare | **no** |
| `sync.py:739` (`scaffold_program`) | **create** | yes, `sync.py:735-737` | **no** | n/a (nothing to restore) |
| `imports/commit.py:165` | **create** | yes, via `draft.to_program()` at `commit.py:157` | deliberately not (`CLAUDE.md`) | n/a (DB snapshot at `:160`) |

(`seed.py:312` is demo-data generation, not a user write.) The section comment
at `sync.py:745` scopes the truth precisely — "transactional program **edits**
(all via write_through)". Edits go through it; **creations do not.** Placing the
guard in `write_through` leaves `renew` — the single most important write in
this entire spec — unguarded and unsnapshotted, while the test passes. That is
the 2026-08-15 `push_form` failure repeated one function over.

**The decision, in three parts:**

**(a) One bookkit-side wrapper owns every `dump_program` call, enforced by a
convention test.** `services/program_files.py` grows a `write(...)` that is the
only place in `src/bookkit/` naming `dump_program`; `tests/test_conventions.py`
asserts it by AST walk, exactly like the existing no-raw-SQL and
no-await-in-a-transaction rules there. A guard that lives where the write is
*declared* is `CLAUDE.md`'s rule ("Guards on identity belong in repo/ where
every surface inherits them"); a grep-enforced chokepoint is how it stays true.

**(b) The refusal stops keying on a tool name.** `batches.revert` refuses a
batch by testing `batch.tool.startswith("program_")` (`batches.py:330-333`). The
TUI does not produce that prefix: `FormModal` derives its `BatchSpec` from the
form title (`tui/widgets/forms.py:92-93`), `BatchSpec.for_title` slugs the head
of the title (`forms/spec.py:68-77`), and the layer form's title is
`f"edit layer — {layer['name']}"` (`entity_actions.py:190`) — tool slug
`edit_layer`. The guard never fires. The commit runs inside that batch
(`forms.py:211-229`) and `write_through` → `project` → `placements.update` logs
real event rows (`sync.py:256-269`), so pressing `R` on a TUI layer edit reverts
the placement's cached totals **while the file keeps the edit** — the exact lie
the comment at `batches.py:331-332` says the guard exists to prevent. The web
surface derives its batch the same way (`web/routes/account.py:246`) and
`edit_layer` is on its roadmap (`web/parity.py:98`), so it will inherit the hole
verbatim.

Replace the string test with a lookup: the wrapper records `(batch_ref, path,
kind)` in a small additive table, and `revert` refuses any batch that has a row
there. A guard keyed on what actually happened cannot be evaded by naming a
form differently. `program_file_refusal` (`batches.py:291-305`) keeps its
sentence unchanged; only the predicate changes.

*Scope honesty: the inventory of what `R` currently corrupts is narrower than
the first draft claimed. A layer edit does not change `program.period`, so
nothing period-shaped is logged; what reverts is `total_limit` /
`total_premium`, and on a period-only layer edit the whole change lives in the
file and `proj_layer`, neither of which `revert` touches. The lie is still a
lie; on a period-only edit it is a quieter one.*

**(c) A creation gets a "did not exist" record, not a pre-image.** MCP's
`_program_write` reads the pre-image from an existing path
(`mcpserver.py:1190`), which is meaningless for `renew`, `scaffold_program` and
`imports/commit`. The wrapper records `kind='create'` and the file-side revert
for a creation **deletes the file it created** rather than restoring nothing.

`renew` and `scaffold_program` must additionally open a batch (`source='tui'`)
so there is something to refuse and something to key the record on;
`imports/commit` stays unbatched per `CLAUDE.md` (its DB snapshot at
`commit.py:160` is its rollback) but still registers its write, so nothing
pretends the file is revertible.

*Housekeeping: `services/program_files.py`'s docstring says "Snapshot-based
revert for MCP program-file writes" and the on-disk directory is
`.mcp-snapshots` (`program_files.py:23`). The docstring is now wrong and should
be fixed. **Do not rename the directory** — it holds Grant's existing snapshots
and a rename orphans them. Note the name is historical and move on.*

**Cost if wrong.** The chokepoint is the risk: if a future write genuinely
cannot go through the wrapper, the convention test becomes an obstacle someone
routes around. Mitigated by the wrapper taking `(program, path, kind, batch)`
and nothing else — it does not own validation, ordering or projection, so there
is nothing to route around. The risk of *not* doing it is concrete: shipping
layer-level renewal on the TUI means shipping a destructive write with no undo
and a misleading `R`.

### D8 — `clone_as_renewal` must carry layer periods forward

**Bump each `layer.period` by a year alongside the program period**, with the
same Feb-29 clamp `_plus_year` already applies
(`towerkit/src/towerkit/model.py:216-220`).

*Reasoning.* Proven by execution above: today it does not, so a renewed file's
staggered line reads as expired months before the new program incepts, and the
fresh placement is created directly into the overdue bucket. Bumping asserts
the *intended* anniversary — IM still renews in November, one year on. The
shipped `atomic-2027.json` is the strongest evidence available that this is the
right default: its Property layers read `2027-04-01 → 2028-04-01`, one year on
from the 2026 file, which is what a human wrote when producing that file by
hand. Whether that layer has actually been renewed is D6's job, not the date's.

This changes towerkit's own "clone as next renewal" browser action, not only
bookkit's `renew`. That is the point — the bug is in the shared primitive.

**No containment check is added.** A layer whose period already extends past its
program period stays legal; `end > start` remains the only rule
(`validate.py:212-218`). Adding containment is a separate call with its own
false-positive risk on genuinely long-tail cover.

**Cost if wrong.** If a renewal is genuinely expected to converge dates rather
than preserve them, the bump writes a date the broker immediately overwrites —
an annoyance, not a lie, because D6 marks the layer `PROPOSED` until someone
confirms it. Leaving the bug in place is a lie.

### D9 — `renewal_on` counts to the earliest line whose cover is not bound onward

**Per line, over the chain as of today: find the layer in force; take its end;
if a `BOUND` layer in the successor covers that line starting on or before that
end, the line is covered onward and is not an attention item. Otherwise it is,
and its end is a candidate for `renewal_on`.** `renewal_on` is the minimum of
the candidates; a placement with no candidates is not an attention item at all.
The 120-day window and its bucket alignment are unchanged, and nothing overdue
falls off.

*Reasoning.* This preserves the entry's requirement — "a just-renewed layer must
not make an unrenewed one fall off the list" — and fixes the converse, which is
the live defect: today a line that has already been renewed in the successor
file keeps the predecessor counting down to its old end, because `line_ends`
reads one file (`sync.py:923-946`). Suppression arrives only once the whole
placement is overdue, via `_renewed`'s name match (`renewals.py:38-49`) — the
guess D2 replaces with a fact. Under D9, `_renewed` stops guessing: the chain
edge says whether a successor exists, and the successor's layers say whether
cover continues.

The invariant from `CLAUDE.md` holds unchanged: `days_remaining < 0` decides
overdue, and the date printed under `renews` is the date counted to.

**Cost if wrong.** If this over-suppresses, a real renewal falls off the
attention list — the failure mode `CLAUDE.md` calls out as never acceptable.
Two mitigations, both structural rather than promised: suppression requires a
`BOUND` layer, which requires someone to have said so; and D6's fallback makes a
freshly cloned layer read `PROPOSED` (`model.py:181`), so a clone suppresses
nothing on the day it is created.

---

## The three consequences `CLAUDE.md` forces this choice to answer

**What a file MEANS.** A file is the cover as bound for one policy period of one
program — complete, self-contained, and still the sole authority for its own
structure. It is *not* "the program." "The program" is the chain of files joined
by D2's edge. This keeps `CLAUDE.md`'s rule intact rather than weakening it:
every fact about a layer still lives in exactly one file, and `proj_*` stays
rebuildable from the files. `program_lineage` and the D7 write record are
bookkeeping about how the book came to be — the same category as `event_log` and
`program_link` — not authority over structure.

**What an export is a snapshot OF.** Today an export is a snapshot of one file,
and it is already dishonest at the boundary: `_row` prints each layer's own
effective/expiration (`soi.py:125-137`), while `sheet_title` (`soi.py:170-175`)
and `default_filename` (`soi.py:178-183`) label the whole workbook with the
*program* period's years. A schedule whose rows say 2026-07-01 under a tab named
"SOI - 25-26" is the misleading document the entry warns about, and it is what
the code produces now. Under this ruling an export is a snapshot of **the chain
as of a date**: every row is the layer in force on that date, the as-of is
printed on the sheet, and the title stops being derived from any one file's
period. `build_soi(program)` (`soi.py:140-167`) becomes `build_soi(chain,
as_of)`.

**What `revert_batch` can put back.** The principle is unchanged and correct:
`revert` refuses program-file batches because file contents are not event rows
(`batches.py:330-333`), and the file-side path is `program_revert_file` over
`program_files`' snapshots. What changes is that the refusal becomes reliable
(D7b) and the snapshot becomes universal (D7a), including the create case
(D7c). Note the retention limit — `SNAPSHOT_KEEP = 20` per directory
(`program_files.py:22`, pruned at `:67-75`) — which is fine for an editing
session and is **not** an archive. It is not a substitute for the predecessor
file, which is the real record of last year. That is another argument for D1.

**What this spec does NOT make revertible: a renewal.** `sync.renew` creates a
file, a placement, a link and (under D2) a lineage row. Under D7 its batch is
marked file-touching, so `R` refuses it and points at the file-side path — and
the file-side path for a creation deletes the file. The *placement* row it
created stays: `created` is in `NON_MUTATION_FIELDS` precisely because a row's
birth is "undone by deleting, not reverting" (`repo/events.py:70-88`). So
"undo a renewal" remains a documented gap rather than a silent half-undo. It is
listed in *Still open*.

---

## Edge cases

**A layer renewed early.** The successor's layer is `BOUND` with a period
starting later than today. The as-of read (D3) shows today's cover — the
predecessor's layer — and the successor's is visibly future. The program does
*not* show next year's limit today; it shows both, marked.

**A line not renewed.** Two distinct states, and D5/D6 separate them. *Pending*:
present in the successor, `PROPOSED`. *Dropped*: absent from a `'renewal'`
successor. Never-entered is absent from both. Against a successor with any other
`minted_by`, the read says so rather than choosing.

**A line restructured rather than dropped.** New, and not covered by the first
draft. `remove_line` only removes layers left with an **empty** `appliesTo`
(`edit.py:94-115`), so a layer covering GL and AL survives the removal of AL with
the id stripped. The composed read must therefore key on `(line_id, layer_id)`,
not on layer alone — otherwise "AL was dropped from this layer" and "this layer
was dropped" render identically. This is a requirement on the D5 primitive, not
a separate feature.

**Mid-term endorsement vs renewal.** An endorsement edits the *predecessor's*
layer and does not start a period. A renewal writes the *successor*. Under D1
these are different files, so they cannot be confused in the data. They can
absolutely be confused in the UI, and the surfaces must name them differently;
see *What must be true*.

**A renewal that changes the tower's shape.** This already partly breaks today.
`compare_programs` keys on `(carrier, layer_id)` (`compare.py:86-93`), and
`sync.add_layer` mints ids by slugifying the name (`sync.py:838`, `_slug` at
`sync.py:992-999`). A layer that splits in two gets new ids, so the comparison
reads one `LAPSED` plus two `NEW` rather than "this split." The composed view
inherits that: it is honest — nothing false is asserted — but not informative,
and "the one visual cannot assume the columns match" is exactly right. Layer
identity across a restructure is a separate question; this spec only requires
that the composed view never *invents* a match. Note the shipped
`atomic-2026`/`atomic-2027` pair keeps all fourteen layer ids stable across the
boundary, so id survival is the common case, not the guaranteed one.

**Converging renewal dates.** Under D8 the successor's IM layer is born at the
old anniversary bumped a year; converging it means the broker sets a **short
period** on that layer — a stub term ending on the target date. The model
accepts it (`end > start` is the only rule, `validate.py:212-218`) and the
composed view will show it correctly. What the model cannot tell is that the
short term is *deliberate* rather than a typo. See *Open decisions*.

**Attention while half-renewed.** D9. Restating the invariant plainly because it
is the one `CLAUDE.md` protects hardest: a `BOUND` successor layer removes *its
line* from the count and nothing else; every other line keeps its own clock; a
lapsed line with nothing bound onward stays overdue indefinitely and cannot fall
off the list.

---

## Visualisation — one function, two chromes

Internal and client-facing are genuinely different problems, but D3 makes them
the same *function* with different arguments, which is what stops them drifting.
Both compose the chain as of a date.

**Internal.** Working view, `as_of = today`, chrome on: per-layer periods shown,
`PROPOSED` layers dashed, dropped lines rendered as absent-with-a-mark against
the predecessor. The display half is mostly built: `cell_dates` already prints
`layer.period or program.period` per cell (`mpl_program.py:156-159`), and dashed
outlines for not-yet-real layers already exist keyed on `signed_bps == 0`
(`mpl_program.py:126-128, 168-194`). The work is to key the dashed treatment on
D6's `Layer.placement` as well, and to render the predecessor's dropped lines —
not to invent a time axis. Time stays a per-layer label plus a per-layer state;
making it a spatial dimension would mean rebuilding `layout.py`'s dollar
geometry for a picture a broker reads once a year.

**Client-facing.** `as_of` chosen and printed, chrome off: only cover in force
on that date, no `PROPOSED` layers, no dropped-line marks, and a stated as-of on
the document. The entry's "these three lines renew in March" is a footnote
derived from the same composition — the lines whose in-force layer ends before
the next anniversary.

**Why this matters for R66.** The Program tab is still a stub
(`web/templates/account/program.html:1-8`), so the HTML and SVG renderers do not
exist yet and can be built agreeing about this from the start. What they must
agree about is not pixels; it is that **both take `(chain, as_of)` and neither
may be called without it**. An HTML view that quietly defaults to "today" and an
SVG export that quietly defaults to "the file's period" would render two
different truths from one click, and the export is the one that leaves the
building.

---

## What must be true before code starts

Each item names the **mutation to production code that makes its test fail.** A
test whose failure mode cannot be named is decoration; item 3 of the first draft
was exactly that and has been replaced.

1. **D7a lands first — the chokepoint.**
   *Test:* `tests/test_conventions.py` walks `src/bookkit/**.py` and asserts
   `dump_program` is named in exactly one module (`services/program_files.py`).
   *Mutation that fails it:* restore `dump_program(clone, new_path)` inline at
   `sync.py:668`. **Fails on today's code** — four modules name it.

2. **D7b — the refusal fires for a TUI write.**
   *Test:* perform a TUI layer edit (title `edit layer — …`, tool slug
   `edit_layer`), then assert `batches.revert` raises with
   `program_file_refusal`'s sentence.
   *Mutation that fails it:* revert `batches.py:330` to
   `batch.tool.startswith("program_")`. **Fails on today's code.**

3. **D7a/c — the snapshot exists on every surface, and a creation is undone by
   deletion.**
   *Test (edit):* after a TUI layer edit, `program_files.restore(path,
   batch_ref)` puts the pre-image back.
   *Mutation:* drop the capture from the wrapper → `restore` raises "no
   snapshot".
   *Test (create):* after `sync.renew`, the file-side revert leaves
   `new_path` **absent**.
   *Mutation:* treat a create like an edit and capture an empty pre-image → the
   file returns as zero bytes and the absence assertion fails.

4. **D8 — the clone is not born expired.**
   *Test:* clone a program of the seeded shape (`seed.py:343-354`: 2025-09-01→
   2026-09-01 with an IM layer ending 2026-06-03) and assert every entry of
   `sync.line_ends` on the clone falls inside the new program period.
   *Mutation that fails it:* remove the layer-period bump from
   `clone_as_renewal`. **Fails on today's code — I ran it:** the clone yields
   `[('IM', 2026-06-03), ('GL', 2027-09-01)]` under a 2026-09-01→2027-09-01
   program, with `validate_program(...).ok == True`.

5. **D6 — additive and byte-identical.**
   *Test:* every file in `towerkit/programs/` (`atomic-2026.json`,
   `atomic-2027.json`) loads, dumps byte-identically, and validates clean
   without the new key.
   *Mutation A:* give `Layer.placement` a non-`None` default → the key is
   emitted on every layer of every file → byte comparison fails.
   *Mutation B:* add the model field and forget `_LAYER_KEYS` → `_ordered`
   raises `RuntimeError` (`model.py:246-249`) → fails loudly rather than
   silently. (B is already guarded; it is listed so the guard is not
   re-discovered later.)

6. **D5 — the read refuses rather than guesses.**
   *Test:* compose a predecessor against a successor whose `program_lineage`
   row says `minted_by='scaffold'`, and assert the read reports
   "not comparable", never `dropped`.
   *Mutation that fails it:* make the composed read ignore `minted_by` → it
   asserts `dropped` on a line the scaffold simply never had.
   *(This replaces the first draft's "a test that a successor is only ever
   created by clone", which was unfalsifiable: there is no enumeration of
   successor-creation paths, three non-clone paths already exist, and any such
   test would either pick a path arbitrarily or pass vacuously.)*

7. **A backup before any of it.** Nothing here is a destructive schema
   migration — D2 and D7 are additive `CREATE TABLE`s, D6 is an optional JSON
   key — but D6 rewrites program files on the next save, and program files are
   the authority. `./bookctl backup` before the first write, and the drill runs
   against seeded sample data locally, never Grant's book (`CLAUDE.md`,
   Process).

8. **The MCP surface gains the layer period** it is already missing
   (`mcpserver.py:398-412`, `1261-1281`) — not part of this design, but leaving
   it out is what produced the report that started this. There is also no MCP
   `renew` tool at all; whether to add one is downstream of D7.

9. **A naming decision, applied to all three surfaces at once**, separating
   "endorse this policy" (edits the predecessor) from "renew this line" (writes
   the successor). One verb for both is how these get confused.

10. **Fix two comments that describe behaviour the code does not have:**
    `renewals.py:33-34` ("the earliest live line end" — there is no liveness
    filter) and `renewals.py:77-78` ("capped by the program period end" — the
    expression caps by `placement.period_to`). Both are the source of the
    ROADMAP entry's wording. This is a comment fix, not a behaviour change; D9
    is the behaviour change.

---

## Recommend against

- **A `renew_line` MCP tool built before any of the above.** It is the obvious
  read of the report and the wrong first move: without D2 there is no successor
  the book can find again, without D6 the result is indistinguishable from a
  carried-forward clone, and without D7 it is a destructive write with no undo
  on the surface Grant uses most.
- **Teaching `_check_line_stack` about time** so one file can span both years.
  It is the change that would make D1 unnecessary, and it is the most dangerous
  change available here — that function is what catches gaps and overlaps in
  real placements (`validate.py:278-334`).
- **Putting the D7 guard in `write_through`.** Named explicitly because it is
  the obvious-looking place and it is wrong: three of the four `dump_program`
  call sites are creations that never touch it, `renew` among them.
- **Reusing `compare_programs` as the composition primitive.** It is keyed on
  `(carrier, layer_id)` and skips layers with no participants
  (`compare.py:20-31, 65-83`) — which is exactly what an unrenewed `PROPOSED`
  layer will be. It would report "no change" on precisely the lines this spec
  exists to surface.
- **Deriving anything further from file names.** `_bump_years`
  (`sync.py:603-605`) is already doing more than it should, and the shipped
  corpus shows the program name carries no year at all.

---

## Suggested next step

Take the real client's two files and hand-build the composed read for three
dates — before the first renewal, mid-renewal, after convergence — as a
throwaway script against a **copy**. If D5 and D6 answer "dropped", "pending"
and "renewed" correctly on real data at all three dates, the model is settled
and the implementation plan can be written. If they do not, the failure will be
in D6, and it is better to find it in a script than in a schema.



---

## Verification report — round 2 (independent adversarial pass)

**Verdict: needs-revision.** Reject. The revision fixes what round 1 got wrong and then commits the same defect class three more times, twice inside the fixes themselves.

What survives, verified: both executed findings reproduce exactly — clone_as_renewal leaves layer periods stale (line_ends on the clone = [('IM', 2026-06-03), ('GL', 2027-09-01)] under a 2026-09-01→2027-09-01 program, validate ok=True), and two policy years of one layer are refused as `line-overlap … at $2,000,000 vs $0`. The shipped atomic-2026/2027 pair checks out in full (identical program name with no year, all fourteen layer ids stable, Property carried 2026-04-01→2027-04-01 then 2027-04-01→2028-04-01), and D8 is correct. The four-call-site refutation of write_through is correct in substance. Nearly every line citation resolves; this is a well-researched document.

What sinks it, in order:

1. The D7b reversal does not achieve its own goal. It removes the prefix predicate at batches.py:330-333 and leaves an identical one at mcpserver.py:1314 on the exact path it redirects users to, plus an org_id gate at 1320-1321 that every TUI form fails by construction (forms.py:93 omits org_id; web/routes/account.py:246 passes it). Shipped as written, a TUI layer edit gains a refusal from `R` and gets refused again by program_revert_file — less undo than today, delivered by the decision whose whole premise is "a guard keyed on what actually happened cannot be evaded by a name".

2. `sync.renew` has a second caller the document twice says does not exist: imports/commit.py:206, inside `with db.transaction(conn)` at commit.py:205. db.py:189-195 says an inner `batch=` is deliberately ignored when nested, so D7's "renew must open a batch" is void there — no batch_ref, no snapshot, no record row, no refusal — while the D7a convention test stays green. That is the 2026-08-15 push_form failure reproduced inside the fix written to prevent it.

3. Item 5's Mutation B cannot fire and is certified as covered. _ordered inspects the hand-written layer dict at model.py:290-330, so adding Layer.placement and forgetting the dict drops the field silently on the authority file. Round 1 was rejected for exactly this.

4. Two load-bearing reversals are argued from evidence that does not support them. D1's "the validator already enforces this" is refuted by execution: a half-renewed single file (GL on 2026, IM on 2025) validates clean — and that file is Grant's actual scenario. The renewal_on withdrawal rests on period_to == program period end, but line_ends reads the file live while period_to is the last manual projection (project_all is called only from cli.py:325 and today.py:297), and write_through's WriteConflict exists because the file drifts.

5. D6's fallback is unsafe in the direction that matters: once Program.placement flips to bound — the normal end state of a renewal, and a field bookkit already reads at sync.py:300 — every layer with placement=None inherits BOUND and D9 suppresses every line at once. The document offers that same fallback as the reason over-suppression cannot happen.

6. D7c introduces an irreversible file delete with no changed-since guard, no backup story, and no row cleanup, leaving the linked-placement-with-missing-file state that renew itself refuses (sync.py:632-634) — while item 7 asserts nothing in the spec is destructive.

Two items genuinely need Grant, and both should be raised before the next draft rather than decided in it. First: D5 refuses to compare against any successor not minted by renew, but the routine case — next year arrives as a pasted schedule (imports/commit.py:165, minted_by='import', and commit refuses an already-linked placement at commit.py:150-152) — then reads "not comparable" forever, with no user-assertable lineage anywhere in the spec. Options: (a) let the user assert a lineage edge explicitly, recorded as minted_by='user', and permit comparison against it; (b) keep the refusal and accept that the feature is dark for imported successors; (c) let imports adopt an existing predecessor's chain at commit time. I recommend (a) — it is the only one that serves the request that started this, and "the user said so" is a fact, not a guess, which is what D5's own rule actually asks for. Second: D6's granularity (layer vs participant) is flagged in the document as its most likely wrong call and should be tested against the real client's two files before the format changes, per its own suggested next step.

Fix-and-move-on items, no re-deciding needed: three modules name dump_program, not four (sync.py, seed.py, imports/commit.py; five call sites), and item 1's test as written contradicts the seed.py excusal; item 4's assertion should be "each cloned layer period is the predecessor's plus one year" rather than "inside the new program period", which contradicts D8's own refusal of a containment check and fails on the shipped atomic-2027 corpus; D2's first rejection mechanic (the UPSERT clobbering `source`) is a strawman since the proposed columns are not in the set-list — the links.forget mechanic carries that decision alone; and D7b's "quieter lie" paragraph understates the damage, since source_sha256 reverts too (base.py:168-178, batches.py:24) and bricks subsequent write_through.

Items 2, 3 and 6's mutations all fire as described; that part of the method is working.


### Decisions round 2 reversed from round 1

- **Was:** D7: the file-write guard and pre-image capture belong in `sync.write_through`, "the single function every program-file write already goes through".
  
  **Now:** The guard belongs in a single bookkit-side wrapper that owns every `dump_program` call, enforced by an AST convention test in tests/test_conventions.py; the refusal predicate stops keying on the `program_` tool-name prefix and keys on a recorded (batch_ref, path, kind) row instead; and a file CREATION records "did not exist" so its file-side revert deletes rather than restores. `renew` and `scaffold_program` must additionally open a batch; imports/commit stays unbatched but registers its write.
  
  **Why:** `dump_program` is called from four production sites in bookkit, not one — sync.py:668 (renew, a creation), sync.py:739 (scaffold_program, a creation), sync.py:1037 (write_through, the only edit path), and imports/commit.py:165 (a creation). sync.py:745's own section comment scopes it: 'transactional program EDITS (all via write_through)'. Guarding write_through would leave renew — the most important write in this spec — unguarded and unsnapshotted while the test went green: the exact 2026-08-15 push_form failure the draft quoted two paragraphs earlier. Separately, MCP's existing wrapper reads the pre-image from an existing path (mcpserver.py:1190), so it cannot serve a creation at all — a creation needs deletion-on-revert, not a pre-image.

- **Was:** D5: "Dropped cover is present in the predecessor, absent from the successor", stated as unambiguous because "the successor is always born as a full clone (sync.py:667)".
  
  **Now:** Dropped means: present in the predecessor, absent from a successor whose recorded `minted_by` is 'renewal'. Against any other successor the composed read REFUSES to answer — it reports "not comparable: this year's file was built independently". Making renew the only minting path was considered and rejected (a client's next-year program routinely arrives as a pasted schedule).
  
  **Why:** The clone invariant is not a property of today's code. sync.py:666 gates the clone on `new_path is not None and program is not None`, so a placement with no program_path renews with no file at all (sync.py:630). Three other paths mint program files from scratch: scaffold_program (sync.py:711-739), imports/commit.py:165, and towerkit's own editor. The draft conceded the invariant "must be asserted" and then rested the whole ruling on it already holding. Surface, don't guess — the same rule program_files.restore already follows at program_files.py:60-63.

- **Was:** D2: `program_link` gains a nullable `renewed_from TEXT` column naming the predecessor's path.
  
  **Now:** A dedicated additive table `program_lineage(successor_path PK, predecessor_path, minted_by, recorded_at)`. The `minted_by` half is new and is what makes D5 statable at all.
  
  **Why:** Two mechanics I re-read make a column on program_link unsafe: links.confirm is an UPSERT whose ON CONFLICT set-list overwrites `source` (repo/links.py:20-22), so a re-link would silently rewrite the provenance the dropped-cover read depends on; and links.forget deletes the row (links.py:49-50), so unlinking a file would destroy the lineage as a side effect. Both are fixable with care, and care is what this project has repeatedly found missing a year later. The draft's own "cost if wrong" already anticipated needing an edge table; the migration cost is identical either way. Note this is still an EDGE, not the lineage ENTITY the draft rejected — it has no identity of its own and nothing is filed under it.

- **Was:** The "Wrong" correction to the ROADMAP: `renewal_on` is capped by the placement row's period_to, not the program period, and "once every line has rolled forward, renewal_on is still pinned to the stale placement row and the program reads as permanently overdue."
  
  **Now:** Withdrawn. The genuine finding (no liveness filter in `_renewal_on`, renewals.py:76-81) is kept, but retargeted at the CODE's own comments rather than at the ROADMAP author, and the "permanently overdue" scenario is deleted. The real attention defect — a line already renewed in the successor file keeps the predecessor counting to its old end — is what D9 now rests on.
  
  **Why:** project() writes `period_to=program.period.end.isoformat()` on every projection (sync.py:262), so for exactly the population that HAS line_ends (file-linked, projected placements) the cap IS the program period end and the ROADMAP's wording is accurate. The "permanently overdue" scenario requires every layer period pushed past the program period end inside one file — the in-place-renewal world D1 explicitly forbids — so it is a consequence of the rejected design, not evidence about current code. The word "live" is lifted from renewals.py:33-34, which is the comment that is wrong; renewals.py:77-78 also misdescribes its own expression.

- **Was:** D5: `compare_programs` "already does exactly this shape of read" and "already has the vocabulary", offered as the strongest argument that the read-time composition primitive already exists.
  
  **Now:** The primitive has to be written. It must be keyed on (line_id, layer_id) and must read layers whether or not they carry participants. `compare_programs` stays as it is and is explicitly listed under Recommend Against for this purpose.
  
  **Why:** DeltaRow (compare.py:20-31) has no line, no applies_to and no period — keyed (carrier, layer_id) only — so "this LINE was dropped" is inexpressible. Worse for D6: `_cells` iterates layer.participants (compare.py:65-83), so a layer with NO participants contributes zero rows — and a participant-less layer is exactly what towerkit calls pending (soi.py:20-22, mpl_program.py:126-128) and exactly what an unrenewed PROPOSED layer will be. It would report "no change" on precisely the lines this spec exists to surface.

- **Was:** D6 should land together with versioning `SCHEMA_ID` (model.py:27), "because the next change will need a discriminator and today there is nowhere to put it."
  
  **Now:** Drop SCHEMA_ID versioning from this spec. Use the emit-only-when-set pattern this codebase already established, which requires `Layer.placement` to default to None (not PROPOSED) — an explicit, testable constraint.
  
  **Why:** model.py:273-276 and 311-314 document the pattern in comments on soiSchematic and statutory: "untouched programs re-save byte-identically, and older towerkit wheels only reject files that USE the feature." `_ordered` already drops None values (model.py:246-250), so a None default gets it for free. SCHEMA_ID versioning would add a version no reader branches on. The draft also under-specified the edit surface: the change is four coordinated edits (Layer field, _LAYER_KEYS at 232-236, program_to_jsonable's hand-written layer dict at 290-330, and the schema's layer properties, which are additionalProperties:false).

- **Was:** "What must be true" item 3: a test that a successor is only ever created by clone.
  
  **Now:** Deleted and replaced with a falsifiable test: compose against a successor whose lineage row says minted_by='scaffold' and assert the read reports "not comparable", never "dropped". Named mutation: make the composed read ignore minted_by.
  
  **Why:** The original was unfalsifiable — there is no enumeration anywhere of "the paths that create a successor", three non-clone creation paths already exist (sync.py:739, imports/commit.py:165, towerkit's editor), and such a test would either assert something about an arbitrarily-chosen code path or pass vacuously. This build has repeatedly shipped tests that passed for a reason adjacent to their claim.

- **Was:** D1 rejection argued partly from the frozen canonical key order, "whose stated purpose is readable git diffs between renewal years".
  
  **Now:** Argument withdrawn. D1 rests solely on the validator result, which I reproduced by execution.
  
  **Why:** The comment at model.py:225-226 says key order is frozen so that arbitrary TUI reformatting does not make diffs unreadable; "between renewal years" is the example, not the purpose, and nothing about key order stops one file holding two years. D1 does not need the argument and was weaker for carrying it.


### Regressions the revision introduced

*This is the list that stopped the iteration: a fix reproducing its own defect class one level down.*

- D7b removes the tool-name prefix predicate at batches.py:330-333 and leaves an identical one at mcpserver.py:1314 (`if not batch.tool.startswith("program_")`) on the very path it redirects users to, plus a second blocker at mcpserver.py:1320-1321 (batch.org_id is None, which is what forms.py:93 produces for every TUI form). The revision's headline reversal — 'the guard is a recorded fact, not a name' — is defeated one function over by the same name check, and the shipped result is a TUI layer edit that `R` refuses and program_revert_file also refuses: strictly less undo than today.

- D7's 'renew and scaffold_program must additionally open a batch' is silently void on the second renew caller the document says does not exist. imports/commit.py:206 calls sync.renew inside `with db.transaction(conn):` (commit.py:205), and db.py:189-195 states that an inner `batch=` is 'deliberately IGNORED' when nested. That path creates a program file with no batch_ref — no snapshot, no (batch_ref, path, kind) row, no refusal — while the D7a convention test passes. This is the 2026-08-15 push_form failure reproduced inside the fix written to prevent it.

- Item 5's Mutation B is declared 'already guarded' and cannot fire: _ordered (model.py:246-250) inspects the hand-written dict at model.py:290-330, so adding Layer.placement to the model and forgetting both the dict and _LAYER_KEYS drops the field silently on every save. Round 1 was rejected for a mutation that could not turn its test red; round 2 ships another one and additionally certifies it as covered.

- The compare_programs argument was reversed from 'it already does this' to 'it cannot do this because participant-less layers are invisible' — swapping one unverified claim for its unverified opposite. clone_as_renewal deep-copies participants (model.py:177), so on the only successor kind D5 permits, unrenewed layers have full signed_bps (model.py:110-111) and are perfectly visible to _cells. The conclusion survives on the DeltaRow keying argument alone; the new supporting claim is false.

- D6's Layer.placement fallback is presented as giving D9 'its safe default for free'. The same fallback makes every unrenewed layer read BOUND as soon as Program.placement is set to bound in towerkit's editor (a field bookkit already reads at sync.py:300), at which point D9 suppresses every line on the placement — the exact over-suppression CLAUDE.md forbids and that D9's cost-if-wrong claims is structurally impossible.

- D2 was moved off program_link partly to escape links.forget destroying the edge, but the new path-keyed table is invisible to _detect_rename (sync.py:439-444), which calls links.forget on the old path during an ordinary file move. The edge now survives pointing at a dead path — a stale wrong answer replacing an absent one, which inverts the 'surface, don't guess' principle the same decision invokes.

- D7c introduces an irreversible file DELETE as the revert path for a creation, with no changed-since guard, no backup story, and no cleanup of the placement / program_link / program_lineage rows that keep pointing at it — while item 7 simultaneously asserts nothing in the spec is destructive. The resulting state (linked placement, missing file) is one sync.renew already has a guard against at sync.py:632-634.


### Citations that still did not check out

- **`src/bookkit/tui/widgets/entity_actions.py:159 — "sync.renew is reachable only from entity_actions.py:159" (stated twice: "Incomplete in the entry" and D7's table, "batched today? no — entity_actions.py:159 calls it bare")`** — claimed: renew has exactly one caller, the TUI, so making renew open a batch closes the hole
  
  *Actually:* grep for sync.renew returns TWO production callers: entity_actions.py:159 AND imports/commit.py:206, inside commit_renewal (commit.py:187-241). The second is wrapped in `with db.transaction(conn):` at commit.py:205. Per db.py:189-195 — "NESTING JOINS… an inner `batch=` is deliberately IGNORED" — a batch opened by renew on that path is silently discarded. So D7's "renew must additionally open a batch" does not take effect for renewal-imports: no batch_ref, therefore no snapshot key, no (batch_ref, path, kind) row, and D7b's lookup cannot refuse it. The D7a convention test stays green throughout. This is the identical single-call-site error, and the identical green-test-wrong-path failure, that the revision exists to fix.

- **`src/bookkit/services/batches.py:330-333 — D7b, "Replace the string test with a lookup… A guard keyed on what actually happened cannot be evaded by naming a form differently."`** — claimed: batches.py:330-333 is the place the tool-name prefix predicate lives; replacing it makes the guard un-evadable
  
  *Actually:* There is a SECOND, unmentioned prefix predicate on the file-side path: mcpserver.py:1314, `if not batch.tool.startswith("program_"): raise ValueError(f"{batch_ref} is not a program-file write — use revert_batch")`. The document's own remedy routes the refused user to program_revert_file, which will reject a TUI batch whose slug is `edit_layer`. A third blocker sits at mcpserver.py:1320-1321 (`if batch.org_id is None: raise …cannot locate its file`) — the TUI derives its batch at forms.py:92-93 as `BatchSpec.for_title(spec.title)` with no org_id, while the web passes one (account.py:246). Net effect of D7 as specified: `R` starts refusing TUI layer edits and the file-side path they are pointed at refuses them too — no undo at all, where today there is at least a partial one.

- **`towerkit/src/towerkit/model.py:246-249 — item 5, "Mutation B: add the model field and forget _LAYER_KEYS → _ordered raises RuntimeError… (B is already guarded; it is listed so the guard is not re-discovered later.)"`** — claimed: forgetting _LAYER_KEYS after adding Layer.placement fails loudly
  
  *Actually:* _ordered computes `missing = set(raw) - set(keys)` on the dict it is HANDED. The layer dict is hand-written at model.py:290-330 and contains only the keys typed there. Adding `placement` to the Layer model changes nothing about that dict, so no RuntimeError fires — the field is silently dropped on every save, a round-trip data loss on the authority file. The guard fires only for the reverse mistake (added to program_to_jsonable, absent from _LAYER_KEYS). The named mutation cannot turn its test red, and the parenthetical "already guarded" is a false claim of coverage — the exact defect round 1 was rejected for.

- **`towerkit/src/towerkit/compare.py:65-83 — D5/Recommend-against, "_cells iterates layer.participants, so a layer with no participants contributes no rows at all — and a participant-less layer is… precisely what an unrenewed PROPOSED layer will be under D6… It would report 'no change' on precisely the lines this spec exists to surface."`** — claimed: compare_programs would render unrenewed lines invisible, and that is what disqualifies it
  
  *Actually:* compare.py:65-83 does iterate participants — but the inference is wrong. clone_as_renewal is `model_copy(deep=True)` (model.py:177), so a cloned successor's unrenewed layers keep the predecessor's participants; signed_bps is `sum(p.share_bps …)` (model.py:110-111) and is full, not zero. D5 permits composition ONLY against a minted_by='renewal' successor, i.e. exactly a clone — where the participant-less case never arises. I confirmed by execution that a clone retains participants (no layer-unplaced warning). The first bullet (DeltaRow keyed (carrier, layer_id), no line/applies_to/period, compare.py:20-31) is correct and sufficient; the second is a new false claim substituted for round 1's opposite false claim.

- **`src/bookkit/sync.py:262 — the withdrawal of the "permanently overdue" finding: "for the population that has line_ends at all — file-linked, projected placements — those two ARE the same value"`** — claimed: placement.period_to always equals the program period end for file-linked placements, so the cap can never diverge
  
  *Actually:* sync.py:262 is correct as far as it goes, but the two values come from different times. `line_ends` loads the file from disk on every read (sync.py:930-935); `placement.period_to` is whatever the LAST projection wrote. Re-projection is manual — project_all is called only from cli.py:325 and today.py:297. write_through carries a WriteConflict at sync.py:1026-1029 whose message is "changed on disk since last projection — probably towerkit's TUI" (class docstring, sync.py:1006), i.e. the codebase is built around the assumption that the file drifts ahead of the DB. Extend a program in towerkit's editor without re-syncing and min(live line end, stale period_to) pins renewal_on to the stale date — the withdrawn scenario, reached by a route the document itself relies on in D5 ("towerkit's own editor writing a file bookkit later adopts"). The withdrawal is over-broad: the ROADMAP wording holds only at projection time, not as an invariant.

- **`src/bookkit/sync.py:668, 739, 1037 and imports/commit.py:165 — item 1, "Fails on today's code — four modules name it"`** — claimed: four modules name dump_program
  
  *Actually:* THREE modules name it — sync.py (import at :37, calls at 668/739/1037), seed.py (import at :26, call at :312) and imports/commit.py (import at :138, call at :165) — across five call sites. Worse, the test as specified ("named in exactly one module") contradicts the document's own excusal of seed.py two paragraphs earlier ("seed.py:312 is demo-data generation, not a user write"). As written the test either forces seed() through a batch-taking wrapper or needs an exemption list — which is precisely the "obstacle someone routes around" that D7's own cost-if-wrong names.

- **`src/bookkit/services/batches.py:330-333 — D7b's "scope honesty" paragraph: "on a period-only layer edit the whole change lives in the file and proj_layer, neither of which revert touches. The lie is still a lie; on a period-only edit it is a quieter one."`** — claimed: reverting a period-only TUI layer edit is comparatively harmless
  
  *Actually:* base.update logs every field whose value actually changed (base.py:168-178), and project() writes source_sha256 and synced_at on every projection (sync.py:267-268). SKIP_FIELDS excludes only source/import/carrier_alias/merged_from (batches.py:24), so source_sha256 IS reverted. That writes back the pre-edit hash while the file holds the post-edit bytes, so the next write_through raises WriteConflict at sync.py:1026-1029 and every further program edit on that placement is refused until someone re-syncs. Not quieter — louder, and in a way the user cannot diagnose.

- **`src/bookkit/repo/links.py:20-22 — D2's first rejection mechanic, "links.confirm is an UPSERT whose ON CONFLICT set-list overwrites `source`, so a re-link would silently rewrite the provenance the dropped-cover read depends on"`** — claimed: the UPSERT would clobber lineage columns added to program_link
  
  *Actually:* The set-list at links.py:20-22 names org_id, insured_name, confirmed_at and source only. A new `renewed_from`/`minted_by` column would not appear in it and would survive re-confirmation untouched. The argument only bites if `source` is reused as minted_by, which the draft did not propose. The second mechanic (links.forget deletes the row, links.py:49-50) is genuine and decisive on its own — but the reversal is presented as resting on two re-read mechanics, and one of them is a strawman.


### Claims challenged

- **[CRITICAL]** D1: "This is not a preference; it is what the validator already enforces, reproduced by execution above: a file holding both years of one layer is refused as line-overlap."
  
  *Evidence:* I reproduced the overlap exactly (`line-overlap | gl: OVERLAP Primary GL 25→Primary GL 26 at $2,000,000 vs $0`, ok=False) — the citation resolves. But it proves something narrower than the ruling. I also executed the case that matters: one file with GL rolled to 2026-09-01→2027-09-01 and IM still 2025-09-01→2026-06-03, under a 2025-09-01→2026-09-01 program period. Result: `ok=True`, warnings only. _check_line_stack (validate.py:278-334) refuses two layers stacked in the same DOLLAR space on the same LINE; it says nothing about a file whose layers span different years on different lines. That half-renewed file is exactly Grant's stated situation — "multiple renewal dates across multiple policies… keep seeing it as one visual" — and it is expressible in one file today and validates clean. D1 may still be the right call, but it is a preference about what a file MEANS, not an enforcement the validator already performs, and the document must say so rather than borrow authority from an execution that tested a different shape.

- **[CRITICAL]** D6 + D9: "The fallback gives D9 its safe default for free… A cloned layer with placement=None therefore reads PROPOSED, and under D9 a PROPOSED layer never suppresses an attention item." And D9's mitigation: "suppression requires a BOUND layer, which requires someone to have said so."
  
  *Evidence:* The fallback runs both ways. Program.placement (model.py:149) is a real, mutable field — sync.py:300 reads it to set placement status, and towerkit's editor is the surface that sets it. The moment a broker marks the successor program BOUND (the normal end state of any renewal), every layer still carrying placement=None inherits BOUND by the same fallback the document praises, and D9 suppresses EVERY line at once — including the ones nobody renewed. "Requires someone to have said so" is false: nobody said anything about that layer. This is the over-suppression failure the document itself calls the one CLAUDE.md forbids, arriving through the mechanism it offers as the mitigation. Either D9 must require an explicitly-set layer placement (not the fallback), or D6's fallback must not be BOUND-inheriting.

- **[CRITICAL]** D7c: "the file-side revert for a creation deletes the file it created," and "'undo a renewal' remains a documented gap rather than a silent half-undo."
  
  *Evidence:* Three problems, none addressed. (a) No changed-since guard is specified. program_files.restore refuses when the file moved under it (program_files.py:58-63) and the document invokes that same "surface, don't guess" rule for D5 — but the delete path is specified with no equivalent, so reverting a week-old renewal would destroy whatever renewal work has since been done in the successor. (b) There is no backup story for an irreversible file DELETE, while item 7 asserts "nothing here is a destructive schema migration" — CLAUDE.md requires the destructive operation to be surfaced explicitly, and a delete with no pre-image is the least recoverable operation in the spec. (c) It IS a silent half-undo: `created` is a NON_MUTATION_FIELD (events.py:82-88), so deleting the file leaves the successor placement row, its program_link row and its program_lineage row all pointing at a path that no longer exists — precisely the state renew itself refuses to operate on ("linked file is missing — fix the link first", sync.py:632-634).

- **[IMPORTANT]** D2: program_lineage keyed on paths "names files by the same path key program_link already uses," presented as the safe alternative to a column that links.forget would destroy.
  
  *Evidence:* _detect_rename (sync.py:424-445) re-points a moved file by calling links.confirm on the new path and links.forget on the old one (439-441), then rewrites placement.program_path (442-444). A separate program_lineage table is invisible to that code, so a renamed or moved file leaves lineage rows pointing at a path that no longer exists — silently. Judged by the document's own criterion, this is worse than the rejected column, not better: the column would have vanished (absent → D5 refuses → safe), whereas the table survives with a stale key and can produce a confidently wrong predecessor. The reversal re-read links.forget and drew the opposite conclusion from the same mechanic without checking who else calls it.

- **[IMPORTANT]** D5 in practice: the composed read "refuses to answer" against any successor not minted by renew, offered as the house rule "surface, don't guess."
  
  *Evidence:* The document also establishes that a client's next-year program "routinely arrives as a pasted schedule" (imports/commit.py:165, minted_by='import'), and that commit refuses to attach a file to an already-linked placement (commit.py:150-152), so imported successors necessarily land as separate, lineage-less placements. There is no user-assertable lineage anywhere in the spec — 'user' appears in the minted_by vocabulary but nothing writes it and nothing says whether it permits comparison. So for the routine case, the feature Grant asked for renders "not comparable: this year's file was built independently" forever. Refusing is right; refusing with no way to say "yes, this is the renewal" ships a permanently dark feature.

- **[IMPORTANT]** Item 4's test: "assert every entry of sync.line_ends on the clone falls inside the new program period."
  
  *Evidence:* I reproduced the underlying finding exactly (clone yields [('IM', 2026-06-03), ('GL', 2027-09-01)] under a 2026-09-01→2027-09-01 program, validate ok=True), so D8 stands. But the assertion contradicts D8's own next paragraph, "No containment check is added… A layer whose period already extends past its program period stays legal." It also fails against the shipped corpus the document cites as its strongest evidence: atomic-2027.json's Property layers run 2027-04-01→2028-04-01 under a program ending 2028-01-01. The invariant that actually follows from D8 is "each cloned layer period equals the predecessor's plus one year (Feb-29 clamped)" — assert that instead, or the test bakes in a rule the design explicitly refuses.

- **[MINOR]** Item 3's create-case mutation: "treat a create like an edit and capture an empty pre-image → the file returns as zero bytes and the absence assertion fails."
  
  *Evidence:* This one does fire — restore would copy a 0-byte image over new_path, leaving it present, and the absence assertion goes red. Noted as sound, in contrast to Mutation B in item 5. Item 2's mutation and item 6's mutation also fire as described. The failure is concentrated in item 5 and in item 1's module count.

- **[MINOR]** "SNAPSHOT_KEEP = 20 per directory… which is fine for an editing session."
  
  *Evidence:* Holds mechanically — _prune globs "MCP-*.json" (program_files.py:70) and BATCH_REF is "MCP" for every source including TUI and web (repo/batches.py:12), so TUI-originated snapshots do get pruned. But under D7a every TUI layer edit now competes for the same 20 slots on Grant's most-used surface, so the window shrinks from "an MCP session" to "a few minutes of form edits", which makes the "file-side path exists" promise thinner than the refusal sentence implies. Worth a number, not a redesign.


### Needs Grant

- Is renewal state per LAYER or per PARTICIPANT? D6 adds `Layer.placement: Placement | None`. If, on the real client, one carrier on a shared layer has renewed while another has not, the field is at the wrong granularity. RECOMMENDATION: per layer — a layer is the issued policy, which is why policy_number and period already live there (towerkit model.py:85-88, 90-93), and a genuinely part-renewed layer is more naturally two layers than one layer with mixed state. COST IF WRONG: every file written with the field needs rewriting, plus a second additive field and a migration over anything already saved. Highest-cost item in this document after D1, and the one most worth checking against the real client's two files before code starts.

- Does a mid-renewal client schedule go out as ONE document as of today, or as TWO (last year's remaining cover and next year's incepting cover)? D3 makes either buildable; the default is what the first client actually receives. RECOMMENDATION: one document, as-of today, with the as-of printed on the sheet and a footnote naming the lines that renew later. Two documents push the reconciliation onto the client. COST IF WRONG: a client reads a partial schedule as their full programme — the 'document that misleads' the ROADMAP entry warns about, and the one artefact that leaves the building. Note this is already partly broken today: soi.py:125-137 prints each layer's own dates while soi.py:170-175 titles the workbook with the program period's years.

- When renewal dates converge onto one date, the converging layer gets a SHORT stub term. Should the model recognise that as deliberate, or leave it as an ordinary period? Today nothing distinguishes an intentional stub from a mistyped date — the only rule is end > start (towerkit/validate.py:212-218). RECOMMENDATION: leave it underived. Do not add a flag or a warning yet; show the short term plainly in the internal view and let the broker read it. Add the guard only once convergence has been done for real once or twice. COST IF WRONG: a mistyped expiry renders as an intentional stub and nothing warns — structurally the same failure as the bare-number date bug, on a field with no guard at all.

- Naming, applied to all three surfaces at once: what verb separates 'endorse this policy' (edits the predecessor's layer, starts no period) from 'renew this line' (writes the successor's layer)? This is a vocabulary call, not a technical one, and it has to be settled before any of the three surfaces ships a control. One verb for both is how these get confused, and under D1 the data cannot disambiguate them because they land in different files. I have no recommendation beyond 'not the same word'.


### Deliberately not settled

- How to undo a renewal. Under D7 renew's batch is marked file-touching so `R` refuses it and the file-side path deletes the file it created — but the placement row renew also created stays, because 'created' is a NON_MUTATION_FIELD (repo/events.py:70-88): a row's birth is undone by deleting, not reverting. So 'undo a renewal' is a documented gap after this spec, not a closed one. Closing it means a delete-the-placement path, which is a separate decision about whether a placement can ever be deleted at all.

- Layer identity across a restructure. When a layer splits in two, sync.add_layer mints new ids by slugifying the name (sync.py:838, _slug at 992-999), so the composed view reads one LAPSED plus two NEW rather than 'this split'. This spec only requires that the composed view never INVENTS a match; making a split legible is a separate question. The shipped corpus keeps all fourteen layer ids stable across the renewal boundary, so id survival is the common case — which is why this can wait.

- Whether a containment check (layer period inside program period) should exist. D8 deliberately does not add one: today a layer may legally end after its program period (validate.py has only end > start at 212-218), and a rule would carry false-positive risk on genuinely long-tail cover. Named so nobody assumes D8's test implies a general invariant — the test asserts it for the seeded shape only.

- Whether MCP should gain a `renew` tool at all. There is none today (sync.renew is reachable only from entity_actions.py:159), which is part of what produced Grant's report. Adding one is downstream of D7 landing, and this spec does not decide it.

- The composition primitive's home. D5 requires a new (line_id, layer_id)-keyed read over a chain. Whether it lives in towerkit (alongside compare.py, pure and renderer-facing) or in bookkit (alongside services/renewals.py, because it needs the program_lineage edge which is bookkit-side) is a real boundary call this spec does not settle. The edge dependency argues for bookkit composing and passing two loaded Programs into a pure towerkit function.

- Whether `program_lineage` and the D7 write-record table should be one table or two. Kept separate here because they have different lifetimes — lineage is permanent, the write record prunes alongside SNAPSHOT_KEEP=20 (program_files.py:22, 67-75) — but that is a judgement, not a proof.
