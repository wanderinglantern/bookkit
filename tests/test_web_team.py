"""Team on the web (gap 7): the roster page and the account rail's live
Team section.

The rules being exercised are deliberately NOT this surface's: name
uniqueness is repo/team.py's guard, retire/reinstate are
services.team.member_deactivate/_reactivate (the same calls mcpserver
delegates to — the seam test at the bottom pins that delegation), and
every write is one batch a later `R` can revert."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.repo import batches as batches_repo
from bookkit.repo import orgs as orgs_repo
from bookkit.repo import team as team_repo
from bookkit.web.app import create_app

# The frozen suite clock (conftest.FROZEN_TODAY) is 2026-08-14; reverts take
# `now` as a parameter, never the wall clock.
_NOW = "2026-08-14T10:00:00Z"


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _conn(client: TestClient):
    return client.app.state.conn


def _org_with_team(client: TestClient):
    conn = _conn(client)
    org = next(
        (o for o in orgs_repo.list_orgs(conn, kind="client")
         if team_repo.for_org(conn, o.id)),
        None,
    )
    assert org is not None, "the seed assigns team members to 8 accounts"
    return org


def _member_with_assignments(client: TestClient):
    conn = _conn(client)
    member = next(
        (m for m in team_repo.list_members(conn)
         if team_repo.for_member(conn, m.id)),
        None,
    )
    assert member is not None, "the seed assigns team members"
    return member


def _batch_events(conn, batch_id: str):
    return conn.execute(
        "SELECT * FROM event_log WHERE batch_id = ?", (batch_id,)
    ).fetchall()


# --- the roster page ---------------------------------------------------------


def test_team_page_lists_the_tui_columns(client):
    """TeamScreen's columns — name, title, specialty, where assigned — plus
    the assignment count and active state the web brief adds."""
    response = client.get("/team")
    assert response.status_code == 200
    # the seeded roster, with the columns beside the names
    assert "Dana Okafor" in response.text
    assert "SVP, Cyber Practice" in response.text
    assert "cyber, tech E&amp;O" in response.text
    assert ">active<" in response.text


def test_the_line_filter_mirrors_the_tui_f(client):
    """?line=cyber runs the same services.team.find_specialists the TUI's
    filter runs — specialties AND live assignment lines — and shows the
    match evidence."""
    response = client.get("/team?line=cyber")
    assert response.status_code == 200
    assert "Dana Okafor" in response.text  # specialty "cyber, tech E&O"
    assert "% ·" in response.text  # match evidence rendered
    # no seeded specialty or assignment line contains a q or a z, and
    # partial_ratio needs SOME overlap to clear the service's 60 threshold
    none = client.get("/team?line=zzqqzz")
    assert none.status_code == 200
    assert "nobody matches" in none.text


def test_member_create_through_the_form(client):
    response = client.post(
        "/team/members/new",
        data={"name": "Priya Shah", "title": "Marine Broker",
              "specialty": "marine", "email": "", "phone": "", "notes": ""},
    )
    assert response.status_code == 200
    conn = _conn(client)
    names = [m.name for m in team_repo.list_members(conn)]
    assert "Priya Shah" in names
    # one batch, source=web, the TUI form title's own tool slug
    batch = batches_repo.most_recent(conn)
    assert batch is not None and batch.source == "web"
    assert batch.tool == "new_team_member"


def test_duplicate_name_is_refused_by_the_repo_guard(client):
    """The refusal is repo/team.py's own — case-insensitive, with its own
    sentence — rendered back in the form (HTTP 200, input intact, nothing
    written), never a second web-side check."""
    conn = _conn(client)
    before = len(team_repo.list_members(conn, active_only=False))
    response = client.post("/team/members/new", data={"name": "dana okafor"})
    assert response.status_code == 200
    assert "already holds that name" in response.text
    assert 'value="dana okafor"' in response.text  # commit-in-place
    assert len(team_repo.list_members(conn, active_only=False)) == before


def test_rename_goes_through_the_same_guard(client):
    conn = _conn(client)
    dana = next(m for m in team_repo.list_members(conn) if m.name == "Dana Okafor")
    response = client.post(
        f"/team/members/{dana.id}/edit", data={"name": "Raj Patel"}
    )
    assert response.status_code == 200
    assert "already holds that name" in response.text
    assert team_repo.get_member(conn, dana.id).name == "Dana Okafor"


# --- retiring ---------------------------------------------------------------


def test_deactivate_refuses_while_assignments_are_live(client):
    """The service's refusal, in the page: still assigned means still
    routing attention — the roster must not silently point at someone who
    left."""
    conn = _conn(client)
    member = _member_with_assignments(client)
    response = client.post(f"/team/members/{member.id}/deactivate", data={})
    assert response.status_code == 200
    assert f"{member.name} is still on" in response.text
    assert team_repo.get_member(conn, member.id).active


def test_confirm_step_names_the_live_assignments_and_writes_nothing(client):
    conn = _conn(client)
    member = _member_with_assignments(client)
    rows = team_repo.for_member(conn, member.id)
    response = client.get(f"/team/members/{member.id}/deactivate")
    assert response.status_code == 200
    for row in rows:
        if row["org_name"]:
            assert row["org_name"] in response.text
    assert "ONE revertible batch" in response.text
    # a confirm GET writes NOTHING
    assert team_repo.get_member(conn, member.id).active
    assert len(team_repo.for_member(conn, member.id)) == len(rows)


def test_cascade_is_one_batch_and_r_can_revert_it(client):
    """The whole point of the cascade: N removed assignments plus the active
    flip land in ONE batch (source=web, the MCP tool's own name), and
    services.batches.revert — the same call behind the TUI's `R` — puts all
    of it back in one move."""
    from bookkit.services import batches as batches_svc

    conn = _conn(client)
    member = _member_with_assignments(client)
    rows = team_repo.for_member(conn, member.id)
    assert rows, "fixture guarantees assignments"

    response = client.post(
        f"/team/members/{member.id}/deactivate", data={"cascade": "1"}
    )
    assert response.status_code == 200
    assert not team_repo.get_member(conn, member.id).active
    assert team_repo.for_member(conn, member.id) == []

    batch = batches_repo.most_recent(conn)
    assert batch is not None
    assert batch.source == "web" and batch.tool == "member_deactivate"
    assert f"removed {len(rows)} assignments" in batch.summary

    # the batch SHAPE: every touched entity is in this one batch — each
    # assignment's soft delete and the member's active flip
    events = _batch_events(conn, batch.id)
    touched = {(e["entity_type"], e["entity_id"]) for e in events}
    assert ("team_member", member.id) in touched
    for row in rows:
        assert ("team_assignment", str(row["id"])) in touched

    # and R-revertibility is real, not implied: one revert restores all of it
    result = batches_svc.revert(conn, batch.ref, now=_NOW)
    assert result.applied and not result.refused
    assert team_repo.get_member(conn, member.id).active
    assert len(team_repo.for_member(conn, member.id)) == len(rows)


def test_reactivate_brings_a_retired_member_back(client):
    conn = _conn(client)
    member = team_repo.create_member(conn, "Sol Reyes")
    client.post(f"/team/members/{member.id}/deactivate", data={})
    assert not team_repo.get_member(conn, member.id).active
    # the retired list renders the control
    page = client.get("/team")
    assert "Reactivate" in page.text and "Sol Reyes" in page.text
    response = client.post(f"/team/members/{member.id}/reactivate")
    assert response.status_code == 200
    assert team_repo.get_member(conn, member.id).active
    batch = batches_repo.most_recent(conn)
    assert batch is not None and batch.tool == "member_reactivate"
    assert batch.source == "web"


# --- the account rail --------------------------------------------------------


def test_the_rail_renders_live_team_controls(client):
    """The Team section is no longer read-only: Assign is a real hx-get
    button and every row carries Edit and Remove. Nothing in the section is
    the aria-disabled pending treatment any more."""
    org = _org_with_team(client)
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    panel = re.search(
        r'<section class="rail-section" id="team-panel".*?</section>',
        response.text, re.S,
    )
    assert panel, "no #team-panel in the rail"
    html = panel.group(0)
    assert f'hx-get="/accounts/{org.ref}/team/assign"' in html
    assert "aria-disabled" not in html
    first = team_repo.for_org(_conn(client), org.id)[0]
    assert f"/accounts/{org.ref}/team/{first['id']}/edit" in html
    assert f"/accounts/{org.ref}/team/{first['id']}/remove" in html


def test_assignment_add_from_the_account_page(client):
    conn = _conn(client)
    org = next(
        o for o in orgs_repo.list_orgs(conn, kind="client")
        if not team_repo.for_org(conn, o.id)
    )
    member = team_repo.list_members(conn)[0]
    form = client.get(f"/accounts/{org.ref}/team/assign")
    assert form.status_code == 200 and "assign team member" in form.text
    response = client.post(
        f"/accounts/{org.ref}/team/assign",
        data={"team_member_id": member.id, "role": "account_lead",
              "lines": "cyber", "notes": ""},
    )
    assert response.status_code == 200
    rows = team_repo.for_org(conn, org.id)
    assert len(rows) == 1
    assert str(rows[0]["team_member_id"]) == member.id
    assert rows[0]["role"] == "account_lead" and rows[0]["lines"] == "cyber"
    batch = batches_repo.most_recent(conn)
    assert batch is not None and batch.source == "web"
    assert batch.tool == "assign_team_member" and batch.org_id == org.id


def test_the_new_member_sentinel_refuses_with_a_pointer(client):
    """The TUI chains '+ new team member…' into a second modal; the web
    points at /team instead — and writes nothing."""
    from bookkit.forms.entities import NEW_MEMBER

    conn = _conn(client)
    org = _org_with_team(client)
    before = len(team_repo.for_org(conn, org.id))
    response = client.post(
        f"/accounts/{org.ref}/team/assign",
        data={"team_member_id": NEW_MEMBER, "role": "", "lines": "", "notes": ""},
    )
    assert response.status_code == 200
    assert "add the colleague on the Team page first" in response.text
    assert len(team_repo.for_org(conn, org.id)) == before


def test_assignment_edit_is_in_place_and_never_rescopes(client):
    """role/lines/notes only: the edit form renders NO member select and NO
    client/placement field (assignment_form(existing=...) deliberately
    omits them — re-scoping is unassign + assign), and the write leaves the
    scope untouched."""
    conn = _conn(client)
    org = _org_with_team(client)
    row = team_repo.for_org(conn, org.id)[0]
    aid = str(row["id"])

    form = client.get(f"/accounts/{org.ref}/team/{aid}/edit")
    assert form.status_code == 200
    assert 'name="team_member_id"' not in form.text
    assert 'name="org_id"' not in form.text and 'name="placement_id"' not in form.text

    response = client.post(
        f"/accounts/{org.ref}/team/{aid}/edit",
        data={"role": "claims_advocate", "lines": "marine", "notes": "handover"},
    )
    assert response.status_code == 200
    edited = team_repo.get_assignment(conn, aid)
    assert edited.role == "claims_advocate" and edited.lines == "marine"
    assert edited.team_member_id == str(row["team_member_id"])
    assert edited.org_id == row["org_id"]
    assert edited.placement_id == row["placement_id"]
    batch = batches_repo.most_recent(conn)
    assert batch is not None and batch.tool == "edit_assignment"


def test_assignment_remove_confirms_then_removes_in_one_batch(client):
    conn = _conn(client)
    org = _org_with_team(client)
    row = team_repo.for_org(conn, org.id)[0]
    aid, who = str(row["id"]), str(row["member_name"])

    confirm = client.get(f"/accounts/{org.ref}/team/{aid}/remove")
    assert confirm.status_code == 200 and who in confirm.text
    # the confirm GET wrote NOTHING
    assert any(str(r["id"]) == aid for r in team_repo.for_org(conn, org.id))

    response = client.post(f"/accounts/{org.ref}/team/{aid}/remove")
    assert response.status_code == 200
    assert not any(str(r["id"]) == aid for r in team_repo.for_org(conn, org.id))
    batch = batches_repo.most_recent(conn)
    assert batch is not None
    # the TUI D-flow's own tool and sentence, so `R` reads one thing
    assert batch.tool == "team_unassign" and batch.source == "web"
    assert batch.summary == f"removed {who} from this team"

    # double-submit: 200 with the truth in the page, nothing else written
    again = client.post(f"/accounts/{org.ref}/team/{aid}/remove")
    assert again.status_code == 200
    assert "already removed" in again.text


def test_someone_elses_assignment_is_a_404(client):
    """/accounts/{ref}/team/{assignment_id} is a compound claim — this
    account AND this row — and a foreign row answers 404 on every verb."""
    conn = _conn(client)
    org = _org_with_team(client)
    other = next(
        o for o in orgs_repo.list_orgs(conn, kind="client")
        if o.id != org.id and team_repo.for_org(conn, o.id)
    )
    foreign = str(team_repo.for_org(conn, other.id)[0]["id"])
    assert client.get(f"/accounts/{org.ref}/team/{foreign}/edit").status_code == 404
    assert client.get(f"/accounts/{org.ref}/team/{foreign}/remove").status_code == 404
    assert client.post(f"/accounts/{org.ref}/team/{foreign}/remove").status_code == 404
    # and it is still there
    assert any(str(r["id"]) == foreign for r in team_repo.for_org(conn, other.id))


# --- the seam: MCP goes through the same service -----------------------------


def test_mcpserver_deactivate_routes_through_the_shared_service(client, monkeypatch):
    """A green suite proves nothing broke, not that the new path is taken:
    this pins that mcpserver._member_deactivate actually delegates to
    services.team.member_deactivate — the rule the web calls — rather than
    keeping a private copy that could drift."""
    from bookkit import mcpserver
    from bookkit.services import team as team_svc

    conn = _conn(client)
    calls: list[tuple] = []
    real = team_svc.member_deactivate

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(team_svc, "member_deactivate", spy)
    member = team_repo.create_member(conn, "Seam Test")
    mcpserver._member_deactivate(conn, "Seam Test")
    assert len(calls) == 1
    assert calls[0][1]["source"] == "mcp"
    assert calls[0][0][1] == member.id
