"""Markets — carrier list with appetite, submissions, hit rate; selecting one
shows every account it currently sits on (the reverse of the account view)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from ...dates import days_until
from ...repo import contacts, orgs, submissions
from ...services import exposure, hit_rate
from .. import theme
from ..theme import dash, date_text, days_text, money_text, right, status_text
from ..widgets.tables import ListTable

# appetite vocabulary → state colors, same grammar as theme.STATUS_STYLES
# (candidates to move there: target/will_consider/selective/no)
APPETITE_STYLES: dict[str, str] = {
    "target": theme.GREEN,
    "will_consider": theme.BLUE,
    "selective": theme.AMBER,
    "no": theme.RED,
}


def _appetite_text(value: str) -> Text:
    """Color + the word itself, snake_case prettified ('will consider')."""
    return Text(value.replace("_", " "), style=APPETITE_STYLES.get(value, theme.FG))


def _pretty(value: str | None) -> Text:
    return Text(value.replace("_", " ")) if value else dash()


class MarketsScreen(Screen):
    app: BookkitApp

    DEFAULT_CSS = """
    MarketsScreen #markets-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "new_market", "New market"),
        Binding("e", "edit_market", "Edit"),
        Binding("x", "merge_market", "Merge duplicate"),
        Binding("N", "nest_market", "Nest under…", show=False),
        Binding("A", "add_alias", "Alias", show=False),
    ]

    def action_nest_market(self) -> None:
        """Nest the selected market under a master company — creating the
        master on the spot when it doesn't exist yet (the AXA XL case).
        Organizational only: aliases and towers keep the issuing entity."""
        from ..widgets.forms import Field, FormModal, FormSpec
        from ..widgets.picker import Picker

        market_id = self._selected_market_id()
        if market_id is None:
            return
        conn = self.app.conn
        market = orgs.get(conn, market_id)
        options: list[tuple[str, str]] = [
            ("— no parent (unnest) —", "__none__"),
            ("create new master…", "__new__"),
        ]
        for candidate in orgs.list_orgs(conn, kind="market"):
            if candidate.id != market_id:
                options.append((candidate.name, candidate.id))

        def picked(choice: str | None) -> None:
            if choice is None:
                return
            if choice == "__new__":
                from ...repo import vocab

                spec = FormSpec(
                    f"new master company for {market.name}",
                    [Field("name", "master company name", required=True,
                           suggestions=tuple(vocab.market_names(conn)))],
                )

                def commit(values: dict) -> str | None:
                    master = orgs.create(
                        conn, kind="market", name=values["name"], status="active"
                    )
                    orgs.set_parent(conn, market_id, master.id)
                    return None

                def done(values: dict | None) -> None:
                    if values is not None:
                        self.notify(f"{market.name} nested under {values['name']}")
                        self._refresh()

                self.app.push_screen(FormModal(spec, commit=commit), done)
                return
            try:
                parent = None if choice == "__none__" else choice
                orgs.set_parent(conn, market_id, parent)
            except ValueError as exc:
                self.notify(str(exc), severity="error")
                return
            label = "unnested" if parent is None else f"nested under {orgs.get(conn, parent).name}"
            self.notify(f"{market.name} {label}")
            self._refresh()

        self.app.push_screen(Picker(f"nest {market.name} under…", options), picked)

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListTable(id="markets-table")
        yield Static(
            f"[{theme.DIM}][b]enter[/b] opens market · [b]a[/b] add · [b]e[/b] edit · "
            f"[b]N[/b] nest under master · [b]x[/b] merge duplicate · "
            f"[b]A[/b] alias[/]",
            id="markets-hint",
        )
        yield Footer()

    def on_mount(self) -> None:
        conn = self.app.conn
        rates = {r.market_org_id: r for r in hit_rate.by_market(conn)}
        table = self.query_one("#markets-table", ListTable)
        table.add_columns(
            "market", "type", "rating", "target lines",
            right("subs out"), right("quote rate"), right("bind rate"),
        )
        def add(org, depth: int) -> None:
            profile = orgs.get_market_profile(conn, org.id)
            appetite = orgs.appetite_for_market(conn, org.id)
            targets = [a.line.replace("_", " ") for a in appetite if a.appetite == "target"]
            rate = rates.get(org.id)
            out = len(submissions.for_market(conn, org.id, status="out"))
            name = (
                Text.assemble(("  └ ", theme.DIM), org.name) if depth else Text(org.name)
            )
            table.add_row(
                name,
                _pretty(profile.market_type if profile else None),
                Text(profile.am_best_rating) if profile and profile.am_best_rating else dash(),
                Text(", ".join(targets), style=theme.GREEN) if targets else dash(),
                Text(str(out), justify="right", style=theme.BLUE if out else theme.DIM),
                Text(f"{rate.quote_rate:.0%}", justify="right")
                if rate else Text("—", style=theme.DIM, justify="right"),
                Text(f"{rate.bind_rate:.0%}", justify="right")
                if rate else Text("—", style=theme.DIM, justify="right"),
                key=org.id,
            )

        # outline: master companies first, issuing companies nested beneath
        for master, kids in orgs.market_families(conn):
            add(master, 0)
            for kid in kids:
                add(kid, 1)
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

        def commit(values: dict) -> str | None:
            org = apply_org(self.app.conn, values)
            self.notify(f"created {org.name}")
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self._refresh()

        self.app.push_screen(
            FormModal(org_form(default_kind="market", conn=self.app.conn), commit=commit), done
        )

    def action_merge_market(self) -> None:
        """Fold a duplicate market ('Axa XL') into the real one ('AXA XL');
        the duplicate's name becomes an alias so towers keep resolving."""
        from ...services.merge import MergeError, merge_markets
        from ..widgets.picker import Picker

        source_id = self._selected_market_id()
        if source_id is None:
            return
        source = orgs.get(self.app.conn, source_id)
        targets = [
            o for o in orgs.list_orgs(self.app.conn, kind="market") if o.id != source_id
        ]
        if not targets:
            return

        def picked(target_id: str | None) -> None:
            if target_id is None:
                return
            try:
                result = merge_markets(self.app.conn, source_id, target_id)
            except MergeError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(
                f"merged into {result.target.name} — {result.alias_added!r} is now an "
                f"alias ({result.moved_contacts} contacts, {result.moved_submissions} "
                "submissions moved)"
            )
            self._refresh()

        self.app.push_screen(
            Picker(f"merge {source.name} into which market?",
                   [(o.name, o.id) for o in targets]),
            picked,
        )

    def action_add_alias(self) -> None:
        """Record another tower spelling for the selected market."""
        from ...repo import aliases
        from ..widgets.forms import Field, FormModal, FormSpec

        market_id = self._selected_market_id()
        if market_id is None:
            return
        market = orgs.get(self.app.conn, market_id)

        def commit(values: dict) -> str | None:
            aliases.set_alias(self.app.conn, values["alias"], market_id)
            self.notify(f"{values['alias']!r} now resolves to {market.name}")
            return None

        unresolved = tuple(aliases.unresolved_carriers(self.app.conn))
        spec = FormSpec(
            f"alias for {market.name}",
            [Field("alias", "tower spelling", required=True,
                   placeholder="exactly as the file writes it",
                   suggestions=unresolved)],
        )
        self.app.push_screen(FormModal(spec, commit=commit))

    def action_edit_market(self) -> None:
        from ..widgets.entity_forms import apply_org, org_form_initial_profile
        from ..widgets.forms import FormModal

        market_id = self._selected_market_id()
        if market_id is None:
            return
        existing = orgs.get(self.app.conn, market_id)

        def commit(values: dict) -> str | None:
            apply_org(self.app.conn, values, existing)
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self._refresh()

        self.app.push_screen(
            FormModal(org_form_initial_profile(self.app.conn, existing), commit=commit),
            done,
        )


class MarketDetailScreen(Screen):
    app: BookkitApp
    """One market before a meeting: appetite, underwriters, live submissions,
    and every tower it sits on in the next 90 days."""

    DEFAULT_CSS = """
    MarketDetailScreen #market-header {
        height: auto;
        padding: 0 1;
    }
    MarketDetailScreen #md-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    MarketDetailScreen .pane-title {
        color: $text-muted;
        text-style: bold;
        padding: 0 1;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add_appetite", "Add appetite"),
        Binding("w", "add_underwriter", "Add underwriter"),
        Binding("i", "import_underwriter", "Import (paste sig)"),
    ]

    def action_import_underwriter(self) -> None:
        """Paste an underwriter's email signature — same parser as contact
        capture, defaulting the role to underwriter under this market."""
        from ...imports.commit import commit_contact_paste
        from ...imports.mappers.contact_paste import stage_contact_paste
        from ..widgets.paste_import import PasteImportModal

        conn = self.app.conn
        org = orgs.get(conn, self.market_org_id)

        def stage(text: str):
            staged = stage_contact_paste(conn, text, org.id, org.name)
            for record in staged.records:
                if record.kind == "contact":
                    record.fields.setdefault("role", "underwriter")
            return staged

        def commit(staged) -> str:
            commit_contact_paste(conn, staged, org.id, self.app.db_file())
            self._refresh()
            return f"underwriter captured for {org.name}"

        self.app.push_screen(
            PasteImportModal(f"paste underwriter signature — {org.name}", stage, commit)
        )

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

        def commit(values: dict) -> str | None:
            orgs.add_appetite(self.app.conn, self.market_org_id, **dropped(values))
            self.notify("appetite recorded")
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self._refresh()

        self.app.push_screen(
            FormModal(appetite_form(conn=self.app.conn), commit=commit), done
        )

    def action_add_underwriter(self) -> None:
        from ..widgets.entity_forms import apply_contact, contact_form
        from ..widgets.forms import FormModal

        def commit(values: dict) -> str | None:
            values["role"] = values.get("role") or "underwriter"
            contact = apply_contact(self.app.conn, self.market_org_id, values)
            self.notify(f"added {contact.name}")
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self._refresh()

        self.app.push_screen(FormModal(contact_form(), commit=commit), done)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(id="market-header")
            yield Static(
                f"[{theme.DIM}][b]a[/b] add appetite · [b]w[/b] add underwriter · "
                f"[b]i[/b] paste signature · [b]enter[/b] on a tower row opens the "
                f"account[/]",
                id="md-hint",
            )
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
        # dossier layout: each table hugs its rows so the page reads top to
        # bottom (inline styles — the app-wide `DataTable {height: 1fr}` wins
        # the cascade over this screen's DEFAULT_CSS)
        for table in self.query(ListTable):
            table.styles.height = "auto"
            table.styles.max_height = 14
        conn = self.app.conn
        market = orgs.get(conn, self.market_org_id)
        profile = orgs.get_market_profile(conn, market.id)
        kind = (
            profile.market_type.replace("_", " ")
            if profile and profile.market_type else "market"
        )
        rating_text = (
            f"   [b]AM Best {profile.am_best_rating}[/b]"
            if profile and profile.am_best_rating else ""
        )
        rate = next(
            (r for r in hit_rate.by_market(conn) if r.market_org_id == market.id), None
        )
        rate_text = (
            f"   quote {rate.quote_rate:.0%} · bind {rate.bind_rate:.0%}"
            f" [{theme.DIM}]({rate.sent} sent)[/]" if rate else ""
        )
        from ...repo import aliases

        known = aliases.for_market(conn, market.id)
        alias_text = (
            f"\n[{theme.DIM}]also written as: {', '.join(known)}[/]" if known else ""
        )
        self.query_one("#market-header", Static).update(
            f"[b]{market.name}[/b]  [{theme.DIM}]({kind})[/]"
            f"{rating_text}{rate_text}{alias_text}"
        )

        table = self.query_one("#md-appetite", ListTable)
        table.add_columns(
            "line", "appetite", right("min premium"), right("max limit"), "territories"
        )
        for a in orgs.appetite_for_market(conn, market.id):
            table.add_row(
                _pretty(a.line), _appetite_text(a.appetite),
                money_text(a.min_premium), money_text(a.max_limit),
                a.territories or dash(),
            )

        table = self.query_one("#md-contacts", ListTable)
        table.add_columns("name", "title", "email", "phone")
        for c in contacts.for_org(conn, market.id):
            table.add_row(
                c.name, c.title or dash(), c.email or dash(),
                c.phone or c.mobile or dash(),
            )

        table = self.query_one("#md-subs", ListTable)
        table.add_columns("sent", "status", right("quoted"), "response")
        for s in submissions.for_market(conn, market.id):
            table.add_row(
                s.sent_on, status_text(s.status),
                money_text(s.quoted_premium),
                s.response_on or dash(),
            )

        table = self.query_one("#md-exposure", ListTable)
        table.add_columns(
            "account", "expiry", right("due in"), "program", "layer",
            "as written", right("share"), right("premium"),
        )
        for row in exposure.for_market(conn, market.id, days=90):
            days = days_until(row.period_to)
            table.add_row(
                row.org_name, date_text(row.period_to, days), days_text(days),
                row.program_name, row.layer_name,
                row.carrier if row.carrier != market.name else dash(),
                Text(f"{row.share_bps / 100:g}%", justify="right"),
                money_text(row.premium),
                key=row.org_id,
            )

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        if event.data_table.id == "md-exposure" and event.row_key.value:
            self.app.open_account(event.row_key.value)
