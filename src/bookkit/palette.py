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

# --- Web palette: towerkit's light document scheme ------------------------
#
# Taken verbatim from the palette table in
# docs/superpowers/specs/2026-08-17-web-visual-direction.md — do not invent
# values here; that document is authoritative and this module mirrors it.

WEB_INK = "#000F47"  # Midnight — primary text, masthead rule
WEB_ACCENT = "#0B4BFF"  # Blue 500 — current tab, focus, the rail marker
WEB_SKY = "#CEECFF"  # quiet fill (the rail's elapsed span, notices)
WEB_GROUND = "#FBFCFD"  # page ground — cool near-white, biased toward the ink
WEB_PAPER = "#FFFFFF"  # panels, table bodies
WEB_BAND = "#F2F6FA"  # table headers
WEB_RULE = "#D8E0EC"  # hairlines
WEB_RULE_FIRM = "#B9C6DA"  # container borders, input borders
WEB_MUTED = "#5B6478"  # labels, secondary text, empty states

# Traffic lights — status only, never a data series (towerkit's rule).
WEB_BOUND = "#14853D"  # bound, done, received (borders/fills)
WEB_BOUND_TX = "#2F7500"  # success TEXT on white (Green 1000) — WEB_BOUND fails contrast there
WEB_WARN = "#FFBE00"  # due soon
WEB_OVER = "#C53532"  # overdue, error

# Every name above the traffic lights plus the traffic lights themselves,
# for anything (tests, theme_css.py) that needs to walk the whole web set
# rather than naming tokens one at a time.
WEB_TOKENS: tuple[str, ...] = (
    "WEB_INK", "WEB_ACCENT", "WEB_SKY", "WEB_GROUND", "WEB_PAPER", "WEB_BAND",
    "WEB_RULE", "WEB_RULE_FIRM", "WEB_MUTED",
    "WEB_BOUND", "WEB_BOUND_TX", "WEB_WARN", "WEB_OVER",
)
