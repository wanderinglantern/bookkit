"""Presentation-agnostic form definitions and the one value parser.

Both surfaces render from these: the TUI through FormModal, the web through
the Jinja form macro. The parser lives here rather than on either renderer so
that money round-trips as cents and a bare number is refused as a date in
exactly one place."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from ..dates import parse_human_date
from ..money import MoneyParseError, format_cents, parse_money_cents
from ..normalize import (
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


@dataclass
class BatchSpec:
    """What undo unit this form's save belongs to.

    A form passes this instead of opening its own transaction: FormModal wraps
    the whole `commit` callback in one batch, so a save that writes four rows
    is reverted as one thing by `R` — and rolls back entirely if any part of
    it refuses. `summary` may be a callable when the sentence needs the values
    the user just typed."""

    tool: str
    summary: str | Callable[[dict[str, Any]], str]
    org_id: str | None = None

    def sentence(self, values: dict[str, Any]) -> str:
        return self.summary(values) if callable(self.summary) else self.summary

    @staticmethod
    def for_title(title: str, org_id: str | None = None) -> BatchSpec:
        """The default every form gets. 'edit contact — Atomic Industries'
        becomes tool 'edit_contact' with the whole title as the summary: the
        changes list groups by tool, so the slug must not carry the record's
        name, while the sentence should."""
        head = title.split("—")[0].split("(")[0].strip().lower()
        return BatchSpec(
            tool="_".join(head.split()[:3]) or "form", summary=title, org_id=org_id
        )


PLACEHOLDERS = {
    "date": "today · fri · +2w · 2026-10-15",
    "money": "1.5m · 250k · 1,500,000",
    "phone": "312 555 0142 · +44 …",
    "email": "name@company.com",
    "linkedin": "profile URL or handle",
    "url": "https://company.com",
    "domain": "company.com",
    "naics": "6-digit code · 524126",
}

# Everything typed gets cleaned on save; textarea (multi-line notes) is the
# one kind stored verbatim.
CLEANERS: dict[str, Callable[[str], str]] = {
    "text": clean_text,
    "email": clean_email,
    "phone": clean_phone,
    "url": clean_url,
    "domain": clean_domain,
    "linkedin": clean_linkedin,
    "naics": clean_naics,
    "textarea": lambda text: text,
}


class FieldError(Exception):
    """A value the parser refused, tagged with the field it came from so a
    renderer can put focus (TUI) or an inline message (web) in the right
    place."""

    def __init__(self, field_key: str, message: str) -> None:
        super().__init__(message)
        self.field_key = field_key
        self.message = message


def date_refusal(text: str) -> str:
    """The one sentence every surface gives when a date will not parse.

    Names the offending value AND the remedy: the old message said only what
    the parser objected to, which tells a user nothing about how to fix it.
    The rule is unchanged — a bare 1-2 digit number is refused, never guessed,
    because dateparser reads "5" as a month and future-biases it. Every
    site that raises this (forms/spec.py, mcpserver.py's tool arguments,
    imports/mappers/book.py's row errors) calls this function rather than
    writing its own copy, so the wording cannot drift by surface again."""
    return (
        f"{text!r} is not a date — enter one like 2026-10-15, friday, or +2w; "
        "a bare number is ambiguous"
    )


def parse_value(field: Field, raw: str | None) -> Any:
    """One raw widget/form string → the stored representation. Money returns
    integer cents, dates return ISO strings, everything else is cleaned."""
    text = (raw or "").strip()
    if field.kind == "textarea":
        # verbatim, but a whitespace-only note is still nothing
        return (raw or "") if (raw or "").strip() else None
    if not text:
        return None
    if field.kind == "date":
        parsed = parse_human_date(text)
        if parsed is None:
            raise ValueError(date_refusal(text))
        return parsed.isoformat()
    if field.kind == "money":
        try:
            return parse_money_cents(text)
        except MoneyParseError as exc:
            raise ValueError(str(exc)) from exc
    if field.kind == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{text!r} is not a whole number") from exc
    cleaner = CLEANERS.get(field.kind, clean_text)
    return cleaner(text)


def parse_values(spec: FormSpec, raw: Mapping[str, str | None]) -> dict[str, Any]:
    """Parse every field in the spec, refusing on the first bad or missing
    required value. Raises FieldError."""
    values: dict[str, Any] = {}
    for field in spec.fields:
        try:
            values[field.key] = parse_value(field, raw.get(field.key))
        except ValueError as exc:
            raise FieldError(field.key, f"{field.label}: {exc}") from exc
        if field.required and values[field.key] in (None, ""):
            raise FieldError(field.key, f"{field.label} is required")
    return values


def initial_text(field: Field, initial: Any) -> str:
    """The string a renderer should pre-fill. Money renders as plain cents
    with no dollar sign, because the parser accepts exactly that back."""
    if initial is None:
        return ""
    if field.kind == "money":
        return format_cents(int(initial)).lstrip("$")
    return str(initial)


def dropped(values: dict[str, Any]) -> dict[str, Any]:
    """Strip None entries so optional blanks don't overwrite on edit."""
    return {k: v for k, v in values.items() if v is not None}
