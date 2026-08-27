# 2026-08-27 — One ask, three markets (RFI ↔ subjectivity join) + the grid order hold

**Status: everything below is MERGED AND PUSHED to `main`.** Nothing is half-built.
`main` is at `4fd90b0`, `origin/main` matches, no worktrees left behind. This
document exists so the *next* piece of work starts from what was decided rather
than re-deriving it.

Design artifact (kept current):
<https://claude.ai/code/artifact/bd135a66-bd12-4399-90dc-44490a4e5a53>

---

## The goal

Grant's job runs on three things: **marketing, open items, and the balance of
RFI information.** They lived in three places with nothing joining them, so
*"what is blocking this placement"* had no single answer. Chasing these IS the
three weeks between a quote arriving and a policy being bound.

Two pieces shipped, in this order:

1. `9e8823e` — the marketing grid stops re-sorting under the hand working it.
2. `4fd90b0` — a market's subjectivity and the client ask that answers it become
   one chain.

---

## Piece 1 — the grid order hold (`9e8823e`)

**The report.** Grant: *"as I am updating status, the grid moves with the item
which makes it difficult to update multiple records or be quick about updates
with the screen jumping around."*

**The cause — two correct decisions colliding, neither one a bug on its own:**

- `services/marketing_report.py::_default_key` (~line 134) orders rows
  **status first**. Right for READING a grid somebody else filled in; it is what
  the client workbook prints.
- `status` is in `web/routes/marketing.py::_BLOCK_CELLS`, so a status write
  re-composes the **whole block** — it has to, the premium bridge and the
  clearance strip hang off which row leads.
- `web/static/inline-cell.js::refocus` put the caret back with a bare
  `cell.focus()`, which scrolls — and the cell had just moved.

**Measured on the running app**, nine markets on one line: ONE status write moved
**six of nine rows**, the edited row travelling from position 8 to position 3.

**Why it is not cosmetic:** working down the column, each entry pulls the finished
row UP and pushes the untouched rows DOWN, so the row under the cursor for the
next click is *a different market*. That is a wrong-record write on a field that
is a market's status — data-entry rules, not polish.

### What was built

- `services/marketing_report.py::order_rows` (line 151) gained a `pinned`
  parameter. Pinned rows come back in the pin's order; anything the pin never saw
  falls to the end — the same "an unknown figure is last" rule one level up.
  **The pin OUTRANKS the column**, because it is a snapshot *of* the column.
- `services/marketing_report.py::out_of_order` (line 220) — whether the hold is
  currently hiding a move. Drives the marker, and clears itself.
- `web/marketing_grid.py::parse_holds` / `format_holds` (lines 1264 / 1291) —
  wire format `<line_id>:<id>.<id>`, comma-joined between lines.
- `web/routes/marketing.py::_held_by` (line 105) — read off the same
  router-level dependency (`_remember_sort`) the sort spec uses.
- The section publishes it as part of the **same inherited `hx-vals`** the sort
  already rode (`templates/account/_marketing_panel.html`).

### The model, in one line

**The grid re-sorts when you LOAD it, not while you USE it.** A page load is the
read; everything after it is work. The server cannot tell reading from working
and does not need to.

### Things that will look wrong and are not

- The section publishes `hold` on **every** render, including unsorted ones.
  Deliberate — `test_the_section_publishes_the_order_it_is_actually_in` in
  `tests/test_web_marketing.py` asserts both halves and says why.
- Releasing needed **no new route**: it is a sort click without the sort, using
  the same `hx-vals` override the header buttons already use
  (`block_view`'s `release_url` / `release_vals`).
- The marker says `order held while you work`, **not a count**. "6 rows out of
  order" is a number that churns on every keystroke, which is the eye-catching
  movement the feature exists to stop.

---

## Piece 2 — the RFI ↔ subjectivity join (`4fd90b0`)

### Grant's decisions, and why (do not re-litigate these)

| Decision | Why |
|---|---|
| A subjectivity **spawns** an RFI, they do not merge | Each keeps its own vocabulary: a document is *received*, a condition is *met*. The single-table `asked_by` version forces one vocabulary onto two different facts and needs a destructive migration for a join a nullable FK gives. |
| **Many** subjectivities → **one** RFI item | The duplication worth removing is the one the CLIENT experiences. Three markets wanting loss runs is one email. |
| An RFI **never** runs to the market | So no *of whom* column anywhere; every ask is of the client. |
| The **placement** is the spine | `/items` stays the cross-book queue. Today deferred until Grant has used the placement view for a week. |
| Both due dates kept, **earlier one shown** | The market's deadline and the date we asked the client to hit are different facts. Follows `rfi.effective_due`. |
| **Same placement** is a hard filter | An ask satisfied on last year's renewal is a document from another year. Not offered at all. |
| The RFI side gets the **reverse control** too | Grant: *"Yes. That makes sense."* Both doors ship together so neither becomes the one people learn to avoid. |

### The load-bearing rule

**RECEIVED IS NOT MET.** The client sending loss runs does not satisfy AIG's
condition; AIG having them and accepting them does. An arriving answer
**surfaces** the markets it would clear and offers to settle them as one
confirmed batch — it never decides. Until the broker says so the condition reads
**answer in hand**: no longer waiting on the client, still outstanding to the
market. *That state was invisible before this change and it is the thing the
whole feature was built to show.*

### Where everything is

**Schema** — `migrations/021_subjectivity_rfi_link.sql`. One additive nullable
column `submission_subjectivity.rfi_item_id` + an index. Model field on
`models.py::Subjectivity`.

**repo** (`repo/submissions.py`):
- `subjectivities_waiting_on(conn, rfi_item_id)` — every live condition on one
  ask. Returns *all* statuses on purpose; callers filter.
- `unlink_rfi_item(conn, rfi_item_id)` — returns the ids it changed.

**services** (`services/rfi.py`):

| Function | Line | What it owns |
|---|---|---|
| `candidates` | 322 | Ranked asks that might already answer a condition. RapidFuzz `token_set_ratio`, `_WORTH_OFFERING = 55.0` floor. Same-placement hard filter. |
| `promote` | 419 | Attach to an existing ask (`item_id`) **or** write a new one (`prompt`). Exactly one. |
| `_already_asked` | 556 | The duplicate guard. `_SAME_QUESTION = 88.0`. |
| `_request_for_placement` | 573 | One envelope per renewal; reuses only an OPEN request. |
| `unlink` | 603 | The pair `promote` needs. |
| `unblocked_by` | 640 | Outstanding conditions on one ask. |
| `mark_met` | 659 | One undo unit over every market. |
| `unasked_on` | 693 | Reverse direction: conditions nobody has asked for. |
| `attach` | 728 | Reverse direction write; each id goes through `promote` so rules hold identically. |

**services/blocking.py** — `for_placement` (line 119) composes the list both the
browser and MCP read. `Blocker.days_remaining` is `int | None`; **None means
undated, never 0** (a 0 renders as the most urgent row on the page).

**web:**
- `routes/marketing.py` — `_SUBJ_ASK` (1986), `_ask_form_html` (1989),
  `subjectivity_ask_form` GET (2035), the `/row` route for [keep] (2047),
  `subjectivity_ask` POST (2065), `_owned_subjectivity` (2112).
- `routes/work.py` — `_COVERS` (614), `item_covers` (661),
  `item_received` POST (848), `item_received_confirm` GET (892).
- Templates: `_blocking.html`, `_subjectivity_ask.html`,
  `_item_received_confirm.html`, `_item_covers.html`.

**MCP** (`mcpserver.py`) — five tools: `_blocking_list` (2730),
`_subjectivity_add` (2772), `_subjectivity_ask_client` (2814),
`_subjectivity_unlink` (2853), `_request_item_add` (2867). Helpers
`_due_or_refuse` (2701), `_resolve_subjectivity` (2714).
`_request_item_received` gained `met: bool` and returns `unblocks` / `marked_met`.

**Tests** — `tests/test_blocking.py` (17), `tests/test_web_blocking.py` (14),
plus additions to `test_mcp_marketing.py` and `test_mcpserver.py`.

---

## Two defects the BUILD found that the design did not

Both were invisible on paper and obvious the moment the thing was running. Recorded
because they are the shape of what to look for next time.

1. **The picker made the duplicate it exists to prevent.** It offers the asks
   already out and then a free-text box under them — typing into the box what the
   list above was showing wrote a *second email for one document*, silently, with
   both rows then printing in the Blocking list under the same wording. Found by
   doing it in a browser. Fixed by `_already_asked`: `promote` refuses and **names
   the existing ask**, so attaching is one click. A fuzzy score is not good enough
   to attach on; it *is* good enough to stop and point.
2. **The Blocking block went stale on the first ask.** It was rendered in
   `templates/account/marketing.html` ABOVE the marketing section, and asking the
   client answers with the *section* — so the row just asked for went on reading
   "not asked yet" until reload. Now composed in `marketing_grid.panel` and
   rendered INSIDE `_marketing_panel.html`. **Do not "fix" this kind of thing with
   `hx-swap-oob`** — that is the destroyed-panel bug in CLAUDE.md.
   `test_asking_refreshes_the_blocking_block_in_the_same_answer` pins it and was
   mutation-verified by moving the block back out.

---

## Gotchas that cost time

- **`subjectivity_rows_for_placement` returns a JOIN, not a table row.** It carries
  `market_org_id` and `line_ids`, which `Subjectivity.from_row` rejects
  (`extra_forbidden`). Narrow it: `{k: row[k] for k in row.keys() if k in
  Subjectivity.model_fields}` — the same shape `RfiChase` uses over
  `outstanding_rows`.
- **`inline-cell.js` commits on `focusout`, not `change`.** Synthetic
  `blur()`/`change` events from the browser-automation tool do NOT commit a
  `<select>`. Drive it with a real click on the cell and a real click elsewhere,
  or you will "verify" a write that never happened. This wasted several rounds.
- **Jinja escapes `hx-vals`.** Tests reading it must `html.unescape` first, or you
  are testing the escaping rather than the contract.
- **The G5 refusal gate walks `web/routes/marketing.py` by AST** and treats any
  returned string literal as a refusal. Picker *labels* are not refusals —
  `candidate_says` was moved to `web/marketing_grid.py` (line 1687) rather than
  adding an exception to the gate. Keep presentation strings out of that module.
- **`promote` stamps THREE tables** (`submission_subjectivity`, `rfi_item`,
  `rfi_request`) because asking for something no ask covers opens the envelope
  too. `_TOUCHES` in `test_mcpserver.py` records this; the gate caught it.
- **A multi-value form post** in TestClient is `data={"covers": [a, b]}`, not a
  list of tuples (that sends raw content and silently posts nothing).
- Gates in a fresh worktree: `uv sync --group dev` then
  `uv run --no-sync python -m pytest`. Full suite ≈ 5m45s, 2,725 tests.

---

## What I would NOT do

- **Do not merge the two tables.** Considered and rejected — see the decision
  table. The artifact has the full argument.
- **Do not let the fuzzy score attach on its own.** A wrong attach does not fail
  loudly: it says a market's condition is answered by a document that does not
  answer it, and that surfaces at the bind.
- **Do not auto-promote every subjectivity.** It would ask the client for a signed
  application only Chubb wants, for a warranty nobody sends the client, and for
  the same loss runs three times — each with Grant's name on it.
- **Do not fix the grid jump by dropping the status sort.** Live-options-first is
  why the page reads when opened cold, and the workbook is composed from that
  order. Hold it while it is worked; do not abandon it.

---

## Standing feedback from this session

**STOP PUTTING EXPLAINERS IN USER-FACING OUTPUT** (Grant, 2026-08-27, about the
marketing `.xlsx`): *"You don't need to say 'What each market requires before its
quote can be bound'. Stop doing that."*

- Name the thing and stop. A heading is "Subjectivities", "Blocking", "Marketing".
- A **refusal** still gets a sentence, and so does an **empty state** that would
  otherwise read as a rendering fault.
- **Code comments are the opposite rule** and stay long — CLAUDE.md demands the
  WHY beside the code. Do not sweep those.
- Swept once already: `SUBJECTIVITY_SECTION_LABEL`, the marketing caption, the
  Blocking caption and several legends. Several of them were written the same
  afternoon, which is the tell that this is a default to watch.

---

## What's next — logged, NOT started

Four items, in the order I would take them. None is begun; each has a memory file
with the reasoning under
`~/.claude/projects/-Users-grantgreeson-Developer-bookkit/memory/`.

1. **Composite rate from premium ÷ exposure** (`composite-rate-from-premium-and-exposure.md`)
   — Grant: *"feat: in marketing tab - calculating a composite rate if given
   expiring premium and exposure basis"*. The block header carries
   `expiring_premium`, `expiring_exposure`, `expiring_basis` AND
   `expiring_rate_micros` — four typed fields where the fourth is derivable. The
   open question is what a TYPED rate means once the other two are set; my
   inclination is the `stated-market-premium` shape (typed wins, and the surface
   says which it is showing). Arithmetic belongs beside
   `money.parse_rate_micros`. Watch divide-by-zero and missing basis → UNKNOWN,
   not zero. **Smallest and most immediately useful of the four.**

2. **Umbrella / excess over multiple lines** (`umbrella-over-multiple-lines.md`) —
   Grant, cleaning up an MCP-created program: *"it is not clear how to do this at
   all in the interface"*, and assigning one raises
   `general-liability: OVERLAP Commercial General Liability -> Umbrella Liability
   at $1,000,000 vs $0`. Two problems that are probably one: no discoverable
   control, and the OVERLAP compares against **$0**. Likely root cause is that an
   umbrella sits at a *different height over each line* and a single positional
   attachment cannot say that. **Reproduce first and read what towerkit's
   `line-overlap` is actually comparing before touching either the message or the
   control. Do not relax the diagnostic.**

3. **Program files vs one database** (`program-files-vs-one-database.md`) — a
   production error: a moved `.mcp-snapshots/MCP-0079.json` makes *every*
   placement read-only. Grant reads this as the argument for merging program data
   into the one SQLite DB. Large, and it contradicts the standing rule that
   towerkit files are the sole authority. Also worth checking whether something is
   cleaning `.mcp-snapshots/`, which would make it recurring rather than a one-off.

4. **Blocking on Today** — deferred by Grant until he has used the placement
   Blocking block for a week. Revisit then, not before.

### Also known, not urgent

`scroll-keep.js`'s comment claims it covers "every scroller on the page" and it
queries only `.table-scroll`. Today that is true by accident: I measured every
working surface in a browser and **only the window and `.table-scroll` actually
scroll** — the eight other `overflow: auto` declarations (`.program-rail`,
`.worksheet`, `.structure-index`, `.book-body`, `.markets-body`, `.right-rail`,
`.tab-panel`, `.topbar-nav`) all grow with their content and measured zero scroll
room. `.program-workbench`'s own comment says "three panes, siblings, each its own
scroll", so that intent is not realised. **If a height cap is ever added there,
scroll-keep will silently not cover it.**
