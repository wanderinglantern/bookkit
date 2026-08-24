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

from ..models import (
    CONTACT_ROLES,
    NEED_STATUSES,
    PROJECT_STATUSES,
    PlacementStatus,
)
from ..repo import assignees, vocab
from .spec import Field

CONTACT_FIELDS: tuple[Field, ...] = (
    # A PICKER, not a text box. The modal form has offered a select over
    # CONTACT_ROLES since it was written, while this — the PRIMARY edit path
    # on both surfaces — took anything at all, so "risk manager", "Risk
    # Manager" and "RM" could all sit on the same book beside the eleven
    # declared spellings and no filter would ever gather them.
    #
    # These are only the DECLARED options; `contact_fields(conn)` widens them
    # to include what the book already stores, and every route that PARSES a
    # role must use that widened field (checked_option is authoritative on the
    # way in, so the narrow list would refuse a role already on the record).
    # The suggestions half is for the TUI, whose inline editor is a one-line
    # Input for every kind — it completes from the same list the web renders
    # as <option>s, so neither surface is the weaker one.
    Field(
        "role", "role", "select",
        tuple((r, r) for r in CONTACT_ROLES),
        optional_select=True, suggestions=CONTACT_ROLES,
    ),
    Field("title", "title"),
    Field("email", "email", "email"),
    Field("phone", "phone", "phone"),
)


def contact_fields(conn: sqlite3.Connection) -> tuple[Field, ...]:
    """CONTACT_FIELDS with the role and title vocabularies filled in from the
    book — the same shape task_fields has, and for the same reason.

    `role` becomes a picker over the declared vocabulary UNION every role
    already stored (repo.vocab.contact_roles). That constrains new entry
    without stranding anything already typed: a select whose options are only
    the declared eleven would make `checked_option` refuse a legacy role on
    its own record, and the cell would then be unsaveable until somebody
    re-classified the contact — a picker must offer only what is storable, and
    equally must not refuse what is already stored.

    `title` gets SUGGESTIONS, not options: a title is prose off a signature
    block, so the valid set is not knowable and a select would refuse the next
    real one. Completion is the whole win there.

    A function, not a constant, because a vocabulary is DATA and grows as the
    book does. Field ORDER is unchanged, because each screen maps column
    positions off it."""
    roles = tuple(vocab.contact_roles(conn))
    titles = tuple(vocab.contact_titles(conn))
    widened: dict[str, Field] = {}
    for f in CONTACT_FIELDS:
        if f.key == "role":
            widened[f.key] = replace(
                f, options=tuple((r, r) for r in roles), suggestions=roles
            )
        elif f.key == "title":
            widened[f.key] = replace(f, suggestions=titles)
    return tuple(widened.get(f.key, f) for f in CONTACT_FIELDS)


TASK_FIELDS: tuple[Field, ...] = (
    Field("due_on", "due", "date"),
    Field("title", "task", required=True),
    Field("category", "category"),
    Field("description", "description"),
    # NOT a column on `task`. One typed string that repo.assignees turns
    # into three columns — which is why every surface routes an assignee
    # save through assignees.set_on_task rather than the generic one-key
    # update the other four cells use.
    Field("assignee", "assignee"),
)


def task_fields(
    conn: sqlite3.Connection, org_id: str | None = None
) -> tuple[Field, ...]:
    """TASK_FIELDS with the category and assignee cells' vocabularies filled in.

    The inline cell is the PRIMARY edit path on both surfaces (`i` on the Open
    Items tab, click-the-cell on the web); the add/edit modal is the secondary
    one. So the field that decides whether a task leaves the building has to
    offer "Internal" here, not only there — repo.vocab.task_categories always
    includes it, so the one flag that changes what the client receives is
    discoverable before anybody has typed it once.

    A function, not a constant, because the vocabulary is DATA: it grows as
    the book does. The column positions stay static (they are layout, and each
    screen maps them itself), which is why this returns the same fields in the
    same order TASK_FIELDS declares them.

    `org_id` scopes the assignee suggestions to one account's own contacts,
    on top of the team and the market contacts that are offered everywhere.
    A table spanning accounts (the navigator's attention pane) passes None
    and offers the two unscoped sources — the account's own people are still
    typeable, they just land as freeform, which is the safe side of the
    export."""
    categories = tuple(vocab.task_categories(conn))
    people = tuple(c.label for c in assignees.candidates(conn, org_id))
    vocabs = {"category": categories, "assignee": people}
    return tuple(
        replace(f, suggestions=vocabs[f.key]) if f.key in vocabs else f
        for f in TASK_FIELDS
    )


PLACEMENT_FIELDS: tuple[Field, ...] = (
    Field("program_name", "program", required=True),
    Field("period_from", "effective", "date", required=True),
    Field("period_to", "expiry", "date", required=True),
    Field(
        "status", "status", "select",
        tuple((s.value, s.value) for s in PlacementStatus), required=True,
    ),
    Field("commission_bps", "commission (bps)", "int"),
)
"""A placement's header facts, as cells. KEYS ARE services.placement_edit's
OWN VOCABULARY (FILE_OWNED + BOOK_OWNED): the cell route hands {key: value}
to that service's split, so which owner a field writes to is decided in one
place. `status` is a select over the real statuses — the same tuple the TUI
form offers — because a status typo would silently fall out of every
status-filtered view."""

LAYER_FIELDS: tuple[Field, ...] = (
    Field("name", "layer", required=True),
    Field("policy_number", "policy no"),
    # REQUIRED, both of them. A layer without an attachment point and a limit
    # is not a layer — towerkit's own model demands both — and declaring them
    # optional let a blank field through as None, which reached sync.add_layer
    # and came back to the broker as
    # "unsupported operand type(s) for %: 'NoneType' and 'int'" (found by
    # review, 2026-08-19). Premium stays optional: a layer is routinely placed
    # before it is priced.
    Field("attach_cents", "attaches at", "money", required=True),
    Field("limit_cents", "limit", "money", required=True),
    Field("premium_cents", "premium", "money"),
    Field("period_from", "from", "date"),
    Field("period_to", "to", "date"),
)
"""A towerkit layer's editable facts.

KEYS ARE `sync.update_layer`'S OWN KEYWORD NAMES, so a cell route passes
`**{key: value}` straight through and no translation table can drift from the
writer it feeds.

`signed_pct` and `statutory` are NOT here, and must not be: signed is the sum
of the participants' shares and statutory is a towerkit model rule, so both are
DERIVED. A cell that offers to edit a derived value writes nothing and reads as
broken — the same reason `status` is kept out of RFI_ITEM_FIELDS.

Money is cents on the wire and dollars in the file: `sync._require_dollars`
REFUSES a sub-dollar amount rather than rounding it, and that refusal is meant
to reach the field as its error text."""

PARTICIPANT_FIELDS: tuple[Field, ...] = (
    Field("carrier", "market", required=True),
    # A seat with no share is not a seat. Blank reached add_participant as None
    # and surfaced as a towerkit type error rather than "share is required".
    Field("share_pct", "share", "share", required=True),
    # THIS market's own premium, where it differs from its share of the
    # layer's — a differential, tax and fees on one paper, a non-concurrent
    # quote (Grant, 2026-08-24). OPTIONAL, and blank is a real answer that
    # CLEARS the whole layer back to a premium split by share: towerkit's
    # rule is all-or-nothing, because a seat left deriving beside stated ones
    # derives from a base that already contains their money. It CONFIRMS
    # first (routes/program.py `_market_premium_save`), naming every figure
    # given up — the blank moves every number in the table and arrives on
    # blur, which makes it the easiest of these writes to trigger by accident.
    #
    # Not part of the ADD form — a market is bound at a share, and its own
    # premium is a correction made afterwards, if at all. `_market_add_fields`
    # is what drops it.
    Field("premium_cents", "premium", "money"),
)
"""A market's seat on a layer.

`share_pct` is the "share" kind, which delegates to towerkit's one percent→bps
rule. The key says pct and the stored value is bps on purpose — the same
distinction layer_details already draws, so nobody compares a participant's
share to a layer total and lands a hundred out."""


PROJECT_FIELDS: tuple[Field, ...] = (
    Field("name", "project", required=True),
    Field("site", "site"),
    Field("status", "status", "select", tuple((s, s) for s in PROJECT_STATUSES)),
    Field("start_on", "start", "date"),
    Field("end_on", "end", "date"),
    Field("description", "description"),
)
"""A client project's editable facts.

KEYS ARE `projects_repo.update_project`'S OWN COLUMN NAMES, so a cell route
passes `**{key: value}` straight through — the same rule LAYER_FIELDS follows.

`ref` is not here and must not be: it is minted by `ids.next_ref` and printed
so a person can quote it, which is the opposite of a value they may retype.
The status vocabulary is models.PROJECT_STATUSES — controlled but extensible,
the TEAM_ROLES pattern — and NOT a hand-written list, so a status added to the
model appears here without a second edit."""

NEED_FIELDS: tuple[Field, ...] = (
    Field("line", "line of coverage", required=True),
    Field("needed_by", "needed by", "date", required=True),
    Field("limit_cents", "limit", "money"),
    Field("premium_indication_cents", "premium indication", "money"),
    Field("status", "status", "select", tuple((s, s) for s in NEED_STATUSES)),
    Field("notes", "notes"),
)
"""One insurance need on a project.

`line` stays FREE TEXT with completion rather than becoming a picker, and that
is a deliberate reading of the constrained-input rule: the valid set has to be
knowable for a picker to be right, and lines of coverage are not — a broker will
name cover this book has never carried. A picker there would refuse a real
answer, which the research says is worse than no picker. `need_fields()` below
wires the suggestions.

`opportunity_id` and `placement_id` are NOT editable cells. They are set by
linking (the need → opportunity flow) and are shown as a derived "linked"
column; a cell offering to retype an id writes nothing a person can reason
about and reads as broken — the same reason `signed_pct` is kept out of
LAYER_FIELDS."""


def need_fields(conn: sqlite3.Connection) -> tuple[Field, ...]:
    """NEED_FIELDS with the line cell's vocabulary filled in — the mirror of
    task_fields and rfi_item_fields, and for the same reason: the whole-record
    form completes `line` from the book's own lines (forms.entities.need_form),
    so an inline cell that did not would offer less than the modal beside it."""
    lines = tuple(vocab.lines(conn))
    return tuple(
        replace(field, suggestions=lines) if field.key == "line" else field
        for field in NEED_FIELDS
    )


RFI_ITEM_FIELDS: tuple[Field, ...] = (
    Field("prompt", "item", required=True),
    Field("category", "group"),
    Field("due_on", "needed by", "date"),
    Field("response", "response"),
)


def rfi_item_fields(conn: sqlite3.Connection) -> tuple[Field, ...]:
    """RFI_ITEM_FIELDS with the category cell's vocabulary filled in — the
    mirror of task_fields, and it was missing.

    `rfi_item_form` has completed the category from repo.vocab.rfi_categories
    since it was written, so the SECONDARY edit path (the whole-record modal)
    offered the book's own grouping labels while the PRIMARY one (the inline
    cell, on both surfaces) offered nothing. The categories are what a request
    is grouped by on the client-facing export, so two spellings of the same
    group split one section into two.

    SUGGESTIONS, not options: unlike a contact's role there is no declared RFI
    category vocabulary — the labels are whatever this book files asks under —
    so the set is open and a select would refuse the next new one. On an empty
    book this is legitimately empty, and the cell renders with no datalist
    rather than with an empty one."""
    categories = tuple(vocab.rfi_categories(conn))
    return tuple(
        replace(f, suggestions=categories) if f.key == "category" else f
        for f in RFI_ITEM_FIELDS
    )
