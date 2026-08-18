<!-- DRAFT — NOT APPROVED. Read the verification report at the bottom before building from this. -->

> **Status: DRAFT — NEEDS REVISION** (2026-08-18).
> Produced by a drafting pass, then checked by an independent adversarial pass that
> opened every citation and challenged the load-bearing claims.
> **68 citations checked · 7 failed · 9 claims challenged.**
> Kind: `spec`.
> The verification report at the bottom is PART OF THIS DOCUMENT — some of its findings
> would break an implementation built from the body above it.

---

# A program across a renewal boundary — design

Date: 2026-08-18
Status: draft, for review. Nothing here is implemented. This settles the
model only; it deliberately does **not** contain an implementation plan.
It is bound by `CLAUDE.md` — where this document and `CLAUDE.md` disagree
unintentionally, `CLAUDE.md` wins and this document is wrong.

Scope note: this touches both repos. `towerkit` is a path dependency
(`pyproject.toml:34`), and one decision below (D6) changes towerkit's file
format. Everything else is bookkit-side.

---

## Why this exists

Grant, 2026-08-18: a client with multiple renewal dates across multiple
policies. The program technically renews from a 2025 program to a 2026 one,
but he needs to keep seeing it as **one visual**. He tried to renew a single
policy through MCP; it could not renew one line, and ultimately created a new
program instead.

That failure is worth being precise about, because the ROADMAP entry states
it more broadly than the code supports — see *Corrections* below. The model
already carries per-layer periods. The service layer already writes them. The
MCP surface does not expose them. What is genuinely missing is not a
`renew_line` tool; it is an answer to **what a program's identity is across
time**, because without one there is no honest way to say which of two files
is "the program" while cover is half-2025 and half-2026.

---

## What I verified before writing anything

The ROADMAP entry makes six factual claims. Five hold; one is materially
wrong and two more are incomplete in ways that change the design.

**Holds.**

- `towerkit.model.Layer.period` is optional and falls back to the program
  period. `period: Period | None = None`
  (`towerkit/src/towerkit/model.py:93`); the docstring states the reason —
  "programs can carry several policy effective/expiry dates, so the period is
  per-layer, defaulting to the program period when absent"
  (`model.py:85-87`).
- `sync.line_ends` is at `sync.py:923` and derives each line's end as
  `min(layer.period.end if layer.period else program.period.end)` over the
  layers covering it (`sync.py:941-944`).
- `services.renewals.RenewalItem` carries `line_ends` per line
  (`renewals.py:32`) and `days_remaining` counts to `renewal_on`
  (`renewals.py:27, 114`).
- `sync.renew` is at `sync.py:618`, takes a `placement_id`, clones the whole
  file with `clone_as_renewal` (`sync.py:667`), bumps both placement period
  dates a year (`sync.py:650-651`), and refuses when next year's file already
  exists (`sync.py:645-648`).
- `Line` has no period — `id`, `name`, `abbr`, `group` only
  (`model.py:47-53`). Any answer works on layers.

**Wrong.** The entry says `renewal_on` is "the earliest **live** line end,
capped by the program period." There is no liveness filter anywhere.
`_renewal_on` returns `min(ends[0][1], placement.period_to)`
(`renewals.py:79-80`) — the earliest line end full stop, capped by the
**placement row's** `period_to`, not the file's program period. Those diverge
exactly when the program is half-renewed, which is the case this spec is
about. The cap is load-bearing in a way the entry does not capture: once every
line has been rolled forward, `renewal_on` is still pinned to the stale
placement row and the program reads as permanently overdue.

**Incomplete, in ways that change the design.**

- *"There is no line-level or layer-level renewal anywhere."* True of
  renewal. **False of the layer period.** `sync.update_layer` accepts
  `period_from` / `period_to` (`sync.py:794-795`) and writes
  `layer.period` (`sync.py:813-819`); the TUI's layer form exposes both
  fields and passes them (`tui/widgets/entity_actions.py:197-198, 221-222`).
  The MCP tool `program_layer_edit` omits them from its signature
  (`mcpserver.py:398-412`) and `_program_layer_edit` never passes them
  (`mcpserver.py:1261-1281`). So "it could not renew one line" is a property
  of **one surface**, not of the model or the service — the same
  capability-present-on-one-surface shape as the 33 unbatched `FormModal`
  call sites (`CLAUDE.md`, 2026-08-15).
- *"Time becomes a dimension of the tower diagram, which it is not today."*
  Per-layer periods are already renderable. `cell_dates` prints
  `layer.period or program.period` into each participant cell
  (`towerkit/render/mpl_program.py:156-159`), and it is a saved render
  setting (`model.py:139`). Time is not a *state* in the diagram; it is
  already a *label*.

**Two things nobody has written down, both proven by running the code.**

1. **`clone_as_renewal` does not bump layer periods, so a renewed file is
   born expired.** It sets `clone.period` only (`model.py:178-180`); layers
   come across by `model_copy(deep=True)` with their old dates intact.
   Executed against the shape `seed.py:343-352` calls "the norm": a
   2025-09-01→2026-09-01 program with an IM layer ending 2026-06-03 clones to
   a 2026-09-01→2027-09-01 program whose IM layer still reads
   2025-09-01→2026-06-03. `line_ends` then returns `('IM', 2026-06-03)` for
   the *new* file, `_renewal_on` returns that date, and the renewal placement
   lands in the overdue bucket on the day it is created. Nothing refuses it:
   the only period rule on a layer is `end > start`
   (`towerkit/validate.py:212-217`); there is no containment check against the
   program period, and validating that file returns zero errors.
2. **One file cannot hold two policy years of the same layer.**
   `_check_line_stack` is dollar-space only (`validate.py:278-333`): it
   requires the base layer to attach at $0 and each layer above to attach
   exactly at the one below's top, with no reference to `layer.period`. A file
   carrying both the expiring and the incepting primary on one line validates
   as `line-overlap: gl: OVERLAP Primary GL 25→Primary GL 26 at $2,000,000 vs
   $0`. Since `write_through` validates before it dumps
   (`sync.py:1031-1037`), that state is **unreachable through every bookkit
   write path**. This is the fact that decides the ruling.

Also established, and cited in the decisions below:

- `proj_layer` carries no period columns (`migrations/001_initial.sql:227-237`)
  and the projection never writes one (`sync.py:222-234`). There is no
  `proj_line` table at all. Everything period-aware re-reads the file.
- `placement` has no predecessor/successor column
  (`migrations/001_initial.sql:103-122`). The only continuity thread is
  `_renewed`'s name match modulo `_bump_years` plus a date ordering
  (`renewals.py:38-49`), and `program_link.source='renewal'`
  (`migrations/002_program_link_source.sql`), which records *that* a link came
  from a renewal but not *from what*.
- The file format is closed: `additionalProperties: false` at every level of
  `towerkit/schema/program.schema.json`, `extra="forbid"` on the pydantic base
  (`model.py:44`), and `SCHEMA_ID` carries no version (`model.py:27`).
- `remove_line` hard-cascades the line, its layers, its retentions and its
  sublimits out of the file (`towerkit/edit.py:94-115`). `line_ends` silently
  omits any line with no covering layers (`sync.py:937-939`). There is no
  tombstone anywhere, so dropped and never-entered are byte-identical.
- `batches.revert` refuses any batch whose `tool` starts with `program_`
  (`services/batches.py:330-333`, message at `:291-305`), because "File
  contents are not event_log rows" (`services/program_files.py:3-4`). Only
  `mcpserver.py:1196` ever calls `program_files.capture`.
- The web Program tab is still a stub
  (`web/templates/account/program.html:3-7`), so this lands before the
  HTML/SVG renderers of R66, as the entry hoped.

---

## The ruling

**A program file stays one policy period. The identity across time is an
explicit recorded edge between files. "One program" is a view composed at
read time over that chain, always as of a date.**

In the entry's terms: **(b) plus (c). (a) is rejected.**

### D1 — One file is one policy period, and a renewal never edits the predecessor

**A towerkit program file describes the cover as bound for exactly one policy
period.** A renewal creates a successor file; the predecessor is thereafter
read-only in the sense that matters — it is never mutated by a renewal
operation. Correcting a mistake in last year's placement is still an edit to
last year's file, and that stays legal.

*Reasoning.* This is not a preference; it is what the validator already
enforces. A file holding both years of one layer is refused as `line-overlap`
(`validate.py:278-333`, proven above), and `write_through` validates before it
dumps (`sync.py:1031-1037`). Making one file span a renewal boundary means
teaching `_check_line_stack` about time — every gap/overlap/base check becomes
"per line, per instant" — and that check is the thing that catches real
placement errors. Time-slicing it is a large, subtle change to the one rule
that protects the numbers.

**Rejected: one file that spans both years.** Besides the validator cost, it
breaks the frozen canonical key order whose stated purpose is readable git
diffs *between renewal years* (`model.py:226-231`) — if the years live in one
file there are no diffs between years to read. It also makes `Program.period`
meaningless, and `Program.period` is what `sheet_title`, `default_filename`,
`_renewal_on`'s cap, and the projection's `period_from`/`period_to` all read.

**Cost if wrong.** If Grant genuinely works one continuous document per client
rather than a document per year, every read path becomes a filter and the file
grows without bound. Reversing D1 later means a real format migration and a
rewrite of `_check_line_stack`. This is the single hardest decision here to
undo.

### D2 — The continuity thread is a recorded edge in bookkit, not a name and not a lineage entity

**`program_link` gains a nullable `renewed_from TEXT` column naming the
predecessor's path.** `sync.renew` writes it at the moment it creates the
successor — it already calls `links.confirm(..., source="renewal")` right
there (`sync.py:669`), and already knows both paths. The chain is walkable in
both directions from any file.

*Reasoning.* Three properties decide this. It is an **additive migration**,
which is the only kind this project has done (`CLAUDE.md`: "Migrations are
additive-only so far"). It **touches no JSON**, so every file on Grant's disk
stays valid — which matters because the format is closed on all three fronts
(`additionalProperties: false`, `extra="forbid"` at `model.py:44`, and an
unversioned `SCHEMA_ID` at `model.py:27`, so there is no discriminator to
migrate on). And it **replaces a guess with a fact**: today the only link is
`_renewed`'s match on `program_name` or `_bump_years(program_name)`
(`renewals.py:44-47`) plus `_bump_years` on the file stem (`sync.py:641-644`).
Rename a program at renewal — which brokers do — and the chain silently breaks,
the predecessor never leaves the overdue list, and the successor looks like a
brand new placement.

**Rejected: (a), a first-class program-lineage entity.** It has two homes and
both are bad. In SQL it becomes an authority the files do not have, which is
precisely what `proj_*` is forbidden from being (`CLAUDE.md`: "towerkit JSON
files are the sole authority for program structure; proj_* tables are a
rebuildable cache"); a lineage row is not rebuildable from the files, so the
cache stops being a cache. In the file it is a closed-format change for a fact
that is about the *relationship between* files, not about any one of them.
Either way it adds an id, a ref, event_log fields, undo semantics, and a second
thing that can be wrong — a file filed under the wrong lineage — in exchange for
information an edge plus the periods already yields.

**Rejected: keeping the name convention and hardening it.** `_bump_years`
rewrites *every* four-digit year in a string (`sync.py:603-605`); the
convention cannot be made reliable without becoming a parser, and a parser
that is right 95% of the time is worse than an edge that is right always.

**Cost if wrong.** Low and recoverable. If the edge turns out to want more than
one predecessor (a merge of two programs into one) it becomes an edge *table*
rather than a column — a second additive migration, no data loss.

### D3 — Every composed read takes an as-of date. There is no default view without one

**No function that composes a chain may be called without an explicit as-of
date.** Internal views default it to today; exports require it to be chosen and
printed. `as_of` is a parameter, never the wall clock — the same rule
`batches.revert`'s `now` already follows (`services/batches.py:319`).

*Reasoning.* "One visual" and "as of when" are the same question. As of
2026-08-18 in Grant's client, GL is 2026 cover and IM is 2025 cover; as of
2026-12-01 both are 2026. There is no single true picture, only a picture at an
instant, and a renderer that does not take the instant will invent one. This
is also the only rule that makes the internal and client-facing views the same
function — see *Visualisation*.

**Cost if wrong.** If Grant wants a "whole chain, all years" overview rather
than a point-in-time one, that is an additional view, not a replacement — D3
costs an unused parameter. Getting it wrong the other way (composing without
an as-of) is the entry's own "document that misleads."

### D4 — The renewal unit is a layer's period, moved forward in the successor file

**"Renewing a line" means: in the successor file, the layer covering that line
gets its real 2026 period and terms.** The predecessor is untouched (D1). The
successor exists from the first renewal onward, not from the last.

*Reasoning.* This falls out of D1 and the existing shape. `Line` has no period
(`model.py:47-53`), so the operation was always going to be on layers. The
successor already exists eagerly — `sync.renew` creates the whole clone up
front (`sync.py:667`) — so nothing new has to be invented to hold a partially
renewed year.

**Rejected: editing the expiring layer's period in place.** It is reachable
today (`sync.update_layer`, `sync.py:813-819`) and it is the wrong primitive:
it overwrites the expiring terms, so what the client actually had in 2025-26
becomes unreadable. On the TUI surface it is also unrevertible — see D7.

**Cost if wrong.** If a broker really does want to extend one policy rather
than renew it (a mid-term extension), that is an edit to the predecessor's
layer period, which stays legal under D1. The two operations must be named
differently on every surface or they will be confused; that naming is part of
"what must be true" below.

### D5 — Dropped, renewed, and pending are answered by the pair, not by a diagram

**Dropped cover is "present in the predecessor, absent from the successor."**
That is unambiguous *only* because the successor is always born as a full
clone (`sync.py:667`), so absence is always a deliberate removal. That
invariant is now load-bearing and must be asserted: a successor built by hand
rather than by clone destroys the distinction.

*Reasoning.* The entry is right that dropped and missing look identical in a
diagram, and it understates the problem: `remove_line` cascades the line, its
layers, its retentions and its sublimits out of the file
(`towerkit/edit.py:94-115`), and `line_ends` omits any line with no covering
layers without comment (`sync.py:937-939`). There is no tombstone. But the
**pair** answers it with no new data at all — which is the strongest single
argument for composing at read time rather than inventing a lineage object.
`compare_programs` already does exactly this shape of read, keyed on
`(carrier, layer_id)` across two programs, and already has the vocabulary:
`NEW` / `RENEWED` / `LAPSED` (`towerkit/compare.py:86-115, 15-17`). Layer ids
survive the clone, because `clone_as_renewal` deep-copies (`model.py:177`).

**Cost if wrong.** If a successor can be created any other way, "dropped"
becomes a guess again and a diagram will assert that a client lost cover they
still have. Mitigation is a test, not a design change.

### D6 — `Layer` gains an optional `placement`, exactly as it has an optional `period`

**`Layer.placement: Placement | None = None`, defaulting to the program's when
absent.** A half-renewed program is then: `Program.placement = PROPOSED`, the
renewed GL layer `BOUND`, the unrenewed IM layer `PROPOSED`. This is the one
format change in this document.

*Reasoning.* I tried hard to derive this and it cannot be derived. A layer
carried forward untouched and a layer renewed as-expiring with the same carrier
at the same share are byte-identical; only intent separates them, and intent is
not in the data. Nor can the dates carry it — whatever `clone_as_renewal` does
to layer periods (D8), it does uniformly, so the period cannot also mean
"someone has worked this one."

The reason to accept the format change rather than fight it: **this is not a
new kind of field.** `Layer.period` exists for precisely this reason and says
so — "programs can carry several policy effective/expiry dates, so the period
is per-layer" (`model.py:85-87`). A program can equally carry several placement
states. This is `Program.placement` (`model.py:149`) moved down one level for
the same reason `Program.period` was, and it inherits the same fallback shape,
the same optionality, and the same canonical-key treatment.

It is additive and optional, so every existing file stays valid under both
pydantic and JSON Schema. It should land together with **versioning
`SCHEMA_ID`** (`model.py:27`) — not because this change needs a discriminator,
but because the next one will and today there is nowhere to put it.

Derived state stays derived: "pending" already means `signed_bps == 0` and
already renders dashed (`mpl_program.py:126-128`), and `carrier_text` already
prints "To be placed" (`towerkit/soi.py:20-22`). A `PROPOSED` layer reuses that
treatment; it does not get a new one.

**Rejected: recording renewal state in `Layer.notes`.** Freeform fields that
secretly carry state are the `carrier_alias` / `merged_from` landmine
(`CLAUDE.md`). Declare the name.

**Rejected: recording it in bookkit alongside the edge (D2).** It is a fact
about a layer, and the file is the sole authority for layer facts. Splitting it
into SQL makes `proj_*` authoritative for one column, which is the rule this
project protects hardest.

**Cost if wrong.** Highest-cost item here after D1. If `Layer.placement` is the
wrong granularity — if renewal state is really per *participant*, because one
carrier renewed and another has not — then the field is in the wrong place and
files written with it need rewriting. This is the strongest candidate in this
document for a wrong call and it is the one I would most want Grant to check
against the real client (see *Open decisions*).

### D7 — The file-write guard moves to where the write is declared

**`write_through` marks its batch as file-touching, and captures the
pre-image.** Not the MCP tool name; the function that dumps the file.

*Reasoning.* This is a live defect, not a hypothetical, and layer-level
renewal makes it dangerous. `batches.revert` refuses a batch by testing
`batch.tool.startswith("program_")` (`batches.py:330-333`). The TUI does not
produce that prefix: `FormModal` derives its `BatchSpec` from the form title
(`tui/widgets/forms.py:93`, `forms/spec.py:68-77`), and the layer form's title
is `f"edit layer — {layer['name']}"` (`entity_actions.py:190`), so the tool
slug is `edit_layer`. The guard never fires. The commit runs inside that batch
(`forms.py:214-224`) and `write_through` → `project` → `placements.update`
logs real event rows (`sync.py:259-270`). So pressing `R` on a TUI layer edit
reverts the placement's cached period and totals **while the file keeps the
edit** — the exact lie `program_file_refusal` exists to prevent, stated in its
own comment: "reverting the proj_* cache under an untouched file would be a
lie" (`batches.py:331-332`).

Second half of the same defect: `program_files.capture` is called from exactly
one place, `mcpserver.py:1196`. The TUI's layer edit takes no snapshot, so
there is no file-side undo for it at all — `program_revert_file` has nothing to
restore.

This is `CLAUDE.md`'s own rule twice over: "Guards on identity belong in repo/
where every surface inherits them," and "opt-in would leave whichever one you
missed unreachable." The guard belongs in `write_through` (`sync.py:1009`),
which is the single function every program-file write already goes through.

**Cost if wrong.** None that I can see; this strictly widens an existing
guarantee. The risk is in *not* doing it: shipping layer-level renewal on the
TUI first means shipping a destructive write with no undo and a misleading one.

### D8 — `clone_as_renewal` must carry layer periods forward

**Bump each `layer.period` by a year alongside the program period**, with the
same Feb-29 clamp `_plus_year` already applies (`model.py:186-190`).

*Reasoning.* Proven above: today it does not, so a renewed file's staggered
line reads as expired months before the new program incepts, and the fresh
placement is created directly into the overdue bucket. Bumping asserts the
*intended* anniversary — IM still renews in November, one year on — which is
the right default and is what the broker would type anyway. Whether that layer
has actually been renewed is D6's job, not the date's.

**Cost if wrong.** If a renewal is genuinely expected to converge dates rather
than preserve them (see *Edge cases*), the bump writes a date the broker
immediately overwrites — an annoyance, not a lie, because D6 marks the layer
`PROPOSED` until someone confirms it. Leaving the bug in place is a lie.

### D9 — `renewal_on` counts to the earliest line whose cover is not bound onward

**Per line, over the chain as of today: find the layer in force; take its end;
if a `BOUND` layer in the successor starts on or before that end, the line is
covered onward and is not an attention item. Otherwise it is, and its end is a
candidate for `renewal_on`.** `renewal_on` is the minimum of the candidates.
The 120-day window and its bucket alignment are unchanged, and nothing overdue
falls off.

*Reasoning.* This preserves the entry's requirement — "a just-renewed layer
must not make an unrenewed one fall off the list" — for the right reason. The
current `min(ends[0][1], placement.period_to)` (`renewals.py:79-80`) happens to
be protective in the same direction, because the unrenewed line is the earlier
one, but it is protective by accident and its cap is wrong: once every line has
rolled forward, `renewal_on` is still pinned to the placement row's stale
`period_to` and the program reads overdue forever. Replacing the cap with "is
anything bound onward?" fixes both ends.

The invariant from `CLAUDE.md` holds unchanged: `days_remaining < 0` decides
overdue, and the date printed under `renews` is the date counted to.

**Cost if wrong.** If this over-suppresses, a real renewal falls off the
attention list — the failure mode `CLAUDE.md` calls out as never acceptable.
The mitigation is that suppression requires a `BOUND` layer, which requires
someone to have said so; `PROPOSED` never suppresses.

---

## The three consequences `CLAUDE.md` forces this choice to answer

**What a file MEANS.** A file is the cover as bound for one policy period of
one program — complete, self-contained, and still the sole authority for its
own structure. It is *not* "the program." "The program" is the chain of files
joined by D2's edge. This keeps `CLAUDE.md`'s rule intact rather than
weakening it: every fact about a layer still lives in exactly one file, and
`proj_*` stays rebuildable from the files plus one nullable link column that
`sync.renew` writes and nothing else derives.

**What an export is a snapshot OF.** Today an export is a snapshot of one
file, and it is already dishonest at the boundary: `_row` prints each layer's
own effective/expiration (`soi.py:125-138`), while `sheet_title` and
`default_filename` label the whole workbook with the *program* period's years
(`soi.py:170-185`). A schedule whose rows say 2026-07-01 under a tab named
"SOI - 25-26" is the misleading document the entry warns about, and it is what
the code produces now. Under this ruling an export is a snapshot of **the
chain as of a date**: every row is the layer in force on that date, the as-of
is printed on the sheet, and the title stops being derived from any one file's
period. `build_soi` (`soi.py:140-167`) becomes `build_soi(chain, as_of)`.

**What `revert_batch` can put back.** Unchanged in principle and correct as
written: `revert` refuses program-file batches (`batches.py:330-333`) because
file contents are not event rows, and the file-side path is
`program_revert_file` over `program_files`' snapshots. Renewing a layer is a
file write, so it belongs entirely on the file-side path. Two things must be
true first, both D7: the guard must fire for TUI writes (today it does not),
and the snapshot must be taken for TUI writes (today it is not). Note also the
retention limit — `SNAPSHOT_KEEP = 20` per directory (`program_files.py:22`,
pruned at `:67-75`) — which is fine for an editing session and is *not* an
archive. It is not a substitute for the predecessor file, which is the real
record of last year. That is another argument for D1.

---

## Edge cases

**A layer renewed early.** The successor's layer is `BOUND` with a period
starting later than today. The as-of read (D3) shows today's cover — the
predecessor's layer — and the successor's is visibly future. So the program
does *not* show next year's limit today; it shows both, marked. The
convergence-to-one-date view is where the future limit belongs.

**A line not renewed.** Two distinct states, and D5/D6 separate them.
*Pending*: present in the successor, `PROPOSED`. *Dropped*: absent from the
successor. Both are readable only against the predecessor, which is the point.
Never-entered is absent from both.

**Mid-term endorsement vs renewal.** An endorsement edits the *predecessor's*
layer — limit, premium, participants — and does not start a period. A renewal
writes the *successor*. Under D1 these are different files, so they cannot be
confused in the data. They can absolutely be confused in the UI, and the
surfaces must name them differently; see *What must be true*.

**A renewal that changes the tower's shape.** This already partly breaks
today, and it is worth naming because the fix is not in this spec.
`compare_programs` keys on `(carrier, layer_id)` (`compare.py:91-93`), and
`sync.add_layer` mints ids by slugifying the name (`sync.py:838`,
`_slug` at `sync.py:992-1003`). A layer that splits in two gets new ids, so
the comparison reads one `LAPSED` plus two `NEW` rather than "this split."
Under this ruling the composed view inherits that behaviour. It is honest —
nothing is asserted that is false — but it is not informative, and "the one
visual cannot assume the columns match" is exactly right. Layer identity
across a restructure is a separate question; this spec only requires that the
composed view never *invents* a match.

**Converging renewal dates.** This is the goal of the exercise and it needs to
be visible as progress. Under D8 the successor's IM layer is born at the old
anniversary bumped a year; converging it means the broker sets a **short
period** on that layer — a stub term ending on the target date. The model
accepts it (`end > start` is the only rule, `validate.py:212-217`) and the
composed view will show it correctly. What the model cannot tell is that the
short term is *deliberate* rather than a typo, and there is no warning today
that would flag it either way. See *Open decisions*.

**Attention while half-renewed.** D9. Restating the invariant plainly because
it is the one `CLAUDE.md` protects hardest: a `BOUND` successor layer removes
*its line* from the count and nothing else; every other line keeps its own
clock; a lapsed line with nothing bound onward stays overdue indefinitely and
cannot fall off the list.

---

## Visualisation — one function, two chromes

Internal and client-facing are genuinely different problems, but D3 makes them
the same *function* with different arguments, which is what stops them
drifting. Both compose the chain as of a date.

**Internal.** Working view, `as_of = today`, chrome on: per-layer periods
shown, `PROPOSED` layers dashed, dropped lines rendered as absent-with-a-mark
against the predecessor. Note the correction above — the display half is
mostly built. `cell_dates` already prints `layer.period or program.period` per
cell (`mpl_program.py:156-159`); dashed outlines for not-yet-real layers
already exist keyed on `signed_bps == 0` (`mpl_program.py:126-128, 168-191`).
The work is to key the dashed treatment on D6's `Layer.placement` as well, and
to render the predecessor's dropped lines — not to invent a time axis. Time
stays a per-layer label plus a per-layer state; it does not become a spatial
dimension. Making it one would mean rebuilding `layout.py`'s dollar geometry,
for a picture a broker reads once a year.

**Client-facing.** `as_of` chosen and printed, chrome off: only cover in force
on that date, no `PROPOSED` layers, no dropped-line marks, and a stated
as-of on the document. The entry's "these three lines renew in March" is a
footnote derived from the same composition — the lines whose in-force layer
ends before the next anniversary.

**Why this matters for R66.** The Program tab is still a stub
(`web/templates/account/program.html:3-7`), so the HTML and SVG renderers do
not exist yet and can be built agreeing about this from the start. The thing
they must agree about is not pixels; it is that **both take `(chain, as_of)`
and neither may be called without it**. An HTML view that quietly defaults to
"today" and an SVG export that quietly defaults to "the file's period" would
render two different truths from one click, and the export is the one that
leaves the building.

---

## What must be true before code starts

1. **D7 lands first.** The file-write guard and the pre-image capture move
   into `write_through` (`sync.py:1009-1037`), with a test that a TUI layer
   edit is refused by `batches.revert` and restorable by
   `program_revert_file`. Layer-level renewal must not ship onto a surface
   with no undo.
2. **D8 lands second**, with a test that clones a program carrying a
   staggered layer and asserts the clone's `line_ends` are all inside the new
   program period. That test fails on today's code.
3. **A test that a successor is only ever created by clone**, because D5's
   dropped-vs-missing distinction rests on it.
4. **`Layer.placement` is added additively and `SCHEMA_ID` is versioned in the
   same commit**, with a round-trip test proving every existing file in
   `towerkit/programs/` loads, dumps byte-identically, and validates clean
   without the new key.
5. **A backup before any of it.** Nothing here is a destructive schema
   migration — D2 is an additive `ALTER TABLE`, D6 is an optional JSON key —
   but D6 rewrites program files on the next save, and program files are the
   authority. `./bookctl backup` before the first write, and the drill is run
   against seeded sample data locally, never Grant's book
   (`CLAUDE.md`, Process).
6. **The MCP surface gains the layer period** it is already missing
   (`mcpserver.py:398-412`) — not as part of this design, but because leaving
   it out is what produced the report that started this.
7. **A naming decision, applied to all three surfaces at once**, separating
   "endorse this policy" (edits the predecessor) from "renew this line"
   (writes the successor). One verb for both is how these get confused.

---

## Recommend against

- **A `renew_line` MCP tool built before any of the above.** It is the obvious
  read of the report and it is the wrong first move: without D2 there is no
  successor to write into that the book can find again, without D6 the result
  is indistinguishable from a carried-forward clone, and without D7 it is a
  destructive write with no undo on the surface Grant uses most.
- **Teaching `_check_line_stack` about time** so one file can span both years.
  It is the change that would make D1 unnecessary, and it is the most
  dangerous change available here — that function is what catches gaps and
  overlaps in real placements (`validate.py:278-333`).
- **Deriving anything further from file names.** `_bump_years`
  (`sync.py:603-605`) is already doing more than it should.

---

## Open decisions

These are Grant's, not mine. Each changes what gets built.

1. **Is renewal state per layer, or per participant?** D6 puts it on the
   layer. If, on the real client, one carrier on a shared layer has renewed
   and another has not, the field is in the wrong place and files written with
   it need rewriting.
   *Recommendation:* per layer. A layer is the issued policy — that is why
   `policy_number` and `period` live there (`model.py:85-87`) — and a
   part-renewed layer is more naturally two layers.
   *Cost if wrong:* files rewritten; a second additive field, and a migration
   over anything already saved.
2. **Does a mid-renewal client schedule go out as one document as of today, or
   as two?** D3 makes either possible; the default has to be chosen, because
   it is what the first client actually receives.
   *Recommendation:* one document, as-of today, with the as-of printed and a
   footnote naming the lines that renew later. Two documents make the client
   reconcile them.
   *Cost if wrong:* a client reads a schedule as their full programme when it
   is a slice — the failure the entry names.
3. **When dates converge, is a short stub term a first-class thing the model
   should recognise?** Today it is just a period with `end > start` and
   nothing distinguishes deliberate convergence from a typo.
   *Recommendation:* leave it underived for now; do not add a flag. Show the
   short term plainly in the internal view and let the broker read it.
   *Cost if wrong:* a mistyped date renders as an intentional stub and nothing
   warns — the shape of the `parse_human_date` bug, on a field with no
   guard.

---

## Suggested next step

Take the real client's two files and hand-build the composed read for three
dates — before the first renewal, mid-renewal, after convergence — as a
throwaway script against a copy. If D5 and D6 answer "dropped", "pending" and
"renewed" correctly on real data at all three dates, the model is settled and
the implementation plan can be written. If they do not, the failure will be in
D6, and it is better to find it in a script than in a schema.



---

## Verification report (independent adversarial pass, 2026-08-18)

**Verdict: needs-revision.** The document's two original discoveries are real and I reproduced both by executing towerkit: clone_as_renewal leaves layer periods stale (the clone's IM line ends 2026-06-03 under a 2026-09-01→2027-09-01 program, validating clean), and a single file cannot hold two policy years of one layer (the exact line-overlap string the draft quotes). The TUI batch-guard evasion also checks out end to end from source. But two load-bearing premises are false and both sit under decisions the draft says must land first: `write_through` is NOT the single program-file write path (renew at sync.py:668, scaffold at :739 and imports/commit.py:165 all dump directly), so D7's guard as placed would leave renew — the most important write in this spec — unguarded and unsnapshotted; and "the successor is always born as a full clone" is not true today, which removes the foundation D5 rests its dropped-vs-pending ruling on. The weakest point overall is that the draft applies CLAUDE.md's own "opt-in leaves whichever one you missed" lesson to the TUI while committing the same error one function over.


### Citations that did not check out

- **`src/bookkit/sync.py:1009 (citation 68) — "write_through is the single function every program-file write goes through"`** — claimed: D7: "The guard belongs in `write_through` (sync.py:1009), which is the single function every program-file write already goes through." Also underwrites citation 21's "unreachable through every bookkit write path."
  
  *Actually:* FALSE. `dump_program` is called from FOUR places in bookkit: sync.py:668 (renew — dumps the clone directly, never re-validated), sync.py:739 (scaffold_program), sync.py:1037 (write_through), imports/commit.py:165, plus seed.py:312. sync.py:745's own section comment scopes the truth precisely: "--- transactional program EDITS (all via write_through) ---". Edits go through it; CREATIONS do not. (Citation 21's separate conclusion — that a two-policy-year file is unreachable — happens to survive anyway, because scaffold validates at sync.py:735, imports validate via `draft.to_program()` at commit.py:157, and renew's clone comes from a source validated at sync.py:635-639. But it survives by coincidence of four paths, not because one function owns the write.)

- **`towerkit/src/towerkit/model.py:186-190 (citation 5) — _plus_year Feb-29 clamp`** — claimed: D8: "with the same Feb-29 clamp `_plus_year` already applies (`model.py:186-190`)"
  
  *Actually:* `_plus_year` is at model.py:216-220. Lines 186-190 are inside the `underlying_tops` docstring, about attachment order deciding what is "beneath" a follows-underlying layer — unrelated. The rule the draft describes is real and correctly stated; the pointer is to the wrong function.

- **`src/bookkit/sync.py:650-651 (citation 12) — renew bumps BOTH placement period dates`** — claimed: "renew bumps BOTH placement period dates by a year via _plus_year_iso (sync.py:650-651)"
  
  *Actually:* Line 650 is blank; 651 is `from_date = _plus_year_iso(placement.period_from)` and 652 is `to_date = _plus_year_iso(placement.period_to)`. The claim is true; the cited range contains only half of it.

- **`src/bookkit/sync.py:641-644 (draft body, D2) — "_bump_years on the file stem"`** — claimed: "the only link is `_renewed`'s match … plus `_bump_years` on the file stem (`sync.py:641-644`)"
  
  *Actually:* The stem bump is sync.py:640 (`stem = _bump_years(path.stem)`). Lines 641-644 are the FALLBACK for a stem containing no year, which appends the new start year instead. The claim is true at 640.

- **`src/bookkit/sync.py:937-939 (citation 8 / correction 4) — "without comment"`** — claimed: "`line_ends` omits any line with no covering layers without comment (sync.py:937-939)" / "silently skips … with no signal"
  
  *Actually:* The behaviour is documented explicitly in line_ends' own docstring at sync.py:927-929: "Lines with no layers (TBD, unplaced) are omitted — there is no policy to expire." The runtime silence is real; "without comment" is not. It is a deliberate, stated rule, which changes the argument from "nobody noticed" to "someone decided, for unplaced lines, and a dropped line was not considered."

- **`src/bookkit/services/batches.py:319 (citation 65) — revert takes `now` as a parameter`** — claimed: D3: "`as_of` is a parameter, never the wall clock — the same rule `batches.revert`'s `now` already follows (`services/batches.py:319`)."
  
  *Actually:* Line 319 is the docstring's first line ("Put the book back the way it was before this batch."). The signature carrying `now: str` is 316-318; the stated rule "`now` is a parameter, never the wall clock" is line 323. Claim true, pointer off.

- **`src/bookkit/services/batches.py:291-305 (citation 48) — "its comment states that reverting the proj_* cache … would be a lie"`** — claimed: "`program_file_refusal` … its comment states that reverting the proj_* cache under an untouched file would be a lie" / draft body: "the exact lie `program_file_refusal` exists to prevent, stated in its own comment"
  
  *Actually:* program_file_refusal (291-305) contains no such comment — its docstring is about why the sentence was extracted for the web surface. The "would be a lie" comment lives at batches.py:331-332, inside `revert`. The draft body cites 331-332 correctly elsewhere, so this is an attribution muddle rather than an invention.


### Claims challenged (even where the citation resolved)

- **[CRITICAL]** D5: "Dropped cover is 'present in the predecessor, absent from the successor.' That is unambiguous *only* because the successor is always born as a full clone (`sync.py:667`), so absence is always a deliberate removal."
  
  *Evidence:* Stated in the present tense as a property of today's code; it is not one. sync.py:666 gates the clone on `new_path is not None and program is not None` — a placement with no `program_path` is renewed with NO file at all (sync.py:630). Two other bookkit paths create program files from scratch: `scaffold_program` (sync.py:711-739, one TBD line, one 'To be placed' layer) and `imports/commit.py:165` (from a pasted draft). towerkit's own editor is a fourth. Any of those can produce the file that is in fact next year's program, and against such a successor 'absent' means nothing. The draft concedes the invariant "must be asserted" but then rests D5's whole ruling — "the pair answers it with no new data at all" — on the invariant already holding. It does not. Either D2's edge must carry the fact that the successor was cloned, or renew must become the only way to mint a successor, and that is design work D5 currently assumes away.

- **[CRITICAL]** D7: the file-write guard and pre-image capture belong in `write_through`, "which is the single function every program-file write already goes through" — presented as the fix that must "land first".
  
  *Evidence:* The premise is false (see citation failures). Landing the guard only in write_through leaves sync.renew (sync.py:668), sync.scaffold_program (sync.py:739) and imports/commit.py:165 writing program files with no batch marking and no snapshot — and renew is the single most important write in this entire spec. This is precisely the failure CLAUDE.md records at 2026-08-15 ("Batching the shared entity_actions.push_form looked right and went green — while 33 call sites built FormModal directly"), which the draft itself quotes two paragraphs earlier. The guard belongs at `dump_program`'s bookkit-side boundary — a single wrapper every one of the four call sites must go through — not at write_through, or the D7 test will pass while renew stays unguarded. The underlying defect D7 names is real and I confirmed the whole chain from source: entity_actions.py:190 title → forms.py:92-93 `BatchSpec.for_title` → spec.py:74-76 slug `edit_layer` → batches.py:330 `startswith("program_")` never fires; FormModal is constructed at entity_actions.py:232 with no `batch=` opt-out, and no test in tests/ asserts otherwise. Only the placement of the fix is wrong.

- **[IMPORTANT]** Correction 1 / the "Wrong" section: renewal_on is "capped by the **placement row's** `period_to`, not the file's program period. Those diverge exactly when the program is half-renewed … once every line has been rolled forward, `renewal_on` is still pinned to the stale placement row and the program reads as permanently overdue."
  
  *Evidence:* Half right, and the half that is wrong is the half the draft calls load-bearing. The genuine finding — there is no liveness filter in `_renewal_on` (renewals.py:76-81) — holds. But placement.period_to is NOT independent of the file: `project()` writes `period_to=program.period.end.isoformat()` on every projection (sync.py:262), and every bookkit path that changes a placement period writes through the file first (the TUI placement form calls sync.update_program at entity_actions.py:129 before touching the book). So for exactly the population that HAS line_ends (file-linked, projected placements) the cap IS the program period end, and the ROADMAP's wording is accurate. Divergence needs an un-reprojected external towerkit edit, which the next bookkit write refuses anyway (WriteConflict, sync.py:1026). The "permanently overdue" scenario requires every layer period pushed past the program period end *inside one file* — the in-place-renewal world D1 explicitly forbids — so it is not evidence about the current cap, it is a consequence of the rejected design. Separately: the word "live" the ROADMAP used is lifted from the code's own comment at renewals.py:33-34 ("the earliest live line end when the placement is file-linked"). The draft says "there is no liveness filter anywhere" without noting that the comment is the source of the entry's wording and is itself wrong — so the fix list should include correcting renewals.py:33-34, and the correction should be aimed at the code comment, not at the ROADMAP author.

- **[IMPORTANT]** D5: "`compare_programs` already does exactly this shape of read … and already has the vocabulary: NEW / RENEWED / LAPSED" — offered as evidence that "the read-time composition primitive option (c) needs already exists."
  
  *Evidence:* The vocabulary exists (compare.py:15-17); the primitive does not answer D5's question. `DeltaRow` (compare.py:20-31) has no line, no applies_to, no period — it is keyed (carrier, layer_id) only, so "this LINE was dropped" is inexpressible. Worse for D6: `_cells` iterates `layer.participants` (compare.py:68-83), so a layer with NO participants contributes no rows at all — and a layer with no participants is exactly what towerkit calls pending ("To be placed", soi.py:20-22; mpl_program.py:126-128) and exactly what a PROPOSED unrenewed layer will be. Compare a predecessor against a successor whose unrenewed lines are still to-be-placed and those lines are invisible, not LAPSED. The composition primitive D5 relies on has to be written; it is not sitting there.

- **[IMPORTANT]** "What must be true" item 3: "A test that a successor is only ever created by clone, because D5's dropped-vs-missing distinction rests on it."
  
  *Evidence:* Not falsifiable as written — I cannot name a mutation to production code that makes it fail, because there is no enumeration anywhere of "the paths that create a successor", and three creation paths already exist that are not clones (sync.py:739, imports/commit.py:165, towerkit's editor writing a file bookkit later links). A test asserting the invariant would either assert something about a code path it picks arbitrarily, or pass vacuously. This is the decoration-test shape the ledger keeps recording. It becomes real only if restated as an enforced runtime guard — e.g. D2's edge records HOW the successor was minted, and any composed dropped/pending read refuses to answer when the edge says 'not a clone'. The other three tests are sound: item 2 (clone's line_ends inside the new program period) fails on today's code — I ran it, the clone of a 2025-09-01→2026-09-01 program with an IM layer ending 2026-06-03 yields line_ends [('IM', 2026-06-03), ('GL', 2027-09-01)] with zero validation errors; item 1 fails if the guard is reverted to the tool-name prefix; item 4 has a real corpus (towerkit/programs/atomic-2026.json, atomic-2027.json) and fails if _LAYER_KEYS or the dumper drifts.

- **[MINOR]** D6: "It is additive and optional, so every existing file stays valid under both pydantic and JSON Schema. It should land together with **versioning SCHEMA_ID** … because the next one will [need a discriminator] and today there is nowhere to put it."
  
  *Evidence:* The first half is true; the second half ignores the precedent this exact codebase already established for exactly this problem. model.py:273-276 documents the pattern in a comment on `soiSchematic`: "emitted only when true (the followsUnderlying pattern): untouched programs re-save byte-identically, and older towerkit wheels only reject files that USE the feature." That is the answer to forward-compatibility for an optional layer field, and it is stronger than versioning SCHEMA_ID (which no reader currently branches on). The draft also omits the full edit surface for D6: `_LAYER_KEYS` (model.py:232-236), `program_to_jsonable`'s hand-written layer dict (model.py:290+), `program_from_jsonable`, and the layer subschema's `additionalProperties: false` all have to change in the same commit — `_ordered` raises RuntimeError on a model field missing from the canonical order (model.py:246-249), so it fails loudly, but it is four coordinated edits, not one field.

- **[MINOR]** D7: "pressing `R` on a TUI layer edit reverts the placement's cached period and totals while the file keeps the edit."
  
  *Evidence:* The defect is real; the inventory is overstated. A layer edit does not change `program.period`, so `project()`→`placements.update` (sync.py:256-269) logs no period change and there is nothing period-shaped to revert. What reverts is `total_limit` / `total_premium` (and, for a layer period edit specifically, nothing at all in the placement row — the whole edit lives in the file and in proj_layer, neither of which revert touches). The lie is still a lie; on a period-only layer edit it is a quieter one, and on the premium case it is exactly as described.

- **[MINOR]** Correction 4: "`remove_line` hard-cascades the line, its layers, its retentions and its sublimits out of the file (towerkit/edit.py:94-115)."
  
  *Evidence:* Only those left with an EMPTY appliesTo are removed (edit.py:101-115) — a layer covering GL and AL survives the removal of AL with the id stripped out. The docstring says so at 95-98. This does not weaken the no-tombstone conclusion (nothing records the removal either way); it does mean "dropped" and "restructured" are also indistinguishable, which is a case the draft's edge-case section does not cover.

- **[MINOR]** D1 rejection: one file spanning both years "breaks the frozen canonical key order whose stated purpose is readable git diffs *between renewal years* (`model.py:226-231`) — if the years live in one file there are no diffs between years to read."
  
  *Evidence:* The comment at model.py:225-226 says key order is frozen so that arbitrary TUI reformatting does not make diffs unreadable; "between renewal years" is the example, not the purpose, and nothing about key order stops one file holding two years. This is a rhetorical flourish on top of a decision that is already settled by the validator argument I confirmed by execution (a two-year GL stack returns exactly `line-overlap | gl: OVERLAP Primary GL 25→Primary GL 26 at $2,000,000 vs $0`, the string the draft quotes). D1 does not need this argument and is weaker for carrying it.


### Decisions the draft left open

- **Is renewal state per LAYER or per PARTICIPANT? D6 adds `Layer.placement: Placement | None`. If, on the real client, one carrier on a shared layer has renewed while another has not, the field is at the wrong granularity.**
  - Recommendation: Per layer. A layer is the issued policy — that is why `policy_number` and `period` already live there (towerkit model.py:85-87) — and a genuinely part-renewed layer is more naturally modelled as two layers than as a layer with mixed state.
  - Cost if wrong: Every file written with the field needs rewriting, plus a second additive field and a migration over anything already saved. This is the highest-cost item in the document after D1 and the one most worth checking against the real client's two files.

- **Does a mid-renewal client schedule go out as ONE document as of today, or as TWO (last year's remaining cover and next year's incepting cover)? D3 makes either buildable; the default is what the first client actually receives.**
  - Recommendation: One document, as-of today, with the as-of date printed on the sheet and a footnote naming the lines that renew later. Two documents push the reconciliation onto the client.
  - Cost if wrong: A client reads a partial schedule as their full programme — precisely the 'document that misleads' the ROADMAP entry warns about, and the one artefact that leaves the building.

- **When renewal dates converge onto one date, the converging layer gets a SHORT stub term. Should the model recognise that as deliberate, or leave it as an ordinary period? Today nothing distinguishes an intentional stub from a mistyped date — the only rule is `end > start` (towerkit/validate.py:212-217).**
  - Recommendation: Leave it underived. Do not add a flag or a warning yet; show the short term plainly in the internal view and let the broker read it. Add the guard only once convergence has been done for real once or twice.
  - Cost if wrong: A mistyped expiry renders as an intentional stub and nothing warns — structurally the same failure as the bare-number date bug, on a field with no guard at all.


### Needs Grant

- Is renewal state per layer or per participant? (D6 — decides the one format change in this spec, and rewriting files later is the expensive undo.) Recommendation: per layer.

- Does a mid-renewal client schedule go out as one as-of document, or two? (Product call; decides what the first client actually receives.) Recommendation: one, with the as-of printed.

- Should a short 'convergence' stub term be a recognised thing the model can warn about, or just an ordinary period? Recommendation: leave it underived for now.


### Corrections this draft makes to the ROADMAP entry

- ROADMAP said: `services.renewals.RenewalItem` … counts `days_remaining` to `renewal_on` — the earliest LIVE line end, capped by the program period.
  - Code says: There is no liveness filter anywhere. `_renewal_on` returns `min(ends[0][1], placement.period_to)` — the earliest line end full stop, capped by the PLACEMENT ROW's `period_to`, not the file's program period. The distinction matters exactly in the half-renewed case: once every line has rolled forward, `renewal_on` stays pinned to the stale placement row and the program reads as permanently overdue. (`src/bookkit/services/renewals.py:76-81`)

- ROADMAP said: There is no line-level or layer-level renewal anywhere. That asymmetry — read staggered, write whole — is exactly what MCP hit.
  - Code says: True of renewal, false of the layer PERIOD, and the distinction identifies the actual defect. `sync.update_layer` accepts `period_from`/`period_to` (sync.py:794-795) and writes `layer.period` (sync.py:813-819); the TUI's layer form exposes both fields and passes them (entity_actions.py:197-198, 221-222). The MCP tool omits them from its signature (mcpserver.py:398-412) and `_program_layer_edit` never passes them (mcpserver.py:1261-1281). 'It could not renew one line' is a property of ONE SURFACE, not of the model or the service — the same capability-present-on-one-surface shape as the 33 unbatched FormModal call sites. (`src/bookkit/sync.py:813-819 vs src/bookkit/mcpserver.py:398-412`)

- ROADMAP said: Internal: … Time becomes a dimension of the tower diagram, which it is not today.
  - Code says: Per-layer periods are already renderable and already a saved program setting. `cell_dates` prints `layer.period or program.period` into each participant cell (mpl_program.py:156-159) and is persisted in RenderSettings (model.py:139). A per-layer not-yet-real state also already exists and renders dashed, keyed on `signed_bps == 0` (mpl_program.py:126-128, 168-191). Time is not a STATE in the diagram; it is already a LABEL. That changes the internal-visualisation work from 'invent per-layer period display' to 'key the existing dashed treatment on renewal state too'. (`towerkit/src/towerkit/render/mpl_program.py:156-159`)

- ROADMAP said: A line not renewed: dropped cover has to read as dropped, not as missing data. The two look identical in a diagram unless the model distinguishes them.
  - Code says: Correct, and stronger than stated — they are identical in the DATA, not just the diagram. `remove_line` hard-cascades the line, its layers, its retentions and its sublimits out of the file (towerkit/edit.py:94-115), and `line_ends` silently omits any line with no covering layers (sync.py:937-939). There is no tombstone anywhere in the format. The distinction is only recoverable by reading the predecessor file alongside the successor, which is a direct argument for composing at read time over a linked pair rather than inventing a lineage object. (`towerkit/src/towerkit/edit.py:94-115`)

- ROADMAP said: (Not claimed — missing from the entry.) `sync.renew` … clones the whole file forward with `clone_as_renewal`, bumps BOTH period dates by a year.
  - Code says: It bumps both PROGRAM period dates and leaves every LAYER period stale. `clone_as_renewal` sets `clone.period` only (model.py:178-180); layers arrive via `model_copy(deep=True)` with last year's dates intact. Executed against the shape seed.py:343-352 calls 'the norm': a 2025-09-01→2026-09-01 program with an IM layer ending 2026-06-03 clones to a 2026-09-01→2027-09-01 program whose IM layer still reads 2025-09-01→2026-06-03. `line_ends` then returns ('IM', 2026-06-03) for the NEW file and the fresh renewal placement is created directly into the overdue bucket. Nothing refuses it: the only layer period rule is `end > start` (validate.py:212-217), there is no containment check, and validating that file returns zero errors. (`towerkit/src/towerkit/model.py:174-182`)

- ROADMAP said: (Not claimed — missing from the entry, and it decides the ruling.) Whatever gets built has to answer whether the continuity thread is a new concept, a link between files, or a view composed at read time.
  - Code says: One of the three is already impossible. A single file cannot hold two policy years of the same layer: `_check_line_stack` is dollar-space only (validate.py:278-333) and refuses it as `line-overlap: gl: OVERLAP Primary GL 25→Primary GL 26 at $2,000,000 vs $0`. Since `write_through` validates before it dumps (sync.py:1031-1037), that state is unreachable through every bookkit write path. So 'a view composed at read time' can only mean composed over TWO files, never over one — which forces the identity question to be answered by a link, not by a container. (`towerkit/src/towerkit/validate.py:278-333`)

- ROADMAP said: (Not claimed — a live defect this spec depends on fixing.) `revert_batch` refuses program batches; the file-side path is `program_revert_file`.
  - Code says: The guard is keyed on a string the TUI does not produce. `batches.revert` tests `batch.tool.startswith('program_')` (batches.py:330-333), while FormModal derives its BatchSpec from the form title (forms.py:93, forms/spec.py:68-77) and the layer form's title is 'edit layer — {name}' (entity_actions.py:190) — tool slug `edit_layer`. The guard never fires. The commit runs inside that batch (forms.py:214-224) and the write logs real placement events (sync.py:259-270), so pressing `R` on a TUI layer edit reverts the cached period and totals while the FILE keeps the edit — the exact lie program_file_refusal's own comment says it exists to prevent. Compounding it: `program_files.capture` is called only from mcpserver.py:1196, so a TUI layer edit takes no snapshot and has no file-side undo either. (`src/bookkit/services/batches.py:330-333`)
