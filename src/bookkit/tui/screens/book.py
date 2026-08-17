"""Book — the whole client list: sortable, filterable, enter opens the account."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from datetime import date

from rapidfuzz import fuzz
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static

from ...repo import interactions, orgs
from ...repo import placements as placements_repo
from ...services import renewals
from .. import theme
from ..theme import dash, date_text, days_text, money_text, right, status_text
from ..widgets.tables import ListTable


class BookScreen(Screen):
    app: BookkitApp
    DEFAULT_CSS = """
    BookScreen #book-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "new_account", "New account"),
        Binding("e", "edit_account", "Edit"),
        Binding("f", "focus_filter", "Filter"),
        Binding("u", "undo", "Undo"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Input(placeholder="filter by name / owner / status …", id="book-filter")
            yield ListTable(id="book-table")
            yield Static(id="book-hint")
        yield Footer()

    # the filter is remembered per screen under this key (review F21)
    FILTER_SETTING = "book.filter"

    def on_mount(self) -> None:
        from ...repo import settings

        # a filter you retype every morning is a filter the app should have
        # kept: settings is already a KV store, so this costs one read
        saved = str(settings.get(self.app.conn, self.FILTER_SETTING) or "")
        if saved:
            self.query_one("#book-filter", Input).value = saved
        self.refresh_data(saved)
        table = self.query_one("#book-table", ListTable)
        table.focus()
        self._render_hint(saved)
        if table.row_count == 0 and not saved:
            self.notify("empty book — press a to create your first account")

    def _render_hint(self, filter_text: str) -> None:
        """Say when a filter is on: silently hiding most of the book is the
        kind of state that has to be visible."""
        keys = (
            "[b]a[/b] add · [b]e[/b] edit · [b]f[/b] filter · "
            "[b]Y[/b] copy · [b]enter[/b] opens"
        )
        if filter_text.strip():
            from rich.markup import escape

            keys = (
                f"[{theme.AMBER}]filtered by '{escape(filter_text.strip())}'[/] — "
                f"[b]f[/b] to change, empty it to clear · {keys}"
            )
        self.query_one("#book-hint", Static).update(f"[{theme.DIM}]{keys}[/]")

    def refresh_data(self, filter_text: str = "") -> None:
        conn = self.app.conn
        today = date.today()
        table = self.query_one("#book-table", ListTable)
        table.clear(columns=True)
        table.add_columns(
            "ref", "account", "owner", "status", "renews",
            right("days"), right("bound"), "last touch",
        )
        for org in orgs.list_orgs(conn, kind="client"):
            if filter_text and not _matches(org, filter_text):
                continue
            nxt_item = renewals.next_for_org(conn, org.id, today)
            last = interactions.last_for_org(conn, org.id)
            # the ACCOUNT's bound premium, not whichever placement renews
            # next: that printed one placement's number as the whole account's
            # and mixed bound with unbound, so an account with $15.6M across
            # two bound placements read $8M and one with nothing bound read
            # $900K — neither reconcilable with the navigator's bound-only
            # headline. Same rule as the account header.
            bound = [
                p for p in placements_repo.for_org(conn, org.id)
                if p.status == "bound"
            ]
            premium_cell: Text = money_text(
                sum(p.total_premium or 0 for p in bound) if bound else None
            )
            if nxt_item is None:
                renewal_cell: Text = dash()
                days_cell: Text = Text("—", style=theme.DIM, justify="right")
            else:
                renewal_cell = date_text(
                    nxt_item.renewal_on or nxt_item.placement.period_to,
                    nxt_item.days_remaining,
                )
                days_cell = days_text(nxt_item.days_remaining)
            table.add_row(
                Text(org.ref, style=theme.DIM),
                org.name,
                org.owner or dash(),
                status_text(org.status),
                renewal_cell,
                days_cell,
                premium_cell,
                last.occurred_on if last else Text("never", style=theme.DIM),
                key=org.id,
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "book-filter":
            from ...repo import settings

            self.refresh_data(event.value)
            self._render_hint(event.value)
            settings.set_value(self.app.conn, self.FILTER_SETTING, event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "book-filter":
            self.query_one("#book-table", ListTable).focus()

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        if event.row_key.value:
            self.app.open_account(event.row_key.value)

    def _selected_org_id(self) -> str | None:
        table = self.query_one("#book-table", ListTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        from textual.coordinate import Coordinate

        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    def action_new_account(self) -> None:
        from ...forms.entities import apply_org, org_form
        from ..widgets.forms import FormModal

        def commit(values: dict) -> str | None:
            org = apply_org(self.app.conn, values)
            self.notify(f"created {org.ref} {org.name}")
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data(self.query_one("#book-filter", Input).value)

        self.app.push_screen(
            FormModal(org_form(conn=self.app.conn), commit=commit), done
        )

    def action_edit_account(self) -> None:
        from ...forms.entities import apply_org, org_form_initial_profile
        from ...repo import orgs
        from ..widgets.forms import FormModal

        org_id = self._selected_org_id()
        if org_id is None:
            return
        existing = orgs.get(self.app.conn, org_id)

        def commit(values: dict) -> str | None:
            apply_org(self.app.conn, values, existing)
            self.notify(f"updated {existing.ref}")
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data(self.query_one("#book-filter", Input).value)

        self.app.push_screen(
            FormModal(org_form_initial_profile(self.app.conn, existing), commit=commit),
            done,
        )

    def action_focus_filter(self) -> None:
        self.query_one("#book-filter", Input).focus()

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data(self.query_one("#book-filter", Input).value)


def _matches(org, text: str) -> bool:
    hay = " ".join(filter(None, (org.name, org.owner, org.status, org.industry)))
    return fuzz.partial_ratio(text.lower(), hay.lower()) >= 70
