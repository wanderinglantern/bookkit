"""Review queue for unlinked towerkit files (§5.2): the user confirms every
file ↔ account link; fuzzy matches are suggestions only."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ... import sync


class LinkReview(ModalScreen):
    app: BookkitApp
    """Steps through a SyncReport's needs_link entries one file at a time."""

    BINDINGS = [Binding("escape", "skip", "Skip this file")]

    def __init__(self, report: sync.SyncReport) -> None:
        super().__init__()
        self.queue = list(report.needs_link)
        self.report = report

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("LINK PROGRAM FILE", classes="modal-title")
            yield Static(id="lr-file")
            yield OptionList(id="lr-candidates")
            yield Static("enter confirms the link · esc skips the file", classes="hint")

    def on_mount(self) -> None:
        self._show_next()

    def _show_next(self) -> None:
        if not self.queue:
            self.dismiss(None)
            return
        suggestion = self.queue[0]
        self.query_one("#lr-file", Static).update(
            f"{suggestion.path}\ninsured: [b]{suggestion.insured}[/b]"
        )
        options = self.query_one("#lr-candidates", OptionList)
        options.clear_options()
        for org, score in suggestion.candidates:
            options.add_option(Option(f"{org.name}  ({score:.0f}% match)", id=org.id))
        if not suggestion.candidates:
            options.add_option(Option("(no candidates — create the account first)", disabled=True))
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option.id:
            return
        suggestion = self.queue.pop(0)
        diags = sync.confirm_link(self.app.conn, Path(suggestion.path), event.option.id)
        if diags.ok:
            self.notify(f"linked and projected {suggestion.path.name}")
        else:
            self.notify(f"linked, but projection failed: {diags.errors[0]}", severity="error")
        self._show_next()

    def action_skip(self) -> None:
        if self.queue:
            self.queue.pop(0)
        self._show_next()
