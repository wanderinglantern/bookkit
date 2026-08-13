"""Account detail — where most time is spent. Header + tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

import subprocess
from datetime import date
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from ...dates import days_until
from ...money import format_cents_compact
from ...repo import (
    contacts,
    documents,
    interactions,
    opportunities,
    orgs,
    placements,
    projection,
    submissions,
)
from ...repo import tasks as tasks_repo
from .. import theme
from ..theme import dash, date_text, days_text, money_text, right
from ..widgets.tables import ListTable, grouped_by_category, task_detail_cell
from ..widgets.tower_preview import TowerPreview


def _pretty(value: str) -> str:
    """snake_case vocab reads as words in cells ('site_visit' → 'site visit')."""
    return value.replace("_", " ")


def _status(value: str) -> Text:
    """status_text, plus the snake_case prettify — style keyed on the raw value."""
    return Text(_pretty(value), style=theme.STATUS_STYLES.get(value, theme.FG))


# the visible home for each tab's hidden single-letter keys — only the keys
# that actually work on that tab (help lists them too)
TAB_HINTS: dict[str, str] = {
    "tab-overview": (
        "[b]a[/b] task · [b]e[/b] edit task/account · [b]d[/b] task done · "
        "[b]w[/b] assign team · [b]i[/b] paste import · [b]u[/b] undo"
    ),
    "tab-contacts": (
        "[b]a[/b] add · [b]e[/b] edit · [b]p[/b] make primary · "
        "[b]i[/b] paste import · [b]w[/b] assign team"
    ),
    "tab-interactions": (
        "[b]a[/b] log interaction · [b]e[/b] edit account · [b]i[/b] paste import"
    ),
    "tab-placements": (
        "[b]a[/b] add · [b]e[/b] edit · [b]s[/b] submission · [b]r[/b] renew · "
        "[b]l[/b]/[b]L[/b] layer · [b]o[/b] towerkit · [b]t[/b] tower file · "
        "[b]i[/b] paste · [b]w[/b] assign · [b]x[/b] merge"
    ),
    "tab-projects": (
        "[b]a[/b] add project/need · [b]e[/b] edit · "
        "[b]o[/b] need → opportunity · [b]tab[/b] projects ⇄ needs"
    ),
    "tab-pipeline": (
        "[b]a[/b] opportunity · [b]e[/b] edit opp / record response · "
        "[b]s[/b] submission · [b]tab[/b] opps ⇄ submissions"
    ),
    "tab-documents": "[b]a[/b] add · [b]enter[/b] opens the file · [b]e[/b] edit account",
}

# where the cursor lands when a tab opens — j/k and the row keys work at once
TAB_TABLES: dict[str, str] = {
    "tab-overview": "ov-tasks",
    "tab-contacts": "contacts-table",
    "tab-interactions": "interactions-table",
    "tab-placements": "placements-table",
    "tab-projects": "projects-table",
    "tab-pipeline": "pipeline-opps",
    "tab-documents": "documents-table",
}


class ConfirmRenew(ModalScreen):
    """One look before a renew: what gets created, including the cloned file."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y,enter", "accept", "Yes"),
    ]

    def __init__(self, placement) -> None:
        super().__init__()
        self.placement = placement

    def compose(self) -> ComposeResult:
        p = self.placement
        file_note = (
            f"\n+ clone {Path(p.program_path).name} to next year's file, linked at birth"
            if p.program_path
            else ""
        )
        with VerticalScroll(classes="modal-box"):
            yield Static("RENEW PLACEMENT", classes="modal-title")
            yield Static(
                f"{p.ref} {p.program_name}\ncreate the {p.period_to} → next-year period "
                f"as prospective{file_note}"
            )
            yield Static("y / enter renew · n / esc cancel", classes="hint")

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)


class MergePicker(ModalScreen):
    """Pick which placement a duplicate merges into."""

    app: BookkitApp
    BINDINGS = [Binding("escape", "decline", "Cancel")]

    def __init__(self, source, targets) -> None:
        super().__init__()
        self.source = source
        self.targets = targets

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("MERGE PLACEMENT", classes="modal-title")
            yield Static(
                f"merge [b]{self.source.ref} {self.source.program_name}[/b] "
                f"({self.source.period_from}→{self.source.period_to}) into:"
            )
            yield OptionList(id="merge-targets")
            yield Static("enter merges · esc cancels · undoable with u", classes="hint")

    def on_mount(self) -> None:
        option_list = self.query_one("#merge-targets", OptionList)
        for p in self.targets:
            linked = " · file-linked" if p.program_path else ""
            option_list.add_option(
                Option(
                    f"{p.ref}  {p.program_name}  {p.period_from}→{p.period_to}"
                    f"  [{p.status}]{linked}",
                    id=p.id,
                )
            )
        option_list.focus()
        option_list.highlighted = 0

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_decline(self) -> None:
        self.dismiss(None)


class AccountScreen(Screen):
    app: BookkitApp

    # screen-local polish only; anything bookkit.tcss already styles is set
    # inline in on_mount instead (shared-file rules would win over these)
    DEFAULT_CSS = """
    AccountScreen #tab-hint {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $panel;
    }
    AccountScreen .pane-title {
        color: $text-muted;
        text-style: bold;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add_here", "Add (this tab)"),
        Binding("e", "edit_here", "Edit"),
        Binding("s", "new_submission", "Submission"),
        Binding("r", "renew_placement", "Renew"),
        Binding("l", "edit_layer", "Layer"),
        Binding("L", "add_layer", "Add layer", show=False),
        Binding("o", "open_towerkit", "Open in towerkit"),
        Binding("w", "assign_team", "Assign team", show=False),
        Binding("t", "scaffold_tower", "Tower file", show=False),
        Binding("x", "merge_placement", "Merge", show=False),
        Binding("i", "import_here", "Import (paste)"),
        Binding("d", "task_done", "Done (task)", show=False),
        Binding("p", "mark_primary", "Primary (contact)", show=False),
        Binding("u", "undo", "Undo"),
        # tabs answer to their number — no reaching for the tab bar
        Binding("1", "show_tab('tab-overview')", "Overview", show=False),
        Binding("2", "show_tab('tab-contacts')", "Contacts", show=False),
        Binding("3", "show_tab('tab-interactions')", "Interactions", show=False),
        Binding("4", "show_tab('tab-placements')", "Placements", show=False),
        Binding("5", "show_tab('tab-projects')", "Projects", show=False),
        Binding("6", "show_tab('tab-pipeline')", "Pipeline", show=False),
        Binding("7", "show_tab('tab-documents')", "Documents", show=False),
    ]

    def __init__(self, org_id: str) -> None:
        super().__init__()
        self.current_org_id = org_id

    def compose(self) -> ComposeResult:
        yield Static(id="account-header")
        with TabbedContent():
            with TabPane("1 Overview", id="tab-overview"):
                with VerticalScroll():
                    yield Static("TEAM", classes="pane-title")
                    yield ListTable(id="ov-team")
                    yield Static("KEY CONTACTS", classes="pane-title")
                    yield ListTable(id="ov-contacts")
                    yield Static("RECENT INTERACTIONS", classes="pane-title")
                    yield ListTable(id="ov-interactions")
                    yield Static("OPEN TASKS", classes="pane-title")
                    yield ListTable(id="ov-tasks")
                    yield Static("OPEN OPPORTUNITIES", classes="pane-title")
                    yield ListTable(id="ov-opps")
            with TabPane("2 Contacts", id="tab-contacts"):
                yield ListTable(id="contacts-table")
            with TabPane("3 Interactions", id="tab-interactions"):
                yield ListTable(id="interactions-table")
            with TabPane("4 Placements", id="tab-placements"):
                with Horizontal():
                    with Vertical(id="placement-side"):
                        yield ListTable(id="placements-table")
                        yield ListTable(id="carriers-table")
                        yield Static(id="sync-state")
                    yield TowerPreview(id="tower-preview")
            with TabPane("5 Projects", id="tab-projects"):
                with Vertical():
                    yield ListTable(id="projects-table")
                    yield ListTable(id="needs-table")
            with TabPane("6 Pipeline", id="tab-pipeline"):
                with VerticalScroll():
                    yield Static("OPPORTUNITIES", classes="pane-title")
                    yield ListTable(id="pipeline-opps")
                    yield Static("SUBMISSIONS", classes="pane-title")
                    yield ListTable(id="pipeline-subs")
            with TabPane("7 Documents", id="tab-documents"):
                yield ListTable(id="documents-table")
        yield Static(id="tab-hint")
        yield Footer()

    def on_mount(self) -> None:
        # bookkit.tcss (shared, off-limits here) styles #account-header for the
        # old two-line layout; inline styles win, making it a 1-line context bar
        bar = self.query_one("#account-header", Static)
        bar.styles.height = 1
        bar.styles.padding = (0, 2)
        bar.styles.border_bottom = ("none", "black")
        # the placements table needs room for status + premium; the shared
        # 44% width crops them, and the tower preview scrolls anyway
        self.query_one("#placement-side").styles.width = "52%"
        # stacked tables inside scrolling panes size to their rows instead of
        # splitting the viewport into slivers
        for table_id in ("ov-team", "ov-contacts", "ov-interactions", "ov-tasks",
                         "ov-opps", "pipeline-opps", "pipeline-subs"):
            self.query_one(f"#{table_id}", ListTable).styles.height = "auto"
        self.refresh_data()
        self._render_tab_hint()
        self._focus_tab_table()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        conn = self.app.conn
        today = date.today()
        org = orgs.get(conn, self.current_org_id)

        from ...services import renewals as renewals_service

        nxt_item = renewals_service.next_for_org(conn, org.id, today)
        if nxt_item is None:
            renewal = f"renewal [{theme.DIM}]none scheduled[/]"
        elif nxt_item.days_remaining < 0:
            renewal = (
                f"renewal [b {theme.RED}]◆ {nxt_item.placement.period_to} · "
                f"{-nxt_item.days_remaining}d over · "
                f"{nxt_item.placement.program_name}[/]"
            )
        else:
            style = theme.AMBER if nxt_item.days_remaining <= 60 else theme.FG
            renewal = (
                f"renewal [{style}]{nxt_item.placement.period_to} · "
                f"{nxt_item.days_remaining}d[/]"
            )
        bound = [p for p in placements.for_org(conn, org.id) if p.status == "bound"]
        bound_premium = sum(p.total_premium or 0 for p in bound)
        status_style = theme.STATUS_STYLES.get(org.status, theme.FG)
        parts = [
            f"[b {theme.GOLD}]{org.name}[/]  [{theme.DIM}]{org.ref}[/]",
            f"[{status_style}]{_pretty(org.status)}[/]",
            f"owner {org.owner or '—'}",
            renewal,
            f"[{theme.GREEN}]{format_cents_compact(bound_premium)} bound[/]"
            f" [{theme.DIM}]({len(bound)} placements)[/]",
        ]
        self.query_one("#account-header", Static).update(
            f"  [{theme.RULE}]·[/]  ".join(parts)
        )

        team_table = self.query_one("#ov-team", ListTable)
        team_table.clear(columns=True)
        team_table.add_columns("who", "role", "lines", "scope")
        from ...repo import team as team_repo

        for row in team_repo.for_org(conn, org.id):
            scope = (
                f"{row['placement_ref']} {row['program_name']}"
                if row["placement_ref"] else "account"
            )
            team_table.add_row(
                f"{row['member_name']} ({row['specialty'] or row['member_title'] or '—'})",
                _pretty(row["role"]) if row["role"] else dash(),
                row["lines"] or dash(), scope,
                key=row["id"],
            )

        roster = contacts.for_org(conn, org.id)
        for table_id, rows in (("#ov-contacts", roster[:5]), ("#contacts-table", roster)):
            table = self.query_one(table_id, ListTable)
            table.clear(columns=True)
            table.add_columns("", "name", "role", "title", "email", "phone")
            for c in rows:
                table.add_row(
                    Text("★", style=theme.GOLD) if c.is_primary else "",
                    c.name, _pretty(c.role) if c.role else dash(),
                    c.title or dash(), c.email or dash(),
                    c.phone or c.mobile or dash(),
                    key=c.id,
                )

        log = interactions.for_org(conn, org.id)
        for table_id, int_rows in (("#ov-interactions", log[:5]), ("#interactions-table", log)):
            table = self.query_one(table_id, ListTable)
            table.clear(columns=True)
            table.add_columns("date", "type", "subject", "who")
            for i in int_rows:
                who = ", ".join(c.name for c in interactions.attendees(conn, i.id))
                table.add_row(
                    i.occurred_on, Text(_pretty(i.type), style=theme.DIM),
                    i.subject, who, key=i.id,
                )

        open_tasks = grouped_by_category(tasks_repo.open_tasks(conn, org_id=org.id))
        table = self.query_one("#ov-tasks", ListTable)
        table.clear(columns=True)
        table.add_columns(
            "due", right("due in"), "task", "category", "description", "detail"
        )
        for t in open_tasks:
            if t.due_on:
                days = days_until(t.due_on, today)
                due, due_in = date_text(t.due_on, days), days_text(days)
            else:
                due, due_in = dash(), Text("", justify="right")
            table.add_row(
                due, due_in, t.title,
                Text(t.category, style=theme.AMBER) if t.category else dash(),
                t.description or dash(),
                task_detail_cell(t), key=t.id,
            )

        opps = opportunities.for_org(conn, org.id, open_only=True)
        table = self.query_one("#ov-opps", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "title", "stage", right("target"), "close")
        for o in opps:
            table.add_row(
                o.ref, o.title, _status(o.stage),
                money_text(o.target_premium),
                o.target_effective or dash(),
                key=o.id,
            )

        self._refresh_placements(org.id)
        self._refresh_pipeline(org.id)
        self._refresh_projects(org.id)

        docs = documents.for_org(conn, org.id)
        table = self.query_one("#documents-table", ListTable)
        table.clear(columns=True)
        table.add_columns("added", "kind", "title", "path")
        for d in docs:
            table.add_row(
                d.added_at[:10],
                _pretty(d.kind) if d.kind else dash(),
                d.title, Text(d.path, style=theme.DIM), key=d.id,
            )
        self._settle_tables()

    def _settle_tables(self) -> None:
        """Workaround for a DataTable paint quirk: rows added before the first
        idle pass leave the visible tab painted at label-only column widths
        (the width measure runs on idle, but the stale strips are never
        invalidated). Flush the pending measurements now and drop the caches
        so the next paint uses real content widths."""
        for table in self.query(ListTable):
            if not table._require_update_dimensions:
                continue
            table._require_update_dimensions = False
            new_rows = table._new_rows.copy()
            table._new_rows.clear()
            table._update_dimensions(new_rows)
            table._clear_caches()
            table.refresh()

    def _refresh_placements(self, org_id: str) -> None:
        conn = self.app.conn
        table = self.query_one("#placements-table", ListTable)
        table.clear(columns=True)
        table.add_columns(
            "ref", "program", "effective", "expires", right("d"), "status",
            right("premium"),
        )
        # live placements first, soonest expiry on top; already-expired ones
        # sink below (most recently expired first) instead of pinning forever
        rows = sorted(
            placements.for_org(conn, org_id),
            key=lambda p: (days_until(p.period_to) < 0, abs(days_until(p.period_to))),
        )
        for p in rows:
            days = days_until(p.period_to)
            table.add_row(
                p.ref, p.program_name, p.period_from,
                date_text(p.period_to, days), days_text(days), _status(p.status),
                money_text(p.total_premium),
                key=p.id,
            )
        if rows:
            self.show_placement(rows[0].id)
        else:
            self.query_one("#sync-state", Static).update("no placements")
            self.query_one("#tower-preview", TowerPreview).show_placeholder()

    def _refresh_projects(self, org_id: str) -> None:
        from ...repo import projects as projects_repo

        conn = self.app.conn
        table = self.query_one("#projects-table", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "project", "status", "start", "end", right("open needs"))
        rows = projects_repo.projects_for_org(conn, org_id)
        for project in rows:
            open_needs = sum(
                1
                for need in projects_repo.needs_for_project(conn, project.id)
                if need.status in projects_repo.ATTENTION_STATUSES
            )
            table.add_row(
                project.ref, project.name, _status(project.status),
                project.start_on or dash(), project.end_on or dash(),
                Text(str(open_needs), justify="right")
                if open_needs else Text("—", style=theme.DIM, justify="right"),
                key=project.id,
            )
        if rows:
            self.show_project(rows[0].id)
        else:
            needs = self.query_one("#needs-table", ListTable)
            needs.clear(columns=True)
            needs.add_columns(
                "line", "needed by", right("d"), "status", right("limit"), "linked"
            )

    def show_project(self, project_id: str) -> None:
        """Fill the needs table for one project, expiry-style styling on the
        needed-by dates."""
        from ...repo import projects as projects_repo

        table = self.query_one("#needs-table", ListTable)
        table.clear(columns=True)
        table.add_columns(
            "line", "needed by", right("d"), "status", right("limit"), "linked"
        )
        for need in projects_repo.needs_for_project(self.app.conn, project_id):
            days = days_until(need.needed_by)
            if need.status in projects_repo.ATTENTION_STATUSES:
                needed, due_in = date_text(need.needed_by, days), days_text(days)
            else:  # settled needs don't shout about their dates
                needed = Text(need.needed_by, style=theme.DIM)
                due_in = Text(f"{days}d", style=theme.DIM, justify="right")
            linked = (
                "opp" if need.opportunity_id
                else ("plc" if need.placement_id else dash())
            )
            table.add_row(
                need.line, needed, due_in, _status(need.status),
                money_text(need.limit_cents),
                linked,
                key=need.id,
            )
        self._settle_tables()

    def _refresh_pipeline(self, org_id: str) -> None:
        conn = self.app.conn
        table = self.query_one("#pipeline-opps", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "title", "stage", right("target"), right("prob"))
        for o in opportunities.for_org(conn, org_id):
            table.add_row(
                o.ref, o.title, _status(o.stage),
                money_text(o.target_premium),
                Text(f"{o.probability_pct}%", justify="right"),
                key=o.id,
            )
        table = self.query_one("#pipeline-subs", ListTable)
        table.clear(columns=True)
        table.add_columns("market", "sent", "status", right("quoted"), "response")
        for p in placements.for_org(conn, org_id):
            for s in submissions.for_placement(conn, p.id):
                table.add_row(
                    orgs.get(conn, s.market_org_id).name, s.sent_on, _status(s.status),
                    money_text(s.quoted_premium),
                    s.response_on or dash(),
                    key=s.id,
                )
        for o in opportunities.for_org(conn, org_id):
            for s in submissions.for_opportunity(conn, o.id):
                table.add_row(
                    orgs.get(conn, s.market_org_id).name, s.sent_on, _status(s.status),
                    money_text(s.quoted_premium),
                    s.response_on or dash(),
                    key=s.id,
                )

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """The placements pane lays out at zero size until first shown; re-show
        the selection once it has a real size. Every tab lands the cursor on
        its table so j/k and the row keys work with no extra tab-presses."""
        if event.pane.id == "tab-placements":
            key = self._selected_key("placements-table")
            if key:
                self.call_after_refresh(self.show_placement, key)
        self._render_tab_hint()
        # focus synchronously: a deferred focus posts TabPane.Focused late,
        # and TabbedContent would snap `active` back to the pane it names,
        # eating the next 1–7 tab switch
        self._focus_tab_table()

    def action_show_tab(self, tab_id: str) -> None:
        """1–7 jump straight to a tab (and its table)."""
        self.query_one(TabbedContent).active = tab_id

    def _focus_tab_table(self) -> None:
        table_id = TAB_TABLES.get(self._active_tab())
        if table_id:
            self.query_one(f"#{table_id}", ListTable).focus()

    def _render_tab_hint(self) -> None:
        hint = TAB_HINTS.get(self._active_tab(), "")
        self.query_one("#tab-hint", Static).update(f"[{theme.DIM}]{hint}[/]")

    def on_data_table_row_highlighted(self, event: ListTable.RowHighlighted) -> None:
        """j/k on the placements table switches the previewed program live."""
        if event.data_table.id == "placements-table" and event.row_key is not None:
            key = event.row_key.value
            if key:
                self.show_placement(key)
        elif event.data_table.id == "projects-table" and event.row_key is not None:
            key = event.row_key.value
            if key:
                self.show_project(key)

    def show_placement(self, placement_id: str) -> None:
        """Fill the tower preview, carrier list, and sync-state for one placement."""
        conn = self.app.conn
        placement = placements.get(conn, placement_id)
        status_style = theme.STATUS_STYLES.get(placement.status, theme.FG)
        header = (
            f"[b]▸ {placement.ref}  {placement.program_name}[/b]  "
            f"[{status_style}]{placement.status}[/]"
        )
        from ...repo import team as team_repo

        deal_team = [
            f"{row['member_name']} ({row['role'] or row['specialty'] or 'team'})"
            for row in team_repo.for_org(conn, placement.org_id)
            if row["placement_id"] == placement.id
        ]
        if deal_team:  # deal staffing belongs where the deal lives
            header += f"\nteam: {', '.join(deal_team)}"

        carriers = self.query_one("#carriers-table", ListTable)
        carriers.clear(columns=True)
        carriers.add_columns("carrier", "layer", right("share"), right("premium"))
        # rows are keyed "<layer_id>:<n>" so `l` can edit the layer under the
        # cursor; participant-less layers get a placeholder row to stay reachable
        seen_layers: set[str] = set()
        for index, row in enumerate(projection.participants_for_placement(conn, placement_id)):
            seen_layers.add(str(row["layer_id"]))
            carriers.add_row(
                row["carrier"], row["layer_name"],
                Text(f"{row['share_bps'] / 100:g}%", justify="right"),
                money_text(row["premium"]),
                key=f"{row['layer_id']}:{index}",
            )
        from ... import sync as _sync

        for layer in _sync.layer_details(conn, placement_id):
            if str(layer["id"]) not in seen_layers:
                carriers.add_row(
                    Text("— to be placed —", style=theme.DIM), layer["name"], "", "",
                    key=f"{layer['id']}:x",
                )
        self._settle_tables()

        preview = self.query_one("#tower-preview", TowerPreview)
        state = self.query_one("#sync-state", Static)
        if not placement.program_path:
            state.update(f"{header}\n○ no program file linked")
            preview.show_placeholder()
            return
        path = Path(placement.program_path)
        from ... import sync

        if not path.exists():
            state.update(f"{header}\n✗ file missing: {path}")
            preview.show_placeholder()
            return
        if placement.source_sha256 and sync.file_sha256(path) != placement.source_sha256:
            state.update(f"{header}\n⚠ file changed on disk — re-sync to update")
        else:
            state.update(f"{header}\n✓ in sync ({path.name})")
        preview.show_program(path)

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        table_id = event.data_table.id
        key = event.row_key.value or ""
        if table_id == "placements-table":
            self.show_placement(key)
        elif table_id == "documents-table":
            doc = next(
                (d for d in documents.for_org(self.app.conn, self.current_org_id) if d.id == key),
                None,
            )
            if doc:
                subprocess.Popen(["open", doc.path])
                self.notify(f"opening {doc.path}")

    def action_mark_primary(self) -> None:
        table = self.query_one("#contacts-table", ListTable)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if key:
            contacts.set_primary(self.app.conn, key)
            self.notify("primary contact set")
            self.refresh_data()

    def action_task_done(self) -> None:
        table = self.query_one("#ov-tasks", ListTable)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if key:
            tasks_repo.complete(self.app.conn, key)
            self.notify("task done — u to undo")
            self.refresh_data()

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()

    # --- add / edit (contextual per tab) --------------------------------------

    def _active_tab(self) -> str:
        return self.query_one(TabbedContent).active

    def _selected_key(self, table_id: str) -> str | None:
        table = self.query_one(f"#{table_id}", ListTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    def _push_form(self, spec, on_save) -> None:
        from ..widgets.forms import FormModal

        def commit(values) -> str | None:
            on_save(values)  # raises → FormModal shows it and stays open
            return None

        def done(values) -> None:
            if values is not None:
                self.refresh_data()

        self.app.push_screen(FormModal(spec, commit=commit), done)

    def action_add_here(self) -> None:
        from ..widgets import entity_forms as ef

        conn = self.app.conn
        org_id = self.current_org_id
        tab = self._active_tab()
        if tab == "tab-contacts":
            self._push_form(
                ef.contact_form(),
                lambda v: self.notify(
                    f"added {ef.apply_contact(conn, org_id, v).name}"
                ),
            )
        elif tab == "tab-placements":
            self._push_form(
                ef.placement_form(conn=conn),
                lambda v: self.notify(
                    f"created {ef.apply_placement(conn, v, org_id).ref}"
                ),
            )
        elif tab == "tab-pipeline":
            self._push_form(
                ef.opportunity_form(conn=conn),
                lambda v: self.notify(
                    f"created {ef.apply_opportunity(conn, v, org_id).ref}"
                ),
            )
        elif tab == "tab-documents":
            self._push_form(
                ef.document_form(),
                lambda v: documents.add(
                    conn, org_id, v["title"], v["path"], kind=v.get("kind")
                ),
            )
        elif tab == "tab-interactions":
            self.app.action_quick_capture()
        elif tab == "tab-projects":
            from ...repo import projects as projects_repo

            needs_table = self.query_one("#needs-table", ListTable)
            project_id = self._selected_key("projects-table")
            if self.focused is needs_table and project_id:
                project = projects_repo.get_project(conn, project_id)
                self._push_form(
                    ef.need_form(conn=conn),
                    lambda v: self.notify(
                        f"need added to {project.name}: "
                        f"{ef.apply_need(conn, v, project.id).line}"
                    ),
                )
            else:
                self._push_form(
                    ef.project_form(),
                    lambda v: self.notify(
                        f"created {ef.apply_project(conn, v, org_id).ref}"
                    ),
                )
        else:  # overview → a new task for this account
            self._push_form(
                ef.task_form(conn=conn, default_org_id=org_id),
                lambda v: ef.apply_task(conn, v, org_id=org_id),
            )

    def action_edit_here(self) -> None:
        from ...repo import opportunities as opps_repo
        from ..widgets import entity_forms as ef

        conn = self.app.conn
        tab = self._active_tab()
        if tab == "tab-contacts":
            key = self._selected_key("contacts-table")
            if key:
                existing = contacts.get(conn, key)
                self._push_form(
                    ef.contact_form(existing),
                    lambda v: ef.apply_contact(conn, existing.org_id, v, existing),
                )
        elif tab == "tab-placements":
            key = self._selected_key("placements-table")
            if key:
                from ..widgets import entity_actions

                entity_actions.edit_placement(self, placements.get(conn, key))
        elif tab == "tab-pipeline":
            focused = self.focused
            if focused is not None and focused.id == "pipeline-subs":
                key = self._selected_key("pipeline-subs")
                if key:
                    sub = submissions.get(conn, key)

                    def response_saved(values: dict) -> None:
                        ef.apply_response(conn, sub.id, values)
                        if values.get("status") == "bound":
                            # bound → offer to put the market on a layer
                            self._offer_bind_to_layer(sub.id)

                    self._push_form(ef.response_form(sub), response_saved)
            else:
                key = self._selected_key("pipeline-opps")
                if key:
                    opp = opps_repo.get(conn, key)
                    self._push_form(
                        ef.opportunity_form(opp, conn=conn),
                        lambda v: ef.apply_opportunity(conn, v, opp.org_id, opp),
                    )
        elif tab == "tab-projects":
            from ...repo import projects as projects_repo

            needs_table = self.query_one("#needs-table", ListTable)
            need_key = self._selected_key("needs-table")
            if self.focused is needs_table and need_key:
                need = projects_repo.get_need(conn, need_key)
                self._push_form(
                    ef.need_form(need, conn=conn),
                    lambda v: ef.apply_need(conn, v, need.project_id, need),
                )
            else:
                project_key = self._selected_key("projects-table")
                if project_key:
                    project = projects_repo.get_project(conn, project_key)
                    self._push_form(
                        ef.project_form(project),
                        lambda v: ef.apply_project(conn, v, project.org_id, project),
                    )
        elif tab == "tab-overview":
            key = self._selected_key("ov-tasks")
            if key and self.focused is not None and self.focused.id == "ov-tasks":
                task = tasks_repo.get(conn, key)
                self._push_form(
                    ef.task_form(task, conn=conn), lambda v: ef.apply_task(conn, v, existing=task)
                )
            else:  # otherwise e edits the account itself
                self._edit_org()
        else:
            self._edit_org()

    def _edit_org(self) -> None:
        from ..widgets import entity_forms as ef

        conn = self.app.conn
        existing = orgs.get(conn, self.current_org_id)
        self._push_form(
            ef.org_form_initial_profile(conn, existing),
            lambda v: ef.apply_org(conn, v, existing),
        )

    def action_renew_placement(self) -> None:
        """Roll the selected placement into next period; file-backed ones get
        next year's towerkit file cloned and linked at birth."""
        if self._active_tab() != "tab-placements":
            self.notify("r renews the selected placement (placements tab)", severity="warning")
            return
        key = self._selected_key("placements-table")
        if key is None:
            return
        from ..widgets import entity_actions

        entity_actions.renew_placement(self, placements.get(self.app.conn, key))

    def action_import_here(self) -> None:
        """Paste imports for this account: contact/signature, a program
        schedule onto an unlinked placement, or renewal terms onto a linked
        one. Every flow stages first; commit is gated on zero errors."""
        from ..widgets.paste_import import ImportChooser

        conn = self.app.conn
        org = orgs.get(conn, self.current_org_id)
        options: list[tuple[str, str]] = [("contact", "Contact / signature paste")]
        for placement in placements.for_org(conn, self.current_org_id):
            label = f"{placement.ref} {placement.program_name} {placement.period_from}"
            if placement.program_path:
                options.append((f"renewal:{placement.id}", f"Renewal terms → {label}"))
            else:
                options.append((f"program:{placement.id}", f"Program schedule → {label}"))

        def chosen(option_id: str | None) -> None:
            if option_id is None:
                return
            if option_id == "contact":
                self._paste_contact(org)
            elif option_id.startswith("program:"):
                self._paste_program(org, option_id.partition(":")[2])
            elif option_id.startswith("renewal:"):
                self._paste_renewal(option_id.partition(":")[2])

        self.app.push_screen(ImportChooser(options), chosen)

    def _paste_contact(self, org) -> None:
        from ...imports.commit import commit_contact_paste
        from ...imports.mappers.contact_paste import stage_contact_paste
        from ..widgets.paste_import import PasteImportModal

        def stage(text: str):
            return stage_contact_paste(self.app.conn, text, org.id, org.name)

        def commit(staged) -> str:
            commit_contact_paste(self.app.conn, staged, org.id, self.app.db_file())
            self.refresh_data()
            return "contact captured"

        self.app.push_screen(PasteImportModal("paste signature / thread", stage, commit))

    def _paste_program(self, org, placement_id: str) -> None:
        from ... import sync
        from ...imports.commit import commit_program
        from ...imports.mappers.program_paste import stage_program
        from ..widgets.paste_import import PasteImportModal

        placement = placements.get(self.app.conn, placement_id)
        roots = sync.configured_roots(self.app.conn)
        if not roots:
            self.notify("set the program file location first (, on Today)", severity="warning")
            return
        slug = "-".join(org.name.lower().split()[:2]).strip(",.")
        dest = roots[0] / f"{slug}-{placement.period_from[:4]}.json"
        state: dict = {}

        def stage(text: str):
            staged, draft = stage_program(
                self.app.conn, text, org.name, placement.program_name,
                placement.period_from, placement.period_to,
            )
            state["draft"] = draft
            return staged

        def commit(staged) -> str:
            path, diags = commit_program(
                self.app.conn, staged, state["draft"], placement.id, dest,
                self.app.db_file(),
            )
            if path is None:
                first = diags.errors[0] if diags.errors else "unknown error"
                raise ValueError(str(first))
            self.refresh_data()
            return f"created {path.name} — projected onto {placement.ref}"

        self.app.push_screen(
            PasteImportModal(f"paste schedule → {placement.program_name}", stage, commit)
        )

    def _paste_renewal(self, placement_id: str) -> None:
        from ...imports.commit import commit_renewal
        from ...imports.mappers.renewal_paste import stage_renewal
        from ..widgets.paste_import import PasteImportModal

        def stage(text: str):
            return stage_renewal(self.app.conn, placement_id, text)

        def commit(staged) -> str:
            new_id, diags = commit_renewal(
                self.app.conn, staged, placement_id, self.app.db_file()
            )
            if new_id is None:
                first = diags.errors[0] if diags.errors else "unknown error"
                raise ValueError(str(first))
            self.refresh_data()
            if diags.errors:  # renewed, but some pasted terms were refused
                return (
                    f"renewed, but {len(diags.errors)} term(s) NOT applied — "
                    f"{diags.errors[0]}"
                )
            return "renewed with pasted terms — review in the placements tab"

        self.app.push_screen(PasteImportModal("paste renewal terms", stage, commit))

    def action_scaffold_tower(self) -> None:
        """Create the towerkit file FROM the selected placement — insured,
        name, period, and indicated premium flow over; build the tower itself
        in towerkit. Nothing is typed twice."""
        from ... import sync
        from ..widgets.forms import Field, FormModal, FormSpec

        if self._active_tab() != "tab-placements":
            self.notify("t scaffolds a tower file (placements tab)", severity="warning")
            return
        key = self._selected_key("placements-table")
        if key is None:
            return
        placement = placements.get(self.app.conn, key)
        if placement.program_path:
            self.notify(f"{placement.ref} already has a file: {placement.program_path}")
            return
        roots = sync.configured_roots(self.app.conn)
        if not roots:
            self.notify("set the program file location first (, on Today)", severity="warning")
            return
        org = orgs.get(self.app.conn, placement.org_id)
        slug = "-".join(org.name.lower().split()[:2]).strip(",.")
        year = placement.period_from[:4]
        default = roots[0] / f"{slug}-{year}.json"

        created: dict[str, str] = {}

        def commit(values: dict) -> str | None:
            dest, diags = sync.scaffold_program(
                self.app.conn, placement.id, Path(values["path"]).expanduser()
            )
            if dest is None or not diags.ok:
                first = diags.errors[0] if diags.errors else "unknown error"
                return f"scaffold refused: {first}"
            created["name"] = dest.name
            return None

        def done(values: dict | None) -> None:
            if values is None:
                return
            self.notify(f"created {created['name']} — build the tower in towerkit")
            self.refresh_data()

        spec = FormSpec(
            "create tower file",
            [Field("path", "file path", required=True)],
            initial={"path": str(default)},
        )
        self.app.push_screen(FormModal(spec, commit=commit), done)

    def _selected_linked_placement(self):
        """The selected placement when it has a program file, else None+notify."""
        if self._active_tab() != "tab-placements":
            self.notify("layer keys work on the placements tab", severity="warning")
            return None
        key = self._selected_key("placements-table")
        if key is None:
            return None
        placement = placements.get(self.app.conn, key)
        if not placement.program_path:
            self.notify(
                f"{placement.ref} has no program file — e edits it directly, "
                "or t scaffolds a file first",
                severity="warning",
            )
            return None
        return placement

    def action_edit_layer(self) -> None:
        from ..widgets import entity_actions

        placement = self._selected_linked_placement()
        if placement is None:
            return

        def _layer_under_cursor() -> str | None:
            """The carriers table's highlighted row names a layer directly."""
            table = self.query_one("#carriers-table", ListTable)
            if self.focused is not table or not table.row_count:
                return None
            cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
            row_key = cell_key.row_key.value
            return str(row_key).partition(":")[0] if row_key else None

        entity_actions.edit_layer(self, placement, layer_id=_layer_under_cursor())

    def action_add_layer(self) -> None:
        from ... import sync
        from ..widgets.forms import Field, FormModal, FormSpec

        placement = self._selected_linked_placement()
        if placement is None:
            return
        lines = sync.program_lines(self.app.conn, placement.id)
        if not lines:
            self.notify("the program has no lines — add them in towerkit (o)")
            return
        line_options = tuple((name, line_id) for line_id, name in lines)
        if len(lines) > 1:
            line_options = (("all lines", "__all__"), *line_options)
        spec = FormSpec(
            "add layer (pending — markets join as they bind)",
            [
                Field("name", "name", required=True, placeholder="1st Excess"),
                Field("line", "applies to", "select", line_options, required=True),
                Field("attach", "attach", "money", required=True),
                Field("limit", "limit", "money", required=True),
                Field("premium", "indicated premium", "money"),
            ],
        )

        def commit(values: dict) -> str | None:
            line_ids = (
                [line_id for line_id, _ in lines]
                if values["line"] == "__all__"
                else [values["line"]]
            )
            diags = sync.add_layer(
                self.app.conn,
                placement.id,
                values["name"],
                line_ids,
                attach_cents=values["attach"],
                limit_cents=values["limit"],
                premium_cents=values.get("premium"),
            )
            return f"refused: {diags.errors[0]}" if not diags.ok else None

        def done(values: dict | None) -> None:
            if values is None:
                return
            self.notify(f"added {values['name']} (to be placed)")
            self.refresh_data()

        self.app.push_screen(FormModal(spec, commit=commit), done)

    def action_open_towerkit(self) -> None:
        """Suspend bookkit, open the linked file in towerkit's editor, and
        re-project on the way back so the cache follows the edits.
        On the projects tab, `o` instead turns the selected need into its
        opportunity — the optional link forming when the need gets real."""
        import shutil
        import sys

        from ... import sync

        if self._active_tab() == "tab-projects":
            self._need_to_opportunity()
            return

        placement = self._selected_linked_placement()
        if placement is None:
            return
        towerctl = Path(sys.executable).with_name("towerctl")
        if not towerctl.exists():
            found = shutil.which("towerctl")
            if found is None:
                self.notify("towerctl not found on PATH", severity="error")
                return
            towerctl = Path(found)
        with self.app.suspend():
            subprocess.run([str(towerctl), "edit", placement.program_path])
        diags = sync.project(self.app.conn, Path(placement.program_path))
        if diags.ok:
            self.notify("back from towerkit — re-projected")
        else:
            self.notify(
                f"back from towerkit, but the file has issues: {diags.errors[0]}",
                severity="error",
            )
        self.refresh_data()

    def _need_to_opportunity(self) -> None:
        """o on a need row: create the pre-filled opportunity and link it."""
        from ...repo import projects as projects_repo

        conn = self.app.conn
        need_key = self._selected_key("needs-table")
        if need_key is None:
            self.notify("select a need first (needs table)", severity="warning")
            return
        need = projects_repo.get_need(conn, need_key)
        if need.opportunity_id is not None:
            self.notify("this need already has an opportunity")
            return
        project = projects_repo.get_project(conn, need.project_id)
        opp = opportunities.create(
            conn,
            project.org_id,
            f"{project.name} — {need.line}",
            lines=need.line,
            target_effective=need.needed_by,
            target_premium=need.premium_indication_cents,
        )
        projects_repo.update_need(conn, need.id, opportunity_id=opp.id)
        self.notify(f"{opp.ref} created from the need — it's in the pipeline")
        self.refresh_data()

    def _offer_bind_to_layer(self, submission_id: str) -> None:
        """A market bound: offer to put them on a layer at their share."""
        from ... import sync
        from ...money import MoneyParseError, parse_share_bps
        from ..widgets.forms import Field, FormModal, FormSpec
        from ..widgets.picker import Picker

        conn = self.app.conn
        sub = submissions.get(conn, submission_id)
        if sub.placement_id is None:
            return
        placement = placements.get(conn, sub.placement_id)
        if not placement.program_path:
            return
        market = orgs.get(conn, sub.market_org_id)
        layers = sync.layer_details(conn, placement.id)
        if not layers:
            return

        def picked(layer_id: str | None) -> None:
            if layer_id is None:
                return
            layer = next(ly for ly in layers if ly["id"] == layer_id)
            spec = FormSpec(
                f"{market.name} on {layer['name']}",
                [Field("share", "share % ('25', '12.5%')", required=True)],
            )

            def commit(values: dict) -> str | None:
                try:
                    share_bps = parse_share_bps(str(values["share"]))
                except MoneyParseError as exc:
                    return str(exc)
                diags = sync.add_participant(
                    conn, placement.id, layer_id, market.name, share_bps
                )
                if not diags.ok:
                    return f"refused: {diags.errors[0]}"
                bound["share_bps"] = share_bps
                return None

            bound: dict[str, int] = {}

            def done(values: dict | None) -> None:
                if values is None:
                    return
                self.notify(
                    f"{market.name} added to {layer['name']} "
                    f"at {bound['share_bps'] / 100:g}%"
                )
                self.refresh_data()

            self.app.push_screen(FormModal(spec, commit=commit), done)

        options = [
            (
                f"{ly['name']}  {format_cents_compact(ly['limit_cents'])} xs "
                f"{format_cents_compact(ly['attach_cents'])}  ({ly['signed_pct']:g}% placed)",
                str(ly["id"]),
            )
            for ly in layers
        ]
        self.app.push_screen(
            Picker(f"add {market.name} to which layer? (esc skips)", options), picked
        )

    def action_assign_team(self) -> None:
        """Assign a colleague: to the account, or to the selected placement
        when the placements tab is open."""
        from ...repo import team as team_repo
        from ..widgets.entity_forms import assignment_form
        from ..widgets.forms import FormModal, dropped

        conn = self.app.conn
        members = team_repo.list_members(conn)
        if not members:
            self.notify("no team members yet — press w on Today, then a", severity="warning")
            return
        placement_id = (
            self._selected_key("placements-table")
            if self._active_tab() == "tab-placements"
            else None
        )
        org_id = None if placement_id else self.current_org_id

        from ..widgets.entity_forms import NEW_MEMBER

        def commit(values: dict) -> str | None:
            if values["team_member_id"] == NEW_MEMBER:
                return None  # nothing to write yet — done() chains to the member form
            cleaned = dropped(values)
            member_id = cleaned.pop("team_member_id")
            team_repo.assign(
                conn, member_id, org_id=org_id, placement_id=placement_id, **cleaned
            )
            assigned["name"] = team_repo.get_member(conn, member_id).name
            return None

        assigned: dict[str, str] = {}

        def done(values: dict | None) -> None:
            if values is None:
                return
            if values["team_member_id"] == NEW_MEMBER:
                self._create_member_then_assign(values, org_id, placement_id)
                return
            scope = "placement" if placement_id else "account"
            self.notify(f"{assigned['name']} assigned to this {scope}")
            self.refresh_data()

        options = tuple(
            (f"{m.name} ({m.specialty})" if m.specialty else m.name, m.id)
            for m in members
        )
        self.app.push_screen(FormModal(assignment_form(options, conn=conn), commit=commit), done)

    def _create_member_then_assign(
        self, assignment: dict, org_id: str | None, placement_id: str | None
    ) -> None:
        """The who-select's '+ new team member…' sentinel chains here: create
        the member and complete the assignment in one transaction, so a
        refused member save keeps that form open with input intact."""
        from ...db import transaction
        from ...repo import team as team_repo
        from ..widgets import entity_forms as ef
        from ..widgets.forms import FormModal, dropped

        conn = self.app.conn

        def commit(values: dict) -> str | None:
            core = dropped(values)
            name = core.pop("name")
            with transaction(conn):
                member = team_repo.create_member(conn, name, **core)
                team_repo.assign(
                    conn, member.id, org_id=org_id, placement_id=placement_id,
                    role=assignment.get("role"),
                    lines=assignment.get("lines"),
                    notes=assignment.get("notes"),
                )
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.notify("member created and assigned")
                self.refresh_data()

        self.app.push_screen(
            FormModal(ef.member_form(conn=conn), commit=commit), done
        )

    def action_merge_placement(self) -> None:
        """Merge the selected (duplicate) placement into another of this org's
        placements — submissions, tasks, and documents move with it."""
        if self._active_tab() != "tab-placements":
            self.notify("x merges the selected placement (placements tab)", severity="warning")
            return
        key = self._selected_key("placements-table")
        if key is None:
            return
        source = placements.get(self.app.conn, key)
        targets = [
            p for p in placements.for_org(self.app.conn, source.org_id) if p.id != source.id
        ]
        if not targets:
            self.notify("nothing to merge into — this is the only placement")
            return
        self.app.push_screen(MergePicker(source, targets), self._merge_confirmed(source.id))

    def _merge_confirmed(self, source_id: str):
        def done(target_id: str | None) -> None:
            if target_id is None:
                return
            from ...services.merge import MergeError, merge_placements

            try:
                result = merge_placements(self.app.conn, source_id, target_id)
            except MergeError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(
                f"merged into {result.target.ref}: {result.moved_submissions} submissions, "
                f"{result.moved_tasks} tasks, {result.moved_documents} documents moved"
                + (" (file link carried)" if result.carried_link else "")
            )
            self.refresh_data()

        return done

    def action_new_submission(self) -> None:
        from ...repo import orgs as orgs_repo
        from ..widgets import entity_forms as ef

        conn = self.app.conn
        if not orgs_repo.list_orgs(conn, kind="market"):
            self.notify("no markets on file — create one in the markets screen (m, then a)",
                        severity="warning")
            return
        tab = self._active_tab()
        placement_id = self._selected_key("placements-table") if tab == "tab-placements" else None
        opportunity_id = self._selected_key("pipeline-opps") if tab == "tab-pipeline" else None
        if placement_id is None and opportunity_id is None:
            self.notify("select a placement or opportunity first (s works on those tabs)",
                        severity="warning")
            return
        self._push_form(
            ef.submission_form(conn),
            lambda v: ef.apply_submission(
                conn, v, placement_id=placement_id, opportunity_id=opportunity_id
            ),
        )
