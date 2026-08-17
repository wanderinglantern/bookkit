"""The Textual app: screen stack, global keys, the one DB connection."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.screen import Screen

from .. import db
from .commands import RecordCommands, ScreenCommands
from .theme import BOOKKIT_THEME


class BookkitApp(App):
    TITLE = "bookkit"
    CSS_PATH = "bookkit.tcss"
    # the palette is already bound and already in the Footer; these are what
    # make it worth pressing (review F10)
    COMMANDS = App.COMMANDS | {RecordCommands, ScreenCommands}
    BINDINGS = [
        Binding("slash", "global_search", "Search", key_display="/"),
        Binding("n", "quick_capture", "Log interaction"),
        Binding("ctrl+t", "new_task", "Task", priority=True),
        # help.py lists u under "everywhere", and it was bound per screen —
        # Calendar, Markets, Team and Onboarding never got it. show=False so
        # the screens that DO name it in their footer stay the ones that do.
        Binding("u", "undo_last", "Undo", show=False),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        db_path: Path | str | None = None,
        open_org_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._db_path = db_path
        # `bookctl open <ref-or-name>` lands here: the Navigator is still
        # pushed underneath, so esc means what it always means (review F20)
        self._open_org_id = open_org_id
        self.conn = db.connect(db_path)

    def db_file(self) -> Path:
        """The on-disk database path — import commits snapshot it first."""
        return Path(self._db_path) if self._db_path else db.default_db_path()

    def on_mount(self) -> None:
        from .screens.navigator import NavigatorScreen

        self.register_theme(BOOKKIT_THEME)
        self.theme = "bookkit"
        self.push_screen(NavigatorScreen())
        if self._open_org_id:
            self.open_account(self._open_org_id)

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
        from ..forms.entities import apply_task, task_form
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

    def action_undo_last(self) -> None:
        self.show_undo_result()
        refresh = getattr(self.screen, "refresh_data", None)
        if refresh is not None:
            refresh()

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

    def crash_log_path(self) -> Path:
        """Where a failed action's traceback goes — beside the database, so it
        travels with the data rather than with the checkout."""
        return self.db_file().parent / "bookkit-errors.log"

    async def run_action(
        self,
        action,
        default_namespace=None,
        namespaces=None,
    ) -> bool:
        """Every keybinding routes through here, so this is where a failing
        action stops being fatal.

        Before this, a stale row key or a locked database on any un-guarded row
        action — `action_renew_row`, `action_edit_row`, `action_mark_primary` —
        dropped the whole session to a traceback, losing the screen stack, any
        open form and the quick-capture draft in progress (review F3). The
        per-site try/except guards remain the graceful path; this is the net
        under the ones nobody thought of.

        This covers actions. Message handlers are covered separately by
        `_guard_message_dispatch` at the bottom of this module — they used to
        reach Textual's fatal path, which is how three DuplicateID bugs killed
        whole sessions. The per-site stale-key guards remain the graceful path
        for both."""
        try:
            return await super().run_action(action, default_namespace, namespaces)
        except Exception as exc:  # noqa: BLE001 — the point is to catch anything
            self._report_action_failure(action, exc)
            return True

    def _report_action_failure(self, action, exc: Exception) -> None:
        """Say what broke, write the traceback down, and stay on the screen."""
        import traceback

        from ..db import utc_now

        try:
            path = self.crash_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n--- {utc_now()}  action={action!r}\n")
                traceback.print_exception(
                    type(exc), exc, exc.__traceback__, file=handle
                )
            where = f" · logged to {path.name}"
        except OSError:  # a failure to log must not become the failure
            where = ""
        message = str(exc) or type(exc).__name__
        self.notify(f"{message[:160]}{where}", severity="error", timeout=10)

    def run_palette_action(self, action: str) -> None:
        """Run a screen-level action chosen from the command palette.

        The palette can fire from any screen, including one that has no such
        binding, so these route through the app rather than through whatever
        happens to be on top. `go_home` pops back to the Navigator instead of
        pushing a second one."""
        from .screens.navigator import NavigatorScreen

        if action == "go_home":
            while len(self.screen_stack) > 2 and not isinstance(
                self.screen, NavigatorScreen
            ):
                self.pop_screen()
            return
        if action == "help":
            self.action_help()
            return
        if action == "undo_last":
            self.show_undo_result()
            refresh = getattr(self.screen, "refresh_data", None)
            if refresh is not None:
                refresh()
            return
        if action == "setup":
            from .widgets.settings import SettingsModal

            self.push_screen(SettingsModal())
            return
        screens = {
            "open_today": ("today", "TodayScreen"),
            "open_book": ("book", "BookScreen"),
            "open_calendar": ("calendar", "CalendarScreen"),
            "open_pipeline": ("pipeline", "PipelineScreen"),
            "open_markets": ("markets", "MarketsScreen"),
            "open_team": ("team", "TeamScreen"),
        }
        module_name, class_name = screens[action]
        module = __import__(
            f"bookkit.tui.screens.{module_name}", fromlist=[class_name]
        )
        self.push_screen(getattr(module, class_name)())

    def show_undo_result(self) -> None:
        from ..services import undo

        result = undo.undo_last(self.conn)
        if result is None:
            self.notify("nothing to undo", severity="warning")
        elif not result.applied:
            # a refusal is not a failure to report quietly: it names the field
            # that blocks it so the user's next move is obvious
            self.notify(result.description, severity="warning", timeout=10)
        else:
            self.notify(result.description)


def run(db_path: Path | str | None = None) -> None:
    BookkitApp(db_path).run()


_DISPATCH_GUARDED = False


def _guard_message_dispatch() -> None:
    """Make a raising message handler non-fatal, app-wide.

    `App.run_action` is the net under ACTIONS. Message handlers had none, and
    that is how three separate DuplicateID bugs — an OptionList keyed on a
    non-unique org_id, in search, team and market exposure — took whole
    sessions down: screen stack, open forms, the quick-capture draft.

    The catch has to sit INSIDE `_dispatch_message`, not in
    `App._handle_exception`. By the time Textual calls the latter, the pump
    that raised has already stopped, so the app survives with a widget that
    silently never processes another message again — worse than crashing,
    because it looks fine. Catching at dispatch keeps the loop alive.

    Patched once, at import, because the guard has to cover every MessagePump
    (screens, widgets, the app), not a base class we happen to own."""
    global _DISPATCH_GUARDED
    from textual.message_pump import MessagePump

    if _DISPATCH_GUARDED:
        return
    original = MessagePump._dispatch_message

    async def guarded(self, message):
        try:
            return await original(self, message)
        except Exception as exc:  # noqa: BLE001 — the point is to catch anything
            app = getattr(self, "app", None)
            report = getattr(app, "_report_action_failure", None)
            if report is None:
                raise
            report(f"handling {type(message).__name__}", exc)
            return None

    MessagePump._dispatch_message = guarded  # type: ignore[method-assign]
    _DISPATCH_GUARDED = True


_guard_message_dispatch()
