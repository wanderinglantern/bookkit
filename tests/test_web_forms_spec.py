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
    by a comment. One home, or the surfaces drift."""
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
        mcp_cleaned = mcpserver._clean_field_value(kind, raw)
        form_cleaned = parse_value(Field(kind, kind, kind), raw)
        assert mcp_cleaned == form_cleaned, kind


def test_mcp_has_no_second_cleaner_map():
    from pathlib import Path

    import bookkit

    source = (Path(bookkit.__file__).parent / "mcpserver.py").read_text()
    assert "_FIELD_CLEANERS" not in source, "the duplicate cleaner map is back"


def test_mcp_stores_multi_line_notes_verbatim():
    """The old map marked `notes` verbatim; CLEANERS is keyed by KIND, so the
    name misses and falls through to clean_text, which collapses newlines. A
    note typed over three lines came back as one until this test existed."""
    from bookkit import mcpserver

    note = "called Dana\n\n- loss runs promised Friday\n- wants EL quoted separately"
    assert mcpserver._clean_field_value("notes", note) == note
