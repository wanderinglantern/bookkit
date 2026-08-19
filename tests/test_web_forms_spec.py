"""The parser both surfaces share. If these behaviours ever differ between
the TUI and the web, a record saved in one place is not the record saved in
the other."""

from __future__ import annotations

import pytest

from bookkit.forms.spec import (
    BatchSpec,
    Field,
    FieldError,
    FormSpec,
    dropped,
    initial_text,
    parse_value,
    parse_values,
)


def test_money_accepts_cents():
    """bookkit stores cents and format_cents renders them, so a form that
    refuses its own pre-filled value makes the record unsaveable."""
    assert parse_value(Field("premium", "premium", "money"), "1,234.56") == 123456


def test_money_accepts_shorthand():
    assert parse_value(Field("premium", "premium", "money"), "1.5m") == 150000000


def test_money_refusal_is_a_field_error():
    with pytest.raises(ValueError):
        parse_value(Field("premium", "premium", "money"), "not money")


def test_bare_number_is_not_a_date():
    """dateparser reads '5' as a MONTH and future-biases it; 'the 5th' once
    saved as 2027-05-01 and fell off every attention window."""
    with pytest.raises(ValueError):
        parse_value(Field("due_on", "due", "date"), "5")


def test_human_date_parses_to_iso():
    assert parse_value(Field("due_on", "due", "date"), "2026-10-15") == "2026-10-15"


def test_email_is_cleaned():
    # the DOMAIN lowercases; the local part does not. RFC 5321 makes local
    # parts case-sensitive, so "A@B.COM" is "A@b.com" and never "a@b.com".
    assert parse_value(Field("email", "email", "email"), "  A@B.COM ") == "A@b.com"


def test_textarea_is_stored_verbatim():
    field = Field("notes", "notes", "textarea")
    assert parse_value(field, "  two  spaces\nand a line  ") == "  two  spaces\nand a line  "


def test_blank_becomes_none():
    assert parse_value(Field("title", "title"), "   ") is None


def test_parse_values_reports_the_offending_field():
    spec = FormSpec("edit thing", [Field("due_on", "due", "date")])
    with pytest.raises(FieldError) as caught:
        parse_values(spec, {"due_on": "5"})
    assert caught.value.field_key == "due_on"
    assert "due" in caught.value.message


def test_parse_values_enforces_required():
    spec = FormSpec("new thing", [Field("first_name", "first name", required=True)])
    with pytest.raises(FieldError) as caught:
        parse_values(spec, {"first_name": ""})
    assert caught.value.field_key == "first_name"
    assert "required" in caught.value.message


def test_dropped_strips_none_but_keeps_empty_string():
    assert dropped({"a": None, "b": "", "c": 0}) == {"b": "", "c": 0}


def test_initial_text_renders_money_without_a_dollar_sign():
    assert initial_text(Field("premium", "premium", "money"), 123456) == "1,234.56"


def test_batch_spec_derives_tool_from_title_without_the_record_name():
    batch = BatchSpec.for_title("edit contact — Atomic Industries")
    assert batch.tool == "edit_contact"
    assert batch.sentence({}) == "edit contact — Atomic Industries"


def test_mcp_cleans_exactly_like_the_forms_do():
    """mcpserver kept a hand-copied duplicate of the cleaner map, held in sync
    by a comment. One home, or the surfaces drift.

    This checks the cleaner primitive (_clean_by_kind) directly with real
    KINDS, which is safe: unlike a field NAME, a kind is never ambiguous.
    Field-name-to-kind resolution (where the actual bug lived — a NAME is
    not globally 1:1 with a kind, e.g. `description`) is covered end-to-end
    through _edit_field/_enrich_field in tests/test_mcpserver.py, not here."""
    from bookkit import mcpserver
    from bookkit.forms.spec import Field, parse_value

    for kind, raw in [
        ("email", "  A@B.COM "),
        ("phone", "(312) 555-0142"),
        ("url", "company.com"),
        ("domain", "https://company.com/path"),
        ("linkedin", "in/someone"),
        ("naics", "524126"),
        ("text", "  spaced  "),
    ]:
        mcp_cleaned = mcpserver._clean_by_kind(kind, raw)
        form_cleaned = parse_value(Field(kind, kind, kind), raw)
        assert mcp_cleaned == form_cleaned, kind


def test_mcp_has_no_second_cleaner_map():
    from pathlib import Path

    import bookkit

    source = (Path(bookkit.__file__).parent / "mcpserver.py").read_text()
    assert "_FIELD_CLEANERS" not in source, "the duplicate cleaner map is back"


# --- a select's value is checked against its own options ----------------------
#
# parse_value had no `select` branch at all, so the server stored whatever was
# posted. Both halves of that hole are here: a vocabulary that must be one of
# a fixed set, and an account-scoped picker whose options ARE a query. The
# route-level reproductions live in tests/test_web_scoping.py (cross-account
# placement) and tests/test_web_work.py (a status outside the vocabulary).

_STATUSES = (("outstanding", "outstanding"), ("received", "received"),
             ("waived", "waived"))


def test_a_vocabulary_select_refuses_a_value_it_never_offered():
    """`status="NOT_A_STATUS"` was storable, which makes an open request read
    as closed and drop off every attention queue — silently, because nothing
    downstream expects a value outside the vocabulary."""
    field = Field("status", "status", "select", _STATUSES)
    with pytest.raises(ValueError) as caught:
        parse_value(field, "NOT_A_STATUS")
    # a vocabulary is `(s, s)`, so the offered set is worth naming
    assert "outstanding" in str(caught.value)


def test_a_vocabulary_select_accepts_its_own_options():
    field = Field("status", "status", "select", _STATUSES)
    assert parse_value(field, "waived") == "waived"


def test_a_scoped_select_refuses_an_id_it_never_offered():
    """The account-scoped half. The options are a QUERY — this account's
    placements — so membership in them is the account scope check, and there
    is nothing else correct to compare against."""
    mine = Field("placement_id", "about placement", "select",
                 (("PLC-0001 — Atomic casualty", "plc-mine"),), optional_select=True)
    with pytest.raises(ValueError) as caught:
        parse_value(mine, "plc-theirs")
    # opaque ids are noise in a refusal, and not ours to print back
    assert "plc-mine" not in str(caught.value)


def test_a_scoped_select_accepts_a_value_it_offered():
    mine = Field("placement_id", "about placement", "select",
                 (("PLC-0001 — Atomic casualty", "plc-mine"),), optional_select=True)
    assert parse_value(mine, "plc-mine") == "plc-mine"


def test_a_blank_optional_select_is_still_none():
    """The check must not turn 'nothing chosen' into a refusal — every scoped
    picker on request_form is optional_select."""
    field = Field("placement_id", "about placement", "select",
                  (("PLC-0001", "plc-mine"),), optional_select=True)
    assert parse_value(field, "") is None


def test_a_select_with_no_options_stores_nothing():
    """A picker whose query came back empty offered nothing, so nothing it is
    handed can have come from it."""
    with pytest.raises(ValueError):
        parse_value(Field("placement_id", "about placement", "select"), "plc-anything")


def test_the_select_refusal_names_its_field_through_parse_values():
    spec = FormSpec("new item", [Field("status", "status", "select", _STATUSES)])
    with pytest.raises(FieldError) as caught:
        parse_values(spec, {"status": "NOT_A_STATUS"})
    assert caught.value.field_key == "status"
    assert caught.value.message.startswith("status:")


def test_the_two_writers_of_a_vocabulary_now_agree():
    """mcpserver._clean_typed always checked a closed vocabulary; the forms
    did not. Two writers of the same field disagreeing about whether the value
    is checked is the shape this codebase already fought once with the cleaner
    map — so this pins that they refuse the same value."""
    from bookkit import mcpserver

    vocabulary = ("outstanding", "received", "waived")
    with pytest.raises(ValueError):
        mcpserver._clean_typed(vocabulary, "status", "NOT_A_STATUS")
    with pytest.raises(ValueError):
        parse_value(Field("status", "status", "select",
                          tuple((s, s) for s in vocabulary)), "NOT_A_STATUS")
