"""One FormSpec, two surfaces. The macro must render every field of every
builder — a kind it does not know renders nothing, and the form then saves
while dropping that field with no error anywhere."""

from __future__ import annotations

import inspect

import pytest

from bookkit.forms import entities
from bookkit.forms.spec import Field, FormSpec
from bookkit.web.forms_render import render_cell, render_form


def _spec_builders():
    for name, fn in vars(entities).items():
        if name.endswith("_form") or name.endswith("_form_initial_profile"):
            if inspect.isfunction(fn):
                yield name, fn


def test_there_are_builders_to_check():
    """Guards the loop below: if the discovery breaks, the parametrised tests
    silently pass on an empty set."""
    assert len(list(_spec_builders())) >= 17


@pytest.mark.parametrize("kind", ["text", "textarea", "select", "date", "money", "int",
                                  "email", "phone", "url", "domain", "linkedin", "naics"])
def test_every_field_kind_renders_a_named_input(kind: str):
    spec = FormSpec("probe", [Field("probe_key", "probe label", kind,
                                    options=(("a", "a"),) if kind == "select" else ())])
    html = render_form(None, spec, action="/probe")
    assert 'name="probe_key"' in html, f"kind {kind!r} rendered no named input"
    assert "probe label" in html


def test_money_and_date_are_text_inputs():
    """type=number rejects '1,234.56' before the server sees it; type=date
    bypasses parse_human_date, whose job is to refuse a bare number."""
    spec = FormSpec("probe", [
        Field("premium", "premium", "money"),
        Field("due_on", "due", "date"),
    ])
    html = render_form(None, spec, action="/probe")
    assert 'type="number"' not in html
    assert 'type="date"' not in html


def test_required_fields_are_marked():
    spec = FormSpec("probe", [Field("name", "name", required=True)])
    html = render_form(None, spec, action="/probe")
    assert "required" in html


def test_submitted_values_win_over_initial():
    """A refused save re-renders with what the user typed, not what was there
    before — commit-in-place is the platform default."""
    spec = FormSpec("probe", [Field("name", "name")], initial={"name": "old"})
    html = render_form(None, spec, action="/probe", submitted={"name": "typed"})
    assert "typed" in html
    assert 'value="old"' not in html


def test_the_error_message_is_rendered():
    spec = FormSpec("probe", [Field("due_on", "due", "date")])
    html = render_form(None, spec, action="/probe", error="due: cannot read a date from '5'")
    assert "cannot read a date from" in html


def test_inline_field_sets_are_shared_not_duplicated():
    """Which fields are editable in place is not a per-surface choice. The TUI
    screens build their column maps from these tuples; the web renders the same
    ones as cells."""
    from bookkit.forms import inline
    from bookkit.tui.screens.navigator import CONTACT_INLINE, TASK_INLINE

    assert tuple(CONTACT_INLINE.values()) == inline.CONTACT_FIELDS
    assert tuple(TASK_INLINE.values()) == inline.TASK_FIELDS


def test_rfi_item_inline_is_shared_not_duplicated():
    from bookkit.forms import inline
    from bookkit.tui.screens.account import RFI_ITEM_INLINE

    assert tuple(RFI_ITEM_INLINE.values()) == inline.RFI_ITEM_FIELDS


@pytest.mark.parametrize("field", [
    Field("role", "role"),
    Field("due_on", "due", "date"),
    Field("email", "email", "email"),
])
def test_render_cell_produces_one_named_input(field):
    html = render_cell(None, field, value="", action="/probe")
    assert f'name="{field.key}"' in html
    assert 'type="number"' not in html and 'type="date"' not in html


def test_render_cell_shows_a_refusal_beside_the_value_it_kept():
    field = Field("due_on", "due", "date")
    message = "enter a date like 2026-10-15, friday, or +2w — a bare number is ambiguous"
    html = render_cell(None, field, value="5", action="/probe", error=message)
    assert "a bare number is ambiguous" in html
    assert 'value="5"' in html


def test_the_date_refusal_says_how_to_fix_it():
    """'cannot read a date from 5' names the objection, not the remedy."""
    import pytest as _pytest

    from bookkit.forms.spec import Field as F
    from bookkit.forms.spec import parse_value as pv

    with _pytest.raises(ValueError) as caught:
        pv(F("due_on", "due", "date"), "5")
    assert "2026-10-15" in str(caught.value)
    assert "ambiguous" in str(caught.value)
