"""Generic keyboard-first form modal.

One declarative Field list → one modal. Values are drained directly from the
widgets on save (never trust Input.Submitted), parsed per kind (human dates,
money shorthand, ints), validated, and returned to the caller through
dismiss(); the caller owns the repo call. Money round-trips as cents, dates
as ISO YYYY-MM-DD.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from ...forms.spec import (
    PLACEHOLDERS,
    BatchSpec,
    Field,
    FieldError,
    FormSpec,
    initial_text,
    parse_values,
)


class _Refused(Exception):
    """A commit callback returned an error string. Raised so the surrounding
    transaction rolls back — a refused save must leave nothing behind — then
    unwrapped back into that same string for the form to display."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class FormModal(ModalScreen):
    """Dismisses with a {key: parsed_value} dict, or None on cancel.

    With `commit` set (the default wiring across the app), the save itself
    runs while the form is still open: an error string or exception keeps
    the form up with every field intact, so a refusal is corrected in place
    instead of retyped from scratch."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]
    # The box hugs its content and only the field list scrolls, so the title
    # and the "^s save" hint stay on screen however long the form is.
    #
    # `max-height: 55vh` on the fields USED to break that promise: it is
    # measured against the viewport, while the box is capped at 80% by
    # bookkit.tcss, and the two add up. Below ~34 rows the hint and the Save
    # button landed outside the box — invisible, while Tab still reached them
    # and Enter still fired. `1fr` makes the scroller absorb whatever is left
    # after the chrome instead of claiming its own slice of the screen.
    DEFAULT_CSS = """
    FormModal .modal-box {
        height: auto;
    }
    FormModal .modal-fields {
        height: 1fr;
        min-height: 3;
    }
    """

    def __init__(
        self,
        spec: FormSpec,
        commit: Callable[[dict[str, Any]], str | None] | None = None,
        draft_key: str | None = None,
        batch: BatchSpec | Literal[False] | None = None,
    ) -> None:
        super().__init__()
        self.spec = spec
        self._commit = commit
        self._draft_key = draft_key
        # Batching is the DEFAULT, not an opt-in: 33 call sites build a
        # FormModal directly rather than through entity_actions.push_form, and
        # any one of them left unbatched is a save that `R` cannot reach and
        # that `u` can only half undo. `batch=False` opts a form out.
        if batch is None:
            batch = BatchSpec.for_title(spec.title)
        self._batch: BatchSpec | None = batch or None

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Static(self.spec.title.upper(), classes="modal-title")
            with VerticalScroll(classes="modal-fields"):
                yield from self._compose_fields()
            yield Static("[b]^s[/b] save · [b]esc[/b] cancel", classes="hint")
            yield Button("Save", variant="primary", id="form-save")

    def _compose_fields(self) -> ComposeResult:
        for f in self.spec.fields:
            suffix = " *" if f.required else ""
            yield Label(f"{f.label}{suffix}", classes="field-label")
            initial = self.spec.initial.get(f.key)
            if f.kind == "select":
                # NB: Select.BLANK is a plain False in Textual 8.x — the
                # real no-selection sentinel is Select.NULL.
                yield Select(
                    list(f.options),
                    value=initial if initial is not None else Select.NULL,
                    allow_blank=f.optional_select or initial is None,
                    id=f"form-{f.key}",
                )
            elif f.kind == "textarea":
                area = TextArea(id=f"form-{f.key}")
                area.text = str(initial) if initial else ""
                yield area
            else:
                from textual.suggester import SuggestFromList

                yield Input(
                    value=initial_text(f, initial),
                    placeholder=f.placeholder or PLACEHOLDERS.get(f.kind, ""),
                    id=f"form-{f.key}",
                    suggester=(
                        SuggestFromList(f.suggestions, case_sensitive=False)
                        if f.suggestions
                        else None
                    ),
                )
                if f.suggestions:
                    from textual_autocomplete import AutoComplete

                    yield AutoComplete(
                        f"#form-{f.key}", candidates=list(f.suggestions)
                    )

    def on_mount(self) -> None:
        if self._draft_key:
            self._restore_draft(self._draft_key)
        first = self.spec.fields[0]
        self.query_one(f"#form-{first.key}").focus()

    def _restore_draft(self, draft_key: str) -> None:
        import json

        from ...repo import drafts

        payload = drafts.load(self.app.conn, draft_key)
        if not payload:
            return
        try:
            saved: dict[str, str] = json.loads(payload)
        except ValueError:
            return  # unreadable scratch is not worth an error
        for f in self.spec.fields:
            raw = saved.get(f.key)
            if not raw:
                continue
            widget = self.query_one(f"#form-{f.key}")
            if isinstance(widget, Input) and not widget.value:
                widget.value = raw
            elif isinstance(widget, TextArea) and not widget.text:
                widget.text = raw
            elif isinstance(widget, Select) and widget.value == Select.NULL:
                try:
                    widget.value = raw
                except Exception:  # option list changed since the draft — skip
                    pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-save":
            self.action_save()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    def action_save(self) -> None:
        raw = {f.key: self._drain(f) for f in self.spec.fields}
        try:
            values = parse_values(self.spec, raw)
        except FieldError as exc:
            self.notify(exc.message, severity="error")
            self.query_one(f"#form-{exc.field_key}").focus()
            return
        if self._commit is not None:
            try:
                error = self._run_commit(values)
            except Exception as exc:  # a failed save must never crash the TUI
                error = str(exc)
            if error is not None:
                self.notify(error, severity="error")
                return
        if self._draft_key:
            from ...repo import drafts

            drafts.clear(self.app.conn, self._draft_key)
        self.dismiss(values)

    def _run_commit(self, values: dict[str, Any]) -> str | None:
        """Run the save, inside one batch when the form declared one.

        Without a BatchSpec this is the old behaviour verbatim, so a form that
        has not been converted yet still works — the conversion is per-form,
        not all-or-nothing."""
        assert self._commit is not None
        if self._batch is None:
            return self._commit(values)

        from ...services import batches as batches_svc

        try:
            with batches_svc.open_batch(
                self.app.conn,
                source="tui",
                tool=self._batch.tool,
                summary=self._batch.sentence(values),
                org_id=self._batch.org_id,
            ):
                error = self._commit(values)
                if error is not None:
                    raise _Refused(error)
        except _Refused as refused:
            return refused.message
        return None

    def _drain(self, f: Field) -> str | None:
        widget = self.query_one(f"#form-{f.key}")
        if isinstance(widget, Select):
            return None if widget.value == Select.NULL else str(widget.value)
        if isinstance(widget, TextArea):
            return widget.text
        if isinstance(widget, Input):
            return widget.value
        raise TypeError(f"unexpected form widget for {f.key}: {type(widget).__name__}")

    def action_cancel(self) -> None:
        if self._draft_key:
            import json

            from ...repo import drafts

            raw = {f.key: (self._drain(f) or "") for f in self.spec.fields}
            if any(raw.values()):
                drafts.save(self.app.conn, self._draft_key, json.dumps(raw))
            else:
                drafts.clear(self.app.conn, self._draft_key)
        self.dismiss(None)
