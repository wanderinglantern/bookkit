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
    # Five wide since 2026-08-19 — the last cell is the client's answer, blank
    # here. The row shape does not depend on the account; whether the fifth
    # column is PRINTED does (export_open_items._RFI_RESPONSE_COLUMN).
    assert row == (
        "audited financials", "Please send audited financials for the last 3 years.",
        "Document", "2026-09-01", "",
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


# --- the internal rule, on this sheet too --------------------------------------
#
# Sheet 1's SCOPE_NOTE says "Internal administrative items are not included"
# and speaks for the whole workbook. This module had zero references to the
# rule until 2026-08-19, so an item categorised `Internal` shipped here under
# a heading naming it — making that sentence false about the document it
# appears in. Both tiers of the rule apply: exact equality withholds the ROW,
# a prefix suppresses the HEADING (models.is_internal_category /
# reads_as_internal say why the two matches differ).


def test_an_internal_item_never_reaches_the_clients_copy(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    rfi.add_item(conn, req.id, "pull prior loss runs from our file", category="Internal")

    sections = compose_information_requests(conn, org.id, TODAY)
    labels = [s.label for s in sections]
    prompts = [row[0] for s in sections for row in s.rows]

    assert labels == ["— — onboarding docs · Financials"]
    assert prompts == ["audited financials"]


def test_the_internal_match_is_exact_and_case_folded(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "one", category="internal")
    rfi.add_item(conn, req.id, "two", category=" INTERNAL ")

    assert compose_information_requests(conn, org.id, TODAY) == []


def test_a_request_whose_only_outstanding_item_is_internal_is_omitted(conn) -> None:
    """No empty heading, and its date takes no part in ordering the sheet —
    the rule runs before the emptiness test, not after it."""
    org = _client(conn)
    hidden = rfi.create_request(conn, org.id, "our own chase list", "2026-08-01",
                                due_on="2026-08-14")
    rfi.add_item(conn, hidden.id, "reserve note", category="Internal")
    real = rfi.create_request(conn, org.id, "Sompo questions", "2026-08-01",
                              due_on="2026-09-01")
    rfi.add_item(conn, real.id, "how many vehicles?")

    sections = compose_information_requests(conn, org.id, TODAY)
    assert [s.label for s in sections] == [
        "— — Sompo questions · asked 1 Aug · due 1 Sep"
    ]


def test_a_heading_that_reads_internal_is_suppressed_but_its_rows_ship(conn) -> None:
    """C9's half. Equality withholds the row; a prefix only withholds the
    banner — "Internal Review" is a real client-facing ask and vanishing it
    silently would be the unrecoverable failure."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    rfi.add_item(conn, req.id, "sign the audit letter", category="Internal Review")

    sections = compose_information_requests(conn, org.id, TODAY)
    labels = [s.label for s in sections]
    prompts = [row[0] for s in sections for row in s.rows]

    assert not any(label and "Internal" in label for label in labels)
    assert "sign the audit letter" in prompts


def test_a_suppressed_heading_files_its_rows_last_not_mid_list(conn) -> None:
    """groupby only groups ADJACENT equals, so a blanked category left where
    it sat would emit an unlabelled section BETWEEN two labelled ones — and an
    unbannered row reads as belonging to whichever section printed above it."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    rfi.add_item(conn, req.id, "sign the audit letter", category="Internal Review")
    rfi.add_item(conn, req.id, "safety manual", category="Safety")

    sections = compose_information_requests(conn, org.id, TODAY)
    assert [s.label for s in sections] == [
        "— — onboarding docs · Financials",
        "— — onboarding docs · Safety",
        None,
    ]
    assert [row[0] for row in sections[-1].rows] == ["sign the audit letter"]


def test_a_request_of_only_suppressed_headings_keeps_its_full_context(conn) -> None:
    """With every category blanked the request falls back to its single
    full-context section, exactly as an uncategorised request always has."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "sign the audit letter", category="Internal Review")

    sections = compose_information_requests(conn, org.id, TODAY)
    assert [s.label for s in sections] == ["— — onboarding docs · asked 5 Aug"]


def test_the_operator_is_told_what_this_sheet_withheld(conn) -> None:
    """Withholding an outstanding ASK is the costlier direction — a thing never
    asked for is never sent — so it is never silent."""
    from bookkit.services.export_open_items import withheld_note

    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "reserve note", category="Internal")
    rfi.add_item(conn, req.id, "sign the audit letter", category="Internal Review")

    note = withheld_note(conn, org.id)
    assert "1 internal request item withheld" in note
    assert '1 request item categorised "Internal Review" WAS exported' in note


def test_the_operator_note_is_unchanged_when_no_item_is_internal(conn) -> None:
    from bookkit.services.export_open_items import withheld_note

    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")

    assert withheld_note(conn, org.id) == ""


# --- the client's own answers, on the client's own sheet ----------------------


def test_an_answer_rides_along_with_the_item(conn) -> None:
    """Grant, 2026-08-19: responses are client-visible, because they are
    written in language the client could read back."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    item = rfi.add_item(conn, req.id, "payroll by class code")
    rfi.update_item(conn, item.id, response="Confirmed **$4.2M**, split 63/37.")

    row = compose_information_requests(conn, org.id, TODAY)[0].rows[0]

    assert row[4] == "Confirmed $4.2M, split 63/37.", "markdown is flattened, as elsewhere"


def test_a_received_item_keeps_its_answer_on_the_sheet(conn) -> None:
    """REVERSED by Grant on 2026-08-19, the same day the column shipped.

    The sheet was outstanding-only, so marking an item received removed the
    whole row — and the answer with it. The client's copy therefore lost the
    record of what they had told us at the exact moment it became a fact
    rather than a promise. It stays now, under its own heading."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    item = rfi.add_item(conn, req.id, "payroll by class code")
    rfi.update_item(conn, item.id, response="Confirmed.", status="received")

    sections = compose_information_requests(conn, org.id, TODAY)

    rows = [row for section in sections for row in section.rows]
    assert [row[0] for row in rows] == ["payroll by class code"]
    assert rows[0][4] == "Confirmed."


# --- what the client has already told us (Grant, 2026-08-19) ------------------
#
# The sheet was outstanding-only, so an answer left the client's copy the moment
# the item was marked received — taking the record of what they had sent with
# it. Answered items now stay, in their own section, under their own heading.


def test_an_answered_item_stays_on_the_sheet_after_it_is_received(conn) -> None:
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    item = rfi.add_item(conn, req.id, "payroll by class code")
    rfi.update_item(
        conn, item.id, response="Confirmed $4.2M.", status="received",
        received_on="2026-08-18",
    )

    sections = compose_information_requests(conn, org.id, TODAY)

    rows = [row for section in sections for row in section.rows]
    assert any("payroll by class code" in row[0] for row in rows)
    assert any("Confirmed $4.2M." == row[4] for row in rows)


def test_answered_items_sit_under_their_own_heading(conn) -> None:
    """"Items we need from you" is a false statement about something they have
    already sent. The outstanding sections keep that heading; the answered ones
    say what they are."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    still_open = rfi.add_item(conn, req.id, "loss runs")
    done = rfi.add_item(conn, req.id, "payroll by class code")
    rfi.update_item(conn, done.id, response="Confirmed.", status="received")

    sections = compose_information_requests(conn, org.id, TODAY)

    where = {
        row[0]: section.label
        for section in sections
        for row in section.rows
    }
    assert "already sent" in (where["payroll by class code"] or "").lower()
    assert "already sent" not in (where["loss runs"] or "").lower()
    del still_open


def test_a_received_item_with_no_answer_is_not_resurrected(conn) -> None:
    """The point is keeping the ANSWER. A received item nobody recorded an
    answer for carries nothing the client does not already know, and printing
    it back at them pads the deliverable."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    item = rfi.add_item(conn, req.id, "signed application")
    rfi.update_item(conn, item.id, status="received", received_on="2026-08-18")

    sections = compose_information_requests(conn, org.id, TODAY)

    rows = [row for section in sections for row in section.rows]
    assert not any("signed application" in row[0] for row in rows)


def test_a_request_whose_asks_are_all_answered_still_shows_them(conn) -> None:
    """It used to vanish entirely: the composer skipped any request with
    nothing outstanding, so a completed ask took its answers with it."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    item = rfi.add_item(conn, req.id, "payroll by class code")
    rfi.update_item(conn, item.id, response="Confirmed.", status="received")

    sections = compose_information_requests(conn, org.id, TODAY)

    assert sections, "a fully answered request disappeared, answers and all"


def test_an_internal_item_stays_withheld_even_once_it_is_answered(conn) -> None:
    """The client-safe rule applies to the answered half too. Withholding
    outstanding Internal asks and then shipping them the moment they are
    answered would be the same leak, delayed."""
    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "loss runs")
    secret = rfi.add_item(conn, req.id, "our own file note", category="Internal")
    rfi.update_item(conn, secret.id, response="done internally", status="received")

    sections = compose_information_requests(conn, org.id, TODAY)

    rows = [row for section in sections for row in section.rows]
    assert not any("our own file note" in row[0] for row in rows)
    assert not any("done internally" in row[4] for row in rows)


def test_an_answered_internal_item_is_counted_as_withheld(conn) -> None:
    """The withheld count reports what the workbook actually held back. It read
    the outstanding items only, so once answered items joined the sheet an
    Internal one that had been answered was withheld from the client and
    counted by nobody — an omission the operator line exists to prevent."""
    from bookkit.services.export_rfi import withheld_items

    org = _client(conn)
    req = rfi.create_request(conn, org.id, "onboarding docs", "2026-08-05")
    secret = rfi.add_item(conn, req.id, "our own file note", category="Internal")
    rfi.update_item(conn, secret.id, response="done internally", status="received")

    assert [i.id for i in withheld_items(conn, org.id)] == [secret.id]
