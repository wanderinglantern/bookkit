"""Book import (i on Today): point at a spreadsheet, review the staging
report, commit in one transaction. The CLI's `bookctl import book --dry-run`
prints the same report; committing lives here, where confirmation lives."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ...imports.fieldspec import BOOK_FIELDS
from ...imports.mappers.book import stage_book
from ...imports.readers import read_table
from ...imports.staging import StagedImport
from ...imports.tablemap import map_headers


class ImportScreen(ModalScreen):
    app: BookkitApp
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("ctrl+s", "commit", "Commit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._staged: StagedImport | None = None
        self._staged_path: Path | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("IMPORT BOOK (xlsx/csv)", classes="modal-title")
            yield Input(
                placeholder="path to spreadsheet — enter previews",
                id="import-path",
            )
            from textual_autocomplete import PathAutoComplete

            # enter must still preview even with the dropdown open
            yield PathAutoComplete("#import-path", prevent_default_enter=False)
            yield Static(
                "tip: bookctl template book book.xlsx writes a fill-in template",
                classes="hint",
            )
            yield Static("", id="import-preview")
            yield Static("enter previews · ctrl+s commits · esc cancels", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#import-path", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = Path(event.value).expanduser()
        preview = self.query_one("#import-preview", Static)
        self._staged = None
        try:
            table = read_table(path)
        except (ValueError, OSError) as exc:
            preview.update(str(exc))
            return
        from rich.text import Text

        mapping = map_headers(table.headers, BOOK_FIELDS)
        self._staged = stage_book(self.app.conn, table, mapping)
        self._staged_path = path
        preview.update(Text(self._staged.report()))

    def action_commit(self) -> None:
        from ...imports.commit import commit_book

        if self._staged is None or self._staged_path is None:
            self.notify("preview first (enter on the path)", severity="warning")
            return
        try:
            if read_table(self._staged_path).sha256 != self._staged.sha256:
                self.notify(
                    "file changed since the preview — press enter to re-preview",
                    severity="warning",
                )
                self._staged = None
                return
        except (ValueError, OSError) as exc:
            self.notify(f"cannot re-read file: {exc}", severity="error")
            return
        if not self._staged.ok:
            self.notify("fix the errors first (see preview)", severity="error")
            return
        try:
            result = commit_book(self.app.conn, self._staged, self.app.db_file())
        except Exception as exc:  # rollback already happened; never crash the TUI
            self.notify(f"commit failed (rolled back): {exc}", severity="error")
            return
        created = sum(result.created.values())
        updated = sum(result.updated.values())
        self.dismiss(None)
        self.app.notify(
            f"imported: {created} created, {updated} updated "
            f"(backup {result.backup.name})"
        )

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
