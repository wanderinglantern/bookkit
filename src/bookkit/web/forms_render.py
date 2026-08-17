"""Render any FormSpec to HTML, and one inline cell, in its two states.

One macro renders every form in bookkit.forms.entities, because they are all
the same dataclass. Adding a Field to a builder makes the input appear on both
surfaces — there is no second list to update. render_cell_display and
render_cell render the same Field, one at a time, for the inline-editable
columns declared once in bookkit.forms.inline: the display half is the
persistent state (one per row/column, always in the DOM), the editor half is
fetched and swapped in on activation, so a table never carries a hidden form
per cell — only the one cell being edited ever has one.

Both macros ASSUME a route contract that nothing here wires up yet (Task 8
owns the routes): `action` is the base `…/cell/{key}` URL; the display cell
fetches its editor from `action + "/edit"`; the editor posts to `action` and
reverts (Escape) by re-fetching `action`. Both swaps are outerHTML — see
macros/cell.html's comments for why innerHTML left a stale listener that
discarded a click into the input as a re-fetch."""

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


def render_cell_display(
    request: Any,
    field: Field,
    value: str,
    action: str,
) -> str:
    """The persistent state of one inline-editable cell: its value, or an
    em-dash when empty. `action` is the base cell URL; activation (click, or
    Enter while focused) fetches the editor from `action + "/edit"`."""
    template = TEMPLATES.env.get_template("macros/cell.html")
    module = template.make_module({})
    render = module.display  # type: ignore[attr-defined]
    return str(render(field, value, action))


def render_cell(
    request: Any,
    field: Field,
    value: str,
    action: str,
    error: str | None = None,
) -> str:
    """The editor swapped into one cell on activation: the same input the
    form macro renders for this field's kind, `autofocus` because exactly one
    of these is ever on the page at a time. On a refusal `value` is what the
    user typed (not the stored value) and `error` sits beside the input —
    nothing is retyped, nothing is written."""
    template = TEMPLATES.env.get_template("macros/cell.html")
    module = template.make_module({})
    render = module.editor  # type: ignore[attr-defined]
    placeholder = field.placeholder or PLACEHOLDERS.get(field.kind, "")
    return str(render(field, value, placeholder, action, error))
