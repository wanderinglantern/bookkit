"""Navigator — the home screen. Attention-first tree on the left; the right
pane is a WORKING DataTable whenever a group is selected: a/e/d/r/l act on
rows through commit-in-place forms, enter jumps to the full screen. Tree for
structure, tables for work (spec 2026-08-12)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..app import BookkitApp

from datetime import date
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static, Tree

from ...dates import days_until
from ...money import format_cents_compact
from ...repo import contacts, opportunities, orgs, placements
from ...repo import projects as projects_repo
from ...repo import tasks as tasks_repo
from ...services import renewals, sla
from ..widgets.tables import ListTable
from ..widgets.tower_preview import TowerPreview

NodeData = tuple[str, Any]


class NavigatorScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("t", "open_today", "Today"),
        Binding("b", "open_book", "Book"),
        Binding("c", "open_calendar", "Calendar"),
        Binding("p", "open_pipeline", "Pipeline"),
        Binding("m", "open_markets", "Markets"),
        Binding("w", "open_team", "Team"),
        Binding("comma", "settings", "Setup", key_display=","),
        Binding("a", "add_row", "Add", show=False),
        Binding("e", "edit_row", "Edit", show=False),
        Binding("d", "task_done", "Done (task)", show=False),
        Binding("r", "renew_row", "Renew", show=False),
        Binding("l", "edit_layer_row", "Layer", show=False),
        Binding("u", "undo", "Undo"),
        Binding("R", "refresh", "Refresh", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current: NodeData = ("none", None)
        self._row_org: dict[str, str] = {}  # table row key → org id (for enter)
        self._attention: dict[str, list[Any]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="nav-split"):
            yield Tree("book", id="nav-tree")
            with Vertical(id="nav-pane"):
                yield Static(id="nav-card")
                yield ListTable(id="nav-table")
                yield TowerPreview(id="nav-preview")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.query_one("#nav-tree", Tree).focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    # -- tree ------------------------------------------------------------------

    def refresh_data(self) -> None:
        conn = self.app.conn
        today = date.today()
        tree = self.query_one("#nav-tree", Tree)
        tree.clear()
        tree.show_root = False
        tree.root.expand()

        items = renewals.upcoming(conn, today, days=120)
        overdue = [i for i in items if i.days_remaining < 0]
        soon = [i for i in items if i.days_remaining >= 0]
        needs = projects_repo.needs_due(conn, today, days=120)
        due_tasks = tasks_repo.open_tasks(conn, due_by=today.isoformat())
        late = sla.past_sla(conn, today)
        self._attention = {
            "overdue": overdue, "renewals": soon, "needs": needs,
            "tasks": due_tasks, "sla": late,
        }
        att = tree.root.add("⚠ ATTENTION", expand=True, data=("att-root", None))
        for key, label, count in (
            ("overdue", "overdue renewals", len(overdue)),
            ("renewals", "renewals ≤ 120d", len(soon)),
            ("needs", "project needs due", len(needs)),
            ("tasks", "tasks due", len(due_tasks)),
            ("sla", "submissions past SLA", len(late)),
        ):
            style = "[red]" if key == "overdue" and count else ""
            end = "[/red]" if style else ""
            att.add_leaf(f"{style}{label} ({count}){end}", data=("att", key))

        clients = orgs.list_orgs(conn, kind="client")
        overdue_orgs = {i.org.id for i in overdue}
        accounts = tree.root.add(
            f"ACCOUNTS ({len(clients)})", expand=True, data=("accounts-root", None)
        )
        for org in clients:
            badge = "[red]⚠ [/red]" if org.id in overdue_orgs else ""
            node = accounts.add(f"{badge}{org.name}", data=("account", org.id))
            node.allow_expand = True
        markets_root = tree.root.add("MARKETS", data=("markets-root", None))
        for master, kids in orgs.market_families(conn):
            if kids:  # a family: master expands to its issuing companies
                family = markets_root.add(master.name, data=("market", master.id))
                for kid in kids:
                    family.add_leaf(kid.name, data=("market", kid.id))
            else:
                markets_root.add_leaf(master.name, data=("market", master.id))
        self._render_pane()

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        data = event.node.data
        if data is None or data[0] != "account" or event.node.children:
            return
        conn = self.app.conn
        org_id = data[1]
        counts = (
            ("placements", len(placements.for_org(conn, org_id))),
            ("contacts", len(contacts.for_org(conn, org_id))),
            ("opportunities", len(opportunities.for_org(conn, org_id))),
            ("tasks", len(tasks_repo.open_tasks(conn, org_id=org_id))),
            ("projects", len(projects_repo.projects_for_org(conn, org_id))),
        )
        for group, count in counts:
            event.node.add_leaf(f"{group} ({count})", data=("group", (group, org_id)))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self._current = event.node.data or ("none", None)
        self._render_pane()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if data and data[0] == "account":
            self.app.open_account(data[1])
        elif data and data[0] == "market":
            from .markets import MarketDetailScreen

            self.app.push_screen(MarketDetailScreen(data[1]))

    # -- right pane --------------------------------------------------------------

    def _render_pane(self) -> None:
        kind, payload = self._current
        card = self.query_one("#nav-card", Static)
        table = self.query_one("#nav-table", ListTable)
        preview = self.query_one("#nav-preview", TowerPreview)
        preview.display = False
        self._row_org.clear()
        if kind == "att":
            card.display = False
            table.display = True
            self._fill_attention_table(table, payload)
        elif kind == "group":
            card.display = False
            table.display = True
            self._fill_group_table(table, *payload)
        elif kind == "account":
            table.display = False
            card.display = True
            card.update(self._account_card(payload))
        elif kind == "market":
            table.display = False
            card.display = True
            card.update(self._market_card(payload))
        else:
            table.display = False
            card.display = True
            card.update("select a node — j/k moves, space expands, enter opens")

    def _fill_attention_table(self, table: ListTable, which: str) -> None:
        conn = self.app.conn
        today = date.today()
        table.clear(columns=True)
        if which in ("overdue", "renewals"):
            table.add_columns("expiry", "d", "account", "program", "status", "premium")
            for item in self._attention[which]:
                key = f"placement:{item.placement.id}"
                self._row_org[key] = item.org.id
                table.add_row(
                    item.placement.period_to, str(item.days_remaining), item.org.name,
                    item.placement.program_name, item.placement.status,
                    format_cents_compact(item.placement.total_premium)
                    if item.placement.total_premium else "—",
                    key=key,
                )
        elif which == "needs":
            table.add_columns("needed by", "d", "account", "need", "status")
            for need in self._attention["needs"]:
                key = f"need:{need['id']}"
                self._row_org[key] = need["org_id"]
                table.add_row(
                    need["needed_by"], str(days_until(need["needed_by"], today)),
                    need["org_name"], f"{need['line']} — {need['project_name']}",
                    need["status"], key=key,
                )
        elif which == "tasks":
            table.add_columns("due", "task", "account")
            for task in self._attention["tasks"]:
                key = f"task:{task.id}"
                name = ""
                if task.org_id:
                    self._row_org[key] = task.org_id
                    try:  # a task can outlive its soft-deleted account
                        name = orgs.get(conn, task.org_id).name
                    except KeyError:
                        name = "(deleted account)"
                table.add_row(task.due_on or "—", task.title, name, key=key)
        elif which == "sla":
            table.add_columns("market", "account", "sent", "out")
            for item in self._attention["sla"]:
                key = f"submission:{item.submission.id}"
                self._row_org[key] = item.account.id
                table.add_row(
                    item.market.name, item.account.name,
                    item.submission.sent_on, f"{item.days_out}d", key=key,
                )

    def _fill_group_table(self, table: ListTable, group: str, org_id: str) -> None:
        conn = self.app.conn
        table.clear(columns=True)
        if group == "placements":
            table.add_columns("ref", "program", "effective", "expires", "d", "status", "premium")
            rows = sorted(
                placements.for_org(conn, org_id),
                key=lambda p: (days_until(p.period_to) < 0, abs(days_until(p.period_to))),
            )
            for p in rows:
                days = days_until(p.period_to)
                expires = p.period_to
                if days < 0:
                    expires = f"[red]{p.period_to}[/red]"
                elif days <= 60:
                    expires = f"[yellow]{p.period_to}[/yellow]"
                key = f"placement:{p.id}"
                self._row_org[key] = org_id
                table.add_row(
                    p.ref, p.program_name, p.period_from, expires, str(days), p.status,
                    format_cents_compact(p.total_premium) if p.total_premium else "—",
                    key=key,
                )
        elif group == "contacts":
            table.add_columns("", "name", "role", "title", "email", "phone")
            for c in contacts.for_org(conn, org_id):
                key = f"contact:{c.id}"
                self._row_org[key] = org_id
                table.add_row(
                    "p" if c.is_primary else "", c.name, c.role or "—", c.title or "—",
                    c.email or "—", c.phone or c.mobile or "—", key=key,
                )
        elif group == "opportunities":
            table.add_columns("ref", "title", "stage", "target", "eff", "prob")
            for o in opportunities.for_org(conn, org_id):
                key = f"opportunity:{o.id}"
                self._row_org[key] = org_id
                table.add_row(
                    o.ref, o.title, o.stage,
                    format_cents_compact(o.target_premium) if o.target_premium else "—",
                    o.target_effective or "—", f"{o.probability_pct}%", key=key,
                )
        elif group == "tasks":
            table.add_columns("due", "task", "status")
            for task in tasks_repo.open_tasks(conn, org_id=org_id):
                key = f"task:{task.id}"
                self._row_org[key] = org_id
                table.add_row(task.due_on or "—", task.title, task.status, key=key)
        elif group == "projects":
            table.add_columns("ref", "project", "status", "start", "end")
            for project in projects_repo.projects_for_org(conn, org_id):
                key = f"project:{project.id}"
                self._row_org[key] = org_id
                table.add_row(
                    project.ref, project.name, project.status,
                    project.start_on or "—", project.end_on or "—", key=key,
                )

    def _account_card(self, org_id: str) -> str:
        conn = self.app.conn
        org = orgs.get(conn, org_id)
        nxt = renewals.next_for_org(conn, org_id)
        if nxt is None:
            renewal = "none scheduled"
        elif nxt.days_remaining < 0:
            renewal = (
                f"[red]{nxt.placement.period_to} "
                f"({-nxt.days_remaining}d overdue — {nxt.placement.program_name})[/red]"
            )
        else:
            renewal = f"{nxt.placement.period_to} ({nxt.days_remaining}d)"
        lines = [
            f"[b]{org.name}[/b]  {org.ref}",
            f"status: {org.status}   owner: {org.owner or '—'}",
            f"next renewal: {renewal}",
            "",
            "space expands · enter opens the account screen",
        ]
        return "\n".join(lines)

    def _market_card(self, org_id: str) -> str:
        org = orgs.get(self.app.conn, org_id)
        profile = orgs.get_market_profile(self.app.conn, org_id)
        kind = profile.market_type if profile and profile.market_type else "market"
        rating = (
            f"   AM Best {profile.am_best_rating}"
            if profile and profile.am_best_rating
            else ""
        )
        return f"[b]{org.name}[/b]  ({kind}){rating}\n\nenter opens the market screen"

    # -- table row context -------------------------------------------------------

    def _selected_row(self) -> tuple[str, str] | None:
        from textual.coordinate import Coordinate

        table = self.query_one("#nav-table", ListTable)
        # row actions require the TABLE to hold focus — pressing e/d/r/l while
        # browsing the tree must never mutate a row the user isn't looking at
        if (
            not table.display
            or not table.has_focus
            or table.cursor_row is None
            or table.row_count == 0
        ):
            return None
        value = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if not value:
            return None
        kind, _, entity_id = str(value).partition(":")
        return kind, entity_id

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        key = str(event.row_key.value or "")
        org_id = self._row_org.get(key)
        if org_id:
            self.app.open_account(org_id)

    def on_data_table_row_highlighted(self, event: ListTable.RowHighlighted) -> None:
        preview = self.query_one("#nav-preview", TowerPreview)
        key = str(event.row_key.value or "") if event.row_key else ""
        kind, _, entity_id = key.partition(":")
        if kind == "placement":
            try:  # row keys can go stale mid-rebuild (undo, deletes)
                placement = placements.get(self.app.conn, entity_id)
            except KeyError:
                preview.display = False
                return
            if placement.program_path and Path(placement.program_path).exists():
                preview.display = True
                preview.show_program(Path(placement.program_path))
                return
        preview.display = False

    # -- row actions ---------------------------------------------------------------

    def action_add_row(self) -> None:
        from ..widgets import entity_actions
        from ..widgets import entity_forms as ef

        kind, payload = self._current
        if kind != "group":
            return
        group, org_id = payload
        conn = self.app.conn
        if group == "contacts":
            entity_actions.push_form(
                self, ef.contact_form(),
                lambda v: self.notify(f"added {ef.apply_contact(conn, org_id, v).name}"),
            )
        elif group == "placements":
            entity_actions.push_form(
                self, ef.placement_form(),
                lambda v: self.notify(f"created {ef.apply_placement(conn, v, org_id).ref}"),
            )
        elif group == "opportunities":
            entity_actions.push_form(
                self, ef.opportunity_form(),
                lambda v: self.notify(f"created {ef.apply_opportunity(conn, v, org_id).ref}"),
            )
        elif group == "tasks":
            entity_actions.push_form(
                self, ef.task_form(conn=conn, default_org_id=org_id),
                lambda v: ef.apply_task(conn, v, org_id=org_id),
            )
        elif group == "projects":
            entity_actions.push_form(
                self, ef.project_form(),
                lambda v: self.notify(f"created {ef.apply_project(conn, v, org_id).ref}"),
            )

    def action_edit_row(self) -> None:
        from ..widgets import entity_actions
        from ..widgets import entity_forms as ef

        row = self._selected_row()
        if row is None:
            return
        kind, entity_id = row
        conn = self.app.conn
        if kind == "placement":
            entity_actions.edit_placement(self, placements.get(conn, entity_id))
        elif kind == "contact":
            existing = contacts.get(conn, entity_id)
            entity_actions.push_form(
                self, ef.contact_form(existing),
                lambda v: ef.apply_contact(conn, existing.org_id, v, existing),
            )
        elif kind == "opportunity":
            opp = opportunities.get(conn, entity_id)
            entity_actions.push_form(
                self, ef.opportunity_form(opp),
                lambda v: ef.apply_opportunity(conn, v, opp.org_id, opp),
            )
        elif kind == "task":
            task = tasks_repo.get(conn, entity_id)
            entity_actions.push_form(
                self, ef.task_form(task, conn=conn),
                lambda v: ef.apply_task(conn, v, existing=task),
            )
        elif kind == "project":
            project = projects_repo.get_project(conn, entity_id)
            entity_actions.push_form(
                self, ef.project_form(project),
                lambda v: ef.apply_project(conn, v, project.org_id, project),
            )

    def action_task_done(self) -> None:
        row = self._selected_row()
        if row is None or row[0] != "task":
            return
        tasks_repo.complete(self.app.conn, row[1])
        self.notify("task done — u to undo")
        self.refresh_data()

    def action_renew_row(self) -> None:
        from ..widgets import entity_actions

        row = self._selected_row()
        if row is None or row[0] != "placement":
            return
        entity_actions.renew_placement(
            self, placements.get(self.app.conn, row[1])
        )

    def action_edit_layer_row(self) -> None:
        from ..widgets import entity_actions

        row = self._selected_row()
        if row is None or row[0] != "placement":
            return
        entity_actions.edit_layer(self, placements.get(self.app.conn, row[1]))

    # -- navigation -----------------------------------------------------------------

    def action_open_today(self) -> None:
        from .today import TodayScreen

        self.app.push_screen(TodayScreen())

    def action_open_book(self) -> None:
        from .book import BookScreen

        self.app.push_screen(BookScreen())

    def action_open_calendar(self) -> None:
        from .calendar import CalendarScreen

        self.app.push_screen(CalendarScreen())

    def action_open_pipeline(self) -> None:
        from .pipeline import PipelineScreen

        self.app.push_screen(PipelineScreen())

    def action_open_markets(self) -> None:
        from .markets import MarketsScreen

        self.app.push_screen(MarketsScreen())

    def action_open_team(self) -> None:
        from .team import TeamScreen

        self.app.push_screen(TeamScreen())

    def action_settings(self) -> None:
        from ..widgets.settings import SettingsModal

        self.app.push_screen(SettingsModal())

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()
