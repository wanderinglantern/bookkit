"""What the web surface covers of the TUI's account actions, and what it does
not yet.

The destination is 1:1. Narrowing early slices is build order, not scope, and
this ledger is what stops the two from being confused: tests/test_web_parity.py
fails on any AccountScreen action that is in neither dict, so a new TUI feature
turns the suite red until its web equivalent is built or consciously deferred.

Keys are TUI action names (Binding.action, with any argument stripped) —
AccountScreen's own bindings, plus the ones its ListTable/InlineTable rows
bind for themselves (`Y` copy_row, `i` inline_edit). AccountScreen.BINDINGS
alone missed those two entirely (fix round 2, 2026-08-17); see
tests/test_web_parity.py for the widget enumeration and the guard that
catches the next one."""

from __future__ import annotations

# action name -> the web route that covers it
IMPLEMENTED: dict[str, str] = {
    "show_tab": "GET /accounts/{ref}/{tab} — Program/Relationship/Work/Pipeline, "
    "each a real route with its own count badge (see routes/account.py, "
    "docs/superpowers/specs/2026-08-17-web-visual-direction.md)",
    "inline_edit": (
        "GET/POST /accounts/{ref}/contacts/{contact_id}/cell/{key} — click-to-edit "
        "cells for role/title/email/phone (bookkit.forms.inline.CONTACT_FIELDS), "
        "Enter/Tab/Escape/blur per the visual-direction spec (routes/relationship.py, "
        "the cell contract settled in Task 6). Also on the Work tab "
        "(routes/work.py): due/title/category/description on open tasks "
        "(bookkit.forms.inline.TASK_FIELDS) and prompt/category/due_on/response "
        "on request items (bookkit.forms.inline.RFI_ITEM_FIELDS) — 'status' is "
        "deliberately excluded there since apply_rfi_item owns the "
        "status/received_on pair."
    ),
    "undo": (
        "POST /accounts/{ref}/changes/{batch_ref}/revert (routes/changes.py) — "
        "the top bar's 'Undo <last change>' pill and the right rail's "
        "per-change 'Revert' are the same POST against different batches, both "
        "through services.batches.revert, the same call the TUI's `u` and `R` "
        "make. The response is 204 + HX-Redirect (a revert can move a panel, "
        "the header badge, the tab counts and the rail at once, so no single "
        "panel swap is honest) and the outcome comes back as a token the tab "
        "route renders into a toast. FORCE IS DEFERRED: the TUI offers it "
        "behind ConfirmRevertBatch, and the web refuses conflicts outright, "
        "naming the fields that changed and pointing at the TUI's `R` — a "
        "force path needs a confirmation screen showing the plan, and "
        "inventing one unspecified is not this slice's call to make. Both "
        "controls carry hx-confirm naming what goes back: a revert cannot "
        "itself be reverted (its writes carry no batch_id), and the TUI puts "
        "the same call behind ConfirmRevertBatch, so shipping it as one "
        "unconfirmed click was the wrong trade (review round 1, F4). A browser "
        "confirm() is NOT that modal — it shows no plan — and should give way "
        "to the _confirm.html fragment when Task 10 builds it."
    ),
    "task_done": (
        "POST /accounts/{ref}/tasks/{task_id}/done (tasks_repo.complete) and "
        "POST /accounts/{ref}/requests/{request_id}/items/{item_id}/received "
        "(services.rfi.mark_received) — one TUI key ('d') drives both flows "
        "(action_task_done dispatches to _mark_item_received on the requests "
        "tab), so both share this ledger entry. Each is a POST button, not a "
        "cell: both write two fields together (status+completed_at, "
        "status+received_on) and a cell edit only ever writes one column."
    ),
    "edit_layer": (
        "BUILT 2026-08-19. The Program tab's layer table edits in place through "
        "the same inline-cell contract contacts, tasks and request items use — "
        "GET/POST .../program/{placement_id}/layers/{layer_id}/cell/{key}, "
        "keyed by forms.inline.LAYER_FIELDS whose names ARE sync.update_layer's "
        "keywords. The write goes through services.program_files.write, so it "
        "is one batch with a pre-image, and a CONFLICT (the file moved under "
        "the edit) renders a three-way — Reload / Overwrite / Keep editing — "
        "rather than the same one-line refusal an invalid value gets. "
        "Markets ride the SAME cell contract as of phase 1 (2026-08-19): a "
        "seat's carrier and share are inline cells on the chip, addressed by "
        "index (.../markets/{index}/cell/{key}), remove fetches an in-place "
        "confirm, and + market is an in-row form with carrier completion. "
        "The layer's long tail (policy number, policy dates) opens as a "
        "details row — every LAYER_FIELDS key is reachable, asserted by "
        "tests/test_web_dead_controls.py."
    ),
    "add_layer": (
        "BUILT 2026-08-19. POST .../program/{placement_id}/layers appends a "
        "pending layer, and .../layers/{layer_id}/markets binds a market to "
        "one — both through services.program_files.write, both refusing in the "
        "page with towerkit's own words when the validator says no (a gap, an "
        "overlap, an over-sign)."
    ),
    "scaffold_tower": (
        "BUILT 2026-08-19. A confirm step shows the DESTINATION PATH and then "
        "POST .../program/{placement_id}/scaffold writes the file and links it. "
        "The default path is the TUI's own rule — first configured root, "
        "<two-word-slug>-<period year>.json — mirrored rather than reinvented, "
        "so a file scaffolded from either surface lands in the same place."
    ),
}

# action name -> why it is not covered yet
PENDING: dict[str, str] = {
    "add_here": (
        "generic per-tab add ('a') — contacts, tasks, requests and request "
        "items now have one each (POST .../contacts/new, .../tasks/new, "
        ".../requests/new, .../requests/{id}/items/new), and the placements "
        "tab has one as of 2026-08-19 (POST .../program/placements); pipeline "
        "and projects do not yet, and the action covers all of them, so it "
        "stays PENDING as a whole"
    ),
    "edit_here": (
        "generic per-tab edit ('e', a whole-form modal) — for contacts, tasks "
        "and request items this is deliberately NOT what got built (Grant's "
        "2026-08-17 amendment replaced it with inline_edit's cell-by-cell "
        "editing, see that entry). Requests DO get a whole-form edit "
        "(GET/POST .../requests/{request_id}/edit, routes/work.py) — they "
        "carry FK selects the cell contract has no editor for, matching the "
        "TUI's own edit_request. So does an interaction (GET/POST "
        ".../interactions/{interaction_id}/edit, routes/relationship.py): "
        "bookkit.forms.inline declares no INTERACTION_FIELDS — the TUI edits "
        "one through the whole interaction_form modal — and a web-only inline "
        "set would fork the surfaces on the axis that module exists to keep "
        "unified, so the design prototype's dashed underline on the subject is "
        "deliberately not built (R49). Interaction CREATION is not here either: "
        "forms.entities has an edit builder and no create builder, because "
        "logging one is quick capture's job (account matching, the follow-up-"
        "task offer). Org-level edit_here (falling through to _edit_org) is "
        "still not built, so the action as a whole stays PENDING"
    ),
    "new_submission": (
        "a plain DB write (repo/submissions.create, no towerkit involvement) — "
        "not built on the web yet"
    ),
    "renew_placement": (
        "programs tab — phase 2 of the 2026-08-19 web-program plan wires the "
        "header's Renew (sync.renew, confirm-first); until then the button is "
        "not rendered (D4: unbuilt is unrendered)"
    ),
    "open_towerkit": (
        "two flows behind one key. Opening a program in towerkit is a later "
        "slice; on the projects tab this same key runs _need_to_opportunity "
        "instead — a plain projects_repo write turning a need into an "
        "opportunity, with no towerkit involvement."
    ),
    "assign_team": "team assignment editing not built on the web yet",
    "export_open_items": (
        "two flows behind one key. The XLSX export is deferred by decision "
        "(needs a file-download response the web spec does not cover — see "
        "docs/superpowers/specs/2026-08-17-web-frontend-design.md); on the "
        "placements tab this same key runs action_merge_placement instead — a "
        "DB-mutating merge with its own modal. Neither is on the web."
    ),
    "import_here": (
        "deferred by decision, not yet reached: bulk paste-import needs a "
        "browser-side parser design of its own; the TUI flow does not port"
    ),
    "delete_row": (
        "PARTLY built. `D` resolves by focused table: seven tables, FOUR kinds "
        "of row (contact, interaction, task, team assignment — AccountScreen."
        "DELETABLE), and request items are not among them on either surface. "
        "TWO kinds have a web route, each a confirm GET that writes nothing "
        "followed by a POST to the same path: contacts via "
        "/accounts/{ref}/contacts/{contact_id}/remove "
        "(services.contacts.remove) and interactions via "
        "/accounts/{ref}/interactions/{interaction_id}/delete "
        "(services.interactions.delete) — both routes/relationship.py, both "
        "through the same service the TUI calls, so neither surface can "
        "differ on the consequences shown, on the soft delete, or on the one "
        "revertible batch. Contacts were built ahead of their turn because it "
        "was a LIVE DATA problem: MCP filed a wholesaler as a client contact "
        "and no surface could take it off. Tasks (dropped, not deleted, in "
        "the TUI), request items and team assignments still have no web "
        "removal, so the action as a whole stays PENDING — do not promote "
        "this entry until they do."
    ),
    "mark_primary": (
        "not built on the web; the pending aria-disabled span it used to "
        "render was removed under D4 (2026-08-19, unbuilt is unrendered) — "
        "the rest of a contact's fields are editable (see inline_edit), this "
        "one write specifically is not"
    ),
    "paste_items": (
        "deferred by decision, not yet reached: bulk paste-import needs a "
        "browser-side parser design of its own; the TUI flow does not port"
    ),
    # the following two are bound on ListTable/InlineTable, not AccountScreen
    # itself — see _WIDGET_SOURCES in tests/test_web_parity.py, added in fix
    # round 2 after they turned out invisible to the ledger
    "copy_row": (
        "row-to-clipboard shortcut for a terminal; the web has native text "
        "selection/copy, so no dedicated route is planned unless that proves "
        "insufficient"
    ),
}


# --- the program-verb ledger (phase 2, 2026-08-19) ----------------------------
#
# Every sync.py program mutator, per surface. The action-level dicts above
# answer "does the web cover this TUI key"; this one answers the question the
# parity review had to reconstruct by hand — "who can make this WRITE" — and
# tests/test_web_parity.py discovers the verb set from sync.py's own source
# (any function that calls _mutate, plus the named non-mutate writers), so a
# new sync verb turns the suite red until it is covered or consciously
# deferred here, in both directions.

SYNC_VERBS: dict[str, dict[str, str]] = {
    "update_program": {
        "web": "placement header cells (POST /program/{placement_id}/cell/{key})",
        "tui": "e on a placement, via services.placement_edit",
        "mcp": "program_edit",
    },
    "update_layer": {
        "web": "layer cells + the details row (POST .../layers/{layer_id}/cell/{key})",
        "tui": "l on the placements tab",
        "mcp": "program_layer_edit",
    },
    "add_layer": {
        "web": "+ Add layer, applies-to select required (POST .../layers)",
        "tui": "L, applies-to select required",
        "mcp": "program_layer_add",
    },
    "remove_layer": {
        "web": "details row -> remove layer, confirm names the seats (D2)",
        "tui": "D on a placeholder carriers row, confirm names the seats",
        "mcp": "DEFERRED — no tool; whether the assistant may remove a layer "
        "is an mcpparity decision nobody has made",
    },
    "add_participant": {
        "web": "+ market, in the row (POST .../markets)",
        "tui": "offered when a submission binds (_offer_bind_to_layer) — no "
        "direct put-a-market-on-a-layer key; DEFERRED as such",
        "mcp": "program_bind",
    },
    "update_participant": {
        "web": "market cells on the chip (POST .../markets/{index}/cell/{key})",
        "tui": "e on a carriers-table row",
        "mcp": "DEFERRED — no tool; same mcpparity decision as remove_layer",
    },
    "remove_participant": {
        "web": "chip remove, confirm in place (POST .../markets/{index}/remove)",
        "tui": "D on a carriers-table row, confirm first",
        "mcp": "DEFERRED — no tool; same mcpparity decision as remove_layer",
    },
    "set_applies_to": {
        "web": "DEFERRED — phase 3 (applies-to chips on layer rows)",
        "tui": "DEFERRED — phase 3; today the whole verb is dead code with tests",
        "mcp": "DEFERRED — phase 3",
    },
    "scaffold_program": {
        "web": "scaffold confirm, destination editable (POST .../scaffold)",
        "tui": "t on the placements tab, destination editable",
        "mcp": "DEFERRED BY DECISION — mcpparity: placements are read-only "
        "to the assistant",
    },
    "renew": {
        "web": "Renew on the program section, confirm-first (POST .../renew)",
        "tui": "r on the placements tab (ConfirmRenew)",
        "mcp": "DEFERRED — renewal from an assistant needs its own decision",
    },
    # --- phase 3: structure (D1) — routes land later in the same branch ---
    "add_line": {
        "web": "the lines strip, + line in-row (phase 3 task 2)",
        "tui": "via o -> towerkit's editor (the terminal's structure surface)",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "rename_line": {
        "web": "the lines strip, name as an inline cell (phase 3 task 2)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "remove_line": {
        "web": "the lines strip, confirm names the cascade (phase 3 task 2)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "set_statutory": {
        "web": "the details row's statutory toggle (phase 3 task 3)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "set_follows_underlying": {
        "web": "the details row's follows toggle (phase 3 task 3)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
}


# --- the towerkit-editor capability ledger (Grant, 2026-08-19) -----------------
#
# "Fully built but not accessible": statutory handling was modelled, projected
# and rendered everywhere, and no browser control could change it — because
# the parity work enumerated BOOKKIT's surfaces and treated towerkit's editor
# as out of scope behind the TUI's `o`. A daily-driver web UI has no `o`, so
# the honest parity universe is EVERYTHING THE TERMINAL WORKFLOW CAN REACH,
# towerkit's editor included. This ledger enumerates towerkit.edit's public
# operations — introspected at runtime by tests/test_web_parity.py, so an op
# towerkit GROWS turns the suite red until bookkit covers it or defers it
# here by name, with a reason. (The current towerkit checkout is the
# in-flight feat/mcp-hardening branch; its branch-only ops are marked as
# such and must be re-decided when the branch merges.)

TOWERKIT_EDIT_OPS: dict[str, str] = {
    # utilities consumed by bookkit's own wrappers — not user capabilities
    "slugify": "utility — id naming; consumed by sync.add_layer",
    "unique_id": "utility — id collision rule; consumed by sync.add_layer",
    "ordinal": "utility — layer auto-naming inside edit.add_layer",
    "suggested_attach": "utility — default attachment inside edit.add_layer",
    "heal_follows": "utility — run by sync.write_through on every write",
    "parse_states": "utility (BRANCH-ONLY) — state-list parsing for set_states",
    "adopt": "internal — towerkit's line-transfer flow; no bookkit use",
    # covered — a sync wrapper exists and a surface reaches it
    "add_line": "sync.add_line (phase 3); see SYNC_VERBS",
    "rename_line": "sync.rename_line (phase 3); see SYNC_VERBS",
    "remove_line": "sync.remove_line (phase 3); see SYNC_VERBS",
    "add_layer": "sync.add_layer; see SYNC_VERBS",
    "remove_layer": "sync.remove_layer (D2); see SYNC_VERBS",
    "set_applies_to": "sync.set_applies_to; chips land phase 3 task 3",
    "set_follows_underlying": "sync.set_follows_underlying (phase 3); see SYNC_VERBS",
    # deferred BY NAME, with the reason
    "set_line_group": (
        "DEFERRED — line grouping is diagram cosmetics (Line.group is not "
        "even projected, see NOTES.md); joins the Towers page work (phase 4)"
    ),
    "move_line": (
        "DEFERRED — column order in the drawing; joins the Towers page work "
        "(phase 4) where the drawing is the point"
    ),
    "restack": (
        "DEFERRED — a bulk re-seat of every layer is a wide blast radius for "
        "one click; needs its own confirm design showing the before/after"
    ),
    "add_retention": (
        "DEFERRED — retentions render in the tower and the SOI; their editor "
        "is phase 4 (the drawing surface), not a table row"
    ),
    "edit_retention": "DEFERRED — with add_retention (phase 4)",
    "remove_retention": "DEFERRED — with add_retention (phase 4)",
    "add_sublimit": "DEFERRED — with add_retention (phase 4)",
    "edit_sublimit": "DEFERRED — with add_retention (phase 4)",
    "remove_sublimit": "DEFERRED — with add_retention (phase 4)",
    # branch-only (feat/mcp-hardening): not on towerkit main — do not depend;
    # re-decide each when the branch merges
    "set_statutory": (
        "BRANCH-ONLY in towerkit; bookkit's sync.set_statutory writes the "
        "field directly (established field-write practice) so the web toggle "
        "does not depend on the in-flight branch"
    ),
    "set_states": "BRANCH-ONLY — SOI prose field; decide when the branch merges",
    "set_premium_detail": "BRANCH-ONLY — SOI prose field; decide when the branch merges",
    "add_named_limit": "BRANCH-ONLY — decide when the branch merges",
    "edit_named_limit": "BRANCH-ONLY — decide when the branch merges",
    "remove_named_limit": "BRANCH-ONLY — decide when the branch merges",
    "set_field": "BRANCH-ONLY — the MCP field-write seam; decide when the branch merges",
    "set_container": "BRANCH-ONLY — the MCP container seam; decide when the branch merges",
}
