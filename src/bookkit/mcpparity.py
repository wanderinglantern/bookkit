"""What the MCP surface reaches of the book, entity by entity and verb by verb.

THIS LEDGER POSES A QUESTION IT DOES NOT ANSWER. `web/parity.py` can say "the
destination is 1:1 with the TUI" because for the web that destination is
obvious. MCP's is not: an assistant should probably not be able to delete an
account, and it probably should be able to add an item to a request it already
filed. Nobody has decided which of the gaps below are permanent. Filling this
in was the point — the gaps are now countable, named and machine-checked, so
the decision can be made in one sitting instead of rediscovered one bug at a
time.

WHY IT EXISTS. Every MCP registration test in the suite is a *subset*
assertion (`{...} <= names`), so a 45th tool or a deleted tool changes no
assertion anywhere. `web/parity.py` fails in BOTH directions — an unaccounted
TUI action turns it red, and so does a stale entry — and nothing equivalent
guarded MCP. tests/test_mcp_parity.py gives this the same shape:

- every (entity, verb) cell over repo.base.ENTITY_TABLES is in exactly one of
  IMPLEMENTED and DEFERRED, so a new entity table turns the suite red;
- every key in either dict is a real cell, so a deleted entity does too;
- every tool named here is registered, so a rename turns it red;
- every registered tool is named here, so a NEW tool turns it red. That is the
  direction nothing checked before.

READ semantics, so entries can be compared: a cell is IMPLEMENTED when a tool
returns that entity at all. Several of those returns are thin — a contact with
no ref, a submission with no underwriter — and the note says so rather than
the cell being downgraded. Thin is a different problem from absent and the
ledger should not blur them.
"""

from __future__ import annotations

VERBS = ("create", "read", "update", "delete")

# (entity, verb) -> (the tools that cover it, what they do and what they don't)
IMPLEMENTED: dict[tuple[str, str], tuple[tuple[str, ...], str]] = {
    # --- org ---
    ("org", "create"): (
        ("client_create",),
        "duplicate-guarded: rapidfuzz WRatio at 87 refuses a near-name and "
        "names the match, so 'Henderson Grp' cannot quietly become a second "
        "Henderson Group. Optional contacts and tasks land in the same batch.",
    ),
    ("org", "read"): (
        ("search", "staleness_report", "today_brief"),
        "search returns kind/title/snippet and NO ids by design; every "
        "client-scoped tool resolves a name or ref itself through "
        "_resolve_client, which refuses on a miss and names the nearest "
        "candidates. An account is never fuzzy-matched into a write.",
    ),
    ("org", "update"): (
        ("edit_field", "enrich_field"),
        "edit_field is compare-and-set over the derived field set "
        "(mcpsurface); enrich_field is the same set, fill-blanks-only. "
        "org.kind is denied — see mcpsurface.DENIED.",
    ),
    # --- contact ---
    ("contact", "create"): (
        ("contact_add",),
        "resolves the client, refuses a duplicate person, and can set primary.",
    ),
    ("contact", "read"): (
        ("search",),
        "THIN. search matches a contact by name and returns no ref and no "
        "org, so five people called Chen are five identical rows (AE review, "
        "2026-08-18); fts_contact does not index email. Nothing enumerates "
        "one account's people — contact_add's own duplicate refusal is the "
        "only place a list of them appears. edit_field resolves a contact by "
        "name WITHIN a client, so writes work; discovery does not.",
    ),
    ("contact", "update"): (
        ("edit_field", "enrich_field"),
        "first/last name, title, role, email, phone, mobile, linkedin and "
        "notes. `role` and both names are new with the derivation.",
    ),
    ("contact", "delete"): (
        ("contact_remove",),
        "services.contacts.remove — soft, revertible, and it says what it "
        "took off. Built ahead of its turn because MCP itself filed a "
        "wholesaler as a client contact and no surface could undo it.",
    ),
    # --- interaction ---
    ("interaction", "create"): (
        ("log_activity",),
        "GAP NAMED IN THE AUDIT: it takes neither `type` nor `occurred_on`, "
        "so the assistant cannot record yesterday's call, and cannot correct "
        "one afterwards (see interaction/update).",
    ),
    ("interaction", "read"): (
        ("recent_activity",),
        "returns interaction_ref — added specifically because a mistake "
        "found later was unnameable.",
    ),
    ("interaction", "delete"): (
        ("activity_delete",),
        "soft and revertible — the same services call the TUI makes, so "
        "neither surface can differ on what a deletion costs.",
    ),
    # --- task ---
    ("task", "create"): (
        ("task_create",),
        "optionally scoped to a client, with a human date that refuses a "
        "bare number rather than guessing a month.",
    ),
    ("task", "read"): (
        ("today_brief", "open_items"),
        "today_brief is due-or-overdue-today only; open_items is the full "
        "list including undated and future-due, with refs.",
    ),
    ("task", "update"): (
        ("edit_field", "task_complete", "task_reopen"),
        "status and completed_at move together and belong to the two verbs; "
        "everything else is a field edit, priority included.",
    ),
    # --- placement ---
    ("placement", "read"): (
        ("renewals_due", "list_programs", "program_summary", "program_layers"),
        "renewals_due counts to the earliest LINE end and names the lines of "
        "cover. program_layers' description promises participants and "
        "sync.layer_details returns none (AE review).",
    ),
    ("placement", "update"): (
        (
            "program_edit",
            "program_bind",
            "program_layer_add",
            "program_layer_edit",
            "program_revert_file",
        ),
        "the guarded towerkit cycle — load, mutate, validate, canonical dump, "
        "re-project, sha256-guarded against a concurrent editor. The "
        "placement ROW is a projection, which is why it is not an edit_field "
        "kind (mcpsurface.UNMAPPED_BUILDERS).",
    ),
    # --- opportunity ---
    ("opportunity", "create"): (
        ("opportunity_create",),
        "returns the OPP- ref, which for a long time was the only way an "
        "assistant could ever hold one.",
    ),
    ("opportunity", "read"): (
        ("opportunities", "pipeline_status"),
        "`opportunities` closed the audit's worst finding: before it, no read "
        "tool returned an OPP- ref, so opportunity_stage and "
        "edit_field(kind='opportunity') could only ever act on a deal the "
        "assistant had created in the same session. pipeline_status is "
        "aggregates only and names no deal.",
    ),
    ("opportunity", "update"): (
        ("edit_field", "opportunity_stage"),
        "stage/outcome/closed_at/loss_reason belong to opportunity_stage, "
        "which refuses an illegal move by naming the legal ladder.",
    ),
    # --- submission ---
    ("submission", "read"): (
        ("today_brief", "program_summary"),
        "THIN, and it is the gap the AE review rated as the one that loses "
        "money rather than time. today_brief names submissions past SLA by "
        "MARKET with no ref and no underwriter contact — six rows saying "
        "'Travelers', which you cannot email. "
        "submission.underwriter_contact_id is declared and used by nothing.",
    ),
    # --- team_member ---
    ("team_member", "create"): (
        ("member_create",),
        "name uniqueness is guarded in repo/team, not in the caller — two "
        "colleagues sharing a name made every later lookup land on the "
        "first match.",
    ),
    ("team_member", "read"): (
        ("team_roster",),
        "members with their assignments and the exact assignment_id the write "
        "tools take.",
    ),
    ("team_member", "update"): (
        ("edit_field", "member_deactivate", "member_reactivate"),
        "`active` belongs to the two verbs: deactivate refuses while "
        "assignments are live, and cascade=True removes them in one "
        "revertible batch. Renames go through edit_field behind the duplicate "
        "guard.",
    ),
    # --- team_assignment ---
    ("team_assignment", "create"): (
        ("team_assign",),
        "scoped to a client or a placement, and it creates the member too "
        "when the name is new.",
    ),
    ("team_assignment", "read"): (
        ("team_roster",),
        "with the exact assignment_id that edit_field and team_unassign "
        "take — read it before any team write.",
    ),
    ("team_assignment", "update"): (
        ("edit_field",),
        "role/lines/notes only. Re-scoping is unassign + assign, deliberately "
        "separate — see the foreign-key rule in mcpsurface.",
    ),
    ("team_assignment", "delete"): (
        ("team_unassign",),
        "by assignment_id, exact — never by name, because one colleague "
        "holds several assignments on one account.",
    ),
    # --- project ---
    ("project", "create"): (
        ("project_create",),
        "returns the PRJ- ref that need_add and edit_field take.",
    ),
    ("project", "read"): (
        ("open_items", "today_brief"),
        "reached through their needs: a project is named on every need row.",
    ),
    ("project", "update"): (
        ("edit_field",),
        "name, site, status, start/end dates, description and notes — the "
        "whole of project_form plus the notes column no form declares.",
    ),
    # --- project_need ---
    ("project_need", "create"): (
        ("need_add",),
        "line + needed_by against a project ref; an unmet need never falls "
        "off the attention window.",
    ),
    ("project_need", "read"): (
        ("today_brief", "open_items"),
        "unmet needs never fall off the attention window.",
    ),
    ("project_need", "update"): (
        ("edit_field",),
        "status is denied: it implies links (an opportunity, a placement) "
        "that are separate columns — mcpsurface.DENIED.",
    ),
    # --- rfi_request ---
    ("rfi_request", "create"): (
        ("request_create",),
        "one call files the request AND all its items, splitting a pasted "
        "numbered block into one item per line, in one transaction.",
    ),
    ("rfi_request", "read"): (
        ("requests_to_chase", "request_items", "open_items"),
        "with request_ref, the open/total counts and who asked.",
    ),
    ("rfi_request", "update"): (
        ("edit_field",),
        "title/requested_on/due_on/notes. cancelled_at is denied because "
        "edit_field can set a date and cannot clear one.",
    ),
    # --- rfi_item ---
    ("rfi_item", "read"): (
        ("request_items", "open_items"),
        "with the exact item_ref the two transition tools take — no fuzzy "
        "prompt matching, same contract as task_complete.",
    ),
    ("rfi_item", "update"): (
        ("edit_field", "request_item_received", "request_item_waive"),
        "status OWNS received_on, so both belong to the verbs.",
    ),
}

# (entity, verb) -> why it is not on the surface. Nothing here is a promise to
# build it; several are decisions to leave it alone.
DEFERRED: dict[tuple[str, str], str] = {
    # --- deliberate: the assistant should not do this ---
    ("org", "delete"): (
        "DECISION, not a gap. Removing an account cascades through "
        "placements, programs, contacts and history, and the TUI has no "
        "delete either (merge is the closest thing). Nothing should be able "
        "to do this in one call."
    ),
    ("placement", "create"): (
        "DECISION. Placements are read-only to the assistant; a program is "
        "scaffolded in towerkit, where its structure can be validated."
    ),
    ("placement", "delete"): (
        "DECISION. Same as placement/create: the proj_* tables are a "
        "rebuildable cache of a towerkit file, so deleting the row would "
        "not delete the program and the next projection would put it back."
    ),
    ("team_member", "delete"): (
        "DECISION. A colleague with history is deactivated, never removed — "
        "member_deactivate is the retire path and it is revertible."
    ),
    ("task", "delete"): (
        "The TUI drops a task (status='dropped') rather than deleting it and "
        "MCP has neither. task_complete is the normal end; if 'drop' should "
        "exist here it is a verb, not a delete."
    ),
    ("rfi_item", "delete"): (
        "request_item_waive is the 'this is never coming' path and it keeps "
        "the row, which is the right answer for an audit trail. A real delete "
        "is probably not wanted."
    ),
    # --- unbuilt, and arguably should be ---
    ("interaction", "update"): (
        "No interaction kind in edit_field: _edit_target has no resolver, "
        "though recent_activity already returns the refs one would need. "
        "Audit rank 10. The body in particular had no way to be corrected on "
        "ANY surface until the TUI's interaction_form was added (review F33)."
    ),
    ("rfi_item", "create"): (
        "REAL GAP. request_create takes its items at creation and nothing "
        "adds an item to a request that already exists — so an underwriter's "
        "follow-up question cannot be filed against the request it belongs "
        "to. The TUI has this ('a' on the items table)."
    ),
    ("rfi_request", "delete"): (
        "Withdrawal is `cancelled_at` (blank = live, a date = withdrawn), and "
        "the denylist keeps that off edit_field because a date cannot be "
        "cleared through it. So a request filed in error cannot be withdrawn "
        "here. A `request_cancel` verb that could also un-cancel is the "
        "shape this wants."
    ),
    ("submission", "create"): (
        "REAL GAP, and the AE review's top-ranked one. Nothing on this "
        "surface records a submission going out, a market answering, a quote, "
        "an expiry or a subjectivity. The in-flight placement view is being "
        "built; MCP should follow it, not lead it."
    ),
    ("submission", "update"): (
        "Same as submission/create: no submission tool exists at all, so a "
        "quote landing, an expiry lapsing and a subjectivity being cleared "
        "are all invisible here."
    ),
    ("submission", "delete"): (
        "Same as submission/create. A submission that went out is a fact "
        "about the market and probably should never be removable anyway; "
        "'withdrawn' is already a status."
    ),
    ("opportunity", "delete"): (
        "A deal that went nowhere is marked lost through opportunity_stage, "
        "which keeps it in the hit-rate denominator. Deleting one would "
        "quietly flatter the numbers."
    ),
    ("project", "delete"): (
        "Not built. A project holds needs which hold links to opportunities "
        "and placements; removing one needs the same care as an account."
    ),
    ("project_need", "delete"): (
        "Not built. Needs are the thing the attention window refuses to drop, "
        "so removing one is exactly the write that should be hard."
    ),
    # --- whole entities nothing touches ---
    ("appetite", "create"): (
        "SURPRISE WORTH READING. Nothing on the MCP surface touches market "
        "appetite at all, so the assistant cannot answer 'who writes cyber "
        "for a $50m manufacturer' — arguably the most useful question the "
        "book can answer and the one an LLM is best placed to ask. Appetite "
        "is maintained on the Markets screen."
    ),
    ("appetite", "read"): (
        "See appetite/create. Nothing returns an appetite row, so the "
        "assistant cannot even ANSWER an appetite question, let alone "
        "maintain one."
    ),
    ("appetite", "update"): (
        "See appetite/create. Appetite is edited on the Markets screen, "
        "where the market it belongs to is on the surrounding screen."
    ),
    ("appetite", "delete"): (
        "See appetite/create. repo/orgs.delete_appetite is soft and "
        "undoable, but no MCP tool reaches it."
    ),
    ("document", "create"): (
        "A document is a path on Grant's disk. The assistant cannot verify "
        "one exists, so filing one would be recording a claim rather than a "
        "fact."
    ),
    ("document", "read"): (
        "See document/create. No tool lists an account's documents, so the "
        "assistant cannot say what is already on file before asking for it "
        "again."
    ),
    ("document", "update"): (
        "See document/create. Correcting a path the assistant cannot read "
        "is the same problem as filing one."
    ),
    ("document", "delete"): (
        "See document/create. Removing a document row leaves the file, so "
        "this is a bookkeeping write with a misleading name."
    ),
}

# Tools that are not one entity's CRUD. Listed so the roster check can be
# exhaustive in both directions without pretending these are cells.
NON_ENTITY_TOOLS: dict[str, str] = {
    "describe": (
        "the write surface as data — kinds, fields, types, vocabularies, and "
        "the denied fields with their reasons. Derived from the same "
        "declarations edit_field enforces."
    ),
    "list_batches": (
        "recent undo units. NB the docstring says 'changes THIS server made' "
        "and repo/batches.recent applies no source filter, so it returns TUI "
        "and web batches too — the tool is MORE capable than advertised, "
        "which is why a model will not reach for it to answer 'what changed "
        "on this account this week'."
    ),
    "revert_batch": (
        "all-or-nothing revert, refusing when a field changed since and "
        "naming it. Deliberately unbatched: a revert's own writes carry no "
        "batch_id, so it cannot itself be reverted."
    ),
}
