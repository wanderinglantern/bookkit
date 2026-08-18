"""The bookkit look — one warm dark palette, and the small Rich helpers that
keep days/money/status rendering identical in every table.

Color is signal, not decoration: gold means "you are here", red means late,
amber means soon, green means bound/done, dim means secondary. Every colored
state also carries a glyph or word so it survives monochrome terminals."""

from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.theme import Theme

from ..dates import days_until
from ..models import is_internal_category
from ..money import format_cents_compact
from ..palette import AMBER, BG, BLUE, DIM, FG, GOLD, GREEN, PANEL, RED, RULE, SURFACE

__all__ = [
    "AMBER", "BG", "BLUE", "DIM", "FG", "GOLD", "GREEN", "PANEL", "RED", "RULE",
    "SURFACE", "BOOKKIT_THEME", "STATUS_STYLES", "status_text", "days_text",
    "date_text", "money_text", "dash", "lines_text", "right", "category_text",
]

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
    # orgs
    "prospect": BLUE,
    "dormant": DIM,
    # projects / needs
    "planned": DIM,
    "active": BLUE,
    "completed": GREEN,
    "cancelled": DIM,
    "placed": GREEN,
    "not_needed": DIM,
    # rfi items
    "outstanding": AMBER,
    "received": GREEN,
    "waived": DIM,
}


def status_text(status: str) -> Text:
    return Text(status, style=STATUS_STYLES.get(status, FG))


def category_text(category: str | None) -> Text:
    """A task's category cell. The Internal category says, ON THE ROW, that
    the task is withheld from the client export — the WORD, not only a glyph,
    and on the row rather than in a hint line.

    Four tables across two screens render this: the account overview and its
    Open Items tab, the navigator's attention feed and its per-account tasks
    group. Their hint lines have UNEQUAL headroom against the 140-column floor
    CLAUDE.md sets — TAB_HINTS["tab-overview"] is already 133 columns and has
    none, while "tab-open-items" is 96 and the navigator's tasks hint is 89
    with the "enter/tab into rows" prefix. So a legend would fit on three of
    the four and be impossible on the default tab of every account. The fact
    belongs to the TASK, not to a screen; putting it in the cell makes it read
    the same on all four, at 23 columns, whatever the hint line is doing.
    "not exported" is the same wording the web surface uses."""
    if not category:
        return dash()
    if is_internal_category(category):
        return Text(f"{category} ⊘ not exported", style=DIM)
    return Text(category, style=AMBER)


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


def lines_text(
    line_ends: tuple[tuple[str, str], ...], today: date | None = None
) -> Text:
    """Per-line renewal clocks: 'IM ◆ 9d over · PR 32d · GL 87d'. Each line
    of cover carries its own countdown — the line, not the program, is what
    the broker renews."""
    if not line_ends:
        return dash()
    text = Text()
    for i, (label, end_iso) in enumerate(line_ends[:4]):
        if i:
            text.append(" · ", style=RULE)
        days = days_until(end_iso, today)
        if days < 0:
            text.append(f"{label} ◆ {-days}d over", style=f"bold {RED}")
        elif days <= 60:
            text.append(f"{label} {days}d", style=AMBER)
        else:
            text.append(f"{label} {days}d", style=DIM)
    return text


def right(label: str) -> Text:
    """A right-aligned column header, to sit over numeric columns."""
    return Text(label, justify="right")
