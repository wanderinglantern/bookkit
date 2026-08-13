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
  ctrl+t   new task (attaches to the account you're viewing)
  ?        this help                  u   undo last change
  esc      back / close               ctrl+q  quit
  forms save in place: a refused save keeps the form open to fix

[b]lists[/b]
  j / k    move                       enter   open
  g / G    top / bottom               d       mark task done
  a        add (contextual)           e       edit selected
  D        delete the selected interaction (account tabs 1 and 3) — the way
           to remove a wrongly logged activity; soft, so u puts it back
  i        edit in cell (contacts, tasks): enter saves · tab saves + next
           cell · esc abandons · every save is one u-undoable change
  renewal tables count down PER LINE OF COVER — a line whose policy dies
  before the program period surfaces on its own clock (IM ◆ 70d over)

[b]navigator (home)[/b]
  the tree drills: attention → accounts → their placements/contacts/
  opportunities/tasks/projects; the right pane is a working table —
  a add · e edit · d done · r renew · l layer · enter opens the account
  the hop loop: enter (or tab) dives from the tree into the rows,
  e edits in place, esc hops back to the tree — no detours
  t  classic Today dashboard    b/c/p/m/w  book/calendar/pipeline/markets/team
  o  onboard (resume or start)  x  export open items workbook
  ,  setup (where program files live)

[b]today screen (t)[/b]
  b  book        c  renewal calendar        a  new task
  p  pipeline    m  markets                 y  sync program files
  w  team (who to go to for what)            i  import book (xlsx/csv)
  ,  setup (where program files live — first y opens it for you)

[b]book screen[/b]
  a  new account · e  edit account · f  filter

[b]calendar (c)[/b]
  cells: ░░ prospective  ▒▒ submitted  ▓▓ quoted  ██ bound  ×× lapsed
  ◆ overdue · current month in gold · enter opens the account

[b]account screen[/b]
  1–8 jump straight to a tab; the cursor lands on its rows, so
  j/k and e work immediately — each tab's hint line names its keys
  a adds whatever the open tab holds: task (overview), contact,
  placement, opportunity, document; e edits the selected row
  (or the account itself). s sends a submission for the selected
  placement/opportunity; e on a submission records the market's
  response (quote / decline / bind).
  p  mark contact primary (contacts tab)
  enter on a placement shows its tower · enter on a document opens it
  8  open items · i in-cell edit · x export

[b]markets screen[/b]
  a  new market · e  edit · N  nest under a master company (creates
     the master on the spot) · families render as an outline
  in a market: a appetite · w underwriter · i paste their signature

[b]placements tab[/b]
  j/k switch the previewed program · header names the selection
  r  renew into next period (file-backed placements clone next
     year's towerkit file, linked at birth)
  e  edit — dates/name write to the file, status/commission to bookkit
  l  edit a layer — the one under the cursor in the carriers table,
     straight to the form when there's only one, else a picker
  L  add a pending layer · o  open the file in towerkit's editor
  i  paste imports: contact/signature, program schedule, renewal terms
  t  create the towerkit file FROM this placement (nothing typed twice)
  w  assign a team member to this placement
     (on the Team screen, w assigns the selected member to an account)
  x  merge a duplicate into another placement
  binding a submission (e on it → bound) offers to put the market
  on a layer at its share — towerkit validation gates every write

[b]markets screen[/b]
  x  merge a duplicate market (its name becomes an alias)
  A  add a tower spelling as an alias for the selected market

[b]entry — type fast, it stores clean[/b]
  pickers filter as you type — ↑↓ steers, enter selects
  forms: ^s saves from any field · first field is focused on open
  dates accept: today · tomorrow · fri · +2w · 15 oct · 2026-10-15
  money accepts: 1.5m · 250k · 1,500,000
  emails, phones, URLs, LinkedIn handles are tidied on save
  ("312.555.0142" → "(312) 555-0142")

[b]quick capture[/b]
  ctrl+s save · esc keeps a draft for next time
  "follow up Tuesday" in a note offers to create the task

[b]sync review (y on today)[/b]
  steps through unlinked files, ambiguous placements, offered
  opportunities from proposed programs' unplaced (TBD) lines, and
  unknown carriers (alias a towerkit spelling to a market, or
  create the market)
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
