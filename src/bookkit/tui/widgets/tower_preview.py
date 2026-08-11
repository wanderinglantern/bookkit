"""Tower preview — towerkit's own ASCII renderer, straight onto the account
screen. Rendering is towerkit's job; this widget only sizes and displays,
re-rendering whenever its real size changes (a tab pane lays out at zero size
before it is first shown)."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.widgets import Static
from towerkit.model import load_program
from towerkit.render.ascii import render_ascii
from towerkit.theme import load_theme


class TowerPreview(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._path: Path | None = None

    def show_placeholder(self) -> None:
        self._path = None
        self.update(Text("(no program file)", style="dim"))

    def show_program(self, path: Path) -> None:
        self._path = path
        self._render_current()

    def on_resize(self) -> None:
        if self._path is not None:
            self._render_current()

    def _render_current(self) -> None:
        path = self._path
        if path is None:
            return
        width, height = self.size.width, self.size.height
        if width < 20 or height < 6:  # pane not laid out yet; resize will re-run
            return
        try:
            program = load_program(path)
        except Exception as exc:  # file may be mid-edit in towerkit's TUI
            self.update(Text(f"cannot render {path.name}: {exc}", style="red"))
            return
        ansi = render_ascii(
            program, load_theme(None), width=max(46, width - 2), height=max(12, height - 2)
        )
        self.update(Text.from_ansi(ansi))
