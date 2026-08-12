"""The bookkit look — one warm dark palette, and the small Rich helpers that
keep days/money/status rendering identical in every table.

Color is signal, not decoration: gold means "you are here", red means late,
amber means soon, green means bound/done, dim means secondary. Every colored
state also carries a glyph or word so it survives monochrome terminals."""

from __future__ import annotations

from rich.text import Text
from textual.theme import Theme

from ..money import format_cents_compact

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

BOOKKIT_THEME = Theme(
    name="bookkit",
    primary=GOLD,
    secondary=BLUE,
    accent=GOLD,
    foreground=FG,
    background=BG,
    surface=SURFACE,
    panel=PANEL,
    success=GREEN,
    warning=AMBER,
    error=RED,
    dark=True,
    variables={
        "block-cursor-background": GOLD,
        "block-cursor-foreground": BG,
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": RULE,
        "block-cursor-blurred-foreground": FG,
        "block-cursor-blurred-text-style": "none",
        "footer-background": PANEL,
        "footer-key-foreground": GOLD,
        "footer-description-foreground": DIM,
        "input-selection-background": f"{GOLD} 35%",
    },
)

# every stage/status vocabulary in the app, mapped onto the five state colors
STATUS_STYLES: dict[str, str] = {
    # placements
    "prospective": DIM,
    "submitted": BLUE,
    "quoted": BLUE,
    "bound": GREEN,
    "lapsed": RED,
    # opportunities
    "identified": DIM,
    "qualified": BLUE,
    "presented": AMBER,
    "won": GREEN,
    "lost": RED,
    # tasks
    "open": FG,
    "done": GREEN,
    "dropped": DIM,
    # submissions
    "out": BLUE,
    "declined": RED,
    "withdrawn": DIM,
    # projects / needs
    "planned": DIM,
    "active": BLUE,
    "completed": GREEN,
    "cancelled": DIM,
    "placed": GREEN,
    "not_needed": DIM,
}


def status_text(status: str) -> Text:
    return Text(status, style=STATUS_STYLES.get(status, FG))


def days_text(days: int) -> Text:
    """-345 → '◆ 345d over' (red); 49 → '49d' (amber ≤ 60); 210 → '210d'."""
    if days < 0:
        return Text(f"◆ {-days}d over", style=f"bold {RED}", justify="right")
    if days <= 60:
        return Text(f"{days}d", style=AMBER, justify="right")
    return Text(f"{days}d", style=DIM, justify="right")


def date_text(iso: str, days: int) -> Text:
    if days < 0:
        return Text(iso, style=RED)
    if days <= 60:
        return Text(iso, style=AMBER)
    return Text(iso)


def money_text(cents: int | None) -> Text:
    if not cents:
        return Text("—", style=DIM, justify="right")
    return Text(format_cents_compact(cents), justify="right")


def dash() -> Text:
    return Text("—", style=DIM)


def right(label: str) -> Text:
    """A right-aligned column header, to sit over numeric columns."""
    return Text(label, justify="right")
