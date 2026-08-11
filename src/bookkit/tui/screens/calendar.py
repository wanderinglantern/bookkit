"""Renewal calendar — months across, accounts down, blocks at expiry,
coloured by status. The one screen that makes the year legible at a glance."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from datetime import date

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header

from ...services import renewals
from ..widgets.tables import ListTable

STATUS_GLYPH = {
    "prospective": "[dim]░░[/dim]",
    "submitted": "[yellow]▒▒[/yellow]",
    "quoted": "[cyan]▓▓[/cyan]",
    "bound": "[green]██[/green]",
    "lapsed": "[red]××[/red]",
}


def _add_months(d: date, months: int) -> date:
    month0 = d.month - 1 + months
    return date(d.year + month0 // 12, month0 % 12 + 1, 1)


class CalendarScreen(Screen):
    app: BookkitApp
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListTable(id="calendar-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#calendar-table", ListTable)
        today = date.today()
        months = [_add_months(today.replace(day=1), i) for i in range(12)]
        table.add_columns("account", *[m.strftime("%b %y") for m in months])
        rows: dict[str, list[str]] = {}
        keys: dict[str, str] = {}
        for item in renewals.upcoming(self.app.conn, today, days=365):
            cells = rows.setdefault(item.org.name, [""] * 12)
            keys[item.org.name] = item.org.id
            expiry = date.fromisoformat(item.placement.period_to)
            idx = (expiry.year - months[0].year) * 12 + expiry.month - months[0].month
            if 0 <= idx < 12:
                cells[idx] = f"{STATUS_GLYPH.get(item.placement.status, '??')} {expiry.day}"
        for name in sorted(rows):
            table.add_row(name, *rows[name], key=keys[name])
        table.focus()

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        if event.row_key.value:
            self.app.open_account(event.row_key.value)
