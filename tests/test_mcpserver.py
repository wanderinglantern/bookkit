"""MCP server: tool functions against a real (temp) database. Tools are
tested as plain functions via the registry — the stdio round-trip lives in
test_mcp_roundtrip.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bookkit import db, mcpserver
from bookkit.mcpserver import build_server
from bookkit.repo import batches as batches_repo
from bookkit.repo import (
    contacts,
    interactions,
    orgs,
    placements,
    projects,
    rfi,
    submissions,
    tasks,
)
from bookkit.repo import tasks as tasks_repo
from bookkit.services import batches as batches_svc


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


def test_build_server_creates_the_database_when_it_is_missing(tmp_path: Path):
    """A first run points at a path nothing has created yet. The read-only
    connection cannot create a file, so the read-write connect (which does,
    and migrates) has to come first — otherwise the server dies at startup
    and the client sees NO tools, which reads as 'the tool doesn't exist'."""
    path = tmp_path / "not-yet.db"
    assert not path.exists()

    server = build_server(path)

    assert path.exists()
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"today_brief", "team_assign", "request_create"} <= names


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
                        "onboarding_incomplete", "information_requests"}


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


def test_open_items_scoped_still_shows_internal_tasks_flagged(server_db):
    """Grant's own assistant reading Grant's own book: per-client open_items
    passes include_internal=True, so an Internal task is VISIBLE and carries
    internal: true. Flipping that default is a deliberate act — hiding a task
    from himself is the silent failure this feature exists to prevent."""
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    internal = tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    normal = tasks.create(conn, "Chase loss runs", org_id=org.id, category="Renewal")
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._open_items(ro, client="Acme")
    rows = {r["ref"]: r for s in out["sections"] for r in s["rows"]}
    assert internal.id in rows, "the Internal task vanished from the assistant's view"
    assert rows[internal.id]["internal"] is True
    assert rows[normal.id]["internal"] is False


def test_open_items_bookwide_flags_internal_tasks(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    internal = tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    normal = tasks.create(conn, "Chase loss runs", org_id=org.id)
    conn.close()
    ro = db.connect_readonly(server_db)
    rows = {t["ref"]: t for t in mcpserver._open_items(ro, client=None)["tasks_due"]}
    assert rows[internal.id]["internal"] is True
    assert rows[normal.id]["internal"] is False


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


# -- opportunities are nameable from a read -----------------------------------


def _two_client_opps(server_db):
    """Two clients, one open deal each, plus a closed one — the shape that
    tells a scoped list from a book-wide one."""
    from bookkit.repo import base, opportunities

    conn = db.connect(server_db)
    acme = orgs.create(conn, name="Acme Manufacturing", kind="client")
    other = orgs.create(conn, name="Borealis Foods", kind="client")
    opportunities.create(conn, acme.id, "Acme cyber renewal", lines="cyber",
                         target_premium=150_000_00)
    opportunities.create(conn, other.id, "Borealis property")
    dead = opportunities.create(conn, acme.id, "Acme drone program")
    base.update(conn, "opportunity", dead.id, {"stage": "lost"})
    conn.close()
    return db.connect_readonly(server_db)


def test_opportunities_names_a_deal_the_assistant_did_not_create(server_db):
    """The whole point: one call from "the Acme cyber deal" to an OPP- ref
    that opportunity_stage will take. Before this tool the only OPP- ref in
    any return value came from opportunity_create/opportunity_stage, so a
    fresh session could not name a single existing deal."""
    ro = _two_client_opps(server_db)

    out = mcpserver._opportunities(ro)

    row = next(r for r in out if r["title"] == "Acme cyber renewal")
    assert row["opportunity_ref"].startswith("OPP-")
    assert row["account"] == "Acme Manufacturing"   # names its account
    assert row["stage"] == "identified"
    assert row["target_premium"].startswith("$")    # cents never leave raw


def test_opportunities_excludes_closed_deals_unless_asked(server_db):
    ro = _two_client_opps(server_db)

    titles = {r["title"] for r in mcpserver._opportunities(ro)}
    assert "Acme drone program" not in titles

    with_closed = {r["title"] for r in mcpserver._opportunities(ro, include_closed=True)}
    assert "Acme drone program" in with_closed


def test_opportunities_scopes_to_one_client(server_db):
    ro = _two_client_opps(server_db)

    titles = {r["title"] for r in
              mcpserver._opportunities(ro, client="Acme Manufacturing")}
    assert titles == {"Acme cyber renewal"}


def test_opportunities_unknown_client_names_the_nearest(server_db):
    """Same refusal every client-scoped tool gives — never a bare KeyError."""
    ro = _two_client_opps(server_db)
    with pytest.raises(ValueError, match="no client matching"):
        mcpserver._opportunities(ro, client="Acmee")


def test_opportunities_is_registered_as_a_read_tool(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "opportunities" in names


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
    """Exact refs only — and the refusal names where to get one. It used to
    be a bare repo KeyError ("task TSK-9999 not found"), which named the
    failure and no recovery."""
    rw = db.connect(server_db)
    with pytest.raises(ValueError, match="no task 'not-a-real-id' — read open_items"):
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
    with pytest.raises(ValueError, match="is not a date"):
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
    """`kind` is a real org column that the denylist keeps off the derived
    surface (mcpsurface.DENIED), so this also guards the denylist. It used to
    read `status`, which is now derived-and-editable — and enrich still
    refuses it, just for the other reason (a status is never blank)."""
    rw = db.connect(server_db)
    orgs.create(rw, name="Acme", kind="client")
    with pytest.raises(ValueError, match="not enrichable"):
        mcpserver._enrich_field(rw, "Acme", "kind", "market")
    with pytest.raises(ValueError, match="not enrichable"):
        mcpserver._enrich_field(rw, "Acme", "revenue", "lots")


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


# --- information requests (RFI) ---------------------------------------------


def _iso(days: int) -> str:
    from datetime import date, timedelta

    return (date.today() + timedelta(days=days)).isoformat()


def _seed_request(conn, account="Acme", title="Sompo questions", market=None, **fields):
    """A client + one request; returns (org, request)."""
    org = orgs.find_by_name(conn, account) or orgs.create(conn, name=account, kind="client")
    if market is not None:
        market_org = (orgs.find_by_name(conn, market)
                      or orgs.create(conn, name=market, kind="market"))
        fields.setdefault("market_org_id", market_org.id)
    request = rfi.create_request(
        conn, org.id, title, _iso(-3), **fields)
    return org, request


def test_requests_to_chase_is_one_row_per_request_with_counts(server_db):
    conn = db.connect(server_db)
    org, request = _seed_request(conn, market="Sompo", due_on=_iso(10))
    done = rfi.add_item(conn, request.id, "loss runs")
    rfi.add_item(conn, request.id, "vehicle schedule")
    rfi.add_item(conn, request.id, "payroll")
    rfi.update_item(conn, done.id, status="received", received_on=_iso(-1))
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._requests_to_chase(ro, days=120)
    assert len(out) == 1                       # one entry per REQUEST, not per item
    assert out[0]["request_ref"] == request.ref
    assert out[0]["account"] == "Acme"
    assert out[0]["title"] == "Sompo questions"
    assert out[0]["asked_by"] == "Sompo"
    assert out[0]["scope"] == "—"              # account-level ask
    assert out[0]["needed_by"] == _iso(10)
    assert out[0]["days"] == 10
    assert (out[0]["open_count"], out[0]["total_count"]) == (2, 3)


def test_requests_to_chase_shows_overdue_with_negative_days(server_db):
    conn = db.connect(server_db)
    _, request = _seed_request(conn, due_on=_iso(-40))
    rfi.add_item(conn, request.id, "ancient ask")
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._requests_to_chase(ro, days=120)
    assert [r["request_ref"] for r in out] == [request.ref]
    assert out[0]["days"] == -40


def test_request_items_resolves_ref_case_insensitively(server_db):
    conn = db.connect(server_db)
    _, request = _seed_request(conn, market="Sompo", due_on=_iso(14))
    early = rfi.add_item(conn, request.id, "loss runs", kind="document",
                         due_on=_iso(2), category="Underwriting")
    rfi.add_item(conn, request.id, "how many vehicles?")
    conn.close()
    ro = db.connect_readonly(server_db)
    out = mcpserver._request_items(ro, request.ref.lower())
    assert out["request_ref"] == request.ref
    assert out["account"] == "Acme"
    assert out["asked_by"] == "Sompo"
    assert out["cancelled"] is False
    assert out["due_on"] == _iso(14)
    first = next(i for i in out["items"] if i["item_ref"] == early.id)
    assert first["kind"] == "document"
    assert first["category"] == "Underwriting"
    assert first["needed_by"] == _iso(2)            # the item's own due wins
    assert first["status"] == "outstanding"
    other = next(i for i in out["items"] if i["item_ref"] != early.id)
    assert other["needed_by"] == _iso(14)           # falls back to the request's


def test_request_items_unknown_ref_raises_naming_real_refs(server_db):
    conn = db.connect(server_db)
    _, request = _seed_request(conn)
    conn.close()
    ro = db.connect_readonly(server_db)
    with pytest.raises(ValueError, match="no information request matching") as excinfo:
        mcpserver._request_items(ro, "RFI-9999")
    assert request.ref in str(excinfo.value)        # never guesses; names real ones


def test_open_items_scoped_lists_outstanding_information_requests(server_db):
    conn = db.connect(server_db)
    _, request = _seed_request(conn, market="Sompo", due_on=_iso(9))
    outstanding = rfi.add_item(conn, request.id, "1. loss runs")
    received = rfi.add_item(conn, request.id, "signed application")
    rfi.update_item(conn, received.id, status="received", received_on=_iso(-1))
    conn.close()
    ro = db.connect_readonly(server_db)
    rows = mcpserver._open_items(ro, client="Acme")["information_requests"]
    assert [r["item_ref"] for r in rows] == [outstanding.id]   # received one absent
    assert rows[0]["request_ref"] == request.ref
    assert rows[0]["title"] == "Sompo questions"
    assert rows[0]["kind"] == "question"
    assert rows[0]["needed_by"] == _iso(9)
    assert rows[0]["asked_by"] == "Sompo"
    assert "account" not in rows[0]        # the per-client branch already names it


def test_open_items_bookwide_lists_information_requests_with_account(server_db):
    conn = db.connect(server_db)
    _, request = _seed_request(conn, market="Sompo", due_on=_iso(9))
    outstanding = rfi.add_item(conn, request.id, "loss runs")
    received = rfi.add_item(conn, request.id, "signed application")
    rfi.update_item(conn, received.id, status="received", received_on=_iso(-1))
    conn.close()
    ro = db.connect_readonly(server_db)
    rows = mcpserver._open_items(ro, client=None)["information_requests"]
    assert [r["item_ref"] for r in rows] == [outstanding.id]
    assert rows[0]["account"] == "Acme"
    assert rows[0]["request_ref"] == request.ref
    assert rows[0]["asked_by"] == "Sompo"


def test_open_items_bookwide_is_unwindowed_like_the_per_client_branch(server_db):
    """open_items is the "everything outstanding" tool in BOTH branches: an
    undated ask and one due past the 120-day horizon are still owed. The
    windowed view is requests_to_chase, and it stays windowed."""
    conn = db.connect(server_db)
    _, undated = _seed_request(conn, account="Acme", title="Onboarding asks")
    no_due = rfi.add_item(conn, undated.id, "org chart")
    _, far = _seed_request(conn, account="Beta Co", title="2028 renewal asks")
    far_off = rfi.add_item(conn, far.id, "audited financials", due_on=_iso(900))
    conn.close()
    ro = db.connect_readonly(server_db)
    rows = mcpserver._open_items(ro, client=None)["information_requests"]
    assert {r["item_ref"] for r in rows} == {no_due.id, far_off.id}
    by_ref = {r["item_ref"]: r for r in rows}
    assert by_ref[no_due.id]["account"] == "Acme"
    assert by_ref[no_due.id]["needed_by"] is None
    assert by_ref[far_off.id]["account"] == "Beta Co"
    assert by_ref[far_off.id]["needed_by"] == _iso(900)
    # and summing the per-client branch agrees with the book-wide one
    per_client = (mcpserver._open_items(ro, client="Acme")["information_requests"]
                  + mcpserver._open_items(ro, client="Beta Co")["information_requests"])
    assert {r["item_ref"] for r in per_client} == {r["item_ref"] for r in rows}
    # the chase queue keeps its 120-day window — neither of these is a chase yet
    assert mcpserver._requests_to_chase(ro, days=120) == []


def test_request_item_received_flips_status_and_drops_the_open_count(server_db):
    conn = db.connect(server_db)
    _, request = _seed_request(conn, due_on=_iso(5))
    item = rfi.add_item(conn, request.id, "how many vehicles?")
    rfi.add_item(conn, request.id, "payroll")
    conn.close()
    rw = db.connect(server_db)
    out = mcpserver._request_item_received(rw, item.id, response="41 vehicles")
    assert out["item_ref"] == item.id
    assert out["status"] == "received"
    assert out["received_on"] == _iso(0)
    assert out["response"] == "41 vehicles"
    assert out["request_ref"] == request.ref
    assert (out["open_count"], out["total_count"]) == (1, 2)
    stored = rfi.get_item(rw, item.id)
    assert stored.response == "41 vehicles"


def test_request_item_received_requires_exact_ref(server_db):
    rw = db.connect(server_db)
    with pytest.raises(
        ValueError, match="no request item 'not-a-real-id' — read request_items"
    ):
        mcpserver._request_item_received(rw, "not-a-real-id")


def test_request_create_files_items_in_paste_order_stripped(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Acme", kind="client")
    orgs.create(rw, name="Sompo", kind="market")
    out = mcpserver._request_create(
        rw, "Acme", "Sompo questions",
        ["1. loss runs\n2) vehicle schedule\n- payroll figures\n\n"],
        market="Sompo", due_on="+2w",
    )
    assert out["account"] == "Acme"
    assert out["item_count"] == 3
    request = rfi.find_request(rw, out["request_ref"])
    assert request.requested_on == _iso(0)          # defaults to today
    assert request.due_on == _iso(14)
    assert [i.prompt for i in rfi.items_for_request(rw, request.id)] == [
        "loss runs", "vehicle schedule", "payroll figures",
    ]


def test_request_create_refuses_both_placement_and_project(server_db):
    rw = db.connect(server_db)
    org = orgs.create(rw, name="Acme", kind="client")
    placement = placements.create(
        rw, org_id=org.id, program_name="Acme Property 26-27",
        period_from="2026-01-01", period_to="2026-10-01", status="bound",
    )
    project = projects.create_project(rw, org.id, "New warehouse")
    with pytest.raises(ValueError, match="never both"):
        mcpserver._request_create(
            rw, "Acme", "Scoped", ["loss runs"],
            placement_ref=placement.ref, project_ref=project.ref,
        )
    assert rfi.requests_for_org(rw, org.id) == []


def test_request_create_scopes_to_a_placement_of_that_client(server_db):
    rw = db.connect(server_db)
    org = orgs.create(rw, name="Acme", kind="client")
    placement = placements.create(
        rw, org_id=org.id, program_name="Acme Property 26-27",
        period_from="2026-01-01", period_to="2026-10-01", status="bound",
    )
    out = mcpserver._request_create(
        rw, "Acme", "Property questions", ["loss runs"],
        placement_ref=placement.ref,
    )
    assert mcpserver._request_items(rw, out["request_ref"])["scope"] == placement.ref


def test_request_create_refuses_another_clients_placement(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Acme", kind="client")
    other = orgs.create(rw, name="Beta Co", kind="client")
    placement = placements.create(
        rw, org_id=other.id, program_name="Beta Property 26-27",
        period_from="2026-01-01", period_to="2026-10-01", status="bound",
    )
    with pytest.raises(ValueError, match="no placement"):
        mcpserver._request_create(
            rw, "Acme", "Wrong scope", ["loss runs"], placement_ref=placement.ref,
        )


def test_request_create_scopes_to_a_project_of_that_client(server_db):
    rw = db.connect(server_db)
    org = orgs.create(rw, name="Acme", kind="client")
    project = projects.create_project(rw, org.id, "New warehouse")
    out = mcpserver._request_create(
        rw, "Acme", "Warehouse questions", ["sprinkler report"],
        project_ref=project.ref,
    )
    assert mcpserver._request_items(rw, out["request_ref"])["scope"] == project.name
    request = rfi.find_request(rw, out["request_ref"])
    assert request.project_id == project.id
    assert request.placement_id is None


def test_request_create_refuses_another_clients_project(server_db):
    rw = db.connect(server_db)
    acme = orgs.create(rw, name="Acme", kind="client")
    other = orgs.create(rw, name="Beta Co", kind="client")
    project = projects.create_project(rw, other.id, "Beta warehouse")
    with pytest.raises(ValueError, match="no project"):
        mcpserver._request_create(
            rw, "Acme", "Wrong scope", ["sprinkler report"], project_ref=project.ref,
        )
    assert rfi.requests_for_org(rw, acme.id) == []      # nothing written
    assert rfi.requests_for_org(rw, other.id) == []     # and not on the owner either


def test_request_create_unknown_market_raises_with_candidates(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Acme", kind="client")
    orgs.create(rw, name="Sompo", kind="market")
    with pytest.raises(ValueError, match="no market matching"):
        mcpserver._request_create(
            rw, "Acme", "Questions", ["loss runs"], market="Sompoo Insurance",
        )
    assert rfi.requests_for_org(rw, orgs.find_by_name(rw, "Acme").id) == []


def test_rfi_writes_are_event_logged_with_mcp_provenance(server_db):
    rw = db.connect(server_db)
    orgs.create(rw, name="Acme", kind="client")
    out = mcpserver._request_create(
        rw, "Acme", "Sompo questions", ["loss runs\nvehicle schedule"])
    request = rfi.find_request(rw, out["request_ref"])
    items = rfi.items_for_request(rw, request.id)
    mcpserver._request_item_received(rw, items[0].id, response="attached")

    def stamped(entity_id):
        return [
            r["new_value"] for r in rw.execute(   # test-only SQL is fine
                "SELECT * FROM event_log WHERE entity_id = ? AND field = 'source'",
                (entity_id,)).fetchall()
        ]

    assert stamped(request.id) == ["mcp"]
    assert all(stamped(item.id) for item in items)
    assert stamped(items[0].id) == ["mcp", "mcp"]  # created, then received


def test_build_server_registers_rfi_tools(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"requests_to_chase", "request_items", "request_item_received",
            "request_create"} <= names


# -- undoing a bad activity -------------------------------------------------


def test_recent_activity_returns_refs_the_delete_tool_can_use(server_db):
    """search() deliberately returns no ids, so a model that logged a bad
    activity in an earlier session had no way to name it. This is that way in."""
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    first = interactions.log(conn, org.id, type="note", subject="older",
                             occurred_on="2026-08-01", body="older")
    second = interactions.log(conn, org.id, type="note", subject="newer",
                              occurred_on="2026-08-10", body="newer")
    conn.close()

    rw = db.connect(server_db)
    out = mcpserver._recent_activity(rw, "Acme")
    refs = [row["interaction_ref"] for row in out]
    assert refs == [second.id, first.id]  # newest first, same order as the tab
    assert out[0]["subject"] == "newer"


def test_recent_activity_honours_limit(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    for n in range(5):
        interactions.log(conn, org.id, type="note", subject=f"note {n}",
                         occurred_on=f"2026-08-0{n + 1}", body="x")
    conn.close()
    rw = db.connect(server_db)
    assert len(mcpserver._recent_activity(rw, "Acme", limit=2)) == 2


def test_activity_delete_removes_it_and_stays_undoable(server_db):
    """The correction path for an MCP mistake: soft delete, event-logged, so
    `u` in the TUI puts it back. Nothing is destroyed."""
    from bookkit.services import undo

    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()

    rw = db.connect(server_db)
    logged = mcpserver._log_activity(rw, "Acme", "wrong client, wrong note")
    ref = logged["interaction_ref"]

    out = mcpserver._activity_delete(rw, ref)
    assert out["deleted"] is True
    assert interactions.for_org(rw, org.id) == []

    # `u` is the TUI's undo and is scoped to source='tui' (Grant 2026-08-15),
    # so the correction path for an MCP mistake is R / revert_batch, not u.
    assert undo.undo_last(rw) is None
    batch = batches_repo.last_undoable(rw, source="mcp")
    assert batch is not None
    batches_svc.revert(rw, batch.ref, now="2026-08-14T09:00:00+00:00")
    assert [i.id for i in interactions.for_org(rw, org.id)] == [ref]


def test_activity_delete_refuses_an_unknown_ref(server_db):
    """base.soft_delete would happily UPDATE zero rows and log an event —
    reporting success for a delete that deleted nothing. Guarded."""
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)
    with pytest.raises(ValueError) as err:
        mcpserver._activity_delete(rw, "not-a-real-id")
    # and it says where a real ref comes from — this was the fifth bare
    # KeyError, on the path log_activity names as the ONLY way to correct a
    # mis-logged interaction, so a raw `interaction <id> not found` left a
    # model sent here with no next step
    assert "recent_activity" in str(err.value)


def test_activity_delete_refuses_to_delete_twice(server_db):
    """The second call must not read as success — the row is already gone."""
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)
    ref = mcpserver._log_activity(rw, "Acme", "oops")["interaction_ref"]
    mcpserver._activity_delete(rw, ref)
    with pytest.raises(ValueError) as err:
        mcpserver._activity_delete(rw, ref)
    assert "recent_activity" in str(err.value)


def test_activity_delete_is_registered_as_a_write_tool(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"recent_activity", "activity_delete"} <= names


def test_undo_after_an_mcp_write_reverts_the_write_not_the_provenance(server_db):
    """Regression, twice over. The MCP server stamps a 'source' provenance
    event after every write; treating it as undoable made `u` raise
    IndexError straight after any MCP write. base._assert_known_field now
    refuses such a field at write time, and `u` no longer reaches MCP writes
    at all — it is scoped to source='tui'. Reverting the batch is what undoes
    an assistant write, and it must revert the WRITE, not the provenance."""
    from bookkit.services import undo

    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()

    rw = db.connect(server_db)
    mcpserver._enrich_field(rw, "Acme", "website", "https://acme.example")
    assert orgs.get(rw, org.id).website == "https://acme.example"

    assert undo.undo_last(rw) is None            # not this app's write

    batch = batches_repo.last_undoable(rw, source="mcp")
    assert batch is not None
    result = batches_svc.revert(rw, batch.ref, now="2026-08-14T09:00:00+00:00")
    assert result.applied
    assert orgs.get(rw, org.id).website is None  # the write, not the provenance


# -- batching: one call, one undo unit ----------------------------------------


# The roster below is what makes this section's name true. It used to check
# TWO tools while claiming to check every one of them, which is worse than no
# test: an auditor asking "is every write batched?" got a green tick from an
# assertion that had never seen nine of them. The roster is derived from
# _register_write_tools itself, so an eleventh write tool fails
# test_the_write_tool_roster_is_accounted_for on the commit that adds it, not
# whenever someone next reads this file.


def _registered_write_tools(tmp_path) -> set[str]:
    """Every tool _register_write_tools registers — read off the registrar, so
    the list cannot go stale."""
    from mcp.server.mcpserver import MCPServer

    probe = MCPServer("roster-probe")
    mcpserver._register_write_tools(probe, db.connect(tmp_path / "roster.db"))
    return {t.name for t in probe._tool_manager.list_tools()}


# Registered on the rw connection but read-only: they need the writable
# connection for nothing but proximity to the verbs they serve refs to.
_NON_MUTATING = {"recent_activity", "program_layers", "list_batches"}

# The two reverts, deliberately unbatched: a revert's own writes carry
# note='revert' and NO batch_id, so a revert cannot itself be batch-reverted
# (services/batches.py:326). program_revert_file DOES return a "batch" key,
# but it is the ref of the batch being PUT BACK — asserting on it would be
# the test agreeing with itself.
_UNBATCHED_BY_DESIGN = {"revert_batch", "program_revert_file"}


def _acme(rw):
    return orgs.create(rw, name="Acme", kind="client")


def _a_task(rw):
    org = _acme(rw)
    return org, tasks_repo.create(rw, "chase the quote", org_id=org.id)


def _a_request_item(rw):
    _acme(rw)
    out = mcpserver._request_create(rw, "Acme", "Sompo questions", ["loss runs"])
    items = mcpserver._request_items(rw, out["request_ref"])
    return items["items"][0]["item_ref"]


def _an_assignment(rw):
    _acme(rw)
    mcpserver._member_create(rw, "Dana Okafor")
    return mcpserver._team_assign(rw, "Dana Okafor", client="Acme")


def _a_marketed_placement(rw):
    """A client with a placement and a market on the book — what the four
    marketing writes need. No towerkit file: a program nobody has drawn yet is
    still a placement being marketed."""
    org = _acme(rw)
    placement = placements.create(
        rw, org_id=org.id, program_name="2027 casualty",
        period_from="2027-01-01", period_to="2028-01-01",
    )
    orgs.create(rw, kind="market", name="Travelers", status="active")
    return placement


def _an_approach(rw):
    # A SEND DATE IN THE PAST, explicitly. Left to default it takes TODAY, and
    # the two cases that record a reply against it then hit
    # repo.marketing._reply_guard — a market cannot answer a package it has
    # not been sent, and every plausible reply date is before today.
    return mcpserver._market_approach(
        rw, _a_marketed_placement(rw).ref, "General Liability",
        market="Travelers", sent_on="2026-07-07",
    )


def _a_bare_package(rw) -> str:
    """A submission with NO response rows — the state `market_assign_line` is
    the only verb for. Written through the repo rather than through a tool,
    because no MCP tool creates one and that is deliberate: the web control
    that did was removed on 2026-08-26, and what remains of this state on a
    real book is history plus the retired TUI's `s`."""
    placement = _a_marketed_placement(rw)
    market = orgs.find_by_name(rw, "Travelers")
    return submissions.create(
        rw, market_org_id=market.id, sent_on="2026-07-07", placement_id=placement.id,
    ).id


def _linked_placement(rw, tmp_path):
    """A placement backed by a real towerkit program file — what the four
    program_* writes need. Same shape as tests/test_mcp_program.py's fixture."""
    from test_linking_flow import make_program, write_program

    from bookkit import sync

    org = orgs.create(rw, kind="client", name="Test Client, Inc.", status="active")
    path = write_program(
        tmp_path / "p" / "test.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01", tbd_line=True),
    )
    assert sync.confirm_link(rw, path, org.id).ok
    return placements.by_program_path(rw, str(path))


# tool name -> a call that must come back with a fresh batch ref. Each builds
# its own prerequisites: one fresh database per case, so order is not a
# hidden input.
_BATCHED_WRITES = {
    "log_activity": lambda rw, tmp: (
        _acme(rw), mcpserver._log_activity(rw, "Acme", "a note"))[1],
    "activity_delete": lambda rw, tmp: (
        _acme(rw),
        mcpserver._activity_delete(
            rw, mcpserver._log_activity(rw, "Acme", "a note")["interaction_ref"]),
    )[1],
    "task_create": lambda rw, tmp: (
        _acme(rw), mcpserver._task_create(rw, "chase the quote", client="Acme"))[1],
    "task_complete": lambda rw, tmp: mcpserver._task_complete(
        rw, _a_task(rw)[1].id),
    "task_assign": lambda rw, tmp: (
        _acme(rw),
        mcpserver._member_create(rw, "Dana Okafor"),
        mcpserver._task_assign(
            rw,
            mcpserver._task_create(rw, "chase the quote", client="Acme")["task_ref"],
            "Dana Okafor",
        ),
    )[2],
    "task_reopen": lambda rw, tmp: mcpserver._task_reopen(
        rw, mcpserver._task_complete(rw, _a_task(rw)[1].id)["task_ref"]),
    "client_create": lambda rw, tmp: mcpserver._client_create(
        rw, "Zephyr Logistics"),
    "enrich_field": lambda rw, tmp: (
        _acme(rw),
        mcpserver._enrich_field(rw, "Acme", "website", "https://acme.example"),
    )[1],
    "edit_field": lambda rw, tmp: mcpserver._edit_field(
        rw, "task", _a_task(rw)[1].id, "title",
        value="chase the binder", expecting="chase the quote"),
    "contact_add": lambda rw, tmp: (
        _acme(rw), mcpserver._contact_add(rw, "Acme", "Ann", "Lee"))[1],
    "contact_remove": lambda rw, tmp: (
        _acme(rw),
        mcpserver._contact_add(rw, "Acme", "Ann", "Lee"),
        mcpserver._contact_remove(rw, "Acme", "Ann Lee"),
    )[2],
    "opportunity_create": lambda rw, tmp: (
        _acme(rw), mcpserver._opportunity_create(rw, "Acme", "Cyber placement"))[1],
    "opportunity_stage": lambda rw, tmp: (
        _acme(rw),
        mcpserver._opportunity_stage(
            rw,
            mcpserver._opportunity_create(
                rw, "Acme", "Cyber placement")["opportunity_ref"],
            "qualified"),
    )[1],
    "project_create": lambda rw, tmp: (
        _acme(rw), mcpserver._project_create(rw, "Acme", "Warehouse build"))[1],
    "need_add": lambda rw, tmp: (
        _acme(rw),
        mcpserver._need_add(
            rw,
            mcpserver._project_create(
                rw, "Acme", "Warehouse build")["project_ref"],
            "GL", "2026-12-01"),
    )[1],
    "member_create": lambda rw, tmp: mcpserver._member_create(rw, "Dana Okafor"),
    "team_assign": lambda rw, tmp: _an_assignment(rw),
    "team_unassign": lambda rw, tmp: mcpserver._team_unassign(
        rw, _an_assignment(rw)["assignment_id"]),
    "member_deactivate": lambda rw, tmp: (
        mcpserver._member_create(rw, "Dana Okafor"),
        mcpserver._member_deactivate(rw, "Dana Okafor"),
    )[1],
    "member_reactivate": lambda rw, tmp: (
        mcpserver._member_create(rw, "Dana Okafor"),
        mcpserver._member_deactivate(rw, "Dana Okafor"),
        mcpserver._member_reactivate(rw, "Dana Okafor"),
    )[2],
    "request_create": lambda rw, tmp: (
        _acme(rw),
        mcpserver._request_create(rw, "Acme", "Sompo questions", ["loss runs"]),
    )[1],
    "request_item_received": lambda rw, tmp: mcpserver._request_item_received(
        rw, _a_request_item(rw)),
    "request_remove": lambda rw, tmp: (
        _acme(rw),
        mcpserver._request_remove(
            rw,
            mcpserver._request_create(
                rw, "Acme", "filed in error", ["loss runs"]
            )["request_ref"],
        ),
    )[1],
    "request_item_remove": lambda rw, tmp: mcpserver._request_item_remove(
        rw, _a_request_item(rw)),
    "request_item_waive": lambda rw, tmp: mcpserver._request_item_waive(
        rw, _a_request_item(rw)),
    "line_add": lambda rw, tmp: mcpserver._line_add(rw, "Kidnap & Ransom"),
    "market_create": lambda rw, tmp: mcpserver._market_create(
        rw, "Hartwell Mutual"),
    # BOTH FIELDS, not just the rating. market_type is the enum-typed one, and
    # a roster case that leaves it None cannot see an enum leak into a JSON
    # reply — which is exactly what test_every_write_tool_answers_in_json is
    # for, and it passed under the mutation until this set it (2026-08-26).
    "market_edit": lambda rw, tmp: (
        mcpserver._market_create(rw, "Hartwell Mutual"),
        mcpserver._market_edit(
            rw, "Hartwell Mutual", market_type="carrier", am_best_rating="A-"),
    )[1],
    "market_approach": lambda rw, tmp: _an_approach(rw),
    "market_assign_line": lambda rw, tmp: mcpserver._market_assign_line(
        rw, _a_bare_package(rw), "General Liability"),
    "market_responded": lambda rw, tmp: mcpserver._market_responded(
        rw, _an_approach(rw)["response_id"], status="quoted",
        responded_on="2026-07-20", premium="120,000"),
    # QUOTED FIRST, so the removal actually MOVES the package. The roll-up only
    # writes when the derived status changes, and a bare pending approach comes
    # off leaving the submission exactly where it was — which would let this
    # tool pass the batch gate while covering only half of what it does.
    "market_response_remove": lambda rw, tmp: (
        lambda ref: (
            mcpserver._market_responded(
                rw, ref, status="quoted", responded_on="2026-07-20",
                premium="120,000",
            ),
            mcpserver._market_response_remove(rw, ref),
        )[1]
    )(_an_approach(rw)["response_id"]),
    "submission_sent_on": lambda rw, tmp: mcpserver._submission_sent_on(
        rw, _an_approach(rw)["response_id"], "2026-07-01"),
    "submission_withdraw": lambda rw, tmp: mcpserver._submission_withdraw(
        rw, _an_approach(rw)["submission_id"]),
    "submission_reinstate": lambda rw, tmp: (
        lambda sub: (
            mcpserver._submission_withdraw(rw, sub),
            mcpserver._submission_reinstate(rw, sub),
        )[1]
    )(_an_approach(rw)["submission_id"]),
    # THE ANSWER COMES OFF FIRST, because that is the ONLY way to reach this
    # verb: `remove_package` refuses while any response speaks for the package,
    # and driving it any other way would be exercising a path no caller has.
    # The first call is `market_response_remove`'s own batch (covered above);
    # the batch this row checks is the second.
    "submission_remove": lambda rw, tmp: (
        lambda a: (
            mcpserver._market_response_remove(rw, a["response_id"]),
            mcpserver._submission_remove(rw, a["submission_id"]),
        )[1]
    )(_an_approach(rw)),
    "set_placement_line": lambda rw, tmp: mcpserver._set_placement_line(
        rw, _a_marketed_placement(rw).ref, "GL",
        expiring_premium="100,000", rating_basis="gross_sales",
        expected_exposure="48,500,000"),
    "program_layer_add": lambda rw, tmp: mcpserver._program_layer_add(
        rw, _linked_placement(rw, tmp).ref, "Excess GL", line_ids=["gl"],
        attach="2m", limit="5m"),
    "program_bind": lambda rw, tmp: mcpserver._program_bind(
        # primary-cy is the unsigned layer in the fixture program; primary-gl
        # is already 100% Zurich and any share on it over-signs
        rw, _linked_placement(rw, tmp).ref, "primary-cy", "Chubb", "25%"),
    "program_market_premium": lambda rw, tmp: mcpserver._program_market_premium(
        # primary-gl is 100% Zurich in the fixture — a lone seat, so there is
        # nothing to freeze and the layer premium simply becomes the figure.
        rw, _linked_placement(rw, tmp).ref, "primary-gl", "Zurich", "1.5m"),
    "program_layer_edit": lambda rw, tmp: mcpserver._program_layer_edit(
        rw, _linked_placement(rw, tmp).ref, "primary-gl", policy_number="GL-1"),
    "program_edit": lambda rw, tmp: mcpserver._program_edit(
        rw, _linked_placement(rw, tmp).ref, name="Renamed Program"),
}


# tool name -> the entity types its batch MUST contain. The receipt test
# below proves a batch ROW exists; this proves the call's EVENTS were stamped
# with it. Without it `db.current_batch()` could return None for every write —
# every event_log row landing with batch_id NULL, the undo spine entirely
# dead — and all twenty-six receipt cases still passed (2026-08-18).
_TOUCHES = {
    "log_activity": {"interaction"},
    "activity_delete": {"interaction"},
    "task_create": {"task"},
    "task_complete": {"task"},
    "task_assign": {"task"},
    "task_reopen": {"task"},
    "client_create": {"org"},
    "enrich_field": {"org"},
    "edit_field": {"task"},
    "contact_add": {"contact"},
    "contact_remove": {"contact"},
    "opportunity_create": {"opportunity"},
    "opportunity_stage": {"opportunity"},
    "project_create": {"project"},
    "need_add": {"project_need"},
    "member_create": {"team_member"},
    "team_assign": {"team_assignment"},
    "team_unassign": {"team_assignment"},
    "member_deactivate": {"team_member"},
    "member_reactivate": {"team_member"},
    "request_create": {"rfi_request", "rfi_item"},
    "request_item_received": {"rfi_item"},
    "request_remove": {"rfi_request", "rfi_item"},
    "request_item_remove": {"rfi_item"},
    "request_item_waive": {"rfi_item"},
    "line_add": {"line_of_coverage"},
    "market_create": {"org"},
    # THE PROFILE ROW, not the org. market_type and am_best_rating live on
    # `market_profile`, which is the whole reason this is a verb and not two
    # more edit_field columns (mcpsurface.NOT_A_COLUMN says so by name).
    "market_edit": {"market_profile"},
    # the submission is filed by the same call — see mcpparity's submission
    # cells, which say why that is a consequence and not a submission verb
    "market_approach": {"submission", "market_response"},
    # THE RESPONSE ALONE, and the absence of a submission event IS the
    # invariant rather than a gap in the coverage. The roll-up runs inside this
    # batch and recomputes the package from the row it just created — and every
    # status maps to one that derives back to itself, so it computes exactly
    # what the columns already held and `base.update` logs nothing. A
    # submission event appearing here would mean assigning a line silently
    # restated what the Pipeline says about the package.
    "market_assign_line": {"market_response"},
    # the roll-up moves the submission from 'out' to 'quoted' in the same unit
    "market_responded": {"market_response", "submission"},
    # The soft delete stamps `deleted_at` on the response; the roll-up under it
    # writes the submission back down from the rows that are left, which on a
    # package whose only answer just went is a real status change.
    "market_response_remove": {"market_response", "submission"},
    # the package alone: the responses hanging off it are untouched, which is
    # exactly why the reply names the rows it moved rather than rewriting them
    "submission_sent_on": {"submission"},
    # ONE COLUMN ON THE PACKAGE, and the absence of a market_response event is
    # the invariant rather than a coverage gap: what each market said stays
    # exactly where it is when we pull a package, which is what makes
    # withdrawing a decision about the SUBMISSION rather than a summary of
    # anything a market said.
    "submission_withdraw": {"submission"},
    # Likewise on the way back. The status is re-derived from the rows and the
    # five figures beside it are recomputed, all on the submission — the rows
    # themselves never move.
    "submission_reinstate": {"submission"},
    # THE PACKAGE ALONE, and it can only BE alone: the verb refuses while a
    # response row still speaks for it, so a market_response event in this
    # batch would mean the guard had been bypassed.
    "submission_remove": {"submission"},
    "set_placement_line": {"placement_line"},
    "program_layer_add": {"placement"},
    "program_bind": {"placement"},
    "program_market_premium": {"placement"},
    "program_layer_edit": {"placement"},
    "program_edit": {"placement"},
}


def test_the_write_tool_roster_is_accounted_for(tmp_path):
    """Every tool _register_write_tools registers is either exercised below or
    named as a deliberate exception. This is the assertion that makes the next
    test's name true — and the one that fails when a write tool is added with
    no batch-ref coverage."""
    accounted = set(_BATCHED_WRITES) | _NON_MUTATING | _UNBATCHED_BY_DESIGN
    registered = _registered_write_tools(tmp_path)
    assert registered - accounted == set(), "write tool with no batch-ref case"
    assert accounted - registered == set(), "stale entry: no such write tool"
    # a batch ref with no stamped events is a dead undo unit, so the roster
    # gates the spine case too — a new tool cannot be added with only a receipt
    assert set(_TOUCHES) == set(_BATCHED_WRITES), "write tool with no _TOUCHES entry"


@pytest.mark.parametrize("tool", sorted(_BATCHED_WRITES))
def test_every_write_tool_returns_a_batch_ref(tool, server_db, tmp_path):
    """One MCP call is one undo unit, on all twenty-six of them. This checked
    exactly two — _log_activity and _task_create — under this name; the other
    write tools were batched, but nothing held them there."""
    rw = db.connect(server_db)
    out = _BATCHED_WRITES[tool](rw, tmp_path)
    assert isinstance(out, dict), f"{tool} returned no dict"
    assert "batch" in out, f"{tool} returned no batch ref"
    assert out["batch"].startswith("MCP-"), f"{tool} batch ref: {out['batch']!r}"
    # a real row, stamped by this surface, not a string that merely looks right
    batch = batches_repo.get_by_ref(rw, out["batch"])
    assert batch.source == "mcp"
    assert batch.tool == tool


@pytest.mark.parametrize("tool", sorted(_BATCHED_WRITES))
def test_every_write_tool_stamps_its_events_with_that_batch(tool, server_db, tmp_path):
    """The receipt above is not the undo unit — the stamped events are. `u`
    and revert_batch both work off `events_for(batch.id)`; a batch whose events
    carry batch_id NULL reverts NOTHING while reporting a healthy ref.

    Lives beside the receipt test and over the same roster so the roster
    assertion gates both: a write tool added tomorrow cannot reach main with a
    ref and no spine."""
    rw = db.connect(server_db)
    out = _BATCHED_WRITES[tool](rw, tmp_path)
    batch = batches_repo.get_by_ref(rw, out["batch"])

    events = batches_repo.events_for(rw, batch.id)
    assert events, f"{tool}: batch {out['batch']} has no events — nothing to undo"
    assert {e.entity_type for e in events} == _TOUCHES[tool], (
        f"{tool} stamped {sorted({e.entity_type for e in events})}, "
        f"expected {sorted(_TOUCHES[tool])}"
    )


@pytest.mark.parametrize("tool", sorted(_BATCHED_WRITES))
def test_every_write_tool_answers_in_json(tool, server_db, tmp_path):
    """EVERY REPLY CROSSES A JSON BOUNDARY, so every value in one has to be a
    JSON type. A model row handed back whole, a `date`, a `Decimal` or a set
    reaches the transport as a TypeError at serialisation — after the write
    has already landed, which is the worst moment for it.

    Over the SAME roster as the two tests above, so a write tool added
    tomorrow is held to this too.

    WHERE IT CANNOT LOOK: the read tools, which have no roster; `describe`,
    whose reply is assembled from mcpsurface's declarations rather than from a
    row; and — checked by mutation, 2026-08-26 — the enum columns, because
    every enum in models.py is a `StrEnum` and json.dumps writes those as
    their value. A `str(...)`-shaped reply field is therefore a consistency
    rule, not something this test is holding up."""
    rw = db.connect(server_db)
    out = _BATCHED_WRITES[tool](rw, tmp_path)
    json.dumps(out)  # raises TypeError on anything that is not JSON


def test_market_create_is_the_door_client_create_is_not(server_db):
    """Grant hit this in real use (2026-08-26): asked to add a carrier, the
    assistant reached for the only create tool there was and the carrier
    landed on the book as a CLIENT — invisible to every market picker,
    unreachable by market_approach, and a kind that cannot be corrected."""
    rw = db.connect(server_db)
    out = mcpserver._market_create(rw, "Hartwell Mutual", market_type="carrier")

    assert out["market_ref"].startswith("ACC-")
    assert out["market_type"] == "carrier"
    market = orgs.find_market(rw, "Hartwell Mutual")
    assert market is not None
    assert str(getattr(market.kind, "value", market.kind)) == "market"
    # and market_approach can now reach it — the point of the whole thing
    assert mcpserver._resolve_market(rw, "Hartwell Mutual").id == market.id


def test_market_create_refuses_a_near_duplicate_naming_it(server_db):
    """A REFUSAL here where line_add only warns. Two lines of coverage four
    letters apart are routinely different cover; two markets four letters
    apart are 'Zurich' and 'Zurich Insurance Group', and a second row for one
    carrier splits its submissions, appetite and underwriters across records
    no lookup joins."""
    rw = db.connect(server_db)
    mcpserver._market_create(rw, "Kestrel Specialty")

    with pytest.raises(ValueError, match="possible duplicate of market Kestrel"):
        mcpserver._market_create(rw, "Kestrel Speciality")
    with pytest.raises(ValueError, match="already on the book"):
        mcpserver._market_create(rw, "kestrel specialty")


def test_the_market_miss_names_the_tool_that_adds_one(server_db):
    """A REFUSAL NAMES A FIX, and names the RIGHT one: `nearest: none close`
    stated the objection and stopped, which is how the assistant went looking
    for another door and found client_create."""
    rw = db.connect(server_db)
    with pytest.raises(ValueError, match="market_create") as caught:
        mcpserver._resolve_market(rw, "Nowhere Re")
    assert "never client_create" in str(caught.value)


def test_market_edit_reaches_the_two_fields_edit_field_cannot(server_db):
    """market_type and am_best_rating are `market_profile` columns, so
    base.update against `org` cannot reach them — mcpsurface.NOT_A_COLUMN has
    always said so and now names this door."""
    rw = db.connect(server_db)
    mcpserver._market_create(rw, "Kestrel Specialty")

    out = mcpserver._market_edit(
        rw, "Kestrel Specialty", market_type="wholesaler", am_best_rating="A-"
    )
    assert (out["market_type"], out["am_best_rating"]) == ("wholesaler", "A-")
    # an omitted argument LEAVES IT ALONE — a partial update is the common one
    again = mcpserver._market_edit(rw, "Kestrel Specialty", am_best_rating="A")
    assert again["market_type"] == "wholesaler"
    with pytest.raises(ValueError, match="must be one of"):
        mcpserver._market_edit(rw, "Kestrel Specialty", market_type="bank")
    with pytest.raises(ValueError, match="nothing to set"):
        mcpserver._market_edit(rw, "Kestrel Specialty")


def test_what_a_market_is_can_be_taken_back(server_db):
    """A Best rating written by raw SQL appeared in no changes list and `u`
    could not take it back (migration 017). The write-tool spine gate caught
    it as 'a batch with no events'; this is the user-facing half of the same
    fact."""
    rw = db.connect(server_db)
    mcpserver._market_create(rw, "Kestrel Specialty")
    out = mcpserver._market_edit(rw, "Kestrel Specialty", am_best_rating="A-")

    result = batches_svc.revert(rw, out["batch"], now=db.utc_now())
    assert result.reverted, result
    profile = orgs.get_market_profile(rw, orgs.find_market(rw, "Kestrel Specialty").id)
    assert profile is None or profile.am_best_rating is None


def test_markets_list_says_which_orgs_are_markets(server_db):
    """`search` returns kind/title/snippet and does not say whether an org is
    a client or a market — the gap the assistant fell through."""
    rw = db.connect(server_db)
    orgs.create(rw, name="Hartwell Mutual", kind="client")
    mcpserver._market_create(rw, "Hartwell Mutual", am_best_rating="A")

    rows = mcpserver._markets_list(rw, "hartwell")
    assert [r["name"] for r in rows] == ["Hartwell Mutual"]
    assert rows[0]["am_best_rating"] == "A"
    assert rows[0]["status"] == "active"


def test_two_calls_are_two_undo_units(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    logged = mcpserver._log_activity(rw, "Acme", "a note")
    made = mcpserver._task_create(rw, "chase the quote", client="Acme")
    assert made["batch"] != logged["batch"]


def test_one_mcp_call_is_one_batch(server_db):
    """log_activity writes an interaction AND a follow-up task. Both must land
    in the same batch, or reverting it would unwind half."""
    from bookkit.repo import batches as batches_repo

    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._log_activity(rw, "Acme", "spoke to Ann", follow_up="friday")
    batch = batches_repo.get_by_ref(rw, out["batch"])
    touched = {e.entity_type for e in batches_repo.events_for(rw, batch.id)}
    assert touched == {"interaction", "task"}


def test_a_batch_records_the_tool_and_the_account(server_db):
    from bookkit.repo import batches as batches_repo

    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._enrich_field(rw, "Acme", "website", "https://acme.example")
    batch = batches_repo.get_by_ref(rw, out["batch"])
    assert batch.tool == "enrich_field"
    assert batch.org_id == org.id
    assert batch.source == "mcp"


def test_list_batches_shows_recent_work_newest_first(server_db):
    from datetime import date as date_cls

    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    first = mcpserver._log_activity(rw, "Acme", "one")
    second = mcpserver._task_create(rw, "two", client="Acme")

    out = mcpserver._list_batches(rw, today=date_cls.today())
    refs = [row["ref"] for row in out]
    assert refs == [second["batch"], first["batch"]]
    assert out[0]["tool"] == "task_create"
    assert out[0]["reverted"] is False
    assert out[1]["account"] == "Acme"


def test_list_batches_covers_every_surface_not_just_this_server(server_db):
    """The docstring said "changes THIS server made"; repo.batches.recent has
    no source filter and never had one. The tool is MORE capable than it
    advertised, so a model would never have reached for it to answer "what
    changed on this account this week" — the fix is the docstring plus the
    `source` field that lets a caller tell them apart."""
    from datetime import date as date_cls

    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    mine = mcpserver._log_activity(rw, "Acme", "an assistant note")
    with batches_svc.open_batch(
        rw, source="tui", tool="task_form", summary="a TUI edit", org_id=org.id
    ):
        tasks_repo.create(rw, "typed at the keyboard", org_id=org.id)

    out = mcpserver._list_batches(rw, today=date_cls.today())
    by_source = {row["source"] for row in out}
    assert by_source == {"mcp", "tui"}
    assert next(r for r in out if r["ref"] == mine["batch"])["source"] == "mcp"


def test_list_batches_filters_to_one_account(server_db):
    from datetime import date as date_cls

    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    orgs.create(conn, name="Borealis Foods", kind="client")
    conn.close()
    rw = db.connect(server_db)

    acme = mcpserver._log_activity(rw, "Acme", "acme note")
    mcpserver._log_activity(rw, "Borealis Foods", "borealis note")

    out = mcpserver._list_batches(rw, today=date_cls.today(), client="Acme")
    assert [row["ref"] for row in out] == [acme["batch"]]
    assert out[0]["account"] == "Acme"


def test_list_batches_unknown_account_names_the_nearest(server_db):
    from datetime import date as date_cls

    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)
    with pytest.raises(ValueError, match="no client matching"):
        mcpserver._list_batches(rw, today=date_cls.today(), client="Acmee")


def test_list_batches_window_is_a_parameter_defaulting_to_fourteen_days(server_db):
    """Default behaviour is unchanged for existing callers: 14 days, every
    account. `days` widens or narrows it."""
    from datetime import date as date_cls
    from datetime import timedelta

    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._log_activity(rw, "Acme", "a note")
    today = date_cls.today()

    # 20 days on: outside the default window, inside a 30-day one
    later = today + timedelta(days=20)
    assert mcpserver._list_batches(rw, today=later) == []
    refs = [row["ref"] for row in mcpserver._list_batches(rw, today=later, days=30)]
    assert refs == [out["batch"]]

    # and the default really is 14, not "everything"
    edge = today + timedelta(days=13)
    assert [r["ref"] for r in mcpserver._list_batches(rw, today=edge)] == [out["batch"]]


def test_registered_tools_pass_their_arguments_through(server_db):
    """The rest of this module calls the _verb helpers directly (see the
    module docstring), which cannot catch a wrapper that forgets to forward a
    new argument — a mutation that dropped list_batches' `days`/`client` from
    the wrapper survived the whole suite. These drive the REGISTERED closures.
    """
    import asyncio

    from bookkit.repo import opportunities as opportunities_repo

    conn = db.connect(server_db)
    acme = orgs.create(conn, name="Acme", kind="client")
    other = orgs.create(conn, name="Borealis Foods", kind="client")
    opportunities_repo.create(conn, acme.id, "Acme cyber renewal")
    # a second account's deal, so a wrapper that drops `client` and falls
    # back to the book-wide list returns two rows and fails below
    opportunities_repo.create(conn, other.id, "Borealis property")
    conn.close()

    server = build_server(server_db)
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    deals = asyncio.run(tools["opportunities"].fn(client="Acme"))
    assert [d["title"] for d in deals] == ["Acme cyber renewal"]
    assert deals[0]["opportunity_ref"].startswith("OPP-")

    asyncio.run(tools["log_activity"].fn(client="Acme", note="a note"))
    asyncio.run(tools["log_activity"].fn(client="Borealis Foods", note="other"))
    scoped = asyncio.run(tools["list_batches"].fn(days=30, client="Acme"))
    assert [row["account"] for row in scoped] == ["Acme"]
    assert scoped[0]["source"] == "mcp"


def test_revert_batch_puts_the_value_back(server_db):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._enrich_field(rw, "Acme", "website", "https://acme.example")
    got = mcpserver._revert_batch(rw, out["batch"], now="2026-08-13T18:00:00Z")
    assert got["applied"] is True
    assert orgs.get(rw, org.id).website is None


def test_revert_batch_refuses_and_explains_a_conflict(server_db):
    from bookkit.repo import base

    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    out = mcpserver._enrich_field(rw, "Acme", "website", "https://acme.example")
    base.update(rw, "org", org.id, {"website": "https://grant-typed.example"})

    got = mcpserver._revert_batch(rw, out["batch"], now="2026-08-13T18:00:00Z")
    assert got["applied"] is False
    assert got["refused"][0]["field"] == "website"
    assert got["refused"][0]["current"] == "https://grant-typed.example"
    assert orgs.get(rw, org.id).website == "https://grant-typed.example"


def test_revert_batch_names_what_still_hangs_off_a_shared_row(server_db):
    """`market_approach` on a second line JOINS the submission the first one
    opened, so reverting the first would soft-delete a package two live
    responses still hang off. The refusal has to reach the ASSISTANT too, and
    entity/field/current describe it wrongly on their own — they say "the
    submission was created", which is not the blocker. `why` is the planner's
    own sentence, the same one the browser toast prints (2026-08-26)."""
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo

    conn = db.connect(server_db)
    org = orgs_repo.create(conn, name="Acme", kind="client")
    orgs_repo.create(conn, name="Chubb", kind="market")
    placement = placements_repo.create(
        conn, org_id=org.id, program_name="2027 casualty",
        period_from="2027-01-01", period_to="2028-01-01",
    )
    conn.close()
    rw = db.connect(server_db)

    first = mcpserver._market_approach(
        rw, placement.ref, "general liability", market="Chubb",
        sent_on="2026-08-12",
    )
    mcpserver._market_approach(
        rw, placement.ref, "auto", market="Chubb", sent_on="2026-08-12",
    )

    got = mcpserver._revert_batch(rw, first["batch"], now="2026-08-26T18:00:00Z")
    assert got["applied"] is False
    assert [row["why"] for row in got["refused"]] == [
        "submission still has 1 market response(s) recorded against it since "
        "— undo those first"
    ]
    alive = rw.execute(
        "SELECT COUNT(*) FROM market_response WHERE deleted_at IS NULL"
    ).fetchone()[0]
    assert alive == 2


def test_batch_tools_are_registered(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {"list_batches", "revert_batch"} <= names


# -- edit_field: compare-and-set overwrites -----------------------------------


def _rw(server_db, *, client=True):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client") if client else None
    conn.close()
    return db.connect(server_db), org


def test_edit_field_overwrites_when_expecting_matches(server_db):
    rw, org = _rw(server_db)
    mcpserver._enrich_field(rw, "Acme", "website", "https://old.example")

    out = mcpserver._edit_field(
        rw, "org", "Acme", "website",
        value="https://new.example", expecting="https://old.example",
    )
    assert out["batch"].startswith("MCP-")
    assert orgs.get(rw, org.id).website == "https://new.example"

    mcpserver._revert_batch(rw, out["batch"], now="2026-08-14T02:00:00Z")
    assert orgs.get(rw, org.id).website == "https://old.example"


def test_edit_field_refuses_on_stale_expecting_and_writes_nothing(server_db):
    rw, org = _rw(server_db)
    mcpserver._enrich_field(rw, "Acme", "website", "https://real.example")

    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "org", "Acme", "website",
            value="https://new.example", expecting="https://wrong.example",
        )
    assert "https://real.example" in str(err.value)   # names the actual value
    assert orgs.get(rw, org.id).website == "https://real.example"


def test_edit_field_expecting_none_means_blank(server_db):
    rw, org = _rw(server_db)
    # blank field + expecting None == enrich semantics, made explicit
    out = mcpserver._edit_field(
        rw, "org", "Acme", "website",
        value="https://first.example", expecting=None,
    )
    assert out["batch"].startswith("MCP-")
    # non-blank field + expecting None must refuse
    with pytest.raises(ValueError):
        mcpserver._edit_field(
            rw, "org", "Acme", "website",
            value="https://other.example", expecting=None,
        )
    assert orgs.get(rw, org.id).website == "https://first.example"


def test_edit_field_rejects_fields_off_the_allowlist(server_db):
    rw, _ = _rw(server_db)
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(rw, "org", "Acme", "ref",
                              value="ACC-9999", expecting="ACC-0001")
    assert "ref" in str(err.value)


def test_edit_field_contact_requires_client_and_exact_name(server_db):
    from bookkit.repo import contacts as contacts_repo

    rw, org = _rw(server_db)
    ann = contacts_repo.create(rw, org.id, first_name="Ann", last_name="Lee",
                               email="ann@old.example")

    out = mcpserver._edit_field(
        rw, "contact", "Ann Lee", "email",
        value="ann@new.example", expecting="ann@old.example", client="Acme",
    )
    assert out["batch"].startswith("MCP-")
    assert contacts_repo.get(rw, ann.id).email == "ann@new.example"

    with pytest.raises(ValueError):
        mcpserver._edit_field(
            rw, "contact", "Ann Lee", "email",
            value="x@y.example", expecting="ann@new.example",  # no client
        )


def test_edit_field_moves_a_task_due_date(server_db):
    rw, org = _rw(server_db)
    task = tasks.create(rw, "chase quote", org_id=org.id, due_on="2026-08-20")

    out = mcpserver._edit_field(
        rw, "task", task.id, "due_on", value="2026-09-01", expecting="2026-08-20",
    )
    assert out["batch"].startswith("MCP-")
    assert tasks.get(rw, task.id).due_on == "2026-09-01"


def test_edit_field_never_touches_opportunity_stage(server_db):
    from bookkit.repo import opportunities

    rw, org = _rw(server_db)
    opp = opportunities.create(rw, org.id, "Cyber placement")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(rw, "opportunity", opp.ref, "stage",
                              value="qualified", expecting="identified")
    assert "stage" in str(err.value)
    assert opportunities.get(rw, opp.id).stage == "identified"


def test_edit_field_validates_vocab_and_lists_legal_values(server_db):
    from bookkit.repo import projects

    rw, org = _rw(server_db)
    project = projects.create_project(rw, org.id, "HQ Build", status="planned")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(rw, "project", project.ref, "status",
                              value="underway", expecting="planned")
    assert "active" in str(err.value)          # the legal list is in the error
    out = mcpserver._edit_field(rw, "project", project.ref, "status",
                                value="active", expecting="planned")
    assert out["edited"] and projects.get_project(rw, project.id).status == "active"


def test_edit_field_money_compares_in_human_form(server_db):
    from bookkit.repo import opportunities

    rw, org = _rw(server_db)
    opp = opportunities.create(rw, org.id, "Cyber placement",
                               target_premium=120_000_000)  # cents = $1.2m
    out = mcpserver._edit_field(
        rw, "opportunity", opp.ref, "target_premium",
        value="1.5m", expecting="1.2m",
    )
    assert out["value"] == 150_000_000
    assert opportunities.get(rw, opp.id).target_premium == 150_000_000


def test_edit_field_edits_rfi_item_response(server_db):
    rw, org = _rw(server_db)
    req = rfi.create_request(rw, org.id, "Sompo questions", "2026-08-05")
    item = rfi.add_item(rw, req.id, "how many vehicles?")
    out = mcpserver._edit_field(rw, "rfi_item", item.id, "response",
                                value="42 vehicles", expecting=None)
    assert out["edited"]
    assert rfi.get_item(rw, item.id).response == "42 vehicles"


# -- edit_field/enrich_field: kind is per-(entity, field), never per-name ----
#
# `description` means two different things: task.description is a one-line
# summary (forms/entities.py:185); project.description is the textarea
# (:490). A field NAME is not globally 1:1 with a kind, so these go through
# the real edit_field/enrich_field entry points on a seeded connection —
# never by poking a cleaner helper with a bare field name, which is exactly
# the assumption that let the description bug through undetected.


def test_task_description_is_a_one_line_summary_and_is_collapsed(server_db):
    """task.description is plain text (forms/entities.py:185, 'one-line summary');
    project.description is a textarea (:490). The same NAME, two kinds — which is
    why the kind is declared per entity and never looked up by name alone."""
    rw, org = _rw(server_db)
    task = tasks.create(rw, "chase quote", org_id=org.id)
    out = mcpserver._edit_field(
        rw, "task", task.id, "description",
        value="  called twice   this week  ", expecting=None,
    )
    assert out["value"] == "called twice this week"
    assert tasks.get(rw, task.id).description == "called twice this week"


def test_project_description_is_stored_verbatim(server_db):
    """project.description is forms/entities.py's textarea (:490) — the
    opposite of task.description above, on the same field name."""
    rw, org = _rw(server_db)
    project = projects.create_project(rw, org.id, "HQ Build")
    prose = "site visit Monday\n\n- confirm access\n- bring hard hats"
    out = mcpserver._edit_field(
        rw, "project", project.ref, "description", value=prose, expecting=None,
    )
    assert out["value"] == prose
    assert projects.get_project(rw, project.id).description == prose


def test_task_detail_is_stored_verbatim(server_db):
    rw, org = _rw(server_db)
    task = tasks.create(rw, "chase quote", org_id=org.id)
    prose = "called Dana\n\n- loss runs promised Friday\n- wants EL quoted separately"
    out = mcpserver._edit_field(
        rw, "task", task.id, "detail", value=prose, expecting=None,
    )
    assert out["value"] == prose
    assert tasks.get(rw, task.id).detail == prose


def test_rfi_item_response_is_stored_verbatim(server_db):
    """The existing rfi_item.response test above only ever sent a single
    word, which clean_text would return unchanged too — vacuous. Multi-line
    input is what actually discriminates textarea from text."""
    rw, org = _rw(server_db)
    req = rfi.create_request(rw, org.id, "Sompo questions", "2026-08-05")
    item = rfi.add_item(rw, req.id, "how many vehicles?")
    prose = "42 vehicles total:\n- 30 pickups\n- 12 sedans"
    out = mcpserver._edit_field(
        rw, "rfi_item", item.id, "response", value=prose, expecting=None,
    )
    assert out["value"] == prose
    assert rfi.get_item(rw, item.id).response == prose


def test_enrich_field_normalises_mobile_as_a_phone(server_db):
    """mobile is a phone (forms/entities.py:145); the bare field name is not."""
    rw, org = _rw(server_db)
    ann = contacts.create(rw, org.id, first_name="Ann", last_name="Lee")
    out = mcpserver._enrich_field(
        rw, "Acme", "mobile", " 312.555.0142 ", contact="Ann Lee",
    )
    assert out["value"] == "(312) 555-0142"
    assert contacts.get(rw, ann.id).mobile == "(312) 555-0142"


def test_enrich_field_normalises_website_as_a_url(server_db):
    """website is a url (forms/entities.py:86); the bare field name is not."""
    rw, org = _rw(server_db)
    out = mcpserver._enrich_field(rw, "Acme", "website", "company.com")
    assert out["value"] == "https://company.com"
    assert orgs.get(rw, org.id).website == "https://company.com"


def test_edit_field_team_member_by_exact_name(server_db):
    from bookkit.repo import team

    rw, _ = _rw(server_db)
    member = team.create_member(rw, "Dana Cruz", specialty="cyber")
    out = mcpserver._edit_field(rw, "team_member", "Dana Cruz", "specialty",
                                value="cyber, tech E&O", expecting="cyber")
    assert out["edited"]
    assert team.get_member(rw, member.id).specialty == "cyber, tech E&O"


def test_edit_field_renames_a_member_by_their_old_name(server_db):
    from bookkit.repo import team

    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruze")

    out = mcpserver._edit_field(
        rw, "team_member", "Dana Cruze", "name", "Dana Cruz",
        expecting="Dana Cruze",
    )
    assert out["batch"].startswith("MCP-")
    names = [m.name for m in team.list_members(rw, active_only=False)]
    assert names == ["Dana Cruz"]


def test_rename_refuses_a_name_another_member_holds(server_db):
    """Two members sharing a name makes every lookup ambiguous — _find_member
    and _edit_target both take the first match."""
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._member_create(rw, "Sam Okafor")

    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_member", "Sam Okafor", "name", "dana cruz",
            expecting="Sam Okafor",
        )
    assert "Dana Cruz" in str(err.value)


def test_rename_refuses_a_name_an_INACTIVE_member_holds(server_db):
    """Inactive members still resolve in _find_member (active_only=False), so
    they collide just as hard as active ones."""
    from bookkit.repo import base, team

    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._member_create(rw, "Sam Okafor")
    gone = next(m for m in team.list_members(rw, active_only=False)
                if m.name == "Dana Cruz")
    base.update(rw, "team_member", gone.id, {"active": 0})

    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_member", "Sam Okafor", "name", "Dana Cruz",
            expecting="Sam Okafor",
        )
    assert "Dana Cruz" in str(err.value)


def test_renaming_to_the_same_name_is_not_a_self_collision(server_db):
    """The guard must exclude the member being renamed, or a no-op rename
    reports a collision with itself."""
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    out = mcpserver._edit_field(
        rw, "team_member", "Dana Cruz", "name", "Dana  Cruz",
        expecting="Dana Cruz",
    )
    assert out["batch"].startswith("MCP-")


# -- creates ------------------------------------------------------------------


def test_contact_add_links_and_optionally_primaries(server_db):
    from bookkit.repo import contacts as contacts_repo

    rw, org = _rw(server_db)
    out = mcpserver._contact_add(
        rw, "Acme", first_name="Ann", last_name="Lee",
        email="ann@acme.example", make_primary=True,
    )
    assert out["batch"].startswith("MCP-")
    roster = contacts_repo.for_org(rw, org.id)
    assert [c.name for c in roster] == ["Ann Lee"]
    assert roster[0].is_primary
    assert roster[0].email == "ann@acme.example"


def test_contact_add_refuses_exact_duplicate_name(server_db):
    rw, _ = _rw(server_db)
    mcpserver._contact_add(rw, "Acme", first_name="Ann", last_name="Lee")
    with pytest.raises(ValueError) as err:
        mcpserver._contact_add(rw, "Acme", first_name="ann", last_name="lee")
    assert "Ann Lee" in str(err.value)


def test_opportunity_create_dup_guard_fuzzy_against_open_opps(server_db):
    from bookkit.repo import opportunities

    rw, org = _rw(server_db)
    opportunities.create(rw, org.id, "Cyber placement", lines="cyber")
    with pytest.raises(ValueError) as err:
        mcpserver._opportunity_create(rw, "Acme", "Cyber Placement", lines="cyber")
    assert "Cyber placement" in str(err.value)


def test_opportunity_create_closed_opps_do_not_block(server_db):
    from bookkit.repo import base, opportunities

    rw, org = _rw(server_db)
    old = opportunities.create(rw, org.id, "Cyber placement", lines="cyber")
    base.update(rw, "opportunity", old.id, {"stage": "lost"})

    out = mcpserver._opportunity_create(
        rw, "Acme", "Cyber placement", lines="cyber",
        target_premium="1.2m", target_effective="2027-01-01",
    )
    assert out["batch"].startswith("MCP-")
    made = opportunities.find(rw, out["opportunity_ref"])
    assert made.target_premium == 120_000_000
    assert made.target_effective == "2027-01-01"


def test_project_create_and_need_add_round_trip(server_db):
    from bookkit.repo import projects

    rw, org = _rw(server_db)
    made = mcpserver._project_create(rw, "Acme", "HQ Tower Build",
                                     site="Chicago, IL", start_on="2026-09-01")
    project = projects.find_project(rw, made["project_ref"])
    assert project.site == "Chicago, IL"

    need = mcpserver._need_add(rw, made["project_ref"], "builders risk",
                               needed_by="2026-08-25")
    got = projects.needs_for_project(rw, project.id)
    assert [n.line for n in got] == ["builders risk"]
    assert need["batch"] != made["batch"]


def test_create_batches_revert_wholesale(server_db):
    from bookkit.repo import opportunities

    rw, org = _rw(server_db)
    out = mcpserver._opportunity_create(rw, "Acme", "Cyber placement", lines="cyber")
    mcpserver._revert_batch(rw, out["batch"], now="2026-08-14T03:00:00Z")
    assert opportunities.find(rw, out["opportunity_ref"]) is None


# -- team ---------------------------------------------------------------------


def test_team_roster_exposes_assignment_ids(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    member = team.create_member(rw, "Dana Cruz", specialty="cyber")
    assignment = team.assign(rw, member.id, org_id=org.id, lines="cyber")

    out = mcpserver._team_roster(rw)
    names = [m["name"] for m in out["members"]]
    assert "Dana Cruz" in names
    dana = next(m for m in out["members"] if m["name"] == "Dana Cruz")
    assert dana["assignments"][0]["assignment_id"] == assignment.id
    assert dana["assignments"][0]["account"] == "Acme"


def test_member_create_refuses_duplicate_name(server_db):
    rw, _ = _rw(server_db)
    out = mcpserver._member_create(rw, "Dana Cruz", specialty="cyber")
    assert out["batch"].startswith("MCP-")
    with pytest.raises(ValueError):
        mcpserver._member_create(rw, "dana cruz")


def test_team_assign_by_exact_member_name_scopes_org_and_lines(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    out = mcpserver._team_assign(rw, "Dana Cruz", client="Acme",
                                 lines="cyber", role="placement_specialist")
    assert out["batch"].startswith("MCP-")
    rows = team.for_org(rw, org.id)
    assert len(rows) == 1 and rows[0]["lines"] == "cyber"


def test_team_assign_requires_exactly_one_scope(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    with pytest.raises(ValueError):
        mcpserver._team_assign(rw, "Dana Cruz")   # neither client nor placement


def test_team_assign_validates_role_vocab(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    with pytest.raises(ValueError) as err:
        mcpserver._team_assign(rw, "Dana Cruz", client="Acme", role="wizard")
    assert "account_lead" in str(err.value)


def test_unassign_takes_exact_id_and_reverts(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme")
    aid = assigned["assignment_id"]

    out = mcpserver._team_unassign(rw, aid)
    assert team.for_org(rw, org.id) == []

    mcpserver._revert_batch(rw, out["batch"], now="2026-08-14T03:30:00Z")
    assert len(team.for_org(rw, org.id)) == 1


def test_team_unassign_stamps_the_org_for_a_deal_level_assignment(server_db):
    """A deal-level assignment carries only placement_id, so reading org_id
    off the row leaves the batch unattributed — invisible in that client's
    history. team_assign and edit_field both resolve it through the
    placement; unassign has to agree."""
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import placements

    rw, org = _rw(server_db)
    placement = placements.create(rw, org.id, program_name="Tower GL",
                                  period_from="2026-01-01",
                                  period_to="2027-01-01")
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz",
                                      placement_ref=placement.ref)

    out = mcpserver._team_unassign(rw, assigned["assignment_id"])
    assert batches_repo.get_by_ref(rw, out["batch"]).org_id == org.id


def test_team_unassign_keeps_the_org_for_an_account_level_assignment(server_db):
    """The account-level path must not regress while fixing the deal-level one."""
    from bookkit.repo import batches as batches_repo

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme")

    out = mcpserver._team_unassign(rw, assigned["assignment_id"])
    assert batches_repo.get_by_ref(rw, out["batch"]).org_id == org.id


def test_member_deactivate_retires_someone_with_no_assignments(server_db):
    from bookkit.repo import team

    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")

    out = mcpserver._member_deactivate(rw, "Dana Cruz")
    assert out["active"] is False
    assert out["unassigned"] == 0
    assert out["batch"].startswith("MCP-")
    assert [m.name for m in team.list_members(rw, active_only=True)] == []
    assert [m.name for m in team.list_members(rw, active_only=False)] == ["Dana Cruz"]


def test_member_deactivate_refuses_and_names_the_clients(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme")

    with pytest.raises(ValueError) as err:
        mcpserver._member_deactivate(rw, "Dana Cruz")
    message = str(err.value)
    assert "Acme" in message
    assert "cascade" in message
    # refused means nothing moved
    assert len(team.for_org(rw, org.id)) == 1
    assert team.list_members(rw, active_only=True)[0].name == "Dana Cruz"


def test_member_deactivate_refuses_someone_already_inactive(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._member_deactivate(rw, "Dana Cruz")
    with pytest.raises(ValueError) as err:
        mcpserver._member_deactivate(rw, "Dana Cruz")
    assert "already inactive" in str(err.value)


def test_member_deactivate_cascade_removes_every_assignment(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme", lines="cyber")

    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert out["active"] is False
    assert out["unassigned"] == 1
    assert team.for_org(rw, org.id) == []
    assert team.list_members(rw, active_only=True) == []


def test_cascade_is_ONE_batch_and_revert_restores_everything(server_db):
    """The whole point of cascade over N separate unassigns: one undo unit."""
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme", lines="cyber")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme", lines="property")

    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert out["unassigned"] == 2
    assert team.for_org(rw, org.id) == []

    mcpserver._revert_batch(rw, out["batch"], now="2026-08-14T04:00:00Z")
    assert len(team.for_org(rw, org.id)) == 2
    assert team.list_members(rw, active_only=True)[0].name == "Dana Cruz"


def test_cascade_on_someone_with_no_assignments_still_works(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert out["unassigned"] == 0
    assert out["active"] is False


def test_cascade_covers_deal_level_assignments_too(server_db):
    from bookkit.repo import placements, team

    rw, org = _rw(server_db)
    placement = placements.create(rw, org.id, program_name="Tower GL",
                                  period_from="2026-01-01",
                                  period_to="2027-01-01")
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", placement_ref=placement.ref)

    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert out["unassigned"] == 1
    assert team.for_org(rw, org.id) == []


def test_cascade_tags_provenance_on_each_unassigned_assignment(server_db):
    """A cascaded removal must leave the same source=mcp audit trail as a
    standalone team_unassign — see _member_deactivate's cascade loop."""
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme", lines="cyber")
    aid = assigned["assignment_id"]

    mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert team.for_org(rw, org.id) == []

    events = rw.execute(
        "SELECT * FROM event_log WHERE entity_id = ? AND field = 'source'",
        (aid,)).fetchall()
    assert events and events[0]["new_value"] == "mcp"


def test_cascade_batch_has_no_org_id(server_db):
    """Spec Decision 2: a cascade spans clients, so no single org owns the
    batch — the client names go in the summary instead."""
    from bookkit.repo import batches as batches_repo

    rw, _org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme", lines="cyber")

    out = mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)
    assert batches_repo.get_by_ref(rw, out["batch"]).org_id is None


def test_member_deactivate_is_registered(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "member_deactivate" in names


def test_member_reactivate_brings_someone_back(server_db):
    from bookkit.repo import team

    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._member_deactivate(rw, "Dana Cruz")

    out = mcpserver._member_reactivate(rw, "Dana Cruz")
    assert out["active"] is True
    assert out["batch"].startswith("MCP-")
    assert [m.name for m in team.list_members(rw, active_only=True)] == ["Dana Cruz"]


def test_member_reactivate_refuses_someone_already_active(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    with pytest.raises(ValueError) as err:
        mcpserver._member_reactivate(rw, "Dana Cruz")
    assert "already active" in str(err.value)


def test_reactivate_does_NOT_resurrect_cascaded_assignments(server_db):
    """Spec decision: revert_batch is the undo for a cascade. Half-restoring
    would be worse than saying so."""
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    mcpserver._team_assign(rw, "Dana Cruz", client="Acme")
    mcpserver._member_deactivate(rw, "Dana Cruz", cascade=True)

    mcpserver._member_reactivate(rw, "Dana Cruz")
    assert team.list_members(rw, active_only=True)[0].name == "Dana Cruz"
    assert team.for_org(rw, org.id) == []      # assignments stay gone


def test_edit_field_changes_an_assignment_role(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme",
                                      role="analyst")
    aid = assigned["assignment_id"]

    out = mcpserver._edit_field(
        rw, "team_assignment", aid, "role", "account_lead",
        expecting="analyst",
    )
    assert out["batch"].startswith("MCP-")
    assert team.for_org(rw, org.id)[0]["role"] == "account_lead"


def test_edit_field_changes_an_assignment_lines(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme",
                                      lines="cyber")
    aid = assigned["assignment_id"]

    out = mcpserver._edit_field(
        rw, "team_assignment", aid, "lines", "cyber, property",
        expecting="cyber",
    )
    assert out["batch"].startswith("MCP-")
    assert team.for_org(rw, org.id)[0]["lines"] == "cyber, property"


def test_edit_field_refuses_a_role_outside_the_vocabulary(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme",
                                      role="analyst")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_assignment", assigned["assignment_id"], "role",
            "wizard", expecting="analyst",
        )
    assert "account_lead" in str(err.value)


def test_edit_field_on_an_assignment_refuses_a_stale_expecting(server_db):
    from bookkit.repo import team

    rw, org = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme",
                                      role="analyst")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_assignment", assigned["assignment_id"], "role",
            "account_lead", expecting="claims_advocate",
        )
    assert "analyst" in str(err.value)
    assert team.for_org(rw, org.id)[0]["role"] == "analyst"   # nothing written


def test_edit_field_refuses_an_unknown_assignment_id(server_db):
    rw, _ = _rw(server_db)
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_assignment", "NOPE", "lines", "cyber", expecting="x",
        )
    assert "team_roster" in str(err.value)


def test_edit_field_refuses_rescoping_an_assignment(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_assignment", assigned["assignment_id"], "org_id",
            "somewhere-else", expecting=None,
        )
    assert "not editable" in str(err.value)


def test_edit_field_redirects_active_to_the_deactivate_tools(server_db):
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "team_member", "Dana Cruz", "active", "no", expecting="yes",
        )
    message = str(err.value)
    assert "member_deactivate" in message
    assert "member_reactivate" in message


def test_edit_field_redirects_item_status_to_the_transition_tools(server_db):
    """status and received_on move together (services.rfi.mark_received), so
    neither is a single-field compare-and-set. Without a redirect the model
    got the generic "not editable; allowed: [...]" list and no idea where the
    transition actually lives."""
    rw, _ = _rw(server_db)
    created = mcpserver._request_create(rw, "Acme", "Sompo questions", ["loss runs"])
    item = mcpserver._request_items(rw, created["request_ref"])["items"][0]

    for field, expected in (
        ("status", "request_item_received"),
        ("received_on", "request_item_received"),
    ):
        with pytest.raises(ValueError) as err:
            mcpserver._edit_field(
                rw, "rfi_item", item["item_ref"], field, "received", expecting=None,
            )
        assert expected in str(err.value)


def test_assignment_notes_round_trip_through_the_roster(server_db):
    """notes is only editable if a read hands the model its current value —
    compare-and-set has nothing to compare against otherwise."""
    rw, _ = _rw(server_db)
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz", client="Acme")
    aid = assigned["assignment_id"]

    dana = next(m for m in mcpserver._team_roster(rw)["members"]
                if m["name"] == "Dana Cruz")
    assert dana["assignments"][0]["notes"] is None

    mcpserver._edit_field(rw, "team_assignment", aid, "notes",
                          "covers the London tower", expecting=None)

    dana = next(m for m in mcpserver._team_roster(rw)["members"]
                if m["name"] == "Dana Cruz")
    assert dana["assignments"][0]["notes"] == "covers the London tower"


def test_edit_field_resolves_org_for_a_deal_level_assignment(server_db):
    """A placement-scoped assignment has org_id NULL; the batch still has to
    be stamped with the org, or the change is invisible to that client's
    history."""
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import placements

    rw, org = _rw(server_db)
    placement = placements.create(rw, org.id, program_name="Tower GL",
                                  period_from="2026-01-01",
                                  period_to="2027-01-01")
    mcpserver._member_create(rw, "Dana Cruz")
    assigned = mcpserver._team_assign(rw, "Dana Cruz",
                                      placement_ref=placement.ref,
                                      role="analyst")

    out = mcpserver._edit_field(
        rw, "team_assignment", assigned["assignment_id"], "role",
        "account_lead", expecting="analyst",
    )
    assert batches_repo.get_by_ref(rw, out["batch"]).org_id == org.id


# -- transitions --------------------------------------------------------------


def test_opportunity_stage_advances_one_gate(server_db):
    from bookkit.repo import opportunities

    rw, org = _rw(server_db)
    opp = opportunities.create(rw, org.id, "Cyber placement")
    out = mcpserver._opportunity_stage(rw, opp.ref, "qualified")
    assert out["stage"] == "qualified"
    assert out["batch"].startswith("MCP-")


def test_opportunity_stage_illegal_jump_refused_with_ladder(server_db):
    from bookkit.repo import opportunities

    rw, org = _rw(server_db)
    opp = opportunities.create(rw, org.id, "Cyber placement")
    with pytest.raises(Exception) as err:
        mcpserver._opportunity_stage(rw, opp.ref, "quoted")
    assert "qualified" in str(err.value)      # the ladder is in the error
    assert opportunities.get(rw, opp.id).stage == "identified"


def test_opportunity_stage_won_closes_properly(server_db):
    from bookkit.repo import base, opportunities

    rw, org = _rw(server_db)
    opp = opportunities.create(rw, org.id, "Cyber placement")
    out = mcpserver._opportunity_stage(rw, opp.ref, "won")
    got = opportunities.get(rw, opp.id)
    assert got.probability_pct == 100
    assert got.outcome == "won" and got.closed_at is not None
    assert out["stage"] == "won"
    assert base.alive  # keep import


def test_task_reopen_flips_back(server_db):
    rw, org = _rw(server_db)
    task = tasks.create(rw, "chase quote", org_id=org.id)
    tasks.complete(rw, task.id)
    out = mcpserver._task_reopen(rw, task.id)
    assert out["status"] == "open"
    assert tasks.get(rw, task.id).completed_at is None


def test_request_item_waive_sets_status(server_db):
    rw, org = _rw(server_db)
    req = rfi.create_request(rw, org.id, "Sompo questions", "2026-08-05")
    item = rfi.add_item(rw, req.id, "how many vehicles?")
    out = mcpserver._request_item_waive(rw, item.id)
    assert out["status"] == "waived"
    assert rfi.get_item(rw, item.id).status == "waived"


def test_write_expansion_tools_are_registered(server_db):
    server = build_server(server_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {
        "edit_field", "contact_add", "opportunity_create", "project_create",
        "need_add", "member_create", "team_assign", "team_unassign",
        "team_roster", "opportunity_stage", "task_reopen",
        "request_item_waive", "member_deactivate", "member_reactivate",
    } <= names


def test_editable_fields_all_exist_on_their_table(server_db):
    """A field offered by edit_field that has no column fails at the DB layer,
    after the tool has already told the caller it was allowed. `opportunity.notes`
    was advertised that way; the table never had the column."""
    from bookkit.repo.base import ENTITY_TABLES

    conn = db.connect(server_db)
    for kind, fields in mcpserver._EDITABLE.items():
        table = ENTITY_TABLES[kind]
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        missing = set(fields) - columns
        assert not missing, f"{kind}: {sorted(missing)} offered but not on {table}"


# -- a refusal says something: no bare repo KeyError reaches the model --------


def test_unknown_refs_refuse_with_a_recovery_path(server_db):
    """Six kinds in _edit_target already named where the right id comes from
    ("read team_roster for exact ids"); task, project need, rfi item and batch
    fell straight through to repo KeyError — `task TSK-9999 not found` names
    the failure and no recovery. One function, two standards. A KeyError also
    is not a ValueError, so these assertions fail on the old behaviour twice
    over: wrong type, and no pointer."""
    rw, org = _rw(server_db)

    with pytest.raises(ValueError) as task_err:
        mcpserver._edit_target(rw, "task", "TSK-9999", None)
    assert "no task 'TSK-9999'" in str(task_err.value)
    assert "open_items" in str(task_err.value)

    with pytest.raises(ValueError) as need_err:
        mcpserver._edit_target(rw, "project_need", "nope", None)
    assert "no project need 'nope'" in str(need_err.value)
    assert "open_items" in str(need_err.value)

    with pytest.raises(ValueError) as item_err:
        mcpserver._edit_target(rw, "rfi_item", "nope", None)
    assert "no request item 'nope'" in str(item_err.value)
    assert "request_items" in str(item_err.value)

    with pytest.raises(ValueError) as batch_err:
        mcpserver._revert_batch(rw, "MCP-9999", now="2026-08-18T09:00:00+00:00")
    assert "no batch 'MCP-9999'" in str(batch_err.value)
    assert "list_batches" in str(batch_err.value)


def test_every_ref_taking_verb_refuses_the_same_way(server_db):
    """The resolvers are shared, so the verbs that take the same ref direct
    from the model refuse identically — task_complete was the message the
    audit actually quoted."""
    rw, org = _rw(server_db)

    for call in (
        lambda: mcpserver._task_complete(rw, "TSK-9999"),
        lambda: mcpserver._task_reopen(rw, "TSK-9999"),
    ):
        with pytest.raises(ValueError, match="no task 'TSK-9999' — read open_items"):
            call()

    for call in (
        lambda: mcpserver._request_item_waive(rw, "nope"),
        lambda: mcpserver._request_item_received(rw, "nope"),
    ):
        with pytest.raises(ValueError, match="no request item 'nope' — read request_items"):
            call()

    with pytest.raises(ValueError, match="no batch 'MCP-9999' — read list_batches"):
        mcpserver._program_revert_file(rw, "MCP-9999")


# -- log_activity can record yesterday ----------------------------------------


def test_log_activity_records_the_type_and_the_day_it_happened(server_db):
    """It hardcoded type="note" and occurred_on=today, so "the call I had with
    Sarah last Tuesday" could not be logged — and, there being no interaction
    kind in edit_field, could not be corrected after the fact either."""
    rw, org = _rw(server_db)

    out = mcpserver._log_activity(
        rw, "Acme", "walked the yard with Sarah",
        type="site_visit", occurred_on="2026-08-11",
    )

    assert out["type"] == "site_visit"
    assert out["occurred_on"] == "2026-08-11"
    logged = interactions.get(rw, out["interaction_ref"])
    assert logged.type == "site_visit"
    assert logged.occurred_on == "2026-08-11"
    # and it is findable by the read that names refs
    found = mcpserver._recent_activity(rw, "Acme")[0]
    assert found["type"] == "site_visit" and found["occurred_on"] == "2026-08-11"


def test_log_activity_defaults_are_exactly_what_it_used_to_hardcode(server_db):
    from datetime import date as date_cls

    rw, org = _rw(server_db)
    out = mcpserver._log_activity(rw, "Acme", "a note")
    assert out["type"] == "note"
    assert out["occurred_on"] == date_cls.today().isoformat()


def test_log_activity_refuses_a_type_outside_the_vocabulary(server_db):
    rw, org = _rw(server_db)
    with pytest.raises(ValueError) as err:
        mcpserver._log_activity(rw, "Acme", "a note", type="phonecall")
    assert "'type' must be one of" in str(err.value)
    assert "site_visit" in str(err.value)          # names the legal values
    assert interactions.for_org(rw, org.id) == []  # nothing written


def test_log_activity_refuses_a_bare_number_as_a_date(server_db):
    """CLAUDE.md's rule, reached through the tool: dateparser reads "5" as a
    MONTH and future-biases it, so "the 5th" once saved as 2027-05-01 and fell
    off every attention window silently. parse_human_date refuses it and the
    refusal must reach the model intelligibly, not be routed around."""
    rw, org = _rw(server_db)
    with pytest.raises(ValueError) as err:
        mcpserver._log_activity(rw, "Acme", "a note", occurred_on="5")
    assert "'5' is not a date" in str(err.value)
    assert "a bare number is ambiguous" in str(err.value)
    assert interactions.for_org(rw, org.id) == []  # nothing written


def test_log_activity_takes_a_human_backdate(server_db):
    """The write-up-after-the-fact case, in the forms parse_human_date
    actually accepts. NOTE: "last tuesday" is NOT one of them — it returns
    None and is refused, so the tool docstring must not promise it."""
    from datetime import date as date_cls
    from datetime import timedelta

    rw, org = _rw(server_db)
    today = date_cls.today()

    yesterday = mcpserver._log_activity(
        rw, "Acme", "spoke to Sarah", type="call", occurred_on="yesterday")
    assert yesterday["occurred_on"] == (today - timedelta(days=1)).isoformat()

    older = mcpserver._log_activity(
        rw, "Acme", "and again", type="call", occurred_on="2 days ago")
    assert older["occurred_on"] == (today - timedelta(days=2)).isoformat()


def test_registered_log_activity_forwards_type_and_occurred_on(server_db):
    import asyncio

    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    server = build_server(server_db)
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    out = asyncio.run(tools["log_activity"].fn(
        client="Acme", note="a call", type="call", occurred_on="2026-08-11"))
    assert out["type"] == "call" and out["occurred_on"] == "2026-08-11"
