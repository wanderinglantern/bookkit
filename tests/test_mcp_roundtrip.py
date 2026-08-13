"""One real protocol round-trip: in-memory client <-> MCPServer.

The brief this test was built from named the SDK's earlier
`create_connected_server_and_client_session` helper
(`mcp.shared.memory`). The installed SDK (mcp==2.0.0) replaced that
helper: `mcp.shared.memory` now only exposes the raw
`create_client_server_memory_streams` transport primitive, and the
supported in-memory testing entry point is `mcp.client.Client`, which
takes a `Server`/`MCPServer` instance directly and drives it in-process
(this is the SDK's own documented example — see the `Client` class
docstring in `mcp/client/client.py`). No `pytest.mark.anyio` marker is
needed: pyproject.toml sets `asyncio_mode = "auto"` (pytest-asyncio),
which collects bare `async def test_...` functions without a marker —
the anyio pytest plugin isn't installed and collection succeeds without
it, so no anyio config was added per the brief's fallback clause.
"""

from __future__ import annotations

from bookkit import db
from bookkit.mcpserver import build_server


async def test_list_tools_and_call_over_protocol(tmp_path):
    path = tmp_path / "rt.db"
    db.connect(path).close()
    server = build_server(path)

    from mcp.client import Client

    async with Client(server) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert {"today_brief", "open_items", "task_create",
                "task_complete", "client_create", "enrich_field"} <= names
        result = await client.call_tool("task_create", {"title": "from the wire"})
        assert not result.is_error
