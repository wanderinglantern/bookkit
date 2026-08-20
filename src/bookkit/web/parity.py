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
        "Correcting or removing a market rides the same seam."
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
    "renew_placement": "placements tab — later slice, needs towerkit writes (sync.renew)",
    "open_towerkit": (
        "two flows behind one key. Opening a program in towerkit is a later "
        "slice; on the projects tab this same key runs _need_to_opportunity "
        "instead — a plain projects_repo write turning a need into an "
        "opportunity, with no towerkit involvement."
    ),
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
        "and no surface could take it off. Team assignments joined 2026-08-20 "
        "(gap 7): confirm GET + POST /accounts/{ref}/team/{assignment_id}/"
        "remove, routes/team.py, one batch with the TUI D-flow's own tool and "
        "summary. Tasks (dropped, not deleted, in the TUI) and request items "
        "still have no web removal, so the action as a whole stays PENDING — "
        "do not promote this entry until they do."
    ),
    "mark_primary": (
        "rendered as a pending row action on the contacts table "
        "(_contacts_panel.html, aria-disabled) — not wired; the rest of a "
        "contact's fields are now editable (see inline_edit), this one write "
        "specifically is not built yet"
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

# TUI screen name -> what the web covers of it. The dicts above are
# action-granular for the account page; this ledger is screen-granular for
# the screens outside it, so a whole screen cannot be silently missing the
# way an action could.
SCREENS: dict[str, str] = {
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
}
