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
from typing import Any

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
    "subjectivity_form": (
        "A subjectivity hangs off a submission, and submissions are not an "
        "edit_field kind — there is no _edit_target resolver for one, so a ref "
        "would advertise a kind and then refuse every id given for it. It is "
        "also the wrong primitive: settling a subjectivity moves status and "
        "satisfied_on together, which is a transition rather than a field "
        "edit, the same reason rfi_item.status is denied. The shape when it "
        "arrives is add_subjectivity / settle_subjectivity beside the quote "
        "tools — see ROADMAP's quotes entry."
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
        "Recording a market's answer writes status, response_on, premium and "
        "limit together — a transition, not a field edit. Same missing "
        "resolver as submission_form."
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
        "against `org` cannot reach it. A market_profile kind would need its "
        "own resolver."
    ),
    ("org", "am_best_rating"): (
        "Same: a market_profile column, written by orgs.set_market_profile, "
        "not by an org field write."
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


def _value_type(field: Field) -> Any:
    """A field's value type as `mcpserver._clean_typed` consumes it: the
    declared kind for everything scalar, and a tuple of the legal values for a
    select — so a refusal can list the vocabulary instead of writing junk."""
    if field.kind == "select":
        return tuple(value for _, value in field.options)
    return field.kind


def denial_reason(kind: str, field: str) -> str | None:
    """Why this field is not editable, or None if it is (or is unknown)."""
    if field in SYSTEM_COLUMNS:
        return SYSTEM_COLUMNS_REASON
    if field.endswith(FOREIGN_KEY_SUFFIX):
        return FOREIGN_KEY_REASON
    return NOT_A_COLUMN.get((kind, field)) or DENIED.get((kind, field))


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
                # not a column of this entity and nobody wrote down why —
                # refuse to advertise it rather than fail at the DB layer
                continue
            fields[field.key] = _value_type(field)
        for (also_kind, also_field), (vtype, _) in ALSO_EDITABLE.items():
            if also_kind == kind:
                fields[also_field] = vtype
        surface[kind] = fields
    return surface


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
    out["denied_fields"] = {
        f"{denied_kind}.{field}": reason
        for (denied_kind, field), reason in sorted(DENIED.items())
        if kind is None or denied_kind == kind
    }
    out["note"] = (
        "edit_field is a compare-and-set: `expecting` must match what the "
        "field holds now, and expecting=None asserts it is blank. Money is "
        "cents, dates are anything parse_human_date accepts except a bare "
        "number, and a select refuses a value outside its list."
    )
    return out
