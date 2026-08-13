# Client open-items tab — design

Date: 2026-08-13
Status: approved in conversation; rides the wizard phase (bookkit lane,
tasks 8-10 appended to the wizard plan)

## Goal

A dedicated tab in the client view (AccountScreen) that focuses
exclusively on that client's open items — the aggregate today/attention
features brought forward to one client, in a datasheet-style view built
for fast editing: add tasks, flip dates, categories, descriptions in
place. Includes the client's open-items export from the same tab.

## Design

**Tab 8 — "Open items"** (binding `8`, joining the existing 1-7 tabs):

- **Primary surface: the tasks datasheet.** An `InlineTable` of ALL the
  client's open tasks — org-owned AND placement-owned, via
  `tasks.open_tasks_for_client` (the same ownership rule the export
  uses). Columns: due · task · category · description · detail · status;
  grouped by category (`widgets/tables.grouped_by_category`), the same
  order the export sections use. Inline editing (`i`) on due, title,
  category, description — the TASK_INLINE pattern; category completes
  from `vocab.task_categories`. `a` adds a task pre-attached to this
  client (with the detail/category fields available in the form), `e`
  opens the full form, `d` completes, `u` undoes — the exact verbs the
  rest of the app already teaches.
- **Secondary surface: other open items, read-only.** Below the
  datasheet, a compact ListTable of unmet project needs and outstanding
  submissions (kind · item · due/needed · status · days open) — the rest
  of what the export workbook shows, for context. Editing those lives in
  their own tabs (enter jumps nowhere in v1; hint says where to edit).
  Two tables rather than one mixed table: inline-edit semantics stay
  uniform per table, and the datasheet keeps single-purpose focus.
- **Workflow ease:** selecting the tab lands focus directly IN the
  datasheet (no tab-then-hop), cursor on the first row; the tab hint
  line carries the full verb set; refused saves keep input (commit-in-
  place everywhere); refresh after every mutation keeps grouping true.
- **Export from the tab:** `x` writes this client's open-items workbook
  — the SAME flow as the Navigator's `x`. The flow is extracted to
  `widgets/entity_actions.py` (shared, guarded: KeyError/OSError →
  notify) so both callers stay one implementation, per the
  shared-flows convention.

## Testing

Pilot tests: tab opens with focus in the datasheet; placement-owned task
appears; inline category edit persists; `a` adds a task attached to the
client; `d` completes; `x` writes the workbook (tmp cwd). Convention
gates as always.

## Out of scope (v1)

Editing needs/submissions from this tab, bulk operations, filtering
controls, MCP exposure (open_items already covers the data).
