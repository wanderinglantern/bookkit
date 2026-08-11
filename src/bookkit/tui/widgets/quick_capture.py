"""Quick capture (n, from anywhere): pick the account, choose type, type the
note, ctrl-s. Defaults to today and the account you were looking at. Typed
text survives esc and crashes via the draft table; a follow-up phrase in the
note OFFERS a task, never silently creates one."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

import json
from datetime import date

from rapidfuzz import fuzz
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from ...dates import parse_human_date
from ...models import InteractionType
from ...normalize import clean_text
from ...repo import drafts, interactions, orgs
from ...repo import tasks as tasks_repo
from ...services import capture

DRAFT_KEY = "quick_capture"


class QuickCapture(ModalScreen):
    app: BookkitApp
    BINDINGS = [
        Binding("escape", "cancel", "Cancel (keeps draft)"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    def __init__(self, default_org_id: str | None = None) -> None:
        super().__init__()
        self.default_org_id = default_org_id
        self.selected_org_id: str | None = default_org_id
        self._orgs: list = []

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("LOG INTERACTION", classes="modal-title")
            yield Label("account", classes="field-label")
            yield Input(placeholder="type to fuzzy-match…", id="qc-org")
            yield OptionList(id="qc-org-options")
            yield Label("type", classes="field-label")
            yield Select(
                [(t.value, t.value) for t in InteractionType],
                value="note",
                allow_blank=False,
                id="qc-type",
            )
            yield Label("date (today, fri, +2w, 2026-10-15…)", classes="field-label")
            yield Input(value="today", id="qc-date")
            yield Label("subject", classes="field-label")
            yield Input(placeholder="what happened", id="qc-subject")
            yield Label("note", classes="field-label")
            yield TextArea(id="qc-note")
            yield Static("ctrl-s save · esc cancel (draft kept)", classes="hint")
            yield Button("Save", variant="primary", id="qc-save")

    def on_mount(self) -> None:
        self._orgs = orgs.list_orgs(self.app.conn)
        self._restore_draft()
        org_input = self.query_one("#qc-org", Input)
        if self.selected_org_id and not org_input.value:
            org_input.value = orgs.get(self.app.conn, self.selected_org_id).name
        self._refresh_options(org_input.value)
        if org_input.value:
            self.query_one("#qc-subject", Input).focus()
        else:
            org_input.focus()

    # --- account fuzzy pick ---------------------------------------------------

    def _refresh_options(self, text: str) -> None:
        options = self.query_one("#qc-org-options", OptionList)
        options.clear_options()
        scored = []
        for org in self._orgs:
            score = fuzz.WRatio(text, org.name) if text else 50
            if score >= 55 or not text:
                scored.append((score, org))
        scored.sort(key=lambda pair: -pair[0])
        for _, org in scored[:10]:
            options.add_option(Option(f"{org.name}  [{org.kind}]", id=org.id))
        if scored:
            self.selected_org_id = scored[0][1].id

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "qc-org":
            self._refresh_options(event.value)
        self._save_draft()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._save_draft()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.selected_org_id = event.option.id
        self.query_one("#qc-org", Input).value = str(event.option.prompt).rsplit("  [", 1)[0]
        self.query_one("#qc-subject", Input).focus()

    # --- drafts ---------------------------------------------------------------

    def _payload(self) -> dict:
        return {
            "org": self.query_one("#qc-org", Input).value,
            "org_id": self.selected_org_id,
            "type": self.query_one("#qc-type", Select).value,
            "date": self.query_one("#qc-date", Input).value,
            "subject": self.query_one("#qc-subject", Input).value,
            "note": self.query_one("#qc-note", TextArea).text,
        }

    def _save_draft(self) -> None:
        if self.is_mounted:
            drafts.save(self.app.conn, DRAFT_KEY, json.dumps(self._payload()))

    def _restore_draft(self) -> None:
        raw = drafts.load(self.app.conn, DRAFT_KEY)
        if not raw:
            return
        try:
            saved = json.loads(raw)
        except json.JSONDecodeError:
            return
        if saved.get("org"):
            self.query_one("#qc-org", Input).value = saved["org"]
            self.selected_org_id = saved.get("org_id") or self.selected_org_id
        if saved.get("type"):
            self.query_one("#qc-type", Select).value = saved["type"]
        if saved.get("date"):
            self.query_one("#qc-date", Input).value = saved["date"]
        if saved.get("subject"):
            self.query_one("#qc-subject", Input).value = saved["subject"]
        if saved.get("note"):
            self.query_one("#qc-note", TextArea).text = saved["note"]
        self.notify("draft restored")

    # --- save / cancel --------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "qc-save":
            self.action_save()

    def action_save(self) -> None:
        # drain every field directly — Input.Submitted never fired for these
        payload = self._payload()
        if not payload["org_id"]:
            self.notify("pick an account first", severity="error")
            return
        if not payload["subject"] and not payload["note"]:
            self.notify("nothing to save", severity="error")
            return
        occurred = parse_human_date(payload["date"] or "today") or date.today()
        subject = clean_text(payload["subject"]) or payload["note"].splitlines()[0][:60]
        interaction = interactions.log(
            self.app.conn,
            payload["org_id"],
            payload["type"] or "note",
            subject,
            occurred.isoformat(),
            body=payload["note"] or None,
        )
        drafts.clear(self.app.conn, DRAFT_KEY)
        self.notify("logged")
        suggestion = capture.suggest_task(f"{payload['subject']} {payload['note']}")
        self.dismiss(interaction.id)
        if suggestion is not None:
            self.app.push_screen(
                ConfirmTask(payload["org_id"], suggestion, source_interaction_id=interaction.id)
            )

    def action_cancel(self) -> None:
        self._save_draft()  # esc must not lose typed text
        self.dismiss(None)


class ConfirmTask(ModalScreen):
    app: BookkitApp
    """The offered follow-up task. Enter/y creates, esc/n declines."""

    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y", "accept", "Yes"),
    ]

    def __init__(
        self, org_id: str, suggestion: capture.TaskSuggestion, source_interaction_id: str
    ) -> None:
        super().__init__()
        self.org_id = org_id
        self.suggestion = suggestion
        self.source_interaction_id = source_interaction_id

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("CREATE FOLLOW-UP TASK?", classes="modal-title")
            yield Static(
                f"the note says {self.suggestion.phrase!r} — "
                f"create a task due {self.suggestion.due_on.isoformat()}?"
            )
            yield Input(value=self.suggestion.phrase.capitalize(), id="ct-title")
            yield Static("y / enter create · n / esc skip", classes="hint")
            yield Button("Create task", variant="primary", id="ct-yes")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ct-yes":
            self.action_accept()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_accept()

    def action_accept(self) -> None:
        title = self.query_one("#ct-title", Input).value or self.suggestion.phrase
        tasks_repo.create(
            self.app.conn,
            title,
            org_id=self.org_id,
            due_on=self.suggestion.due_on.isoformat(),
            source_interaction_id=self.source_interaction_id,
        )
        self.notify(f"task created, due {self.suggestion.due_on.isoformat()}")
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)
