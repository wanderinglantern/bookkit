"""Help (?) — every key, in one place.

One `Static` held all of this until 2026-08-14: 160 rendered rows inside a
19-row viewport at 80 columns, laid out in two hand-aligned columns that only
line up when the body is wider than about 70 cells, which at 80 columns it
never is. Now it is a list of sections, each a `Collapsible`, and every line is
short enough to survive the narrowest terminal the app supports (review F13).

Keep lines at or under 46 cells. The box is widened in on_mount so there is
more room than that, but 46 is what the body gets if the box ever narrows
again, and short reference lines cost nothing. The test enforces it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Static

# (title, body). The first section starts open; the rest are one keypress away.
HELP_SECTIONS: list[tuple[str, str]] = [
    (
        "everywhere",
        """\
/        search everything
n        log an interaction (quick capture)
ctrl+t   new task, on the account you view
?        this help       u  undo last change
esc      back / close    ctrl+q  quit

Forms save in place: a refused save keeps the
form open so you can fix it.""",
    ),
    (
        "lists and tables",
        """\
j / k    move          enter  open
g / G    top / bottom  d      mark task done
a        add (contextual)
e        edit the selected row
i        edit in cell — contacts, tasks, items
           enter saves · tab saves + next
           esc abandons · one save, one undo
         On the account screen i is ONLY this;
         its paste-imports moved to I.
D        delete the selected interaction
           (account tabs 1 and 3), soft

Renewal tables count down PER LINE OF COVER.
A line whose policy dies before the programme
period gets its own clock: "IM ◆ 70d over".""",
    ),
    (
        "navigator — the home screen",
        """\
The tree drills: attention → accounts → their
placements, contacts, opportunities, tasks and
projects. The right pane is a working table.

a add · e edit · d done · r renew · l layer
enter opens the account

The hop loop: enter (or tab) dives from tree
into rows, e edits in place, esc hops back.

t  Today dashboard     b  book
c  renewal calendar    p  pipeline
m  markets             w  team
o  onboard (resume or start)
x  export open items workbook
,  setup (where program files live)

MCP CHANGES — what the assistant changed in
the last 14 days. enter shows before → after.
R reverts the highlighted change, refusing if
you edited the same fields since; f forces.""",
    ),
    (
        "account screen",
        """\
1–9 jump straight to a tab, and the cursor
lands on its rows, so j/k and e work at once.
Each tab's hint line names the keys that work
there, and says so when the tab is empty.

a  add whatever the open tab holds
e  edit the selected row, or the account
s  submission for the selected placement or
     opportunity
e  on a submission records the response
     (quote / decline / bind)
p  mark a contact primary (contacts tab)
x  export open items (merge on placements)
I  paste import        w  assign a colleague

enter on a placement shows its tower.
enter on a document opens the file.""",
    ),
    (
        "placements tab",
        """\
j/k switches the previewed programme; the
header names the selection.

r  renew into the next period — file-backed
     placements clone next year's towerkit
     file, linked at birth
e  edit — dates and name write to the file,
     status and commission to bookkit
l  edit the layer under the carriers cursor
L  add a pending layer
o  open the file in towerkit's editor
t  create the towerkit file FROM this
     placement (nothing is typed twice)
I  paste a schedule or renewal terms
w  assign a colleague to this placement
x  merge a duplicate into another placement

Binding a submission (e on it → bound) offers
to put the market on a layer at its share.
towerkit validation gates every write.""",
    ),
    (
        "requests tab (9)",
        """\
What a client still owes. The request list on
top, the selected one's items below; tab hops
between the two, and both answer the keys.

a  new request (top) · new item (bottom)
P  paste a whole emailed list — one line per
     item, numbering and bullets stripped
i  edit an item in its cell
e  edit the request, or the item, as a form
d  the item under the cursor came back,
     dated today

state column reads the request, not a count:
  N open · closed · withdrawn · no items yet

Withdrawn is a date in the form's "cancelled
on" field. It drops the request from the
chase queue and the client export — nothing
else, and it is reversible by clearing it.

An UNDATED request never reaches the 120-day
chase queue, but it is still owed: it shows
here and on the export. One with no items
reaches neither.""",
    ),
    (
        "book, calendar, pipeline",
        """\
book      a new account · e edit · f filter
calendar  ░░ prospective   ▒▒ submitted
          ▓▓ quoted        ██ bound
          ×× lapsed        ◆  overdue
          current month in gold
          enter opens the account
pipeline  h/l columns · ↑/↓ cards
          > advance · < mark lost · e edit
          enter opens the account · u undo""",
    ),
    (
        "markets and team",
        """\
markets   a new market · e edit
          x merge a duplicate
          N nest under a master company,
            creating the master on the spot
          A add a tower spelling as an alias
in a market
          a appetite · w underwriter
          i paste their signature
team      a add · e edit · i paste signature
          w assign to an account
          f who do I go to for…?""",
    ),
    (
        "today screen (t)",
        """\
b  book             c  renewal calendar
p  pipeline         m  markets
w  team             a  new task
y  sync program files
i  import a book (xlsx/csv)
,  setup — where program files live; the
     first y opens this for you

Below 100 columns the four panes stack into
one column and the screen scrolls, rather
than cropping every value to nothing.""",
    ),
    (
        "typing — type fast, it stores clean",
        """\
Pickers filter as you type; ↑↓ steers,
enter selects. Forms: ^s saves from any
field, and the first field is focused.

dates  today · fri · +2w · 2026-10-15
money  1.5m · 250k · 1,500,000

Emails, phones, URLs and LinkedIn handles are
tidied on save: "312.555.0142" becomes
"(312) 555-0142".

Vocabulary fields complete from what you have
typed before — a dropdown plus ghost text you
accept with →.""",
    ),
    (
        "quick capture and imports",
        """\
quick capture (n)
  ^s save · esc keeps a draft for next time
  "follow up Tuesday" in a note OFFERS to
  create the task, never creates it silently

sync review (y on Today)
  steps through unlinked files, ambiguous
  placements, opportunities offered from
  proposed programmes' unplaced (TBD) lines,
  and unknown carriers — alias a towerkit
  spelling to a market, or create the market.""",
    ),
]


class HelpScreen(ModalScreen):
    app: BookkitApp

    # NB width/height are set inline in on_mount, NOT here: bookkit.tcss is the
    # app-level stylesheet and outranks a screen's DEFAULT_CSS whatever the
    # selector specificity (measured: `HelpScreen .modal-box {width:96%}` still
    # rendered at the shared 70%). Only the rules below, which the shared file
    # does not set, take effect from here.
    DEFAULT_CSS = """
    HelpScreen Collapsible {
        border: none;
        padding: 0;
        width: 1fr;
    }
    HelpScreen .help-body {
        width: 1fr;
        padding: 0 0 1 0;
    }
    """

    BINDINGS = [Binding("escape,question_mark", "dismiss_modal", "Close")]

    def on_mount(self) -> None:
        # help is reference text, not a form: the shared 70% box left it 42
        # usable cells at 80 columns, narrower than any layout it documents
        box = self.query_one(".modal-box")
        box.styles.width = "96%"
        box.styles.max_width = 84
        box.styles.max_height = "92%"

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("KEYS", classes="modal-title")
            for index, (title, body) in enumerate(HELP_SECTIONS):
                with Collapsible(title=title, collapsed=index > 0):
                    yield Static(body, classes="help-body")
            yield Static(
                "[b]enter[/b] opens a section · [b]esc[/b] closes", classes="hint"
            )

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
