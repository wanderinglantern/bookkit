# Web surface — visual direction

Companion to `2026-08-17-web-frontend-design.md`. Binding on every template and
stylesheet in `src/bookkit/web/`. Where this document and a task's inline HTML
disagree, this document wins on *appearance and interaction*; the task wins on
*structure and routes*.

Revised 2026-08-17 after Grant reviewed the first mockup. The original direction
inherited `tui/theme.py`'s dark terminal palette wholesale and was rejected as
reading generically AI-generated. That was a fair call: a terminal palette is
correct for a terminal, and carrying it into a browser was inheritance by
default dressed up as consistency.

## The read

One user, at a desk, on a Mac. The page's job is to work an account: see what is
urgent, log what happened, fix what is wrong.

**The work is document-shaped and it happens in daylight.** The surrounding
applications are Outlook, Excel, carrier portals and PDF policy documents — all
light, all paperwork. The artefacts of the trade are schedules of insurance,
submissions, binders: ruled tables of figures. The web surface should look like
it belongs beside those, not beside a terminal.

**Density is still a feature.** The TUI shows an account's whole state without
scrolling, and a web page with 56px rows and floating cards is a downgrade
however modern it looks. Rows sit at roughly 32px. Nothing floats; structure is
carried by rules, not by shadows.

**Instant is the baseline.** The TUI has no loading states. Motion is limited to
a ~120ms settle on swapped panels, and nothing else.

## Palette — towerkit's, and why

Every colour comes from towerkit's brand set, documented as authoritative in
`towerkit/CLAUDE.md` and verified there against the official swatches. The
reason is not decoration: bookkit renders towerkit programs, so the CRM and the
deliverables produced inside it share one visual language.

| Token | Value | Use |
|---|---|---|
| `--ink` | `#000F47` | Midnight — primary text, masthead rule |
| `--accent` | `#0B4BFF` | Blue 500 — current tab, focus, the rail marker |
| `--sky` | `#CEECFF` | quiet fill (the rail's elapsed span, notices) |
| `--ground` | `#FBFCFD` | page ground — cool near-white, biased toward the ink |
| `--paper` | `#FFFFFF` | panels, table bodies |
| `--band` | `#F2F6FA` | table headers |
| `--rule` | `#D8E0EC` | hairlines |
| `--rule-firm` | `#B9C6DA` | container borders, input borders |
| `--muted` | `#5B6478` | labels, secondary text, empty states |

Traffic lights are **status only, never a data series** — towerkit's rule, and it
holds here:

| Token | Value | Use |
|---|---|---|
| `--bound` | `#14853D` | bound, done, received (borders/fills) |
| `--bound-tx` | `#2F7500` | success **text** on white (Green 1000) |
| `--warn` | `#FFBE00` | due soon |
| `--over` | `#C53532` | overdue, error |

**Contrast rule, inherited and load-bearing:** white text fails on any gold, on
Blue 500, on Green 750 and on Sky. Filled chips in those colours take dark ink,
never white. Success *text* on white uses `--bound-tx`, not `--bound`.

**Colour is signal, never decoration.** Every coloured state also carries a word
or a glyph so meaning survives without it. Red plus the word "over" — never red
alone.

Single theme by choice: one user, one machine, a document that wants to look like
paper. `body` paints `background` and `color` explicitly rather than inheriting.

## Type — three roles

```css
--sans:  ui-sans-serif, system-ui, -apple-system, "Segoe UI", "Noto Sans", sans-serif;
--serif: "Iowan Old Style", Charter, Georgia, "Noto Serif", serif;
--mono:  ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
```

- **Serif for names only** — the account name and form titles. **Regular weight
  only, never bold**: towerkit's brand records that Marsh Serif has no bold, and
  honouring that constraint is what keeps this off the generic serif-display look.
- **Sans for language** — everything a person reads as prose.
- **Mono for anything that aligns or is scanned** — dates, money, day counts,
  refs, policy numbers, emails and phones in a column. Apply
  `font-variant-numeric: tabular-nums` wherever digits stack.

No webfonts: nothing loads from a CDN and nothing is bundled — the work machine
reaches github.com and nothing else.

Scale: `0.5625rem` (table/section labels, uppercase, `0.14em` tracking) ·
`0.6875rem` (hints) · `0.8125rem` (table and log body) · `0.9375rem` (prose) ·
`1.6rem` (account name, serif) · `2.75rem` (the day count, mono, tabular).

## The signature: the renewal rail

The one element this page is remembered by, and the one place boldness is spent.

Every account is a clock. The attention window is 120 days, bucket-aligned, and
overdue never falls off. The header renders that window literally: a rail with
Sky filling the elapsed span, ticks at the real bucket boundaries
(`RenewalItem.bucket`), a Blue 500 caret at this account's position, and **the
renewal date printed at the marker** — `RenewalItem.renewal_on`, the date
`days_remaining` counts to. The day count sits large in tabular mono to the
right, with `days` or `days over` beneath it. Lines of cover
(`RenewalItem.lines`) sit under the right end; a program name alone is not enough
context.

Overdue overruns the left edge in `--over` carrying the word `over`, and is
decided by `days_remaining < 0` — never by pixel position.

**Why this is the signature.** The most-repeated bug in this codebase is printing
`placement.period_to` beside a countdown computed from `renewal_on`; it shipped
on four surfaces and rendered a future date as "70d over". The rail puts the date
and its own countdown in one element sourced from one object, making the bug hard
to *draw*, not merely hard to write.

When an account has no live renewal the rail is not rendered at all — an empty
rail implies a clock that is not running.

## Editing: inline first

**Grant's call, 2026-08-17: fields are edited in place, not behind an Edit
button.** A row's editable cells are editable where they sit; the button-then-
modal round trip is the thing the TUI already avoids and the web should not
reintroduce.

This mirrors `tui/widgets/inline_edit.py`, which has done exactly this since
before the web existed, and it reuses that module's declarations rather than
inventing a second set. Which fields are inline-editable is **not** a per-surface
choice.

- **Inline** — the fields the TUI already declares editable: contacts
  (`CONTACT_INLINE`), tasks (`TASK_INLINE`), RFI items (`RFI_ITEM_INLINE`).
- **Form** — creating a record, and editing anything the TUI deliberately kept in
  a form. Interactions are the notable case: the note body is multi-line prose,
  and the TUI declares no inline set for them. Follow that judgment.
- **Confirm step** — destructive actions, server-rendered, never a JS `confirm()`.

Mechanics, and the rules that are not negotiable:

- A cell renders as its value until activated; activating swaps in a single
  input for that one field, pre-filled with the current value.
- **The record's id travels in the URL, never its row position.** The TUI learned
  this: `row_key` is captured when the editor opens because a mid-edit refresh
  can reorder rows, and the write must land on the record the user saw.
- Values parse through `forms.spec.parse_value` — the same function the modal
  forms and the TUI cells use. No second validator.
- **One field edit is one writer action, so it is one batch** (`open_batch(
  source="web", …)`), revertible on its own. This is consistent with the house
  rule, not an exception to it.
- A refusal re-renders the input with the typed value intact and the message
  beside it. Nothing is written, and nothing is retyped.
- Enter commits; Escape reverts to the rendered value.

## Layout

Single column, full width to about 78rem — a working tool, not a reading column.

**Masthead.** Account name in serif Regular, status as a small outlined pill,
the renewal rail beneath. Closed by the tab bar's 2px ink rule.

**Tabs.** Mono, uppercase, letter-spaced, sitting on that ink rule; the current
tab takes `--ink` with a 3px `--accent` underline. A document masthead, not an
app navbar.

**Panels.**
- *Overview* — five ruled sections on `--paper`, each with an uppercase `--muted`
  label. Sections of one document, not floating cards.
- *Contacts, requests, items, open items* — schedule tables. `--band` headers,
  hairline rows, mono for every value that aligns, cells editable in place.
- *Interactions* — the ledger. A continuous hairline down the left margin, dates
  hanging in that margin in mono, the type as a small accent label, and the note
  body in prose sans indented past the rule. The body is the point of the record
  and is always shown, never truncated behind a click.

**Forms.** On `--paper`, with a 2px `--ink` top rule so they read as a slip laid
on the page. Labels above inputs in the uppercase mono label style. Radius is
`2px` on controls, `0` elsewhere. No shadows, no gradients.

## Copy

Plain, active, specific. Buttons name what happens and keep the verb through the
flow. Empty states are invitations, not statements of absence: "No contacts yet.
Add the first one." · "Nothing logged yet." · "No one assigned." · "Nothing
open." · "Nothing in the pipeline."

**Errors say how to fix it, in the interface's voice.** The parser's messages are
currently the parser talking to itself — `cannot read a date from '5'` names the
objection without naming the remedy. Rewrite them **in `forms/spec.py`**, so both
surfaces improve together; do not fork the copy per surface. The date refusal
becomes:

> Enter a date like 2026-10-15, friday, or +2w. A bare number is ambiguous.

which states the fix and preserves the underlying rule (a bare 1–2 digit number
is refused, never guessed).

No terminal affectations in prose — no `!` prefixes, no glyph-as-severity where a
word will do.

## Quality floor

- Responsive to a narrow window: the rail collapses to date plus count, tables
  scroll inside their own `overflow-x: auto` container, and the page body never
  scrolls sideways.
- `:focus-visible` is a 2px `--accent` outline at 2px offset on every interactive
  element, including cells that open an inline editor. This is a keyboard-first
  user coming from a keyboard-first app.
- `@media (prefers-reduced-motion: reduce)` disables the panel settle.
