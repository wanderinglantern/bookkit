"""Render any FormSpec to HTML, and one inline cell, in its two states.

One macro renders every form in bookkit.forms.entities, because they are all
the same dataclass. Adding a Field to a builder makes the input appear on both
surfaces — there is no second list to update. render_cell_display and
render_cell render the same Field, one at a time, for the inline-editable
columns declared once in bookkit.forms.inline: the display half is the
persistent state (one per row/column, always in the DOM), the editor half is
fetched and swapped in on activation, so a table never carries a hidden form
per cell — only the one cell being edited ever has one.

Both macros ASSUME a route contract that Task 8 wires up: `action` is the
base `…/cell/{key}` URL; the display cell fetches its editor from
`action + "/edit"`; the editor posts to `action` and reverts (Escape or
blur) by re-fetching `action`. Both swaps are outerHTML — see
macros/cell.html's comments for why innerHTML left a stale listener that
discarded a click into the input as a re-fetch.

`tag` (default "td") is the wrapping element. Contacts' card panel (Task 8,
fix round 1) passes "div" — a `<td>` outside a table-row ancestor is
silently dropped by the HTML parser, so the tag has to track whatever the
caller's actual container is, not stay hardcoded."""

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
    tag: str = "td",
    extra_class: str = "",
    suffix: str = "",
) -> str:
    """The persistent state of one inline-editable cell: its value, or an
    em-dash when empty. `action` is the base cell URL; activation (click, or
    Enter while focused) fetches the editor from `action + "/edit"`.

    `tag` is the wrapping element — "td" for a real `<table class="rows">`
    row (every caller before Task 8), "div" for a container that isn't a
    table (Task 8's contacts card panel). A `<td>` outside a table-row
    ancestor is dropped outright by the HTML parser, attributes and all —
    see macros/cell.html's docstring comment — so this can't default to
    anything but the caller's actual container.

    `extra_class` adds a column class (e.g. "num") and `suffix` appends
    caller-built, already-safe HTML (a badge) inside this SAME cell —
    the whole point is that this call already returns the cell's own
    `<td>`/`<th>`, so a caller must never wrap the result in another one to
    get either effect. See macros/cell.html's `display` docstring for why
    that nests illegally and silently misaligns every later column."""
    template = TEMPLATES.env.get_template("macros/cell.html")
    module = template.make_module({})
    render = module.display  # type: ignore[attr-defined]
    return str(render(field, value, action, tag, extra_class, suffix))


def render_cell(
    request: Any,
    field: Field,
    value: str,
    action: str,
    error: str | None = None,
    tag: str = "td",
    extra_class: str = "",
) -> str:
    """The editor swapped into one cell on activation: the same input the
    form macro renders for this field's kind, `autofocus` because exactly one
    of these is ever on the page at a time. On a refusal `value` is what the
    user typed (not the stored value) and `error` sits beside the input —
    nothing is retyped, nothing is written. `tag`/`extra_class` — see
    render_cell_display."""
    template = TEMPLATES.env.get_template("macros/cell.html")
    module = template.make_module({})
    render = module.editor  # type: ignore[attr-defined]
    placeholder = field.placeholder or PLACEHOLDERS.get(field.kind, "")
    return str(render(field, value, placeholder, action, error, tag, extra_class))
