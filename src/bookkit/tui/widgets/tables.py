"""ListTable — DataTable with the bookkit keyboard vocabulary (j/k move,
enter opens via the standard RowSelected message)."""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import DataTable


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
