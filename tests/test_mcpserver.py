"""MCP server: tool functions against a real (temp) database. Tools are
tested as plain functions via the registry — the stdio round-trip lives in
test_mcp_roundtrip.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db
from bookkit.mcpserver import build_server


@pytest.fixture
def server_db(tmp_path: Path) -> Path:
    path = tmp_path / "mcp.db"
    db.connect(path).close()
    return path


def test_build_server_registers_tools(server_db):
    server = build_server(server_db)
    # MCPServer (the installed SDK's FastMCP successor) keeps registered
    # tools in its tool manager.
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "today_brief" in names
