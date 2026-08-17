# Web front end for bookkit (and towerkit) — design

Date: 2026-08-17
Status: approved in brainstorming; implementation plan not yet written

## Why

bookkit's interface today is a Textual TUI. Two things a terminal cannot do
well motivate a browser surface:

1. **Richer visuals** — real SVG towers, calendars, dashboards, side-by-side
   comparison. towerkit already renders deterministic SVG that a terminal can
   only approximate.
2. **Eventual replacement of the TUI.** The web surface is intended to reach
   1:1 parity and become the primary interface. Narrowing early slices is a
   build order, never the destination.

The app stays **single-user and local**. It is not hosted, not multi-tenant,
and not reachable off the machine. The SQLite database holds real client
contacts and premium figures at mode 0600.

## Decisions

### Stack: server-rendered Python, with JS islands where direct manipulation earns it

`bookctl web` starts uvicorn bound to `127.0.0.1` (loopback explicitly), opens
a browser, and serves FastAPI routes rendering Jinja templates. HTMX is
vendored as a single JS file in package data. No Node at runtime, no build
step, no bundler.

New runtime dependencies: `fastapi`, `uvicorn[standard]`, `python-multipart`.
New dev dependency: `httpx` (for `TestClient`). All have arm64 wheels. Adding
them triggers the wheelhouse drill documented on the Makefile's `wheelhouse`
target, with `WHEELHOUSE_SHA256` taken from the uploaded asset in the same
commit as the upload.

An "island" is a self-contained TypeScript component compiled to a static
asset, used only where direct manipulation is the point. The tower canvas is
the first and, for now, only planned island; it does not arrive until
sub-project 3, so no compile step exists before then.

**Rejected: a full React/TypeScript SPA.** Grant initially chose this and then
moved off it; the reasoning is recorded because the tradeoff will resurface.
Under an SPA every form field must be declared three times — in `FormSpec`
(for the TUI), in a Pydantic request model (for the API), and in a TypeScript
component (for the browser) — in two languages, with nothing forcing agreement.
The characteristic failure is silent: a field the API does not know about is
dropped on save with no error. The codebase already demonstrates the pattern at
smaller scale — `mcpserver.py:976` `_FIELD_CLEANERS` is a hand-copied duplicate
of the TUI's cleaner map held in sync by a comment. An SPA also requires
building assets on the Mac and shipping them in the wheel, because the work
machine has no npm access.

**Rejected: `textual serve`.** It streams the existing TUI into a browser in
about ten minutes of work, but delivers a terminal in a canvas — no SVG towers,
no HTML typography, no charts. It answers "access from elsewhere," which is not
a goal here, and cannot be a path to replacing the TUI because it *is* the TUI.
Worth remembering as a stopgap if remote access ever becomes a goal.

**Rejected: Next.js.** It requires a Node server running permanently beside the
Python one — two runtimes, two install paths, two failure modes, on a machine
where neither is easy to debug. Nothing in a single-user localhost CRM pays for
that.

### Drag-and-drop: what is actually well-defined

Direct manipulation of tower layers motivated the SPA question. Checking the
model changed the answer.

`Layer` has no ordinal. Vertical position in a tower is *derived* from
`Layer.attach`, a Money value (`towerkit/model.py:165` sorts by `attach`).
"Drag a layer up" therefore means "set its attachment point to wherever it was
dropped," which collides with towerkit's rule that follows-underlying
attachments are derived state and auto-heal — the user is never made to compute
an attachment. A free vertical drag can open a gap the `restack()` /
`heal_follows()` design exists to make unreachable.

The gestures that *are* well-defined against the existing model:

- **Drag a column left/right** — `edit.move_line(program, line_id, delta)`.
- **Drag a layer across columns** — `edit.set_applies_to(...)`.
- **Drag a layer's top edge to resize** — changes `limit`, then `restack()`
  re-derives every attachment above it server-side. This is the gesture that
  feels like a design tool, and it is safe *because* the server re-derives the
  stack afterwards.

All three live in one canvas component on one page. None require an app-wide
SPA.

## Decomposition

Each gets its own spec, plan, and build.

1. **Web shell + editable account page** (this design's subject).
2. Navigator, Today, dashboards — read-heavy, charts.
3. towerkit in the browser — SVG towers, then the tower canvas island and
   program editing.
4. TUI retirement, decided on evidence.

## Sub-project 1 scope

Three tabs of the account page: **Overview, Contacts, Interactions**.

`AccountScreen` is 2,262 LOC across nine tabs with roughly twenty row actions.
Three tabs exercise every hard mechanism exactly once: a form that creates, a
form that edits an existing record, a destructive action with confirmation, a
write that must batch and be revertible, and a refusal that must preserve
input. Placements, Projects, Pipeline, Documents, Open items and Requests are
the same shapes repeated; Placements additionally drags in towerkit program
writes, which belong to sub-project 3.

**Deliverable:** `bookctl web` serves an account page where the org profile can
be read and edited; contacts can be read, added, and edited; and interactions
can be read, logged, edited, and deleted — with every write forming one
revertible batch that appears in the same changes list the TUI and MCP server
write to.

## Architecture

### The seam

A new package `src/bookkit/forms/`:

- **`forms/spec.py`** — `Field`, `FormSpec`, `BatchSpec`, and field-cleaner
  routing, moved from `tui/widgets/forms.py`. These dataclasses already import
  nothing from Textual. This becomes the single home for the normalisation map;
  `mcpserver._FIELD_CLEANERS` is deleted and re-pointed here, removing an
  existing duplicate that is currently held in sync by a comment.
- **`forms/entities.py`** — the seventeen `*_form()` builders and thirteen
  `apply_*()` appliers moved from `tui/widgets/entity_forms.py`. The builders
  take no Textual; the appliers take a connection. Four builders
  (`member_form`, `document_form`, `appetite_form`,
  `org_form_initial_profile`) have no matching applier — their saves are
  applied inline at the call site, and slice 1 does not touch them.

`tui/widgets/forms.py` retains `FormModal` and nothing else. No re-export
shim — a second name for one thing is how drift starts.

**`src/bookkit/web/`** holds FastAPI routes, Jinja templates, and static
assets. A route handler fetches through `repo/`, renders a `FormSpec` to HTML,
and on POST calls the same `apply_*` the TUI calls, inside
`services.batches.open_batch(source="web", ...)`. It contains no field lists,
no validators, no normalisation, and no SQL. Adding a `Field` to a form builder
makes the input appear on both surfaces with the same label, cleaner, and
suggestion vocabulary, because there is one list.

`open_batch` gains `source="web"` alongside `'mcp'` and `'tui'`. Nothing else
in the undo machinery changes.

### Convention enforcement

Extending `tests/test_conventions.py`, which already asserts the rule for
`tui/` and `imports/`:

- `web/` contains zero raw SQL.
- `web/` never imports `bookkit.tui`, and `tui/` never imports `bookkit.web`.
  Shared code lives in `forms/` or it is not shared.

### Parity ledger

`tests/test_web_parity.py` enumerates every `(screen, tab, binding)` from the
running TUI app the same way `test_dead_keys.py` derives `_live_keys`, and
asserts each is either implemented as a web route or listed in an explicit
`PENDING` manifest with a one-line reason. Anything that is neither fails the
test.

This is the mechanism that makes 1:1 parity the destination rather than an
intention:

- The gap is a number, not a memory, and it shrinks every slice.
- A new TUI feature turns the parity test red until its web equivalent is built
  or consciously declared pending. Divergence becomes loud.
- Narrowing cannot quietly become permanent, because the manifest is a to-do
  list the suite recites on every run.

The ledger tracks that an action *exists*, not that it feels equivalent.
Interaction parity is explicitly not claimed: the browser gets its own idiom —
links and buttons — rather than keystrokes ported into HTML.

## Page design

### URLs

`/accounts/{ref}` redirects to `/accounts/{ref}/overview`. Each tab is its own
address: `/overview`, `/contacts`, `/interactions`. HTMX swaps the panel and
pushes the URL, so back, forward, and bookmarks work. The TUI's numeric tab
keys have no web equivalent and are not ported.

### Overview

Five cards — team, key contacts, recent interactions, open tasks, open
opportunities — replacing five stacked `ListTable`s. Same data, same service
calls.

### The account header

The header prints the renewal date from `renewals.next_for_org()` and shows the
**same date it counts to**, under a `renews` label, with overdue decided by
`days_remaining < 0` and never by grid position.

This rule is called out because it has now shipped broken on four surfaces.
Today, Book, the account header, and the calendar all printed
`placement.period_to` beside a countdown computed from `renewal_on`, so a date
twenty days in the future rendered red as "70d over." The web makes a fifth
surface, so it gets a named test rather than trust.

### The edit round-trip

For "edit contact":

- `GET /accounts/{ref}/contacts/{id}/edit` — `contact_form(existing)` returns a
  `FormSpec`; one generic Jinja macro renders it. The same macro renders every
  form, because they are all the same dataclass.
- `POST` the same URL — the route derives a `BatchSpec` exactly as `FormModal`
  does (`BatchSpec.for_title`), opens `open_batch(source="web", ...)`, and calls
  `apply_contact(conn, ...)` inside it. Success returns the refreshed panel.

One writer action, one undo unit, revertible by the existing service because it
went through the same function the TUI calls.

### Refusals

On `ValueError` the exception propagates out of the `open_batch` context, the
transaction rolls back, and the route re-renders the *form partial* with the
submitted values and the error message; HTMX swaps it in place. This is
commit-in-place with a different transport — the same contract as `_Refused`
in `tui/widgets/forms.py:91`, with the same guarantees: a refused save leaves
nothing behind and costs nothing retyped.

### Input rules the browser would otherwise break

- **Money is `<input type="text">`.** A `type="number"` input rejects
  `1,234.56` in the browser before the server sees it. Entry accepts cents
  because bookkit stores them; a form that refuses the value it pre-filled
  makes the record unsaveable.
- **Dates are `<input type="text">`.** A native date picker would silently
  replace `parse_human_date`, which is the function that *refuses* bare 1–2
  digit input — the guard that exists because "the 5th" once saved as
  2027-05-01 and fell off every attention window. Text entry preserves both the
  human forms and the refusal. A picker may later be added as an island that
  writes an ISO date into the same field.
- **Colour has one source.** `tui/theme.py` owns the palette and the
  status/days/money helpers; the web layer emits CSS custom properties derived
  from that module rather than a second palette in a stylesheet. The house rule
  carries forward: every coloured state also carries a word or glyph.

### Confirmations

Destructive actions are a server-rendered confirm step that POSTs — not a
JavaScript `confirm()` — so they stay testable and inside the batch.

## Testing

Gates unchanged: `uv run pytest -q`, `uv run mypy src`,
`uv run ruff check src tests`, with output redirected to the scratchpad and the
gate placed on the command, never after a pipe. Work happens in
`.claude/worktrees/web-account`, with `uv sync --group dev` and
`uv run --no-sync python -m pytest`.

Routes are exercised in-process with FastAPI's `TestClient` against a seeded
temp DB, in the fixture style `test_mcpserver.py` already uses. No browser is
needed for slice 1.

Tests that matter:

- **Seam tests, not outcome tests.** The assertion for a web edit is that a
  batch row exists with `source='web'`, contains the expected events, and that
  `services.batches.revert()` restores the record — not merely that the field
  changed. A green outcome assertion would pass even if the route wrote outside
  a batch, which is exactly the failure that let 33 `FormModal` call sites
  bypass the batched `push_form` seam.
- **Every `FormSpec` renders completely.** A generated test walks all seventeen
  form builders, renders each through the Jinja macro, and asserts every
  `Field.key` appears as a named input and every `Field.kind` in use has a
  renderer. Without it, an unhandled `kind` renders nothing and the form saves
  while dropping a field. This test is what makes "one definition, two
  surfaces" true rather than intended.
- **The refusal contract.** POST an invalid date (`"5"`); assert the response
  carries the error *and* the other submitted values, and that no row and no
  batch were written.
- **The renewal-date pairing.** Assert the header's printed date and its
  countdown derive from the same `renewal_on`, and that overdue is decided by
  `days_remaining < 0`.
- **Parity ledger** and the two convention tests above.

**Tests will be verified capable of failing.** The convention tests and the
refusal test's "nothing was written" are negative assertions — the class that
towerkit's `CLAUDE.md` records as passing for the wrong reason, where four
statutory assertions passed trivially. Each is confirmed by mutating the
production code and observing the failure before it is claimed to protect
anything.

**Rejected: full-page HTML snapshots.** The TUI's 38 `pytest-textual-snapshot`
baselines work because a terminal grid is stable and small. HTML snapshots
break on whitespace and class changes, and the failure mode is worse than
having no test: re-blessing becomes reflexive, and a real regression gets
blessed with the noise. Structural assertions instead.

### Not covered by automated tests

- **Visual layout.** There is no web equivalent of `test_layout.py`'s
  footer-fit assertion. Legibility, long account names in the header, and
  narrow windows need eyes. Playwright is available and should be introduced
  when the tower canvas lands in sub-project 3, where interaction cannot be
  tested any other way; it is not worth the setup for slice 1.
- **Whether the browser idiom actually beats the terminal.** That judgment is
  the main thing slice 1 exists to produce.
- **Two surfaces on one database.** TUI and web open simultaneously is WAL-safe
  for writes, but an open web page will not know the TUI changed something.
  Known rough edge, not designed around in slice 1.

Manual verification runs against `make demo` seeded data on the Mac, per the
standing rule that the real book lives on the production machine. Anything
data-dependent is handed over as `bookctl` commands rather than assumed from
this machine's database.

## Risks

- **Parity is a long road.** Nine tabs, roughly twenty row actions, plus eight
  other screens. The ledger keeps the remaining distance visible but does not
  shorten it. Two surfaces will coexist for a long time.
- **The forms extraction touches the TUI's most-exercised code.** It is a move
  plus an import rewrite gated by the existing suite, but `entity_forms.py` is
  745 LOC with fifteen callers' worth of behaviour behind it.
- **Committed built assets** are not yet a problem (no build step in slice 1)
  but become one when the tower island arrives. Decide then between committing
  `dist/` and building in CI.
- **A third write surface is a third chance to reintroduce a fixed bug.** The
  renewal-date rule is the documented example; there may be others not yet
  written down.

## Open questions

- Port selection for `bookctl web` — fixed default with a `--port` flag, or
  ephemeral. Trivial, decided at implementation.
- Whether the web surface should eventually replace `FormModal` rendering in
  the TUI as well, collapsing to one renderer. Not now; revisit after
  sub-project 2.
