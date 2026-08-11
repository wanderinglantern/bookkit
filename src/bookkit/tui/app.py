"""The Textual app: screen stack, global keys, the one DB connection."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from .. import db


class BookkitApp(App):
    TITLE = "bookkit"
    CSS_PATH = "bookkit.tcss"
    BINDINGS = [
        Binding("slash", "global_search", "Search", key_display="/"),
        Binding("n", "quick_capture", "Log interaction"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, db_path: Path | str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._db_path = db_path
        self.conn = db.connect(db_path)

    def on_mount(self) -> None:
        from .screens.today import TodayScreen

        self.push_screen(TodayScreen())

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
        self.push_screen(SearchModal())

    def action_quick_capture(self) -> None:
        from .widgets.quick_capture import QuickCapture

        if self._modal_open():
            return
        org_id = getattr(self.screen, "current_org_id", None)
        self.push_screen(QuickCapture(default_org_id=org_id))

    def action_help(self) -> None:
        from .screens.help import HelpScreen

        self.push_screen(HelpScreen())

    def open_account(self, org_id: str) -> None:
        from .screens.account import AccountScreen

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
