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
}

# action name -> why it is not covered yet
PENDING: dict[str, str] = {
    "add_here": "slice 1 has no per-tab add route built yet",
    "edit_here": "slice 1 has no per-tab edit route built yet",
    "new_submission": (
        "a plain DB write (repo/submissions.create, no towerkit involvement) — "
        "not built on the web yet"
    ),
    "renew_placement": "placements tab — later slice, needs towerkit writes (sync.renew)",
    "edit_layer": "placements tab — later slice, needs towerkit writes (sync.update_layer)",
    "add_layer": "placements tab — later slice, needs towerkit writes (sync.add_layer)",
    "open_towerkit": (
        "two flows behind one key. Opening a program in towerkit is a later "
        "slice; on the projects tab this same key runs _need_to_opportunity "
        "instead — a plain projects_repo write turning a need into an "
        "opportunity, with no towerkit involvement."
    ),
    "assign_team": "team assignment editing not built on the web yet",
    "scaffold_tower": (
        "placements tab — later slice, needs towerkit writes (sync.scaffold_program)"
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
    "task_done": "task mutation not built on the web yet",
    "delete_row": "row deletion not built on the web yet",
    "mark_primary": "contact mutation not built on the web yet",
    "paste_items": (
        "deferred by decision, not yet reached: bulk paste-import needs a "
        "browser-side parser design of its own; the TUI flow does not port"
    ),
    "undo": (
        "undo is not built on the web yet — the top-bar 'Undo <last change>' "
        "pill on the account page is display-only (reads repo.batches.recent) "
        "and does not revert anything"
    ),
    # the following two are bound on ListTable/InlineTable, not AccountScreen
    # itself — see _WIDGET_SOURCES in tests/test_web_parity.py, added in fix
    # round 2 after they turned out invisible to the ledger
    "copy_row": (
        "row-to-clipboard shortcut for a terminal; the web has native text "
        "selection/copy, so no dedicated route is planned unless that proves "
        "insufficient"
    ),
    "inline_edit": (
        "the web's primary edit affordance — in-place cell editing lands in a "
        "later task building on this account page; flip to IMPLEMENTED then"
    ),
}
