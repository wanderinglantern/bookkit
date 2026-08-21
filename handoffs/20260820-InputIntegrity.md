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

## Overnight agent fan-out — IN FLIGHT AT TIME OF WRITING

Five agents, disjoint file ownership, none of them running git. Each was told
to mutation-check every test it writes.

| Agent | Area | Owns |
|---|---|---|
| A | parsers, bounds, refusal wording | `money.py`, `forms/spec.py` |
| B | web error UX, label visibility | `inline-cell.js`, `app.css` error styles, `macros/cell.html`, `macros/form.html` |
| C | vocabularies on task surfaces | `repo/vocab.py`, `forms/inline.py`, `routes/work.py` wiring |
| D | cross-field consistency | new `services/consistency.py`, `forms/entities.py`, `mcpserver.py` |
| E | data collection / imports | `imports/**`, `import_screen.py`, `paste_import.py` |

**When they report: gate, then commit each area separately.** They were told not
to touch each other's files; verify that held (`git status` before each commit).

## Gotchas carried forward

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

## Open for Grant

1. **Sublimits / aggregate model** — deferred by him, pending a conversation.
   The research is in the skill; the gap is real and affects what a schedule of
   insurance can say.
2. **Import commission ambiguity** (agent E) — a percent-formatted cell reads as
   15 bps instead of 1500. Instruction given: REFUSE and name both readings,
   never silently reinterpret, because a silent 100× correction is the same
   class of bug as the silent 100× error. Confirm that is what he wants.
3. Whether `/items` should also edit RFI items in place (today it links to the
   account's Work tab, on the grounds that an item belongs to a request and
   editing one properly means seeing its request).
