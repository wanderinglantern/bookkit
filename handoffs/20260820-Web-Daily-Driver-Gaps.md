# 2026-08-20 — Web daily-driver gaps 1–7: built, reviewed, fixed

## Goal

Close the full-TUI audit's seven daily-driver gaps so the Web UI can be
Grant's daily driver ("Build 1-7", "leverage workflows for this work").
All seven are DONE, merged to main, adversarially reviewed, findings fixed.
Final gate: **1752 passed, mypy green, ruff green**, working tree clean.
Main HEAD at handoff: `20e43fd`.

## What shipped

- **Gap 1 (by hand, on main):** `/today` (routes/today.py) — TUI severity
  order: overdue renewals (never fall off), renewals ≤120d, tasks due (tick
  done in place, batch tool `task_done`), project needs, past-SLA, quotes
  expiring, onboarding, chase, going stale, cross-account Recent changes
  with Revert. `/` redirects to `/today`. `/calendar` (routes/calendar.py)
  bucket-aligned, lines of cover shown, counts to earliest line end.
- **Gaps 2–7 (six-agent Workflow, isolated worktrees, then hand-merged):**
  - capture: routes/capture.py + services/capture.resolve_attendees; date
    refusal (bare number is not a date); + Log on account headers.
  - search: routes/search.py, topbar live form on every page.
  - pipeline: routes/pipeline.py — response quote/decline + bind offer,
    opportunity create/edit/stage-move/close, subjectivities.
  - account CRUD: routes/orgs.py + services/orgs.find_duplicate (cutoff 87).
  - markets: routes/markets.py — outline, appetite, underwriters, aliases,
    create, merge, nest.
  - team: routes/team.py + services/team.member_deactivate/_reactivate;
    account rail Assign/edit/remove (assignments corrected, NEVER re-scoped).
- Parity ledgers (web/parity.py) all flipped in the same commits; SCREENS
  now 14 entries, tests green both directions.

## The review workflow and its findings (all fixed same day)

Run wf_38f67653-bd4: 7 reviewers (per-gap + integration/old-base-drift) →
independent skeptic per non-minor finding. 3 confirmed / 0 refuted:

1. **merge_markets orphaned nested children** → child detail page 500
   (KeyError on dead parent FK). Fixed in services/merge.py: children fold
   into the survivor; merging a master into its own child unnests the
   child. routes/markets.py market_detail floats a pre-fix dead parent
   free (same as orgs.market_families). Tests: tests/test_web_markets.py
   (folds/unnests/died trio). Commit `34fc1df`.
2. **test_web_dead_controls.py POST sweep was blind**: `method = "GET"`
   rebound the loop var after the topbar search form, so every later form
   action was checked as GET. Fixed: per-URL `verb`, plus a sentinel
   assertion that ≥1 POST form action was verified. Same commit.
3. **SCREENS["pipeline"] ledger drift** (still claimed the writes absent).
   Rewritten; remaining honest absence = the global kanban. Same commit.

Two dropped-as-minor findings fixed anyway (`20e43fd`): team deactivate
cascade catches db.BlastRadiusExceeded (refusal in the panel, not a 500);
search.html sets `section` so Book isn't highlighted on /search.

## Open / needs Grant

- **TUI latent bug, flagged NOT fixed** (TUI-side, needs his call): tui
  QuickCapture (tui/widgets/quick_capture.py:264) does
  `parse_human_date(...) or date.today()` — silently substitutes today for
  an unparseable date, violating "ambiguous entry is refused, never
  guessed". The web capture form refuses. Also flagged earlier by the
  capture build agent: TUI ConfirmTask write is unbatched.
- **Global pipeline kanban** — the one remaining screen-level absence
  (SCREENS["pipeline"] says so honestly now).
- Screenshot feedback: artifact labels S5-1..S5-3, S6-1..S6-5 await
  Grant's paste-back comments.
- changelog.md not updated (per its prompt, done when asked).

## Gotchas for a fresh session

- Workflow worktrees snapshot the SESSION-START commit, not current main —
  the six gap branches predated phases 1–5; merges needed real conflict
  work (parity.py hand-merged repeatedly; watch for conflict markers).
- Gates: `uv run --no-sync python -m pytest -q`; check exit codes
  DIRECTLY, never through pipes (bitten repeatedly).
- Fresh worktree: `uv sync --group dev`, and `.claude/worktrees/towerkit`
  symlink makes the towerkit path dep resolve — don't delete.
- The towerkit checkout was mid-edit by a peer session (feat/mcp-hardening)
  — only depend on towerkit MAIN APIs; transient schema flakes possible.
- Disk was ~99% full during parallel suites; cleaned to ~6GB free. Watch
  scratchpad demo DBs and pytest basetemps.
- conftest freezes date.today() PER MODULE — tests compute expectations
  via the route module's own `date` attribute (see tests/test_web_today.py).

## Report

Artifact (kept updated at the same URL):
https://claude.ai/code/artifact/e0c5ca22-fe0f-4ae1-b24e-a3f4a8611cbe
