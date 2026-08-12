"""Global search (/): FTS5 across accounts, contacts, interactions, grouped
by type; enter jumps to the owning account."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...repo import search as search_repo


class SearchModal(ModalScreen):
    app: BookkitApp
    # hug the content — an empty result list must not leave an 80%-tall box
    DEFAULT_CSS = """
    SearchModal .modal-box {
        height: auto;
    }
    """
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        # from the input, down drops straight into the result list
        Binding("down", "focus_results", "Results", show=False),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("SEARCH EVERYTHING", classes="modal-title")
            yield Input(placeholder="accounts, contacts, notes…", id="search-input")
            yield OptionList(id="search-results")
            yield Static(
                "[b]enter[/b] opens the account · [b]esc[/b] closes", classes="hint"
            )

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        results = self.query_one("#search-results", OptionList)
        results.clear_options()
        if len(event.value) < 2:
            return
        hits = search_repo.search(self.app.conn, event.value, limit=20)
        current_kind = None
        for hit in hits:
            if hit.kind != current_kind:
                current_kind = hit.kind
                results.add_option(Option(f"— {hit.kind.upper()}S —", disabled=True))
            snippet = f"   {hit.snippet}" if hit.snippet else ""
            results.add_option(Option(f"{hit.title}{snippet}", id=f"{hit.kind}:{hit.org_id}"))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_focus_results()

    def action_focus_results(self) -> None:
        results = self.query_one("#search-results", OptionList)
        if results.option_count:
            results.focus()
            enabled = (
                i
                for i in range(results.option_count)
                if not results.get_option_at_index(i).disabled
            )
            results.highlighted = next(enabled, None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            _, org_id = event.option.id.split(":", 1)
            self.dismiss(None)
            self.app.open_account(org_id)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
