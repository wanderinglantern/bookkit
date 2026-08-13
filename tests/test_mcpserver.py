"""MCP server: tool functions against a real (temp) database. Tools are
tested as plain functions via the registry — the stdio round-trip lives in
test_mcp_roundtrip.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bookkit import db, mcpserver
from bookkit.mcpserver import build_server
from bookkit.repo import interactions, orgs, placements, projects, submissions, tasks


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


def test_open_items_scoped_reuses_export_composition(server_db):
    # seed a client + org task + need (reuse Task 2's seeding style)
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    tasks.create(conn, "Chase loss runs", org_id=org.id)
    project = projects.create_project(conn, org.id, "New warehouse")
    projects.add_need(conn, project.id, "Property", "2026-09-01", status="identified")
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._open_items(ro, client="Acme")
    assert out["account"] == "Acme"
    assert out["sections"][0]["rows"][0]["kind"] in ("Task", "Need", "Submission")


def test_open_items_bookwide_matches_attention_windows(server_db):
    ro = db.connect_readonly(server_db)
    out = mcpserver._open_items(ro, client=None)
    assert set(out) == {"tasks_due", "project_needs", "submissions_past_sla",
                        "onboarding_incomplete"}


def test_open_items_unknown_client_raises_with_hint(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme Corp", kind="client")
    conn.close()
    ro = db.connect_readonly(server_db)
    with pytest.raises(ValueError, match="no client matching"):
        mcpserver._open_items(ro, client="Acmee")


def test_pipeline_status_formats_cents_as_dollars(server_db):
    ro = db.connect_readonly(server_db)
    out = mcpserver._pipeline_status(ro)
    assert len(out["stages"]) == 7  # every STAGES entry, even at zero count
    for stage in out["stages"]:
        assert isinstance(stage["total"], str) and stage["total"].startswith("$")
        assert isinstance(stage["weighted"], str) and stage["weighted"].startswith("$")
    assert "win_rate" in out["conversion"]
    assert out["submissions_past_sla"] == 0


def test_build_server_registers_open_items_and_pipeline_status(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"open_items", "pipeline_status"} <= names


def test_resolve_client_unknown_name_suggests_nearest(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme Corp", kind="client")
    conn.close()
    ro = db.connect_readonly(server_db)
    with pytest.raises(ValueError, match="no client matching"):
        mcpserver._resolve_client(ro, "Acmee")


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


def test_log_activity_appends_interaction_with_provenance(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)
    out = mcpserver._log_activity(rw, "Acme", "spoke to Ann re GL renewal",
                                   follow_up="friday")
    got = interactions.for_org(rw, out["org_id"])
    assert got[0].body == "spoke to Ann re GL renewal"
    events = rw.execute(  # test-only SQL is fine
        "SELECT * FROM event_log WHERE entity_id = ? AND field = 'source'",
        (got[0].id,)).fetchall()
    assert events and events[0]["new_value"] == "mcp"
    assert out["follow_up_task"] is not None


def test_log_activity_without_follow_up_creates_no_task(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)
    out = mcpserver._log_activity(rw, "Acme", "left a voicemail")
    assert out["follow_up_task"] is None


def test_task_create_carries_description_and_category(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)
    out = mcpserver._task_create(
        rw, "Chase loss runs", client="Acme", description="need 5yr history",
        detail="ask broker portal first", category="submissions", due="+1w",
    )
    task = tasks.get(rw, out["task_ref"])
    assert task.description == "need 5yr history"
    assert task.category == "submissions"
    assert task.detail == "ask broker portal first"
    assert task.due_on == out["due"]
    events = rw.execute(
        "SELECT * FROM event_log WHERE entity_id = ? AND field = 'source'",
        (task.id,)).fetchall()
    assert events and events[0]["new_value"] == "mcp"


def test_task_complete_flips_status_with_provenance(server_db):
    conn = db.connect(server_db)
    task = tasks.create(conn, "Chase loss runs")
    conn.close()
    rw = db.connect(server_db)
    out = mcpserver._task_complete(rw, task.id)
    assert out["status"] == "done"
    assert out["completed_at"] is not None
    events = rw.execute(
        "SELECT * FROM event_log WHERE entity_id = ? AND field = 'source'",
        (task.id,)).fetchall()
    assert events and events[0]["new_value"] == "mcp"


def test_task_complete_requires_exact_ref(server_db):
    rw = db.connect(server_db)
    with pytest.raises(KeyError):
        mcpserver._task_complete(rw, "not-a-real-id")


def test_write_tools_never_touch_ro_connection(server_db):
    ro = db.connect_readonly(server_db)
    with pytest.raises(sqlite3.OperationalError):
        mcpserver._task_create(ro, "should fail")


def test_build_server_registers_write_tools(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"log_activity", "task_create", "task_complete"} <= names
