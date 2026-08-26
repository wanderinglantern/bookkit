"""Cross-field consistency: the rules where one field must relate correctly
to another (services/consistency.py, audit item D, 2026-08-20).

Every rule gets BOTH halves — the bad combination is refused, AND the
legitimate edge cases still save. An over-strict rule that refuses a normal
working state (a project with no end date yet, a market that answers the day
it was asked) is worse than the gap it closes, so the legal edges are asserted
as hard as the illegal ones.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from bookkit import db, mcpserver
from bookkit.forms import entities as ef
from bookkit.repo import orgs, placements, rfi, submissions
from bookkit.repo import projects as projects_repo
from bookkit.services import consistency, placement_edit

TODAY = date.today().isoformat()


@pytest.fixture
def book(tmp_path: Path):
    conn = db.connect(tmp_path / "consistency.db")
    yield conn
    conn.close()


def _client(conn: sqlite3.Connection, name: str = "Zephyr Logistics"):
    return orgs.create(conn, name=name, kind="client")


# --- the shared primitive -----------------------------------------------------


def test_check_order_lets_a_missing_half_through() -> None:
    """A blank date means "not known yet" and cannot contradict anything.

    This is the guard that keeps every optional date in the book saveable:
    refusing the pair when one half is empty would make a project with no end
    date, or a request with no due date, unsaveable records."""
    consistency.check_order(None, "2020-01-01", earlier_label="a", later_label="b")
    consistency.check_order("2030-01-01", None, earlier_label="a", later_label="b")
    consistency.check_order(None, None, earlier_label="a", later_label="b")


def test_a_refusal_names_the_value_and_the_fix() -> None:
    """forms/spec.py:date_refusal is the standard: the offending value AND
    what would be accepted, never just the objection."""
    with pytest.raises(ValueError) as err:
        consistency.check_order(
            "2026-01-01", "2020-01-01", earlier_label="start", later_label="end"
        )
    message = str(err.value)
    assert "2020-01-01" in message  # the value objected to
    assert "2026-01-01" in message  # what it disagrees with
    assert "on or after" in message  # the remedy
    assert "correct the start" in message  # the OTHER remedy


# --- 1. placement period ------------------------------------------------------


def test_placement_form_refuses_an_expiry_before_its_inception(book) -> None:
    org = _client(book)
    with pytest.raises(ValueError) as err:
        ef.apply_placement(
            book,
            {"program_name": "P", "period_from": "2026-10-01",
             "period_to": "2025-10-01", "status": "prospective"},
            org.id,
        )
    assert "2025-10-01" in str(err.value) and "later than 2026-10-01" in str(err.value)
    assert placements.for_org(book, org.id) == []


def test_placement_form_refuses_a_zero_day_period(book) -> None:
    """Equal dates are refused HERE and nowhere else in this module: towerkit's
    validator refuses `end <= start` and imports/mappers/book.py refuses
    `period_to <= period_from`, so a form that accepted one would make a
    placement the file layer can never adopt."""
    org = _client(book)
    with pytest.raises(ValueError):
        ef.apply_placement(
            book,
            {"program_name": "P", "period_from": "2026-10-01",
             "period_to": "2026-10-01", "status": "prospective"},
            org.id,
        )


def test_a_one_day_program_period_is_legal(book) -> None:
    org = _client(book)
    made = ef.apply_placement(
        book,
        {"program_name": "Event cover", "period_from": "2026-10-01",
         "period_to": "2026-10-02", "status": "prospective"},
        org.id,
    )
    assert made.period_to == "2026-10-02"


def test_editing_only_the_expiry_is_checked_against_the_stored_inception(book) -> None:
    """The half that was typed is not the row. An edit that moves only the
    expiry has to be compared against the inception already stored."""
    org = _client(book)
    made = placements.create(book, org.id, "P", "2026-10-01", "2027-10-01")

    with pytest.raises(ValueError):
        ef.apply_placement(book, {"period_to": "2026-09-01"}, org.id, existing=made)
    assert placements.get(book, made.id).period_to == "2027-10-01"

    moved = ef.apply_placement(book, {"period_to": "2027-12-01"}, org.id, existing=made)
    assert moved.period_to == "2027-12-01"


def test_the_web_cell_seam_refuses_a_reversed_period(book) -> None:
    """placement_edit.split is the seam the web's one-field cell edit passes
    through, and it is where an UNLINKED placement's dates fold into the book
    half — which is exactly why towerkit's guard never saw them."""
    org = _client(book)
    made = placements.create(book, org.id, "P", "2026-10-01", "2027-10-01")

    with pytest.raises(ValueError):
        placement_edit.split(made, {"period_to": "2025-01-01"})

    file_changes, book_changes = placement_edit.split(made, {"period_to": "2028-01-01"})
    assert (file_changes, book_changes) == ({}, {"period_to": "2028-01-01"})


# --- 2. subjectivity status / satisfied_on ------------------------------------


def _submission(conn: sqlite3.Connection, sent_on: str = "2026-08-01"):
    org = _client(conn, "Harborline")
    market = orgs.create(conn, name="Sompo", kind="market")
    placement = placements.create(conn, org.id, "P", "2026-01-01", "2027-01-01")
    return submissions.create(conn, market.id, sent_on, placement_id=placement.id)


def test_met_with_no_date_stamps_today(book) -> None:
    sub = _submission(book)
    made = ef.apply_subjectivity(
        book,
        {"description": "signed application", "status": "met", "satisfied_on": None},
        sub.id,
    )
    assert made.satisfied_on == TODAY


def test_met_keeps_a_back_dated_satisfaction(book) -> None:
    sub = _submission(book)
    made = ef.apply_subjectivity(
        book,
        {"description": "loss runs", "status": "met", "satisfied_on": "2026-07-04"},
        sub.id,
    )
    assert made.satisfied_on == "2026-07-04"

    # an unrelated later edit must not lose the recorded date
    again = ef.apply_subjectivity(
        book, {"notes": "chased", "status": "met", "satisfied_on": None},
        sub.id, existing=made,
    )
    assert again.satisfied_on == "2026-07-04"


def test_putting_a_subjectivity_back_to_outstanding_clears_its_date(book) -> None:
    """The leftover case: a normal correction, not a second mistake. Refusing
    would mean the only way back from a mis-marked row is to clear two fields
    in the right order."""
    sub = _submission(book)
    met = ef.apply_subjectivity(
        book,
        {"description": "sprinkler cert", "status": "met", "satisfied_on": "2026-07-04"},
        sub.id,
    )
    back = ef.apply_subjectivity(
        book, {"status": "outstanding", "satisfied_on": "2026-07-04"},
        sub.id, existing=met,
    )
    assert (back.status, back.satisfied_on) == ("outstanding", None)


def test_waiving_a_subjectivity_carries_no_satisfied_date(book) -> None:
    """'waived' settles a condition without satisfying it — the same shape
    rfi_item has, where a waived item carries no received_on."""
    sub = _submission(book)
    met = ef.apply_subjectivity(
        book,
        {"description": "audited financials", "status": "met",
         "satisfied_on": "2026-07-04"},
        sub.id,
    )
    waived = ef.apply_subjectivity(
        book, {"status": "waived", "satisfied_on": None}, sub.id, existing=met
    )
    assert waived.satisfied_on is None


def test_a_satisfied_date_typed_beside_outstanding_is_refused(book) -> None:
    """The contradiction case, which the leftover rule must not swallow: the
    user is asserting two things that cannot both be true, and silently
    discarding what they typed loses the input without saying so."""
    sub = _submission(book)
    with pytest.raises(ValueError) as err:
        ef.apply_subjectivity(
            book,
            {"description": "loss runs", "status": "outstanding",
             "satisfied_on": "2026-07-04"},
            sub.id,
        )
    assert "2026-07-04" in str(err.value)
    assert "'met'" in str(err.value)  # names the fix
    assert submissions.subjectivities_for(book, sub.id) == []


def test_an_outstanding_subjectivity_with_no_date_is_the_normal_case(book) -> None:
    sub = _submission(book)
    made = ef.apply_subjectivity(
        book,
        {"description": "loss runs to 8/1", "status": "outstanding",
         "satisfied_on": None, "due_on": "2026-08-20"},
        sub.id,
    )
    assert (made.status, made.satisfied_on, made.due_on) == (
        "outstanding", None, "2026-08-20"
    )


def test_settling_late_is_legal(book) -> None:
    """due_on and satisfied_on are deliberately NOT compared: clearing a
    subjectivity after its due date is the ordinary case, not an error."""
    sub = _submission(book)
    made = ef.apply_subjectivity(
        book,
        {"description": "cert", "status": "met", "due_on": "2026-08-01",
         "satisfied_on": "2026-08-30"},
        sub.id,
    )
    assert made.satisfied_on == "2026-08-30"


# --- 3. submission / response dates -------------------------------------------
#
# THE PAIR MOVED TO THE ROW THAT STATES IT (2026-08-26). `apply_response` used
# to write the submission's own columns and check them with
# `check_submission_dates`; it now writes a `market_response`, and the same
# ordering is held by `repo.marketing._reply_guard` and `._expiry_guard` — in
# repo/, where the Marketing panel's cells and MCP's `market_responded`
# inherit it too, which a check in this one form never was.
#
# The assertions still read the SUBMISSION, because that is what these dates
# are rolled up onto and what every quote surface reads.

GL = "general-liability"


def test_a_response_before_the_submission_was_sent_is_refused(book) -> None:
    sub = _submission(book, sent_on="2026-08-01")
    with pytest.raises(ValueError) as err:
        ef.apply_response(
            book, sub.id,
            {"line_id": GL, "status": "quoted", "responded_on": "2026-07-25"},
        )
    assert "2026-07-25" in str(err.value) and "2026-08-01" in str(err.value)
    assert submissions.get(book, sub.id).response_on is None


def test_a_same_day_response_is_legal(book) -> None:
    """The ordinary case on a small account: sent in the morning, quoted in
    the afternoon."""
    sub = _submission(book, sent_on="2026-08-01")
    out = ef.apply_response(
        book, sub.id,
        {"line_id": GL, "status": "quoted", "responded_on": "2026-08-01"},
    )
    assert out.response_on == "2026-08-01"


def test_a_quote_expiring_before_the_response_is_refused(book) -> None:
    """The year-typo case: a live quote lands straight in the EXPIRED bucket
    on save, and nothing objects."""
    sub = _submission(book, sent_on="2026-08-01")
    with pytest.raises(ValueError) as err:
        ef.apply_response(
            book, sub.id,
            {"line_id": GL, "status": "quoted", "responded_on": "2026-08-05",
             "quote_expires_on": "2025-09-04"},
        )
    assert "2025-09-04" in str(err.value)
    assert submissions.get(book, sub.id).quote_expires_on is None


def test_an_expiry_is_checked_against_sent_when_there_is_no_response_date(book) -> None:
    """A year typed off last year's diary is just as wrong on a submission
    whose response date has not been filled in."""
    sub = _submission(book, sent_on="2026-08-01")
    with pytest.raises(ValueError):
        ef.apply_response(
            book, sub.id,
            {"line_id": GL, "status": "quoted", "quote_expires_on": "2025-09-04"},
        )


def test_a_quote_that_expires_the_day_it_arrives_is_legal(book) -> None:
    sub = _submission(book, sent_on="2026-08-01")
    out = ef.apply_response(
        book, sub.id,
        {"line_id": GL, "status": "quoted", "responded_on": "2026-08-05",
         "quote_expires_on": "2026-08-05"},
    )
    assert out.quote_expires_on == "2026-08-05"


def test_an_already_lapsed_quote_still_records(book) -> None:
    """An expiry in the PAST relative to today is legal and must stay so —
    quotes lapse, and recording the lapse is the whole point of the field.
    Only the relationship between the three dates is checked."""
    sub = _submission(book, sent_on="2020-01-01")
    out = ef.apply_response(
        book, sub.id,
        {"line_id": GL, "status": "quoted", "responded_on": "2020-01-08",
         "quote_expires_on": "2020-02-08"},
    )
    assert out.quote_expires_on == "2020-02-08"


def test_a_response_with_no_dates_at_all_still_saves(book) -> None:
    sub = _submission(book, sent_on="2026-08-01")
    out = ef.apply_response(
        book, sub.id,
        {"line_id": GL, "status": "declined", "decline_reason": "class"},
    )
    assert (out.response_on, out.quote_expires_on) == (None, None)


def test_a_later_edit_is_checked_against_the_dates_already_stored(book) -> None:
    """dropped() strips the blanks, so the row as it WILL be is what has to be
    compared — not the two fields that happened to be typed this time."""
    sub = _submission(book, sent_on="2026-08-01")
    ef.apply_response(
        book, sub.id,
        {"line_id": GL, "status": "quoted", "responded_on": "2026-08-20"},
    )
    with pytest.raises(ValueError):
        ef.apply_response(
            book, sub.id, {"line_id": GL, "quote_expires_on": "2026-08-10"}
        )


# --- 4. project start / end ---------------------------------------------------


def test_a_project_ending_before_it_starts_is_refused(book) -> None:
    """This pair reaches a client: export_open_items prints "HQ Tower
    (start → end)" into the workbook that goes out."""
    org = _client(book)
    with pytest.raises(ValueError) as err:
        ef.apply_project(
            book,
            {"name": "HQ Tower", "start_on": "2026-01-01", "end_on": "2020-01-01",
             "status": "planned"},
            org.id,
        )
    assert "2020-01-01" in str(err.value)
    assert projects_repo.projects_for_org(book, org.id) == []


def test_a_one_day_project_is_legal(book) -> None:
    org = _client(book)
    made = ef.apply_project(
        book,
        {"name": "Site survey", "start_on": "2026-05-04", "end_on": "2026-05-04",
         "status": "planned"},
        org.id,
    )
    assert (made.start_on, made.end_on) == ("2026-05-04", "2026-05-04")


def test_a_project_with_only_one_date_is_legal(book) -> None:
    """Both dates are optional and usually arrive one at a time — a job with a
    start and no known finish is the normal state of a live project."""
    org = _client(book)
    started = ef.apply_project(
        book, {"name": "Warehouse", "start_on": "2026-05-04", "status": "planned"}, org.id
    )
    assert (started.start_on, started.end_on) == ("2026-05-04", None)
    open_ended = ef.apply_project(
        book, {"name": "Fit-out", "end_on": "2026-05-04", "status": "planned"}, org.id
    )
    assert (open_ended.start_on, open_ended.end_on) == (None, "2026-05-04")


def test_adding_an_end_date_is_checked_against_the_stored_start(book) -> None:
    org = _client(book)
    made = ef.apply_project(
        book, {"name": "Warehouse", "start_on": "2026-05-04", "status": "planned"}, org.id
    )
    with pytest.raises(ValueError):
        ef.apply_project(book, {"end_on": "2026-01-01"}, org.id, existing=made)
    finished = ef.apply_project(book, {"end_on": "2027-01-01"}, org.id, existing=made)
    assert finished.end_on == "2027-01-01"


# --- 5. rfi request asked-on / due ---------------------------------------------


def test_a_request_due_before_it_was_asked_is_refused(book) -> None:
    """The chase queue reads the due date against today, so this row would
    open life overdue — a queue that exists to say what to chase, starting
    with something nobody can act on."""
    org = _client(book)
    with pytest.raises(ValueError) as err:
        ef.apply_request(
            book,
            {"title": "Sompo property questions", "requested_on": "2026-08-10",
             "due_on": "2026-08-01"},
            org.id,
        )
    assert "2026-08-01" in str(err.value)
    assert rfi.requests_for_org(book, org.id) == []


def test_a_same_day_request_is_legal(book) -> None:
    """"Asked this morning, need it today" is a real and common instruction."""
    org = _client(book)
    made = ef.apply_request(
        book,
        {"title": "urgent", "requested_on": "2026-08-10", "due_on": "2026-08-10"},
        org.id,
    )
    assert made.due_on == "2026-08-10"


def test_a_request_with_no_due_date_is_legal(book) -> None:
    org = _client(book)
    made = ef.apply_request(
        book, {"title": "no deadline given", "requested_on": "2026-08-10"}, org.id
    )
    assert made.due_on is None


def test_an_item_needed_before_its_request_was_asked_is_refused(book) -> None:
    """The effective deadline is `item.due_on or request.due_on`, so an
    item-level date opens life overdue just as easily as a request-level one,
    and the request's own guard does not reach it."""
    org = _client(book)
    req = rfi.create_request(book, org.id, "Sompo questions", "2026-08-10")
    with pytest.raises(ValueError) as err:
        ef.apply_rfi_item(
            book,
            {"prompt": "loss runs", "kind": "document", "status": "outstanding",
             "due_on": "2026-08-01"},
            req.id,
        )
    assert "2026-08-01" in str(err.value)
    assert rfi.items_for_request(book, req.id) == []


def test_an_item_due_the_day_it_was_asked_is_legal(book) -> None:
    org = _client(book)
    req = rfi.create_request(book, org.id, "Sompo questions", "2026-08-10")
    made = ef.apply_rfi_item(
        book,
        {"prompt": "loss runs", "kind": "document", "status": "outstanding",
         "due_on": "2026-08-10"},
        req.id,
    )
    assert made.due_on == "2026-08-10"


def test_clearing_an_item_due_date_is_legal(book) -> None:
    """The blanking loop writes an explicit None; the guard must read
    membership, not truthiness, or a cleared date would be re-checked against
    the value being removed."""
    org = _client(book)
    req = rfi.create_request(book, org.id, "Sompo questions", "2026-08-10")
    made = ef.apply_rfi_item(
        book, {"prompt": "loss runs", "due_on": "2026-09-01"}, req.id
    )
    cleared = ef.apply_rfi_item(
        book, {"prompt": "loss runs", "due_on": None}, req.id, existing=made
    )
    assert cleared.due_on is None


# --- rows that already violate a rule stay workable ---------------------------
#
# This is the whole reason the checks are service-layer and not a DB CHECK
# constraint: Grant's real book predates every rule here, and a rule that
# turned an existing bad row into a row nobody can touch would be a worse bug
# than the one it closed.


def test_a_pre_existing_bad_item_due_date_can_still_be_cleared(book) -> None:
    """Written straight through repo/, the way a row from before the rule
    would look. Clearing it must not re-validate the value being REMOVED —
    the guard reads whether the key is present, not whether it is truthy, or
    the only route out of a bad date is blocked by the bad date."""
    org = _client(book)
    req = rfi.create_request(book, org.id, "Sompo questions", "2026-08-10")
    legacy = rfi.add_item(book, req.id, "loss runs", due_on="2020-01-01")

    cleared = ef.apply_rfi_item(
        book, {"prompt": "loss runs", "due_on": None}, req.id, existing=legacy
    )
    assert cleared.due_on is None


def test_a_pre_existing_reversed_project_can_be_repaired(book) -> None:
    org = _client(book)
    legacy = projects_repo.create_project(
        book, org.id, "HQ Tower", start_on="2026-01-01", end_on="2020-01-01"
    )
    assert projects_repo.get_project(book, legacy.id).end_on == "2020-01-01"

    fixed = ef.apply_project(
        book, {"name": "HQ Tower", "start_on": "2026-01-01", "end_on": "2027-01-01"},
        org.id, existing=legacy,
    )
    assert fixed.end_on == "2027-01-01"


# --- the rfi_item settle pair, now shared with subjectivity --------------------


def test_a_received_date_typed_beside_outstanding_is_refused_on_create(book) -> None:
    """The hole the shared rule closed: apply_rfi_item only cleared a
    LEFTOVER date, so on create — and on an edit of an item that had no date
    yet — a received date beside an outstanding status was written through."""
    org = _client(book)
    req = rfi.create_request(book, org.id, "Sompo questions", "2026-08-01")
    with pytest.raises(ValueError):
        ef.apply_rfi_item(
            book,
            {"prompt": "loss runs", "status": "outstanding", "received_on": "2026-08-12"},
            req.id,
        )
    assert rfi.items_for_request(book, req.id) == []


def test_the_rfi_item_pair_still_behaves_as_it_did(book) -> None:
    """Regression fence around the refactor: the three shipped behaviours of
    apply_rfi_item's status/received_on coupling are unchanged."""
    org = _client(book)
    req = rfi.create_request(book, org.id, "Sompo questions", "2026-08-01")
    item = ef.apply_rfi_item(book, {"prompt": "loss runs"}, req.id)

    received = ef.apply_rfi_item(
        book, {"status": "received", "received_on": None}, req.id, existing=item
    )
    assert received.received_on == TODAY  # blank on received stamps today

    back = ef.apply_rfi_item(
        book, {"status": "outstanding", "received_on": TODAY}, req.id, existing=received
    )
    assert back.received_on is None  # a leftover date clears

    dated = ef.apply_rfi_item(
        book, {"status": "received", "received_on": "2026-08-02"}, req.id, existing=back
    )
    assert dated.received_on == "2026-08-02"  # back-dating survives


# --- the MCP surface inherits the same rules ----------------------------------


@pytest.fixture
def server_db(tmp_path: Path) -> Path:
    path = tmp_path / "mcp.db"
    db.connect(path).close()
    return path


def _rw(server_db: Path):
    conn = db.connect(server_db)
    org = orgs.create(conn, name="Acme", kind="client")
    conn.close()
    return db.connect(server_db), org


def test_mcp_project_create_refuses_a_reversed_range(server_db) -> None:
    rw, org = _rw(server_db)
    try:
        with pytest.raises(ValueError):
            mcpserver._project_create(
                rw, "Acme", "HQ Tower", start_on="2026-01-01", end_on="2020-01-01"
            )
        assert projects_repo.projects_for_org(rw, org.id) == []
        made = mcpserver._project_create(
            rw, "Acme", "HQ Tower", start_on="2026-01-01", end_on="2027-01-01"
        )
        assert made["name"] == "HQ Tower"
    finally:
        rw.close()


def test_mcp_edit_field_refuses_an_end_before_the_stored_start(server_db) -> None:
    """`_edit_field` writes ONE column through base.update, which is exactly
    the shape a consistency rule is invisible to — nothing in a single-column
    write has any reason to look at the column beside it."""
    rw, org = _rw(server_db)
    try:
        mcpserver._project_create(
            rw, "Acme", "HQ Tower", start_on="2026-01-01", end_on="2027-01-01"
        )
        project = projects_repo.projects_for_org(rw, org.id)[0]
        with pytest.raises(ValueError) as err:
            mcpserver._edit_field(
                rw, "project", project.ref, "end_on",
                value="2020-01-01", expecting="2027-01-01",
            )
        assert "2020-01-01" in str(err.value)
        assert projects_repo.get_project(rw, project.id).end_on == "2027-01-01"

        # and the guard is keyed BOTH ways: moving the start past the end is
        # the same broken row
        with pytest.raises(ValueError):
            mcpserver._edit_field(
                rw, "project", project.ref, "start_on",
                value="2028-01-01", expecting="2026-01-01",
            )
        assert projects_repo.get_project(rw, project.id).start_on == "2026-01-01"

        out = mcpserver._edit_field(
            rw, "project", project.ref, "end_on",
            value="2028-01-01", expecting="2027-01-01",
        )
        assert out["value"] == "2028-01-01"
    finally:
        rw.close()


def test_mcp_request_create_refuses_a_due_date_in_the_past(server_db) -> None:
    """A request is always asked TODAY on this surface, so "last friday" —
    which parse_human_date happily reads — would file something overdue in the
    same breath it was created."""
    rw, _org = _rw(server_db)
    try:
        with pytest.raises(ValueError):
            mcpserver._request_create(
                rw, "Acme", "Sompo questions", ["loss runs"], due_on="2020-01-01"
            )
        out = mcpserver._request_create(
            rw, "Acme", "Sompo questions", ["loss runs"], due_on="+2w"
        )
        assert out["item_count"] == 1
    finally:
        rw.close()


def test_mcp_edit_field_refuses_an_rfi_due_before_the_ask(server_db) -> None:
    rw, org = _rw(server_db)
    try:
        mcpserver._request_create(rw, "Acme", "Sompo questions", ["loss runs"])
        request = rfi.requests_for_org(rw, org.id)[0]
        with pytest.raises(ValueError):
            mcpserver._edit_field(
                rw, "rfi_request", request.ref, "due_on",
                value="2020-01-01", expecting=None,
            )
        assert rfi.get_request(rw, request.id).due_on is None

        item = rfi.items_for_request(rw, request.id)[0]
        with pytest.raises(ValueError):
            mcpserver._edit_field(
                rw, "rfi_item", item.id, "due_on", value="2020-01-01", expecting=None,
            )
        assert rfi.get_item(rw, item.id).due_on is None
    finally:
        rw.close()


# --- the sample book must satisfy every rule it now carries -------------------


def test_the_seeded_book_violates_none_of_the_new_rules(tmp_path: Path) -> None:
    """A rule that the demo data breaks is a rule Grant's real book probably
    breaks too — and the seed is one transaction, so it would fail whole
    rather than half. Checked directly against every seeded row.

    HONEST ABOUT ITS REACH: the seed builds 30 placements and 40 submissions,
    so those two rules are genuinely exercised. It builds no projects, no
    information requests and no subjectivities at all, so those loops are
    empty today — they are here as the fence that catches the day the seed
    grows them, not as evidence they hold now."""
    from bookkit.seed import seed

    conn = db.connect(tmp_path / "seeded.db")
    try:
        seed(conn)
        for org in orgs.list_orgs(conn):
            for placement in placements.for_org(conn, org.id):
                consistency.check_placement_period(
                    placement.period_from, placement.period_to
                )
            for project in projects_repo.projects_for_org(conn, org.id):
                consistency.check_project_dates(project.start_on, project.end_on)
            for request in rfi.requests_for_org(conn, org.id):
                consistency.check_request_dates(request.requested_on, request.due_on)
                for item in rfi.items_for_request(conn, request.id):
                    consistency.check_item_due(request.requested_on, item.due_on)
            for placement in placements.for_org(conn, org.id):
                for sub in submissions.for_placement(conn, placement.id):
                    consistency.check_submission_dates(
                        sub.sent_on, sub.response_on, sub.quote_expires_on
                    )
                    for subj in submissions.subjectivities_for(conn, sub.id):
                        assert (subj.status == "met") == (subj.satisfied_on is not None)
    finally:
        conn.close()
