"""Internal team: members, account/placement assignments, and 'who do I go
to for cyber?'."""

from __future__ import annotations

import sqlite3

import pytest

from bookkit.repo import orgs, placements, team
from bookkit.services.team import find_specialists


@pytest.fixture
def client(conn: sqlite3.Connection):
    return orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")


def test_member_round_trip(conn) -> None:
    member = team.create_member(
        conn, "Dana Okafor", title="VP, Cyber Practice", specialty="cyber, tech E&O",
        email="dana@brokerage.example",
    )
    assert team.get_member(conn, member.id).specialty == "cyber, tech E&O"
    team.update_member(conn, member.id, title="SVP, Cyber Practice")
    assert team.get_member(conn, member.id).title == "SVP, Cyber Practice"
    team.delete_member(conn, member.id)
    with pytest.raises(KeyError):
        team.get_member(conn, member.id)
    assert team.list_members(conn) == []


def test_assignment_requires_exactly_one_parent(conn, client) -> None:
    member = team.create_member(conn, "Dana Okafor")
    with pytest.raises(ValueError):
        team.assign(conn, member.id)
    placement = placements.create(conn, client.id, "Casualty", "2026-01-01", "2027-01-01")
    with pytest.raises(ValueError):
        team.assign(conn, member.id, org_id=client.id, placement_id=placement.id)


def test_for_org_includes_placement_level(conn, client) -> None:
    dana = team.create_member(conn, "Dana Okafor", specialty="cyber")
    raj = team.create_member(conn, "Raj Patel", specialty="property")
    placement = placements.create(conn, client.id, "Cyber", "2026-01-01", "2027-01-01")
    team.assign(conn, dana.id, org_id=client.id, role="account_lead")
    team.assign(conn, raj.id, placement_id=placement.id,
                role="placement_specialist", lines="cyber")
    rows = team.for_org(conn, client.id)
    assert [r["member_name"] for r in rows] == ["Dana Okafor", "Raj Patel"]
    assert rows[0]["placement_ref"] is None  # account-level first
    assert rows[1]["placement_ref"] == placement.ref


def test_find_specialists_ranks_by_specialty_and_assignment_lines(conn, client) -> None:
    team.create_member(conn, "Dana Okafor", specialty="cyber, tech E&O")
    generalist = team.create_member(conn, "Sam Lee", specialty="middle market property")
    placement = placements.create(conn, client.id, "Cyber", "2026-01-01", "2027-01-01")
    # Sam is actively placing cyber even though it isn't their stated specialty
    team.assign(conn, generalist.id, placement_id=placement.id, lines="cyber excess")

    matches = find_specialists(conn, "cyber")
    names = [m.member.name for m in matches]
    assert names[0] == "Dana Okafor"
    assert "Sam Lee" in names  # found through assignment lines
    assert find_specialists(conn, "aviation") == []


def test_unassign_soft_deletes(conn, client) -> None:
    member = team.create_member(conn, "Dana Okafor")
    assignment = team.assign(conn, member.id, org_id=client.id, role="account_lead")
    team.unassign(conn, assignment.id)
    assert team.for_org(conn, client.id) == []
    assert team.for_member(conn, member.id) == []
