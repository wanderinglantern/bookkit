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
    from ...models import Task


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
