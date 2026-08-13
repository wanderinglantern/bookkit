# Client onboarding wizard — design

Date: 2026-08-12
Status: approved in conversation; pending spec review

## Goal

Step-by-step guided capture for NEW clients so every element — org basics,
contacts, program & lines, needs, follow-ups — gets thoroughly recorded.
Information not at hand can be skipped and returned to later; nothing
incomplete falls through the cracks.

TUI-first (decided over work-assistant-first): the wizard ships in
bookkit's TUI on a shared service-layer flow; a conversational MCP
version of the same flow is phase 2 (see the MCP server spec).

## Core principle: the data is the state

- **Flow is data, not screen code.** `services/onboarding.py` defines an
  ordered step list — org basics → contacts → program & lines → needs →
  follow-ups — each step declaring its required and optional fields.
- **Each step commits immediately** when saved: real rows via existing
  repo/services calls, event-logged as usual. There is no big-bang final
  commit and no wizard-state table.
- **`completeness(conn, org_id)`** computes per-step status (complete /
  partial / untouched) from the actual data. Resume = reopen the wizard
  on that org; it derives the first incomplete step from completeness.
  Works even if missing info arrived some other way meanwhile (TUI edit,
  MCP `enrich_field`).
- **Skip is first-class.** Any step or individual field can be skipped
  ("skip for now"); skipping never blocks progress and never loses what
  was already entered.
- The program & lines step routes through the towerkit sync pipeline
  (load → mutate → validate → canonical dump → re-project) like every
  other program write. Additive only; no destructive path, so no new
  backup machinery.

## TUI screen

- `OnboardingScreen`: step list left with completeness glyphs (theme.py
  status conventions — color plus glyph/word, never color alone), current
  step's form right.
- Each step is a FormModal-style commit-in-place form: refused/failed
  save keeps input intact, per the app-wide convention.
- Form wiring reuses `widgets/entity_actions.py` shared flows — no forks.
- Vocab fields wire `Field.suggestions` (repo/vocab.py) — autocomplete
  dropdown plus ghost text — anywhere values already exist.
- Unsaved keystrokes in the current step survive esc/crash via
  `repo/drafts.py` (per-screen scratch payload), cleared on step save.
- Entry points: new-client action from the Navigator; reopening an
  existing incompletely-onboarded org resumes it.
- Stage/status vocabularies stay controlled-but-extensible tuples in
  models.py, rendered via theme.status_text.

## Attention tie-in (what makes it durable)

An incompletely-onboarded client is an OPEN ITEM: unmet onboarding steps
surface in the Navigator attention tree and in the MCP `open_items` tool.
While the client is status 'prospect' they never fall off, matching overdue
renewals and unmet needs; once a client is created, incomplete onboarding
also nags for a 90-day window (decided by Grant 2026-08-13) so legacy
clients missing an owner don't flood attention forever — a client that
flips to active and is never finished onboarding drops out of attention
after day 90 unless it's still 'prospect'. The wizard makes thorough
capture easy; attention makes abandoning it visible; the work assistant can
nag too.

## Error handling

- A step save that fails validation keeps the form open with input
  intact (commit-in-place).
- Program-step sync conflicts surface as sync.WriteConflict with the
  standard resolution path; the wizard never bypasses the sha256 guard.
- Duplicate client guard at the first step: name checked against
  existing orgs via repo/aliases.py; likely duplicates are surfaced with
  the option to resume onboarding the existing org instead.

## Testing

- `services/onboarding.py`: unit tests for step definitions and
  completeness over seeded fixtures — untouched, partial, complete, and
  "filled out-of-band" (data added outside the wizard still counts).
- Pilot tests for the screen: step navigation, skip-and-resume, draft
  survival on esc, commit-in-place on refused save.
- Convention tests: no raw SQL in tui/; onboarding screen uses
  entity_actions shared flows.
- Gates: pytest, mypy, ruff.

## Out of scope (v1)

Conversational onboarding over MCP (phase 2 — same service flow, new
front-end), editing/re-running onboarding for long-established clients,
bulk onboarding from imports (the import pipeline already covers that),
reordering or user-defined steps.
