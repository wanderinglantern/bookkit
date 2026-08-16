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

    _org_by_option: dict[str, str] = {}

    def on_input_changed(self, event: Input.Changed) -> None:
        results = self.query_one("#search-results", OptionList)
        results.clear_options()
        self._org_by_option.clear()
        if len(event.value) < 2:
            return
        hits = search_repo.search(self.app.conn, event.value, limit=20)
        current_kind = None
        for hit in hits:
            if hit.kind != current_kind:
                current_kind = hit.kind
                results.add_option(Option(f"— {hit.kind.upper()}S —", disabled=True))
            snippet = f"   {hit.snippet}" if hit.snippet else ""
            # keyed on entity_id, NOT org_id: two interactions at one account
            # share an org and Textual raises DuplicateID, which — firing from
            # a message handler — took the whole session down. The owning org
            # is what we dismiss with, so it is carried alongside.
            self._org_by_option[hit.entity_id] = hit.org_id
            results.add_option(
                Option(f"{hit.title}{snippet}", id=hit.entity_id)
            )

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
        # dismiss WITH the account and let the caller open it: opening from in
        # here ran while this modal was still on the stack, so open_account
        # could not tell it was already on an account screen (review F7)
        if event.option.id:
            org_id = self._org_by_option.get(event.option.id)
            if org_id:
                self.dismiss(org_id)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
