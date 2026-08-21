# The RFI sheet grows a review column, and the due date becomes editable — Grant, 2026-08-21

Two asks from the queue, logged not built. The first is a SCHEMA CHANGE and
therefore owes the full MCP chain (CLAUDE.md, "a schema change is not done until
an agent can see it").

## 1. "Our review" on an RFI item

> "I want the export to show the item and our review (optional) which would
> include a review of materials we have on file to attempt to answer the
> question, then an open area for the client to enter answers. So I may need
> another field in the items table to be able to capture that marsh review
> element."

He is right that it needs a new field, and the shape of the sheet he is
describing is a three-part row:

| Item | Our review | Your answer |

- **Item** — the ask. Exists (`prompt`, plus `detail`).
- **Our review** — NEW. What we already found in our own file that may answer
  the question: "your 2025 SOV shows 14 locations; confirm this is still
  current". Optional, written by us, client-visible.
- **Your answer** — an OPEN, EMPTY area for them to type into and send back.

### The thing to get right first

`RfiItem` ALREADY has `response`, and `response` is client-visible today: it is
what the client told us, recorded by us, and it prints in the `Response` column
when anything on the sheet has been answered. So the new sheet has THREE
client-visible prose fields and only two of them are ours. Decide before
building:

- Is "your answer" the EXISTING `response` column, rendered blank when empty so
  the client can write in it? That is the DRY answer and I think it is right —
  a second field meaning "what the client said" is exactly the drifting copy the
  standing rule is about.
- If so, the only new column is `review`, and the sheet's shape is
  `Item | Detail | Our review | Needed by | Your answer`.
- The `Response` column is currently PRINTED ONLY when something on the sheet
  has been answered (deliberately — a blank band down a client deliverable reads
  as a form we forgot to fill in). That rule now INVERTS for this use: he wants
  the empty area as the point. So it becomes unconditional, and the header
  should say what it is for ("Your answer") rather than "Response".

### The chain this owes

1. Migration adding `review` to the rfi_item table — additive, so no backup
   drama, but say so in the commit.
2. `repo/rfi.py` read/write, `services/rfi.py` update path.
3. The web form and the inline cell (see item 2 below — same table).
4. **MCP**: `mcpserver.py` must be able to READ and WRITE `review`, or the one
   user who works through tools cannot use the field at all. This is the rule
   that exists precisely because a field landed on the web and not on MCP.
5. `services/export_rfi.py::_item_row` widens; `export_open_items._RFI_COLUMNS`
   gains the column. Note the row shape is asserted in
   `tests/test_export_rfi.py::test_compose_item_row_shape` and the width in
   several `test_services.py` row-height tests — those are the ones that move.

### Data-entry rules that bind it

- NEVER PRE-FILL A FIGURE THAT COMES OFF A DOCUMENT. "Our review" is prose about
  what we hold, and it must not become a place where a figure is asserted on the
  client's behalf. If it quotes a number, it quotes it as something to CONFIRM.
- The answer area is the one place on the workbook that is deliberately empty.
  Do not put an em dash or a placeholder in it.

## 2. The RFI due date, editable inline

> "also make the due date editable for RFI items inline in table edit."

The item's own due date (`RfiItem.due_on`, which falls back to the request's
`due_on` via `services.rfi.effective_due`) should be an inline cell like every
other editable value.

Three things this must respect, all already written down:

- **BLUR COMMITS, ESCAPE DISCARDS**, and an UNCHANGED value closes without
  writing — `web/static/inline-cell.js` owns this and must not be forked.
- **A BARE NUMBER IS NOT A DATE.** `parse_human_date` refuses 1-2 digit input on
  purpose; a follow-up typed as "the 5th" once saved as 2027-05-01 and fell off
  every attention window. The refusal message names the forms that work.
- **The fallback is the subtlety.** An item with no due of its own INHERITS the
  request's. So the cell is showing a value the item does not own, and clearing
  it must mean "go back to inheriting", not "no due date". Show inherited values
  differently from set ones, or the first person to edit one will pin a date
  that was tracking the request.

Grant's separate report — "unclear when changes are saved as it just stays blue,
sometimes I need to hit enter, other times not" — is the same surface and should
be fixed in the same pass, not after it.
