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
    ]

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("cursor_type", "row")
        kwargs.setdefault("zebra_stripes", True)
        super().__init__(**kwargs)


def rfi_asker_cell(conn: sqlite3.Connection, request: RfiRequest) -> str | Text:
    """The 'asked by' column, shared by the chase queue and the account tab.
    The rule lives in services.rfi.asker_name; this only decides the styling —
    a real market name reads plain, a placeholder (no market, or one merged
    away) is dimmed because it names no one to chase."""
    from ...services import rfi as rfi_svc

    name = rfi_svc.asker_name(conn, request)
    return Text(name, style=theme.DIM) if name in rfi_svc.ASKER_PLACEHOLDERS else name


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


def task_detail_cell(task: Task) -> Text:
    """First line of the long notes, dimmed and clipped — full text lives in
    the e form; this is for review at a glance."""
    if not task.detail:
        return dash()
    first = task.detail.strip().splitlines()[0]
    return Text(first[:57] + "…" if len(first) > 58 else first, style=theme.DIM)


def grouped_by_category(tasks: list[Task]) -> list[Task]:
    """Display-level grouping only — repo ordering (due date, priority)
    stays authoritative for briefs; this just clusters categories together
    on screen, case-insensitively (so "renewal" and "Renewal" cluster as one
    group). "~" sorts uncategorized/undated last."""
    return sorted(tasks, key=lambda t: ((t.category or "~").lower(), t.due_on or "~"))
