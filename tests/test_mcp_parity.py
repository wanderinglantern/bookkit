"""Every entity x verb on the MCP surface is implemented or explicitly deferred.

The web has this (tests/test_web_parity.py); MCP did not. Every MCP
registration test in the suite is a subset assertion, so a new tool or a
deleted tool changed no assertion anywhere. This fails in both directions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bookkit.mcpparity import DEFERRED, IMPLEMENTED, NON_ENTITY_TOOLS, VERBS
from bookkit.mcpserver import build_server
from bookkit.repo.base import ENTITY_TABLES


def _cells() -> set[tuple[str, str]]:
    return {(entity, verb) for entity in ENTITY_TABLES for verb in VERBS}


@pytest.fixture
def registered(tmp_path: Path) -> set[str]:
    server = build_server(tmp_path / "parity.db")
    return {tool.name for tool in server._tool_manager.list_tools()}


def test_every_cell_is_implemented_or_explicitly_deferred():
    missing = _cells() - (set(IMPLEMENTED) | set(DEFERRED))
    assert not missing, (
        f"entity x verb cells in neither IMPLEMENTED nor DEFERRED: "
        f"{sorted(missing)} — add each to bookkit/mcpparity.py, with the tool "
        "that covers it or a reason it is not covered"
    )


def test_the_ledger_has_no_stale_cells():
    stale = (set(IMPLEMENTED) | set(DEFERRED)) - _cells()
    assert not stale, (
        f"mcpparity names cells that are not in repo.base.ENTITY_TABLES x "
        f"VERBS: {sorted(stale)}"
    )


def test_a_cell_is_not_both_implemented_and_deferred():
    both = set(IMPLEMENTED) & set(DEFERRED)
    assert not both, sorted(both)


def test_every_tool_the_ledger_names_is_registered(registered):
    named: set[str] = set(NON_ENTITY_TOOLS)
    for tools, _ in IMPLEMENTED.values():
        named |= set(tools)
    gone = named - registered
    assert not gone, (
        f"mcpparity names tools that are not registered: {sorted(gone)} — a "
        "rename or a deletion"
    )


def test_every_registered_tool_appears_in_the_ledger(registered):
    """THE DIRECTION NOTHING CHECKED. A 45th tool used to change no assertion
    in the suite."""
    named: set[str] = set(NON_ENTITY_TOOLS)
    for tools, _ in IMPLEMENTED.values():
        named |= set(tools)
    unaccounted = registered - named
    assert not unaccounted, (
        f"registered MCP tools missing from mcpparity: {sorted(unaccounted)} — "
        "put each in the cell it covers, or in NON_ENTITY_TOOLS"
    )


def test_every_entry_carries_a_reason():
    for cell, (tools, note) in IMPLEMENTED.items():
        assert tools, f"{cell} claims implementation with no tool"
        assert len(note) > 30, f"{cell} has no note"
    for cell, reason in DEFERRED.items():
        assert len(reason) > 30, f"{cell} has no reason"
    for tool, note in NON_ENTITY_TOOLS.items():
        assert len(note) > 30, f"{tool} has no note"


def test_every_tool_has_a_docstring(registered, tmp_path):
    """Zero tests asserted this. A tool with no description is a tool a model
    will not call."""
    server = build_server(tmp_path / "docs.db")
    undocumented = [
        tool.name
        for tool in server._tool_manager.list_tools()
        if not (tool.description or "").strip()
    ]
    assert not undocumented, undocumented


def test_the_gap_is_a_number_you_can_read():
    """Not a threshold to chase — a tripwire on the shape of the ledger, so a
    bulk edit that empties one side is loud."""
    assert len(_cells()) == len(IMPLEMENTED) + len(DEFERRED)
    assert len(IMPLEMENTED) >= 35
    assert len(DEFERRED) >= 20
