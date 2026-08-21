# Input integrity — the research pass and its fixes — 2026-08-20 (overnight)

Branch `feat/d6-towerkit-fields`, **committed, not pushed, not merged**.
Continues the same branch as D6 (see `20260820-D6-TowerkitFields.md`).

Research artifact, kept updated at the same URL:
https://claude.ai/code/artifact/6125dd24-4993-464f-a001-1d6fb2a7674d
D6 report: https://claude.ai/code/artifact/ac507f53-6b0e-4bc2-8601-41e8d08eebab

## Goal

Grant reported the Program tab's layer details row as too dense ("fields blend
together"), asked for help with sublimits and templates, and asked for web
research on dense-UI design and data integrity for this kind of data. Then:
implement what the research argues for, document the sources, and — overnight —
fan agents out across work surfaces, task surfaces and data collection,
building on what the program work established.

## The research, and the one conclusion that reversed a recommendation

Rules and sources: `.claude/skills/data-entry-integrity/SKILL.md`. Load-bearing
ones promoted into CLAUDE.md (`## Data entry and input integrity`).

**The density diagnosis was wrong.** Expert users want density; hiding what
someone frequently needs relocates the cost rather than removing it (Nielsen's
own caveat on progressive disclosure). The defect was PROXIMITY — five kinds of
fact rendered as one inline run. Fixed by grouping, not thinning.

**A "sublimit ≤ the limit it sits inside" validator would be WRONG**, and I had
proposed it before the research. Personal & advertising injury bypasses the
occurrence limit and connects straight to the general aggregate; property
catastrophe sublimits (flood, earth movement) are typically ANNUAL AGGREGATE
and carved *out* of the occurrence limit. The check cannot exist until the
sublimit's basis is a field. Do not resurrect the rule without it.

## Grant's decisions tonight

- **No negative money.** Settled; `parse_money_cents` accepted `-1,000.00` while
  refusing `-1000`.
- **Contact role**: my call, taken as "picker over CONTACT_ROLES *plus any
  distinct role already in the book*" — constrains new entry, strands nothing
  already typed, needs no production data check first.
- **Sublimits deferred** until he and I can talk. The model gap is real
  (`Sublimit` cannot say occurrence-vs-aggregate or express a percentage;
  `Layer.limit` is a single number so "$1M per occ / $2M aggregate" cannot be
  stored faithfully) and is a towerkit change plus a backfill of his real files.

## Landed on the branch

| Commit | What |
|---|---|
| `86bc8d9` | the research pass: details-row grouping, theme picker + export pathway, blank options, Today account names, `/items`, DRY + data-entry rules in CLAUDE.md, the skill |
| `2d608d6` | the convention test over every form select (the first blank-option test was vacuous) + items.py double query |

### Details row: grouped, not thinned
`_layer_details.html` is a label rail with one group per row (policy, coverage,
named limits, applies to, structure) and exactly ONE column allowed to wrap.
Same eight values, nothing hidden. CSS is scoped to `.detail-grid` —
`.detail-label` has other callers (Today, compare, the chart strip) whose
margins must not change.

### Theme is settable, and only to what is storable
`render.theme` shipped in D6 as free text holding a path. towerkit's validator
refuses an ABSOLUTE theme path (program files are portable by contract), and
because every later write re-validates the file, one bad value **wedged the
whole program** until the JSON was hand-edited. Now: a picker filtered to
relative paths, checked server-side with `checked_option`, and `_export_tower`
resolves the stored value by name and REFUSES rather than substituting the
default (a client chart in the wrong brand that looks like it worked is worse
than no chart).

### Every select renders a blank option
Without one the browser pre-selects row 1 and `required` is satisfied by a value
nobody chose. Six were affected — `response_form.status` filed a forgotten
decline as **quoted**; `submission_form.market_org_id` took the first market;
`assignment_form.team_member_id` the first colleague; plus
`subjectivity_form.status`, `appetite_form.appetite`, `interaction_form.type`.
The layer-add `line` select now asks instead of taking "all lines".
`tests/test_form_selects.py` is a convention test over every builder.

### Today names the account
Ten hand-written copies of the same anchor, and the tasks table's row carried no
name, so it printed `ACC-0004`. One macro (`macros/account.html`) over one
lookup (`repo/orgs.labels_for`, returning ref and name together, replacing an
N+1 loop).

### `/items` — open items across the book
New nav section. Inline editing, filters that are URLs, capture with an account
picker. **It owns no writes**: every cell posts to the account-scoped route in
`routes/work.py` that already serves it, because those answer with the cell
alone and are therefore correct on any page. Only `done` differs, and only in
what it re-renders — the write is `work.complete_task`, shared.

## Overnight agent fan-out — ALL FIVE LANDED

Five agents, disjoint file ownership, none of them running git. Each was told
to mutation-check every test it writes.

| Agent | Area | Owns |
|---|---|---|
| A | parsers, bounds, refusal wording | `money.py`, `forms/spec.py` |
| B | web error UX, label visibility | `inline-cell.js`, `app.css` error styles, `macros/cell.html`, `macros/form.html` |
| C | vocabularies on task surfaces | `repo/vocab.py`, `forms/inline.py`, `routes/work.py` wiring |
| D | cross-field consistency | new `services/consistency.py`, `forms/entities.py`, `mcpserver.py` |
| E | data collection / imports | `imports/**`, `import_screen.py`, `paste_import.py` |

**All five reported. Full gate run by the orchestrator AFTER the last one
finished — 1979 passed, mypy clean, ruff clean.** Each agent's own "green" was
measured while others were still writing and is not authoritative; only the
final gate is. Committed in six per-agent slices, `0f493b0`..`e5b3b7f`.

Mutation totals, all killed: A 13, B 16, C 10, D 16, E 16. Three agents caught
a vacuous test of their own and fixed it — B's two assertions were satisfied by
the PROSE in inline-cell.js (its comments name every class it clears), and D's
item-due guard survived a truthiness revert because the difference is only
observable on a legacy bad row.

### What each landed

- **A** — negatives refused consistently (the sign was read in one of two parse
  branches); `Field` min/max from one BOUNDS registry keyed by column, so
  `probability_pct` says "enter a whole number from 0 to 100" instead of
  surfacing SQLite's CHECK error; money/int/select refusals shaped like
  `date_refusal`.
- **B** — a delegated `input` listener clears `.cell-error` / `.cell-error-msg`
  / `.form-error` on the first keystroke (scopes walked UPWARDS: a refused
  named-limit add renders its message outside the form being corrected);
  `.market-unlinked` de-stacked (it is a neutral state, not an error); visible
  labels on six in-row forms, three of which had no accessible name at all;
  the inline cell editor's blank option made unconditional.
- **C** — contact role is a picker over declared ∪ book values, wired through
  the SAVE path as well as the editor (only the save-path test caught the two
  disagreeing); `rfi_item_fields(conn)` mirror; the third copy of the placement
  statuses collapsed onto `PLACEMENT_FIELDS`.
- **D** — `services/consistency.py`, called from `apply_*` and the MCP paths,
  with `_edit_field` guarded BOTH ways per pair because a single-column write is
  the shape a cross-field rule is invisible to. Service layer, not DB CHECKs, so
  a pre-existing bad row stays repairable — two tests prove it.
- **E** — the template's example row moved off the data sheet (the reader asks
  by sheet NAME, so re-ordering tabs cannot feed it back); the percent-formatted
  commission refuses rather than being reinterpreted; a matched update writes
  the period it used to discard; zero records refuses before the snapshot; the
  paste modal's failed re-stage no longer leaves the previous parse live under a
  green verdict.

### Verified by the orchestrator, not just reported
- all six negative spellings refused, positives still parsing cents
- `probability_pct` bounds reaching the real declared field
- contact role: 11 declared + 1 book-only offered, the oddball re-saving, an
  unknown one refused
- the template's `Import` sheet header-only; an untouched template exiting 1;
  the ambiguous commission naming both readings
- the `input` listener delegated off `document.body` with comments stripped
- every main page still 200 on the final code

## Gotchas carried forward

- **Never `git add -A` while agents are running.** Mine swept partial work from
  four of them into a commit whose message described only my own. Recovered with
  `git reset --mixed` (working tree untouched) plus a safety patch, then
  committed in slices. Same family as the mutation-harness trap below.
- The Bash tool's cwd RESETS between calls — absolute paths or `git -C`, always.
  An hour of D6 was written into the main working tree because of this.
- **Commit before mutation testing.** `git checkout -- <file>` in a mutation
  harness discards every uncommitted change in that file, not just the mutation.
- A picker must offer only what the system will ACCEPT. The theme bug is the
  worked example.
- `.detail-label` is shared; scope details-row styling to `.detail-grid`.
- The demo book lives at `scratchpad/demo.db` with program files in
  `scratchpad/programs/`; a theme test needs a RELATIVE `themes/` dir beside the
  server's cwd (deliberately not committed).

## Production checks — BOTH CLEARED (Grant, 2026-08-21)

Two changes shipped with a dependency on real data. Grant ran both on the
production machine and both came back empty:

- **Negatives**: no negative money anywhere in the book, so refusing them
  cannot make an existing record unsaveable. The risk noted in `0f493b0`'s
  commit message is closed — do not re-raise it.
- **Commissions under 1%**: none, so nothing was imported at one hundredth of
  its value by the old percent-formatted-cell parse. The fix in `9ea55a5` is
  forward-looking only; there is no historical data to correct.

Queries kept at `scratchpad/negatives-check.sql` and
`scratchpad/commission-check.sql` if either ever needs re-running after a bulk
import.

## Open for Grant

1. **Sublimits / aggregate model** — deferred by him, pending a conversation.
   The research is in the skill; the gap is real and affects what a schedule of
   insurance can say.
2. Whether `/items` should also edit RFI items in place (today it links to the
   account's Work tab, on the grounds that an item belongs to a request and
   editing one properly means seeing its request).
