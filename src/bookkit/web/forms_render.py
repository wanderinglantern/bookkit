"""Render any FormSpec to HTML, and one inline cell.

One macro renders every form in bookkit.forms.entities, because they are all
the same dataclass. Adding a Field to a builder makes the input appear on both
surfaces — there is no second list to update. render_cell renders the same
Field, one at a time, for the inline-editable columns declared once in
bookkit.forms.inline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..forms.spec import PLACEHOLDERS, Field, FormSpec, initial_text
from .app import TEMPLATES


@dataclass(frozen=True)
class _Row:
    field: Field
    value: str
    placeholder: str


def _rows(spec: FormSpec, submitted: dict[str, str] | None) -> list[_Row]:
    rows: list[_Row] = []
    for f in spec.fields:
        if submitted is not None:
            value = submitted.get(f.key, "")
        else:
            value = initial_text(f, spec.initial.get(f.key))
        rows.append(_Row(f, value, f.placeholder or PLACEHOLDERS.get(f.kind, "")))
    return rows


def render_form(
    request: Any,
    spec: FormSpec,
    action: str,
    error: str | None = None,
    submitted: dict[str, str] | None = None,
) -> str:
    """The form fragment. On a refused save pass `submitted` and `error` — the
    user's input is re-rendered exactly as typed, which is commit-in-place."""
    template = TEMPLATES.env.get_template("macros/form.html")
    module = template.make_module({})
    render = module.form  # type: ignore[attr-defined]
    return str(render(spec, action, _rows(spec, submitted), error))


def render_cell(
    request: Any,
    field: Field,
    value: str,
    action: str,
    error: str | None = None,
) -> str:
    """One editable table cell: its display value until activated, then the
    same input the form macro renders for this field's kind. On a refusal
    `value` is what the user typed (not the stored value) and `error` sits
    beside the input — nothing is retyped, nothing is written."""
    template = TEMPLATES.env.get_template("macros/cell.html")
    module = template.make_module({})
    render = module.cell  # type: ignore[attr-defined]
    placeholder = field.placeholder or PLACEHOLDERS.get(field.kind, "")
    return str(render(field, value, placeholder, action, error))
