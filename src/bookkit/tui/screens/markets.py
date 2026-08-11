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
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

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


class MarketDetailScreen(Screen):
    app: BookkitApp
    """One market before a meeting: appetite, underwriters, live submissions,
    and every tower it sits on in the next 90 days."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def __init__(self, market_org_id: str) -> None:
        super().__init__()
        self.market_org_id = market_org_id

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
        table.add_columns("account", "expiry", "program", "layer", "share", "premium")
        for row in exposure.carrier_exposure(conn, market.name, days=90):
            table.add_row(
                row.org_name, row.period_to, row.program_name, row.layer_name,
                f"{row.share_bps / 100:g}%",
                format_cents_compact(row.premium) if row.premium else "—",
                key=row.org_id,
            )

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        if event.data_table.id == "md-exposure" and event.row_key.value:
            self.app.open_account(event.row_key.value)
