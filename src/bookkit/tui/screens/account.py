"""Account detail — where most time is spent. Header + tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...models import Submission, Task
    from ..app import BookkitApp

import subprocess
import sys
from datetime import date
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from ...dates import days_until
from ...forms import inline as forms_inline
from ...forms.spec import Field
from ...money import format_cents_compact
from ...repo import (
    assignees,
    contacts,
    documents,
    interactions,
    opportunities,
    orgs,
    placements,
    projection,
    submissions,
)
from ...repo import rfi as rfi_repo
from ...repo import tasks as tasks_repo
from ...services import book
from ...services import quotes as quotes_svc
from .. import theme
from ..theme import dash, date_text, days_text, money_text, right, status_text
from ..widgets.entity_actions import batched_write as _batched
from ..widgets.inline_edit import InlineTable
from ..widgets.tables import (
    ListTable,
    detail_cell,
    grouped_by_category,
    rfi_asker_cell,
    rfi_due_cell,
    rfi_state_cell,
    task_detail_cell,
    task_detail_wrapped,
)
from ..widgets.tower_preview import TowerPreview
from .navigator import task_inline


def _pretty(value: str) -> str:
    """snake_case vocab reads as words in cells ('site_visit' → 'site visit')."""
    return value.replace("_", " ")


def _status(value: str) -> Text:
    """status_text, plus the snake_case prettify — style keyed on the raw value."""
    return Text(_pretty(value), style=theme.STATUS_STYLES.get(value, theme.FG))


# the visible home for each tab's hidden single-letter keys — only the keys
# that actually work on that tab (help lists them too)
TAB_HINTS: dict[str, str] = {
    "tab-overview": (
        "[b]a[/b] task · [b]e[/b] edit task/team/account · [b]d[/b] task done · "
        "[b]D[/b] delete interaction / team / contact row · [b]w[/b] assign team · "
        "[b]I[/b] paste import · [b]u[/b] undo"
    ),
    "tab-contacts": (
        "[b]a[/b] add · [b]e[/b] edit · [b]p[/b] make primary · "
        "[b]D[/b] remove contact · [b]I[/b] paste import · [b]w[/b] assign team · "
        "[b]u[/b] undo"
    ),
    "tab-interactions": (
        "[b]a[/b] log · [b]e[/b] edit this interaction · [b]D[/b] delete · "
        "[b]I[/b] paste import · [b]u[/b] undo — the note is below"
    ),
    "tab-placements": (
        "[b]a[/b] add · [b]e[/b] edit · [b]s[/b] submission · [b]r[/b] renew · "
        "[b]l[/b]/[b]L[/b] layer · [b]o[/b] towerkit · [b]t[/b] tower file · "
        "[b]I[/b] paste · [b]w[/b] assign · [b]x[/b] merge"
    ),
    "tab-projects": (
        "[b]a[/b] add project/need · [b]e[/b] edit · "
        "[b]o[/b] need → opportunity · [b]tab[/b] projects ⇄ needs"
    ),
    "tab-pipeline": (
        "[b]a[/b] opportunity / subjectivity · [b]e[/b] edit opp / response / "
        "subjectivity · [b]s[/b] submission · "
        "[b]tab[/b] opps ⇄ submissions ⇄ subjectivities"
    ),
    "tab-documents": "[b]a[/b] add · [b]enter[/b] opens the file · [b]e[/b] edit account",
    "tab-open-items": (
        "[b]i[/b] edit in cell · [b]a[/b] task · [b]e[/b] edit form · "
        "[b]d[/b] done · [b]x[/b] export · needs/submissions edit in their tabs"
    ),
    # no "u undo" here: d is two field writes and a paste is a batch of
    # creates, so neither is a single undoable mutation (see services/rfi)
    "tab-requests": (
        "[b]i[/b] edit in cell · [b]a[/b] add · [b]P[/b] paste list · "
        "[b]e[/b] edit form · [b]d[/b] received · [b]tab[/b] requests ⇄ items"
    ),
}

# the items datasheet's editable columns — the same Field parsers the modal
# form uses, so an in-cell date reads "next fri" exactly as the form does.
#
# status and received-on are deliberately NOT here: `d` owns that transition
# (services.rfi.mark_received sets both together), and two ways to make one
# state change is the coupling to avoid. The response IS here — an answer you
# cannot see on the datasheet defeats the point of the tab.
#
# `detail` shows but is NOT inline-editable: it is a textarea in the form and
# the cell editor is one line, which would flatten a multi-line note on save.
# Showing it at all is the point — it used to be typed into the form and then
# be invisible everywhere in the TUI, reachable only through the export.
# the Field list itself lives in forms/inline.py — see the comment there.
RFI_ITEM_INLINE = dict(zip((0, 3, 4, 7), forms_inline.RFI_ITEM_FIELDS, strict=True))

# narrower than this and a wrapped detail column is not worth the rows it
# costs: the note comes out three characters wide, three lines tall, saying
# nothing. Same call the placements tab makes at PREVIEW_MIN_WIDTH — stand
# down rather than render mush.
OV_DETAIL_MIN = 40
OV_DETAIL_MAX = 60


def _ov_detail_width(tasks: list[Task], total: int) -> int | None:
    """Width for the overview task table's detail column, or None to leave it
    as one clipped line.

    The other five columns are auto-width, so they get measured off the data
    and taken off the screen width first; the detail column wraps into what is
    left, and only if that is worth wrapping into. Measure, then divide — a
    percentage cannot know that one account's task titles are twice as long as
    another's. A long description therefore wins the space, which is the right
    order: it is the field that was filled in.

    Note it costs nothing when nobody wrote a note — the column stays auto-width
    and the table renders exactly as it did before."""
    if not any(t.detail for t in tasks):
        return None

    def widest(pick: Callable[[Task], str]) -> int:
        return max((len(pick(t)) for t in tasks), default=1)

    lead = (
        10  # due, a date
        + 6  # due in
        + widest(lambda t: t.title)
        # the RENDERED label, not the raw value: an Internal task's cell
        # carries a badge, and measuring t.category alone overflows the row
        # by its width (theme.category_text)
        + widest(lambda t: theme.category_text(t.category).plain)
        + widest(lambda t: t.description or "—")
        + 6 * 2  # cell padding, all six columns
        + 2  # the pane's own margin
    )
    spare = total - lead
    return min(spare, OV_DETAIL_MAX) if spare >= OV_DETAIL_MIN else None


# tabs whose `a` creates a row, so an empty one can name the way out of itself
ADDABLE_TABS = frozenset(
    {
        "tab-contacts", "tab-placements", "tab-projects", "tab-pipeline",
        "tab-documents", "tab-open-items", "tab-requests", "tab-overview",
    }
)

# where the cursor lands when a tab opens — j/k and the row keys work at once.
# This is a FOCUS map and nothing else: see TAB_EMPTY_TABLES for what decides
# whether a tab is empty.
TAB_TABLES: dict[str, str] = {
    "tab-overview": "ov-tasks",
    "tab-contacts": "contacts-table",
    "tab-interactions": "interactions-table",
    "tab-placements": "placements-table",
    "tab-projects": "projects-table",
    "tab-pipeline": "pipeline-opps",
    "tab-documents": "documents-table",
    "tab-open-items": "open-items-table",
    "tab-requests": "rfi-requests",
}

# every table a tab actually shows — what the empty state has to be derived
# from. Five tabs stack more than one, and the hint used TAB_TABLES, which
# names only the table the CURSOR lands on: the Overview tab printed
# "empty — a adds the first row" over a team, a contact list, an interaction
# log and an opportunity list, because the account happened to have no open
# task. A tab is empty when everything on it is; a tab with anything on it is
# not empty, whatever the cursor is sitting in.
TAB_EMPTY_TABLES: dict[str, tuple[str, ...]] = {
    "tab-overview": (
        "ov-team", "ov-contacts", "ov-interactions", "ov-tasks", "ov-opps",
    ),
    "tab-projects": ("projects-table", "needs-table"),
    "tab-pipeline": ("pipeline-opps", "pipeline-subs"),
    "tab-open-items": ("open-items-table", "open-items-context"),
    "tab-requests": ("rfi-requests", "rfi-items"),
}


class ConfirmRenew(ModalScreen):
    """One look before a renew: what gets created, including the cloned file."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y,enter", "accept", "Yes"),
    ]

    def __init__(self, placement) -> None:
        super().__init__()
        self.placement = placement

    def compose(self) -> ComposeResult:
        p = self.placement
        file_note = (
            f"\n+ clone {Path(p.program_path).name} to next year's file, linked at birth"
            if p.program_path
            else ""
        )
        with VerticalScroll(classes="modal-box"):
            yield Static("RENEW PLACEMENT", classes="modal-title")
            yield Static(
                f"{p.ref} {p.program_name}\ncreate the {p.period_to} → next-year period "
                f"as prospective{file_note}"
            )
            yield Static("y / enter renew · n / esc cancel", classes="hint")

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)


class ConfirmDeleteInteraction(ModalScreen):
    """One look before removing a logged interaction. Deleting the relationship
    log is how a wrong entry gets corrected — most often one the MCP server
    logged against the wrong account — so it names what is going and says out
    loud that the delete is soft.

    The consequences come from services.interactions.consequences and are
    asserted string-for-string against the web confirm's own render
    (tests/test_web_writes.py), the way ConfirmRemoveContact's already are:
    two surfaces that each compose their own sentences end up promising
    different things about one write."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y,enter", "accept", "Yes"),
    ]

    def __init__(self, interaction, notes: list[str]) -> None:
        super().__init__()
        self.interaction = interaction
        # services.interactions.consequences — the same list the web shows
        self.notes = notes

    def compose(self) -> ComposeResult:
        i = self.interaction
        with VerticalScroll(classes="modal-box"):
            yield Static("DELETE INTERACTION", classes="modal-title")
            yield Static(f"{i.occurred_on}  {_pretty(i.type)}\n{i.subject}")
            for note in self.notes:
                yield Static(f"· {note}")
            yield Static("y / enter delete · n / esc cancel", classes="hint")

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)


class ConfirmRemoveContact(ModalScreen):
    """One look before taking a contact off the account.

    It names the person AND what survives, because the two things a user
    cannot see from the row vanishing are the ones that matter: the
    interactions they attended keep their record, and if they were the primary
    the account is left with NO primary rather than someone promoted behind
    the user's back (services/contacts.py owns that rule).

    The notes come from services.contacts.consequences and are asserted
    string-for-string against the web confirm's own render
    (tests/test_contact_remove.py) — deleting this loop used to leave the suite
    green."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y,enter", "accept", "Yes"),
    ]

    def __init__(self, contact, notes: list[str]) -> None:
        super().__init__()
        self.contact = contact
        # services.contacts.consequences — the same list the web confirm shows
        self.notes = notes

    def compose(self) -> ComposeResult:
        c = self.contact
        detail = " · ".join(
            part for part in (c.title or "", c.role or "", c.email or "") if part
        )
        with VerticalScroll(classes="modal-box"):
            yield Static("REMOVE CONTACT", classes="modal-title")
            yield Static(f"[b]{c.name}[/b]" + (f"\n{detail}" if detail else ""))
            for note in self.notes:
                yield Static(f"· {note}", classes="hint")
            yield Static("y / enter remove · n / esc cancel · undoable with u",
                         classes="hint")

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)


class ConfirmRemoveAssignment(ModalScreen):
    """One look before taking a colleague off this account.

    The prompt names the LINE and the SCOPE, not just the person, because one
    colleague routinely holds several assignments here — a row per line of
    cover, or one per placement. "Remove Rosa Delgado?" is unanswerable when
    two of her rows are on screen; "Rosa Delgado · casualty · account" is the
    only version you can say yes to safely."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y,enter", "accept", "Yes"),
    ]

    def __init__(self, row) -> None:
        super().__init__()
        self.row = row

    def compose(self) -> ComposeResult:
        row = self.row
        scope = (
            f"{row['placement_ref']} {row['program_name']}"
            if row["placement_ref"] else "this account"
        )
        detail = " · ".join(
            part for part in (
                _pretty(row["role"]) if row["role"] else "",
                row["lines"] or "no lines recorded",
                scope,
            ) if part
        )
        with VerticalScroll(classes="modal-box"):
            yield Static("REMOVE FROM TEAM", classes="modal-title")
            yield Static(f"[b]{row['member_name']}[/b]\n{detail}")
            yield Static(
                "this removes the one assignment, not the colleague",
                classes="hint",
            )
            yield Static("y / enter remove · n / esc cancel · undoable with u",
                         classes="hint")

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)


class ConfirmProgramRemoval(ModalScreen):
    """One look before a program-file removal — a market seat, or a layer.
    The body names the blast radius, the same facts the web's in-place
    confirms state for the same writes (phase 2, 2026-08-19)."""

    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y,enter", "accept", "Yes"),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static(self._title, classes="modal-title")
            yield Static(self._body)
            yield Static("y / enter remove · n / esc cancel · R reverts", classes="hint")

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)


class MergePicker(ModalScreen):
    """Pick which placement a duplicate merges into."""

    app: BookkitApp
    BINDINGS = [Binding("escape", "decline", "Cancel")]

    def __init__(self, source, targets) -> None:
        super().__init__()
        self.source = source
        self.targets = targets

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("MERGE PLACEMENT", classes="modal-title")
            yield Static(
                f"merge [b]{self.source.ref} {self.source.program_name}[/b] "
                f"({self.source.period_from}→{self.source.period_to}) into:"
            )
            yield OptionList(id="merge-targets")
            yield Static("enter merges · esc cancels · undoable with u", classes="hint")

    def on_mount(self) -> None:
        option_list = self.query_one("#merge-targets", OptionList)
        for p in self.targets:
            linked = " · file-linked" if p.program_path else ""
            option_list.add_option(
                Option(
                    f"{p.ref}  {p.program_name}  {p.period_from}→{p.period_to}"
                    f"  [{p.status}]{linked}",
                    id=p.id,
                )
            )
        option_list.focus()
        option_list.highlighted = 0

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_decline(self) -> None:
        self.dismiss(None)


class AccountScreen(Screen):
    app: BookkitApp

    # screen-local polish only; anything bookkit.tcss already styles is set
    # inline in on_mount instead (shared-file rules would win over these)
    DEFAULT_CSS = """
    AccountScreen #tab-hint {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $panel;
    }
    AccountScreen .pane-title {
        color: $text-muted;
        text-style: bold;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add_here", "Add (this tab)"),
        Binding("e", "edit_here", "Edit"),
        # these are named by the per-tab hint line one row above the
        # footer, so the footer spends its 140 columns on the keys
        # nothing else advertises
        Binding("s", "new_submission", "Submission", show=False),
        Binding("r", "renew_placement", "Renew", show=False),
        Binding("l", "edit_layer", "Layer", show=False),
        Binding("L", "add_layer", "Add layer", show=False),
        Binding("o", "open_towerkit", "Open in towerkit", show=False),
        Binding("w", "assign_team", "Assign team", show=False),
        Binding("t", "scaffold_tower", "Tower file", show=False),
        # x is screen-wide (Grant's call 2026-08-13): the placements tab still
        # merges under it (unchanged flow), every other tab exports open items —
        # see action_export_open_items
        Binding("x", "export_open_items", "Export / merge"),
        # I, not i: `i` is "edit in cell" and InlineTable binds it at WIDGET
        # level, so a screen-level `i` silently won on every table that is not
        # inline-editable — landing on the requests tab and pressing the key
        # its own hint advertised opened the paste-import chooser instead
        # (Grant's call 2026-08-14). The rarer, heavier flow takes shift, as
        # D, L and P already do.
        # show=False because the per-tab hint line names it and the footer has
        # to fit 140 columns (tests/test_layout.py).
        Binding("I", "import_here", "Import (paste)", show=False),
        Binding("d", "task_done", "Done (task)", show=False),
        # D is delete, taking the shift key as L and P do — d is already
        # "done", and the destructive sibling should never be one keystroke
        # away from the harmless one. It means "remove the selected row",
        # resolved by which table has focus (see action_delete_row).
        Binding("D", "delete_row", "Delete / drop row", show=False),
        Binding("p", "mark_primary", "Primary (contact)", show=False),
        # p is taken; paste-a-litany takes the shift key, as L does for layers
        Binding("P", "paste_items", "Paste items", show=False),
        Binding("u", "undo", "Undo"),
        # tabs answer to their number — no reaching for the tab bar
        Binding("1", "show_tab('tab-overview')", "Overview", show=False),
        Binding("2", "show_tab('tab-contacts')", "Contacts", show=False),
        Binding("3", "show_tab('tab-interactions')", "Interactions", show=False),
        Binding("4", "show_tab('tab-placements')", "Programs", show=False),
        Binding("5", "show_tab('tab-projects')", "Projects", show=False),
        Binding("6", "show_tab('tab-pipeline')", "Pipeline", show=False),
        Binding("7", "show_tab('tab-documents')", "Documents", show=False),
        Binding("8", "show_tab('tab-open-items')", "Open items", show=False),
        Binding("9", "show_tab('tab-requests')", "Requests", show=False),
    ]

    def __init__(self, org_id: str) -> None:
        super().__init__()
        self.current_org_id = org_id
        self._refresh_pending = False  # a refresh deferred while a cell edit is open
        self._rfi_request_id: str | None = None  # master row of the requests tab
        # master row of the pipeline tab's submissions table, so the
        # subjectivities detail only rebuilds when the cursor really moves
        self._pipeline_submission_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="account-header")
        with TabbedContent():
            with TabPane("1 Overview", id="tab-overview"):
                with VerticalScroll():
                    yield Static("TEAM", classes="pane-title")
                    yield ListTable(id="ov-team")
                    yield Static("KEY CONTACTS", classes="pane-title")
                    yield ListTable(id="ov-contacts")
                    yield Static("RECENT INTERACTIONS", classes="pane-title")
                    yield ListTable(id="ov-interactions")
                    yield Static("OPEN TASKS", classes="pane-title")
                    yield ListTable(id="ov-tasks")
                    yield Static("OPEN OPPORTUNITIES", classes="pane-title")
                    yield ListTable(id="ov-opps")
            with TabPane("2 Contacts", id="tab-contacts"):
                yield ListTable(id="contacts-table")
            with TabPane("3 Interactions", id="tab-interactions"):
                # master/detail, the same shape the requests tab uses: the log
                # is the index, the note is the point. Before this the body was
                # stored and never shown anywhere (review F33).
                with Vertical():
                    yield ListTable(id="interactions-table")
                    with VerticalScroll(id="interaction-detail-pane"):
                        yield Static(id="interaction-detail")
            # "Programs" to the user, tab-placements in code (D3, 2026-08-19)
            with TabPane("4 Programs", id="tab-placements"):
                with Horizontal():
                    with Vertical(id="placement-side"):
                        yield ListTable(id="placements-table")
                        yield ListTable(id="carriers-table")
                        yield Static(id="sync-state")
                    yield TowerPreview(id="tower-preview")
            with TabPane("5 Projects", id="tab-projects"):
                with Vertical():
                    yield ListTable(id="projects-table")
                    yield ListTable(id="needs-table")
            with TabPane("6 Pipeline", id="tab-pipeline"):
                with VerticalScroll():
                    yield Static("OPPORTUNITIES", classes="pane-title")
                    yield ListTable(id="pipeline-opps")
                    yield Static("SUBMISSIONS", classes="pane-title")
                    yield ListTable(id="pipeline-subs")
                    # master/detail, the shape the projects and requests tabs
                    # already use: the submission list is the index, the
                    # subjectivities are the work between quote and bind.
                    yield Static(id="pipeline-subj-title", classes="pane-title")
                    yield ListTable(id="pipeline-subjs")
            with TabPane("7 Documents", id="tab-documents"):
                yield ListTable(id="documents-table")
            with TabPane("8 Open items", id="tab-open-items"):
                with Vertical():
                    yield InlineTable(id="open-items-table")
                    yield Static(
                        "other open items — edit in their tabs", classes="pane-title"
                    )
                    yield ListTable(id="open-items-context")
            with TabPane("9 Requests", id="tab-requests"):
                with Vertical():
                    yield ListTable(id="rfi-requests")
                    yield Static(id="rfi-hint", classes="pane-title")
                    yield InlineTable(id="rfi-items")
        yield Static(id="tab-hint")
        yield Footer()

    def on_mount(self) -> None:
        # bookkit.tcss (shared, off-limits here) styles #account-header for the
        # old two-line layout; inline styles win, making it a 1-line context bar
        bar = self.query_one("#account-header", Static)
        bar.styles.height = 1
        bar.styles.padding = (0, 2)
        bar.styles.border_bottom = ("none", "black")
        # the placements table needs room for status + premium; the shared
        # 44% width crops them, and the tower preview scrolls anyway
        # the placements split is sized from the table's real column widths in
        # _size_placement_split, not from a percentage — see the note there
        # stacked tables inside scrolling panes size to their rows instead of
        # splitting the viewport into slivers
        for table_id in ("ov-team", "ov-contacts", "ov-interactions", "ov-tasks",
                         "ov-opps", "pipeline-opps", "pipeline-subs",
                         "pipeline-subjs", "open-items-context"):
            self.query_one(f"#{table_id}", ListTable).styles.height = "auto"
        # inline edits (i) parse existing values off the same fields the modal
        # form would show, keyed by the row's task id
        self.query_one("#open-items-table", InlineTable).inline_initial = (
            self._open_items_inline_initial
        )
        self.query_one("#rfi-items", InlineTable).inline_initial = (
            self._rfi_item_inline_initial
        )
        # master/detail: the request list is the index, the items are the work.
        # bookkit.tcss gives every DataTable height 1fr, so this has to be an
        # inline style to win (same reason #account-header is set here)
        self.query_one("#rfi-requests", ListTable).styles.height = "40%"
        # same master/detail proportion for the interaction log
        self.query_one("#interactions-table", ListTable).styles.height = "45%"
        self.refresh_data()
        self._render_tab_hint()
        self._focus_tab_table()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        conn = self.app.conn
        today = date.today()
        org = orgs.get(conn, self.current_org_id)

        from ...services import renewals as renewals_service

        nxt_item = renewals_service.next_for_org(conn, org.id, today)
        if nxt_item is None:
            renewal = f"renewal [{theme.DIM}]none scheduled[/]"
        else:
            # PRINT THE DATE YOU COUNT TO, on BOTH branches. days_remaining is
            # counted to renewal_on (the earliest live LINE end); period_to is
            # the program period's end, which can be months later. This branch
            # was fixed once and the overdue one above it was not, so a
            # renewal 83 days in the FUTURE rendered red as "7d over" — the
            # louder half of the same bug, on the path where a wrong date is
            # most alarming. One expression now serves both.
            renews = nxt_item.renewal_on or nxt_item.placement.period_to
            if nxt_item.days_remaining < 0:
                renewal = (
                    f"renewal [b {theme.RED}]◆ {renews} · "
                    f"{-nxt_item.days_remaining}d over · "
                    f"{nxt_item.placement.program_name}[/]"
                )
            else:
                style = theme.AMBER if nxt_item.days_remaining <= 60 else theme.FG
                renewal = f"renewal [{style}]{renews} · {nxt_item.days_remaining}d[/]"
        bound = [p for p in placements.for_org(conn, org.id) if p.status == "bound"]
        bound_premium = book.bound_premium_for_org(conn, org.id)
        status_style = theme.STATUS_STYLES.get(org.status, theme.FG)
        parts = [
            f"[b {theme.GOLD}]{org.name}[/]  [{theme.DIM}]{org.ref}[/]",
            f"[{status_style}]{_pretty(org.status)}[/]",
            f"owner {org.owner or '—'}",
            renewal,
            f"[{theme.GREEN}]{format_cents_compact(bound_premium)} bound[/]"
            f" [{theme.DIM}]({len(bound)} placements)[/]",
        ]
        self.query_one("#account-header", Static).update(
            f"  [{theme.RULE}]·[/]  ".join(parts)
        )

        team_table = self.query_one("#ov-team", ListTable)
        team_table.clear(columns=True)
        team_table.add_columns("who", "role", "lines", "scope")
        from ...repo import team as team_repo

        for row in team_repo.for_org(conn, org.id):
            scope = (
                f"{row['placement_ref']} {row['program_name']}"
                if row["placement_ref"] else "account"
            )
            team_table.add_row(
                f"{row['member_name']} ({row['specialty'] or row['member_title'] or '—'})",
                _pretty(row["role"]) if row["role"] else dash(),
                row["lines"] or dash(), scope,
                key=row["id"],
            )

        roster = contacts.for_org(conn, org.id)
        for table_id, rows in (("#ov-contacts", roster[:5]), ("#contacts-table", roster)):
            table = self.query_one(table_id, ListTable)
            table.clear(columns=True)
            table.add_columns("", "name", "role", "title", "email", "phone")
            for c in rows:
                table.add_row(
                    Text("★", style=theme.GOLD) if c.is_primary else "",
                    c.name, _pretty(c.role) if c.role else dash(),
                    c.title or dash(), c.email or dash(),
                    c.phone or c.mobile or dash(),
                    key=c.id,
                )

        log = interactions.for_org(conn, org.id)
        for table_id, int_rows in (("#ov-interactions", log[:5]), ("#interactions-table", log)):
            table = self.query_one(table_id, ListTable)
            table.clear(columns=True)
            table.add_columns("date", "type", "subject", "who", "note")
            for i in int_rows:
                who = ", ".join(c.name for c in interactions.attendees(conn, i.id))
                table.add_row(
                    i.occurred_on, Text(_pretty(i.type), style=theme.DIM),
                    i.subject, who,
                    # a glyph, not the text: the note itself is in the pane
                    # below, and a column here would only truncate it
                    Text("✎", style=theme.GOLD) if i.body else dash(),
                    key=i.id,
                )
        self._show_interaction(str(self._selected_key("interactions-table") or ""))

        # open_tasks_for_client: the overview tab and the open-items tab below
        # are the SAME account's tasks and must not count differently
        open_tasks = grouped_by_category(tasks_repo.open_tasks_for_client(conn, org.id))
        table = self.query_one("#ov-tasks", ListTable)
        table.clear(columns=True)
        # the long notes wrap down the row here instead of being clipped to
        # their first 58 characters. That needs BOTH a fixed-width column and
        # an auto-height row: an auto-width column just grows to fit the
        # longest line and nothing ever wraps. Only this table — the editable
        # task tables stay one line per row, see task_detail_wrapped on why
        detail_width = _ov_detail_width(open_tasks, self.size.width)
        for label in ("due", right("due in"), "task", "category", "description"):
            table.add_column(label)
        table.add_column("detail", width=detail_width)
        for t in open_tasks:
            if t.due_on:
                days = days_until(t.due_on, today)
                due, due_in = date_text(t.due_on, days), days_text(days)
            else:
                due, due_in = dash(), Text("", justify="right")
            table.add_row(
                due, due_in, t.title,
                theme.category_text(t.category),
                t.description or dash(),
                task_detail_wrapped(t) if detail_width else task_detail_cell(t),
                key=t.id, height=None if detail_width else 1,
            )

        opps = opportunities.for_org(conn, org.id, open_only=True)
        table = self.query_one("#ov-opps", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "title", "stage", right("target"), "close")
        for o in opps:
            table.add_row(
                o.ref, o.title, _status(o.stage),
                money_text(o.target_premium),
                o.target_effective or dash(),
                key=o.id,
            )

        self._refresh_placements(org.id)
        self._refresh_pipeline(org.id)
        self._refresh_projects(org.id)

        docs = documents.for_org(conn, org.id)
        table = self.query_one("#documents-table", ListTable)
        table.clear(columns=True)
        table.add_columns("added", "kind", "title", "path")
        for d in docs:
            table.add_row(
                d.added_at[:10],
                _pretty(d.kind) if d.kind else dash(),
                d.title, Text(d.path, style=theme.DIM), key=d.id,
            )

        self._refresh_open_items(org.id)
        self._fill_requests_tab()
        self._settle_tables()
        # the hint follows the data: adding the first row must clear the
        # empty state without waiting for a tab switch
        if self.is_mounted:
            self._render_tab_hint()

    def _refresh_open_items(self, org_id: str) -> None:
        """The client's whole open-items picture: an editable task datasheet
        (direct tasks + placement-owned ones) plus a read-only context table
        of unmet project needs and submissions still out at market."""
        from ...repo import projects as projects_repo

        conn = self.app.conn
        today = date.today()

        table = self.query_one("#open-items-table", InlineTable)
        table.clear(columns=True)
        table.add_columns(
            "due", "task", "category", "description", "detail", "status",
            "assignee",
        )
        table.inline_fields = task_inline(conn, org_id)
        for t in grouped_by_category(tasks_repo.open_tasks_for_client(conn, org_id)):
            due = (
                date_text(t.due_on, days_until(t.due_on, today))
                if t.due_on else dash()
            )
            table.add_row(
                due, t.title,
                theme.category_text(t.category),
                t.description or dash(),
                task_detail_cell(t), status_text(t.status),
                theme.assignee_text(assignees.name_of(conn, t)), key=t.id,
            )

        context = self.query_one("#open-items-context", ListTable)
        context.clear(columns=True)
        context.add_columns("kind", "item", "due / needed", "status", right("days"))
        for project in projects_repo.projects_for_org(conn, org_id):
            for need in projects_repo.needs_for_project(conn, project.id):
                if need.status not in projects_repo.ATTENTION_STATUSES:
                    continue
                days = days_until(need.needed_by, today)
                context.add_row(
                    Text("need", style=theme.DIM), f"{need.line} — {project.name}",
                    date_text(need.needed_by, days), status_text(need.status),
                    days_text(days), key=f"need:{need.id}",
                )
        for row in submissions.outstanding_for_org(conn, org_id):
            days = days_until(row["sent_on"], today)
            context.add_row(
                Text("submission", style=theme.DIM),
                f"{row['market_name']} — {row['about']}",
                date_text(row["sent_on"], days), status_text(row["status"]),
                days_text(days), key=f"submission:{row['id']}",
            )
        # Quotes in hand and the subjectivities under them. Before this, a
        # submission LEFT this list the moment the market answered and entered
        # no other list anywhere — the AE review's "missing middle", and the
        # reason three weeks of real work were invisible on every surface.
        for quote in quotes_svc.for_org(conn, org_id, today=today):
            # `left` rather than reusing `days` above: a quote's countdown can
            # be None (no expiry recorded), which the submission clock cannot
            left = quote.days_remaining
            context.add_row(
                Text("quote", style=theme.DIM),
                f"{quote.market_name} — {quote.about}",
                # one cell, one source: the date printed IS the date counted to
                theme.expiry_text(quote.expires_on, left),
                status_text(quote.submission.status),
                days_text(left) if left is not None else dash(),
                key=f"quote:{quote.submission.id}",
            )
        for row in submissions.outstanding_subjectivity_rows_for_org(conn, org_id):
            left = days_until(row["due_on"], today) if row["due_on"] else None
            context.add_row(
                Text("subjectivity", style=theme.DIM),
                f"{row['market_name']} — {row['description']}",
                date_text(row["due_on"], left) if left is not None else dash(),
                status_text(row["status"]),
                days_text(left) if left is not None else dash(),
                key=f"subjectivity:{row['id']}"
            )

    # -- requests (master) / items (detail) -------------------------------------

    def _fill_requests_tab(self) -> None:
        """The client's information requests up top, the selected one's items
        below. The selection survives a refresh: rebuilding the master table
        must not silently re-point the datasheet at the first request."""
        from ...services import rfi as rfi_svc

        conn = self.app.conn
        table = self.query_one("#rfi-requests", ListTable)
        table.clear(columns=True)
        # state and due LEAD, the descriptive columns trail: a DataTable crops
        # from the right, and with state last "withdrawn" rendered as "wi" at
        # 80 columns — a signal you cannot read is not a signal. What crops now
        # is asked-by/scope/asked, all of which the `e` form also carries.
        table.add_columns(
            "ref", "request", "state", "due", "asked by", "scope", "asked"
        )
        requests = rfi_repo.requests_for_org(conn, self.current_org_id)
        for request in requests:
            table.add_row(
                request.ref, request.title,
                rfi_state_cell(conn, request),
                request.due_on or dash(),
                rfi_asker_cell(conn, request),
                Text(rfi_svc.scope_label(conn, request), style=theme.DIM),
                request.requested_on,
                key=f"rfi:{request.id}",
            )
        ids = [r.id for r in requests]
        keep = self._rfi_request_id if self._rfi_request_id in ids else None
        target = keep or (ids[0] if ids else None)
        if target is not None:
            table.move_cursor(row=ids.index(target))
        self._fill_request_items(target)

    def _fill_request_items(self, request_id: str | None) -> None:
        conn = self.app.conn
        items = self.query_one("#rfi-items", InlineTable)
        items.clear(columns=True)
        items.add_columns(
            "item", "detail", "type", "group", "needed by", "status", "received",
            "response",
        )
        title = self.query_one("#rfi-hint", Static)
        # this runs from a RowHighlighted handler; a request that vanished
        # under the cursor must empty the datasheet, never raise out of a
        # keypress and take the app down
        request = None
        if request_id is not None:
            try:
                request = rfi_repo.get_request(conn, request_id)
            except KeyError:
                request_id = None
        self._rfi_request_id = request_id
        if request is None:
            items.inline_fields = {}
            title.update("no request selected")
            return
        title.update(f"{request.ref} — {request.title}")
        items.inline_fields = RFI_ITEM_INLINE
        for item in rfi_repo.items_for_request(conn, request.id):
            items.add_row(
                item.prompt, detail_cell(item.detail), item.kind,
                item.category or dash(),
                rfi_due_cell(item, request), status_text(item.status),
                item.received_on or dash(), item.response or dash(),
                key=item.id,
            )

    def _show_interaction(self, interaction_id: str) -> None:
        """Fill the detail pane under the log with the selected note.

        Built as rich.Text, never markup: these bodies are pasted emails and
        meeting notes, and Static.update() would read any [brackets] in them as
        markup and swallow the text."""
        detail = self.query_one("#interaction-detail", Static)
        if not interaction_id:
            detail.update(Text("no interaction selected", style=theme.DIM))
            return
        try:
            interaction = interactions.get(self.app.conn, interaction_id)
        except KeyError:  # fires from RowHighlighted; a vanished row must not
            detail.update(Text("(deleted)", style=theme.DIM))  # take the app down
            return
        who = ", ".join(
            c.name for c in interactions.attendees(self.app.conn, interaction.id)
        )
        body = Text()
        body.append(interaction.subject, style="bold")
        body.append(
            f"\n{interaction.occurred_on} · {_pretty(interaction.type)}", style=theme.DIM
        )
        if who:
            body.append(f" · {who}", style=theme.DIM)
        body.append("\n\n")
        if interaction.body:
            body.append(interaction.body)
        else:
            body.append("(no note recorded — e adds one)", style=theme.DIM)
        detail.update(body)

    def _rfi_item_inline_initial(self, row_key: str, field_key: str) -> str:
        try:
            return getattr(rfi_repo.get_item(self.app.conn, row_key), field_key) or ""
        except KeyError:
            return ""

    def _rfi_focus(self) -> str | None:
        """Which of the tab's two tables is live: 'requests', 'items', or None.
        Row actions require table focus, so the same key can mean 'the request'
        or 'the item' without a mode."""
        if self.query_one("#rfi-requests", ListTable).has_focus:
            return "requests"
        if self.query_one("#rfi-items", InlineTable).has_focus:
            return "items"
        return None

    def _settle_tables(self) -> None:
        """Workaround for a DataTable paint quirk: rows added before the first
        idle pass leave the visible tab painted at label-only column widths
        (the width measure runs on idle, but the stale strips are never
        invalidated). Flush the pending measurements now and drop the caches
        so the next paint uses real content widths."""
        for table in self.query(ListTable):
            if not table._require_update_dimensions:
                continue
            table._require_update_dimensions = False
            new_rows = table._new_rows.copy()
            table._new_rows.clear()
            table._update_dimensions(new_rows)
            table._clear_caches()
            table.refresh()

    def _refresh_placements(self, org_id: str) -> None:
        conn = self.app.conn
        table = self.query_one("#placements-table", ListTable)
        table.clear(columns=True)
        # no `effective` column: the previous row's expiry implies it, and the
        # seven columns needed 87 cells in the 72 this pane has — which pushed
        # `premium` off the tab that exists to show what a programme costs, and
        # cropped `status` to a single letter (review F5)
        table.add_columns(
            "ref", "program", "expires", right("d"), "status", right("premium"),
        )
        # live placements first, soonest expiry on top; already-expired ones
        # sink below (most recently expired first) instead of pinning forever
        rows = sorted(
            placements.for_org(conn, org_id),
            key=lambda p: (days_until(p.period_to) < 0, abs(days_until(p.period_to))),
        )
        for p in rows:
            days = days_until(p.period_to)
            table.add_row(
                p.ref, book._program_label(p.program_name),
                date_text(p.period_to, days), days_text(days), _status(p.status),
                money_text(p.total_premium),
                key=p.id,
            )
        self._size_placement_split(table)
        if rows:
            self.show_placement(rows[0].id)
        else:
            self.query_one("#sync-state", Static).update("no placements")
            self.query_one("#tower-preview", TowerPreview).show_placeholder()

    # a tower narrower than this renders as unreadable mush; below it the
    # preview stands down rather than crowding the numbers out
    PREVIEW_MIN_WIDTH = 48

    def _size_placement_split(self, table: ListTable) -> None:
        """Give the table the width its columns actually need, and the preview
        whatever is left.

        A percentage split cannot work here: the same 58% that fits at 140
        columns is one cell short at 120 and twelve short at 100, which put
        `premium` back off the screen on the tab that exists to show it
        (review F5). Measure, then divide."""
        # column widths are measured on idle; ask before that and every column
        # reports its LABEL width, which sized the pane to 45 cells flat
        self._settle_tables()
        needed = sum(c.get_render_width(table) for c in table.columns.values()) + 2
        total = self.size.width
        preview = self.query_one("#tower-preview", TowerPreview)
        side = self.query_one("#placement-side")
        if total - needed >= self.PREVIEW_MIN_WIDTH:
            side.styles.width = needed
            preview.display = True
        else:
            # not enough room for both: the numbers win, and `o` still opens
            # the real tower in towerkit
            side.styles.width = "1fr"
            preview.display = False

    def _refresh_projects(self, org_id: str) -> None:
        from ...repo import projects as projects_repo

        conn = self.app.conn
        table = self.query_one("#projects-table", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "project", "status", "start", "end", right("open needs"))
        rows = projects_repo.projects_for_org(conn, org_id)
        for project in rows:
            open_needs = sum(
                1
                for need in projects_repo.needs_for_project(conn, project.id)
                if need.status in projects_repo.ATTENTION_STATUSES
            )
            table.add_row(
                project.ref, project.name, _status(project.status),
                project.start_on or dash(), project.end_on or dash(),
                Text(str(open_needs), justify="right")
                if open_needs else Text("—", style=theme.DIM, justify="right"),
                key=project.id,
            )
        if rows:
            self.show_project(rows[0].id)
        else:
            needs = self.query_one("#needs-table", ListTable)
            needs.clear(columns=True)
            needs.add_columns(
                "line", "needed by", right("d"), "status", right("limit"), "linked"
            )

    def show_project(self, project_id: str) -> None:
        """Fill the needs table for one project, expiry-style styling on the
        needed-by dates."""
        from ...repo import projects as projects_repo

        table = self.query_one("#needs-table", ListTable)
        table.clear(columns=True)
        table.add_columns(
            "line", "needed by", right("d"), "status", right("limit"), "linked"
        )
        for need in projects_repo.needs_for_project(self.app.conn, project_id):
            days = days_until(need.needed_by)
            if need.status in projects_repo.ATTENTION_STATUSES:
                needed, due_in = date_text(need.needed_by, days), days_text(days)
            else:  # settled needs don't shout about their dates
                needed = Text(need.needed_by, style=theme.DIM)
                due_in = Text(f"{days}d", style=theme.DIM, justify="right")
            linked = (
                "opp" if need.opportunity_id
                else ("plc" if need.placement_id else dash())
            )
            table.add_row(
                need.line, needed, due_in, _status(need.status),
                money_text(need.limit_cents),
                linked,
                key=need.id,
            )
        self._settle_tables()

    def _refresh_pipeline(self, org_id: str) -> None:
        conn = self.app.conn
        table = self.query_one("#pipeline-opps", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "title", "stage", right("target"), right("prob"))
        for o in opportunities.for_org(conn, org_id):
            table.add_row(
                o.ref, o.title, _status(o.stage),
                money_text(o.target_premium),
                Text(f"{o.probability_pct}%", justify="right"),
                key=o.id,
            )
        table = self.query_one("#pipeline-subs", ListTable)
        table.clear(columns=True)
        # `expires` and `subj` are the two columns the AE review said the whole
        # surface was missing: the tool tracked SENT and BOUND and nothing in
        # between. The market cell names the underwriter, so the row you are
        # chasing tells you who to chase.
        table.add_columns(
            "market", "sent", "status", right("quoted"), "response",
            "expires", right("subj"),
        )
        today = date.today()

        def add_submission(s: Submission) -> None:
            uw = None
            if s.underwriter_contact_id:
                try:
                    uw = contacts.get(conn, s.underwriter_contact_id).name
                except KeyError:  # the person left; the submission did not
                    uw = None
            days = (
                days_until(s.quote_expires_on, today)
                if s.quote_expires_on else None
            )
            open_count, total = submissions.subjectivity_counts(conn, s.id)
            table.add_row(
                theme.market_text(orgs.get(conn, s.market_org_id).name, uw),
                s.sent_on, _status(s.status),
                money_text(s.quoted_premium),
                s.response_on or dash(),
                # the date and the countdown out of ONE cell, both read off
                # s.quote_expires_on — never a date from here beside a count
                # from somewhere else (CLAUDE.md, the "70d over" defect)
                theme.expiry_text(s.quote_expires_on, days),
                theme.subjectivity_text(open_count, total),
                key=s.id,
            )

        for p in placements.for_org(conn, org_id):
            for s in submissions.for_placement(conn, p.id):
                add_submission(s)
        for o in opportunities.for_org(conn, org_id):
            for s in submissions.for_opportunity(conn, o.id):
                add_submission(s)
        self._fill_subjectivities(self._selected_key("pipeline-subs"))

    def _fill_subjectivities(self, submission_id: str | None) -> None:
        """The conditions attached to the submission under the cursor.

        Empty is stated, not blank: an empty DataTable is a header over
        nothing, and "no subjectivities recorded" is a different sentence from
        "select a submission" — the first invites `a`, the second says the
        cursor is in the wrong place."""
        conn = self.app.conn
        self._pipeline_submission_id = submission_id
        title = self.query_one("#pipeline-subj-title", Static)
        table = self.query_one("#pipeline-subjs", ListTable)
        table.clear(columns=True)
        table.add_columns("subjectivity", "due", right("in"), "status", "satisfied")
        if submission_id is None:
            title.update(f"[{theme.DIM}]SUBJECTIVITIES — select a submission above[/]")
            return
        rows = submissions.subjectivities_for(conn, submission_id)
        if not rows:
            title.update(
                f"[{theme.DIM}]SUBJECTIVITIES — none recorded; a adds one[/]"
            )
            return
        title.update("SUBJECTIVITIES")
        today = date.today()
        for subj in rows:
            if subj.due_on:
                days = days_until(subj.due_on, today)
                due, due_in = date_text(subj.due_on, days), days_text(days)
            else:
                due, due_in = dash(), dash()
            table.add_row(
                subj.description, due, due_in, _status(subj.status),
                subj.satisfied_on or dash(),
                key=subj.id,
            )

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """The placements pane lays out at zero size until first shown; re-show
        the selection once it has a real size. Every tab lands the cursor on
        its table so j/k and the row keys work with no extra tab-presses."""
        if event.pane.id == "tab-placements":
            key = self._selected_key("placements-table")
            if key:
                self.call_after_refresh(self.show_placement, key)
        self._render_tab_hint()
        # focus synchronously: a deferred focus posts TabPane.Focused late,
        # and TabbedContent would snap `active` back to the pane it names,
        # eating the next 1–9 tab switch
        self._focus_tab_table()

    def action_show_tab(self, tab_id: str) -> None:
        """1–9 jump straight to a tab (and its table)."""
        self.query_one(TabbedContent).active = tab_id

    def _focus_tab_table(self) -> None:
        table_id = TAB_TABLES.get(self._active_tab())
        if table_id:
            self.query_one(f"#{table_id}", ListTable).focus()

    def _render_tab_hint(self) -> None:
        """The hint line, or an empty state when the tab has no rows anywhere.

        An empty DataTable is a header over blank space: 'nothing here' and
        'it failed to load' look identical. The Navigator has said so since it
        was built; this is the same two phrasings on the account screen
        (review F12).

        "Anywhere" is TAB_EMPTY_TABLES, not TAB_TABLES: the label has to
        describe the thing under it, and five tabs put more than one table
        there."""
        tab = self._active_tab()
        hint = TAB_HINTS.get(tab, "")
        # every table the tab SHOWS, not the one the cursor lands in
        table_ids = TAB_EMPTY_TABLES.get(tab)
        if table_ids is None:
            landing = TAB_TABLES.get(tab)
            table_ids = (landing,) if landing else ()
        if table_ids and not any(
            self.query_one(f"#{table_id}", ListTable).row_count
            for table_id in table_ids
        ):
            hint = (
                "empty — [b]a[/b] adds the first row"
                if tab in ADDABLE_TABS
                else "nothing here — that's good"
            )
        self.query_one("#tab-hint", Static).update(f"[{theme.DIM}]{hint}[/]")

    def on_data_table_row_highlighted(self, event: ListTable.RowHighlighted) -> None:
        """j/k on the placements table switches the previewed program live."""
        if event.data_table.id == "placements-table" and event.row_key is not None:
            key = event.row_key.value
            if key:
                self.show_placement(key)
        elif event.data_table.id == "projects-table" and event.row_key is not None:
            key = event.row_key.value
            if key:
                self.show_project(key)
        elif event.data_table.id == "interactions-table" and event.row_key is not None:
            self._show_interaction(str(event.row_key.value or ""))
        elif event.data_table.id == "pipeline-subs" and event.row_key is not None:
            # j/k down the submission list re-points the subjectivities table,
            # the same master/detail move rfi-requests makes below
            key = str(event.row_key.value or "")
            if key != self._pipeline_submission_id:
                self._fill_subjectivities(key or None)
        elif event.data_table.id == "rfi-requests" and event.row_key is not None:
            # j/k down the request list re-points the items datasheet
            _, _, request_id = str(event.row_key.value or "").partition(":")
            if request_id != self._rfi_request_id:
                self._fill_request_items(request_id or None)

    def show_placement(self, placement_id: str) -> None:
        """Fill the tower preview, carrier list, and sync-state for one placement."""
        conn = self.app.conn
        placement = placements.get(conn, placement_id)
        status_style = theme.STATUS_STYLES.get(placement.status, theme.FG)
        header = (
            f"[b]▸ {placement.ref}  {placement.program_name}[/b]  "
            f"[{status_style}]{placement.status}[/]"
        )
        from ...repo import team as team_repo

        deal_team = [
            f"{row['member_name']} ({row['role'] or row['specialty'] or 'team'})"
            for row in team_repo.for_org(conn, placement.org_id)
            if row["placement_id"] == placement.id
        ]
        if deal_team:  # deal staffing belongs where the deal lives
            header += f"\nteam: {', '.join(deal_team)}"

        carriers = self.query_one("#carriers-table", ListTable)
        carriers.clear(columns=True)
        carriers.add_columns("carrier", "layer", right("share"), right("premium"))
        # rows are keyed "<layer_id>:<n>" so `l` can edit the layer under the
        # cursor; participant-less layers get a placeholder row to stay reachable.
        # _carrier_seats resolves a row key back to its SEAT (layer_id, carrier
        # — None for a placeholder row): a carrier name can carry ":" so the
        # key alone cannot say who sits there, and e/D on this table act on
        # the seat, not the string (phase 2).
        self._carrier_seats = {}
        seen_layers: set[str] = set()
        for index, row in enumerate(projection.participants_for_placement(conn, placement_id)):
            seen_layers.add(str(row["layer_id"]))
            key = f"{row['layer_id']}:{index}"
            self._carrier_seats[key] = (str(row["layer_id"]), str(row["carrier"]))
            carriers.add_row(
                row["carrier"], row["layer_name"],
                Text(f"{row['share_bps'] / 100:g}%", justify="right"),
                money_text(row["premium"]),
                key=key,
            )
        from ... import sync as _sync

        for layer in _sync.layer_details(conn, placement_id):
            if str(layer["id"]) not in seen_layers:
                self._carrier_seats[f"{layer['id']}:x"] = (str(layer["id"]), None)
                carriers.add_row(
                    Text("— to be placed —", style=theme.DIM), layer["name"], "", "",
                    key=f"{layer['id']}:x",
                )
        self._settle_tables()

        preview = self.query_one("#tower-preview", TowerPreview)
        state = self.query_one("#sync-state", Static)
        if not placement.program_path:
            state.update(f"{header}\n○ no program file linked")
            preview.show_placeholder()
            return
        path = Path(placement.program_path)
        from ... import sync

        if not path.exists():
            state.update(f"{header}\n✗ file missing: {path}")
            preview.show_placeholder()
            return
        if not placement.source_sha256:
            # "✓ in sync" claimed a verification that never happened: with no
            # recorded sha there is nothing to compare the file against. Same
            # inverted guard as sync.write_through had (2026-08-18).
            state.update(f"{header}\n⚠ never projected — run sync to verify")
        elif sync.file_sha256(path) != placement.source_sha256:
            state.update(f"{header}\n⚠ file changed on disk — re-sync to update")
        else:
            state.update(f"{header}\n✓ in sync ({path.name})")
        preview.show_program(path)

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        table_id = event.data_table.id
        key = event.row_key.value or ""
        if table_id == "placements-table":
            self.show_placement(key)
        elif table_id == "documents-table":
            doc = next(
                (d for d in documents.for_org(self.app.conn, self.current_org_id) if d.id == key),
                None,
            )
            if doc:
                self._open_document(Path(doc.path).expanduser())

    def _open_document(self, path: Path) -> None:
        """Hand a document to the desktop. A moved or deleted file used to be
        launched anyway and reported as 'opening …', so the failure landed in
        the suspended terminal's stderr where nobody saw it (review F22)."""
        if not path.exists():
            self.notify(f"file not found: {path}", severity="error")
            return
        # no Windows entry on purpose: `start` is a cmd builtin, not an
        # executable, so shipping it would only ever raise into the guard below
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.Popen([opener, str(path)])
        except OSError as exc:
            self.notify(f"could not open {path.name}: {exc}", severity="error")
            return
        self.notify(f"opening {path.name}")

    # -- open-items inline edits ------------------------------------------------

    def _open_items_inline_initial(self, row_key: str, field_key: str) -> str:
        try:
            task = tasks_repo.get(self.app.conn, row_key)
        except KeyError:
            return ""
        if field_key == "assignee":
            # three columns, one editor — and the QUALIFIED label, so that
            # opening the cell and pressing enter resolves back to the same
            # person instead of quietly downgrading them to freeform
            return assignees.label_of(self.app.conn, task)
        return getattr(task, field_key, None) or ""

    def on_inline_table_cell_edited(self, event: InlineTable.CellEdited) -> None:
        conn = self.app.conn
        if event.table.id == "open-items-table":
            with _batched(
                self, tool="inline_edit", summary=f"edited task {event.field.key}"
            ):
                if event.field.key == "assignee":
                    # repo.assignees owns all three assignee columns
                    # together; the generic one-key update below would try
                    # to write a column that does not exist.
                    assignees.set_on_task(
                        conn, event.row_key, event.value,
                        org_id=self.current_org_id,
                    )
                else:
                    tasks_repo.update(
                        conn, event.row_key, **{event.field.key: event.value}
                    )
        elif event.table.id == "rfi-items":
            with _batched(
                self, tool="inline_edit", summary=f"edited request item {event.field.key}"
            ):
                rfi_repo.update_item(
                    conn, event.row_key, **{event.field.key: event.value}
                )
        else:
            return
        self.notify(f"{event.field.label} saved — u undoes")
        # a refresh mid-edit (e.g. tab hopping across editable cells) would
        # pull rows out from under the editor; flush once it closes and focus
        # returns to the table (same guard navigator's nav-table uses)
        if event.table.editing:
            self._refresh_pending = True
        else:
            self.refresh_data()

    def on_descendant_focus(self, event) -> None:
        # the CellEditor is mounted on the SCREEN, so opening one fires this;
        # with two inline tables the flush must wait on BOTH, or an edit on
        # one datasheet rebuilds the other out from under its open editor
        if not self._refresh_pending:
            return
        if any(
            self.query_one(f"#{table_id}", InlineTable).editing
            for table_id in ("open-items-table", "rfi-items")
        ):
            return
        self._refresh_pending = False
        self.refresh_data()

    def action_mark_primary(self) -> None:
        table = self.query_one("#contacts-table", ListTable)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if key:
            # batched like every other direct keystroke write on this screen:
            # set_primary clears one contact's flag and sets another's, so `u`
            # has to put BOTH back or it puts the org in a state neither
            # contact was ever in
            with _batched(
                self, tool="contact_set_primary", summary="set the primary contact",
                org_id=self.current_org_id,
            ):
                contacts.set_primary(self.app.conn, key)
            self.notify("primary contact set — u to undo")
            self.refresh_data()

    def action_task_done(self) -> None:
        # the requests tab claims d FIRST and returns: falling through would
        # reach the task path below, which defaults to ov-tasks and would
        # complete a task the cursor is nowhere near
        if self._active_tab() == "tab-requests":
            self._mark_item_received()
            return
        table_id = (
            "open-items-table" if self._active_tab() == "tab-open-items" else "ov-tasks"
        )
        table = self.query_one(f"#{table_id}", ListTable)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if key:
            with _batched(
                self, tool="task_done", summary="completed a task"
            ):
                tasks_repo.complete(self.app.conn, key)
            self.notify("task done — u to undo")
            self.refresh_data()

    # row key -> (layer_id, carrier|None) for the carriers table; rebuilt on
    # every refresh, so a key is never resolved against a stale seating
    _carrier_seats: dict[str, tuple[str, str | None]] = {}

    # which tables D acts on, and what "remove" means for each of them
    DELETABLE = {
        "interactions-table": "interaction",
        "ov-interactions": "interaction",
        "ov-tasks": "task",
        "open-items-table": "task",
        "ov-team": "team_assignment",
        "contacts-table": "contact",
        "ov-contacts": "contact",
    }

    def action_delete_row(self) -> None:
        """D removes the row under the cursor, whichever table holds focus.

        Focus is what disambiguates, exactly as it already does for a, e and d
        on this screen. It used to be wired only to the two interaction tables,
        which left a task with no exit but `complete()` — so a task logged
        against the wrong account had to be recorded as done, a lie in the
        event log (review F34).

        Interactions confirm first and soft-delete: removing an entry is how a
        wrongly logged activity gets corrected, most often one the MCP server
        filed against the wrong account. Tasks are DROPPED rather than deleted:
        one field write, so `u` genuinely restores it, and the row stays in
        history instead of vanishing."""
        seat = self._seat_under_cursor()
        if seat is not None:
            key = self._selected_key("placements-table")
            if key:
                placement = placements.get(self.app.conn, key)
                if seat[1] is not None:
                    self._confirm_remove_seat(placement, seat[0], seat[1])
                else:
                    self._confirm_remove_layer(placement, seat[0])
            return
        for table_id, kind in self.DELETABLE.items():
            table = self.query_one(f"#{table_id}", ListTable)
            if not table.has_focus or table.cursor_row is None or table.row_count == 0:
                continue
            key = table.coordinate_to_cell_key(
                Coordinate(table.cursor_row, 0)
            ).row_key.value
            if not key:
                return
            if kind == "task":
                self._drop_task(str(key))
            elif kind == "team_assignment":
                self._confirm_remove_assignment(str(key))
            elif kind == "contact":
                self._confirm_remove_contact(str(key))
            else:
                self._confirm_delete_interaction(str(key))
            return
        # nothing matched: D is bound screen-wide but only these tables can
        # remove a row, so on the other five tabs it did nothing at all — and
        # the screen's own rule is that an inapplicable key says so (r, t, l
        # and x all do). Silence is what made it look broken.
        self.notify(
            "D removes a task, an interaction, a contact, a market seat or "
            "an unplaced layer — tab into one of those rows",
            severity="warning",
        )

    def _drop_task(self, task_id: str) -> None:
        try:
            task = tasks_repo.get(self.app.conn, task_id)
        except KeyError:  # row key went stale under a concurrent rebuild
            return
        with _batched(
            self, tool="task_drop", summary=f"dropped task {task.title}"
        ):
            tasks_repo.drop(self.app.conn, task_id)
        self.notify(f"dropped {task.title!r} — u to undo")
        self.refresh_data()

    def _confirm_delete_interaction(self, interaction_id: str) -> None:
        from ...services import interactions as interactions_svc

        try:
            interaction = interactions.get(self.app.conn, interaction_id)
            notes = interactions_svc.consequences(self.app.conn, interaction_id)
        except KeyError:  # row key went stale under a concurrent rebuild
            return
        self.app.push_screen(
            ConfirmDeleteInteraction(interaction, notes),
            lambda ok, iid=interaction.id: self._delete_interaction(iid, ok),
        )

    def _delete_interaction(self, interaction_id: str, confirmed: bool | None) -> None:
        """No _batched() here on purpose, for the reason _remove_contact gives:
        services.interactions.delete opens its own batch so both surfaces land
        one identical undo unit — tool AND summary — and db.transaction nests by
        JOINING, so wrapping it would leave a second, empty batch in the changes
        list."""
        from ...services import interactions as interactions_svc

        if not confirmed:
            return
        try:
            summary = interactions_svc.delete(self.app.conn, interaction_id, source="tui")
        except ValueError as exc:            # already deleted under us
            self.notify(str(exc), severity="warning")
            self.refresh_data()
            return
        self.notify(f"{summary} — u to undo")
        self.refresh_data()

    def _confirm_remove_contact(self, contact_id: str) -> None:
        from ...services import contacts as contacts_svc

        try:
            contact = contacts.get(self.app.conn, contact_id)
            notes = contacts_svc.consequences(self.app.conn, contact_id)
        except KeyError:  # row key went stale under a concurrent rebuild
            return
        self.app.push_screen(
            ConfirmRemoveContact(contact, notes),
            lambda ok, cid=contact.id: self._remove_contact(cid, ok),
        )

    def _remove_contact(self, contact_id: str, confirmed: bool | None) -> None:
        """No _batched() here on purpose: services.contacts.remove opens its
        own batch, because the is_primary clear and the delete must be one undo
        unit on every surface — and db.transaction nests by JOINING, so
        wrapping it would leave a second, empty batch in the changes list."""
        from ...services import contacts as contacts_svc

        if not confirmed:
            return
        try:
            removed = contacts_svc.remove(self.app.conn, contact_id, source="tui")
        except ValueError as exc:              # already removed under us
            self.notify(str(exc), severity="warning")
            return
        self.notify(f"{removed.message} — u to undo")
        self.refresh_data()

    def _assignment_row(self, assignment_id: str):
        """The joined row behind a team table key — member name and placement
        come from the join, so re-reading the assignment alone is not enough
        to describe it."""
        from ...repo import team as team_repo

        return next(
            (
                row for row in team_repo.for_org(self.app.conn, self.current_org_id)
                if row["id"] == assignment_id
            ),
            None,
        )

    def _confirm_remove_assignment(self, assignment_id: str) -> None:
        row = self._assignment_row(assignment_id)
        if row is None:  # key went stale under a concurrent rebuild
            return
        self.app.push_screen(
            ConfirmRemoveAssignment(row),
            lambda ok, aid=assignment_id: self._remove_assignment(aid, ok),
        )

    def _remove_assignment(self, assignment_id: str, confirmed: bool | None) -> None:
        from ...repo import team as team_repo

        if not confirmed:
            return
        row = self._assignment_row(assignment_id)
        if row is None:
            return
        who = row["member_name"]
        # batched, or `u` cannot reach it: undo is batch-granular and scoped
        # to source='tui' now, so a direct repo write is invisible to it — and
        # the toast below promises an undo
        with _batched(
            self,
            tool="team_unassign",
            summary=f"removed {who} from this team",
            org_id=self.current_org_id,
        ):
            team_repo.unassign(self.app.conn, assignment_id)
        # name the line: with several rows for one person, "removed Rosa
        # Delgado" reads as though she came off the account entirely
        lines = row["lines"] or "no lines"
        self.notify(f"removed {who} ({lines}) from this team — u to undo")
        self.refresh_data()

    def _edit_assignment(self, assignment_id: str) -> None:
        from ...forms import entities as ef
        from ...repo import team as team_repo

        conn = self.app.conn
        try:
            existing = team_repo.get_assignment(conn, assignment_id)
        except KeyError:
            return
        self._push_form(
            ef.assignment_form(title="edit assignment", conn=conn, existing=existing),
            lambda v: ef.apply_assignment(conn, v, existing),
        )

    def _mark_item_received(self) -> None:
        """d on the items datasheet: received, dated today — 'done' means the
        same thing here as everywhere. Two field writes, so no undo is
        promised: a single u would revert only received_on."""
        from ...services import rfi as rfi_svc

        where = self._rfi_focus()
        if where is None:
            return  # no table focused: the house rule for every row action
        if where != "items":
            # from the master table this used to do nothing at all, silently.
            # `d` is per-ITEM by design — a request closes when its last item
            # comes back — so say which table answers instead of no-op'ing.
            self.notify(
                "d marks one ITEM received — tab down to the items",
                severity="warning",
            )
            return
        item_id = self._selected_key("rfi-items")
        if not item_id:
            self.notify("no item to mark received", severity="warning")
            return
        rfi_svc.mark_received(self.app.conn, str(item_id), date.today().isoformat())
        self.notify("received")
        self.refresh_data()

    def action_paste_items(self) -> None:
        """One pasted block of underwriter questions → one item per line.

        EITHER table answers. Pasting a litany is the intake gesture this tab
        exists for (rfi_paste: typing them one form at a time is the failure
        mode that kills the feature), and the cursor lands on the REQUEST list
        when the tab opens — so demanding items-table focus left the key dead
        exactly where a user first presses it, silently. The highlighted master
        row names the request to paste into, which is what it means anyway."""
        from ..widgets import entity_actions

        if self._active_tab() != "tab-requests":
            return  # P means nothing on the other tabs; silence is right there
        if self._rfi_focus() is None:
            return  # no table focused: the house rule for every row action
        if not self._rfi_request_id:
            self.notify("pick a request first", severity="warning")
            return
        entity_actions.paste_rfi_items(self, self._rfi_request_id)

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()

    # --- add / edit (contextual per tab) --------------------------------------

    def _active_tab(self) -> str:
        return self.query_one(TabbedContent).active

    def _selected_key(self, table_id: str) -> str | None:
        """The row under the cursor, focused or not. RENDERING uses this — the
        detail panes track the cursor whether or not the table has focus."""
        table = self.query_one(f"#{table_id}", ListTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    def _acting_key(self, table_id: str) -> str | None:
        """The row an ACTION may act on.

        d, D and p checked focus from the start; e, r, s, t, w, x and l did
        not, so `tab tab r` fired ConfirmRenew with focus on the TAB BAR — and
        r clones a placement and its towerkit file, acting on a cursor the
        user cannot see.

        The gate is "focus is on a table", not "focus is on THIS table":
        several flows deliberately act on the placement under the cursor while
        a sibling table on the same tab holds focus (`l` edits the layer of
        the shown placement from the carriers table). Requiring this exact
        table broke that, which is what test_l_edits_layer_under_cursor caught.
        What must be refused is chrome — the tab bar, a scroll container —
        where there is no visible cursor at all."""
        if not isinstance(self.focused, ListTable):
            self.notify("press tab to work the rows first", severity="warning")
            return None
        return self._selected_key(table_id)

    def _push_form(self, spec, on_save) -> None:
        from ..widgets.forms import FormModal

        def commit(values) -> str | None:
            on_save(values)  # raises → FormModal shows it and stays open
            return None

        def done(values) -> None:
            if values is not None:
                self.refresh_data()

        self.app.push_screen(FormModal(spec, commit=commit), done)

    def action_add_here(self) -> None:
        from ...forms import entities as ef

        conn = self.app.conn
        org_id = self.current_org_id
        tab = self._active_tab()
        if tab == "tab-contacts":
            self._push_form(
                ef.contact_form(),
                lambda v: self.notify(
                    f"added {ef.apply_contact(conn, org_id, v).name}"
                ),
            )
        elif tab == "tab-placements":
            self._push_form(
                ef.placement_form(conn=conn),
                lambda v: self.notify(
                    f"created {ef.apply_placement(conn, v, org_id).ref}"
                ),
            )
        elif tab == "tab-pipeline":
            # focus decides, exactly as it does on the projects tab: the
            # subjectivities table is a detail of the submission above it, so
            # `a` there adds to THAT submission rather than opening an
            # unrelated opportunity form.
            subjs = self.query_one("#pipeline-subjs", ListTable)
            submission_id = self._selected_key("pipeline-subs")
            if self.focused is subjs:
                if submission_id is None:
                    self.notify(
                        "no submission selected — press tab to the submissions "
                        "table and pick one first"
                    )
                    return
                self._push_form(
                    ef.subjectivity_form(),
                    lambda v: self.notify(
                        "subjectivity added: "
                        f"{ef.apply_subjectivity(conn, v, submission_id).description}"
                    ),
                )
            else:
                self._push_form(
                    ef.opportunity_form(conn=conn),
                    lambda v: self.notify(
                        f"created {ef.apply_opportunity(conn, v, org_id).ref}"
                    ),
                )
        elif tab == "tab-documents":
            self._push_form(
                ef.document_form(),
                lambda v: documents.add(
                    conn, org_id, v["title"], v["path"], kind=v.get("kind")
                ),
            )
        elif tab == "tab-interactions":
            self.app.action_quick_capture()
        elif tab == "tab-projects":
            from ...repo import projects as projects_repo

            needs_table = self.query_one("#needs-table", ListTable)
            project_id = self._selected_key("projects-table")
            if self.focused is needs_table and project_id:
                project = projects_repo.get_project(conn, project_id)
                self._push_form(
                    ef.need_form(conn=conn),
                    lambda v: self.notify(
                        f"need added to {project.name}: "
                        f"{ef.apply_need(conn, v, project.id).line}"
                    ),
                )
            else:
                self._push_form(
                    ef.project_form(),
                    lambda v: self.notify(
                        f"created {ef.apply_project(conn, v, org_id).ref}"
                    ),
                )
        elif tab == "tab-open-items":  # same task_form flow as overview
            self._push_form(
                ef.task_form(conn=conn, default_org_id=org_id),
                lambda v: ef.apply_task(conn, v, org_id=org_id),
            )
        elif tab == "tab-requests":
            from ..widgets import entity_actions

            where = self._rfi_focus()
            if where == "requests":
                entity_actions.add_request(self, org_id)
                return
            if where == "items":
                # mirror action_paste_items: never silently do nothing
                if not self._rfi_request_id:
                    self.notify("pick a request first", severity="warning")
                    return
                entity_actions.add_rfi_item(self, self._rfi_request_id)
                return
            # neither table focused → fall through to the account-level
            # default, the way every other tab does
            self._push_form(
                ef.task_form(conn=conn, default_org_id=org_id),
                lambda v: ef.apply_task(conn, v, org_id=org_id),
            )
        else:  # overview → a new task for this account
            self._push_form(
                ef.task_form(conn=conn, default_org_id=org_id),
                lambda v: ef.apply_task(conn, v, org_id=org_id),
            )

    def action_edit_here(self) -> None:
        from ...forms import entities as ef
        from ...repo import opportunities as opps_repo

        conn = self.app.conn
        tab = self._active_tab()
        if tab == "tab-contacts":
            key = self._selected_key("contacts-table")
            if key:
                existing = contacts.get(conn, key)
                self._push_form(
                    ef.contact_form(existing),
                    lambda v: ef.apply_contact(conn, existing.org_id, v, existing),
                )
        elif tab == "tab-placements":
            key = self._selected_key("placements-table")
            if key:
                placement = placements.get(conn, key)
                # e on a carriers-table row corrects THAT SEAT; anywhere else
                # on the tab it edits the placement, as before
                seat = self._seat_under_cursor()
                if seat is not None and seat[1] is not None:
                    self._edit_seat(placement, seat[0], seat[1])
                    return
                if seat is not None:
                    self.notify(
                        "an unplaced layer has no seat to correct — l edits "
                        "the layer, D removes it",
                        severity="warning",
                    )
                    return
                from ..widgets import entity_actions

                entity_actions.edit_placement(self, placement)
        elif tab == "tab-pipeline":
            focused = self.focused
            if focused is not None and focused.id == "pipeline-subjs":
                subj_key = self._selected_key("pipeline-subjs")
                # A REFUSAL SAYS SOMETHING (CLAUDE.md): returning in silence
                # from a key the hint line names reads as a broken app. `a`
                # on this same table already says so; `e` did not.
                if not subj_key:
                    self.notify(
                        "no subjectivity selected — press a to add one, or tab "
                        "to the submissions table to pick a different quote",
                        severity="warning",
                    )
                    return
                subj = submissions.get_subjectivity(conn, subj_key)
                self._push_form(
                    ef.subjectivity_form(subj),
                    lambda v: ef.apply_subjectivity(
                        conn, v, subj.submission_id, subj
                    ),
                )
            elif focused is not None and focused.id == "pipeline-subs":
                key = self._selected_key("pipeline-subs")
                if not key:
                    # the sibling branch's silence, and the same fix: an empty
                    # submissions table is the commonest way to press e here
                    self.notify(
                        "no submission selected — press s to send one to a "
                        "market first",
                        severity="warning",
                    )
                    return
                sub = submissions.get(conn, key)

                def response_saved(values: dict) -> None:
                    ef.apply_response(conn, sub.id, values)
                    if values.get("status") == "bound":
                        # bound → offer to put the market on a layer
                        self._offer_bind_to_layer(sub.id)

                self._push_form(ef.response_form(sub, conn), response_saved)
            else:
                key = self._selected_key("pipeline-opps")
                if key:
                    opp = opps_repo.get(conn, key)
                    self._push_form(
                        ef.opportunity_form(opp, conn=conn),
                        lambda v: ef.apply_opportunity(conn, v, opp.org_id, opp),
                    )
        elif tab == "tab-projects":
            from ...repo import projects as projects_repo

            needs_table = self.query_one("#needs-table", ListTable)
            need_key = self._selected_key("needs-table")
            if self.focused is needs_table and need_key:
                need = projects_repo.get_need(conn, need_key)
                self._push_form(
                    ef.need_form(need, conn=conn),
                    lambda v: ef.apply_need(conn, v, need.project_id, need),
                )
            else:
                project_key = self._selected_key("projects-table")
                if project_key:
                    project = projects_repo.get_project(conn, project_key)
                    self._push_form(
                        ef.project_form(project),
                        lambda v: ef.apply_project(conn, v, project.org_id, project),
                    )
        elif tab == "tab-interactions":
            # e edits the INTERACTION when its table has focus, and only falls
            # through to the account otherwise. It used to always edit the
            # account, which left interactions with no edit path at all
            # despite interactions.update() existing (review F33).
            self._edit_interaction_or_org("interactions-table")
        elif tab == "tab-overview":
            focused_id = self.focused.id if self.focused is not None else None
            key = self._selected_key("ov-tasks")
            team_key = self._selected_key("ov-team")
            if key and focused_id == "ov-tasks":
                task = tasks_repo.get(conn, key)
                self._push_form(
                    ef.task_form(task, conn=conn), lambda v: ef.apply_task(conn, v, existing=task)
                )
            elif team_key and focused_id == "ov-team":
                # without this the team table fell through to _edit_org, so e
                # on a colleague silently opened the ACCOUNT form (review F35)
                self._edit_assignment(str(team_key))
            else:  # otherwise e edits the account itself
                self._edit_org()
        elif tab == "tab-open-items":
            key = self._selected_key("open-items-table")
            if key and self.focused is not None and self.focused.id == "open-items-table":
                task = tasks_repo.get(conn, key)
                self._push_form(
                    ef.task_form(task, conn=conn), lambda v: ef.apply_task(conn, v, existing=task)
                )
            else:  # otherwise e edits the account itself
                self._edit_org()
        elif tab == "tab-requests":
            from ..widgets import entity_actions

            where = self._rfi_focus()
            try:
                if where == "requests":
                    key = self._selected_key("rfi-requests")
                    _, _, request_id = str(key or "").partition(":")
                    if request_id:
                        entity_actions.edit_request(
                            self, rfi_repo.get_request(conn, request_id)
                        )
                elif where == "items":
                    item_id = self._selected_key("rfi-items")
                    if item_id:
                        entity_actions.edit_rfi_item(
                            self, rfi_repo.get_item(conn, str(item_id))
                        )
                else:  # neither table focused → e edits the account itself
                    self._edit_org()
            except KeyError:
                # name the subject: "no longer exists" left the user guessing
                # which of the tab's two tables had gone stale (review F23)
                gone = {"items": "item", "requests": "request"}.get(where or "", "account")
                self.notify(f"that {gone} no longer exists", severity="error")
        else:
            self._edit_org()

    def _edit_interaction_or_org(self, table_id: str) -> None:
        from ...forms import entities as ef

        conn = self.app.conn
        table = self.query_one(f"#{table_id}", ListTable)
        key = self._selected_key(table_id) if table.has_focus else None
        if not key:
            self._edit_org()
            return
        try:
            existing = interactions.get(conn, str(key))
        except KeyError:
            self.notify("that interaction no longer exists", severity="error")
            return
        self._push_form(
            ef.interaction_form(existing),
            lambda v: ef.apply_interaction(conn, v, existing),
        )

    def _edit_org(self) -> None:
        from ...forms import entities as ef

        conn = self.app.conn
        existing = orgs.get(conn, self.current_org_id)
        self._push_form(
            ef.org_form_initial_profile(conn, existing),
            lambda v: ef.apply_org(conn, v, existing),
        )

    def action_renew_placement(self) -> None:
        """Roll the selected placement into next period; file-backed ones get
        next year's towerkit file cloned and linked at birth."""
        if self._active_tab() != "tab-placements":
            self.notify("r renews the selected program (programs tab)", severity="warning")
            return
        key = self._acting_key("placements-table")
        if key is None:
            return
        from ..widgets import entity_actions

        entity_actions.renew_placement(self, placements.get(self.app.conn, key))

    def action_import_here(self) -> None:
        """Paste imports for this account: contact/signature, a program
        schedule onto an unlinked placement, or renewal terms onto a linked
        one. Every flow stages first; commit is gated on zero errors."""
        from ..widgets.paste_import import ImportChooser

        conn = self.app.conn
        org = orgs.get(conn, self.current_org_id)
        options: list[tuple[str, str]] = [("contact", "Contact / signature paste")]
        for placement in placements.for_org(conn, self.current_org_id):
            label = f"{placement.ref} {placement.program_name} {placement.period_from}"
            if placement.program_path:
                options.append((f"renewal:{placement.id}", f"Renewal terms → {label}"))
            else:
                options.append((f"program:{placement.id}", f"Program schedule → {label}"))

        def chosen(option_id: str | None) -> None:
            if option_id is None:
                return
            if option_id == "contact":
                self._paste_contact(org)
            elif option_id.startswith("program:"):
                self._paste_program(org, option_id.partition(":")[2])
            elif option_id.startswith("renewal:"):
                self._paste_renewal(option_id.partition(":")[2])

        self.app.push_screen(ImportChooser(options), chosen)

    def _paste_contact(self, org) -> None:
        from ...imports.commit import commit_contact_paste
        from ...imports.mappers.contact_paste import stage_contact_paste
        from ..widgets.paste_import import PasteImportModal

        def stage(text: str):
            return stage_contact_paste(self.app.conn, text, org.id, org.name)

        def commit(staged) -> str:
            commit_contact_paste(self.app.conn, staged, org.id, self.app.db_file())
            self.refresh_data()
            return "contact captured"

        self.app.push_screen(PasteImportModal("paste signature / thread", stage, commit))

    def _paste_program(self, org, placement_id: str) -> None:
        from ... import sync
        from ...imports.commit import commit_program
        from ...imports.mappers.program_paste import stage_program
        from ..widgets.paste_import import PasteImportModal

        placement = placements.get(self.app.conn, placement_id)
        roots = sync.configured_roots(self.app.conn)
        if not roots:
            self.notify("set the program file location first (, on Today)", severity="warning")
            return
        slug = "-".join(org.name.lower().split()[:2]).strip(",.")
        dest = roots[0] / f"{slug}-{placement.period_from[:4]}.json"
        state: dict = {}

        def stage(text: str):
            staged, draft = stage_program(
                self.app.conn, text, org.name, placement.program_name,
                placement.period_from, placement.period_to,
            )
            state["draft"] = draft
            return staged

        def commit(staged) -> str:
            path, diags = commit_program(
                self.app.conn, staged, state["draft"], placement.id, dest,
                self.app.db_file(),
            )
            if path is None:
                first = diags.errors[0] if diags.errors else "unknown error"
                raise ValueError(str(first))
            self.refresh_data()
            return f"created {path.name} — projected onto {placement.ref}"

        self.app.push_screen(
            PasteImportModal(f"paste schedule → {placement.program_name}", stage, commit)
        )

    def _paste_renewal(self, placement_id: str) -> None:
        from ...imports.commit import commit_renewal
        from ...imports.mappers.renewal_paste import stage_renewal
        from ..widgets.paste_import import PasteImportModal

        def stage(text: str):
            return stage_renewal(self.app.conn, placement_id, text)

        def commit(staged) -> str:
            new_id, diags = commit_renewal(
                self.app.conn, staged, placement_id, self.app.db_file()
            )
            if new_id is None:
                first = diags.errors[0] if diags.errors else "unknown error"
                raise ValueError(str(first))
            self.refresh_data()
            if diags.errors:  # renewed, but some pasted terms were refused
                return (
                    f"renewed, but {len(diags.errors)} term(s) NOT applied — "
                    f"{diags.errors[0]}"
                )
            return "renewed with pasted terms — review in the programs tab"

        self.app.push_screen(PasteImportModal("paste renewal terms", stage, commit))

    def action_scaffold_tower(self) -> None:
        """Create the towerkit file FROM the selected placement — insured,
        name, period, and indicated premium flow over; build the tower itself
        in towerkit. Nothing is typed twice."""
        from ... import sync
        from ...forms.spec import FormSpec
        from ..widgets.forms import FormModal

        if self._active_tab() != "tab-placements":
            self.notify("t scaffolds a tower file (programs tab)", severity="warning")
            return
        key = self._acting_key("placements-table")
        if key is None:
            return
        placement = placements.get(self.app.conn, key)
        if placement.program_path:
            self.notify(f"{placement.ref} already has a file: {placement.program_path}")
            return
        roots = sync.configured_roots(self.app.conn)
        if not roots:
            self.notify("set the program file location first (, on Today)", severity="warning")
            return
        org = orgs.get(self.app.conn, placement.org_id)
        slug = "-".join(org.name.lower().split()[:2]).strip(",.")
        year = placement.period_from[:4]
        default = roots[0] / f"{slug}-{year}.json"

        created: dict[str, str] = {}

        def commit(values: dict) -> str | None:
            dest, diags = sync.scaffold_program(
                self.app.conn, placement.id, Path(values["path"]).expanduser()
            )
            if dest is None or not diags.ok:
                first = diags.errors[0] if diags.errors else "unknown error"
                return f"scaffold refused: {first}"
            created["name"] = dest.name
            return None

        def done(values: dict | None) -> None:
            if values is None:
                return
            self.notify(f"created {created['name']} — build the tower in towerkit")
            self.refresh_data()

        spec = FormSpec(
            "create tower file",
            [Field("path", "file path", required=True)],
            initial={"path": str(default)},
        )
        self.app.push_screen(FormModal(spec, commit=commit), done)

    def _selected_linked_placement(self):
        """The selected placement when it has a program file, else None+notify."""
        if self._active_tab() != "tab-placements":
            self.notify("layer keys work on the programs tab", severity="warning")
            return None
        key = self._acting_key("placements-table")
        if key is None:
            return None
        placement = placements.get(self.app.conn, key)
        if not placement.program_path:
            self.notify(
                f"{placement.ref} has no program file — e edits it directly, "
                "or t scaffolds a file first",
                severity="warning",
            )
            return None
        return placement

    def _seat_under_cursor(self) -> tuple[str, str | None] | None:
        """The carriers-table row under the cursor as (layer_id, carrier);
        carrier None for a placeholder row; None when the table lacks focus."""
        table = self.query_one("#carriers-table", ListTable)
        if self.focused is not table or not table.row_count or table.cursor_row is None:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        return self._carrier_seats.get(str(key)) if key else None

    def _edit_seat(self, placement, layer_id: str, carrier: str) -> None:
        """Correct one seat IN PLACE — carrier name or share — the write the
        web has had since phase 1 and the TUI could not make at all: a wrong
        share typed here could only be fixed in the browser."""
        from ... import sync
        from ...forms.spec import Field, FormSpec
        from ...repo import vocab
        from ..widgets.forms import FormModal

        conn = self.app.conn
        layer = next(
            (ly for ly in sync.layer_details(conn, placement.id)
             if str(ly["id"]) == layer_id),
            None,
        )
        seat = next(
            (pt for pt in (layer["participants"] if layer else [])
             if pt["carrier"] == carrier),
            None,
        )
        if layer is None or seat is None:
            self.notify("that seat moved — refreshing", severity="warning")
            self.refresh_data()
            return
        # PERCENT -> BPS for the pre-fill: the share Field kind formats BPS,
        # and the seat dict carries share_pct. Handing it the percent is the
        # 100x pre-fill bug the web's market editor shipped with (phase 1).
        current_bps = int(round(seat["share_pct"] * 100))
        spec = FormSpec(
            f"correct {carrier} on {layer['name']}",
            [
                Field(
                    "carrier", "market", required=True,
                    suggestions=tuple(vocab.market_names(conn)),
                ),
                Field("share", "share", "share", required=True),
            ],
            initial={"carrier": carrier, "share": current_bps},
        )

        def commit(values: dict) -> str | None:
            changes: dict = {}
            if values["carrier"] != carrier:
                changes["new_carrier"] = values["carrier"]
            if values["share"] != current_bps:
                changes["share_bps"] = values["share"]
            if not changes:
                return None
            diags = sync.update_participant(
                conn, placement.id, layer_id, carrier, **changes
            )
            return f"refused: {diags.errors[0]}" if not diags.ok else None

        def done(values: dict | None) -> None:
            if values is None:
                return
            self.notify(f"corrected {carrier} on {layer['name']}")
            self.refresh_data()

        self.app.push_screen(FormModal(spec, commit=commit), done)

    def _confirm_remove_seat(self, placement, layer_id: str, carrier: str) -> None:
        from ... import sync
        from ..widgets import entity_actions

        conn = self.app.conn
        layer = next(
            (ly for ly in sync.layer_details(conn, placement.id)
             if str(ly["id"]) == layer_id),
            None,
        )
        if layer is None:
            self.refresh_data()
            return

        def confirmed(answer: bool | None) -> None:
            if not answer:
                return
            try:
                with entity_actions.batched_write(
                    self, tool="program_layer_edit",
                    summary=f"took {carrier} off {layer['name']}",
                    org_id=placement.org_id,
                ):
                    diags = sync.remove_participant(
                        conn, placement.id, layer_id, carrier
                    )
                    if not diags.ok:
                        # raising rolls the batch row back with the write
                        raise ValueError(f"refused: {diags.errors[0]}")
            except ValueError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(f"took {carrier} off {layer['name']} — the layer stays")
            self.refresh_data()

        self.app.push_screen(
            ConfirmProgramRemoval(
                "TAKE MARKET OFF LAYER",
                f"take {carrier} off {layer['name']}? the layer stays, unplaced",
            ),
            confirmed,
        )

    def _confirm_remove_layer(self, placement, layer_id: str) -> None:
        from ... import sync
        from ..widgets import entity_actions

        conn = self.app.conn
        layer = next(
            (ly for ly in sync.layer_details(conn, placement.id)
             if str(ly["id"]) == layer_id),
            None,
        )
        if layer is None:
            self.refresh_data()
            return
        seats = [pt["carrier"] for pt in layer["participants"]]
        blast = f"{', '.join(seats)} come off with it" if seats else "nobody is on it"

        def confirmed(answer: bool | None) -> None:
            if not answer:
                return
            try:
                with entity_actions.batched_write(
                    self, tool="program_layer_remove",
                    summary=f"removed layer {layer['name']}",
                    org_id=placement.org_id,
                ):
                    diags = sync.remove_layer(conn, placement.id, layer_id)
                    if not diags.ok:
                        raise ValueError(f"refused: {diags.errors[0]}")
            except ValueError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(f"removed {layer['name']}")
            self.refresh_data()

        self.app.push_screen(
            ConfirmProgramRemoval(
                "REMOVE LAYER", f"remove {layer['name']}? {blast} (D2)"
            ),
            confirmed,
        )

    def action_edit_layer(self) -> None:
        from ..widgets import entity_actions

        placement = self._selected_linked_placement()
        if placement is None:
            return

        def _layer_under_cursor() -> str | None:
            """The carriers table's highlighted row names a layer directly."""
            table = self.query_one("#carriers-table", ListTable)
            if self.focused is not table or not table.row_count:
                return None
            cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
            row_key = cell_key.row_key.value
            return str(row_key).partition(":")[0] if row_key else None

        entity_actions.edit_layer(self, placement, layer_id=_layer_under_cursor())

    def action_add_layer(self) -> None:
        from ... import sync
        from ...forms.spec import FormSpec
        from ..widgets.forms import FormModal

        placement = self._selected_linked_placement()
        if placement is None:
            return
        lines = sync.program_lines(self.app.conn, placement.id)
        if not lines:
            self.notify("the program has no lines — add them in towerkit (o)")
            return
        line_options = tuple((name, line_id) for line_id, name in lines)
        if len(lines) > 1:
            line_options = (("all lines", "__all__"), *line_options)
        spec = FormSpec(
            "add layer (pending — markets join as they bind)",
            [
                Field("name", "name", required=True, placeholder="1st Excess"),
                Field("line", "applies to", "select", line_options, required=True),
                Field("attach", "attach", "money", required=True),
                Field("limit", "limit", "money", required=True),
                Field("premium", "indicated premium", "money"),
            ],
        )

        def commit(values: dict) -> str | None:
            line_ids = (
                [line_id for line_id, _ in lines]
                if values["line"] == "__all__"
                else [values["line"]]
            )
            diags = sync.add_layer(
                self.app.conn,
                placement.id,
                values["name"],
                line_ids,
                attach_cents=values["attach"],
                limit_cents=values["limit"],
                premium_cents=values.get("premium"),
            )
            return f"refused: {diags.errors[0]}" if not diags.ok else None

        def done(values: dict | None) -> None:
            if values is None:
                return
            self.notify(f"added {values['name']} (to be placed)")
            self.refresh_data()

        self.app.push_screen(FormModal(spec, commit=commit), done)

    def action_open_towerkit(self) -> None:
        """Suspend bookkit, open the linked file in towerkit's editor, and
        re-project on the way back so the cache follows the edits.
        On the projects tab, `o` instead turns the selected need into its
        opportunity — the optional link forming when the need gets real."""
        import shutil
        import sys

        from ... import sync

        if self._active_tab() == "tab-projects":
            self._need_to_opportunity()
            return

        placement = self._selected_linked_placement()
        if placement is None:
            return
        towerctl = Path(sys.executable).with_name("towerctl")
        if not towerctl.exists():
            found = shutil.which("towerctl")
            if found is None:
                self.notify("towerctl not found on PATH", severity="error")
                return
            towerctl = Path(found)
        with self.app.suspend():
            subprocess.run([str(towerctl), "edit", placement.program_path])
        diags = sync.project(self.app.conn, Path(placement.program_path))
        if diags.ok:
            self.notify("back from towerkit — re-projected")
        else:
            self.notify(
                f"back from towerkit, but the file has issues: {diags.errors[0]}",
                severity="error",
            )
        self.refresh_data()

    def _need_to_opportunity(self) -> None:
        """o on a need row: create the pre-filled opportunity and link it."""
        from ...repo import projects as projects_repo

        conn = self.app.conn
        need_key = self._acting_key("needs-table")
        if need_key is None:
            self.notify("select a need first (needs table)", severity="warning")
            return
        need = projects_repo.get_need(conn, need_key)
        if need.opportunity_id is not None:
            self.notify("this need already has an opportunity")
            return
        project = projects_repo.get_project(conn, need.project_id)
        opp = opportunities.create(
            conn,
            project.org_id,
            f"{project.name} — {need.line}",
            lines=need.line,
            target_effective=need.needed_by,
            target_premium=need.premium_indication_cents,
        )
        projects_repo.update_need(conn, need.id, opportunity_id=opp.id)
        self.notify(f"{opp.ref} created from the need — it's in the pipeline")
        self.refresh_data()

    def _offer_bind_to_layer(self, submission_id: str) -> None:
        """A market bound: offer to put them on a layer at their share."""
        from ... import sync
        from ...forms.spec import FormSpec
        from ...money import MoneyParseError, parse_share_bps
        from ..widgets.forms import FormModal
        from ..widgets.picker import Picker

        conn = self.app.conn
        sub = submissions.get(conn, submission_id)
        if sub.placement_id is None:
            return
        placement = placements.get(conn, sub.placement_id)
        if not placement.program_path:
            return
        market = orgs.get(conn, sub.market_org_id)
        layers = sync.layer_details(conn, placement.id)
        if not layers:
            return

        def picked(layer_id: str | None) -> None:
            if layer_id is None:
                return
            layer = next(ly for ly in layers if ly["id"] == layer_id)
            spec = FormSpec(
                f"{market.name} on {layer['name']}",
                [Field("share", "share % ('25', '12.5%')", required=True)],
            )

            def commit(values: dict) -> str | None:
                try:
                    share_bps = parse_share_bps(str(values["share"]))
                except MoneyParseError as exc:
                    return str(exc)
                diags = sync.add_participant(
                    conn, placement.id, layer_id, market.name, share_bps
                )
                if not diags.ok:
                    return f"refused: {diags.errors[0]}"
                bound["share_bps"] = share_bps
                return None

            bound: dict[str, int] = {}

            def done(values: dict | None) -> None:
                if values is None:
                    return
                self.notify(
                    f"{market.name} added to {layer['name']} "
                    f"at {bound['share_bps'] / 100:g}%"
                )
                self.refresh_data()

            self.app.push_screen(FormModal(spec, commit=commit), done)

        options = [
            (
                f"{ly['name']}  {format_cents_compact(ly['limit_cents'])} xs "
                f"{format_cents_compact(ly['attach_cents'])}  ({ly['signed_pct']:g}% placed)",
                str(ly["id"]),
            )
            for ly in layers
        ]
        self.app.push_screen(
            Picker(f"add {market.name} to which layer? (esc skips)", options), picked
        )

    def action_assign_team(self) -> None:
        """Assign a colleague: to the account, or to the selected placement
        when the placements tab is open."""
        from ...forms.entities import assignment_form
        from ...forms.spec import dropped
        from ...repo import team as team_repo
        from ..widgets.forms import FormModal

        conn = self.app.conn
        members = team_repo.list_members(conn)
        if not members:
            self.notify("no team members yet — press w on Today, then a", severity="warning")
            return
        placement_id = (
            self._acting_key("placements-table")
            if self._active_tab() == "tab-placements"
            else None
        )
        org_id = None if placement_id else self.current_org_id

        from ...forms.entities import NEW_MEMBER

        def commit(values: dict) -> str | None:
            if values["team_member_id"] == NEW_MEMBER:
                return None  # nothing to write yet — done() chains to the member form
            cleaned = dropped(values)
            member_id = cleaned.pop("team_member_id")
            team_repo.assign(
                conn, member_id, org_id=org_id, placement_id=placement_id, **cleaned
            )
            assigned["name"] = team_repo.get_member(conn, member_id).name
            return None

        assigned: dict[str, str] = {}

        def done(values: dict | None) -> None:
            if values is None:
                return
            if values["team_member_id"] == NEW_MEMBER:
                self._create_member_then_assign(values, org_id, placement_id)
                return
            scope = "placement" if placement_id else "account"
            self.notify(f"{assigned['name']} assigned to this {scope}")
            self.refresh_data()

        options = tuple(
            (f"{m.name} ({m.specialty})" if m.specialty else m.name, m.id)
            for m in members
        )
        self.app.push_screen(FormModal(assignment_form(options, conn=conn), commit=commit), done)

    def _create_member_then_assign(
        self, assignment: dict, org_id: str | None, placement_id: str | None
    ) -> None:
        """The who-select's '+ new team member…' sentinel chains here: create
        the member and complete the assignment in one transaction, so a
        refused member save keeps that form open with input intact."""
        from ...db import transaction
        from ...forms import entities as ef
        from ...forms.spec import dropped
        from ...repo import team as team_repo
        from ..widgets.forms import FormModal

        conn = self.app.conn

        def commit(values: dict) -> str | None:
            core = dropped(values)
            name = core.pop("name")
            with transaction(conn):
                member = team_repo.create_member(conn, name, **core)
                team_repo.assign(
                    conn, member.id, org_id=org_id, placement_id=placement_id,
                    role=assignment.get("role"),
                    lines=assignment.get("lines"),
                    notes=assignment.get("notes"),
                )
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.notify("member created and assigned")
                self.refresh_data()

        self.app.push_screen(
            FormModal(ef.member_form(conn=conn), commit=commit), done
        )

    def action_export_open_items(self) -> None:
        """x — screen-wide (Grant's call 2026-08-13): the placements tab keeps
        its existing merge-duplicate flow under x; every other tab exports the
        open-items workbook for this client. Export mutates nothing, so it
        never refuses the way a form commit would."""
        if self._active_tab() == "tab-placements":
            self.action_merge_placement()
            return
        from ..widgets import entity_actions

        entity_actions.export_open_items_flow(self, self.current_org_id)

    def action_merge_placement(self) -> None:
        """Merge the selected (duplicate) placement into another of this org's
        placements — submissions, tasks, and documents move with it."""
        if self._active_tab() != "tab-placements":
            self.notify("x merges the selected program (programs tab)", severity="warning")
            return
        key = self._acting_key("placements-table")
        if key is None:
            return
        source = placements.get(self.app.conn, key)
        targets = [
            p for p in placements.for_org(self.app.conn, source.org_id) if p.id != source.id
        ]
        if not targets:
            self.notify("nothing to merge into — this is the only placement")
            return
        self.app.push_screen(MergePicker(source, targets), self._merge_confirmed(source.id))

    def _merge_confirmed(self, source_id: str):
        def done(target_id: str | None) -> None:
            if target_id is None:
                return
            from ...repo import placements as placements_repo
            from ...services.merge import MergeError, merge_placements

            # refs, not ULIDs: this sentence is what the changes list shows
            source_ref = placements_repo.get(self.app.conn, source_id).ref
            target_ref = placements_repo.get(self.app.conn, target_id).ref
            try:
                with _batched(
                    self,
                    tool="merge_placements",
                    summary=f"merged {source_ref} into {target_ref}",
                    org_id=self.current_org_id,
                ):
                    result = merge_placements(self.app.conn, source_id, target_id)
            except MergeError as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(
                f"merged into {result.target.ref}: {result.moved_submissions} submissions, "
                f"{result.moved_tasks} tasks, {result.moved_documents} documents moved"
                + (" (file link carried)" if result.carried_link else "")
            )
            self.refresh_data()

        return done

    def action_new_submission(self) -> None:
        from ...forms import entities as ef
        from ...repo import orgs as orgs_repo

        conn = self.app.conn
        if not orgs_repo.list_orgs(conn, kind="market"):
            self.notify("no markets on file — create one in the markets screen (m, then a)",
                        severity="warning")
            return
        tab = self._active_tab()
        placement_id = self._acting_key("placements-table") if tab == "tab-placements" else None
        opportunity_id = self._acting_key("pipeline-opps") if tab == "tab-pipeline" else None
        if placement_id is None and opportunity_id is None:
            self.notify("select a placement or opportunity first (s works on those tabs)",
                        severity="warning")
            return
        self._push_form(
            ef.submission_form(conn),
            lambda v: ef.apply_submission(
                conn, v, placement_id=placement_id, opportunity_id=opportunity_id
            ),
        )
