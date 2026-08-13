from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import orgs, rfi
from bookkit.tui.widgets import entity_forms as ef


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "rfi.db")
    yield connection
    connection.close()


def test_request_form_offers_markets_and_defaults_today(conn) -> None:
    orgs.create(conn, name="Sompo", kind="market")
    spec = ef.request_form(conn=conn)
    keys = [f.key for f in spec.fields]
    assert keys == [
        "title", "requested_on", "due_on", "market_org_id",
        "placement_id", "project_id", "cancelled_at", "notes",
    ]
    market_field = next(f for f in spec.fields if f.key == "market_org_id")
    assert "Sompo" in [label for label, _ in market_field.options]
    assert market_field.optional_select is True
    assert spec.initial["requested_on"]


def test_cancelling_a_request_closes_it(conn) -> None:
    """Withdrawal happens in the form, not on a key — 'cancelled on' set to a
    date is what closes it."""
    from bookkit.services import rfi as rfi_svc

    org = orgs.create(conn, name="Endeavour", kind="client")
    req = rfi.create_request(conn, org.id, "withdrawn", "2026-08-05")
    rfi.add_item(conn, req.id, "never mind")
    assert rfi_svc.is_open(conn, req.id) is True
    ef.apply_request(conn, {"cancelled_at": "2026-08-12"}, org.id, existing=req)
    assert rfi_svc.is_open(conn, req.id) is False


def test_un_cancelling_a_request_reopens_it(conn) -> None:
    """forms.dropped() strips None, so blanking 'cancelled on' would be a
    no-op and a mis-cancelled request would have no UI path back."""
    from bookkit.services import rfi as rfi_svc

    org = orgs.create(conn, name="Endeavour", kind="client")
    req = rfi.create_request(conn, org.id, "withdrawn by mistake", "2026-08-05")
    rfi.add_item(conn, req.id, "loss runs")
    ef.apply_request(conn, {"cancelled_at": "2026-08-12"}, org.id, existing=req)
    cancelled = rfi.get_request(conn, req.id)
    assert rfi_svc.is_open(conn, req.id) is False

    ef.apply_request(conn, {"cancelled_at": None}, org.id, existing=cancelled)
    assert rfi.get_request(conn, req.id).cancelled_at is None
    assert rfi_svc.is_open(conn, req.id) is True


def test_apply_request_creates_then_updates(conn) -> None:
    org = orgs.create(conn, name="Endeavour", kind="client")
    created = ef.apply_request(
        conn,
        {"title": "Sompo questions", "requested_on": "2026-08-05", "due_on": None,
         "market_org_id": None, "notes": None},
        org.id,
    )
    assert created.ref.startswith("RFI-")
    updated = ef.apply_request(
        conn, {"title": "Sompo questions v2"}, org.id, existing=created
    )
    assert updated.id == created.id
    assert updated.title == "Sompo questions v2"


def test_request_form_offers_the_client_scope(conn) -> None:
    """The spec's binding decision — the request holds the scope link, items
    inherit it — is only reachable if the form can set one."""
    from bookkit.repo import placements
    from bookkit.repo import projects as projects_repo

    org = orgs.create(conn, name="Endeavour", kind="client")
    placement = placements.create(
        conn, org.id, "Property Program", "2026-01-01", "2027-01-01"
    )
    project = projects_repo.create_project(conn, org.id, "HQ Tower Build")
    spec = ef.request_form(conn=conn, org_id=org.id)
    keys = [f.key for f in spec.fields]
    assert keys == [
        "title", "requested_on", "due_on", "market_org_id",
        "placement_id", "project_id", "cancelled_at", "notes",
    ]
    placement_field = next(f for f in spec.fields if f.key == "placement_id")
    assert placement.id in [v for _, v in placement_field.options]
    assert placement.ref in "".join(label for label, _ in placement_field.options)
    assert placement_field.optional_select is True
    project_field = next(f for f in spec.fields if f.key == "project_id")
    assert (project.name, project.id) in project_field.options
    assert project_field.optional_select is True


def test_request_scope_round_trips_and_labels(conn) -> None:
    from bookkit.repo import placements
    from bookkit.services import rfi as rfi_svc

    org = orgs.create(conn, name="Endeavour", kind="client")
    placement = placements.create(
        conn, org.id, "Property Program", "2026-01-01", "2027-01-01"
    )
    created = ef.apply_request(
        conn,
        {"title": "Sompo questions", "requested_on": "2026-08-05",
         "placement_id": placement.id, "project_id": None},
        org.id,
    )
    assert rfi.get_request(conn, created.id).placement_id == placement.id
    assert rfi_svc.scope_label(conn, created) == placement.ref


def test_request_form_keeps_a_live_scope(conn) -> None:
    """The dead-option guard exists for soft-deleted links; it must not fire on
    options that were never built — without org_id both tuples are empty and a
    live placement would be blanked out of the form."""
    from bookkit.repo import placements

    org = orgs.create(conn, name="Endeavour", kind="client")
    placement = placements.create(
        conn, org.id, "Property Program", "2026-01-01", "2027-01-01"
    )
    req = rfi.create_request(
        conn, org.id, "Sompo questions", "2026-08-05", placement_id=placement.id
    )
    assert ef.request_form(req, conn=conn, org_id=org.id).initial[
        "placement_id"
    ] == placement.id
    assert ef.request_form(req, conn=conn).initial["placement_id"] == placement.id


def test_request_scope_can_be_switched_from_placement_to_project(conn) -> None:
    """The natural edit — clear the placement, pick a project — must write the
    blank through, or the row keeps both and the CHECK fires an opaque
    IntegrityError."""
    from bookkit.repo import placements
    from bookkit.repo import projects as projects_repo

    org = orgs.create(conn, name="Endeavour", kind="client")
    placement = placements.create(
        conn, org.id, "Property Program", "2026-01-01", "2027-01-01"
    )
    project = projects_repo.create_project(conn, org.id, "HQ Tower Build")
    created = ef.apply_request(
        conn,
        {"title": "Sompo questions", "requested_on": "2026-08-05",
         "placement_id": placement.id, "project_id": None},
        org.id,
    )
    ef.apply_request(
        conn,
        {"placement_id": None, "project_id": project.id},
        org.id,
        existing=rfi.get_request(conn, created.id),
    )
    switched = rfi.get_request(conn, created.id)
    assert switched.placement_id is None
    assert switched.project_id == project.id


def test_request_scope_can_be_cleared_entirely(conn) -> None:
    from bookkit.repo import placements

    org = orgs.create(conn, name="Endeavour", kind="client")
    placement = placements.create(
        conn, org.id, "Property Program", "2026-01-01", "2027-01-01"
    )
    created = ef.apply_request(
        conn,
        {"title": "Sompo questions", "requested_on": "2026-08-05",
         "placement_id": placement.id},
        org.id,
    )
    ef.apply_request(
        conn,
        {"placement_id": None},
        org.id,
        existing=rfi.get_request(conn, created.id),
    )
    cleared = rfi.get_request(conn, created.id)
    assert cleared.placement_id is None
    assert cleared.project_id is None


def test_request_scope_label_covers_projects_and_account_level(conn) -> None:
    from bookkit.repo import projects as projects_repo
    from bookkit.services import rfi as rfi_svc

    org = orgs.create(conn, name="Endeavour", kind="client")
    project = projects_repo.create_project(conn, org.id, "HQ Tower Build")
    scoped = ef.apply_request(
        conn,
        {"title": "builder's risk questions", "requested_on": "2026-08-05",
         "project_id": project.id},
        org.id,
    )
    assert rfi_svc.scope_label(conn, scoped) == "HQ Tower Build"
    projects_repo.delete_project(conn, project.id)
    assert rfi_svc.scope_label(conn, scoped) == "(deleted project)"

    plain = ef.apply_request(
        conn, {"title": "onboarding docs", "requested_on": "2026-08-05"}, org.id
    )
    assert rfi_svc.scope_label(conn, plain) == "—"


def test_request_refuses_both_a_placement_and_a_project(conn) -> None:
    """The DB CHECK would raise an opaque IntegrityError; FormModal turns a
    ValueError into an in-place refusal with the input intact."""
    from bookkit.repo import placements
    from bookkit.repo import projects as projects_repo

    org = orgs.create(conn, name="Endeavour", kind="client")
    placement = placements.create(
        conn, org.id, "Property Program", "2026-01-01", "2027-01-01"
    )
    project = projects_repo.create_project(conn, org.id, "HQ Tower Build")
    with pytest.raises(
        ValueError, match="about a placement OR a project, not both"
    ):
        ef.apply_request(
            conn,
            {"title": "both", "requested_on": "2026-08-05",
             "placement_id": placement.id, "project_id": project.id},
            org.id,
        )
    assert rfi.requests_for_org(conn, org.id) == []


def test_item_form_completes_categories_from_existing_items(conn) -> None:
    org = orgs.create(conn, name="Endeavour", kind="client")
    req = rfi.create_request(conn, org.id, "docs", "2026-08-05")
    rfi.add_item(conn, req.id, "financials", category="Financials")
    spec = ef.rfi_item_form(conn=conn)
    category = next(f for f in spec.fields if f.key == "category")
    assert "Financials" in category.suggestions
    assert spec.initial["kind"] == "question"
    assert spec.initial["status"] == "outstanding"


def test_apply_rfi_item_creates_then_updates(conn) -> None:
    org = orgs.create(conn, name="Endeavour", kind="client")
    req = rfi.create_request(conn, org.id, "docs", "2026-08-05")
    item = ef.apply_rfi_item(
        conn,
        {"prompt": "loss runs", "kind": "document", "category": "Financials",
         "due_on": None, "detail": None, "status": "outstanding",
         "received_on": None, "response": None},
        req.id,
    )
    assert item.kind == "document"
    done = ef.apply_rfi_item(
        conn, {"status": "received", "received_on": "2026-08-12"}, req.id,
        existing=item,
    )
    assert done.status == "received"
