"""MCP server: tool functions against a real (temp) database. Tools are
tested as plain functions via the registry — the stdio round-trip lives in
test_mcp_roundtrip.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bookkit import db, mcpserver
from bookkit.mcpserver import build_server
from bookkit.repo import contacts, interactions, orgs, placements, projects, submissions, tasks
from bookkit.repo import tasks as tasks_repo


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


def test_open_items_bookwide_includes_undated_and_future_due_tasks(server_db):
    """Book-wide open_items is the full open-task list, per spec — unlike
    today_brief's due-by-today slice, it must not drop undated or far-future
    tasks (repo/tasks.py's open_tasks(due_by=...) filter excludes both)."""
    from datetime import date, timedelta

    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    undated = tasks.create(conn, "Undated task", org_id=org.id)
    far_future_due = (date.today() + timedelta(days=200)).isoformat()
    future = tasks.create(conn, "Far-future task", org_id=org.id, due_on=far_future_due)
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._open_items(ro, client=None)
    refs = {t["ref"] for t in out["tasks_due"]}
    assert undated.id in refs
    assert future.id in refs
    # repo ordering (due_on IS NULL, due_on, priority) already puts the
    # undated task last — no additional sort needed here
    assert out["tasks_due"][-1]["ref"] == undated.id


def test_open_items_scoped_rows_carry_refs(server_db):
    """Per-client open_items rows must carry the exact ref task_complete
    requires — export_open_items.ExportRow has no id of its own otherwise."""
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    task = tasks.create(conn, "Chase loss runs", org_id=org.id)
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._open_items(ro, client="Acme")
    row = out["sections"][0]["rows"][0]
    assert row["ref"] == task.id


def test_task_complete_works_on_ref_harvested_from_scoped_open_items(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    tasks.create(conn, "Chase loss runs", org_id=org.id)
    conn.close()
    ro = db.connect_readonly(server_db)
    ref = mcpserver._open_items(ro, client="Acme")["sections"][0]["rows"][0]["ref"]
    rw = db.connect(server_db)
    out = mcpserver._task_complete(rw, ref)
    assert out["status"] == "done"


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


def test_client_create_refuses_near_duplicate(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Henderson Group", kind="client")
    with pytest.raises(ValueError, match="Henderson Group"):
        mcpserver._client_create(rw, "Henderson Grp")


def test_client_create_bundles_contacts_and_tasks(server_db):
    rw = db.connect(server_db)
    out = mcpserver._client_create(
        rw, "Fresh Co",
        contacts_in=[{"first_name": "Ann", "last_name": "Lee", "email": "a@fresh.co"}],
        note="met at RIMS", tasks_in=[{"title": "send intro deck", "due": "9/1"}],
    )
    org = orgs.find(rw, out["org_ref"])
    assert contacts.for_org(rw, org.id)[0].email == "a@fresh.co"
    assert tasks_repo.open_tasks(rw, org_id=org.id)[0].title == "send intro deck"


def test_client_create_bundle_is_atomic_on_bad_task_date(server_db):
    """A bad task date must roll back the WHOLE bundle, including the org
    itself — not leave an org created with a broken/missing task."""
    rw = db.connect(server_db)
    with pytest.raises(ValueError, match="cannot read a date"):
        mcpserver._client_create(
            rw, "Rollback Co", tasks_in=[{"title": "x", "due": "not a real date"}],
        )
    assert orgs.find_by_name(rw, "Rollback Co") is None


def test_client_create_logs_provenance_on_org(server_db):
    rw = db.connect(server_db)
    out = mcpserver._client_create(rw, "Plain Co")
    org = orgs.find(rw, out["org_ref"])
    events = rw.execute(
        "SELECT * FROM event_log WHERE entity_id = ? AND field = 'source'",
        (org.id,)).fetchall()
    assert events and events[0]["new_value"] == "mcp"


def test_enrich_field_fills_blank_but_never_overwrites(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Acme", kind="client")
    out = mcpserver._enrich_field(rw, "Acme", "industry", "construction")
    assert out["set"] is True
    with pytest.raises(ValueError, match="already has"):
        mcpserver._enrich_field(rw, "Acme", "industry", "manufacturing")


def test_enrich_field_rejects_unknown_field(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Acme", kind="client")
    with pytest.raises(ValueError, match="not enrichable"):
        mcpserver._enrich_field(rw, "Acme", "status", "active")


def test_enrich_field_normalizes_email_via_shared_cleaner(server_db):
    rw = db.connect(server_db)
    org = orgs.create(rw, name="Acme", kind="client")
    contacts.create(rw, org.id, first_name="Ann", last_name="Lee")
    out = mcpserver._enrich_field(
        rw, "Acme", "email", " Ann.Lee@EXAMPLE.com ", contact="Ann Lee")
    assert out["value"] == "Ann.Lee@example.com"  # clean_email lowercases the domain only
    got = contacts.for_org(rw, org.id)[0]
    assert got.email == "Ann.Lee@example.com"


def test_enrich_field_on_contact_unknown_name_raises_with_hint(server_db):
    rw = db.connect(server_db)
    org = orgs.create(rw, name="Acme", kind="client")
    contacts.create(rw, org.id, first_name="Ann", last_name="Lee")
    with pytest.raises(ValueError, match="no contact"):
        mcpserver._enrich_field(rw, "Acme", "email", "x@y.com", contact="Bob Nobody")


def test_enrich_field_logs_provenance(server_db):
    rw = db.connect(server_db)
    org = orgs.create(rw, name="Acme", kind="client")
    mcpserver._enrich_field(rw, "Acme", "industry", "construction")
    events = rw.execute(
        "SELECT * FROM event_log WHERE entity_id = ? AND field = 'source'",
        (org.id,)).fetchall()
    assert events and events[0]["new_value"] == "mcp"


def test_build_server_registers_client_create_and_enrich_field(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"client_create", "enrich_field"} <= names
