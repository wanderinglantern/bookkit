# 2026-08-24 — The Web UI System Pass (design turn 4, built)

## Goal

Adopt the eight shared patterns from `Web UI System Pass.dc.html` across
Book, Open items, Today and Towers, plus the Today restructure and the
exports drawer. Follows the Program Worksheet merge the same day.

## State — COMPLETE on feat/web-system-pass

- **P1–P3**: `macros/page.html` — `band()`, `sect()/sect2()`, `stats()/stat()`.
  The stat-strip CSS is the program band's (`.band-stats/.stat`), one home.
- **P4**: `.edge-danger/.edge-warn` left stripes; decided by days<0.
- **P5**: `.row-tools` holds row actions at opacity 0 (never visibility);
  room reserved; destructive controls keep full accessible names.
- **P6**: `.state-overdue/-soon/-bound/-flight/-closed`; word + colour.
  NOT yet a full consolidation — the nine legacy classes still exist where
  untouched screens use them; the touched screens use the new five. The
  find-and-replace across the remaining six templates is the open half of
  adoption step 3.
- **P7**: `.empty-state` voices on the touched screens; the program tab's
  four distinct states untouched, as the doc requires.
- **P8**: `WEB_SLATE/#0F4C5C`, `WEB_SLATE_WASH` in palette + theme_css; spent
  on the structure index's group headers.
- **Today**: `_needs_you()` merges overdue renewals + due/overdue tasks +
  past-SLA into one worst-first list; each row prints the DATE its countdown
  counts to (the standing rule — the first cut missed it and the old test
  caught it). Unlinked renewals drop the cover clause rather than printing
  "— —". Context lists are `<details class="aside-section">` with counts
  visible closed; changes keeps its Revert. `partials/_today_tasks.html`
  deleted; `partials/_today_needs.html` is the swap target.
- **Open items**: `show` ∈ all/overdue/week/undated/requests (+account);
  `overdue=1` still lands (old bookmarks); done/drop answer
  `partials/_items_list.html` (#items-panel).
- **Exports**: `routes/exports.py` + `exports.html`; registered in app.py.

## Deliberate deviations

- No book-wide open-items export button in the Open-items band — that export
  does not exist yet (unbuilt is unrendered); the drawer lists the
  account-scoped ones.
- Today's stat strip omits unplaced capacity — computing it opens every
  linked file on the front door; needs a projection-backed figure first.
- The demoted lists live as on-page disclosures, not separate screens — the
  screens don't exist; the rail counts anchor to them.

## Review

15-agent adversarial pass: 12 confirmed (S1–S12), all fixed in 09abc31 —
SLA rows count to the DEADLINE (sent + `sla.DEFAULT_SLA_DAYS`) with the
overdue tone and the underwriter to chase; items chips live inside the
swapped section; exports uses `labels_for` + the account macro;
`@media (hover: none)` shows row actions on touch; disclosure summaries
are phrasing-only with a ▸/▾ glyph and anchor ids INSIDE the details;
"Requests to chase" restored as a disclosure with a rail count; web tests
for past-SLA (pinned to the frozen clock) and a de-vacuoused `overdue=1`
test. Report artifact: Web UI System Pass (six labeled screenshots).

## Environmental: 3 failures that are NOT this branch's

`test_contrast` ×2 + `test_snapshots::test_account_placements[140x45]`
fail identically on main. Proven by stash/rerun/pop: they trace to
UNCOMMITTED chevron/theme work in the `../towerkit` sibling checkout
(layout.py CHEVRON_BAND/chevron_points, mpl_program.py, theme.py, dated
"Grant, 2026-08-24") — a parallel session actively working there. Left
strictly untouched; when that work commits, the TUI contrast/snapshot
baselines re-settle on their side.

## Not touched, per the doc's own scope note

Calendar, Markets, Pipeline, Team, capture, search. Relationship keeps its
timeline; it only wants a tier-2 header later.
