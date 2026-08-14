"""The Textual app: screen stack, global keys, the one DB connection."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.screen import Screen

from .. import db
from .theme import BOOKKIT_THEME


class BookkitApp(App):
    TITLE = "bookkit"
    CSS_PATH = "bookkit.tcss"
    BINDINGS = [
        Binding("slash", "global_search", "Search", key_display="/"),
        Binding("n", "quick_capture", "Log interaction"),
        Binding("ctrl+t", "new_task", "Task", priority=True),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, db_path: Path | str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._db_path = db_path
        self.conn = db.connect(db_path)

    def db_file(self) -> Path:
        """The on-disk database path — import commits snapshot it first."""
        return Path(self._db_path) if self._db_path else db.default_db_path()

    def on_mount(self) -> None:
        from .screens.navigator import NavigatorScreen

        self.register_theme(BOOKKIT_THEME)
        self.theme = "bookkit"
        self.push_screen(NavigatorScreen())

    def on_unmount(self) -> None:
        self.conn.close()

    # --- global actions -------------------------------------------------------

    def _modal_open(self) -> bool:
        from textual.screen import ModalScreen

        return isinstance(self.screen, ModalScreen)

    def action_global_search(self) -> None:
        from .screens.search import SearchModal

        if self._modal_open():
            return

        def opened(org_id: str | None) -> None:
            # the modal dismisses with the account to open and the CALLER opens
            # it, so the modal is off the stack before open_account decides
            # whether to switch or push (review F7)
            if org_id:
                self.open_account(org_id)

        self.push_screen(SearchModal(), opened)

    def action_quick_capture(self) -> None:
        from .widgets.quick_capture import QuickCapture

        if self._modal_open():
            return
        org_id = getattr(self.screen, "current_org_id", None)
        self.push_screen(QuickCapture(default_org_id=org_id))

    def action_new_task(self) -> None:
        """ctrl+t anywhere: a task, attached to the client you're looking at."""
        from .widgets.entity_forms import apply_task, task_form
        from .widgets.forms import FormModal

        if self._modal_open():
            return
        default_org_id = getattr(self.screen, "current_org_id", None)
        origin = self.screen

        def commit(values: dict) -> str | None:
            apply_task(self.conn, values)
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.notify("task saved")
                refresh = getattr(origin, "refresh_data", None)
                if refresh is not None:
                    refresh()

        self.push_screen(
            FormModal(
                task_form(conn=self.conn, default_org_id=default_org_id),
                commit=commit,
            ),
            done,
        )

    def action_help(self) -> None:
        from .screens.help import HelpScreen

        self.push_screen(HelpScreen())

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Everything the palette offers EXCEPT "Theme".

        bookkit is deliberately one warm dark palette (see tui/theme.py), and
        that palette is baked into Rich markup in the table cells — switching
        theme repaints the chrome and leaves every status word, glyph and
        separator on the old colours, with FG-styled cells landing invisible on
        a light ground. Offering the command was the bug (review F11)."""
        for command in super().get_system_commands(screen):
            if command.title != "Theme":
                yield command

    def open_account(self, org_id: str) -> None:
        """Open a client. Jumping from one account straight to another REPLACES
        it rather than burying it: `/` is the fast path between clients, and
        pushing each one left esc walking back through every account visited
        that session, re-running a full refresh on each (review F7)."""
        from .screens.account import AccountScreen

        if isinstance(self.screen, AccountScreen):
            self.switch_screen(AccountScreen(org_id))
        else:
            self.push_screen(AccountScreen(org_id))

    def show_undo_result(self) -> None:
        from ..services import undo

        result = undo.undo_last(self.conn)
        if result is None:
            self.notify("nothing to undo", severity="warning")
        else:
            self.notify(f"undid {result.description}")


def run(db_path: Path | str | None = None) -> None:
    BookkitApp(db_path).run()
