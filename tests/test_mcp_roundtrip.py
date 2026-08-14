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
from bookkit.repo import orgs
from bookkit.repo import rfi as rfi_repo


def _payload(result):
    """A tool's return value as the client received it. Asserting on this,
    not on a helper's return, is the whole point of a round-trip test."""
    assert not result.is_error, result.content
    return result.structured_content


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


async def test_rfi_write_tools_are_wired_to_a_writable_connection(tmp_path):
    """The registration test can only prove the NAMES are registered — a write
    tool closed over the read-only connection would pass it and then fail in
    the user's hands with "attempt to write a readonly database". Only a real
    call through the client proves the wiring, so both RFI write tools get
    called here and both are checked against the database afterwards."""
    path = tmp_path / "rfi.db"
    seed = db.connect(path)
    orgs.create(seed, name="Acme", kind="client")
    seed.close()
    server = build_server(path)

    from mcp.client import Client

    async with Client(server) as client:
        created = _payload(await client.call_tool("request_create", {
            "client": "Acme",
            "title": "Sompo questions",
            "items": ["1. loss runs\n2) vehicle schedule"],
        }))
        assert created["item_count"] == 2
        listed = _payload(await client.call_tool(
            "request_items", {"request_ref": created["request_ref"]}))
        item_ref = listed["items"][0]["item_ref"]
        received = _payload(await client.call_tool("request_item_received", {
            "item_ref": item_ref, "response": "attached",
        }))
        assert received["status"] == "received"
        assert (received["open_count"], received["total_count"]) == (1, 2)

    check = db.connect_readonly(path)
    request = rfi_repo.find_request(check, created["request_ref"])
    assert request is not None                       # the write really landed
    items = rfi_repo.items_for_request(check, request.id)
    assert [i.prompt for i in items] == ["loss runs", "vehicle schedule"]
    stored = rfi_repo.get_item(check, item_ref)
    assert (stored.status, stored.response) == ("received", "attached")


async def test_batch_tools_round_trip_over_protocol(tmp_path):
    """log_activity -> list_batches -> revert_batch over real protocol: the
    interaction is gone afterwards. This harness is what caught the sync-tool
    thread-dispatch bug the unit tests could not see."""
    path = tmp_path / "rt.db"
    conn = db.connect(path)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    server = build_server(path)

    from mcp.client import Client

    async with Client(server) as client:
        logged = _payload(await client.call_tool(
            "log_activity", {"client": "Acme", "note": "logged in error"}))
        assert logged["batch"].startswith("MCP-")

        listed = _payload(await client.call_tool("list_batches", {}))
        assert listed["result"][0]["ref"] == logged["batch"]

        reverted = _payload(await client.call_tool(
            "revert_batch", {"ref": logged["batch"]}))
        assert reverted["applied"] is True

    check = db.connect(path)
    from bookkit.repo import interactions
    assert interactions.for_org(check, org.id) == []
    check.close()


async def test_edit_field_compare_and_set_over_protocol(tmp_path):
    """The expecting arg crosses the wire: a stale value refuses with the
    actual named, a fresh one lands — over the real protocol, not the
    registry."""
    path = tmp_path / "rt.db"
    conn = db.connect(path)
    org = orgs.create(conn, name="Acme", kind="client",
                      website="https://old.example")
    conn.close()
    server = build_server(path)

    from mcp.client import Client

    async with Client(server) as client:
        stale = await client.call_tool("edit_field", {
            "kind": "org", "ref": "Acme", "field": "website",
            "value": "https://new.example", "expecting": "https://wrong.example",
        })
        assert stale.is_error
        assert "https://old.example" in str(stale.content)

        fresh = _payload(await client.call_tool("edit_field", {
            "kind": "org", "ref": "Acme", "field": "website",
            "value": "https://new.example", "expecting": "https://old.example",
        }))
        assert fresh["edited"] is True

    check = db.connect(path)
    assert orgs.get(check, org.id).website == "https://new.example"
    check.close()
