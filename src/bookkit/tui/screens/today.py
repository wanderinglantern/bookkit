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
from ...services import book, renewals, sla, staleness
from .. import theme
from ..theme import dash, date_text, days_text, money_text, right
from ..widgets.tables import ListTable

# Below this width the 2x2 grid gives each pane ~36 cells for up to seven
# columns, which is where an overdue "-345" used to truncate to "-". One
# column and a scroll beats four unreadable quadrants (review F4).
TWO_COLUMN_MIN_WIDTH = 100
# rows per pane when stacked: title, header, and ~9 rows of work
PANE_HEIGHT = 12


def _cover(item: renewals.RenewalItem) -> Text:
    """What renews, in one cell: the lines of cover when the placement is
    linked to a program file, else the program standing in for them.

    The lines lead, always. The cell is the last column of a pane narrower
    than its content, so whatever is clipped is clipped off the END — and the
    field that must never be clipped is the one CLAUDE.md makes mandatory.

    The stand-in is the program label with a trailing "Program" dropped:
    _program_label already takes the year off so renewals of the same cover
    read alike, and under a `cover` header the word "Program" is the header's
    job, not the cell's. "2025 Casualty Program" → "Casualty", which sits in
    the same register as "GL, AL, IM" instead of a file title. It is DIM, so
    a stand-in never reads as projected data.

    With neither — no projected lines and no program name — the cell is an em
    dash, not blank. The pre-fix code said `item.lines or dash()` and that half
    of it was right: an empty last column reads as a rendering fault, while a
    dash says "this row has nothing to put here", and the dash is free."""
    if item.lines:
        return Text(item.lines)
    label = book._program_label(item.placement.program_name)
    head = label[: -len("program")].strip() if label.lower().endswith("program") else label
    stand_in = head or label
    return Text(stand_in, style=theme.DIM) if stand_in else dash()


class TodayScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        # screen jumps live in the palette and in ? — the footer is one
        # row and the ROW ACTIONS are what a user needs named there
        # every other working screen pops on escape; Today was the exception,
        # so `t` was a one-way door with no on-screen way back
        Binding("escape", "app.pop_screen", "Back"),
        Binding("b", "open_book", "Book", show=False),
        Binding("c", "open_calendar", "Calendar", show=False),
        Binding("p", "open_pipeline", "Pipeline", show=False),
        Binding("m", "open_markets", "Markets", show=False),
        Binding("w", "open_team", "Team", show=False),
        Binding("a", "new_task", "New task"),
        Binding("e", "edit_task", "Edit task"),
        Binding("d", "task_done", "Done (task)"),
        Binding("u", "undo", "Undo"),
        Binding("i", "import_book", "Import"),
        # esc Back earned its footer slot back (Today was a one-way door);
        # sync is a maintenance action that lives in ? and the palette
        Binding("y", "sync_programs", "Sync programs", show=False),
        Binding("comma", "settings", "Setup", key_display=",", show=False),
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
        # FOUR columns, not seven. This pane is 66 cells wide at the 140-column
        # floor and seven columns need 105, so three of them were painted past
        # the right-hand edge — and `lines` was one of them, the single field
        # CLAUDE.md calls mandatory here ("program name alone is not enough
        # context"). Something had to give, and the footer's precedent is
        # demote one, don't raise the ceiling:
        #   premium goes first. renewals.upcoming() returns every non-lapsed
        #   placement, so this cell is the EXPIRING placement's premium whatever
        #   its status — and the status column that would qualify it does not
        #   fit either. Money that cannot say whether it exists is the exact
        #   column the book screen already had taken off it.
        #   status goes with it: every row here is by definition a live renewal;
        #   where it sits in its lifecycle is the account screen's answer.
        #   program is not deleted, it is DEMOTED into `cover` — the navigator
        #   card's own rule, "without projected lines the programme has to stand
        #   in for it, and then it must not ALSO be printed as its own column".
        # Anything cut off the right of `cover` is therefore the stand-in's
        # tail, never a line of cover.
        renewals_table.add_columns("renews", right("due in"), "account", "cover")
        for item in renewals.upcoming(conn, today, days=120):
            renewals_table.add_row(
                # renewal_on, NOT period_to: days_remaining counts to the
                # earliest LINE end, so printing the program end beside it put
                # a future date under a red "70d over" (the Navigator has
                # always done this correctly)
                date_text(item.renewal_on or item.placement.period_to,
                          item.days_remaining),
                days_text(item.days_remaining),
                item.org.name,
                _cover(item),
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
                # the line first, the project after it — same order as the
                # navigator's needs table, and the same reason: the line of
                # cover is what the date belongs to
                f"{need['line']} — {need['project_name']} (need)",
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
                # the underwriter, when one is recorded: the AE review's
                # complaint was that this pane names "Travelers", which you
                # cannot email. A market with no named person still renders
                # exactly as it always did — the fact is added, never faked.
                theme.market_text(late.market.name, late.underwriter_name),
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
        from ...forms.entities import apply_task, task_form
        from ..widgets.forms import FormModal

        def commit(values: dict) -> str | None:
            apply_task(self.app.conn, values)
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data()

        self.app.push_screen(FormModal(task_form(conn=self.app.conn), commit=commit), done)

    def action_edit_task(self) -> None:
        from ...forms.entities import apply_task, task_form
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
