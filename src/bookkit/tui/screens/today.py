"""Today — the default screen. Four panes: tasks, renewals, stale accounts,
submissions past SLA. Every row is actionable with enter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from datetime import date

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from ...dates import days_until
from ...money import format_cents_compact
from ...repo import tasks as tasks_repo
from ...services import renewals, sla, staleness
from ..widgets.tables import ListTable


class TodayScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("b", "open_book", "Book"),
        Binding("c", "open_calendar", "Calendar"),
        Binding("p", "open_pipeline", "Pipeline"),
        Binding("m", "open_markets", "Markets"),
        Binding("a", "new_task", "New task"),
        Binding("e", "edit_task", "Edit task"),
        Binding("d", "task_done", "Done (task)"),
        Binding("u", "undo", "Undo"),
        Binding("y", "sync_programs", "Sync programs"),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Grid(id="today-grid"):
            with Vertical(classes="pane"):
                yield Static("TASKS DUE & OVERDUE", classes="pane-title")
                yield ListTable(id="tasks-table")
            with Vertical(classes="pane"):
                yield Static("RENEWALS — NEXT 90 DAYS", classes="pane-title")
                yield ListTable(id="renewals-table")
            with Vertical(classes="pane"):
                yield Static("STALE ACCOUNTS", classes="pane-title")
                yield ListTable(id="stale-table")
            with Vertical(classes="pane"):
                yield Static("SUBMISSIONS PAST SLA", classes="pane-title")
                yield ListTable(id="sla-table")
        yield Footer()

    def on_mount(self) -> None:
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
        tasks_table.add_columns("due", "task", "account")
        for task in tasks_repo.open_tasks(conn, due_by=today.isoformat()):
            overdue = days_until(task.due_on, today) if task.due_on else 0
            label = f"{-overdue}d late" if overdue < 0 else "today"
            org_name = ""
            if task.org_id:
                from ...repo import orgs

                org_name = orgs.get(conn, task.org_id).name
            row_key = f"task:{task.id}:{task.org_id or ''}"
            tasks_table.add_row(label, task.title, org_name, key=row_key)

        renewals_table = self.query_one("#renewals-table", ListTable)
        renewals_table.clear(columns=True)
        renewals_table.add_columns("expiry", "d", "account", "program", "status", "premium")
        for item in renewals.upcoming(conn, today, days=90):
            renewals_table.add_row(
                item.placement.period_to,
                str(item.days_remaining),
                item.org.name,
                item.placement.program_name,
                item.placement.status,
                format_cents_compact(item.placement.total_premium)
                if item.placement.total_premium
                else "—",
                key=f"renewal:{item.placement.id}:{item.org.id}",
            )

        stale_table = self.query_one("#stale-table", ListTable)
        stale_table.clear(columns=True)
        stale_table.add_columns("account", "last touch", "stale", "premium")
        for account in staleness.stale_accounts(conn, today):
            stale_table.add_row(
                account.org.name,
                account.last_interaction_on or "never",
                f"{account.days_stale}d",
                format_cents_compact(account.premium) if account.premium else "—",
                key=f"stale:{account.org.id}:{account.org.id}",
            )

        sla_table = self.query_one("#sla-table", ListTable)
        sla_table.clear(columns=True)
        sla_table.add_columns("market", "account", "sent", "out")
        for late in sla.past_sla(conn, today):
            sla_table.add_row(
                late.market.name,
                late.account.name,
                late.submission.sent_on,
                f"{late.days_out}d",
                key=f"sla:{late.submission.id}:{late.account.id}",
            )

    def on_screen_resume(self) -> None:
        self.refresh_data()

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
        if table.cursor_row is None or table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value or ""
        kind, task_id, _ = key.split(":", 2)
        return task_id if kind == "task" else None

    def action_new_task(self) -> None:
        from ..widgets.entity_forms import apply_task, task_form
        from ..widgets.forms import FormModal

        def saved(values: dict | None) -> None:
            if values is not None:
                apply_task(self.app.conn, values)
                self.refresh_data()

        self.app.push_screen(FormModal(task_form()), saved)

    def action_edit_task(self) -> None:
        from ..widgets.entity_forms import apply_task, task_form
        from ..widgets.forms import FormModal

        task_id = self._selected_task_id()
        if task_id is None:
            return
        task = tasks_repo.get(self.app.conn, task_id)

        def saved(values: dict | None) -> None:
            if values is not None:
                apply_task(self.app.conn, values, existing=task)
                self.refresh_data()

        self.app.push_screen(FormModal(task_form(task)), saved)

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_sync_programs(self) -> None:
        """Project towerkit files from $BOOKKIT_PROGRAM_ROOTS (colon-separated),
        then open the review queue for anything unlinked."""
        import os
        from pathlib import Path

        from ... import sync
        from ..widgets.link_review import LinkReview

        raw = os.environ.get("BOOKKIT_PROGRAM_ROOTS", "")
        roots = [Path(p).expanduser() for p in raw.split(":") if p]
        if not roots:
            self.notify(
                "set BOOKKIT_PROGRAM_ROOTS (colon-separated dirs) to sync", severity="warning"
            )
            return
        report = sync.project_all(self.app.conn, roots)
        self.notify(f"projected {len(report.projected)}, {len(report.needs_link)} need linking")
        if report.needs_link:
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

    def action_open_markets(self) -> None:
        from .markets import MarketsScreen

        self.app.push_screen(MarketsScreen())
