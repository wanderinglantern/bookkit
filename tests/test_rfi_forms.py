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
        "title", "requested_on", "due_on", "market_org_id", "cancelled_at",
        "notes",
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
