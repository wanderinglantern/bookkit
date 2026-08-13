# Project needs → pipeline sync — design

Date: 2026-08-13
Status: approved in conversation (brainstormed with Grant); queued after
current phase closings

## Goal

A project need's line of cover becomes trackable in the pipeline the
moment it exists, because Grant identifies needs but a colleague markets
them (99% of the time): the Opportunity is the HANDOFF ARTIFACT. Both
records stay editable — director works the project view, colleague works
the pipeline — with a reconciler that syncs ordinary progress and
surfaces genuine contradictions instead of guessing.

## Decisions (Grant, 2026-08-13)

1. **Auto-create with the need.** Creating a ProjectNeed auto-creates a
   linked Opportunity (need.opportunity_id set at birth): line from the
   need, org from the project, target_effective from needed_by, title
   "{line} — {project name}". Rationale: delegation — the pipeline
   record must exist to be handed to the colleague. (Noted alternative,
   rejected for this workflow: create at marketing-start.)
2. **Both sides editable; a sync service reconciles.** Two people, two
   surfaces. (Noted: single-owner designs rejected because the person
   updating quotes/submissions is not the person updating the project.)
3. **Surface, don't guess.** Ordinary transitions propagate (see map);
   genuine contradictions — terminal states disagreeing — become an
   attention item ("needs reconciliation") resolved with one key by
   Grant. The reconciler never invents an answer.

## Sync semantics

- Mapping (ordinary, most-recent-edit propagates through
  `services/needs_pipeline.py` — never raw writes):
  - opportunity stage ∈ early stages → need `identified`
  - stage quoted → need `quoted`
  - stage won → need `placed` (and placement link-through when the
    placement exists)
  - need `not_needed` → opportunity lost-equivalent stage, reason noted
- Terminal-state contradictions (e.g. need `placed` vs opportunity
  lost): NO auto-write. An attention item appears under a new
  "reconcile" leaf; resolving picks the winner and event-logs both
  sides. Recency comes from event_log (rowid ordering), not wall-clock
  comparisons in code.
- Every sync write goes through the service, lands in event_log, and is
  undoable. No towerkit involvement (opportunities/needs are book-side).

## Delegation routing

- On auto-create, assign the opportunity via TeamAssignment line match:
  exactly one team member covers the need's line for that org (or
  book-wide) → auto-assign; zero or multiple → unassigned + an
  attention nudge ("needs an owner"). REVIEW POINT: assignment rules
  may need a per-line default owner setting.
- **Delegated-and-idle attention:** opportunities born from needs with
  no stage movement and no submission activity for N days surface to
  Grant (the director's chase list). Reuse the SLA/staleness service
  patterns; N defaults to 14 days. REVIEW POINT: N.

## Surfaces

- Need form: on create, silently creates+links the opportunity (notify
  names it). Linked needs show their opportunity stage read-alongside
  (not editable there — stage edits happen in pipeline surfaces; status
  edits on the need side still allowed per decision 2 and sync back).
- Pipeline screen: opportunities born from needs badge their project.
- Navigator attention: "reconcile" leaf (contradictions) and
  "delegated idle" leaf (chase list). Overdue/unmet semantics unchanged.
- MCP: open_items gains the two new attention feeds (read-only);
  no new write tools in v1.

## Backfill

Existing needs without opportunity_id: an additive, idempotent
`bookctl sync-needs` command (and a one-time prompt in the TUI) creates
missing opportunities for OPEN needs only (identified/quoted). Placed/
not_needed needs stay unlinked — history isn't retrofitted. DB backup
via the existing importer snapshot pattern before the bulk run.

## Testing

Service-level: mapping table both directions, contradiction detection,
recency-from-event_log, auto-assign single/zero/multi match, idle
detection with today as a parameter (never hardcoded dates). Pilot
tests: need create → opportunity exists+linked+assigned; reconcile flow
resolves and logs. Convention gates as always.

## Out of scope (v1)

Per-line default-owner settings UI, colleague-facing MCP write tools,
auto-creating submissions, retroactive linking of closed needs,
cross-org opportunity dedup.
