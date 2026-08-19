"""Composition tests for the Information Requests sheet — pure, no xlsx
involved (that's covered in test_services.py's assembler tests). Mirrors
the fixture style of test_rfi_repo.py / test_rfi_service.py."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import orgs, rfi
from bookkit.services.export_rfi import compose_information_requests

TODAY = date(2026, 8, 13)


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "rfi_export.db")
    yield connection
    connection.close()


def _client(conn):
    return orgs.create(conn, name="Endeavour Energy", kind="client")


def test_compose_empty_when_no_requests(conn) -> None:
    org = _client(conn)
    assert compose_information_requests(conn, org.id, TODAY) == []


def test_compose_omits_sheet_when_nothing_outstanding(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "Sompo — property questions", "2026-08-05")
    item = rfi.add_item(conn, req.id, "loss runs")
    rfi.update_item(conn, item.id, status="received", received_on="2026-08-12")
    assert compose_information_requests(conn, org.id, TODAY) == []


def test_compose_excludes_cancelled_requests(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "withdrawn ask", "2026-08-05")
    rfi.add_item(conn, req.id, "still outstanding")
    rfi.update_request(conn, req.id, cancelled_at="2026-08-10")
    assert compose_information_requests(conn, org.id, TODAY) == []


def test_compose_excludes_waived_items_but_keeps_the_request_alive(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "Sompo — property questions", "2026-08-05")
    waived = rfi.add_item(conn, req.id, "loss runs")
    rfi.add_item(conn, req.id, "safety manual")
    rfi.update_item(conn, waived.id, status="waived")
    sections = compose_information_requests(conn, org.id, TODAY)
    assert len(sections) == 1
    prompts = [row[0] for row in sections[0].rows]
    assert prompts == ["safety manual"]


def test_compose_flat_request_one_section_with_asked_and_due(conn) -> None:
    org = _client(conn)
    market = orgs.create(conn, name="Sompo", kind="market")
    req = rfi.create_request(
        conn, org.id, "property questions", "2026-08-05", due_on="2026-08-19",
        market_org_id=market.id,
    )
    rfi.add_item(conn, req.id, "how many locations?")
    sections = compose_information_requests(conn, org.id, TODAY)
    assert len(sections) == 1
    assert sections[0].label == "Sompo — property questions · asked 5 Aug · due 19 Aug"


def test_compose_flat_request_no_due_omits_due_suffix(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials")
    sections = compose_information_requests(conn, org.id, TODAY)
    assert sections[0].label == "— — onboarding docs · asked 5 Aug"


def test_compose_item_row_shape(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05", due_on="2026-09-01")
    rfi.add_item(
        conn, req.id, "audited financials",
        detail="Please send **audited** financials for the last 3 years.",
        kind="document",
    )
    row = compose_information_requests(conn, org.id, TODAY)[0].rows[0]
    assert row == (
        "audited financials", "Please send audited financials for the last 3 years.",
        "Document", "2026-09-01",
    )


def test_compose_item_due_falls_back_to_request_due(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05", due_on="2026-09-01")
    rfi.add_item(conn, req.id, "no own due date")
    row = compose_information_requests(conn, org.id, TODAY)[0].rows[0]
    assert row[3] == "2026-09-01"


def test_compose_subgroups_by_category_with_uncategorised_trailing(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    rfi.add_item(conn, req.id, "tax return", category="Financials")
    rfi.add_item(conn, req.id, "safety manual", category="Safety")
    rfi.add_item(conn, req.id, "anything else")

    sections = compose_information_requests(conn, org.id, TODAY)
    labels = [s.label for s in sections]
    assert labels == [
        "— — onboarding docs · Financials",
        "— — onboarding docs · Safety",
        None,
    ]
    assert [row[0] for row in sections[0].rows] == ["audited financials", "tax return"]
    assert [row[0] for row in sections[1].rows] == ["safety manual"]
    assert [row[0] for row in sections[2].rows] == ["anything else"]


def test_compose_all_categorised_no_trailing_section(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    sections = compose_information_requests(conn, org.id, TODAY)
    assert len(sections) == 1
    assert sections[0].label == "— — onboarding docs · Financials"


def test_compose_orders_requests_by_earliest_outstanding_due_then_ref(conn) -> None:
    org = _client(conn)
    later = rfi.create_request(conn, org.id, "later ask", "2026-08-01", due_on="2026-10-01")
    rfi.add_item(conn, later.id, "x")
    sooner = rfi.create_request(conn, org.id, "sooner ask", "2026-08-01", due_on="2026-08-20")
    rfi.add_item(conn, sooner.id, "y")
    undated = rfi.create_request(conn, org.id, "undated ask", "2026-08-01")
    rfi.add_item(conn, undated.id, "z")

    sections = compose_information_requests(conn, org.id, TODAY)
    titles_in_order = [s.label.split(" · ")[0].split(" — ", 1)[1] for s in sections]
    assert titles_in_order == ["sooner ask", "later ask", "undated ask"]


def test_compose_breaks_a_due_date_tie_on_ref(conn) -> None:
    """The `then ref` half of the name above was unreachable: no two fixture
    requests shared a due date, so dropping the ref from the sort key changed
    nothing (2026-08-18).

    The tie is only visible because requests_for_org hands them back
    `requested_on DESC` within a due date — so repo order is B, A and ref
    order is A, B. A stable sort with no tiebreak keeps B first.
    """
    org = _client(conn)
    first = rfi.create_request(
        conn, org.id, "A asked first", "2026-08-01", due_on="2026-09-01")
    rfi.add_item(conn, first.id, "x")
    second = rfi.create_request(
        conn, org.id, "B asked later", "2026-08-05", due_on="2026-09-01")
    rfi.add_item(conn, second.id, "y")
    assert first.ref < second.ref
    assert [r.ref for r in rfi.requests_for_org(conn, org.id)] == [
        second.ref, first.ref
    ], "repo order must differ from ref order or the tiebreak is untested"

    sections = compose_information_requests(conn, org.id, TODAY)
    titles = [s.label.split(" · ")[0].split(" — ", 1)[1] for s in sections]
    assert titles == ["A asked first", "B asked later"]


def test_compose_item_due_pulls_a_request_forward_in_ordering(conn) -> None:
    """An item's own earlier due beats its request's later due for ordering,
    the same "earliest effective due" rule that drives the chase queue."""
    org = _client(conn)
    slow = rfi.create_request(conn, org.id, "A slow ask", "2026-08-01", due_on="2026-12-01")
    rfi.add_item(conn, slow.id, "x")
    urgent = rfi.create_request(conn, org.id, "B urgent ask", "2026-08-01", due_on="2026-12-01")
    rfi.add_item(conn, urgent.id, "y", due_on="2026-08-15")

    sections = compose_information_requests(conn, org.id, TODAY)
    assert sections[0].label.startswith("— — B urgent ask")


def test_compose_within_request_category_order_matches_repo_order(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "docs", "2026-08-05")
    rfi.add_item(conn, req.id, "safety manual", category="Safety")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    rfi.add_item(conn, req.id, "tax return", category="Financials")
    sections = compose_information_requests(conn, org.id, TODAY)
    assert [s.label for s in sections] == [
        "— — docs · Financials", "— — docs · Safety",
    ]


def test_compose_merged_away_market_reads_as_merged_market(conn) -> None:
    org = _client(conn)
    market = orgs.create(conn, name="Axa XL", kind="market")
    req = rfi.create_request(
        conn, org.id, "property questions", "2026-08-05", market_org_id=market.id,
    )
    rfi.add_item(conn, req.id, "how many vehicles?")
    orgs.delete(conn, market.id)
    sections = compose_information_requests(conn, org.id, TODAY)
    assert sections[0].label.startswith("(merged market) — property questions")


def test_compose_only_scoped_to_the_given_org(conn) -> None:
    org_a = _client(conn)
    org_b = _client(conn)
    req_b = rfi.create_request(conn, org_b.id, "not mine", "2026-08-05")
    rfi.add_item(conn, req_b.id, "z")
    assert compose_information_requests(conn, org_a.id, TODAY) == []
