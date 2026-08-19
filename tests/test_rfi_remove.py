"""Removing an information request, and one item off one.

Grant, at work, 2026-08-19: an MCP call filed an RFI in error and NOTHING on
any surface could take it back. `rfi_repo.delete_request` and `delete_item`
had existed since the feature shipped with no caller anywhere — the CRUD hole
the parity ledger had already predicted in its own words ("a request filed in
error cannot be withdrawn here").

Two verbs, because they are two different facts. A request WITHDRAWN was a
real ask we have since dropped, and it stays in the book with a date on it. A
request filed IN ERROR was never true, and it goes. Collapsing them loses the
distinction the client's own copy depends on.
"""

from __future__ import annotations

import sqlite3

import pytest

from bookkit import db
from bookkit.repo import orgs
from bookkit.repo import rfi as rfi_repo
from bookkit.services import rfi as rfi_svc


@pytest.fixture
def filed(conn: sqlite3.Connection):
    org = orgs.create(conn, kind="client", name="Atomic Industries")
    with db.transaction(conn):
        request = rfi_repo.create_request(
            conn, org.id, "2026 renewal underwriting", "2026-08-19"
        )
        for prompt in ("loss runs 2021-2025", "payroll by class code"):
            rfi_repo.add_item(conn, request.id, prompt)
    return conn, org, request


def test_a_request_filed_in_error_goes_with_its_items(filed):
    conn, org, request = filed

    removed = rfi_svc.remove_request(conn, request.id, source="mcp")

    assert removed.items == 2
    assert rfi_repo.find_request(conn, request.id) is None
    assert rfi_repo.items_for_request(conn, request.id) == []


def test_removing_a_request_is_one_revertible_batch(filed):
    """One writer action is one undo unit. A request that came back without
    its items would be a heading with nothing under it."""
    conn, org, request = filed
    from bookkit.services import batches as batches_svc

    removed = rfi_svc.remove_request(conn, request.id, source="mcp")
    batches_svc.revert(conn, removed.batch, now=db.utc_now())

    assert rfi_repo.find_request(conn, request.id) is not None
    assert len(rfi_repo.items_for_request(conn, request.id)) == 2


def test_a_request_someone_has_answered_is_refused_and_says_why(filed):
    """An answered ask is HISTORY — the client told us something, and deleting
    the question deletes their answer with it. Withdrawing is the verb for a
    live ask we no longer need; this refusal names it."""
    conn, org, request = filed
    item = rfi_repo.items_for_request(conn, request.id)[0]
    with db.transaction(conn):
        rfi_repo.update_item(conn, item.id, response="sent 12 Aug")

    with pytest.raises(ValueError) as refused:
        rfi_svc.remove_request(conn, request.id, source="mcp")

    assert "answered" in str(refused.value)
    assert "cancel" in str(refused.value).lower()
    assert rfi_repo.find_request(conn, request.id) is not None


def test_a_received_item_also_blocks_removal(filed):
    conn, org, request = filed
    item = rfi_repo.items_for_request(conn, request.id)[0]
    with db.transaction(conn):
        rfi_svc.mark_received(conn, item.id, "2026-08-18")

    with pytest.raises(ValueError, match="answered|received"):
        rfi_svc.remove_request(conn, request.id, source="mcp")


def test_removing_a_request_twice_says_it_is_already_gone(filed):
    conn, org, request = filed
    rfi_svc.remove_request(conn, request.id, source="mcp")

    with pytest.raises(ValueError, match="already"):
        rfi_svc.remove_request(conn, request.id, source="mcp")


def test_an_unknown_request_is_refused_by_name(conn: sqlite3.Connection):
    with pytest.raises(ValueError, match="no information request"):
        rfi_svc.remove_request(conn, "01NOTAREALID", source="mcp")


# --- one item off a request ---------------------------------------------------


def test_a_single_item_filed_in_error_goes(filed):
    conn, org, request = filed
    item = rfi_repo.items_for_request(conn, request.id)[0]

    removed = rfi_svc.remove_item(conn, item.id, source="mcp")

    left = rfi_repo.items_for_request(conn, request.id)
    assert [i.id for i in left] == [i.id for i in left if i.id != item.id]
    assert len(left) == 1
    assert removed.batch


def test_removing_an_answered_item_is_refused(filed):
    conn, org, request = filed
    item = rfi_repo.items_for_request(conn, request.id)[0]
    with db.transaction(conn):
        rfi_repo.update_item(conn, item.id, response="sent 12 Aug")

    with pytest.raises(ValueError, match="answered"):
        rfi_svc.remove_item(conn, item.id, source="mcp")


def test_removing_the_last_item_leaves_the_request(filed):
    """A request with no items is an ask not yet written down — services.rfi
    already treats it as open. Taking the last item off is not the same as
    withdrawing the request, and must not silently do it."""
    conn, org, request = filed
    for item in rfi_repo.items_for_request(conn, request.id):
        rfi_svc.remove_item(conn, item.id, source="mcp")

    assert rfi_repo.find_request(conn, request.id) is not None
