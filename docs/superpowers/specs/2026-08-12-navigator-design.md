# Navigator — tree home with working tables

**Date:** 2026-08-12
**Status:** Draft for Grant's review

## Problem

The Today dashboard is four fixed panes; reaching a record means a screen
hop per pivot. Grant wants towerkit's shape as home: an outline to drill
into records, fast pivoting between accounts — and (2026-08-12) DataTable
leveraged wherever possible so records EDIT in place, not just display.

## Decisions (Grant, 2026-08-12)

- Attention-first tree becomes the launch screen; Today survives on `t`.
- Tree depth: entity lists as leaves — records live in tables, not tree rows.
- Right pane is a working `ListTable` (DataTable) whenever a group is
  selected: the standard keys act directly there, via commit-in-place forms.

## Layout

`NavigatorScreen` (new home): `Tree` left (~45 cols) · right pane · footer.

Tree branches:
- **⚠ ATTENTION** (auto-expanded): group nodes with counts — *Overdue
  renewals*, *Renewals ≤ 90d*, *Tasks due*, *Submissions past SLA*
  (+ *Needs due* when projects land). Selecting a group shows its TABLE on
  the right; the groups have no tree children — the table IS the drill.
- **ACCOUNTS (n)**: one node per client, badge glyphs (red overdue, dim
  stale). Expand → group nodes *Placements (n) / Contacts (n) /
  Opportunities (n) / Tasks (n)* (+ *Projects*). Group selected → its table
  on the right, scoped to that account. Account node selected → summary
  card (header line, next renewal incl. overdue, team, recent touches).
- **MARKETS (n)**: leaves; selected → market summary card.

Right pane modes:
- **Table mode** (group node selected): a `ListTable` with the same columns
  as the equivalent existing screen (placements get the expiry columns).
  `tab`/`right` moves focus into the table; `left` back to the tree.
  In-table keys, all through commit-in-place `FormModal`s:
  - contacts/opportunities/tasks: `a` add · `e` edit · `d` done (tasks)
  - placements: `e` dual-owner edit · `r` renew · `l` layer edit (linked)
  - attention tables: `e`/`d` as their kind allows; `enter` jumps to the
    owning account screen for anything deeper
- **Card mode** (account/market/single-record node): read summary;
  placement rows additionally render the ASCII `TowerPreview` beneath the
  table when highlighted (reuse the widget).
- `enter` on a table row = open the full screen (account tab / market
  detail) for deep work — the tabbed AccountScreen remains for that.

## Implementation shape

- Shared entity actions: extract the add/edit closures the AccountScreen
  tabs use (contact/opportunity/task/placement edit) from account.py into
  `tui/widgets/entity_actions.py` helpers taking (screen, conn, org_id,
  record_id) so NavigatorScreen and AccountScreen call the SAME code —
  no forked form wiring. Account.py behavior unchanged.
- Tree nodes carry `(kind, id)` data; children lazy-load on expand
  (`Tree.NodeExpanded`); counts computed in one pass per refresh
  (renewals.upcoming, tasks due, sla.past_sla, orgs list).
- `BookkitApp.on_mount` pushes NavigatorScreen; `t` → TodayScreen;
  all global keys (/, n, ctrl+t, ?, u) work unchanged. Help updated.
- Refresh on resume and after every in-pane commit (the `done` callbacks).

## Testing

Pilots: navigator is home; overdue renewal appears under Attention and its
table renders; expanding an account and selecting Placements shows the
scoped table with expiry columns; `e` there opens the commit-in-place form
and a refused save stays open; `enter` opens AccountScreen; `t` reaches
Today. Convention tests unchanged (no SQL in tui).

## Out of scope (v1)

- Inline CELL editing (spreadsheet-style) — the row-form via commit-in-place
  is the editing surface; a cell-overlay editor is a later candidate once
  the navigator proves out.
- Tree drill into layers/participants (towerkit's structure pane owns that).
- Removing TodayScreen or any existing screen.
