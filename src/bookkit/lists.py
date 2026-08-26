"""THE BUILT-IN VOCABULARIES, and how each of their values reads.

WHAT THIS IS. `models.py` declares WHICH words exist — the tuples every rule in
this book is written against. This module declares, for each of those words,
what a person READS, what colour it takes and where it sorts, and gathers them
into named LISTS that `list_value` is seeded from. It is the registry, not a
second copy: every tuple below is read from models.py rather than repeated, so
a word added there appears here without a second edit, and
tests/test_lists.py fails if one is added and left undressed.

WHY THE PRESENTATION FACETS LIVE HERE. They were in three places — the label in
models.py, the tint in `web/marketing_grid._STATUS_TONE`, the sort position in
`services/marketing_report._STATUS_ORDER` — and only the marketing vocabulary
had all three; the other eight had none, which is why a project status renders
untinted and an RFI status sorts alphabetically. Gathering them is the DRY win
the change is actually for. The two marketing maps stay where they are for now
and a gate holds them equal to this (`test_lists.py`), because moving the reads
is phase 4 and this is phase 1.

WHAT IS DELIBERATELY ABSENT — the STRUCTURAL vocabularies, which are not
vocabulary at all and stay in code (Grant, 2026-08-26):

  * `org.kind` — client / market / other decides which pickers a row reaches,
    which routes accept it and which half of the book it is in. A fourth kind
    would be a word with no behaviour behind it.
  * `interaction.sentiment` — pos / neu / neg is three-valued by nature.
  * `opportunity.outcome` — won / lost / no_decision IS the hit rate.
  * `RATING_BASES` — a basis carries `monetary`, which decides whether an
    exposure is CENTS or a COUNT. A user-added basis would have to answer that,
    and answering it wrongly renders a figure a hundred times out on a client's
    workbook. It is a rule wearing a vocabulary's clothes.
  * `RATE_PER_CHOICES` — three numeric denominators, not words.

A list here is one a broker can legitimately want their own words for. A list
above is one where a new word would be a new RULE, and `behaves_as` cannot help
because there is nothing sensible for it to inherit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .ids import new_ulid
from .models import (
    CONTACT_ROLES,
    MARKET_RESPONSE_STATUS_LABELS,
    MARKET_RESPONSE_STATUSES,
    NEED_STATUSES,
    PROJECT_STATUSES,
    PUBLIC_DECLINE_REASON_LABELS,
    PUBLIC_DECLINE_REASONS,
    RFI_ITEM_KINDS,
    RFI_ITEM_STATUSES,
    SUBJECTIVITY_STATUSES,
    TEAM_ROLES,
    SubmissionStatus,
)

# THE ONLY TONES A VALUE MAY TAKE. Read against app.css by
# tests/test_lists.py, because a tone this file invents renders as no tint at
# all — silently, on a client-facing pill. "" is the absence of a tint and is
# the right answer for a vocabulary that carries no state (a contact's role is
# not good news or bad news).
TONES: tuple[str, ...] = (
    "",
    "is-good",
    "is-warn",
    "is-danger",
    "is-muted",
    "is-accent",
    "is-slate",
)


@dataclass(frozen=True)
class BuiltIn:
    """One value of one list, as the code ships it."""

    value: str
    label: str
    tone: str = ""


@dataclass(frozen=True)
class ListSpec:
    """One editable vocabulary: what it is called, what it is for, whether its
    words leave the building, and the values the code declares.

    `id` is `<table>.<column>` — the one name a definition can have that both a
    reader and a trigger can derive rather than look up.
    """

    id: str
    label: str
    note: str
    values: tuple[BuiltIn, ...]
    client_facing: bool = False

    @property
    def table(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def column(self) -> str:
        return self.id.split(".", 1)[1]


def _titled(raw: str) -> str:
    """A default label for a vocabulary that never had one: the stored word,
    in words. Used only where models.py declares no label map — and the point
    of the table is that a broker can correct any of them."""
    return raw.replace("_", " ").capitalize()


def _plain(values: tuple[str, ...], tones: dict[str, str] | None = None) -> tuple[BuiltIn, ...]:
    tones = tones or {}
    return tuple(BuiltIn(v, _titled(v), tones.get(v, "")) for v in values)


# --- the marketing vocabularies -------------------------------------------
#
# The one list that already had all three facets, so the only one where this
# registry can be checked against what the app renders today — which is
# exactly what tests/test_lists.py does, and why the two maps in
# marketing_grid and marketing_report are left where they are until phase 4.
_RESPONSE_TONES = {
    "bound": "is-good",
    "quoted": "is-accent",
    "indicated": "is-slate",
    "pending": "is-warn",
    "declined_open_elsewhere": "is-warn",
    "declined": "is-danger",
    "not_viable": "is-muted",
    "non_response": "is-muted",
}

# THE ORDER A CLIENT READS A BLOCK IN — live options first, closed last. It is
# `services.marketing_report._STATUS_ORDER` today; the ranks below are seeded
# from that reading and a gate holds the two equal.
_RESPONSE_RANK = (
    "bound", "quoted", "indicated", "pending",
    "declined_open_elsewhere", "declined", "not_viable", "non_response",
)

SPECS: tuple[ListSpec, ...] = (
    ListSpec(
        id="market_response.status",
        label="Market response",
        note=(
            "What one market has said about one line of coverage, on one band. "
            "Printed on the client's marketing workbook."
        ),
        client_facing=True,
        values=tuple(
            BuiltIn(v, MARKET_RESPONSE_STATUS_LABELS[v], _RESPONSE_TONES.get(v, ""))
            # RANKED, NOT DECLARED ORDER. models.MARKET_RESPONSE_STATUSES is in
            # PICKER order (the order a broker meets them in a dropdown) and
            # the report reads live-first; they are two different questions
            # about the same eight words.
            for v in sorted(
                MARKET_RESPONSE_STATUSES,
                key=lambda s: _RESPONSE_RANK.index(s)
                if s in _RESPONSE_RANK
                else len(_RESPONSE_RANK),
            )
        ),
    ),
    ListSpec(
        id="market_response.decline_reason_public",
        label="Decline reason (to the client)",
        note=(
            "The ONLY decline wording that may reach a client. The internal "
            "reason is free text on the row and is never rendered to one."
        ),
        client_facing=True,
        values=tuple(
            BuiltIn(v, PUBLIC_DECLINE_REASON_LABELS[v]) for v in PUBLIC_DECLINE_REASONS
        ),
    ),
    ListSpec(
        id="submission.status",
        label="Submission",
        note=(
            "A whole package out to one market. DERIVED from its response rows "
            "after every write — this list changes how it READS, never what it "
            "is rolled up to."
        ),
        values=(
            BuiltIn("bound", "Bound", "is-good"),
            BuiltIn("quoted", "Quoted", "is-accent"),
            BuiltIn("out", "Out", "is-warn"),
            BuiltIn("declined", "Declined", "is-danger"),
            BuiltIn("withdrawn", "Withdrawn", "is-muted"),
        ),
    ),
    ListSpec(
        id="submission_subjectivity.status",
        label="Subjectivity",
        note="Something a market requires before its quote is bindable.",
        values=(
            BuiltIn("outstanding", "Outstanding", "is-warn"),
            BuiltIn("met", "Met", "is-good"),
            BuiltIn("waived", "Waived", "is-muted"),
        ),
    ),
    ListSpec(
        id="rfi_item.status",
        label="Information request item",
        note="Something asked of the client before a submission can go out.",
        values=(
            BuiltIn("outstanding", "Outstanding", "is-warn"),
            BuiltIn("received", "Received", "is-good"),
            BuiltIn("waived", "Waived", "is-muted"),
        ),
    ),
    ListSpec(
        id="rfi_item.kind",
        label="Information request kind",
        note="Whether the thing asked for is an answer or a document.",
        values=_plain(RFI_ITEM_KINDS),
    ),
    ListSpec(
        id="project.status",
        label="Project",
        note="A piece of work on an account that is not a placement.",
        values=(
            BuiltIn("planned", "Planned", "is-slate"),
            BuiltIn("active", "Active", "is-accent"),
            BuiltIn("completed", "Completed", "is-good"),
            BuiltIn("cancelled", "Cancelled", "is-muted"),
        ),
    ),
    ListSpec(
        id="project_need.status",
        label="Project need",
        note="A line of coverage a project has to place.",
        values=(
            BuiltIn("identified", "Identified", "is-warn"),
            BuiltIn("quoted", "Quoted", "is-accent"),
            BuiltIn("placed", "Placed", "is-good"),
            BuiltIn("not_needed", "Not needed", "is-muted"),
        ),
    ),
    ListSpec(
        id="task.status",
        label="Task",
        note="Something somebody has to do.",
        values=(
            BuiltIn("open", "Open", ""),
            BuiltIn("done", "Done", "is-good"),
            BuiltIn("dropped", "Dropped", "is-muted"),
        ),
    ),
    ListSpec(
        id="placement.status",
        label="Placement",
        note="Where a placement stands in its own cycle.",
        values=(
            BuiltIn("prospective", "Prospective", "is-slate"),
            BuiltIn("submitted", "Submitted", "is-warn"),
            BuiltIn("quoted", "Quoted", "is-accent"),
            BuiltIn("bound", "Bound", "is-good"),
            BuiltIn("lapsed", "Lapsed", "is-danger"),
        ),
    ),
    ListSpec(
        id="opportunity.stage",
        label="Opportunity stage",
        note="How far a new-business opportunity has got.",
        values=(
            BuiltIn("identified", "Identified", "is-slate"),
            BuiltIn("qualified", "Qualified", "is-slate"),
            BuiltIn("submitted", "Submitted", "is-warn"),
            BuiltIn("quoted", "Quoted", "is-accent"),
            BuiltIn("presented", "Presented", "is-accent"),
            BuiltIn("won", "Won", "is-good"),
            BuiltIn("lost", "Lost", "is-danger"),
        ),
    ),
    ListSpec(
        id="org.status",
        label="Organisation",
        note="Where a client or a market stands with us.",
        values=(
            BuiltIn("prospect", "Prospect", "is-slate"),
            BuiltIn("active", "Active", "is-good"),
            BuiltIn("dormant", "Dormant", "is-muted"),
            BuiltIn("lost", "Lost", "is-danger"),
            BuiltIn("declined", "Declined", "is-danger"),
        ),
    ),
    ListSpec(
        id="market_profile.market_type",
        label="Market type",
        note="What kind of market this is — carrier, MGA, wholesaler, and so on.",
        values=(
            BuiltIn("carrier", "Carrier"),
            BuiltIn("mga", "MGA"),
            BuiltIn("wholesaler", "Wholesaler"),
            BuiltIn("reinsurer", "Reinsurer"),
            BuiltIn("lloyds", "Lloyd's"),
        ),
    ),
    ListSpec(
        id="appetite.appetite",
        label="Appetite",
        note="How keen a market is on a class of business.",
        values=(
            BuiltIn("target", "Target", "is-good"),
            BuiltIn("will_consider", "Will consider", "is-accent"),
            BuiltIn("selective", "Selective", "is-warn"),
            BuiltIn("no", "No", "is-muted"),
        ),
    ),
    ListSpec(
        id="interaction.type",
        label="Interaction",
        note="How a conversation happened.",
        values=(
            BuiltIn("call", "Call"),
            BuiltIn("meeting", "Meeting"),
            BuiltIn("email", "Email"),
            BuiltIn("note", "Note"),
            BuiltIn("site_visit", "Site visit"),
            BuiltIn("event", "Event"),
        ),
    ),
    ListSpec(
        id="contact.role",
        label="Contact role",
        note="What a person does at the client or the market.",
        values=_plain(CONTACT_ROLES),
    ),
    ListSpec(
        id="team_assignment.role",
        label="Team role",
        note="What one of our people does on an account.",
        values=_plain(TEAM_ROLES),
    ),
)

BY_ID: dict[str, ListSpec] = {spec.id: spec for spec in SPECS}

# The tuples this registry is DERIVED from, named so tests/test_lists.py can
# hold each list to offering exactly the words models.py declares — a value
# added there and forgotten here would be storable and unpickable.
DERIVED_FROM: dict[str, tuple[str, ...]] = {
    "market_response.status": MARKET_RESPONSE_STATUSES,
    "market_response.decline_reason_public": PUBLIC_DECLINE_REASONS,
    "submission.status": tuple(s.value for s in SubmissionStatus),
    "submission_subjectivity.status": SUBJECTIVITY_STATUSES,
    "rfi_item.status": RFI_ITEM_STATUSES,
    "rfi_item.kind": RFI_ITEM_KINDS,
    "project.status": PROJECT_STATUSES,
    "project_need.status": NEED_STATUSES,
    "contact.role": CONTACT_ROLES,
    "team_assignment.role": TEAM_ROLES,
}


def sync_builtins(conn: sqlite3.Connection) -> int:
    """Seed and re-seed the built-in half of every list from the code above.

    IDEMPOTENT, AND SILENT WHEN NOTHING DIFFERS. Called once per connection
    that migrates, so a word added to models.py appears in its list without a
    second edit and without a migration — which is the whole reason the
    registry reads models.py rather than repeating it. Returns how many rows it
    wrote, which is 0 on every open after the first.

    IT NEVER TOUCHES A VALUE A PERSON ADDED. Only `is_builtin = 1` rows are
    written, and a built-in's `label`, `tone` and `rank` are written ONLY when
    they have never been edited — see `_builtin_edited`. A broker who renames
    "Non-response" to "No reply" keeps it through the next release; the
    alternative is a registry that quietly reverts somebody's own words every
    time the app starts.

    A BUILT-IN IS NEVER REMOVED HERE. A word dropped from models.py is a word
    rows may still hold, and deleting its list value would leave those rows
    naming nothing — it is retired instead, which is the same rule the surface
    follows and the same one lines of coverage follow.
    """
    from .db import utc_now

    now = utc_now()
    written = 0
    for spec in SPECS:
        written += _sync_definition(conn, spec, now)
        for rank, built in enumerate(spec.values):
            written += _sync_value(conn, spec, built, rank, now)
        written += _retire_dropped(conn, spec, now)
    return written


def _sync_definition(conn: sqlite3.Connection, spec: ListSpec, now: str) -> int:
    row = conn.execute(
        "SELECT label, note, client_facing FROM list_definition WHERE id = ?",
        (spec.id,),
    ).fetchone()
    wanted = (spec.label, spec.note, int(spec.client_facing))
    if row is None:
        conn.execute(
            "INSERT INTO list_definition"
            " (id, label, note, client_facing, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (spec.id, *wanted, now, now),
        )
        return 1
    if tuple(row) != wanted:
        # THE DEFINITION IS THE CODE'S, unlike a value's label. What a list IS
        # for and whether its words reach a client are facts about the schema,
        # not preferences — a broker renaming "Market response" to something
        # else would not change what the column holds, and `client_facing`
        # decides whether the page warns them.
        conn.execute(
            "UPDATE list_definition SET label = ?, note = ?, client_facing = ?,"
            " updated_at = ? WHERE id = ?",
            (*wanted, now, spec.id),
        )
        return 1
    return 0


def _sync_value(
    conn: sqlite3.Connection, spec: ListSpec, built: BuiltIn, rank: int, now: str
) -> int:
    row = conn.execute(
        "SELECT label, tone, rank, behaves_as, is_builtin, retired_at"
        " FROM list_value WHERE list_id = ? AND value = ?",
        (spec.id, built.value),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO list_value (id, list_id, value, label, tone, rank,"
            " behaves_as, is_builtin, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (new_ulid(), spec.id, built.value, built.label, built.tone, rank,
             # A BUILT-IN BEHAVES AS ITSELF. That self-reference is what lets
             # one foreign key hold the whole shape with no second table, and
             # it is what every rule resolves to.
             built.value, now, now),
        )
        return 1
    if not row["is_builtin"]:
        # A person's value that happens to share a built-in's key. Left alone:
        # taking it over would rewrite what they wrote, and phase 2's add path
        # refuses the collision at the point it would be made.
        return 0
    if _builtin_edited(conn, spec.id, built.value):
        return 0
    if (row["label"], row["tone"], row["rank"], row["retired_at"]) == (
        built.label, built.tone, rank, None
    ):
        return 0
    conn.execute(
        "UPDATE list_value SET label = ?, tone = ?, rank = ?, retired_at = NULL,"
        " updated_at = ? WHERE list_id = ? AND value = ?",
        (built.label, built.tone, rank, now, spec.id, built.value),
    )
    return 1


def _builtin_edited(conn: sqlite3.Connection, list_id: str, value: str) -> bool:
    """Whether a person has changed this built-in since it was seeded.

    READ OFF THE EVENT LOG, which is where every deliberate write in this book
    already lands — so "has anybody touched this" needs no flag of its own that
    somebody has to remember to set. A row this function calls edited is never
    written over by `sync_builtins` again.
    """
    row = conn.execute(
        "SELECT 1 FROM event_log e JOIN list_value v ON v.id = e.entity_id"
        " WHERE e.entity_type = 'list_value' AND v.list_id = ? AND v.value = ?"
        " LIMIT 1",
        (list_id, value),
    ).fetchone()
    return row is not None


def _retire_dropped(conn: sqlite3.Connection, spec: ListSpec, now: str) -> int:
    """A built-in the code no longer declares is RETIRED, never deleted.

    Rows on disk may still hold it — that is the whole reason a vocabulary
    change is a migration in the first place — and deleting the value would
    leave those rows naming nothing at all. Retired means storable, still
    printed where it was already used, and not offered.
    """
    declared = {b.value for b in spec.values}
    stale = [
        r["value"]
        for r in conn.execute(
            "SELECT value FROM list_value WHERE list_id = ? AND is_builtin = 1"
            " AND retired_at IS NULL",
            (spec.id,),
        )
        if r["value"] not in declared
    ]
    for value in stale:
        conn.execute(
            "UPDATE list_value SET retired_at = ?, updated_at = ?"
            " WHERE list_id = ? AND value = ?",
            (now, now, spec.id, value),
        )
    return len(stale)
