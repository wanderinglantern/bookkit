"""Renewal calendar — months across, accounts down, blocks at expiry,
coloured by status. The one screen that makes the year legible at a glance."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from datetime import date

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from ...services import renewals
from .. import theme
from ..widgets.tables import ListTable

# glyph per status; color comes from theme.STATUS_STYLES so the calendar
# never drifts from the rest of the app
STATUS_GLYPH = {
    "prospective": "░░",
    "submitted": "▒▒",
    "quoted": "▓▓",
    "bound": "██",
    "lapsed": "××",
}


def _add_months(d: date, months: int) -> date:
    month0 = d.month - 1 + months
    return date(d.year + month0 // 12, month0 % 12 + 1, 1)


def _cell(status: str, renews: date, overdue: bool) -> Text:
    """A month cell: status glyph + day of month; overdue goes bold red ◆.

    `overdue` comes from days_remaining, not from the cell's own position:
    driving it off `renews < today` meant a renewal that had already lapsed
    but sat in a future grid column rendered as comfortable, and the legend's
    "◆ overdue" was a marker that could essentially never appear."""
    glyph = STATUS_GLYPH.get(status, "??")
    if overdue:
        return Text(f"◆ {glyph} {renews.day}", style=f"bold {theme.RED}")
    return Text(
        f"{glyph} {renews.day}", style=theme.STATUS_STYLES.get(status, theme.FG)
    )


class CalendarScreen(Screen):
    app: BookkitApp
    DEFAULT_CSS = """
    CalendarScreen #calendar-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListTable(id="calendar-table")
        legend = " ".join(
            f"[{theme.STATUS_STYLES.get(status, theme.FG)}]{glyph} {status}[/]"
            for status, glyph in STATUS_GLYPH.items()
        )
        yield Static(
            f"{legend} [b {theme.RED}]◆ overdue[/]"
            f"  [{theme.DIM}]— [b]j[/b]/[b]k[/b] move · [b]enter[/b] opens account · "
            f"[b]esc[/b] back[/]",
            id="calendar-hint",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#calendar-table", ListTable)
        today = date.today()
        months = [_add_months(today.replace(day=1), i) for i in range(12)]
        headers = [
            Text(m.strftime("%b %y"), style=f"bold {theme.GOLD}" if i == 0 else "")
            for i, m in enumerate(months)
        ]
        # column 0 is "overdue": the grid used to start at THIS month and
        # silently drop everything earlier, so every already-lapsed renewal —
        # the one category CLAUDE.md says must never fall off — was invisible
        # on the screen meant to make the year legible.
        table.add_columns("account", Text("overdue", style=f"bold {theme.RED}"),
                          *headers)
        rows: dict[str, list[Text | str]] = {}
        keys: dict[str, str] = {}
        for item in renewals.upcoming(self.app.conn, today, days=365):
            cells = rows.setdefault(item.org.name, [""] * 13)
            keys[item.org.name] = item.org.id
            # renewal_on, not period_to: the countdown is measured to the
            # earliest LINE end, so bucketing on the program end plotted
            # Atomic three months late, on the wrong side of today
            renews = date.fromisoformat(
                item.renewal_on or item.placement.period_to
            )
            overdue = item.days_remaining < 0
            idx = (renews.year - months[0].year) * 12 + renews.month - months[0].month
            cell = _cell(item.placement.status, renews, overdue)
            if overdue:
                cells[0] = cell
            elif 0 <= idx < 12:
                cells[idx + 1] = cell
        for name in sorted(rows):
            table.add_row(name, *rows[name], key=keys[name])
        table.focus()

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        if event.row_key.value:
            self.app.open_account(event.row_key.value)
