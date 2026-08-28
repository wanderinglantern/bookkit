"""What an assistant may write through `edit_field`, derived — plus THE DENYLIST.

Read the denylist, not the derivation. Everything below the rules is a list of
fields an assistant deliberately cannot write, each with the reason in a
sentence you can disagree with. Strike a line out and the field becomes
editable on the next run; add a line and it stops being.

WHY THIS IS DERIVED. `forms/entities.py` already declares every field of every
entity, and both the TUI and the web render from those declarations — add a
`Field(...)` and both surfaces get it free. MCP used to keep a second,
hand-written table (`mcpserver._EDITABLE`), so the assistant only got a new
field when someone remembered to edit two files. It stopped being remembered:
the org entry was literally `dict(_ENRICHABLE_ORG)` — the deliberate-overwrite
surface defined as a copy of the blank-fill surface — so an assistant could not
rename an account or move a prospect to active or lost, and nothing anywhere
recorded that as a decision. `tests/test_web_forms_spec.py:118` already forbids
MCP keeping a second *cleaner* map; this is the same duplication one layer up.

SO THE ALLOWLIST IS A DENYLIST OVER A DERIVED SET. A new *field* on a form
becomes reachable; a new *dangerous* field does not, because the rules below
catch whole categories (foreign keys, bookkeeping columns) and the per-field
entries catch the rest. If you cannot decide a field, deny it and say why: a
conservative default is recoverable, a permissive one is not.

WHAT IS DELIBERATELY NOT DERIVED, and must not be:
- Identity resolution. `mcpserver._edit_target` is ten resolvers with ten ref
  conventions. One generic rule would make writes fuzzy, which is the thing
  this surface is most carefully not.
- The verb tools. `opportunity_stage` refusing with the legal ladder,
  `member_deactivate` refusing while assignments are live, `client_create`'s
  duplicate guard. Each encodes a domain rule a generic writer would erase,
  and several exist because a specific bug happened.
"""

from __future__ import annotations

from collections.abc import Callable
from types import UnionType
from typing import Any, Union, get_args, get_origin

from .forms.entities import (
    assignment_form,
    contact_form,
    member_form,
    need_form,
    opportunity_form,
    org_form,
    project_form,
    request_form,
    rfi_item_form,
    task_form,
)
from .forms.spec import Field, FormSpec
from .models import (
    Contact,
    Opportunity,
    Org,
    Project,
    ProjectNeed,
    RfiItem,
    RfiRequest,
    Row,
    Task,
    TeamAssignment,
    TeamMember,
)

# --- the derivation's two halves ---------------------------------------------

# edit_field kind -> the FormSpec builder that OWNS that entity's fields. Every
# builder here is callable with no arguments: without a `conn` the DB-backed
# selects come back empty, which is exactly right — those are all foreign keys
# and denied by rule anyway.
#
# A kind appears here only if `mcpserver._edit_target` can resolve a ref for
# it. That is not a coincidence to be tidied away: resolution is hand-written
# per entity on purpose, so "can this kind be edited" and "can this kind be
# named" are the same question. Everything else is in UNMAPPED_BUILDERS.
BUILDERS: dict[str, Callable[[], FormSpec]] = {
    "org": org_form,
    "contact": contact_form,
    "task": task_form,
    "opportunity": opportunity_form,
    "project": project_form,
    "project_need": need_form,
    "rfi_request": request_form,
    "rfi_item": rfi_item_form,
    "team_member": member_form,
    "team_assignment": assignment_form,
}

# kind -> the pydantic row model. `_edit_field` reads the current value with
# getattr(row, field) and `base.update` writes the column, so a field that is
# not on the model is not a column and can never be written. Deriving against
# the model is the belt that stops the surface advertising something that
# fails at the DB layer — which is exactly how `opportunity.notes` shipped.
MODELS: dict[str, type[Row]] = {
    "org": Org,
    "contact": Contact,
    "task": Task,
    "opportunity": Opportunity,
    "project": Project,
    "project_need": ProjectNeed,
    "rfi_request": RfiRequest,
    "rfi_item": RfiItem,
    "team_member": TeamMember,
    "team_assignment": TeamAssignment,
}


# =============================================================================
# THE DENYLIST
# =============================================================================
#
# Part 1 — whole entities.
# Part 2 — two rules that deny a category across every entity.
# Part 3 — form fields that are not columns of their own entity.
# Part 4 — individual fields, one line each.
# Part 5 — the two fields kept editable that no form declares.


# --- Part 1: entities the assistant cannot edit at all ------------------------
#
# Every FormSpec builder in forms/entities.py is either mapped to a kind above
# or listed here with a reason. tests/test_mcp_surface.py fails if a new
# builder is neither, so a new entity form cannot land silently editable OR
# silently unreachable.
UNMAPPED_BUILDERS: dict[str, str] = {
    "rfi_answer_form": (
        "Not a second surface — a roomier door onto ONE field the rfi_item "
        "entry above already carries. The web's request items table edits "
        "`response` in a row cell; this builder opens the same column in a "
        "textarea for the answers that are three sentences long (Grant, "
        "2026-08-19). Mapping it as its own kind would advertise an entity "
        "that does not exist and split one column across two ledger entries. "
        "The assistant reaches the same field through request_item_received's "
        "`response` argument."
    ),
    "subjectivity_form": (
        "A subjectivity hangs off a submission, and submissions are not an "
        "edit_field kind — there is no _edit_target resolver for one, so a ref "
        "would advertise a kind and then refuse every id given for it. It is "
        "also the wrong primitive: settling a subjectivity moves status and "
        "satisfied_on together, which is a transition rather than a field "
        "edit, the same reason rfi_item.status is denied. That shape has "
        "arrived: subjectivity_add is the create door and settle_subjectivity "
        "is the transition (2026-08-28). Description and due-date edits stay "
        "on the web, where the shared form is the right primitive."
    ),
    "placement_form": (
        "Placements are read-only to the assistant by design (see mcpserver's "
        "module docstring). Program structure lives in towerkit JSON files and "
        "the proj_* tables are a rebuildable cache; a compare-and-set against "
        "a cached row would be erased by the next projection. Program writes "
        "go through program_edit / program_bind / program_layer_edit, which "
        "take the guarded load-mutate-validate-dump-reproject cycle."
    ),
    "submission_form": (
        "No MCP tool creates, reads or edits a submission, so there is no ref "
        "to compare-and-set against, and _edit_target has no resolver for one. "
        "Adding submissions to the surface is a decision (audit rank 10), not "
        "something a derivation should make on its own."
    ),
    "response_form": (
        "Recording a market's answer is `market_responded`, which reaches the "
        "same row this form now writes (a `market_response`, since "
        "2026-08-26 — the submission's own quote columns are a roll-up of "
        "those rows and are typed by nobody). What this FORM adds on top is "
        "choosing the line of coverage for a package that has no response on "
        "it yet, which on MCP is `market_approach`; the two submission-owned "
        "fields it also carries — the underwriter and the package notes — "
        "have no tool, for the same missing resolver as submission_form."
    ),
    "document_form": (
        "A document is a path on Grant's disk. The assistant cannot verify "
        "one exists, no MCP tool creates one, and _edit_target has no resolver."
    ),
    "appetite_form": (
        "Market appetite is reference data maintained on the Markets screen. "
        "Nothing on the MCP surface reads or writes it, so an edit path would "
        "be a write with no way to find its target."
    ),
    "interaction_form": (
        "Interactions are created by log_activity and deleted by "
        "activity_delete; correcting one needs a resolver over the refs "
        "recent_activity already returns. Audit rank 10 — a small, real gap, "
        "and a decision rather than a derivation."
    ),
    "org_form_initial_profile": (
        "Not a distinct entity: a wrapper that calls org_form and pre-fills "
        "the market profile. Its fields are org_form's."
    ),
}


# --- Part 2: the two category rules ------------------------------------------

# Denied on EVERY entity.
SYSTEM_COLUMNS = frozenset({"id", "ref", "created_at", "updated_at", "deleted_at"})
SYSTEM_COLUMNS_REASON = (
    "Identity and bookkeeping. `ref` is the handle every other tool resolves "
    "on, `updated_at` is written by repo/base on every real change, and "
    "`deleted_at` is the soft delete — putting a deleted row back is "
    "revert_batch's job, not a field write."
)

# Denied on every entity: any field whose key ends in `_id`.
FOREIGN_KEY_SUFFIX = "_id"
FOREIGN_KEY_REASON = (
    "Foreign keys re-scope a record, and re-scoping is a two-column move a "
    "single-field compare-and-set cannot express. Moving a team assignment "
    "between clients is unassign + assign, kept deliberately separate "
    "(CLAUDE.md). An rfi_request is about a placement OR a project and the DB "
    "CHECK enforces it, so setting one without clearing the other raises an "
    "opaque IntegrityError. Contacts move org through "
    "repo/contacts.reassign_org, which rewrites every row of a merge in one "
    "revertible batch. This rule also covers task.org_id, task.placement_id, "
    "task.source_interaction_id, project_need.opportunity_id / .placement_id, "
    "org.parent_org_id, contact.org_id and team_assignment.team_member_id."
)


# --- Part 3: form fields that are not columns of their own entity ------------
#
# These are declared on a form but stored somewhere else, so `base.update` on
# the entity would fail or silently write nothing. Documented rather than left
# to the model check, because "why can't I set the AM Best rating" deserves an
# answer.
NOT_A_COLUMN: dict[tuple[str, str], str] = {
    ("org", "market_type"): (
        "Lives on the `market_profile` table, not on `org` — forms.entities."
        "apply_org pops it and calls orgs.set_market_profile. base.update "
        "against `org` cannot reach it. NO LONGER UNREACHABLE: `market_edit` "
        "takes both profile columns and writes them through the same "
        "set_market_profile (2026-08-26). Denied field, present verb — the "
        "shape the task.assignee entry above says the answer is, and this "
        "entry asked for by name when it said a market_profile kind would "
        "need its own resolver."
    ),
    ("org", "am_best_rating"): (
        "Same: a market_profile column, written by orgs.set_market_profile, "
        "not by an org field write — and reachable the same way, through "
        "`market_edit`."
    ),
    ("task", "assignee"): (
        "One typed string that becomes THREE task columns — assignee_kind + "
        "assignee_id for a resolved person, assignee_name for a freeform one "
        "— and repo/assignees.py is the only thing that writes them "
        "(forms.entities.apply_task pops `assignee` before the repo call). "
        "`base.update(conn, 'task', id, {'assignee': ...})` would write a "
        "column that does not exist, which is why it is denied HERE and always "
        "will be. It is no longer unreachable: `task_assign` and "
        "`task_create(assignee=...)` take the same typed string and hand it to "
        "repo.assignees.columns (2026-08-19). Denied field, present verb — the "
        "shape this entry always said the answer was."
    ),
}


# --- Part 4: individual fields ------------------------------------------------
#
# One line each. The categories, in the audit's words: fields owned by a
# transition, and anything with a domain rule behind it.
DENIED: dict[tuple[str, str], str] = {
    # -- org --
    ("org", "kind"): (
        "Reclassifying a live account moves it between the book and the "
        "markets list and changes which screens and which tools can find it "
        "at all. The TUI's edit form does offer it, so this is stricter than "
        "the terminal — deliberately, because the TUI shows you the "
        "consequence on the next screen and MCP does not. STRIKE THIS LINE if "
        "you want the assistant to be able to file an account as a market."
    ),
    # -- contact --
    ("contact", "is_primary"): (
        "Exactly one primary per org: repo/contacts.set_primary clears the "
        "others in the same write. A single-field set would leave an account "
        "with two primaries, and nothing would say so."
    ),
    ("contact", "active"): (
        "The removal path is contact_remove (services.contacts.remove), which "
        "is revertible and says what it took off. A field write would "
        "deactivate a contact with none of that."
    ),
    # -- task --
    ("task", "status"): (
        "task_complete and task_reopen own it, and completion writes "
        "completed_at in the same batch. Two columns, one action."
    ),
    ("task", "completed_at"): (
        "Moves with `status`: task_complete stamps both, task_reopen clears "
        "both. Set alone it produces a task that is open and has a "
        "completion date, which every count on Today reads differently."
    ),
    # WHO IS CHASING IT is a three-column move-together set (models.Task:
    # "never set them field by field, or a stale id can outlive a kind and
    # the pair stops meaning anything"). Only assignee_id was denied, and
    # only by the accident of ending in `_id` — a single
    # Field("assignee_name", ...) on any form would have made the other two
    # writable and produced exactly the corruption that comment warns about.
    # Denied by name, with the reason, BEFORE someone adds that field.
    ("task", "assignee_kind"): (
        "One of three columns that only mean anything together — kind + id "
        "for a resolved person, name for a freeform one, all NULL for "
        "unassigned. repo/assignees.py writes the set in one call and is the "
        "only thing that may. Setting kind alone leaves an id pointing into "
        "the wrong table, or a kind with nothing under it."
    ),
    ("task", "assignee_id"): (
        "Same three-column set. The foreign-key rule already catches this "
        "name, but only by accident of its suffix — the real reason is that "
        "it moves with assignee_kind and assignee_name, and a stale id "
        "outliving its kind is the failure models.Task names in a comment."
    ),
    ("task", "assignee_name"): (
        "Same three-column set, and the one with no suffix to hide behind: "
        "a freeform name written while assignee_kind/assignee_id still hold "
        "a resolved person gives one task two different owners, and every "
        "reader picks a different one."
    ),
    # -- opportunity --
    ("opportunity", "stage"): (
        "opportunity_stage owns it and refuses an illegal move by naming the "
        "legal ladder. A generic field write would erase that refusal — this "
        "is the clearest example of a verb worth keeping hand-written."
    ),
    ("opportunity", "outcome"): (
        "Written together with `stage` when a deal closes. Setting it alone "
        "produces a deal with an outcome that is still open."
    ),
    ("opportunity", "closed_at"): (
        "Moves with `stage` and `outcome`, both written by "
        "opportunity_stage when a deal closes. A close date on an open deal "
        "is a row no report can interpret."
    ),
    ("opportunity", "loss_reason"): (
        "Belongs to losing a deal, which is opportunity_stage's move. A loss "
        "reason on an open deal is a record nobody can act on."
    ),
    # -- project_need --
    ("project_need", "status"): (
        "The queued needs-to-pipeline reconciler owns need-status semantics: "
        "`quoted` implies an opportunity and `placed` implies a placement, "
        "and both of those are separate columns. Until the reconciler exists, "
        "a status set on its own is a claim the book cannot support."
    ),
    # -- team_member --
    ("team_member", "active"): (
        "member_deactivate / member_reactivate. Retiring a colleague refuses "
        "while assignments are live, and cascade=True removes them all in one "
        "revertible batch (CLAUDE.md). A field write leaves the assignments "
        "dangling on a member nobody can see."
    ),
    # -- rfi_request --
    ("rfi_request", "cancelled_at"): (
        "Blank means live, a date means withdrawn. edit_field can set the "
        "date but cannot clear it — a blank is refused as a date — so on this "
        "surface it is a one-way door, undoable only through revert_batch. "
        "The TUI's request form can blank it, which is why it is safe there."
    ),
    # -- rfi_item --
    ("rfi_item", "status"): (
        "request_item_received / request_item_waive. Status OWNS received_on "
        "so the pair can never disagree (services.rfi.mark_received); putting "
        "an item back to outstanding used to leave the date stamped, and the "
        "datasheet rendered an outstanding row carrying a received date."
    ),
    ("rfi_item", "received_on"): (
        "request_item_received. Status OWNS this date: a received item "
        "without one gets today stamped, and an item put back to "
        "outstanding has it cleared. Written alone the pair disagree, and "
        "the RFI datasheet renders the contradiction."
    ),
}


# --- Part 5: kept editable, though no form declares them ----------------------
#
# Real columns that `_EDITABLE` already carried and that no FormSpec on either
# surface declares. Without these two lines the derivation would QUIETLY TAKE
# AWAY two capabilities that exist today, and a refactor is not the place to
# lose one. Each is (kind, field) -> (value type, reason).
ALSO_EDITABLE: dict[tuple[str, str], tuple[Any, str]] = {
    ("org", "legal_name"): (
        "text",
        "A real org column no form declares on either surface. It has been "
        "enrichable since the first MCP build; the derivation must not be the "
        "thing that removes it. If you want it gone, delete this line — but "
        "that is a capability decision, not a cleanup.",
    ),
    ("project", "notes"): (
        "textarea",
        "A real project column: project_form declares `description` but not "
        "`notes`, while _EDITABLE has always carried both. Same reasoning as "
        "org.legal_name.",
    ),
}


# =============================================================================
# The derivation itself. Nothing below here makes a policy decision.
# =============================================================================


class IntChoices(tuple[str, ...]):
    """A closed vocabulary whose COLUMN is an integer.

    A select's options are strings on both surfaces — a Textual `Select` and
    an HTML `<option>` both carry text — while `task.priority` is an `int`
    column. Derived as a plain tuple, the surface advertised a `str` for a
    column that stores an `int`, and edit_field's compare-and-set then
    refused every write with

        task.priority holds 2, not what you expected ('2')

    two values a reader cannot tell apart, so a model retries forever:
    expecting=None is refused as not-blank and every legal expecting is
    refused as a mismatch. `forms.entities.apply_task` has always done the
    `int(...)` on the way to the repo; MCP had no equivalent. Carrying the
    coercion on the TYPE, derived from the column, is what stops the next
    int-backed select repeating it — the derivation reconciles it, nobody has
    to remember to.

    A tuple subclass on purpose: `_clean_typed`'s vocabulary check and
    `describe`'s select rendering both key off `isinstance(vtype, tuple)` and
    keep working unchanged.
    """


# What `mcpserver._clean_typed` hands `base.update`, per declared field kind.
# Anything not named here falls through to `_clean_by_kind`, which returns a
# str. Used only to CHECK the derivation against the column types —
# tests/test_mcp_surface.py fails on any pair that does not line up.
PRODUCED_TYPES: dict[str, type] = {
    "money": int,   # parse_money_cents
    "int": int,     # int()
    "date": str,    # an ISO date string
}


def produced_type(vtype: Any) -> type:
    """The python type a cleaned value arrives at the column AS."""
    if isinstance(vtype, IntChoices):
        return int
    if isinstance(vtype, tuple):
        return str
    return PRODUCED_TYPES.get(vtype, str)


def column_type(kind: str, field: str) -> type:
    """The python type a column STORES, Optional unwrapped and enums reduced
    to the primitive they subclass (OrgStatus is a StrEnum, so `str`)."""
    annotation = MODELS[kind].model_fields[field].annotation
    if get_origin(annotation) in (Union, UnionType):
        real = [a for a in get_args(annotation) if a is not type(None)]
        annotation = real[0] if real else annotation
    # bool before int: bool IS a subclass of int and would otherwise vanish
    for primitive in (bool, str, int, float):
        if isinstance(annotation, type) and issubclass(annotation, primitive):
            return primitive
    return object


def _value_type(field: Field, stored_as: type) -> Any:
    """A field's value type as `mcpserver._clean_typed` consumes it: the
    declared kind for everything scalar, and a tuple of the legal values for a
    select — so a refusal can list the vocabulary instead of writing junk.

    `stored_as` is the column's own type, and it is not decoration: a select
    over an int column becomes IntChoices, which cleans to an int. Without
    that reconciliation the surface promises a type the column will not take,
    and the failure lands on the model as an unreadable refusal rather than
    on us as a red test."""
    if field.kind == "select":
        options = tuple(value for _, value in field.options)
        return IntChoices(options) if stored_as is int else options
    return field.kind


def denial_reason(kind: str, field: str) -> str | None:
    """Why this field is not editable, or None if it is (or is unknown).

    THE SPECIFIC REASON WINS. The per-field entries are consulted before the
    two category rules, because `task.assignee_id` denied as "foreign keys
    re-scope a record" is true and useless: it is really denied because it
    moves with assignee_kind and assignee_name, and a caller told the FK
    story will go looking for an unassign/assign pair that does not exist."""
    specific = NOT_A_COLUMN.get((kind, field)) or DENIED.get((kind, field))
    if specific is not None:
        return specific
    if field in SYSTEM_COLUMNS:
        return SYSTEM_COLUMNS_REASON
    if field.endswith(FOREIGN_KEY_SUFFIX):
        return FOREIGN_KEY_REASON
    return None


def editable() -> dict[str, dict[str, Any]]:
    """kind -> field -> value type, derived from the form builders minus the
    denylist. This is `mcpserver._EDITABLE`."""
    surface: dict[str, dict[str, Any]] = {}
    for kind, builder in BUILDERS.items():
        columns = MODELS[kind].model_fields
        fields: dict[str, Any] = {}
        for field in builder().fields:
            if denial_reason(kind, field.key) is not None:
                continue
            if field.key not in columns:
                # Not a column of this entity and nobody wrote down why.
                # Refusing to advertise it is right — it would fail at the DB
                # layer — but a SILENT drop is how task.assignee became a
                # capability the TUI and the web have, MCP does not, and no
                # denylist line, ledger cell or describe output mentioned.
                # unexplained_non_columns() below is asserted empty, so the
                # next one lands as a red test naming the field.
                continue
            fields[field.key] = _value_type(field, column_type(kind, field.key))
        for (also_kind, also_field), (vtype, _) in ALSO_EDITABLE.items():
            if also_kind == kind:
                fields[also_field] = vtype
        surface[kind] = fields
    return surface


def unexplained_non_columns() -> dict[str, list[str]]:
    """kind -> form fields that are not columns of that entity and that
    NOT_A_COLUMN does not explain. Asserted empty: a form field MCP cannot
    write is a capability the other two surfaces have and this one does not,
    and that has to be a written-down decision rather than a `continue`."""
    out: dict[str, list[str]] = {}
    for kind, builder in BUILDERS.items():
        columns = MODELS[kind].model_fields
        stray = [
            field.key
            for field in builder().fields
            if field.key not in columns
            and denial_reason(kind, field.key) is None
        ]
        if stray:
            out[kind] = stray
    return out


# Blank-fill (`enrich_field`) runs over the SAME derived set as deliberate
# overwrite (`edit_field`), with one extra runtime guard: enrich refuses any
# field that already holds a value. That direction is the safe one. The bug
# this module exists to kill was the opposite — the overwrite surface defined
# as a copy of the blank-fill surface, which silently made overwrite the
# NARROWER of the two and hid it as inheritance.
ENRICHABLE_KINDS = ("org", "contact")


def enrichable() -> dict[str, dict[str, Any]]:
    surface = editable()
    return {kind: surface[kind] for kind in ENRICHABLE_KINDS}


# HOW A VALUE IS TYPED, in one place, because two descriptions of the same
# argument disagreed. `describe`'s note said "money is cents" while
# edit_field's own docstring said dollars — and describe is the one a model is
# told to call FIRST, so the wrong one was the loud one: a model wanting a
# $5,000,000 limit passes 500000000 and writes $500,000,000. The parser
# (money.parse_money_cents) takes DOLLARS — "2m", "$1,500,000", "1,234.56" —
# and bookkit stores the cents. mcpserver registers edit_field with this
# string interpolated into its description, so there is no second copy to rot.
VALUE_RULES = (
    "edit_field is a compare-and-set: `expecting` must match what the field "
    "holds now, and expecting=None asserts it is blank. MONEY IS ENTERED IN "
    "DOLLARS, not cents — '2m', '$1,500,000', '1,234.56' — and stored as "
    "integer cents, so pass what a human would say and never multiply by a "
    "hundred yourself. Dates are anything parse_human_date accepts except a "
    "bare 1-2 digit number, which is refused rather than read as a day of "
    "the month. A select refuses a value outside its list."
)


def describe(kind: str | None = None) -> dict[str, Any]:
    """The write surface, as data: kinds, fields, types and allowed values.

    Derived from the same source `edit_field` enforces, so it cannot drift.
    Without this a model can only discover the surface by making a
    deliberately-failing call, which costs a round trip and reads as an error
    in the transcript."""
    surface = editable()
    if kind is not None and kind not in surface:
        why = UNMAPPED_BUILDERS.get(f"{kind}_form")
        tail = f" — {why}" if why else ""
        raise ValueError(
            f"nothing describes kind {kind!r}; editable kinds: "
            f"{sorted(surface)}{tail}"
        )
    wanted = {kind: surface[kind]} if kind is not None else surface
    kinds = {
        name: {
            "fields": {
                field: (
                    {"type": "select", "values": list(vtype)}
                    if isinstance(vtype, tuple)
                    else {"type": vtype}
                )
                for field, vtype in sorted(fields.items())
            }
        }
        for name, fields in wanted.items()
    }
    out: dict[str, Any] = {"kinds": kinds}
    if kind is None:
        out["not_editable"] = {
            name.removesuffix("_form"): reason
            for name, reason in UNMAPPED_BUILDERS.items()
            if name != "org_form_initial_profile"
        }
    # BOTH per-field roosters, not just DENIED. A field a model can see on
    # the TUI or the web and cannot write here needs an answer wherever it
    # looks, and `task.assignee` had none anywhere — the whole point of
    # writing its reason down. NOT_A_COLUMN answers "why can't I set the AM
    # Best rating" too, which the module has always said deserves an answer.
    out["denied_fields"] = {
        f"{denied_kind}.{field}": reason
        for (denied_kind, field), reason in sorted({**NOT_A_COLUMN, **DENIED}.items())
        if kind is None or denied_kind == kind
    }
    out["note"] = VALUE_RULES
    return out
