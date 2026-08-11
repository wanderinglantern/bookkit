"""Generic pick-one modal: a titled OptionList that dismisses with the chosen
id (None on escape)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class Picker(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: list[tuple[str, str]]) -> None:
        """options: (label, id) pairs."""
        super().__init__()
        self.title_text = title
        self.choices = options

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static(self.title_text.upper(), classes="modal-title")
            yield OptionList(id="picker-options")
            yield Static("enter picks · esc cancels", classes="hint")

    def on_mount(self) -> None:
        options = self.query_one("#picker-options", OptionList)
        for label, option_id in self.choices:
            options.add_option(Option(label, id=option_id))
        options.focus()
        if self.choices:
            options.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
