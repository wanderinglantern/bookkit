# Client projects with insurance needs

**Date:** 2026-08-12
**Status:** Draft for Grant's review

## Problem

Clients run construction projects with insurance needs of their own —
builder's risk, wrap-ups, project-specific GL — each with effective/expiry
dates and, critically, **insurance-needed-by dates** that must never sneak up.
Today nothing in bookkit models this; needs live in heads and notes.

## Decisions (Grant, 2026-08-12)

- A project belongs to an **account**, with **optional links** outward: a
  need can point at the opportunity or placement it became. No auto-creation
  of pipeline items — links form when real.
- Needs are **per-need rows**: each carries its own line of cover, needed-by
  date, optional limit, status, notes.

## Data model (one migration, next number in migrations/)

`project` — soft-deleted, event-logged (add to `ENTITY_TABLES`):
- id, ref (`PRJ-nnnn` via ids.next_ref), org_id → org
- name (required), description, site (free text location)
- status: `planned | active | completed | cancelled` (controlled-but-
  extensible tuple `PROJECT_STATUSES`, same pattern as TEAM_ROLES)
- start_on, end_on (the project's own effective/expiry dates, ISO text)
- notes, created_at/updated_at/deleted_at

`project_need` — soft-deleted, event-logged:
- id, project_id → project
- line (required, e.g. "Builder's Risk"), needed_by (required, ISO)
- limit_cents (int, nullable), premium_indication_cents (nullable)
- status: `identified | quoted | placed | not_needed` (`NEED_STATUSES`)
- opportunity_id (nullable → opportunity), placement_id (nullable → placement)
  — the optional links; both usually NULL at birth
- notes, timestamps, deleted_at

## Repo / services

- `repo/projects.py`: create/get/update/delete for both tables;
  `for_org(conn, org_id)`, `needs_for_project(conn, project_id)`,
  `needs_due(conn, today, days)` → needs with needed_by inside the window
  (status identified/quoted only — placed and not_needed are done), joined
  with project + org for display.
- No new money rules: cents everywhere, parse via money.py; dates via
  dates.parse_human_date.

## TUI

- **Account screen → new "Projects" tab**: projects table (name, status,
  start → end, open-needs count) over a needs table for the selected project
  (line, needed by, `d` days, status, linked ref). Same expiry styling as
  placements: yellow ≤ 60d, red past.
- `a` on the tab adds a project (or a need when the needs table has focus);
  `e` edits; forms via entity_forms + commit-in-place (the default).
- **Need → opportunity** (`o` on a need row): creates an opportunity
  pre-filled from the need — title "{project} — {line}", lines = line,
  target_effective = needed_by — and stores opportunity_id back on the need.
  This is the optional link forming when the need becomes real pipeline.
- **Today**: the renewals pane gains project-need rows inside the same
  90-day window, labelled by line + "needed by", enter opens the account.
  A need due is exactly the same class of attention as an expiry.
- `bookctl today` prints the same needs section.

## Import (later, not this spec)

A `projects` flow in imports/ (fieldspec + mapper) once real project
spreadsheets exist — the pipeline is ready for it; don't build on guesses.

## Testing

- Repo round-trips + needs_due window/status filtering (table-driven).
- Pilot: add project + need on the account tab; need shows in Today within
  window; `o` creates the linked opportunity with target_effective set.
- Convention tests keep applying (repo owns SQL, tui has none).

## Out of scope

- Auto-creating opportunities for every need (rejected: noise).
- Project-level team assignments (account/placement scopes exist; add only
  if a real case demands it).
- towerkit involvement — projects are book-side; a project placement that
  gets a tower goes through the existing placement/scaffold flow.
