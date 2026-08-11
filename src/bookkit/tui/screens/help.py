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
  a        add (contextual)           e       edit selected

[b]today screen[/b]
  b  book        c  renewal calendar        a  new task
  p  pipeline    m  markets                 y  sync program files

[b]book screen[/b]
  a  new account · e  edit account · f  filter

[b]account screen[/b]
  a adds whatever the open tab holds: task (overview), contact,
  placement, opportunity, document; e edits the selected row
  (or the account itself). s sends a submission for the selected
  placement/opportunity; e on a submission records the market's
  response (quote / decline / bind).
  p  mark contact primary (contacts tab)
  enter on a placement shows its tower · enter on a document opens it

[b]markets screen[/b]
  a  new market · e  edit · in a market: a appetite, w underwriter

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
