"""Quick capture (n, from anywhere): pick the account, choose type, say who
was there, type the note, ctrl-s. Defaults to today and the account you were
looking at. Typed text survives esc and crashes via the draft table; a
follow-up phrase in the note OFFERS a task, never silently creates one."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

import json
from datetime import date

from rapidfuzz import fuzz
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from ...dates import parse_human_date
from ...models import InteractionType
from ...normalize import clean_text
from ...repo import contacts, drafts, interactions, orgs
from ...repo import tasks as tasks_repo
from ...services import batches as batches_svc
from ...services import capture

DRAFT_KEY = "quick_capture"
# The "who was there" fuzzy thresholds (match floor, runner-up margin) and the
# measurements behind them live with the loop in services.capture — the web's
# capture form runs the same resolution, and two copies of a threshold are two
# thresholds the moment either is tuned.


class QuickCapture(ModalScreen):
    app: BookkitApp
    BINDINGS = [
        Binding("escape", "cancel", "Cancel (keeps draft)"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]
    # Only the field list scrolls — the title and the save/cancel hint are
    # pinned so they never leave the screen on short terminals.
    #
    # `max-height: 55vh` on the fields USED to break that promise, exactly as
    # it did on FormModal: it measures against the VIEWPORT while the box is
    # capped at 80% by bookkit.tcss, and the two add up rather than nesting.
    # The overflow is not scrollable, it is gone — at 140x45 neither "who was
    # there" nor the roster under it was painted at all, and at 80x24 the note
    # went with them. `1fr` makes the scroller absorb whatever is left after
    # the chrome instead of claiming its own slice of the screen.
    DEFAULT_CSS = """
    QuickCapture .modal-box {
        height: auto;
    }
    QuickCapture .modal-fields {
        height: 1fr;
        min-height: 3;
    }
    """

    def __init__(self, default_org_id: str | None = None) -> None:
        super().__init__()
        self.default_org_id = default_org_id
        self.selected_org_id: str | None = default_org_id
        self._orgs: list = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Static("LOG INTERACTION", classes="modal-title")
            with VerticalScroll(classes="modal-fields"):
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
                yield Label("who was there (optional)", classes="field-label")
                yield Input(
                    placeholder="names, comma separated — this account's contacts",
                    id="qc-who",
                )
                # the roster, because you cannot type a name you do not know
                yield Static(id="qc-who-known", classes="hint")
                yield Label("note", classes="field-label")
                yield TextArea(id="qc-note")
            yield Static(
                "[b]^s[/b] save · [b]esc[/b] cancel (draft kept)", classes="hint"
            )
            yield Button("Save", variant="primary", id="qc-save")

    def on_mount(self) -> None:
        # Everything below is an INLINE style, and has to be: bookkit.tcss is
        # app-level CSS and outranks a widget's DEFAULT_CSS in Textual however
        # specific the selector, so `QuickCapture .field-label {…}` is read and
        # discarded. Inline is the only lever that wins.
        #
        # `height: 1fr` on the fields put the Save button back inside the box,
        # but it did not put the last two fields on the screen: this is the
        # densest modal in the app, and its content stacked 41 rows into the
        # 26-row viewport a 45-row terminal leaves it. `who was there` began at
        # row 26 and the roster at row 31 — the two things this modal grew
        # last were the two nobody could see. What goes is the CHROME, not the
        # field.
        #
        # The account matches scroll inside 5 rows rather than 6 (the app-wide
        # 10-row cap had already buried the note fields once). 5 is the LARGEST
        # value that still leaves the roster on a 45-row terminal — measured:
        # at 6 the roster falls off, at 5 it does not — and the account is
        # usually pre-filled from the screen you pressed `n` on anyway.
        self.query_one("#qc-org-options", OptionList).styles.max_height = 5
        # A blank line above every label buys readability in a six-row form
        # and costs a whole field in a fourteen-row one. The roster loses its
        # too, so it reads as the caption of the field it describes.
        for label in self.query(".field-label"):
            label.styles.margin = 0
        self.query_one("#qc-who-known", Static).styles.margin = 0
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
        self._refresh_known_contacts()

    def _refresh_known_contacts(self) -> None:
        """Who `who was there` will match, for the account currently picked."""
        known = self.query_one("#qc-who-known", Static)
        roster = self._contacts()
        known.update(
            "on this account: " + " · ".join(c.name for c in roster)
            if roster
            else "no contacts on this account yet"
        )

    def _contacts(self) -> list:
        """Contacts who are still at the client, and still on the book.

        Two different filters, and it is worth being exact about which does
        what. A REMOVED contact is excluded by `base.alive()`, which
        contacts.for_org applies unconditionally — removal takes them off
        attendee lists while the interaction_contact rows survive for an
        undelete, so a removed person must not be offerable for a NEW one.
        What the `active_only=True` default adds on top is the person who has
        not been removed but has LEFT: `active = 0`, still on the account's
        file because their history is, and not somebody who can have been in
        the room this morning. Naming them would be a plausible-looking lie
        on the client's record, which is the one thing this field exists to
        stop."""
        if not self.selected_org_id:
            return []
        return contacts.for_org(self.app.conn, self.selected_org_id)

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
            "who": self.query_one("#qc-who", Input).value,
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
        if saved.get("who"):
            self.query_one("#qc-who", Input).value = saved["who"]
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
        # Names → contact ids through the shared rule in services.capture, so
        # this surface and the web cannot drift on who a typed name resolves to.
        attendees, refusal = capture.resolve_attendees(
            self.app.conn, payload["org_id"], payload["who"]
        )
        if refusal is not None:
            # commit-in-place: the modal stays open with every field intact.
            # Dropping the name silently is the bug this field exists to fix;
            # dropping the whole note would be worse.
            self.notify(refusal, severity="error")
            return
        occurred = parse_human_date(payload["date"] or "today") or date.today()
        subject = clean_text(payload["subject"]) or payload["note"].splitlines()[0][:60]
        # one writer action, one undo unit: the interaction and its attendee
        # links land together or not at all
        with batches_svc.open_batch(
            self.app.conn,
            source="tui",
            tool="log_interaction",
            summary=f"logged {payload['type'] or 'note'} — {subject}",
            org_id=payload["org_id"],
        ):
            interaction = interactions.log(
                self.app.conn,
                payload["org_id"],
                payload["type"] or "note",
                subject,
                occurred.isoformat(),
                body=payload["note"] or None,
                contact_ids=attendees,
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

    DEFAULT_CSS = """
    ConfirmTask .modal-box {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        # enter here covers the unfocused case; a focused title input submits
        # through on_input_submitted instead, so it never fires twice
        Binding("y,enter", "accept", "Yes"),
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
            yield Static(
                "[b]y[/b] / [b]enter[/b] create · [b]n[/b] / [b]esc[/b] skip",
                classes="hint",
            )
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
