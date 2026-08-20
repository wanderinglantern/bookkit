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
catches the next one.

PipelineScreen (the GLOBAL kanban — h/l columns, >/< card moves across every
account) stays TUI-only and is outside this ledger's key space: as of gap 4
(2026-08-20) the web's pipeline surface is the ACCOUNT tab, now writable
(responses, the bind offer, opportunities with stage moves, subjectivities —
see routes/pipeline.py), while the book-wide board remains a terminal
screen."""

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
    "export_open_items": (
        "BUILT 2026-08-19 (phase 4), both flows behind the TUI's one key: the "
        "open-items workbook downloads from GET /accounts/{ref}/export/"
        "open-items.xlsx (the same services.export_open_items.write the "
        "terminal calls) — the anchor renders in the PROGRAMS PANEL HEAD, "
        "account-scoped, so an account with no linked program still reaches "
        "it (the audit caught it gated behind a linked placement's strip: "
        "built but not accessible), and the merge half landed in phase 3 (POST "
        ".../program/{placement_id}/merge). Downloads are plain anchor GETs "
        "with Content-Disposition: attachment — the file-response decision is "
        "in DECISIONS.md. Tower SVG/PDF and the schematic workbook download "
        "beside it, through towerkit's own renderers."
    ),
    "new_submission": (
        "BUILT 2026-08-19 (phase 2): + Submission on every program section "
        "(GET/POST .../program/{placement_id}/submissions), the shared "
        "submission_form, success redirecting to the Pipeline tab where the "
        "row is visible. (The 2026-08-19 audit caught this entry still "
        "PENDING after the build — the ledger's prose is hand-maintained, "
        "and a shipped feature must move its entry in the same commit.)"
    ),
    "renew_placement": (
        "BUILT 2026-08-19 (phase 2): Renew on every program section, "
        "confirm-first, sync.renew in one web batch "
        "(POST .../program/{placement_id}/renew). (Audit-corrected: this "
        "entry sat in PENDING contradicting SYNC_VERBS in this same file.)"
    ),
    "assign_team": (
        "BUILT 2026-08-20 (gap 7). The rail's Team section is live "
        "(routes/team.py, account/_team_panel.html): Assign is GET/POST "
        "/accounts/{ref}/team/assign (assignment_form's member select + "
        "role/lines/notes, account-level org_id), each row edits IN PLACE "
        "over role/lines/notes via GET/POST .../team/{assignment_id}/edit "
        "(assignment_form(existing=...) renders no scope field — re-scoping "
        "is unassign + assign, the repo's own rule) and removes behind a "
        "confirm step at .../team/{assignment_id}/remove (team.unassign in "
        "one batch, tool='team_unassign', the TUI D-flow's own summary). "
        "DEAL-LEVEL assign is NOT built: the TUI offers it only from the "
        "placements table, and the rail is account-scoped — assigning to a "
        "placement from the web waits for a placements-tab control. The "
        "TUI's '+ new team member…' sentinel in the who-select refuses with "
        "a pointer at /team rather than chaining a second form."
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
        ".../requests/new, .../requests/{id}/items/new), the placements "
        "tab has one as of 2026-08-19 (POST .../program/placements), and the "
        "pipeline tab has BOTH of its adds as of 2026-08-20 — "
        "POST .../pipeline/opportunities/new and "
        "POST .../pipeline/submissions/{id}/subjectivities/new, the same "
        "focus-dependent pair the TUI's `a` resolves on that tab "
        "(routes/pipeline.py); projects does not yet, and the action covers "
        "all of them, so it stays PENDING as a whole"
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
        "deliberately not built (R49). Interaction CREATION is not here either, "
        "and as of 2026-08-20 that is because it EXISTS where it belongs: "
        "logging one is quick capture's job (account matching, the follow-up-"
        "task offer), and the web's quick capture is GET/POST /capture — see "
        "SCREENS['quick_capture_app_key']. Org-level edit_here (falling "
        "through to _edit_org) is still not built, so the action as a whole "
        "stays PENDING"
    ),


    "open_towerkit": (
        "two flows behind one key. Opening a program in towerkit is a later "
        "slice; on the projects tab this same key runs _need_to_opportunity "
        "instead — a plain projects_repo write turning a need into an "
        "opportunity, with no towerkit involvement."
    ),
    "import_here": (
        "deferred by decision, not yet reached: bulk paste-import needs a "
        "browser-side parser design of its own; the TUI flow does not port"
    ),
    "delete_row": (
        "PARTLY built. `D` resolves by focused table: seven tables, FOUR kinds "
        "of row (contact, interaction, task, team assignment — AccountScreen."
        "DELETABLE), and request items are not removable in the TUI — but the WEB "
        "removes both request items (POST .../requests/{request_id}/items/"
        "{item_id}/remove) and whole requests; the web is ahead here "
        "(audit-corrected 2026-08-19 — this entry used to deny it). "
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
        "and no surface could take it off. Team assignments joined 2026-08-20 "
        "(gap 7): confirm GET + POST /accounts/{ref}/team/{assignment_id}/"
        "remove, routes/team.py, one batch with the TUI D-flow's own tool and "
        "summary. Tasks (dropped, not deleted, in the TUI) and request items "
        "still have no web removal, so the action as a whole stays PENDING — "
        "do not promote this entry until they do."
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
        "web": "the details row's applies-to chips — the verb's first caller "
        "ever (POST .../layers/{layer_id}/applies-to)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
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
        "web": "the lines strip: + line, in row (POST .../lines)",
        "tui": "via o -> towerkit's editor (the terminal's structure surface)",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "rename_line": {
        "web": "the lines strip: the name is an inline cell (POST .../lines/{line_id}/cell/name)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "remove_line": {
        "web": "the lines strip: remove, confirm naming both grades of blast "
        "(POST .../lines/{line_id}/remove)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "set_statutory": {
        "web": "the details row: mark statutory (confirm names the limit given "
        "up), leave asks the figure (POST .../layers/{layer_id}/statutory)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "set_follows_underlying": {
        "web": "the details row: one-click follows toggle (POST .../layers/{layer_id}/follows)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    # --- phase 4: terms and order ---
    "add_retention": {
        "web": "the terms strip: + retention, in row (POST .../retentions)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "edit_retention": {
        "web": "the terms strip: chip opens the in-row form "
        "(POST .../retentions/{index})",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "remove_retention": {
        "web": "the terms strip: remove, confirm in place "
        "(POST .../retentions/{index}/remove)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "add_sublimit": {
        "web": "the terms strip: + sublimit, in row (POST .../sublimits)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "edit_sublimit": {
        "web": "the terms strip: chip opens the in-row form "
        "(POST .../sublimits/{index})",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "remove_sublimit": {
        "web": "the terms strip: remove, confirm in place "
        "(POST .../sublimits/{index}/remove)",
        "tui": "via o -> towerkit's editor",
        "mcp": "DEFERRED — structure from an assistant is undecided",
    },
    "move_line": {
        "web": "the lines strip: per-chip left/right "
        "(POST .../lines/{line_id}/move)",
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
    "set_applies_to": "sync.set_applies_to — the details row's chips; see SYNC_VERBS",
    "set_follows_underlying": "sync.set_follows_underlying (phase 3); see SYNC_VERBS",
    # deferred BY NAME, with the reason
    "set_line_group": (
        "DEFERRED BY NAME — line grouping is diagram cosmetics (Line.group is "
        "not even projected, see NOTES.md); revisit with evidence of demand"
    ),
    "move_line": "sync.move_line (phase 4) — the lines strip's per-chip arrows",
    "restack": (
        "UNREACHABLE through the guarded seam, by proof rather than deferral "
        "(phase 4): write_through only accepts files that already projected — "
        "i.e. validated — and a valid tower has no gaps or overlaps for "
        "restack to heal, so through bookkit it is a no-op on every reachable "
        "input. restack is towerkit's DRAFT healer and belongs to its editor; "
        "a web button that provably does nothing would be dead chrome (D4)."
    ),
    "add_retention": "sync.add_retention (phase 4); see SYNC_VERBS",
    "edit_retention": "sync.edit_retention (phase 4); see SYNC_VERBS",
    "remove_retention": "sync.remove_retention (phase 4); see SYNC_VERBS",
    "add_sublimit": "sync.add_sublimit (phase 4); see SYNC_VERBS",
    "edit_sublimit": "sync.edit_sublimit (phase 4); see SYNC_VERBS",
    "remove_sublimit": "sync.remove_sublimit (phase 4); see SYNC_VERBS",
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


# --- the screen-level ledger (2026-08-19 audit) --------------------------------
#
# The audit's structural finding: the ledgers above cover ACCOUNT actions,
# sync verbs and towerkit ops — and none of them can turn red when a WHOLE
# TUI SCREEN has no web answer. Today, Navigator, Calendar, Markets, Team,
# Search and the global Pipeline board were invisible to every guard. This
# ledger enumerates the TUI's screen modules (tests/test_web_parity.py
# discovers them from src/bookkit/tui/screens/*.py, so a new screen turns
# the suite red) plus the app-level keys, each PRESENT with its web home or
# DEFERRED by name with a reason.

SCREENS: dict[str, str] = {
    "account": (
        "PRESENT — /accounts/{ref}/{program|relationship|work|pipeline}; the "
        "per-action truth lives in IMPLEMENTED/PENDING above. Overview folds "
        "into the rail; Projects and Documents tabs are web-absent (documents "
        "are a read-only rail list; projects are a named later slice)."
    ),
    "book": (
        "PRESENT for create+edit (gap 5): New account on /book (org_form "
        "into a form host, POST /book/accounts through the SHARED duplicate "
        "guard services.orgs.find_duplicate — extracted; mcpserver, both TUI "
        "create doors and the web all consult it now), Edit on the account "
        "header (org_form_initial_profile, POST /accounts/{ref}/edit). The "
        "filter and the book XLSX export stay deferred by name."
    ),
    "today": (
        "PRESENT — GET /today (the web's front door: / redirects to it). "
        "The TUI's four panes AND the Navigator's eight attention leaves in "
        "one page — overdue first and never off the list — plus done-from-"
        "the-list for tasks and the cross-account changes list with Revert."
    ),
    "navigator": (
        "PARTIAL — attention and the changes list folded into /today; the "
        "accounts tree is /book, the drawn programs /towers. Still TUI-only: "
        "the batch before-and-after diff (enter on a change) and force-revert."
    ),
    "calendar": (
        "PRESENT — GET /calendar: months across, accounts down, status-"
        "coloured chips at each expiry, overdue in its own leading column."
    ),
    "markets": (
        "PARTIAL (gap 6, 2026-08-20; routes/markets.py, tests/"
        "test_web_markets.py). MarketsScreen: enter -> GET /markets/{ref}; "
        "`a` new_market -> GET/POST /markets/new (org_form "
        "default_kind='market', same apply_org); `e` edit_market -> "
        "GET/POST /markets/{ref}/edit (org_form_initial_profile); `x` "
        "merge_market -> /markets/{ref}/merge + /merge/confirm — a confirm "
        "step naming the blast radius and the alias-preserving rule, then "
        "services.merge.merge_markets in one batch under the TUI's own "
        "tool name (merge_markets); `N` nest_market -> GET/POST "
        "/markets/{ref}/nest (orgs.set_parent; both TUI paths — existing "
        "master, or create one on the spot); `A` add_alias -> GET/POST "
        "/markets/{ref}/aliases/new (aliases.set_alias, suggestions from "
        "aliases.unresolved_carriers). MarketDetailScreen: `a` add_appetite "
        "-> .../appetite/new; `e` edit_row -> .../appetite/{id}/edit and "
        ".../underwriters/{id}/edit (whole forms, as the TUI's are); `D` "
        "delete_row -> .../appetite/{id}/remove — confirm-first on the web "
        "because the undo pill is account-scoped and `u` is not beside it "
        "here; `w` add_underwriter -> .../underwriters/new (role defaults "
        "to underwriter in the write, mirroring the TUI's commit); enter on "
        "an exposure row -> the account link in the ON THE TOWER table. "
        "STILL MISSING, and why it is PARTIAL: `i` import_underwriter "
        "(paste signature) stays deferred by name — it is a TUI paste flow, "
        "and bulk paste-import needs a browser-side parser design of its "
        "own (same deferral as import_here/paste_items above); `u` undo has "
        "no markets-page control — the web's undo pill and changes rail are "
        "account-scoped (routes/changes.py), so a market-scoped revert "
        "control is a later slice."
    ),
    "team": (
        "BUILT 2026-08-20 (gap 7). GET /team (routes/team.py, team.html) — "
        "TeamScreen's columns (name, title, specialty, where assigned) plus "
        "assignment count and active state; the `f` filter is ?line=cyber "
        "through the same services.team.find_specialists, match evidence "
        "shown. `a` is + New member (GET/POST /team/members/new), `e` is the "
        "whole-form edit (GET/POST /team/members/{id}/edit) — renames inherit "
        "repo/team.py's duplicate guard, which is where it lives. Beyond the "
        "TUI: retire behind a confirm step naming every live assignment "
        "(GET/POST /team/members/{id}/deactivate, cascade as ONE revertible "
        "batch via services.team.member_deactivate — the same call mcpserver "
        "makes) and Reactivate for retired members. NOT built: `w` "
        "assign-from-the-member (the rail's Assign covers the account "
        "direction; starting from a member means picking a client, a picker "
        "this slice does not add) and `i` paste-signature import (bulk "
        "paste-import is deferred by decision, same as import_here above)."
    ),
    "pipeline": (
        "PARTIAL — the account Pipeline TAB is readable AND writable "
        "(routes/pipeline.py, gap 4): the market RESPONSE form "
        "(quote/decline with the bind offer), opportunity create/edit, "
        "stage moves, close won/lost, and subjectivity add. Still "
        "web-absent: the TUI's GLOBAL kanban — one board across accounts; "
        "on the web the cross-account view is Today's quotes-expiring "
        "section, and working a deal means opening its account's tab."
    ),
    "search": (

        "PRESENT — GET /search (routes/search.py): the `/` modal as a page, "
        "repo.search end to end (FTS5 + the email LIKE pass), grouped "
        "Accounts / Contacts / Interactions, every hit linking to the owning "
        "account's relationship tab — the same org_id the TUI's enter key "
        "opens. Reached from the topbar's live search form on every page. "
        "This also covers the command palette's record-jump half; the "
        "palette's screen-jump half is the topbar nav's job, not this page's."
    ),
    "help": (
        "N/A BY SHAPE — a key-reference screen; the web equivalent is "
        "discoverable controls, held to by tests/test_web_dead_controls.py."
    ),
    "import_screen": (
        "DEFERRED BY DECISION — the book file import (staged report, "
        "one-transaction commit) does not port; paste imports likewise "
        "(see PENDING import_here/paste_items)."
    ),
    "onboarding": "DEFERRED — the client onboarding wizard has no web answer.",
    "quick_capture_app_key": (
        "PRESENT — GET/POST /capture (routes/capture.py), the TUI's app-level "
        "`n`. Reached from the top bar's global '+ Log' on every page and the "
        "account header's '+ Log interaction' (GET /capture?org={ref} "
        "preselects the account). Attendee resolution is the SHARED "
        "services.capture.resolve_attendees (the TUI widget was rewired "
        "through it the same commit); the log is one batch "
        "(source='web', tool='log_interaction' — interaction + attendee links "
        "one undo unit); a follow-up phrase OFFERS a task on a page of its "
        "own, created via POST /capture/task inside its own batch "
        "(tool='task_add' — and since 2026-08-20 the TUI's ConfirmTask "
        "batches the same way; both surfaces also refuse an unparseable "
        "date with forms.spec.date_refusal). One divergence left: the "
        "account picker is a select over CLIENT orgs where the TUI "
        "fuzzy-matches every org — logging against a market still needs "
        "the TUI."
    ),
    "sync_and_settings_app_keys": (
        "DEFERRED — `y` sync programs + link review, `,` program-roots "
        "setup; the web scaffold refusal still points at the terminal for "
        "roots, and no web surface shows file sync state (in sync / changed "
        "on disk)."
    ),
}
