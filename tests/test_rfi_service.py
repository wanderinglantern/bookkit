from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import orgs, rfi
from bookkit.services import rfi as rfi_svc

TODAY = date(2026, 8, 13)


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "rfi.db")
    yield connection
    connection.close()


def _request(conn, **fields):
    org = orgs.create(conn, name="Endeavour Energy", kind="client")
    return rfi.create_request(conn, org.id, "Sompo questions", "2026-08-05", **fields)


def test_request_with_an_outstanding_item_is_open(conn) -> None:
    req = _request(conn)
    rfi.add_item(conn, req.id, "how many vehicles?")
    assert rfi_svc.is_open(conn, req.id) is True


def test_all_received_or_waived_closes_the_request(conn) -> None:
    req = _request(conn)
    a = rfi.add_item(conn, req.id, "a")
    b = rfi.add_item(conn, req.id, "b")
    rfi.update_item(conn, a.id, status="received", received_on="2026-08-12")
    rfi.update_item(conn, b.id, status="waived")
    assert rfi_svc.is_open(conn, req.id) is False


def test_a_request_with_no_items_reads_open(conn) -> None:
    """Documented convention: an empty request is still something you owe."""
    req = _request(conn)
    assert rfi_svc.is_open(conn, req.id) is True


def test_overdue_requests_never_fall_off(conn) -> None:
    req = _request(conn, due_on=(TODAY - timedelta(days=400)).isoformat())
    rfi.add_item(conn, req.id, "ancient")
    chases = rfi_svc.outstanding_requests(conn, TODAY, days=120)
    assert [c.request.id for c in chases] == [req.id]
    assert chases[0].days_remaining == -400


def test_requests_beyond_the_window_are_excluded(conn) -> None:
    req = _request(conn, due_on=(TODAY + timedelta(days=200)).isoformat())
    rfi.add_item(conn, req.id, "later")
    assert rfi_svc.outstanding_requests(conn, TODAY, days=120) == []


def test_item_due_pulls_the_request_forward(conn) -> None:
    """The earliest EFFECTIVE due wins: an urgent item surfaces its parent."""
    req = _request(conn, due_on=(TODAY + timedelta(days=200)).isoformat())
    rfi.add_item(conn, req.id, "urgent", due_on=(TODAY + timedelta(days=3)).isoformat())
    chases = rfi_svc.outstanding_requests(conn, TODAY, days=120)
    assert len(chases) == 1
    assert chases[0].days_remaining == 3


def test_counts_are_of_outstanding_items_only(conn) -> None:
    req = _request(conn, due_on=TODAY.isoformat())
    a = rfi.add_item(conn, req.id, "a")
    rfi.add_item(conn, req.id, "b")
    rfi.add_item(conn, req.id, "c")
    rfi.update_item(conn, a.id, status="received", received_on="2026-08-12")
    chase = rfi_svc.outstanding_requests(conn, TODAY, days=120)[0]
    assert (chase.open_count, chase.total_count) == (2, 3)


def test_cancelled_and_closed_requests_are_absent(conn) -> None:
    cancelled = _request(conn, due_on=TODAY.isoformat())
    rfi.add_item(conn, cancelled.id, "x")
    rfi.update_request(conn, cancelled.id, cancelled_at="2026-08-10")

    closed = _request(conn, due_on=TODAY.isoformat())
    done = rfi.add_item(conn, closed.id, "y")
    rfi.update_item(conn, done.id, status="received", received_on="2026-08-12")

    assert rfi_svc.outstanding_requests(conn, TODAY, days=120) == []


def test_mark_received_stamps_status_and_date(conn) -> None:
    req = _request(conn)
    item = rfi.add_item(conn, req.id, "loss runs")
    got = rfi_svc.mark_received(conn, item.id, TODAY.isoformat())
    assert got.status == "received"
    assert got.received_on == "2026-08-13"


def test_mark_received_is_two_field_writes_not_one(conn) -> None:
    """Pinning what the docstring now says out loud: two events, so a single
    `u` reverts only the later one. Nothing here claims otherwise."""
    req = _request(conn)
    item = rfi.add_item(conn, req.id, "loss runs")
    rfi_svc.mark_received(conn, item.id, TODAY.isoformat())
    fields = {
        r[0]
        for r in conn.execute(
            "SELECT field FROM event_log WHERE entity_id = ? AND field != 'created'",
            (item.id,),
        ).fetchall()
    }
    assert fields == {"status", "received_on"}


def test_every_rfi_item_status_is_themed(conn) -> None:
    """Color is signal, not decoration — an unthemed status renders in plain
    FG and reads as 'no state at all'."""
    from bookkit.models import RFI_ITEM_STATUSES
    from bookkit.tui import theme

    assert all(s in theme.STATUS_STYLES for s in RFI_ITEM_STATUSES)
    assert theme.STATUS_STYLES["outstanding"] == theme.AMBER
    assert theme.STATUS_STYLES["received"] == theme.GREEN
    assert theme.STATUS_STYLES["waived"] == theme.DIM


# -- the two rules that were duplicated across surfaces ----------------------


def test_effective_due_prefers_the_item_then_falls_back_to_the_request(conn) -> None:
    """The design doc's 'one rule, used by the queue, the tab, and the sheet'
    — it now has exactly one implementation for all three to call."""
    req = _request(conn, due_on="2026-08-19")
    own = rfi.add_item(conn, req.id, "loss runs", due_on="2026-08-15")
    inherited = rfi.add_item(conn, req.id, "how many vehicles?")

    assert rfi_svc.effective_due(own, req) == "2026-08-15"
    assert rfi_svc.effective_due(inherited, req) == "2026-08-19"


def test_effective_due_is_none_when_neither_side_sets_one(conn) -> None:
    req = _request(conn)
    item = rfi.add_item(conn, req.id, "loss runs")
    assert rfi_svc.effective_due(item, req) is None


def test_asker_name_resolves_market_missing_and_merged(conn) -> None:
    """Three-way display convention, previously copy-pasted into navigator.py,
    account.py and export_rfi.py."""
    org = orgs.create(conn, name="Endeavour Energy", kind="client")
    market = orgs.create(conn, name="Sompo", kind="market")

    named = rfi.create_request(
        conn, org.id, "Sompo questions", "2026-08-05", market_org_id=market.id
    )
    internal = rfi.create_request(conn, org.id, "onboarding", "2026-08-05")

    assert rfi_svc.asker_name(conn, named) == "Sompo"
    assert rfi_svc.asker_name(conn, internal) == "—"

    orgs.delete(conn, market.id)
    merged = rfi.get_request(conn, named.id)
    assert rfi_svc.asker_name(conn, merged) == "(merged market)"


def test_asker_placeholders_are_exactly_the_non_name_results(conn) -> None:
    """The TUI dims a placeholder and leaves a real market name plain; that
    test is only honest if the placeholder set stays in sync with the rule."""
    assert rfi_svc.ASKER_PLACEHOLDERS == frozenset({"—", "(merged market)"})
