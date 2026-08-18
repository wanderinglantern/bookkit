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
        from ...forms import entities as ef
        from ...repo import orgs
        from ..widgets.forms import FormModal

        conn = self.app.conn
        key = self._current_status().step.key
        org = orgs.get(conn, self.org_id)
        draft = f"onboarding:{self.org_id}:{key}"

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data(jump_to_first_incomplete=True)

        if key == "org":
            spec = ef.org_form_initial_profile(conn, org)
            spec.title = "account basics"
            self.app.push_screen(
                FormModal(spec, commit=lambda v: _none(ef.apply_org(conn, v, org)),
                          draft_key=draft),
                done,
            )
        elif key == "contacts":
            self.app.push_screen(
                FormModal(ef.contact_form(),
                          commit=lambda v: _none(ef.apply_contact(conn, org.id, v)),
                          draft_key=draft),
                done,
            )
        elif key == "program":
            self.app.push_screen(
                FormModal(ef.placement_form(conn=conn),
                          commit=lambda v: _none(ef.apply_placement(conn, v, org.id)),
                          draft_key=draft),
                done,
            )
        elif key == "projects":
            self._project_then_need(done, draft)
        elif key == "followups":
            self.app.push_screen(
                FormModal(ef.task_form(conn=conn, default_org_id=org.id),
                          commit=lambda v: _none(ef.apply_task(conn, v, org_id=org.id)),
                          draft_key=draft),
                done,
            )

    def _project_then_need(self, done, draft: str) -> None:
        """Route to whichever half of "projects & needs" is still open.

        A bare project (one already created but with no needs recorded —
        e.g. the user escaped out of the need form last time) goes straight
        to its need form; otherwise start a brand-new project and chain
        into its first need on save. Either way, once the project exists in
        the DB the need form's *own* done-callback always refreshes —
        whether the need form is saved or cancelled — so an escape there
        never leaves the wizard showing the step as untouched, and re-
        entering the step routes to adding a need on the project that
        already exists instead of spawning a duplicate."""
        from ...forms import entities as ef
        from ...models import Project
        from ...repo import projects as projects_repo
        from ..widgets.forms import FormModal

        conn = self.app.conn

        def open_need_form(project: Project, need_draft: str) -> None:
            def need_done(_values: dict | None) -> None:
                # The project is already persisted by the time we get here
                # regardless of what happens to the need form, so always
                # refresh — never gate this on values being non-None.
                self.refresh_data(jump_to_first_incomplete=True)

            self.app.push_screen(
                FormModal(ef.need_form(conn=conn),
                          commit=lambda v: _none(ef.apply_need(conn, v, project.id)),
                          draft_key=need_draft),
                need_done,
            )

        bare = [
            p
            for p in projects_repo.projects_for_org(conn, self.org_id)
            if not projects_repo.needs_for_project(conn, p.id)
        ]
        if bare:
            open_need_form(bare[0], draft + ":need")
            return

        created: list = []

        def project_saved(values: dict | None) -> None:
            if values is None:
                done(None)
                return
            open_need_form(created[-1], draft + ":need")

        def commit_project(v: dict) -> None:
            created.append(ef.apply_project(conn, v, org_id=self.org_id))

        self.app.push_screen(
            FormModal(ef.project_form(), commit=commit_project, draft_key=draft),
            project_saved,
        )


def _none(result: object) -> None:
    """Adapt apply_* return values to the commit contract (None = success)."""
    return None
