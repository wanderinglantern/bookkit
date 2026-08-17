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
    "export_open_items": "screen-wide export/merge not built on the web yet",
    "import_here": "paste-import is a TUI-only intake flow for now",
    "task_done": "task mutation not built on the web yet",
    "delete_row": "row deletion not built on the web yet",
    "mark_primary": "contact mutation not built on the web yet",
    "paste_items": "paste-import is a TUI-only intake flow for now",
    "undo": "undo is not built on the web yet",
    "show_tab": "slice 1 renders separate routes, not a tabbed shell",
}
