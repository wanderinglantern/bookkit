# Projects then Navigator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Executed in-session by the author; interfaces pinned here, full detail in the two specs (2026-08-12-projects-design.md, 2026-08-12-navigator-design.md).

**Goal:** Ship client projects with insurance needs, then the attention-first tree navigator with working DataTables, per the two approved specs.

**Global constraints:** branches `projects` then `navigator`; commit-in-place forms everywhere; repo owns SQL, tui has none; money cents, dates via parse_human_date; mypy strict outside tui; ruff clean; full suite green before each merge; backup story — migration 006 is additive only (two new tables, no rewrites).

## Phase 1 — projects (branch `projects`)

1. **Schema + models**: `migrations/006_projects.sql` (project + project_need per spec, 005-style comments/indexes); `models.Project`/`ProjectNeed` + `PROJECT_STATUSES = ("planned","active","completed","cancelled")`, `NEED_STATUSES = ("identified","quoted","placed","not_needed")`; `ids.PROJECT_REF = "PRJ"`; `base.ENTITY_TABLES` gains both. Tests: migration applies, round-trip via repo (next task's API) — commit together with task 2.
2. **repo/projects.py**: `create_project(conn, org_id, name, **fields)` (allocates PRJ ref), `get_project`, `projects_for_org`, `update_project`, `delete_project`; `add_need(conn, project_id, line, needed_by, **fields)`, `get_need`, `needs_for_project`, `update_need`, `delete_need`; `needs_due(conn, today, days=90) -> list[sqlite3.Row]` — needs with status identified/quoted and `needed_by <= horizon` (INCLUDING already-past: unmet needs never fall off), joined project+org, ordered by needed_by. Table-driven tests.
3. **Forms + account tab**: `entity_forms.project_form/apply_project` (name req, site, status select, start/end dates, description) and `need_form/apply_need` (line req, needed_by req date, limit + premium indication money, status select, notes). Account screen gains TabPane "Projects" (projects-table over needs-table for the highlighted project; needed-by styled yellow ≤60d / red past); `a`/`e` dispatch by focused table; `o` on a need row creates the pre-filled opportunity (title "{project} — {line}", lines=line, target_effective=needed_by) and stores opportunity_id on the need. Pilot tests.
4. **Attention wiring**: Today renewals pane appends needs-due rows (key `need:<id>:<org_id>` so enter opens the account; program cell "{line} — {project} (need)"); `bookctl today` prints a PROJECT NEEDS section. Tests; merge to main.

## Phase 2 — navigator (branch `navigator`)

5. **entity_actions.py extraction**: move the placement dual-owner edit, renew confirm, and layer-edit flows out of account.py into `tui/widgets/entity_actions.py` as functions taking `(screen, conn, ...)`; account.py delegates (zero behavior change; suite must stay green before navigator work starts). Contact/opportunity/task add-edit need no extraction — entity_forms already serves both callers.
6. **NavigatorScreen**: `tui/screens/navigator.py` per spec — Tree (ATTENTION groups with counts / ACCOUNTS with badges + lazy group children / MARKETS), right pane ContentSwitcher: summary card (Static) or scoped ListTable per group kind, with in-table keys via commit-in-place forms and `enter` jumping to the full screen; placements table renders TowerPreview below when a linked row is highlighted; `t` opens TodayScreen.
7. **Wiring + polish**: app.on_mount pushes NavigatorScreen; help screen updated; refresh on resume and after every in-pane commit.
8. **Pilots + merge**: navigator is home; attention table shows an overdue renewal; account → Placements group table has expiry columns; `e` there stays open on refused save; `enter` opens AccountScreen; `t` reaches Today. Full suite; merge; push both repos.
