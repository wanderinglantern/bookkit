"""Generic paste-import modal: paste text, preview the staging report,
commit only when it's clean. Which flow it drives comes from the two
callbacks — the modal itself knows nothing about books or towers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...imports.staging import StagedImport
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea


class PasteImportModal(ModalScreen):
    app: BookkitApp
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("ctrl+r", "preview", "Preview"),  # ctrl+p is the command palette
        Binding("ctrl+s", "commit", "Commit"),
    ]

    def __init__(
        self,
        title: str,
        stage: Callable[[str], StagedImport],
        commit: Callable[[StagedImport], str],
    ) -> None:
        super().__init__()
        self._title = title
        self._stage = stage
        self._commit = commit
        self._staged: StagedImport | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static(self._title.upper(), classes="modal-title")
            yield TextArea(id="paste-text")
            yield Static("", id="paste-preview")
            yield Static(
                "ctrl+s previews, ctrl+s again commits · ctrl+r re-previews · esc cancels",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#paste-text", TextArea).focus()

    def action_preview(self) -> None:
        text = self.query_one("#paste-text", TextArea).text
        if not text.strip():
            self.notify("nothing pasted yet", severity="warning")
            return
        try:
            self._staged = self._stage(text)
        except Exception as exc:  # staging must never crash the app
            self.notify(f"could not stage: {exc}", severity="error")
            return
        self.query_one("#paste-preview", Static).update(self._staged.report())

    def action_commit(self) -> None:
        if self._staged is None:  # first ctrl+s stages; the second commits
            self.action_preview()
            if self._staged is not None:
                self.notify("review the preview, then ctrl+s again to commit")
            return
        if not self._staged.ok:
            self.notify("fix the errors first (see preview)", severity="error")
            return
        try:
            message = self._commit(self._staged)
        except Exception as exc:
            self.notify(f"commit failed: {exc}", severity="error")
            return
        self.dismiss(None)
        self.app.notify(message)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class ImportChooser(ModalScreen):
    """Pick which import flow to run; dismisses with the chosen option id."""

    app: BookkitApp
    BINDINGS = [Binding("escape", "dismiss_modal", "Cancel")]

    def __init__(self, options: list[tuple[str, str]]) -> None:
        super().__init__()
        self._options = options

    def compose(self) -> ComposeResult:
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        with VerticalScroll(classes="modal-box"):
            yield Static("IMPORT…", classes="modal-title")
            yield OptionList(
                *[Option(label, id=option_id) for option_id, label in self._options],
                id="import-options",
            )
            yield Static("enter picks · esc cancels", classes="hint")

    def on_mount(self) -> None:
        from textual.widgets import OptionList

        self.query_one("#import-options", OptionList).focus()

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
