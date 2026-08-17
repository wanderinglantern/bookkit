# Web surface — visual direction

**Superseded twice, now derived from the Design handoff.** Binding on every
template and stylesheet in `src/bookkit/web/`.

Source of truth: the Claude Design project *BookKit and TowerKit design*
(`e67f8ba0-4e47-4137-a561-9838b16b8483`), specifically `Account View.dc.html` and
`design_handoff_bookkit_towerkit_web/README.md`. The handoff marks colours,
typography, spacing, row heights and copy as **final and exact**. Where this
document and the handoff disagree, the handoff wins and this document is wrong.

Where the handoff's README and `Account View.dc.html` disagree, **the `.dc.html`
wins** — the README's §5 still describes the TUI's nine numbered tabs, and the
design file replaces them with four. The design file is the later artefact.

## History, so the reasoning is not relitigated

1. First attempt inherited `tui/theme.py`'s dark terminal palette. Rejected by
   Grant as reading AI-generated — correctly: a terminal palette is right for a
   terminal, and carrying it into a browser was inheritance by default.
2. Second attempt used towerkit's brand set on a light document ground. Approved,
   and close to right — but invented a renewal rail and a cool-biased neutral
   ramp the designer did not use.
3. This version takes the handoff's tokens verbatim. **The renewal rail is
   dropped** (Grant, 2026-08-17) in favour of the design's header badge plus
   snapshot line.

## Decisions taken on the handoff (Grant, 2026-08-17)

- **Stack stays server-rendered** (FastAPI + Jinja + HTMX). The handoff
  recommends React + Vite on the premise that "the repos have no web stack yet";
  that premise is stale. Its own instruction is to recreate the designs in the
  target environment using that stack's patterns, and our stack is the target.
- **The four-tab account structure is adopted.**
- **The renewal rail is dropped.**

## Stance

**Mouse-first.** This reverses the previous version. The handoff is explicit: the
TUI's keyboard-only model is replaced by click-to-edit, visible buttons and hover
affordances. Keyboard shortcuts may exist as accelerators but **must never be the
only route to an action**. Every action needs a visible control.

Density stays: row height **30px compact (default) / 38px comfortable, never
below 30px**.

## Palette — from `towerkit/themes/marsh.json` plus state colours

Verified against that file: `ink`, `muted`, `accent`, `grid`, `noCover`,
`unplaced` and the carrier palette all match the handoff exactly.

| Token | Value | Use |
|---|---|---|
| `ink` | `#000F47` | primary text, structural hairlines, zero line |
| `paper` | `#FFFFFF` | page, odd table rows |
| `wash` | `#FDFCFA` | left rail, sheet headers, right rail |
| `wash-2` | `#FBFAF8` | zebra row (even) |
| `stone` | `#F7F3EE` | search field, tower "no cover" column |
| `hairline` | `#E6E2DB` | section borders |
| `hairline-2` | `#F0EDE8` | row separators |
| `border` | `#d9d5ce` | control borders |
| `muted` | `#7B7974` | secondary text, column headers |
| `accent` | `#0B4BFF` | selection, primary buttons, links, editable focus |
| `accent-wash` | `#E9F1FF` | selected row background |
| `grid` | `#CEECFF` | calendar bound block, tower grid |
| `danger` | `#C53532` | overdue, invalid, lost |
| `danger-wash` | `#FDECEA` | overdue badge/cell background |
| `warn` | `#CB7E03` | due soon, unplaced |
| `warn-wash` | `#FFF8E6` | warning strip |
| `good` | `#2F7500` | bound, placed, won |
| `good-wash` | `#F1F7EF` | success strip |
| `good-badge` | `#E8F3E4` | status badge background (from the design file) |
| `edit-underline` | `#cfd6e8` | the dashed underline on editable values |
| `unplaced` | `#B9B6B1` | hatched unplaced capacity |

Row hover `#F2F6FF`. Selected row `#E9F1FF` plus `inset 3px 0 0 #0B4BFF`.

**Colour is signal, never decoration, and every coloured state carries a glyph or
word**: `◆` overdue/error, `△` warning, `★` primary contact, `✓` clean.

Single theme by choice.

## Type — and two corrections to the previous version

Both of the previous version's typographic rules were wrong.

- It banned webfonts as impossible. **They ship**: `towerkit/src/towerkit/fonts/`
  holds NotoSans and NotoSerif under OFL. They are to be self-hosted; nothing is
  fetched from a CDN, so the work machine is unaffected. **Copying the files into
  web package data is Task 16's packaging work** — until then the stacks below
  fall back to Georgia and system-ui, which is a known temporary state, not the
  target.
- It insisted on serif at Regular weight only, citing "Marsh Serif has no bold".
  That rule is about *Marsh Serif*. The Noto Serif fallback **does** ship Bold.

```
--serif : "Noto Serif", Georgia, serif      /* 400 and 700 only — see below */
--sans  : "Noto Sans", system-ui, sans-serif
--mono  : ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace
```

- **Serif** — screen titles 20–25px, section/insured names 16–19px, stat figures
  22–26px. The handoff asks for weight 600; only Regular (400) and Bold (700)
  ship, so **use 700 and do not request 600** — a missing weight is synthesised
  by the browser and looks smeared. Record this as a deviation from the handoff.
- **Sans** — all UI text. Body 13px, secondary 12px, badges 11px.
- **Mono** — every number, date, id, file name, share, and **all column headers**
  (10px, `letter-spacing:.1em`, uppercase). Column headers are mono *because the
  data under them is*. Use `font-variant-numeric: tabular-nums` wherever digits
  stack.

The handoff specifies JetBrains Mono. **It does not ship anywhere in either
repo.** Until it is added, use the system mono stack above — on Grant's Mac that
resolves to SF Mono, which is close in colour and metrics. Flagged as the second
deviation; adding the OFL files later is a drop-in change to one token.

## Geometry

- Page padding 20px horizontal; section gap 14px.
- Top bar 56px; sheet header row 26–30px; table header 28–30px.
- Radii: 3px chips/badges, 4px inputs, 6–7px cards/sheets, 15–18px pills.
- Shadows: exactly two — card hover `0 2px 8px rgba(0,15,71,.08)`, toast
  `0 8px 24px rgba(0,15,71,.28)`. No others.
- Tables are **CSS grid, never table layout**, with a trailing actions column.

## The account page

From `Account View.dc.html`. This replaces the previous version's masthead + rail
+ five overview cards entirely.

**Top bar (56px)** — `BookKit` wordmark in serif 700 19px, nav items, a search
pill (`#F7F3EE`, radius 17), a persistent **`Undo <last change>`** pill
(`1px #d9d5ce`, radius 17, hover accent) shown only when there is something to
undo, and a 32px avatar circle in `ink`.

**Header block** — breadcrumb `Book / <insured>` in 12px muted, then a baseline
row:

- insured name, serif 700, 25px
- `ACC-0001` in mono 11px muted
- status badge: `#E8F3E4` on `#2F7500`, 11px 600, radius 3
- when overdue, a danger badge: `◆ renewal 345d overdue`, `#FDECEA` on `#C53532`

Right-aligned actions: `+ Log interaction` (primary accent pill, white text),
then `+ Task`, `Renew`, `···` as bordered pills.

**Tabs** — four, each with a count badge, on a `1px #000F47` bottom border. The
active tab takes `ink` at weight 600 with a `2px #0B4BFF` bottom border and an
`#E9F1FF`/`#0B4BFF` badge; inactive are `muted` with an `#F7F3EE`/`#7B7974` badge.

| Tab | Holds |
|---|---|
| **Program** | placements, the tower, carriers on this program |
| **Relationship** | contacts, interactions (the timeline) |
| **Work** | tasks, project needs, RFI requests |
| **Pipeline** | opportunities, submissions out |

**There is no Overview tab.** The right rail replaces it.

**Right rail (296px)** — `border-left 1px #E6E2DB`, background `#FDFCFA`, its own
scroll. Four stacked sections, each with a mono 10px `.16em` uppercase 700 label:

- `SNAPSHOT` — key/value rows, label 12px muted left, value mono 12px right.
  Rows: next renewal (danger + `· NNNd over` when overdue), bound premium,
  program premium, top of tower, unplaced (warn), open work, last touch.
- `TEAM` — with an `Assign` link in accent; name at weight 500 over a 12px muted
  detail line.
- `DOCUMENTS` — empty state: "no documents yet" plus "Drop a binder, loss run or
  SOV here — BookKit records the path, not the file."
- `RECENT CHANGES` — mono 11px time, description, who, and a **`Revert` link in
  accent** per row, separated by `1px #F0EDE8`. This is the batch/undo history
  given a surface.

**The renewal invariant survives the rail's removal.** The overdue badge and the
snapshot's `next renewal` row both print the date *and* its own count from one
`RenewalItem`. Keep them sourced from one object; the named test stays.

## Inline editing — the core pattern

Every editable value renders with a **`1px dashed #cfd6e8` underline**. That is
the discoverability affordance the previous version lacked; without it a
mouse-first user cannot tell what is editable.

Click → a 23–24px input (or `<select>` for enums) replaces the text in place,
autofocused, `1px #0B4BFF` border.

- `Enter` commits
- `Tab` commits and moves to the next editable cell in the row
- `Escape` cancels
- **blur cancels — never a surprise write.** This mirrors
  `tui/widgets/inline_edit.py` and is new to this version.

Values run through the same parsers as the forms. A refused parse keeps the
editor open with the text intact.

Exactly one cell editor is open at a time; opening another supersedes it, and
re-pointing a table at different rows cancels it.

## Undo

Every commit is one entry in an undo stack:

- a **toast** — `ink` background, bottom centre, radius 8, shadow
  `0 8px 24px rgba(0,15,71,.28)` — reading `Saved · premium → $8.25M` with an
  `Undo` link in `#82BAFF`
- the **top-bar pill** showing `Undo <last change>`
- `RECENT CHANGES` in the right rail, with per-row `Revert`

Undo is also the recovery path for `Done` / `Drop` / `Lost` / added rows.

## Adding, and row actions

**Never keyboard-only.** A primary `+ Add …` pill in the pane header, a ghost
`+ …` row at the end of the list, and a `+` beside the rail's section. A new row
is appended and opens straight into edit mode on its first field.

Row actions are plain text buttons in the trailing column, `muted` until hover
(`#0B4BFF`): renewals `Renew · Layer · Open`, tasks `Done · Drop`, contacts
`Make primary`.

## Copy

Empty states: attention lists that are empty read "nothing here — that's good";
addable lists read "empty — add the first row"; open tasks read "no open tasks —
add one".

Errors say how to fix it. `forms/spec.py`'s `date_refusal` is the one home; the
handoff's wording for the same refusals is
`"<input>" is not a date (YYYY-MM-DD)` and `"<input>" is not an amount` — align
ours toward naming both the value and the remedy, and keep one function.

## Quality floor

- Desktop-first (1280–1920). Below ~1100px the search pill shrinks
  (`flex:0 1 240px; min-width:0`) and the avatar stays `flex:none`. Below ~1000px
  the right rail moves below the tab body. **Tables scroll horizontally rather
  than dropping columns**; the leftmost identity column and the rightmost actions
  column stay put.
- `:focus-visible` is a visible accent outline on every interactive element.
  Mouse-first does not mean keyboard-hostile.
- `@media (prefers-reduced-motion: reduce)` disables transitions.
- No icon set, no hand-drawn SVG. Glyphs are text: `◆ △ ★ ✓ ▶ ▼ +`.
