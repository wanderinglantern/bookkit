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
    MARKET_RESPONSE_STATUS_LABELS,
    MARKET_RESPONSE_STATUSES,
    NEED_STATUSES,
    PROJECT_STATUSES,
    PUBLIC_DECLINE_REASON_LABELS,
    PUBLIC_DECLINE_REASONS,
    RATE_PER_CHOICES,
    RATING_BASES,
    PlacementStatus,
    rating_basis,
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


# --- marketing: what a market said, edited where it is printed --------------

MARKET_RESPONSE_FIELDS: tuple[Field, ...] = (
    # ATTACH AND LIMIT ARE TWO CELLS, not the one "Layer" column the workbook
    # prints. Same reason the grid splits the workbook's single "Total est.
    # cost" into TRIA / Fees / SL tax: "$5M xs $5M" is DERIVED from these two
    # and nobody can type it. Blank attach reads as primary / the whole line,
    # which is the ordinary case — so neither is required.
    #
    # This is NOT the layer-attachment rule (`_LAYER_CELLS` drops
    # `attach_cents` because a slab's attachment comes from its POSITION in a
    # tower). A market response is not a slab: it is what one carrier said
    # about one band, quoted before any tower exists, and the band it answered
    # on is a fact off their email.
    Field("attach", "attaches at", "money"),
    Field("lim", "limit", "money"),
    # HOW WE REACHED THE PAPER — not WHICH paper. A NAME, resolved to a market
    # by the route the way the add row's `via` is, and BLANK MEANS DIRECT
    # (routes/marketing.py `_save_access` and marketing_grid.DIRECT own the
    # word). Its suggestions are filled per-connection by
    # `market_response_fields`, because the book's market list is data.
    Field("via_org_id", "access point", placeholder="blank = a direct approach"),
    # REQUIRED, and rendered with a blank option anyway (macros/cell.html) —
    # `required` refuses the empty submit, and the blank stops the browser
    # answering the question. A response left untouched filing itself as
    # "quoted" is the exact bug that rule exists for, on this exact field.
    Field(
        "status", "status", "select",
        tuple((MARKET_RESPONSE_STATUS_LABELS[s], s) for s in MARKET_RESPONSE_STATUSES),
        required=True,
    ),
    Field("responded_on", "replied", "date"),
    # WHEN THESE TERMS DIE, on the row that stated them. A LOOKING-FORWARD
    # date, unlike every other one on this row: a quote expires next month, so
    # `check_not_future` is deliberately not applied to it (the declaration
    # lives in tests/test_marketing_gates.py's FORWARD_LOOKING). The ordering
    # against the reply and the send date IS checked, in
    # repo.marketing._expiry_guard, where every surface inherits it.
    #
    # THE CELL IS THE POINT. `services.quotes` keys the whole chase queue on
    # this date and the panel had nowhere to put it, so a quote recorded here
    # — premium, limit, terms and all — never reached the queue whose own
    # module header calls that gap "the only one that loses money rather than
    # time" (Grant, 2026-08-26).
    Field("quote_expires_on", "quote expires", "date"),
    # The one "rate" kind in the book. NOT money: 1.42 is 1.42 per unit of
    # exposure, and money's parser would read "$1.42" as 142 cents.
    Field("rate_micros", "rate", "rate"),
    Field("premium", "premium", "money"),
    # THE THREE THAT DECIDE A TOTAL. NULL is "nobody has told us" and 0 is "we
    # asked, there is none" — only 0 contributes, so all three have to be able
    # to hold a typed zero, and clearing one back to blank has to put the
    # total back to unknown. Neither is a default.
    Field("tria_premium", "TRIA", "money"),
    Field("policy_fees", "policy fees", "money"),
    Field("surplus_lines_tax", "surplus lines tax", "money"),
    # TWO DECLINE REASONS, NEVER ONE FIELD WITH A "SAFE TO SHARE" TICK.
    # Real decline reasons are routinely unusable verbatim ("underwriter
    # doesn't like the loss runs, off the record"), and a single field guarded
    # by a checkbox fails the first time somebody forgets to tick it — a
    # failure whose consequence is a client reading an underwriter's private
    # opinion. The LABELS are the marking, and they are what the cell editor
    # announces (aria-label) and what the column header repeats.
    Field(
        "decline_reason_public", "reason sent to the client", "select",
        tuple((PUBLIC_DECLINE_REASON_LABELS[r], r) for r in PUBLIC_DECLINE_REASONS),
        optional_select=True,
    ),
    Field("decline_reason", "internal note, never sent to a client", "textarea"),
)
"""What a market said, as inline cells. KEYS ARE `market_response` COLUMN NAMES,
so a cell route hands `{key: value}` straight to repo.marketing.edit_response —
which rolls the submission's status up after every write, because two
hand-maintained copies of one fact disagree and then nobody knows which is right.

WHAT IS DELIBERATELY ABSENT, and why each one would read as broken:

* `market_org_id` — retyping the CARRIER is not correcting a figure, it is
  RE-SCOPING the approach onto a different market: a different underwriter
  answered, and the premium, the reply date and the reason on the row all
  belonged to somebody else. Same rule team assignments follow (CLAUDE.md:
  corrected in place, never re-scoped). `via_org_id` IS HERE and is not the
  same act — the market is the same market and the answer is the same answer;
  what is being corrected is how we got to it, which is the one fact on this
  row a broker cannot know until the submission actually goes out. Recording
  a wholesaler and finding out it went direct had NO fix on any surface
  (Grant, 2026-08-26): not a cell, not a form, not an MCP argument.
* the Total — derived from premium + TRIA + fees + tax, and blank while any of
  them is unknown. You cannot type a total.
* the rate movement — derived from the line's expiring rate.
* Best — the carrier's A.M. Best rating, which belongs to the market, not to
  what it said about this placement.
* Subj. — a COUNT of open subjectivity rows on the submission. A cell over it
  would accept "3" and write nothing, which is the `signed_pct` mistake
  LAYER_FIELDS already refuses to repeat.
"""


MARKET_APPROACH_FIELDS: tuple[Field, ...] = (
    # AT LEAST ONE OF THESE TWO, not both required — a submission out to a
    # wholesaler whose carrier is not yet known is a real row, and a row
    # addressed to nobody is not. The cross-field rule is enforced in
    # services.marketing_entry and again in repo.marketing.create_response (and
    # under both, a DB CHECK), never only in a route.
    Field("market", "carrier"),
    # THE SAME WORDS THE COLUMN ABOVE IT USES. This box and the Access cell
    # write the SAME fact and were labelled two different ways on one screen
    # ("via (wholesaler or MGA)" here, "access point" three inches up), which
    # is the DRY rule applied to what a person READS rather than to code.
    Field("via", "access point (wholesaler or MGA)"),
    Field("attach", "attaches at", "money", placeholder="blank = primary"),
    Field("lim", "limit", "money"),
    Field("sent_on", "sent", "date"),
    Field(
        "status", "status", "select",
        tuple((MARKET_RESPONSE_STATUS_LABELS[s], s) for s in MARKET_RESPONSE_STATUSES),
        required=True,
    ),
)
"""The in-row add form: recording that we went to a market on a line.

AN EXPLICIT SAVE, not inline cells (CLAUDE.md: "NOT the rule for whole forms").
Six fields are filled in one go and tabbing between them must not commit half a
row — a half-written approach is a market this book claims to have gone to.

`market` and `via` are NAMES here, not ids, because the route resolves them the
way the MCP tool does. Both complete from the book's existing markets
(`market_approach_fields`), which is the same vocabulary rule the participation
add row follows: freehand carrier spelling is how 'Zurich Insurance Group' vs
'Zurich' drift starts.

NO PREMIUM, NO RATE, NO FEES. Every one of those comes off a quote letter that
does not exist yet when an approach is recorded, and the data-entry rule is
that a figure off a document is never pre-filled and never asked for early —
they are cells on the row the moment it exists.
"""


def market_response_fields(conn: sqlite3.Connection) -> tuple[Field, ...]:
    """MARKET_RESPONSE_FIELDS with the access point completing from the book's
    own markets — the same enrichment `market_approach_fields` makes to the
    same vocabulary one row down, so the box a broker types a wholesaler into
    when RECORDING an approach and the box they correct it in offer the very
    same names.

    ONLY THE EDITOR NEEDS IT. The display half of a cell prints a name it was
    handed; it is the editor that has to complete one, which is why the grid's
    first render still reads the plain tuple and only routes/marketing.py's
    editor route pays for this query."""
    markets = tuple(vocab.market_names(conn))
    return tuple(
        replace(f, suggestions=markets) if f.key == "via_org_id" else f
        for f in MARKET_RESPONSE_FIELDS
    )


def market_approach_fields(conn: sqlite3.Connection) -> tuple[Field, ...]:
    """MARKET_APPROACH_FIELDS with both market cells completing from the book's
    own markets — the mirror of `contact_fields` and `task_fields`.

    SUGGESTIONS, not options: the carrier on a submission may be a market this
    book has never carried, and a select would refuse the next real one. The
    route still resolves what is typed against the book and refuses a name it
    cannot find, naming the nearest — advisory here, authoritative there."""
    markets = tuple(vocab.market_names(conn))
    return tuple(
        replace(f, suggestions=markets) if f.key in {"market", "via"} else f
        for f in MARKET_APPROACH_FIELDS
    )


# --- what a LINE OF COVERAGE on a placement is expected to do ---------------

_BASIS_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (b.label, b.key) for b in RATING_BASES
)
"""The rating-basis picker, off models.RATING_BASES — never a hand-written
list. A basis added to the model appears in every picker without a second
edit, and a basis NOT in the model cannot be stored, which is what keeps
`rating_basis()` from having to guess whether an exposure is money."""

_RATE_PER_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (label, str(value)) for value, label in RATE_PER_CHOICES
)
"""A PICKER, NOT A NUMBER. `rate_per` is the denominator that makes a rate
mean anything — 1.42 per $100 of payroll and 1.42 per $1,000 of sales are ten
times apart — and the three conventions in use are a knowable set, which is
exactly when the constrained-input rule says a picker. Free text there would
accept 10000, and every rate on the line would then be printed against a
denominator no market quoted."""


def placement_line_fields(
    rating_basis_key: str | None = None,
    expiring_basis_key: str | None = None,
) -> tuple[Field, ...]:
    """The block header's cells: what this line is being rated on, what it is
    asking for, and what it did last year.

    KEYS ARE `placement_line` COLUMN NAMES, so a cell route hands
    `{key: value}` straight to repo.marketing.set_placement_line — the same
    rule LAYER_FIELDS and MARKET_RESPONSE_FIELDS follow.

    A FUNCTION, NOT A CONSTANT, because two of the nine change KIND with the
    data. An exposure is integer CENTS on a monetary basis and a whole COUNT
    on any other, and models.RatingBasis.monetary is the one place that
    decides which — read here, never re-judged. A fleet is 42 power units, and
    42 cannot be cents; the same digits rendered the wrong way put $0.42 in
    front of a client where 42 units belonged (found on the client sheet,
    2026-08-25).

    THE BASIS MUST BE KNOWN BEFORE THE FIGURE. With no basis stored the
    exposure field stays `count`, which refuses an amount rather than
    accepting one and filing it as cents — the same refusal MCP's
    `set_placement_line` gives, and the route says which basis to set first.
    Guessing is the bug; refusing is the feature.

    `expiring_rate_micros` is a rate and NOT money: 1.42 is 1.42 per unit of
    exposure, and money's parser would read '$1.42' as 142 cents. It is a
    COLUMN and not the whole answer: where the expiring premium and exposure
    are both recorded the rate IS their quotient and nobody is asked for it
    (`services.marketing_report.expiring_rate`, 2026-08-27), and a typed
    figure outranks that division. With no expiring exposure recorded there is
    nothing to divide and the report leaves the comparison blank rather than
    assuming exposure was flat.
    """

    def exposure_kind(basis_key: str | None) -> str:
        if basis_key is None:
            return "count"
        return "money" if rating_basis(basis_key).monetary else "count"

    return (
        Field("rating_basis", "rating basis", "select", _BASIS_OPTIONS,
              optional_select=True),
        Field("rate_per", "rate per", "select", _RATE_PER_OPTIONS,
              optional_select=True),
        Field("expected_exposure", "expected exposure",
              exposure_kind(rating_basis_key)),
        # BLANK IS PRIMARY, and that is the ordinary answer rather than a gap
        # — the same reading `MarketResponse.attach` gets. Stated only for an
        # excess layer.
        Field("attach_sought", "attach sought", "money"),
        Field("limit_sought", "limit sought", "money"),
        Field("expiring_basis", "expiring basis", "select", _BASIS_OPTIONS,
              optional_select=True),
        Field("expiring_exposure", "expiring exposure",
              exposure_kind(expiring_basis_key)),
        Field("expiring_premium", "expiring premium", "money"),
        Field("expiring_rate_micros", "expiring rate", "rate"),
        # THE ONE FREEFORM FIELD ON THIS HEADER, and the only one a client
        # reads as written. The LABEL is the marking — the same rule
        # `decline_reason` / `decline_reason_public` settled above, where a
        # single field guarded by a "safe to share" tick fails the first time
        # somebody forgets to tick it. It is last because it is a different
        # KIND of fact from the eight figures before it: those are what the
        # line is expected to do, this is what somebody wants said about it.
        # THE LABEL IS THE MARKING and has to stand ALONE — it is what the
        # cell editor announces (aria-label), where the group heading above it
        # is not read. "note" alone would be the `decline_reason` mistake in a
        # new place: one field, no marking, and the day the dead
        # `placement_line.notes` column is surfaced beside it there would be
        # two notes and nothing telling them apart.
        Field("client_note", "the client reads this", "textarea"),
    )


PLACEMENT_LINE_KEYS: tuple[str, ...] = tuple(
    f.key for f in placement_line_fields()
)
"""The editable set, for a server-side check on a URL segment. Derived from
the builder rather than re-listed, so a field added above cannot be reachable
in the markup and refused by the route (or the reverse)."""
