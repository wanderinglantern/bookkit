"""Markets — carrier list with appetite, submissions, hit rate; selecting one
shows every account it currently sits on (the reverse of the account view)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from ...money import format_cents_compact
from ...repo import contacts, orgs, submissions
from ...services import exposure, hit_rate
from ..widgets.tables import ListTable


class MarketsScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "new_market", "New market"),
        Binding("e", "edit_market", "Edit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListTable(id="markets-table")
        yield Footer()

    def on_mount(self) -> None:
        conn = self.app.conn
        rates = {r.market_org_id: r for r in hit_rate.by_market(conn)}
        table = self.query_one("#markets-table", ListTable)
        table.add_columns(
            "market", "type", "rating", "appetite", "subs out", "quote rate", "bind rate"
        )
        for org in orgs.list_orgs(conn, kind="market"):
            profile = orgs.get_market_profile(conn, org.id)
            appetite = orgs.appetite_for_market(conn, org.id)
            targets = [a.line for a in appetite if a.appetite == "target"]
            rate = rates.get(org.id)
            out = len(submissions.for_market(conn, org.id, status="out"))
            table.add_row(
                org.name,
                profile.market_type if profile and profile.market_type else "—",
                profile.am_best_rating if profile and profile.am_best_rating else "—",
                ", ".join(targets) or "—",
                str(out),
                f"{rate.quote_rate:.0%}" if rate else "—",
                f"{rate.bind_rate:.0%}" if rate else "—",
                key=org.id,
            )
        table.focus()

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        if event.row_key.value:
            self.app.push_screen(MarketDetailScreen(event.row_key.value))

    def _refresh(self) -> None:
        table = self.query_one("#markets-table", ListTable)
        table.clear(columns=True)
        self.on_mount()

    def _selected_market_id(self) -> str | None:
        from textual.coordinate import Coordinate

        table = self.query_one("#markets-table", ListTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    def action_new_market(self) -> None:
        from ..widgets.entity_forms import apply_org, org_form
        from ..widgets.forms import FormModal

        def saved(values: dict | None) -> None:
            if values is not None:
                org = apply_org(self.app.conn, values)
                self.notify(f"created {org.name}")
                self._refresh()

        self.app.push_screen(FormModal(org_form(default_kind="market")), saved)

    def action_edit_market(self) -> None:
        from ..widgets.entity_forms import apply_org, org_form_initial_profile
        from ..widgets.forms import FormModal

        market_id = self._selected_market_id()
        if market_id is None:
            return
        existing = orgs.get(self.app.conn, market_id)

        def saved(values: dict | None) -> None:
            if values is not None:
                apply_org(self.app.conn, values, existing)
                self._refresh()

        self.app.push_screen(
            FormModal(org_form_initial_profile(self.app.conn, existing)), saved
        )


class MarketDetailScreen(Screen):
    app: BookkitApp
    """One market before a meeting: appetite, underwriters, live submissions,
    and every tower it sits on in the next 90 days."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add_appetite", "Add appetite"),
        Binding("w", "add_underwriter", "Add underwriter"),
    ]

    def __init__(self, market_org_id: str) -> None:
        super().__init__()
        self.market_org_id = market_org_id

    def _refresh(self) -> None:
        for table in self.query(ListTable):
            table.clear(columns=True)
        self.on_mount()

    def action_add_appetite(self) -> None:
        from ..widgets.entity_forms import appetite_form
        from ..widgets.forms import FormModal, dropped

        def saved(values: dict | None) -> None:
            if values is not None:
                orgs.add_appetite(self.app.conn, self.market_org_id, **dropped(values))
                self.notify("appetite recorded")
                self._refresh()

        self.app.push_screen(FormModal(appetite_form()), saved)

    def action_add_underwriter(self) -> None:
        from ..widgets.entity_forms import apply_contact, contact_form
        from ..widgets.forms import FormModal

        def saved(values: dict | None) -> None:
            if values is not None:
                values["role"] = values.get("role") or "underwriter"
                contact = apply_contact(self.app.conn, self.market_org_id, values)
                self.notify(f"added {contact.name}")
                self._refresh()

        self.app.push_screen(FormModal(contact_form()), saved)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(id="market-header")
            yield Static("APPETITE", classes="pane-title")
            yield ListTable(id="md-appetite")
            yield Static("UNDERWRITERS", classes="pane-title")
            yield ListTable(id="md-contacts")
            yield Static("CURRENT SUBMISSIONS", classes="pane-title")
            yield ListTable(id="md-subs")
            yield Static("ON THE TOWER — RENEWING NEXT 90 DAYS", classes="pane-title")
            yield ListTable(id="md-exposure")
        yield Footer()

    def on_mount(self) -> None:
        conn = self.app.conn
        market = orgs.get(conn, self.market_org_id)
        rate = next(
            (r for r in hit_rate.by_market(conn) if r.market_org_id == market.id), None
        )
        rate_text = (
            f"   quote {rate.quote_rate:.0%} · bind {rate.bind_rate:.0%}"
            f" ({rate.sent} sent)" if rate else ""
        )
        self.query_one("#market-header", Static).update(f"[b]{market.name}[/b]{rate_text}")

        table = self.query_one("#md-appetite", ListTable)
        table.add_columns("line", "appetite", "min premium", "max limit", "territories")
        for a in orgs.appetite_for_market(conn, market.id):
            table.add_row(
                a.line, a.appetite,
                format_cents_compact(a.min_premium) if a.min_premium else "—",
                format_cents_compact(a.max_limit) if a.max_limit else "—",
                a.territories or "—",
            )

        table = self.query_one("#md-contacts", ListTable)
        table.add_columns("name", "title", "email", "phone")
        for c in contacts.for_org(conn, market.id):
            table.add_row(c.name, c.title or "—", c.email or "—", c.phone or c.mobile or "—")

        table = self.query_one("#md-subs", ListTable)
        table.add_columns("sent", "status", "quoted", "response")
        for s in submissions.for_market(conn, market.id):
            table.add_row(
                s.sent_on, s.status,
                format_cents_compact(s.quoted_premium) if s.quoted_premium else "—",
                s.response_on or "—",
            )

        table = self.query_one("#md-exposure", ListTable)
        table.add_columns("account", "expiry", "program", "layer", "as written", "share", "premium")
        for row in exposure.for_market(conn, market.id, days=90):
            table.add_row(
                row.org_name, row.period_to, row.program_name, row.layer_name,
                row.carrier if row.carrier != market.name else "",
                f"{row.share_bps / 100:g}%",
                format_cents_compact(row.premium) if row.premium else "—",
                key=row.org_id,
            )

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        if event.data_table.id == "md-exposure" and event.row_key.value:
            self.app.open_account(event.row_key.value)
