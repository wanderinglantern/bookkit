from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from bookkit.repo import (
    base,
    contacts,
    documents,
    events,
    interactions,
    opportunities,
    orgs,
    placements,
    search,
    submissions,
    tasks,
)

REPO_DIR = Path(__file__).resolve().parents[1] / "src" / "bookkit" / "repo"


def make_client(conn: sqlite3.Connection, name: str = "Atomic Industries, Inc."):
    return orgs.create(conn, kind="client", name=name, status="active", owner="grant")


def make_market(conn: sqlite3.Connection, name: str = "Swiss Re"):
    return orgs.create(conn, kind="market", name=name, status="active")


def test_org_round_trip(conn) -> None:
    org = make_client(conn)
    assert org.ref == "ACC-0001"
    got = orgs.get(conn, org.id)
    assert got.name == "Atomic Industries, Inc."
    assert orgs.find(conn, "ACC-0001").id == org.id
    assert orgs.find_by_name(conn, "Atomic Industries, Inc.").id == org.id


def test_update_writes_event_log(conn) -> None:
    org = make_client(conn)
    orgs.update(conn, org.id, status="dormant", note="gone quiet")
    log = events.field_history(conn, "org", org.id, "status")
    assert len(log) == 1
    assert (log[0].old_value, log[0].new_value, log[0].note) == (
        "active", "dormant", "gone quiet",
    )


def test_soft_delete_hides_everywhere(conn) -> None:
    org = make_client(conn)
    orgs.delete(conn, org.id)
    with pytest.raises(KeyError):
        orgs.get(conn, org.id)
    assert orgs.find(conn, org.ref) is None
    assert orgs.list_orgs(conn) == []
    assert search.search(conn, "Atomic") == []
    base.undelete(conn, "org", org.id)
    assert orgs.get(conn, org.id).name == "Atomic Industries, Inc."
    assert len(search.search(conn, "Atomic")) == 1


def test_no_raw_soft_delete_bypass_in_repo() -> None:
    """Every SELECT in repo/ against a soft-delete table must go through
    alive(); a raw 'deleted_at' comparison outside base.py is a bug."""
    for module in REPO_DIR.glob("*.py"):
        if module.name in ("base.py", "search.py"):
            continue  # base defines the filter; search filters explicitly per join
        text = module.read_text()
        selects = re.findall(r"SELECT .*?FROM (\w+)", text, re.DOTALL)
        for table in selects:
            if table in base.ENTITY_TABLES.values():
                assert "base.alive(" in text or "{base.alive()}" in text, (
                    f"{module.name} queries {table} without the alive() filter"
                )


def test_contacts_primary_flag(conn) -> None:
    org = make_client(conn)
    alice = contacts.create(conn, org.id, first_name="Alice", last_name="Ng", is_primary=1)
    bob = contacts.create(conn, org.id, first_name="Bob", last_name="Reyes")
    contacts.set_primary(conn, bob.id)
    roster = contacts.for_org(conn, org.id)
    assert [c.is_primary for c in roster] == [True, False]
    assert roster[0].id == bob.id
    assert contacts.get(conn, alice.id).is_primary is False


def test_interactions_with_attendees(conn) -> None:
    org = make_client(conn)
    alice = contacts.create(conn, org.id, first_name="Alice", last_name="Ng")
    meeting = interactions.log(
        conn, org.id, "meeting", "Renewal strategy", "2026-08-01",
        body="Discussed cyber tower options", contact_ids=[alice.id],
    )
    assert [c.id for c in interactions.attendees(conn, meeting.id)] == [alice.id]
    assert interactions.last_for_org(conn, org.id).id == meeting.id


def test_task_lifecycle(conn) -> None:
    org = make_client(conn)
    task = tasks.create(conn, "Chase loss runs", org_id=org.id, due_on="2026-08-20")
    assert tasks.open_tasks(conn, due_by="2026-08-25")[0].id == task.id
    assert tasks.open_tasks(conn, due_by="2026-08-15") == []
    done = tasks.complete(conn, task.id)
    assert done.status == "done" and done.completed_at is not None
    assert tasks.open_tasks(conn) == []
    reopened = tasks.reopen(conn, task.id)
    assert reopened.status == "open" and reopened.completed_at is None


def test_placement_queries(conn) -> None:
    org = make_client(conn)
    placements.create(
        conn, org.id, "Casualty", "2025-10-01", "2026-10-01",
        status="bound", total_premium=1_000_000_00,
    )
    hit = placements.expiring_between(conn, "2026-09-01", "2026-12-31")
    assert len(hit) == 1 and hit[0].ref == "PLC-0001"
    assert placements.expiring_between(conn, "2026-11-01", "2026-12-31") == []
    nxt = placements.next_renewal_for_org(conn, org.id, "2026-08-11")
    assert nxt is not None and nxt.period_to == "2026-10-01"


def test_submission_exclusive_parent(conn) -> None:
    org = make_client(conn)
    market = make_market(conn)
    placement = placements.create(conn, org.id, "Casualty", "2025-10-01", "2026-10-01")
    with pytest.raises(ValueError):
        submissions.create(conn, market.id, "2026-08-01")
    opp = opportunities.create(conn, org.id, "New cyber line")
    with pytest.raises(ValueError):
        submissions.create(
            conn, market.id, "2026-08-01",
            placement_id=placement.id, opportunity_id=opp.id,
        )
    sub = submissions.create(conn, market.id, "2026-08-01", placement_id=placement.id)
    assert submissions.for_placement(conn, placement.id)[0].id == sub.id
    assert submissions.outstanding(conn)[0].id == sub.id


def test_opportunity_stage_locked_in_repo(conn) -> None:
    org = make_client(conn)
    opp = opportunities.create(conn, org.id, "New cyber line")
    with pytest.raises(ValueError):
        opportunities.update(conn, opp.id, stage="qualified")


def test_appetite_market_search(conn) -> None:
    swiss = make_market(conn, "Swiss Re")
    chubb = make_market(conn, "Chubb")
    orgs.add_appetite(
        conn, swiss.id, line="cyber", appetite="target",
        max_limit=50_000_000_00, min_premium=100_000_00,
    )
    orgs.add_appetite(conn, chubb.id, line="cyber", appetite="no")
    got = orgs.markets_for_line(conn, "cyber", min_limit=25_000_000_00)
    assert [o.name for o, _ in got] == ["Swiss Re"]
    assert orgs.markets_for_line(conn, "cyber", min_limit=75_000_000_00) == []


def test_documents(conn) -> None:
    org = make_client(conn)
    documents.add(conn, org.id, "2026 policy", "/docs/policy.pdf", kind="policy")
    assert documents.for_org(conn, org.id)[0].path == "/docs/policy.pdf"


def test_fts_search_grouped(conn) -> None:
    org = make_client(conn)
    contacts.create(conn, org.id, first_name="Atomic", last_name="Smith")
    interactions.log(conn, org.id, "note", "Atomic renewal kickoff", "2026-08-01")
    hits = search.search(conn, "atomic")
    kinds = {h.kind for h in hits}
    assert kinds == {"org", "contact", "interaction"}
    # prefix search works
    assert search.search(conn, "atom")


def test_fts_updates_on_edit(conn) -> None:
    org = make_client(conn)
    orgs.update(conn, org.id, name="Molecular Industries")
    assert search.search(conn, "Atomic") == []
    assert len(search.search(conn, "Molecular")) == 1


class TestProjects:
    def test_project_and_need_round_trip(self, conn) -> None:
        from bookkit.repo import projects

        org = orgs.create(conn, kind="client", name="Atomic Industries")
        project = projects.create_project(
            conn, org.id, "HQ Tower Build", site="Chicago, IL",
            status="active", start_on="2026-09-01", end_on="2028-03-01",
        )
        assert project.ref.startswith("PRJ-")
        need = projects.add_need(
            conn, project.id, "Builder's Risk", "2026-08-25",
            limit_cents=5_000_000_000, status="identified",
        )
        assert [n.id for n in projects.needs_for_project(conn, project.id)] == [need.id]
        projects.update_need(conn, need.id, status="placed")
        assert projects.get_need(conn, need.id).status == "placed"
        projects.delete_project(conn, project.id)
        assert projects.projects_for_org(conn, org.id) == []

    def test_needs_due_window_and_statuses(self, conn) -> None:
        from datetime import date

        from bookkit.repo import projects

        today = date(2026, 8, 12)
        org = orgs.create(conn, kind="client", name="Atomic")
        project = projects.create_project(conn, org.id, "Plant Expansion")
        overdue = projects.add_need(conn, project.id, "Wrap-up GL", "2026-08-01")
        soon = projects.add_need(conn, project.id, "Builder's Risk", "2026-09-15")
        projects.add_need(conn, project.id, "Marine Cargo", "2027-06-01")  # far out
        projects.add_need(conn, project.id, "Pollution", "2026-08-20", status="placed")
        rows = projects.needs_due(conn, today, days=90)
        assert [row["id"] for row in rows] == [overdue.id, soon.id]
        assert rows[0]["project_name"] == "Plant Expansion"
        assert rows[0]["org_name"] == "Atomic"


class TestMarketFamilies:
    def test_nest_unnest_and_outline(self, conn) -> None:
        axa = orgs.create(conn, kind="market", name="AXA XL")
        indian = orgs.create(conn, kind="market", name="Indian Harbor Ins Co")
        orgs.create(conn, kind="market", name="Chubb")
        orgs.set_parent(conn, indian.id, axa.id)
        assert [c.id for c in orgs.children(conn, axa.id)] == [indian.id]
        families = dict(
            (top.name, [k.name for k in kids])
            for top, kids in orgs.market_families(conn)
        )
        assert families == {"AXA XL": ["Indian Harbor Ins Co"], "Chubb": []}
        orgs.set_parent(conn, indian.id, None)  # unnest
        assert orgs.children(conn, axa.id) == []

    def test_cycles_and_self_nesting_refused(self, conn) -> None:
        import pytest as _pytest

        a = orgs.create(conn, kind="market", name="A Co")
        b = orgs.create(conn, kind="market", name="B Co")
        orgs.set_parent(conn, b.id, a.id)
        with _pytest.raises(ValueError):
            orgs.set_parent(conn, a.id, b.id)  # cycle
        with _pytest.raises(ValueError):
            orgs.set_parent(conn, a.id, a.id)  # self

    def test_deleted_parent_floats_child_to_top(self, conn) -> None:
        axa = orgs.create(conn, kind="market", name="AXA XL")
        indian = orgs.create(conn, kind="market", name="Indian Harbor Ins Co")
        orgs.set_parent(conn, indian.id, axa.id)
        orgs.delete(conn, axa.id)
        tops = [top.name for top, _ in orgs.market_families(conn)]
        assert "Indian Harbor Ins Co" in tops


class TestVocab:
    def test_lines_union_and_dedupe(self, conn) -> None:
        from bookkit.repo import projects as projects_repo
        from bookkit.repo import vocab

        market = orgs.create(conn, kind="market", name="Chubb")
        orgs.add_appetite(conn, market.id, line="Cyber", appetite="target")
        org = orgs.create(conn, kind="client", name="Atomic")
        opportunities.create(conn, org.id, "Cyber+DO", lines="cyber, D&O")
        project = projects_repo.create_project(conn, org.id, "Build")
        projects_repo.add_need(conn, project.id, "Builder's Risk", "2026-10-01")
        assert vocab.lines(conn) == ["Builder's Risk", "Cyber", "D&O"]

    def test_owner_program_and_market_vocab(self, conn) -> None:
        from bookkit.repo import vocab

        org = orgs.create(conn, kind="client", name="Atomic", owner="grant")
        orgs.create(conn, kind="client", name="Borealis", owner="Grant")  # dupe, case
        orgs.create(conn, kind="market", name="AXA XL")
        placements.create(conn, org.id, "2026 Property", "2026-01-01", "2027-01-01")
        assert vocab.owners(conn) == ["grant"]
        assert vocab.program_names(conn) == ["2026 Property"]
        assert vocab.market_names(conn) == ["AXA XL"]
