"""MCP server: tool functions against a real (temp) database. Tools are
tested as plain functions via the registry — the stdio round-trip lives in
test_mcp_roundtrip.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db, mcpserver
from bookkit.mcpserver import build_server
from bookkit.repo import orgs, placements, submissions, tasks


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
    assert {
        "today_brief", "renewals_due", "search", "list_programs",
        "program_summary", "staleness_report",
    } <= names


def test_renewals_due_names_lines_of_cover(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    placements.create(
        conn, org_id=org.id, program_name="Acme Property 26-27",
        period_from="2026-01-01", period_to="2026-10-01", status="bound",
    )
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._renewals_due(ro, days=120)
    assert out[0]["account"] == "Acme"
    assert "lines_of_cover" in out[0]


def test_search_finds_org_by_name(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Atomic Industries", kind="client")
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._search(ro, "Atomic")
    assert out
    assert out[0]["kind"] == "org"
    assert "Atomic" in out[0]["title"]


def test_list_programs_shape(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    placement = placements.create(
        conn, org_id=org.id, program_name="Acme Property 26-27",
        period_from="2026-01-01", period_to="2026-10-01", status="bound",
    )
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._list_programs(ro)
    assert out == [
        {"ref": placement.ref, "account": "Acme", "program": "Acme Property 26-27",
         "period_to": "2026-10-01", "status": "bound"}
    ]


def test_program_summary_by_ref(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    placement = placements.create(
        conn, org_id=org.id, program_name="Acme Property 26-27",
        period_from="2026-01-01", period_to="2026-10-01", status="bound",
        total_premium=250_000_00,
    )
    tasks.create(conn, "Chase loss runs", org_id=org.id, placement_id=placement.id)
    market = orgs.create(conn, name="Zurich", kind="market")
    submissions.create(
        conn, market_org_id=market.id, sent_on="2026-01-15", placement_id=placement.id,
    )
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._program_summary(ro, placement.ref)
    assert out["account"] == "Acme"
    assert out["program"] == "Acme Property 26-27"
    assert out["ref"] == placement.ref
    assert out["premium"] == "$250,000"
    assert out["lines_of_cover"] == ""
    assert out["open_tasks"] == 1
    assert out["outstanding_submissions"] == 1


def test_program_summary_counts_placement_task_with_null_org_id(server_db):
    """A placement-attached task can legally carry org_id NULL (see tasks.py
    module docstring) — program_summary must still count it via the
    placement-aware join, not drop it because it filtered on org_id alone."""
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    placement = placements.create(
        conn, org_id=org.id, program_name="Acme Property 26-27",
        period_from="2026-01-01", period_to="2026-10-01", status="bound",
    )
    tasks.create(conn, "Chase loss runs", placement_id=placement.id)
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._program_summary(ro, placement.ref)
    assert out["open_tasks"] == 1


def test_program_summary_by_name(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    placements.create(
        conn, org_id=org.id, program_name="Acme Property 26-27",
        period_from="2026-01-01", period_to="2026-10-01", status="bound",
    )
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._program_summary(ro, "Acme Property 26-27")
    assert out["account"] == "Acme"


def test_program_summary_unknown_ref_suggests(server_db):
    ro = db.connect_readonly(server_db)
    with pytest.raises(ValueError, match="no program matching"):
        mcpserver._program_summary(ro, "PLC-9999")


def test_staleness_report_shape(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client", status="active")
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._staleness_report(ro)
    assert out
    assert out[0]["account"] == "Acme"
    assert out[0]["last_touch"] is None
    assert out[0]["days_stale"] > 60
