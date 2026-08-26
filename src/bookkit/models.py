"""Pydantic v2 row models — typed views of the SQLite rows.

Money fields are integer cents; DATE fields are YYYY-MM-DD strings (kept as
str deliberately — they must survive round-trips without timezone conversion);
timestamps are UTC ISO-8601 strings.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum
from typing import Any, NamedTuple, Self

from pydantic import BaseModel, ConfigDict


class OrgKind(StrEnum):
    CLIENT = "client"
    MARKET = "market"
    OTHER = "other"


class OrgStatus(StrEnum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    DORMANT = "dormant"
    LOST = "lost"
    DECLINED = "declined"


class MarketType(StrEnum):
    CARRIER = "carrier"
    MGA = "mga"
    WHOLESALER = "wholesaler"
    REINSURER = "reinsurer"
    LLOYDS = "lloyds"


class AppetiteLevel(StrEnum):
    TARGET = "target"
    WILL_CONSIDER = "will_consider"
    SELECTIVE = "selective"
    NO = "no"


class InteractionType(StrEnum):
    CALL = "call"
    MEETING = "meeting"
    EMAIL = "email"
    NOTE = "note"
    SITE_VISIT = "site_visit"
    EVENT = "event"


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"


class AssigneeKind(StrEnum):
    """WHICH TABLE `task.assignee_id` points at — and, downstream, which side
    of the client's Owner column the row lands on.

    Two values, not three. A contact is a contact whether they sit at the
    client, at a carrier or at a wholesaler; which of those they are is a
    property of their ORG, read at the moment the question is asked, never
    copied onto the task. Storing "client_contact" would go stale the first
    time contacts.reassign_org moved somebody on a market merge, and the
    client's workbook would then disagree with the book it was made from."""

    TEAM = "team"
    CONTACT = "contact"


class PlacementStatus(StrEnum):
    PROSPECTIVE = "prospective"
    SUBMITTED = "submitted"
    QUOTED = "quoted"
    BOUND = "bound"
    LAPSED = "lapsed"


class OpportunityStage(StrEnum):
    IDENTIFIED = "identified"
    QUALIFIED = "qualified"
    SUBMITTED = "submitted"
    QUOTED = "quoted"
    PRESENTED = "presented"
    WON = "won"
    LOST = "lost"


class SubmissionStatus(StrEnum):
    OUT = "out"
    QUOTED = "quoted"
    DECLINED = "declined"
    BOUND = "bound"
    WITHDRAWN = "withdrawn"


# Controlled but extensible contact-role vocabulary (§3.2).
CONTACT_ROLES = (
    "risk_manager",
    "cfo",
    "controller",
    "general_counsel",
    "ceo",
    "procurement",
    "underwriter",
    "underwriting_assistant",
    "claims",
    "broker_of_record",
    "other",
)


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> Self:
        return cls.model_validate(dict(row))


class Org(Row):
    id: str
    ref: str
    kind: OrgKind
    name: str
    parent_org_id: str | None = None  # market families: issuing co → master co
    legal_name: str | None = None
    domain: str | None = None
    status: OrgStatus = OrgStatus.PROSPECT
    industry: str | None = None
    naics: str | None = None
    owner: str | None = None
    hq_city: str | None = None
    hq_country: str | None = None
    website: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class MarketProfile(Row):
    org_id: str
    am_best_rating: str | None = None
    naic_number: str | None = None
    market_type: MarketType | None = None
    notes: str | None = None


class Appetite(Row):
    id: str
    market_org_id: str
    line: str
    class_of_business: str | None = None
    appetite: AppetiteLevel
    min_premium: int | None = None
    max_limit: int | None = None
    territories: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    # Migration 014: the FK that replaces `line`. Nullable and beside the
    # text column, not instead of it — NULL reads as "not yet mapped",
    # which is where every pre-014 row honestly starts.
    line_id: str | None = None
    deleted_at: str | None = None


class Contact(Row):
    id: str
    org_id: str
    first_name: str
    last_name: str
    title: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    linkedin: str | None = None
    is_primary: bool = False
    active: bool = True
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Interaction(Row):
    id: str
    org_id: str
    type: InteractionType
    occurred_on: str
    occurred_at: str | None = None
    subject: str
    body: str | None = None
    sentiment: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class Task(Row):
    id: str
    org_id: str | None = None
    title: str
    description: str | None = None  # brief one-liner; `detail` holds the long notes
    category: str | None = None  # freeform grouping label, vocab-completed
    detail: str | None = None
    due_on: str | None = None
    status: TaskStatus = TaskStatus.OPEN
    priority: int = 2
    source_interaction_id: str | None = None
    placement_id: str | None = None
    completed_at: str | None = None
    # WHO IS CHASING THIS. Exactly one of (kind + id) or name is ever set;
    # all three NULL means unassigned. repo/assignees.py owns writing them —
    # never set them field by field, or a stale id can outlive a kind and the
    # pair stops meaning anything. See migration 013 for why the resolved
    # case stores an identity rather than a name.
    assignee_kind: AssigneeKind | None = None
    assignee_id: str | None = None
    assignee_name: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


# The one task category that never leaves the building: a task filed under it
# is withheld from the client-facing export (services/export_open_items.py).
# Declared here, not in the export service, because four modules ask the
# question (the export composition, mcpserver, tui/theme, web/routes/work) and
# a fifth needs the constant (repo/vocab) — while the export service imports
# towerkit at module scope, so a TUI row renderer or a web route asking "is
# this internal?" there would drag the workbook stack into the import graph.
# `category` stays freeform; this is a well-known VALUE, not an enum, so
# nothing existing is invalidated and no migration is needed.
INTERNAL_CATEGORY = "Internal"


def is_internal_category(category: str | None) -> bool:
    """EXACT equality on the trimmed, case-folded value — "internal",
    "INTERNAL " and "Internal" all count; "Internal Review" does NOT.

    A prefix match would be friendlier and is the wrong trade. The two rules
    fail in opposite directions and only one failure is recoverable: under
    equality, someone who types "Internal Review" ships the task under a
    section header in the client's workbook literally naming it — loud, and
    self-teaching. Under a prefix rule, "Internal audit support" — a real
    client-facing broking task — silently vanishes from the deliverable with
    nothing anywhere saying a section was removed. Guessing at intent from a
    freeform string is what parse_human_date refuses to do with a bare number,
    for the same reason.

    The near miss is NOT left to an absence, though. Nothing on the row marks
    "Internal Review" — it renders exactly like "Renewal", which is correct,
    and an absence informs nobody who has not yet seen the presence. So
    export_open_items.withheld_note NAMES it on the line that reports the
    export, on both surfaces: the rule stays exact and still says out loud
    when it nearly fired.
    """
    return category is not None and category.strip().lower() == INTERNAL_CATEGORY.lower()


def reads_as_internal(category: str | None) -> bool:
    """PREFIX match on the trimmed, case-folded value: "Internal", "Internal
    Review", "internal note" all count, "Client internal audit" does not.

    A DIFFERENT JOB FROM is_internal_category, which is why the wider rule is
    safe here and unsafe there. That one decides whether a ROW is withheld, so
    over-reach silently deletes a real client-facing task from the deliverable
    — unrecoverable, and invisible. This one decides only whether a SECTION
    HEADING may be printed in the client's copy; the rows underneath survive
    either way (export_open_items.compose files them under General). So the
    cost of over-reach is a heading a client would have found unremarkable,
    and the cost of under-reach is a banner reading "Internal Review" in the
    client's workbook — which a client-side CFO called worse than the item
    beneath it (C9, Grant 2026-08-18: suppress them).

    That asymmetry is also why this stays a plain prefix rather than growing a
    word boundary: "Internally managed" reads internal to the person we are
    protecting from it, and suppressing its heading costs nothing.

    Exact-internal satisfies this too — the wider rule contains the narrower
    one — so compose() must withhold before it re-files."""
    return category is not None and category.strip().lower().startswith(
        INTERNAL_CATEGORY.lower()
    )


class Project(Row):
    id: str
    ref: str
    org_id: str
    name: str
    description: str | None = None
    site: str | None = None
    status: str = "planned"
    start_on: str | None = None
    end_on: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class ProjectNeed(Row):
    id: str
    project_id: str
    line: str
    needed_by: str
    limit_cents: int | None = None
    premium_indication_cents: int | None = None
    status: str = "identified"
    opportunity_id: str | None = None
    placement_id: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    # Migration 014: the FK that replaces `line`. Nullable and beside the
    # text column, not instead of it — NULL reads as "not yet mapped",
    # which is where every pre-014 row honestly starts.
    line_id: str | None = None
    deleted_at: str | None = None


# Controlled but extensible vocabularies (same pattern as TEAM_ROLES).
PROJECT_STATUSES = ("planned", "active", "completed", "cancelled")
NEED_STATUSES = ("identified", "quoted", "placed", "not_needed")


class RfiRequest(Row):
    id: str
    ref: str
    org_id: str
    placement_id: str | None = None
    project_id: str | None = None
    market_org_id: str | None = None
    title: str
    requested_on: str
    due_on: str | None = None
    notes: str | None = None
    cancelled_at: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class RfiItem(Row):
    id: str
    request_id: str
    kind: str = "question"
    prompt: str
    detail: str | None = None
    category: str | None = None
    due_on: str | None = None
    response: str | None = None
    received_on: str | None = None
    status: str = "outstanding"
    created_at: str
    updated_at: str
    deleted_at: str | None = None


RFI_ITEM_STATUSES = ("outstanding", "received", "waived")
RFI_ITEM_KINDS = ("question", "document")


class Placement(Row):
    id: str
    ref: str
    org_id: str
    program_name: str
    period_from: str
    period_to: str
    status: PlacementStatus = PlacementStatus.PROSPECTIVE
    total_limit: int | None = None
    total_premium: int | None = None
    currency: str = "USD"
    commission_bps: int | None = None
    program_path: str | None = None
    source_sha256: str | None = None
    synced_at: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class Opportunity(Row):
    id: str
    ref: str
    org_id: str
    title: str
    lines: str | None = None
    stage: OpportunityStage = OpportunityStage.IDENTIFIED
    target_premium: int | None = None
    target_effective: str | None = None
    probability_pct: int = 50
    source: str | None = None
    incumbent_broker: str | None = None
    competitor: str | None = None
    closed_at: str | None = None
    outcome: str | None = None
    loss_reason: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class Submission(Row):
    id: str
    placement_id: str | None = None
    opportunity_id: str | None = None
    market_org_id: str
    underwriter_contact_id: str | None = None
    sent_on: str
    status: SubmissionStatus = SubmissionStatus.OUT
    quoted_premium: int | None = None
    quoted_limit: int | None = None
    quote_expires_on: str | None = None
    response_on: str | None = None
    decline_reason: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class Subjectivity(Row):
    """Something a market requires before its quote is bindable.

    Chasing these IS the three weeks between a quote arriving and a policy
    being bound, which is the stretch the tool tracked nowhere. Shaped on
    RfiItem, the other chaseable line item in the book."""

    id: str
    submission_id: str
    description: str
    due_on: str | None = None
    status: str = "outstanding"
    satisfied_on: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


# Controlled but extensible, same pattern as TEAM_ROLES / RFI_ITEM_STATUSES.
# 'met' rather than 'received': a subjectivity is a CONDITION satisfied, not a
# document handed over — several are satisfied by an inspection happening or a
# warranty being signed, with nothing to receive.
SUBJECTIVITY_STATUSES = ("outstanding", "met", "waived")
SUBJECTIVITY_OPEN_STATUS = "outstanding"


class Document(Row):
    id: str
    org_id: str
    placement_id: str | None = None
    kind: str | None = None
    title: str
    path: str
    added_at: str
    deleted_at: str | None = None


class TeamMember(Row):
    id: str
    name: str
    title: str | None = None
    specialty: str | None = None
    email: str | None = None
    phone: str | None = None
    active: bool = True
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class TeamAssignment(Row):
    id: str
    team_member_id: str
    org_id: str | None = None
    placement_id: str | None = None
    role: str | None = None
    lines: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


# Controlled but extensible internal-role vocabulary.
TEAM_ROLES = (
    "account_lead",
    "placement_specialist",
    "claims_advocate",
    "analyst",
    "coverage_counsel",
    "other",
)


class EventLogEntry(Row):
    id: str
    entity_type: str
    entity_id: str
    field: str
    old_value: str | None = None
    new_value: str | None = None
    changed_at: str
    note: str | None = None
    batch_id: str | None = None


class EventBatch(Row):
    """One writer action grouped for undo — today always an MCP tool call.
    `summary` is the line the TUI shows; `reverted_at` makes a second revert
    inert rather than a double-apply."""

    id: str
    ref: str
    source: str
    tool: str
    summary: str
    org_id: str | None = None
    created_at: str
    reverted_at: str | None = None


# --- Lines of coverage -----------------------------------------------------
#
# THE TERM IS "LINE OF COVERAGE" (Grant, 2026-08-24) and as of 2026-08-25 it
# is a ROW, not a string. It used to be free text in four independent places
# — appetite.line, project_need.line, opportunity.lines, team_assignment.lines
# — which repo/vocab.py::lines() unioned to answer "what does this book call
# its lines". Nothing reconciled them, so "GL" and "General Liability" were
# two different lines, and a report grouped BY line of coverage had no
# grouping key to group on. A LINE OF COVERAGE HOLDS LAYERS; the layers live
# in towerkit and reference this row's id.


class LineOfCoverage(Row):
    """One line of coverage. `id` is a stable slug, not a ULID: these rows are
    vocabulary rather than the user's data, they are referenced from towerkit
    `Line.id` strings a human typed into a program file, and they must be
    greppable across a migration, a test and a seed. `name` is what a client
    reads and may be renamed freely; `acord_code` is identity for interchange
    and is NEVER the display name."""

    id: str
    name: str
    abbr: str | None = None
    acord_code: str | None = None
    sort_order: int = 0
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


# --- Marketing -------------------------------------------------------------


MARKET_RESPONSE_STATUSES = (
    "pending",
    "indicated",
    "quoted",
    "declined",
    "non_response",
    "bound",
)
"""What a market has said about ONE line of coverage.

`pending` is load-bearing and was nearly dropped (Grant, 2026-08-25): without
it a market submitted yesterday renders as `non_response` on tomorrow's client
report, telling the client a market ignored us when we sent it two days ago.
`non_response` therefore means what it should — asked, chased, nothing came
back — which is a JUDGMENT someone makes, not a state a row falls into by the
clock.

`declined` and `non_response` stay distinct for the same reason: "they looked
and said no" is a different fact from "they never came back", and collapsing
them makes the marketing effort look worse than it was on a document whose
whole purpose is to show the effort.

There is no `not_approached`. In a grid of one row per (line, market), the
ABSENCE of a row carries it — provided the report renders that absence in
words rather than as a blank cell a reader has to interpret."""

MARKET_RESPONSE_STATUS_LABELS: dict[str, str] = {
    "pending": "Pending",
    "indicated": "Indicated",
    "quoted": "Quoted",
    "declined": "Declined",
    "non_response": "Non-response",
    "bound": "Bound",
}
"""What a person READS for each status, beside the tuple that declares them.

Here rather than on the report that first needed it, because the report is no
longer the only reader: the Program tab's status picker has to offer the very
words the grid beside it prints, and a picker whose labels are a second copy of
a vocabulary is the copy that quietly differs (CLAUDE.md, DRY). Keyed by the
raw status, so a surface colours and labels off the KEY and never reverse-maps
a label back."""

MARKET_RESPONSE_OPEN_STATUSES = ("pending", "indicated", "quoted")
"""Still live: worth chasing, and what a clearance collision is checked over."""


PUBLIC_DECLINE_REASONS = (
    "class_appetite",
    "loss_history",
    "capacity",
    "pricing",
    "incumbent_relationship",
    "no_reason_given",
)
"""The ONLY decline wording that may reach a client.

Two fields, not one field with a "safe to share" flag: real decline reasons
are routinely unusable verbatim ("underwriter doesn't like the loss runs, off
the record"), and a single field guarded by a checkbox fails the first time
somebody forgets to tick it — a failure whose consequence is a client reading
an underwriter's private opinion. `market_response.decline_reason` is internal
free text and is never rendered to a client; this tuple fills
`decline_reason_public`, which is OPTIONAL: blank there says nothing, which is
safer than a sentence anyone will wish they had not written."""


PUBLIC_DECLINE_REASON_LABELS: dict[str, str] = {
    "class_appetite": "Class / appetite",
    "loss_history": "Loss history",
    "capacity": "Capacity",
    "pricing": "Pricing",
    "incumbent_relationship": "Incumbent relationship",
    "no_reason_given": "No reason given",
}
"""The client-safe wording, beside the tuple that declares it — same rule as
MARKET_RESPONSE_STATUS_LABELS. The picker a broker chooses from and the words
the client's workbook prints are the same words, out of one dict."""


class RatingBasis(NamedTuple):
    """What a premium is measured against, and how the rate is denominated.

    THREE FACTS, NOT ONE. "Rating basis" conflates what is MEASURED (gross
    sales, payroll, TIV, power units) with the DENOMINATOR the rate is quoted
    per (per $1,000 of sales, per $100 of payroll, per unit). Left implied,
    the rate column becomes uninterpretable the first time a reader assumes
    the wrong convention — and the conventions genuinely differ by line.

    `monetary` is the load-bearing one and is declared HERE, once. It decides
    whether `exposure_amount` holds integer CENTS or a whole COUNT, so no read
    site ever has to make that judgment: a fleet is 42 power units, and 42
    cannot be cents."""

    key: str
    label: str
    monetary: bool
    default_rate_per: int  # 100, 1000, or 1 (per unit)
    unit_label: str | None = None  # non-monetary bases name their unit


RATING_BASES: tuple[RatingBasis, ...] = (
    RatingBasis("gross_sales", "Gross sales", True, 1000),
    RatingBasis("payroll", "Payroll", True, 100),
    RatingBasis("tiv", "Total insured value", True, 100),
    RatingBasis("revenue", "Revenue", True, 1000),
    RatingBasis("units", "Units", False, 1, "units"),
    RatingBasis("power_units", "Power units", False, 1, "power units"),
    RatingBasis("headcount", "Headcount", False, 1, "employees"),
    RatingBasis("flat", "Flat", False, 1, None),
)

RATING_BASIS_KEYS = tuple(b.key for b in RATING_BASES)

RATE_PER_CHOICES: tuple[tuple[int, str], ...] = (
    (100, "$100"),
    (1000, "$1,000"),
    (1, "unit"),
)
"""The denominator a rate may be quoted against, and how each one READS.

A CONTROLLED SET, not a free number. `rate_per` is what makes a rate
interpretable at all — 1.42 per $100 of payroll and 1.42 per $1,000 of sales
differ by a factor of ten — and the conventions in use are these three. It is
declared here beside RATING_BASES (whose `default_rate_per` names the same
three values) so the picker a broker chooses from and the words a header
prints come out of ONE list: a second copy in a template is the fourth copy of
an enum that no test and no type checker would see go stale (CLAUDE.md, DRY).
"""

RATE_PER_LABELS: dict[int, str] = dict(RATE_PER_CHOICES)

_RATING_BASIS_BY_KEY = {b.key: b for b in RATING_BASES}


def rating_basis(key: str) -> RatingBasis:
    """The one lookup. Raises rather than returning a default, because a basis
    nobody declared decides whether an exposure is money, and guessing that
    silently mis-renders a client-facing figure by a factor of a hundred."""
    try:
        return _RATING_BASIS_BY_KEY[key]
    except KeyError:
        raise ValueError(
            f"unknown rating basis {key!r} — declare it in models.RATING_BASES"
        ) from None


class MarketResponse(Row):
    """What ONE market said about ONE line of coverage on ONE submission.

    `market_org_id` is the paper and is NULLABLE: a submission sent to a
    wholesaler has no carrier yet, and "out to RT Specialty, carrier TBD" is
    the truth rather than a gap. `via_org_id` is the intermediary. At least
    one of the two is present (a DB CHECK holds it).

    `attach` / `lim` say WHICH SLAB the answer is about, so the same carrier
    can answer twice on one line at two attachments — which is how an excess
    tower is actually marketed. NULL attach reads as primary / whole line.

    Money is cents and is the carrier's STATED figure: commission-inclusive,
    net of fees and TRIA (Grant, 2026-08-25). NULL is "not quoted yet", never
    zero — a report printing $0 of surplus lines tax makes a claim nobody
    made, and on E&S business that tax decides which placement is cheaper."""

    id: str
    submission_id: str
    line_id: str
    market_org_id: str | None = None
    via_org_id: str | None = None
    attach: int | None = None
    lim: int | None = None
    status: str = "pending"
    responded_on: str | None = None
    rating_basis: str | None = None
    rate_per: int | None = None
    exposure_amount: int | None = None
    rate_micros: int | None = None
    premium: int | None = None
    commission_bps: int | None = None
    commission_included: int = 1
    tria_premium: int | None = None
    policy_fees: int | None = None
    surplus_lines_tax: int | None = None
    decline_reason: str | None = None
    decline_reason_public: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None

    @property
    def total_cost(self) -> int | None:
        """Premium plus everything the client also pays — or None when any
        component is simply unknown.

        NOT a sum that treats NULL as zero. Most of a marketing cycle has no
        fee or tax figure at all, and a total that quietly omits them
        understates an E&S placement by the very amount that decides it.
        Blank is the honest answer; the grid prints blank, never a number it
        cannot stand behind.

        NULL AND ZERO ARE DIFFERENT ANSWERS. NULL is "nobody has told us";
        0 is "we asked, and there is none" — which is the ordinary case on
        admitted domestic business with no surplus lines tax. Only 0
        contributes to a total, so the entry form needs a way to SAY "no fees
        or taxes apply" in one act rather than leaving a broker to type three
        zeros or, worse, leaving the column permanently blank."""
        parts = (
            self.premium,
            self.tria_premium,
            self.policy_fees,
            self.surplus_lines_tax,
        )
        if any(part is None for part in parts):
            return None
        return sum(part for part in parts if part is not None)


class PlacementLine(Row):
    """What one line of coverage on one placement is expected to do: the
    expiring figures a client compares against, and the exposure and basis
    every market response inherits unless it overrides them.

    `expiring_rate_micros` is stored rather than derived because deriving it
    needs `expiring_exposure`, which is a fact nobody may have recorded. When
    it is missing the report leaves the rate comparison BLANK — it does not
    assume exposure was flat, because that assumption puts a number in front
    of a client that looks like rate change and is not."""

    id: str
    placement_id: str
    line_id: str
    expiring_premium: int | None = None
    expiring_exposure: int | None = None
    expiring_rate_micros: int | None = None
    expiring_basis: str | None = None
    expected_exposure: int | None = None
    rating_basis: str | None = None
    rate_per: int | None = None
    attach_sought: int | None = None
    limit_sought: int | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None
