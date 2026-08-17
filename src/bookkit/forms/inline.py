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

RFI_ITEM_FIELDS: tuple[Field, ...] = (
    Field("prompt", "item", required=True),
    Field("category", "group"),
    Field("due_on", "needed by", "date"),
    Field("response", "response"),
)
