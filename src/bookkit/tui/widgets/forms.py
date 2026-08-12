"""Generic keyboard-first form modal.

One declarative Field list → one modal. Values are drained directly from the
widgets on save (never trust Input.Submitted), parsed per kind (human dates,
money shorthand, ints), validated, and returned to the caller through
dismiss(); the caller owns the repo call. Money round-trips as cents, dates
as ISO YYYY-MM-DD.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from ...dates import parse_human_date
from ...money import MoneyParseError, format_cents, parse_money_cents
from ...normalize import (
    clean_domain,
    clean_email,
    clean_linkedin,
    clean_naics,
    clean_phone,
    clean_text,
    clean_url,
)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    # text | textarea | select | date | money | int
    # + normalised kinds: email | phone | url | domain | linkedin | naics
    kind: str = "text"
    options: tuple[tuple[str, str], ...] = ()  # (label, value) for select
    required: bool = False
    placeholder: str = ""
    optional_select: bool = False  # allow_blank for selects
    # existing-record vocabulary: dropdown menu (tab/enter picks) plus inline
    # ghost text (right arrow accepts) — data consistency by completion
    suggestions: tuple[str, ...] = ()


@dataclass
class FormSpec:
    title: str
    fields: list[Field]
    initial: dict[str, Any] = dc_field(default_factory=dict)


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

    def __init__(
        self,
        spec: FormSpec,
        commit: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        super().__init__()
        self.spec = spec
        self._commit = commit

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static(self.spec.title.upper(), classes="modal-title")
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
                        value=self._initial_text(f, initial),
                        placeholder=f.placeholder or _PLACEHOLDERS.get(f.kind, ""),
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
            yield Static("ctrl-s save · esc cancel", classes="hint")
            yield Button("Save", variant="primary", id="form-save")

    @staticmethod
    def _initial_text(f: Field, initial: Any) -> str:
        if initial is None:
            return ""
        if f.kind == "money":
            return format_cents(int(initial)).lstrip("$")
        return str(initial)

    def on_mount(self) -> None:
        first = self.spec.fields[0]
        self.query_one(f"#form-{first.key}").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-save":
            self.action_save()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    def action_save(self) -> None:
        values: dict[str, Any] = {}
        for f in self.spec.fields:
            raw = self._drain(f)
            try:
                values[f.key] = self._parse(f, raw)
            except ValueError as exc:
                self.notify(f"{f.label}: {exc}", severity="error")
                self.query_one(f"#form-{f.key}").focus()
                return
            if f.required and values[f.key] in (None, ""):
                self.notify(f"{f.label} is required", severity="error")
                self.query_one(f"#form-{f.key}").focus()
                return
        if self._commit is not None:
            try:
                error = self._commit(values)
            except Exception as exc:  # a failed save must never crash the TUI
                error = str(exc)
            if error is not None:
                self.notify(error, severity="error")
                return
        self.dismiss(values)

    def _drain(self, f: Field) -> str | None:
        widget = self.query_one(f"#form-{f.key}")
        if isinstance(widget, Select):
            return None if widget.value == Select.NULL else str(widget.value)
        if isinstance(widget, TextArea):
            return widget.text
        if isinstance(widget, Input):
            return widget.value
        raise TypeError(f"unexpected form widget for {f.key}: {type(widget).__name__}")

    @staticmethod
    def _parse(f: Field, raw: str | None) -> Any:
        text = (raw or "").strip()
        if not text:
            return None
        if f.kind == "date":
            parsed = parse_human_date(text)
            if parsed is None:
                raise ValueError(f"cannot read a date from {text!r}")
            return parsed.isoformat()
        if f.kind == "money":
            try:
                return parse_money_cents(text)
            except MoneyParseError as exc:
                raise ValueError(str(exc)) from exc
        if f.kind == "int":
            try:
                return int(text)
            except ValueError as exc:
                raise ValueError(f"{text!r} is not a whole number") from exc
        cleaner = _CLEANERS.get(f.kind, clean_text)
        return cleaner(text)

    def action_cancel(self) -> None:
        self.dismiss(None)


_PLACEHOLDERS = {
    "date": "today · fri · +2w · 2026-10-15",
    "money": "1.5m · 250k · 1,500,000",
    "phone": "312 555 0142 · +44 …",
    "email": "name@company.com",
    "linkedin": "profile URL or handle",
}

# Everything typed gets cleaned on save; textarea (multi-line notes) is the
# one kind stored verbatim.
_CLEANERS = {
    "text": clean_text,
    "email": clean_email,
    "phone": clean_phone,
    "url": clean_url,
    "domain": clean_domain,
    "linkedin": clean_linkedin,
    "naics": clean_naics,
    "textarea": lambda text: text,
}


def dropped(values: dict[str, Any]) -> dict[str, Any]:
    """Strip None entries so optional blanks don't overwrite on edit."""
    return {k: v for k, v in values.items() if v is not None}
