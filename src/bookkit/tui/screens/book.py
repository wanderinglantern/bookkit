"""Book — the whole client list: sortable, filterable, enter opens the account."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from datetime import date

from rapidfuzz import fuzz
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input

from ...dates import days_until
from ...money import format_cents_compact
from ...repo import interactions, orgs, placements
from ..widgets.tables import ListTable


class BookScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("f", "focus_filter", "Filter"),
        Binding("u", "undo", "Undo"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Input(placeholder="filter by name / owner / status …", id="book-filter")
            yield ListTable(id="book-table")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.query_one("#book-table", ListTable).focus()

    def refresh_data(self, filter_text: str = "") -> None:
        conn = self.app.conn
        today = date.today()
        table = self.query_one("#book-table", ListTable)
        table.clear(columns=True)
        table.add_columns(
            "ref", "account", "owner", "status", "next renewal", "days", "premium", "last touch"
        )
        for org in orgs.list_orgs(conn, kind="client"):
            if filter_text and not _matches(org, filter_text):
                continue
            nxt = placements.next_renewal_for_org(conn, org.id, today.isoformat())
            last = interactions.last_for_org(conn, org.id)
            table.add_row(
                org.ref,
                org.name,
                org.owner or "—",
                org.status,
                nxt.period_to if nxt else "—",
                str(days_until(nxt.period_to, today)) if nxt else "—",
                format_cents_compact(nxt.total_premium) if nxt and nxt.total_premium else "—",
                last.occurred_on if last else "never",
                key=org.id,
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "book-filter":
            self.refresh_data(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "book-filter":
            self.query_one("#book-table", ListTable).focus()

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        if event.row_key.value:
            self.app.open_account(event.row_key.value)

    def action_focus_filter(self) -> None:
        self.query_one("#book-filter", Input).focus()

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data(self.query_one("#book-filter", Input).value)


def _matches(org, text: str) -> bool:
    hay = " ".join(filter(None, (org.name, org.owner, org.status, org.industry)))
    return fuzz.partial_ratio(text.lower(), hay.lower()) >= 70
