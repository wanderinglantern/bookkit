"""Today — the default screen. Four panes: tasks, renewals, stale accounts,
submissions past SLA. Every row is actionable with enter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from datetime import date

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from ...dates import days_until
from ...repo import tasks as tasks_repo
from ...services import renewals, sla, staleness
from .. import theme
from ..theme import dash, date_text, days_text, money_text, right, status_text
from ..widgets.tables import ListTable

# Below this width the 2x2 grid gives each pane ~36 cells for up to seven
# columns, which is where an overdue "-345" used to truncate to "-". One
# column and a scroll beats four unreadable quadrants (review F4).
TWO_COLUMN_MIN_WIDTH = 100
# rows per pane when stacked: title, header, and ~9 rows of work
PANE_HEIGHT = 12


class TodayScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("b", "open_book", "Book"),
        Binding("c", "open_calendar", "Calendar"),
        Binding("p", "open_pipeline", "Pipeline"),
        Binding("m", "open_markets", "Markets"),
        Binding("w", "open_team", "Team"),
        Binding("a", "new_task", "New task"),
        Binding("e", "edit_task", "Edit task"),
        Binding("d", "task_done", "Done (task)"),
        Binding("u", "undo", "Undo"),
        Binding("i", "import_book", "Import"),
        Binding("y", "sync_programs", "Sync programs"),
        Binding("comma", "settings", "Setup", key_display=","),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="today-scroll"), Grid(id="today-grid"):
            with Vertical(classes="pane"):
                yield Static("TASKS DUE & OVERDUE", classes="pane-title")
                yield ListTable(id="tasks-table")
            with Vertical(classes="pane"):
                yield Static("RENEWALS — NEXT 120 DAYS", classes="pane-title")
                yield ListTable(id="renewals-table")
            with Vertical(classes="pane"):
                yield Static("STALE ACCOUNTS", classes="pane-title")
                yield ListTable(id="stale-table")
            with Vertical(classes="pane"):
                yield Static("SUBMISSIONS PAST SLA", classes="pane-title")
                yield ListTable(id="sla-table")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_layout(self.size.width)
        self.refresh_data()
        self.query_one("#tasks-table", ListTable).focus()
        from ...repo import orgs

        if not orgs.list_orgs(self.app.conn):
            self.notify(
                "empty book — press b for the book, then a to create your first account"
            )

    def refresh_data(self) -> None:
        conn = self.app.conn
        today = date.today()

        tasks_table = self.query_one("#tasks-table", ListTable)
        tasks_table.clear(columns=True)
        tasks_table.add_columns("due", right("due in"), "task", "account")
        for task in tasks_repo.open_tasks(conn, due_by=today.isoformat()):
            overdue = days_until(task.due_on, today) if task.due_on else 0
            org_name = ""
            if task.org_id:
                from ...repo import orgs

                try:  # a task can outlive its soft-deleted account
                    org_name = orgs.get(conn, task.org_id).name
                except KeyError:
                    org_name = "(deleted account)"
            row_key = f"task:{task.id}:{task.org_id or ''}"
            due = date_text(task.due_on, overdue) if task.due_on else dash()
            tasks_table.add_row(
                due, days_text(overdue), task.title, org_name, key=row_key
            )

        renewals_table = self.query_one("#renewals-table", ListTable)
        renewals_table.clear(columns=True)
        renewals_table.add_columns(
            "expiry", right("due in"), "account", "program", "lines", "status",
            right("premium"),
        )
        for item in renewals.upcoming(conn, today, days=120):
            renewals_table.add_row(
                date_text(item.placement.period_to, item.days_remaining),
                days_text(item.days_remaining),
                item.org.name,
                item.placement.program_name,
                item.lines or dash(),
                status_text(item.placement.status),
                money_text(item.placement.total_premium),
                key=f"renewal:{item.placement.id}:{item.org.id}",
            )
        from ...repo import projects as projects_repo

        for need in projects_repo.needs_due(conn, today, days=120):
            # a project's insurance-needed-by is the same class of attention
            days = days_until(need["needed_by"], today)
            renewals_table.add_row(
                date_text(need["needed_by"], days),
                days_text(days),
                need["org_name"],
                f"{need['project_name']} (need)",
                need["line"],
                status_text(need["status"]),
                money_text(need["limit_cents"]),
                key=f"need:{need['id']}:{need['org_id']}",
            )

        stale_table = self.query_one("#stale-table", ListTable)
        stale_table.clear(columns=True)
        stale_table.add_columns(
            "account", "last touch", right("stale"), right("premium")
        )
        for account in staleness.stale_accounts(conn, today):
            stale_table.add_row(
                account.org.name,
                account.last_interaction_on or Text("never", style=theme.DIM),
                # negative days: staleness is time ALREADY elapsed, so it reads
                # in the same red-and-glyph grammar as an overdue renewal
                days_text(-account.days_stale),
                money_text(account.premium),
                key=f"stale:{account.org.id}:{account.org.id}",
            )

        sla_table = self.query_one("#sla-table", ListTable)
        sla_table.clear(columns=True)
        sla_table.add_columns("market", "account", "sent", right("out"))
        for late in sla.past_sla(conn, today):
            sla_table.add_row(
                late.market.name,
                late.account.name,
                late.submission.sent_on,
                days_text(-late.days_out),
                key=f"sla:{late.submission.id}:{late.account.id}",
            )

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def on_resize(self, event) -> None:
        self._apply_layout(event.size.width)

    def _apply_layout(self, width: int) -> None:
        """Two panes across when there is room for them, one when there is not.

        Textual has no CSS media queries, so the breakpoint lives here. Narrow
        gets `height: auto` panes inside the scroller: four stacked quadrants
        squeezed into 21 rows would be no more readable than four squeezed
        into 80 columns."""
        panes = list(self.query(".pane"))
        wide = width >= TWO_COLUMN_MIN_WIDTH
        grid = self.query_one("#today-grid", Grid)
        grid.styles.grid_size_columns = 2 if wide else 1
        grid.styles.grid_size_rows = 2 if wide else len(panes)
        # an explicit cell height, not `auto`: a grid whose rows are unsized
        # collapses to the first row when its own height is auto, which showed
        # one pane and a screenful of nothing
        grid.styles.height = "1fr" if wide else PANE_HEIGHT * len(panes)
        for pane in panes:
            pane.styles.height = "1fr" if wide else PANE_HEIGHT

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        key = event.row_key.value or ""
        _, _, org_id = key.split(":", 2)
        if org_id:
            self.app.open_account(org_id)

    def action_task_done(self) -> None:
        table = self.query_one("#tasks-table", ListTable)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value or ""
        kind, task_id, _ = key.split(":", 2)
        if kind == "task":
            tasks_repo.complete(self.app.conn, task_id)
            self.notify("task done — u to undo")
            self.refresh_data()

    def _selected_task_id(self) -> str | None:
        table = self.query_one("#tasks-table", ListTable)
        # same gate as action_task_done: with four panes on this screen, `e`
        # fired while the renewals/stale/SLA table had focus would edit the row
        # under the tasks table's INVISIBLE cursor (review F1)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value or ""
        kind, task_id, _ = key.split(":", 2)
        return task_id if kind == "task" else None

    def action_import_book(self) -> None:
        from .import_screen import ImportScreen

        self.app.push_screen(ImportScreen())

    def action_new_task(self) -> None:
        from ..widgets.entity_forms import apply_task, task_form
        from ..widgets.forms import FormModal

        def commit(values: dict) -> str | None:
            apply_task(self.app.conn, values)
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data()

        self.app.push_screen(FormModal(task_form(conn=self.app.conn), commit=commit), done)

    def action_edit_task(self) -> None:
        from ..widgets.entity_forms import apply_task, task_form
        from ..widgets.forms import FormModal

        task_id = self._selected_task_id()
        if task_id is None:
            return
        task = tasks_repo.get(self.app.conn, task_id)

        def commit(values: dict) -> str | None:
            apply_task(self.app.conn, values, existing=task)
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data()

        self.app.push_screen(FormModal(task_form(task, conn=self.app.conn), commit=commit), done)

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_settings(self) -> None:
        from ..widgets.settings import SettingsModal

        self.app.push_screen(SettingsModal())

    def action_sync_programs(self) -> None:
        """Project towerkit files from the configured roots; first run opens
        the setup dialogue, then the review queue handles anything unlinked."""
        from ... import sync
        from ..widgets.link_review import LinkReview
        from ..widgets.settings import SettingsModal

        roots = sync.configured_roots(self.app.conn)
        if not roots:
            def configured(saved: bool | None) -> None:
                if saved:
                    self.action_sync_programs()

            self.notify("first sync — tell me where the program files live")
            self.app.push_screen(SettingsModal(), configured)
            return
        report = sync.project_all(self.app.conn, roots)
        bits = [f"projected {len(report.projected)}"]
        if report.adopted:
            bits.append(f"{len(report.adopted)} adopted")
        if report.relinked:
            bits.append(f"{len(report.relinked)} re-linked")
        if report.needs_link or report.needs_placement:
            bits.append(f"{len(report.needs_link) + len(report.needs_placement)} to review")
        if report.opportunity_candidates:
            bits.append(f"{len(report.opportunity_candidates)} opportunity offers")
        if report.unresolved_carriers:
            bits.append(f"{len(report.unresolved_carriers)} unknown carriers")
        self.notify(", ".join(bits))
        if (
            report.needs_link
            or report.needs_placement
            or report.opportunity_candidates
            or report.unresolved_carriers
        ):
            self.app.push_screen(LinkReview(report))
        self.refresh_data()

    def action_open_book(self) -> None:
        from .book import BookScreen

        self.app.push_screen(BookScreen())

    def action_open_calendar(self) -> None:
        from .calendar import CalendarScreen

        self.app.push_screen(CalendarScreen())

    def action_open_pipeline(self) -> None:
        from .pipeline import PipelineScreen

        self.app.push_screen(PipelineScreen())

    def action_open_team(self) -> None:
        from .team import TeamScreen

        self.app.push_screen(TeamScreen())

    def action_open_markets(self) -> None:
        from .markets import MarketsScreen

        self.app.push_screen(MarketsScreen())
