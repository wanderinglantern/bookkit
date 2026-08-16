"""ListTable — DataTable with the bookkit keyboard vocabulary (j/k move,
enter opens via the standard RowSelected message). Also home to the shared
task-table helpers used by both the Navigator and Account screens."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.binding import Binding
from textual.widgets import DataTable

from .. import theme
from ..theme import dash

if TYPE_CHECKING:
    import sqlite3

    from ...models import RfiItem, RfiRequest, Task


class ListTable(DataTable):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
        # Y yanks the row to the system clipboard. On every table, because the
        # thing you want out of a terminal — an email, a ref, a premium — was
        # otherwise retyped by hand (review F19).
        Binding("Y", "copy_row", "Copy row", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("cursor_type", "row")
        kwargs.setdefault("zebra_stripes", True)
        super().__init__(**kwargs)

    def action_copy_row(self) -> None:
        """Copy the row under the cursor.

        An email is what you almost always want off a contact row, so a cell
        that looks like one wins outright; otherwise the whole row goes, tab
        separated, which pastes into a spreadsheet as cells."""
        if self.cursor_row is None or not self.row_count:
            return
        # a wrapped cell (see task_detail_wrapped) carries real newlines, and
        # those would break the row apart into extra spreadsheet rows — flatten
        # every cell to one line before joining
        flat = (" ".join(str(cell).split()) for cell in self.get_row_at(self.cursor_row))
        cells = [cell for cell in flat if cell not in ("", "—")]
        if not cells:
            return
        email = next((c for c in cells if "@" in c and " " not in c), None)
        payload = email or "\t".join(cells)
        self.app.copy_to_clipboard(payload)
        self.notify(f"copied {payload if len(payload) < 40 else cells[0]}")


def rfi_asker_cell(conn: sqlite3.Connection, request: RfiRequest) -> str | Text:
    """The 'asked by' column, shared by the chase queue and the account tab.
    The rule lives in services.rfi.asker_name; this only decides the styling —
    a real market name reads plain, a placeholder (no market, or one merged
    away) is dimmed because it names no one to chase."""
    from ...services import rfi as rfi_svc

    name = rfi_svc.asker_name(conn, request)
    return Text(name, style=theme.DIM) if name in rfi_svc.ASKER_PLACEHOLDERS else name


def rfi_state_cell(conn: sqlite3.Connection, request: RfiRequest) -> Text:
    """The 'state' column, shared by the account tab and the navigator's
    requests group.

    This used to be a bare outstanding COUNT on both surfaces, which left a
    WITHDRAWN request rendering exactly like a live one — same amber number —
    while it had silently dropped out of the chase queue, the export and
    open_items with nothing on screen saying why. The count alone also could
    not tell "everything came back" from "nothing is written down yet".

    The rule lives in services.rfi.is_open; this only picks the word and the
    style. Every state carries a word, not just a color."""
    from ...repo import rfi as rfi_repo
    from ...services import rfi as rfi_svc

    if request.cancelled_at:
        return Text("withdrawn", style=theme.DIM)
    open_count = rfi_repo.open_item_count(conn, request.id)
    if open_count:
        return Text(f"{open_count} open", style=theme.AMBER)
    # is_open is what separates "nothing left to chase" from "nothing typed up
    # yet": a request with no items reaches no chase queue and no export, so
    # saying so here is the only place a user can learn it
    if rfi_svc.is_open(conn, request.id):
        return Text("no items yet", style=theme.AMBER)
    return Text("closed", style=theme.GREEN)


def rfi_due_cell(item: RfiItem, request: RfiRequest) -> str | Text:
    """The 'needed by' column for an RFI item. An item's own date reads plain;
    one inherited from its request is DIM, so an inherited date is never
    mistaken for one stored on the item (the cell is inline-editable, and its
    edit buffer is seeded from the item, so editing an inherited date starts
    blank and saving is a deliberate override)."""
    from ...services import rfi as rfi_svc

    if item.due_on:
        return item.due_on
    inherited = rfi_svc.effective_due(item, request)
    return Text(inherited, style=theme.DIM) if inherited else dash()


def detail_cell(value: str | None, width: int = 58) -> Text:
    """First line of a long free-text field, dimmed and clipped — the full
    text lives in the `e` form; this is for review at a glance.

    Shared, because a field the form lets you TYPE and no table ever shows is
    a note that vanishes the moment you save it (review F33, and again for RFI
    item detail)."""
    if not value:
        return dash()
    first = value.strip().splitlines()[0]
    return Text(
        first[: width - 1] + "…" if len(first) > width else first, style=theme.DIM
    )


def task_detail_cell(task: Task) -> Text:
    """For the one-line task tables. A table that gives its detail column a
    fixed width and its rows auto-height wants task_detail_wrapped instead."""
    return detail_cell(task.detail)


def task_detail_wrapped(task: Task) -> Text:
    """The whole of the long notes, dimmed, for a fixed-width column on an
    auto-height row — Rich wraps it to the column and the row grows to fit.

    Only safe on a plain ListTable: the inline cell editor is one line tall and
    anchors to the top of the cell, so on a table with auto-height rows it would
    float over line one with stale text still showing beneath it."""
    if not task.detail:
        return dash()
    return Text(task.detail.strip(), style=theme.DIM)


def grouped_by_category(tasks: list[Task]) -> list[Task]:
    """Display-level grouping only — repo ordering (due date, priority)
    stays authoritative for briefs; this just clusters categories together
    on screen, case-insensitively (so "renewal" and "Renewal" cluster as one
    group). "~" sorts uncategorized/undated last."""
    return sorted(tasks, key=lambda t: ((t.category or "~").lower(), t.due_on or "~"))
