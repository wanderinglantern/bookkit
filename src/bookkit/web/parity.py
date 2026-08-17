"""What the web surface covers of the TUI's account actions, and what it does
not yet.

The destination is 1:1. Narrowing early slices is build order, not scope, and
this ledger is what stops the two from being confused: tests/test_web_parity.py
fails on any AccountScreen action that is in neither dict, so a new TUI feature
turns the suite red until its web equivalent is built or consciously deferred.

Keys are TUI action names (Binding.action, with any argument stripped)."""

from __future__ import annotations

# action name -> the web route that covers it
IMPLEMENTED: dict[str, str] = {}

# action name -> why it is not covered yet
PENDING: dict[str, str] = {
    "add_here": "slice 1 has no per-tab add route built yet",
    "edit_here": "slice 1 has no per-tab edit route built yet",
    "new_submission": "placements tab — later slice, needs towerkit writes",
    "renew_placement": "placements tab — later slice, needs towerkit writes",
    "edit_layer": "placements tab — later slice, needs towerkit writes",
    "add_layer": "placements tab — later slice, needs towerkit writes",
    "open_towerkit": "placements tab — later slice, needs towerkit writes",
    "assign_team": "team assignment editing not built on the web yet",
    "scaffold_tower": "placements tab — later slice, needs towerkit writes",
    "export_open_items": (
        "deferred by decision, not yet reached: an XLSX export needs a "
        "file-download response, a mechanism the web spec does not cover — "
        "see docs/superpowers/specs/2026-08-17-web-frontend-design.md"
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
    "undo": "undo is not built on the web yet",
    "show_tab": (
        "the web equivalent is a tab link per route, built with the account "
        "page — flip this to IMPLEMENTED when those land"
    ),
}
