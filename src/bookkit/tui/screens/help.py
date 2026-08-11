"""Help (?) — every key, in one place."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

HELP = """\
[b]everywhere[/b]
  /        search everything          n   log an interaction (quick capture)
  ?        this help                  u   undo last change
  esc      back / close               ctrl+q  quit

[b]lists[/b]
  j / k    move                       enter   open
  g / G    top / bottom               d       mark task done

[b]today screen[/b]
  b  book        c  renewal calendar
  p  pipeline    m  markets

[b]account screen[/b]
  p  mark contact primary (contacts tab)
  enter on a placement shows its tower · enter on a document opens it

[b]quick capture[/b]
  ctrl+s save · esc keeps a draft for next time
  dates accept: today · tomorrow · fri · +2w · 15 oct · 2026-10-15
  "follow up Tuesday" in a note offers to create the task
"""


class HelpScreen(ModalScreen):
    app: BookkitApp
    BINDINGS = [Binding("escape,question_mark", "dismiss_modal", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("KEYS", classes="modal-title")
            yield Static(HELP)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
