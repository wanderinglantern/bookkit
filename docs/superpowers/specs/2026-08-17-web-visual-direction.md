# Web surface — visual direction

Companion to `2026-08-17-web-frontend-design.md`. Binding on every template and
stylesheet in `src/bookkit/web/`. Where this document and a task's inline HTML
disagree, this document wins on *appearance*; the task wins on *structure and
routes*.

## The read

One user, at a desk, on a Mac, replacing a keyboard-driven terminal. The page's
job is to work an account: see what is urgent, log what happened, fix what is
wrong. Two consequences:

- **Density is a feature.** The TUI shows an account's whole state without
  scrolling. A web page with 56px rows and floating cards is a downgrade, however
  modern it looks. Rows are ~32px. Padding is tight. Nothing floats.
- **Instant is the baseline.** The TUI has no loading states. Motion is limited to
  a 120ms settle on swapped panels, and nothing else.

## Palette — pinned, not chosen

Every colour comes from `bookkit/palette.py` as a `--bk-*` custom property. No
literal hex in any stylesheet. The palette is a warm-ink-on-cool-dark terminal
scheme and it stays that way:

| Token | Value | Use |
|---|---|---|
| `--bk-bg` | `#15171c` | page ground |
| `--bk-surface` | `#1a1d23` | panels |
| `--bk-panel` | `#232733` | header band, form ground, table header |
| `--bk-rule` | `#3a4150` | every rule and border |
| `--bk-fg` | `#d5d2c9` | primary text (warm bone, never pure white) |
| `--bk-dim` | `#8a8577` | labels, secondary text, empty states |
| `--bk-gold` | `#d6b35a` | focus, current tab, selection — the one accent |
| `--bk-red` | `#d57367` | overdue, error |
| `--bk-amber` | `#d9a441` | due soon |
| `--bk-green` | `#84a98c` | bound, done |
| `--bk-blue` | `#7f9cc4` | in flight (submitted, quoted, out) |

**Colour is signal, never decoration.** Every coloured state also carries a word
or a glyph, so meaning survives without colour. Red plus "over" — never red alone.
Gold is spent on focus and the current tab only; if a third thing wants gold, it
does not get it.

## Type — two roles, both honest

```css
--bk-sans: ui-sans-serif, system-ui, -apple-system, "SF Pro Text", "Segoe UI", sans-serif;
--bk-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
```

**Sans for language. Mono for anything that must align or be scanned** — dates,
money, day counts, refs, IDs, policy numbers. This is inherited from the TUI as
information design, not as a terminal costume: mono earns its place where columns
line up, and nowhere else. Apply `font-variant-numeric: tabular-nums` wherever
digits stack.

No webfonts. Nothing is loaded from a CDN, and nothing is bundled — the work
machine reaches github.com and nothing else.

Scale, and stay on it: `0.6875rem` (labels, uppercase, `0.1em` tracking) ·
`0.8125rem` (table and log body) · `0.9375rem` (prose) · `1.375rem` (account
name) · `2.75rem` (the day count, mono, tabular).

## The signature: the renewal rail

The one element this page is remembered by, and the one place boldness is spent.

Every account is a clock. The app's attention window is 120 days, bucket-aligned,
and overdue never falls off. The header renders that window literally:

```
  ATOMIC INDUSTRIES                                    active
  ┌──────────────────────────────────────────────────────────┐
  │ ◀ over │        │        │        │        │             │   47
  │        ·        ·        ▲        ·        ·             │   days
  └────────────────────────┬─────────────────────────────────┘
   overdue  0-30    31-60  │ 61-90   91-120            GL, AL, EL
                    2026-10-03
```

- A horizontal rail spanning the 120-day window, with ticks at the real bucket
  boundaries the app already computes (`RenewalItem.bucket`).
- A marker at this account's position, with **the renewal date printed at the
  marker** — `RenewalItem.renewal_on`, the date `days_remaining` counts to.
- The day count set large in tabular mono to the right, with the word `days` or
  `over` beneath it.
- Overdue overruns the left edge of the rail in `--bk-red`, carrying the word
  `over`. Overdue is decided by `days_remaining < 0`, never by pixel position.
- Lines of cover (`RenewalItem.lines`, e.g. "GL, AL, EL") sit under the right end.
  A program name alone is not enough context.

**Why this is the signature.** The most-repeated bug in this codebase is printing
`placement.period_to` beside a countdown computed from `renewal_on` — it shipped
on four surfaces and rendered a future date as "70d over". The rail puts the date
and its own countdown in one element, sourced from one object. It makes the bug
hard to *draw*, not merely hard to write.

When an account has no live renewal, the rail is not rendered at all — an empty
rail would imply a clock that is not running.

## Layout

Single column, full width, no max-width gutter — this is a working tool and the
window is the canvas.

**Masthead.** Account name at `1.375rem`, tight tracking, `--bk-fg`. Status as a
small pill. The renewal rail beneath. Closed with a 1px `--bk-rule` line.

**Tabs.** Text, not buttons. `--bk-dim`, uppercase, letter-spaced; the current tab
takes `--bk-fg` with a 2px `--bk-gold` bottom border. Echoes the TUI's tab bar
and costs no vertical space.

**Panels.**
- *Overview* — five ruled sections, each with an uppercase `--bk-dim` label and a
  hairline above. Not floating cards: sections of one document.
- *Contacts* — a schedule table. Ruled rows, `--bk-panel` header, mono for email
  and phone, sans for names and titles.
- *Interactions* — **the ledger.** A continuous 1px `--bk-rule` down the left
  margin. Dates hang in that margin in mono. The type sits as a small `--bk-dim`
  caps label, the subject in `--bk-fg`, and the note body in prose sans beneath,
  indented past the rule. The body is the point of the record and is always shown,
  never truncated behind a click.

**Forms.** Inline, in place, on `--bk-panel`. Labels above inputs in the uppercase
label style. Inputs are `--bk-bg` with a `--bk-rule` border, `2px` radius, and no
shadow. The error message sits directly above the fields in `--bk-red` prefixed
with `!` so it reads without colour.

Radius is `2px` on controls and `0` everywhere else. No shadows. No gradients.

## Copy

Plain, active, specific. Buttons name what happens and keep the same verb through
the flow: "Add contact" → "Save" → the row appears. "Delete interaction" on the
confirm, and the log returns without it.

Empty states are invitations, not statements of absence:

- Contacts — "No contacts yet. Add the first one."
- Interactions — "Nothing logged yet."
- Team — "No one assigned."
- Tasks — "Nothing open."
- Opportunities — "Nothing in the pipeline."

Errors explain what went wrong and how to fix it, in the interface's voice. The
parser's own messages already do this ("due: cannot read a date from '5'") — render
them as-is rather than rewriting them, so the TUI and the web say the same sentence.

## Quality floor

- Responsive to a narrow window: the rail collapses to date + count, and the
  contacts table scrolls inside its own `overflow-x: auto` container. The page
  body never scrolls sideways.
- `:focus-visible` is a 2px `--bk-gold` outline with 2px offset, on every
  interactive element. This is a keyboard-first user coming from a keyboard-first
  app; focus must always be visible.
- `@media (prefers-reduced-motion: reduce)` disables the panel settle.
- Single theme by choice — the palette is a dark terminal scheme and the app has
  exactly one user on one machine. Paint `background` and `color` on `body`
  explicitly rather than inheriting.
