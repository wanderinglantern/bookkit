"""Pydantic v2 row models — typed views of the SQLite rows.

Money fields are integer cents; DATE fields are YYYY-MM-DD strings (kept as
str deliberately — they must survive round-trips without timezone conversion);
timestamps are UTC ISO-8601 strings.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum
from typing import Any, Self

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
    detail: str | None = None
    due_on: str | None = None
    status: TaskStatus = TaskStatus.OPEN
    priority: int = 2
    source_interaction_id: str | None = None
    placement_id: str | None = None
    completed_at: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


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
    deleted_at: str | None = None


# Controlled but extensible vocabularies (same pattern as TEAM_ROLES).
PROJECT_STATUSES = ("planned", "active", "completed", "cancelled")
NEED_STATUSES = ("identified", "quoted", "placed", "not_needed")


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
    response_on: str | None = None
    decline_reason: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


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
