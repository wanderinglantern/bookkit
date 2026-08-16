"""MCP server: tool functions against a real (temp) database. Tools are
tested as plain functions via the registry — the stdio round-trip lives in
test_mcp_roundtrip.py."""

from __future__ import annotations

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
    with pytest.raises(KeyError):
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
    with pytest.raises(KeyError):
        mcpserver._activity_delete(rw, "not-a-real-id")


def test_activity_delete_refuses_to_delete_twice(server_db):
    """The second call must not read as success — the row is already gone."""
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)
    ref = mcpserver._log_activity(rw, "Acme", "oops")["interaction_ref"]
    mcpserver._activity_delete(rw, ref)
    with pytest.raises(KeyError):
        mcpserver._activity_delete(rw, ref)


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


def test_every_write_tool_returns_a_batch_ref(server_db):
    conn = db.connect(server_db)
    orgs.create(conn, name="Acme", kind="client")
    conn.close()
    rw = db.connect(server_db)

    logged = mcpserver._log_activity(rw, "Acme", "a note")
    assert logged["batch"].startswith("MCP-")

    made = mcpserver._task_create(rw, "chase the quote", client="Acme")
    assert made["batch"].startswith("MCP-")
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
