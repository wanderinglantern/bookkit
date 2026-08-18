"""Which fields are editable in place, for both surfaces.

The TUI declared these first (`tui/widgets/inline_edit.py` and the screens
that use it): a row's editable cells are editable where they sit, no button,
no modal. The web's inline cells (`web.forms_render.render_cell`) follow the
same rule (design doc 2026-08-17, "Editing: inline first") — which fields are
inline-editable is not a per-surface choice, so the list lives once, here.

Each screen still needs its own column-index mapping (a TUI table layout
detail); it builds that dict from the ordered tuple below rather than
declaring the Field objects twice."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from ..repo import vocab
from .spec import Field

CONTACT_FIELDS: tuple[Field, ...] = (
    Field("role", "role"),
    Field("title", "title"),
    Field("email", "email", "email"),
    Field("phone", "phone", "phone"),
)

TASK_FIELDS: tuple[Field, ...] = (
    Field("due_on", "due", "date"),
    Field("title", "task", required=True),
    Field("category", "category"),
    Field("description", "description"),
)


def task_fields(conn: sqlite3.Connection) -> tuple[Field, ...]:
    """TASK_FIELDS with the category cell's vocabulary filled in.

    The inline cell is the PRIMARY edit path on both surfaces (`i` on the Open
    Items tab, click-the-cell on the web); the add/edit modal is the secondary
    one. So the field that decides whether a task leaves the building has to
    offer "Internal" here, not only there — repo.vocab.task_categories always
    includes it, so the one flag that changes what the client receives is
    discoverable before anybody has typed it once.

    A function, not a constant, because the vocabulary is DATA: it grows as
    the book does. The column positions stay static (they are layout, and each
    screen maps them itself), which is why this returns the same fields in the
    same order TASK_FIELDS declares them."""
    categories = tuple(vocab.task_categories(conn))
    return tuple(
        replace(f, suggestions=categories) if f.key == "category" else f
        for f in TASK_FIELDS
    )


RFI_ITEM_FIELDS: tuple[Field, ...] = (
    Field("prompt", "item", required=True),
    Field("category", "group"),
    Field("due_on", "needed by", "date"),
    Field("response", "response"),
)
