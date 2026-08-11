"""Tower preview — towerkit's own ASCII renderer, straight onto the account
screen. Rendering is towerkit's job; this widget only sizes and displays."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.widgets import Static
from towerkit.model import load_program
from towerkit.render.ascii import render_ascii
from towerkit.theme import load_theme


class TowerPreview(Static):
    def show_placeholder(self) -> None:
        self.update(Text("(no program file)", style="dim"))

    def show_program(self, path: Path) -> None:
        try:
            program = load_program(path)
        except Exception as exc:  # file may be mid-edit in towerkit's TUI
            self.update(Text(f"cannot render {path.name}: {exc}", style="red"))
            return
        width = max(46, (self.size.width or 76) - 2)
        height = max(12, (self.size.height or 26) - 2)
        ansi = render_ascii(program, load_theme(None), width=width, height=height)
        self.update(Text.from_ansi(ansi))
