"""Setup dialogue: where the towerkit program files live. Opens on demand
(, from Today) and automatically the first time sync runs unconfigured."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from ... import sync
from ...repo import settings


class SettingsModal(ModalScreen):
    """Dismisses True when roots were saved, None on cancel."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("SETUP — PROGRAM FILE LOCATIONS", classes="modal-title")
            yield Static(
                "Directories holding towerkit program JSON (one per line).\n"
                "Sync scans them recursively; linking runs from there."
            )
            yield TextArea(id="settings-roots")
            yield Static(id="settings-preview", classes="hint")
            yield Static("ctrl-s save · esc cancel", classes="hint")
            yield Button("Save", variant="primary", id="settings-save")

    def on_mount(self) -> None:
        area = self.query_one("#settings-roots", TextArea)
        area.text = "\n".join(str(r) for r in sync.configured_roots(self.app.conn))
        area.focus()
        self._preview()

    def _roots(self) -> list[Path]:
        text = self.query_one("#settings-roots", TextArea).text
        return [Path(line.strip()).expanduser() for line in text.splitlines() if line.strip()]

    def _preview(self) -> None:
        roots = self._roots()
        if not roots:
            self.query_one("#settings-preview", Static).update(
                "e.g.  ~/Developer/programs"
            )
            return
        bits = []
        for root in roots:
            if not root.is_dir():
                bits.append(f"✗ {root} — not a directory")
            else:
                bits.append(f"✓ {root} — {len(sync.scan([root]))} program file(s)")
        self.query_one("#settings-preview", Static).update("\n".join(bits))

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-save":
            self.action_save()

    def action_save(self) -> None:
        roots = self._roots()
        if not roots:
            self.notify("add at least one directory", severity="error")
            return
        missing = [r for r in roots if not r.is_dir()]
        if missing:
            self.notify(f"not a directory: {missing[0]}", severity="error")
            return
        settings.set_program_roots(self.app.conn, [str(r) for r in roots])
        self.notify(f"saved {len(roots)} root(s)")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(None)
