"""Every colour bookkit renders, in one place.

There are two palettes here, not one that drifted into two. The TUI palette
(``BG``…``BLUE``) is a terminal scheme: warm, dark, built for a monospace
grid under Textual. The web palette (``WEB_*``) is a document scheme:
towerkit's light brand set, because the web surface renders towerkit
programs and sits beside Outlook, Excel and PDF policy documents, not beside
a terminal (see docs/superpowers/specs/2026-08-17-web-visual-direction.md).
They are different media, not a divergence — but two homes for colour is
exactly the kind of drift this project keeps getting bitten by, so both live
here, and nothing outside this module may declare a hex value.
"""

from __future__ import annotations

# --- TUI palette: one warm dark scheme -----------------------------------

BG = "#15171c"  # screen
SURFACE = "#1a1d23"  # panes
PANEL = "#232733"  # bars, cards, modals
RULE = "#3a4150"  # borders, separators
FG = "#d5d2c9"  # primary text
DIM = "#8a8577"  # secondary text
GOLD = "#d6b35a"  # focus, selection, accent
RED = "#d57367"  # overdue, error
AMBER = "#d9a441"  # due soon, warning
GREEN = "#84a98c"  # bound, done, success
BLUE = "#7f9cc4"  # in flight (submitted, quoted, out)

# --- Web palette: the Design handoff's token table -------------------------
#
# Taken verbatim from the Palette table in
# docs/superpowers/specs/2026-08-17-web-visual-direction.md (source:
# towerkit/themes/marsh.json plus state colours) — do not invent values here;
# that document is authoritative and this module mirrors it.
#
# This REPLACES an earlier cool-biased set (#FBFCFD ground, #5B6478 muted,
# #D8E0EC rule, ...) that Grant rejected on 2026-08-17: it was this project's
# own invention layered on top of towerkit's brand set, not the handoff's
# actual values, and it also carried a renewal-rail set of tokens for a rail
# the handoff replaced with a header badge + snapshot row. Nothing below is
# invented; every hex is copied from the spec's table.

WEB_INK = "#000F47"  # primary text, structural hairlines, zero line
WEB_PAPER = "#FFFFFF"  # page, odd table rows
WEB_WASH = "#FDFCFA"  # left rail, sheet headers, right rail
WEB_WASH_2 = "#FBFAF8"  # zebra row (even)
WEB_STONE = "#F7F3EE"  # search field, tower "no cover" column
WEB_HAIRLINE = "#E6E2DB"  # section borders
WEB_HAIRLINE_2 = "#F0EDE8"  # row separators
WEB_BORDER = "#d9d5ce"  # control borders
WEB_MUTED = "#7B7974"  # secondary text, column headers
WEB_ACCENT = "#0B4BFF"  # selection, primary buttons, links, editable focus
WEB_ACCENT_WASH = "#E9F1FF"  # selected row background
WEB_GRID = "#CEECFF"  # calendar bound block, tower grid
WEB_DANGER = "#C53532"  # overdue, invalid, lost
WEB_DANGER_WASH = "#FDECEA"  # overdue badge/cell background
WEB_WARN = "#CB7E03"  # due soon, unplaced
WEB_WARN_WASH = "#FFF8E6"  # warning strip
WEB_GOOD = "#2F7500"  # bound, placed, won
WEB_GOOD_WASH = "#F1F7EF"  # success strip
WEB_GOOD_BADGE = "#E8F3E4"  # status badge background (from the design file)
WEB_EDIT_UNDERLINE = "#cfd6e8"  # the dashed underline on editable values
WEB_UNPLACED = "#B9B6B1"  # hatched unplaced capacity
WEB_HOVER = "#F2F6FF"  # row hover, named separately from the table above it

# Exactly two shadows exist anywhere on the web surface (Geometry section of
# the spec). Stored as full box-shadow values here, not as literal rgba() in
# app.css, so the stylesheet's no-literal-colour test stays meaningful —
# app.css only ever writes `box-shadow: var(--shadow-card)`.
WEB_SHADOW_CARD = "0 2px 8px rgba(0, 15, 71, .08)"  # card hover
WEB_SHADOW_TOAST = "0 8px 24px rgba(0, 15, 71, .28)"  # the undo toast

# Every web name above, for anything (tests, theme_css.py) that needs to walk
# the whole set rather than naming tokens one at a time.
WEB_TOKENS: tuple[str, ...] = (
    "WEB_INK", "WEB_PAPER", "WEB_WASH", "WEB_WASH_2", "WEB_STONE",
    "WEB_HAIRLINE", "WEB_HAIRLINE_2", "WEB_BORDER", "WEB_MUTED", "WEB_ACCENT",
    "WEB_ACCENT_WASH", "WEB_GRID", "WEB_DANGER", "WEB_DANGER_WASH",
    "WEB_WARN", "WEB_WARN_WASH", "WEB_GOOD", "WEB_GOOD_WASH",
    "WEB_GOOD_BADGE", "WEB_EDIT_UNDERLINE", "WEB_UNPLACED", "WEB_HOVER",
    "WEB_SHADOW_CARD", "WEB_SHADOW_TOAST",
)
