"""Client onboarding wizard: steps left, current step's summary right.

Every step commits immediately through the same forms the rest of the app
uses; esc leaves at any time and loses nothing (the data IS the state —
reopening derives position from services.onboarding.completeness)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from ...services import onboarding
from .. import theme

if TYPE_CHECKING:
    from ..app import BookkitApp

_GLYPHS = {
    onboarding.COMPLETE: (f"[{theme.GREEN}]✓[/]", "done"),
    onboarding.PARTIAL: (f"[{theme.AMBER}]◐[/]", "partial"),
    onboarding.UNTOUCHED: (f"[{theme.DIM}]○[/]", "open"),
}


class OnboardingScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("enter", "do_step", "Fill in", priority=True),
        Binding("s", "skip_step", "Skip for now"),
        Binding("escape", "close", "Done for now"),
    ]

    def __init__(self, org_id: str) -> None:
        super().__init__()
        self.org_id = org_id
        self._statuses: list[onboarding.StepStatus] = []

    def compose(self) -> ComposeResult:
        yield Static(id="onboard-title")
        with Horizontal(id="onboard-split"):
            yield ListView(id="onboard-steps")
            with Vertical(id="onboard-pane"):
                yield Static(id="onboard-summary")
                yield Static(
                    "[b]enter[/b] fill in · [b]s[/b] skip · [b]esc[/b] done for now",
                    classes="hint",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data(jump_to_first_incomplete=True)

    def refresh_data(self, jump_to_first_incomplete: bool = False) -> None:
        conn = self.app.conn
        from ...repo import orgs

        org = orgs.get(conn, self.org_id)
        self._statuses = onboarding.completeness(conn, self.org_id)
        done = "all set" if onboarding.is_complete(conn, self.org_id) else "in progress"
        self.query_one("#onboard-title", Static).update(
            f"[b]onboarding — {org.name}[/b]  [{theme.DIM}]({done})[/]"
        )
        steps = self.query_one("#onboard-steps", ListView)
        keep = steps.index or 0
        steps.clear()
        for status in self._statuses:
            glyph, word = _GLYPHS[status.state]
            required = "" if status.step.required else f" [{theme.DIM}](optional)[/]"
            steps.append(ListItem(Label(f"{glyph} {status.step.label}{required} · {word}")))
        if jump_to_first_incomplete:
            target = onboarding.first_incomplete(conn, self.org_id)
            keys = [s.step.key for s in self._statuses]
            steps.index = keys.index(target) if target else 0
        else:
            steps.index = min(keep, len(self._statuses) - 1)
        self._render_summary()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._render_summary()

    def _current_status(self) -> onboarding.StepStatus:
        index = self.query_one("#onboard-steps", ListView).index or 0
        return self._statuses[index]

    def _render_summary(self) -> None:
        status = self._current_status()
        self.query_one("#onboard-summary", Static).update(
            f"[b]{status.step.label}[/b]\n\n{status.summary}"
        )

    def action_skip_step(self) -> None:
        steps = self.query_one("#onboard-steps", ListView)
        if steps.index is not None and steps.index < len(self._statuses) - 1:
            steps.index += 1

    def action_close(self) -> None:
        self.dismiss(None)

    def action_do_step(self) -> None:
        pass  # Task 4
