# RFI tracking (information requests) — design

Date: 2026-08-13
Status: approved in conversation; pending spec review

## Goal

A unified way to track the questions and document requests a client
receives — from underwriters during a placement, and from us during
onboarding. Track the ask, the response, and when it was received;
scope each request to the placement or project it concerns; give the
client a grouped tab in their open-items workbook showing exactly what
they still owe; and give us a queue of requests to chase.

The physical documents are NOT tracked — they live in a shared folder.
bookkit tracks the ask, the answer, and the dates.

## Framing: why a new entity

bookkit already has three open-item kinds — `Task`, `ProjectNeed`,
`Submission` — and the export renders them side by side with a `kind`
column. `Task` in particular already carries `org_id`, `placement_id`,
`category`, `due_on`, `description`, `detail`, `status`, `priority`.

RFI is nonetheless a distinct entity, for three reasons:

- **Direction.** A task is something *we* do; an RFI item is something
  the *client* owes. Merging them puts client homework in our to-do
  list and forces `AND request_id IS NULL` onto every task query.
- **Lifecycle.** Tasks are open → done. RFI items are outstanding →
  received (or waived), carrying a response payload and a received
  date that would pollute `Task`.
- **Shape.** Requests arrive in batches with their own provenance and
  deadline; tasks are individual.

Reusing `Task` as the item row (with a new parent table) was considered
and rejected on the coupling grounds above.

## Decisions taken (with the trade-offs accepted)

| Decision | Chosen | Trade-off accepted |
|---|---|---|
| Granularity | Two-level: request → items | Heavier than flat-items-with-a-group-label, which was the v1 recommendation. Justified by batches having their own provenance and deadline, and by chasing being a per-request act. Costs a second entity with its own forms and lifecycle. |
| Scope link | Request holds it; items inherit | A market email ranging across two placements becomes two requests. Buys a section header that states scope once, and a chase queue with one grouping rule. |
| Ownership | None — chase by age and due date | Recommended against: "who do I email" is the first question a chase raises. Accepted as a deliberate omission; the request links to the client and its contacts are one keystroke away, and a nullable owner FK is additive later. |
| Response | Answer text + received date | Makes answers searchable through existing FTS, so renewal-time recall is a query. Costs one field and one edit surface. |
| Requester | Optional market FK | Nullable FK to an existing table; empty for onboarding and internal asks. |
| Client view | Outstanding only, one section per request | Received items stay in bookkit for audit and recall but never reach the client sheet. |

## Data model

`migrations/010_rfi.sql`, modelled on `006_projects.sql`:

```sql
CREATE TABLE rfi_request (
    id             TEXT PRIMARY KEY,
    ref            TEXT NOT NULL UNIQUE,        -- RFI-0001
    org_id         TEXT NOT NULL REFERENCES org (id),
    placement_id   TEXT REFERENCES placement (id),
    project_id     TEXT REFERENCES project (id),
    market_org_id  TEXT REFERENCES org (id),    -- who asked; NULL = onboarding/internal
    title          TEXT NOT NULL,
    requested_on   TEXT NOT NULL,
    due_on         TEXT,
    notes          TEXT,
    cancelled_at   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    deleted_at     TEXT,
    CHECK (placement_id IS NULL OR project_id IS NULL)
);
CREATE INDEX idx_rfi_request_org ON rfi_request (org_id);
CREATE INDEX idx_rfi_request_due ON rfi_request (due_on);

CREATE TABLE rfi_item (
    id           TEXT PRIMARY KEY,
    request_id   TEXT NOT NULL REFERENCES rfi_request (id),
    kind         TEXT NOT NULL DEFAULT 'question',     -- question | document
    prompt       TEXT NOT NULL,
    detail       TEXT,
    category     TEXT,
    due_on       TEXT,
    response     TEXT,
    received_on  TEXT,
    status       TEXT NOT NULL DEFAULT 'outstanding',  -- outstanding | received | waived
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    deleted_at   TEXT
);
CREATE INDEX idx_rfi_item_request ON rfi_item (request_id);
CREATE INDEX idx_rfi_item_status ON rfi_item (status);
```

**Request status is derived, never stored.** A request is open iff it
has at least one `outstanding` item; `cancelled_at` covers withdrawal.
A stored status alongside item statuses is two sources of truth and
drifts the moment the last item is received. Accepted cost: a request
with zero items reads as open by convention rather than by data.

**`kind`** distinguishes a question from a document request in one
table — they share every field; a document request leaves `response`
empty and carries `received_on`. It drives the client sheet's Type
column.

**`category`** gives optional sub-grouping inside a long request,
vocabulary-completed via `Field.suggestions` exactly like
`vocab.task_categories`. This is the concrete mechanism for the
client-facing "logical grouping"; absent, a request renders flat.

**Vocabularies** are controlled-but-extensible tuples in `models.py`
(`RFI_ITEM_STATUSES`, `RFI_ITEM_KINDS`) per the `TEAM_ROLES` pattern,
rendered via `theme.status_text`. (The global instruction to back
status fields with `ListDefinition.WellKnown` / `ListValuePicker` is a
Swift-project rule and does not apply here; bookkit's own convention
governs.)

**Refs** come from the existing allocator: `RFI_REF = "RFI"` added to
`ids.py` beside `ORG_REF`/`PLACEMENT_REF`/`PROJECT_REF`, allocated via
`next_ref` in `repo/rfi.py` exactly as `projects.create` does. Items
carry no ref — they are only ever seen under their request.

**Effective due date** of an item is `item.due_on` falling back to
`request.due_on`. One rule, used by the queue, the tab, and the sheet.

**Data safety:** migration 010 is purely additive — two `CREATE TABLE`s,
no `ALTER` of existing tables, no backfill, no rewrite. Nothing existing
is at risk; rollback is dropping two empty tables. Writes go through
`base.insert/update`, so every edit lands in `event_log` and `u` undoes
it.

Considered and dropped: an explicit `sort_order` on items (creation
order is paste order, which is enough until reordering is a real need).

## Chase queue

A new leaf in the Navigator's ATTENTION section, "RFI outstanding (n)",
joining the existing six.

- **Rows are requests, not items** — you chase a request with one
  email. Each row shows earliest outstanding due, outstanding count,
  client, scope, and who asked:
  `Sompo — property questions · 5 of 12 open · due in 3d`.
- An individually urgent item surfaces by pulling its parent's earliest
  date forward.
- Same 120-day bucket-aligned window as every other bucket; overdue
  requests never fall off, matching the renewals and needs rule.
- `services/rfi.py` owns the query and the derived status; `repo/rfi.py`
  owns the SQL.

## TUI surfaces

**Navigator group leaf.** "requests (n)" under each account, joining
placements / contacts / opportunities / tasks / projects. `a` adds,
`e` edits, `enter` opens the account. Cancelling a request happens in
its edit form, NOT on a key: `d` already means "done (task)" app-wide,
and binding it to a withdrawal on one table and to marking an item
received on another would make the same key mean three things.

**AccountScreen tab 9, "Requests"** — master/detail, as a two-level
model requires:

- Top: the client's requests — ref · title · asked by · scope ·
  requested · due · *n* open.
- Bottom: an `InlineTable` of the selected request's items, `i`-editable
  on prompt, category, due and response — the same datasheet feel as the
  Open Items tab. `a` adds an item, `d` marks it received (stamping
  `received_on` with today — the one `d` binding in this feature, and it
  reads as "done" consistently with the rest of the app).

  **Amended 2026-08-13 (controller ruling, final review).** The list
  above originally included **status** and **received-on** as inline
  columns. They are not: `d` owns that transition, setting both fields
  together through `services.rfi.mark_received`, and the edit form can
  set them explicitly when a correction is needed. Two ways to make one
  state change is exactly the coupling this spec warns against
  elsewhere, and an inline status edit that left `received_on` NULL
  would close an item and strip its date. **Response** was added to the
  inline set in the same pass: an answer you cannot see on the datasheet
  defeats the point of the tab.

  Also amended: `u` is not advertised on this tab. `d` is two field
  writes and a paste is a batch of creates, so neither is a single
  undoable mutation — `u` still works for `i` cell edits (one field
  write each), but promising it over the tab as a whole was false.

**Paste-to-create items.** A paste action on the items table opens a
`TextArea`; each pasted line becomes an item, with leading `1.` / `1)` /
`-` / `•` / `*` stripped and blank lines skipped. This is a deliberate
departure from YAGNI: the feature's premise is a *litany* of questions,
and if the only entry path is one form per item the feature will not be
used. It reuses `FormModal` and does not touch the `imports/` pipeline.

Shared flows (add request, add item, mark received) live in
`widgets/entity_actions.py` so the group table and the tab call one
implementation.

## Export tab

The workbook becomes four sheets: **Open Items · Information Requests ·
Projects · Schedule of Insurance** — action sheets first, reference
sheets after. "Information Requests" rather than "RFI": plainer for a
client, and it avoids collision with construction's own RFI meaning.

- Outstanding only, one section per request. Section label carries the
  request: `Sompo — property questions · asked 5 Aug · due 19 Aug`.
- Columns **Item | Detail | Type | Needed by** — the same visual family
  as sheet 1 (`Item | Description | Detail | Type | Due | Status`),
  minus Status (it would read "Outstanding" on every row) and
  Description (RFI items have no brief/long split beyond prompt and
  detail). `Item` is the prompt; `Detail` is the long form through the
  existing `flatten_markdown`; `Needed by` is the effective due date.
- **Sub-grouping emits one section per (request, category) pair** —
  label `Sompo — property questions · Financials` — because
  `TableSection` has only one label level. Uncategorised items fall into
  an unlabelled trailing section under their request, mirroring
  "General" on sheet 1.
- Sheet header line: *"Items we need from you"*.
- Included only when the client has at least one outstanding item;
  omitted entirely rather than rendered blank, matching the Projects
  sheet rule. Cancelled requests and waived items never appear.
- Deterministic: requests by earliest outstanding due then ref; items by
  category then creation; `today` is a parameter, never the wall clock.

**Known overlap, accepted.** A client can now receive two lists of asks
— an open task "chase loss runs" and an RFI item "loss runs 2021–2025"
would both appear. Nothing enforces the boundary and nothing should; the
sheet header makes the distinction legible (sheet 1 is what is open on
their account, sheet 2 is what they personally owe).

**Structure.** Composition goes in a new `services/export_rfi.py`
(pure, no openpyxl); `export_open_items.py` becomes the workbook
assembler calling one composer per sheet — a small refactor in the
direction the three-tab plan already points, keeping a file about to
hold four sheets' logic from doing too much.

## Testing

- **Migration:** applies to a seeded DB, bumps `schema_version`, and is
  idempotent.
- **Repo:** create/read/soft-delete requests and items; `event_log` rows
  written so `u` undoes.
- **Derived status:** all-received reads closed; waived counts as
  not-outstanding; zero-item request reads open; cancelled never
  appears.
- **Chase queue:** overdue never falls off the 120-day window; beyond
  the window is excluded; ordering by earliest outstanding due; count is
  of outstanding items only.
- **Export composition (pure):** section per request, sub-sections per
  category, unlabelled trailing section for uncategorised, outstanding
  only, markdown flattened, deterministic order, sheet omitted when
  nothing is outstanding.
- **Paste splitter (unit):** numbering and bullet stripping, blank
  lines, CRLF, single line, empty paste.
- **TUI pilot:** tab opens with focus in the requests table; selecting a
  request fills the items table; inline edit persists; `d` stamps
  `received_on`; paste creates N items; `u` undoes.
- **End-to-end:** write the workbook, re-read with openpyxl, two runs
  byte-identical.
- Convention tests (no raw SQL outside `repo/`) and the standard gates:
  `pytest`, `mypy src`, `ruff check src tests`.

## Build order

1. **Model + repo** — migration 010, models and status tuples,
   `repo/rfi.py`, category vocabulary. No UI.
2. **Services** — `services/rfi.py`: outstanding-request query, derived
   status, the 120-day chase feed. Includes one line in `bookctl today`
   for requests to chase (cheap, consistent with the existing brief,
   easy to cut).
3. **TUI** — Navigator attention leaf and account group leaf,
   AccountScreen tab 9, shared flows, paste-to-create.
4. **Export sheet** — BLOCKED on towerkit's multi-sheet composition API
   landing from `feat/soi-schematic`. Phases 1–3 have no such dependency
   and should land first regardless.

## Out of scope (v1)

- Responsible-party / owner on requests or items (decided against;
  additive later as a nullable FK).
- MCP exposure, consistent with the open-items decision.
- Storing or attaching the documents themselves — the shared folder
  owns those.
- Automated chase emails or reminders.

**Flagged, not designed — the obvious v2: request templates.**
Onboarding asks the same twenty documents of every client; a saved
template that stamps out a request would turn this from a tracker into
a workflow. It needs the tracker to exist and be used first, so it must
not ride v1.
