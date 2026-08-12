"""Generic pick-one modal: a titled OptionList behind a type-to-filter input
that is focused on mount — open, type a few letters, enter. Dismisses with
the chosen id (None on escape)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class Picker(ModalScreen):
    # hug the content — a three-option pick must not open an 80%-tall box
    DEFAULT_CSS = """
    Picker .modal-box {
        height: auto;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        # the filter input keeps focus; arrows steer the list underneath
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    def __init__(self, title: str, options: list[tuple[str, str]]) -> None:
        """options: (label, id) pairs."""
        super().__init__()
        self.title_text = title
        self.choices = options

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static(self.title_text.upper(), classes="modal-title")
            yield Input(placeholder="type to filter…", id="picker-filter")
            yield OptionList(id="picker-options")
            yield Static("[b]enter[/b] select · [b]esc[/b] cancel", classes="hint")

    def on_mount(self) -> None:
        self._rebuild("")
        self.query_one("#picker-filter", Input).focus()

    def _rebuild(self, text: str) -> None:
        options = self.query_one("#picker-options", OptionList)
        options.clear_options()
        needle = text.strip().casefold()
        for label, option_id in self.choices:
            # "__"-prefixed ids are action rows ("create new master…"):
            # they survive any filter so typing a new name can't strand you
            if (
                not needle
                or needle in label.casefold()
                or (option_id or "").startswith("__")
            ):
                options.add_option(Option(label, id=option_id))
        if options.option_count:
            options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._rebuild(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        options = self.query_one("#picker-options", OptionList)
        if options.highlighted is not None:
            self.dismiss(options.get_option_at_index(options.highlighted).id)

    def action_cursor_down(self) -> None:
        self._move(1)

    def action_cursor_up(self) -> None:
        self._move(-1)

    def _move(self, delta: int) -> None:
        options = self.query_one("#picker-options", OptionList)
        if not options.option_count:
            return
        current = options.highlighted if options.highlighted is not None else 0
        options.highlighted = max(0, min(options.option_count - 1, current + delta))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
