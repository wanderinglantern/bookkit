"""Inline cell editing for tables — i edits the row under the cursor in
place, spreadsheet style. A slim one-line Input opens over the cell itself:
enter commits and closes, tab / shift+tab commit and hop across the row's
editable cells, esc cancels. Values run through the same Field parsers as
the modal forms (dates, money, phone, email), and every commit is a single
field write in the event log — u undoes it.

The screen wires a table up by setting `inline_fields` (column index →
forms.Field) and `inline_initial` (row_key, field_key → current raw text)
after each fill, then handles CellEdited by writing to the repo."""

from __future__ import annotations

from collections.abc import Callable
from typing import Self

from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.message import Message
from textual.widgets import Input

from .. import theme
from .forms import _PLACEHOLDERS, Field, FormModal
from .tables import ListTable


class InlineTable(ListTable):
    """ListTable with in-cell editing (i) on columns the screen marks editable."""

    BINDINGS = [
        Binding("i", "inline_edit", "Edit in cell", show=False),
    ]

    class CellEdited(Message):
        """A cell's value parsed cleanly and should be written to the repo."""

        def __init__(
            self, table: InlineTable, row_key: str, field: Field, value, coordinate: Coordinate
        ) -> None:
            super().__init__()
            self.table = table
            self.row_key = row_key
            self.field = field
            self.value = value
            self.coordinate = coordinate

        @property
        def control(self) -> InlineTable:
            return self.table

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # column index → Field spec; empty dict means the current rows
        # aren't inline-editable (the modal form still is, via e)
        self.inline_fields: dict[int, Field] = {}
        # (row_key, field_key) → raw text to prefill the editor with
        self.inline_initial: Callable[[str, str], str] = lambda _rk, _fk: ""
        self._editor: CellEditor | None = None

    @property
    def editing(self) -> bool:
        """A cell editor is open over this table. Screens check this before
        rebuilding rows — a refresh mid-edit yanks the cell out from under it."""
        return self._editor is not None

    # -- opening ---------------------------------------------------------------

    def action_inline_edit(self) -> None:
        if not self.inline_fields or self.cursor_row is None or not self.row_count:
            return
        first = min(self.inline_fields)
        self._open_editor(Coordinate(self.cursor_row, first))

    def _row_key_at(self, row: int) -> str | None:
        try:
            value = self.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value
        except Exception:
            return None
        return str(value) if value else None

    def _open_editor(self, coordinate: Coordinate) -> None:
        # opening always supersedes: never leave the previous editor floating
        # over a cell we have already decided to move off of
        self._close_editor()
        row_key = self._row_key_at(coordinate.row)
        field = self.inline_fields.get(coordinate.column)
        if row_key is None or field is None:
            return
        try:
            region = self._get_cell_region(coordinate)
        except Exception:
            return  # cell scrolled out of a rebuilt table — nothing to anchor to
        # cell region is in the table's virtual space; place the editor on the
        # screen at the cell's on-screen position
        origin = self.content_region.offset - self.scroll_offset + region.offset
        width = max(region.width, 24)
        max_x = self.content_region.right
        x = min(origin.x, max_x - width)
        editor = CellEditor(
            self, row_key, coordinate, field,
            initial=self.inline_initial(row_key, field.key),
            placeholder=field.placeholder or _PLACEHOLDERS.get(field.kind, ""),
        )
        self._editor = editor
        self.screen.mount(editor)
        editor.styles.offset = (max(x, 0), origin.y)
        editor.styles.width = width
        editor.focus()

    # -- editor callbacks ------------------------------------------------------

    def _commit(self, row_key: str, coordinate: Coordinate, field: Field, raw: str) -> bool:
        """Parse and post. True when the value was accepted.

        row_key was captured when the editor OPENED — a mid-edit refresh can
        reorder rows, and the write must land on the record the user saw."""
        try:
            value = FormModal._parse(field, raw)
        except ValueError as exc:
            self.app.notify(str(exc), severity="error")
            return False
        if field.required and value is None:
            self.app.notify(f"{field.label} can't be empty", severity="error")
            return False
        self.post_message(self.CellEdited(self, row_key, field, value, coordinate))
        return True

    def _hop(self, coordinate: Coordinate, direction: int) -> None:
        """Move the editor to the next/previous editable column in the row.

        The screen owns inline_fields and can swap it (a refresh landing
        mid-edit, the pane switching to a different list). When the column we
        started on is no longer editable there is nowhere to hop to, so close
        — never raise out of a keypress and take the app down."""
        columns = sorted(self.inline_fields)
        if coordinate.column not in columns:
            self._close_editor()
            return
        pos = columns.index(coordinate.column) + direction
        if 0 <= pos < len(columns):
            self._open_editor(Coordinate(coordinate.row, columns[pos]))
        else:
            self._close_editor()

    def cancel_edit(self) -> None:
        """Abandon any open editor. Screens call this before re-pointing the
        table at different rows — esc semantics, so nothing is written.
        Call it while the table is still visible: the editor hands focus back
        to the table, and a hidden table cannot take it."""
        self._close_editor()

    def clear(self, columns: bool = False) -> Self:
        # the rows the editor is anchored to are about to stop existing —
        # abandon the edit rather than float over a stale cell (esc semantics)
        self._close_editor()
        return super().clear(columns=columns)

    def _close_editor(self) -> None:
        if self._editor is not None:
            editor, self._editor = self._editor, None
            # only pull focus back if the editor still had it — when the user
            # clicked away, focus already belongs somewhere they chose
            had_focus = editor.has_focus
            editor.remove()
            if had_focus:
                self.focus()


class CellEditor(Input):
    """The one-line input that floats over the cell being edited."""

    DEFAULT_CSS = f"""
    CellEditor {{
        position: absolute;
        height: 1;
        padding: 0 1;
        border: none;
        background: {theme.PANEL};
        color: {theme.GOLD};
        text-style: bold;
    }}
    CellEditor:focus {{
        background: {theme.RULE};
        /* Input:focus's tall border outranks the bare type selector and
           would eat both rows of a height-1 widget — kill it here too */
        border: none;
    }}
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter", "commit_close", "Save", priority=True),
        Binding("tab", "commit_next", "Save + next", priority=True),
        Binding("shift+tab", "commit_prev", "Save + previous", priority=True),
    ]

    def __init__(
        self,
        table: InlineTable,
        row_key: str,
        coordinate: Coordinate,
        field: Field,
        initial: str,
        placeholder: str = "",
    ) -> None:
        super().__init__(value=initial, placeholder=placeholder)
        self._table = table
        self._row_key = row_key
        self._coordinate = coordinate
        self._field = field

    def action_cancel(self) -> None:
        self._table._close_editor()

    def action_commit_close(self) -> None:
        if self._table._commit(self._row_key, self._coordinate, self._field, self.value):
            self._table._close_editor()

    def action_commit_next(self) -> None:
        if self._table._commit(self._row_key, self._coordinate, self._field, self.value):
            self._table._hop(self._coordinate, +1)

    def action_commit_prev(self) -> None:
        if self._table._commit(self._row_key, self._coordinate, self._field, self.value):
            self._table._hop(self._coordinate, -1)

    def on_blur(self) -> None:
        # clicking elsewhere abandons the edit — esc semantics, never a
        # surprise write
        if self._table._editor is self:
            self._table._close_editor()
