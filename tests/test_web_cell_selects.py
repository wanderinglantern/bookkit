"""Every select an INLINE CELL renders offers "nothing chosen" first.

The same rule tests/test_form_selects.py asserts for macros/form.html, on the
other renderer. macros/cell.html had the identical defect and it was found
second: the blank option was gated on `field.optional_select or not
field.required`, so a REQUIRED select cell — `PLACEMENT_FIELDS.status` is the
one that ships — opened with option one already highlighted whenever the
stored value was empty.

Why this is subtler here than on a form, and why it still had to go:

  * blur commits on this surface, but the "unchanged value closes without
    writing" guard holds (inline-cell.js compares against `data-opened-with`,
    which equals the pre-selected value), so drifting past a cell reverted
    rather than wrote. Nothing was being saved silently.
  * what it DID do is misrepresent the record. A cell displaying an em-dash
    opened claiming a real status was chosen, and Enter or Tab from there
    COMMITS that claim — the user confirms an answer the browser wrote.

Convention test over the inline field tables rather than a test of one cell,
because the next select cell somebody adds is the one that would otherwise
bring it back.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.forms.spec import Field
from bookkit.web.app import create_app
from bookkit.web.forms_render import render_cell

MACRO = (
    Path(__file__).resolve().parents[1]
    / "src" / "bookkit" / "web" / "templates" / "macros" / "cell.html"
)


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o for o in orgs.list_orgs(conn, kind="client") if placements.for_org(conn, o.id)
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _options(html: str) -> list[str]:
    """Every <option> tag in a rendered editor, in document order."""
    return re.findall(r"<option\b[^>]*>", html)


def _select_fields() -> list[tuple[str, Field]]:
    """Every select-kind Field on an inline-cell surface, named by its table.

    Read off forms.inline's module-level tables rather than listed here, so a
    select added to one of them is covered the day it lands."""
    from bookkit.forms import inline

    found = []
    for table in dir(inline):
        if not table.endswith("_FIELDS"):
            continue
        fields = getattr(inline, table)
        if not isinstance(fields, tuple):
            continue
        for field in fields:
            if isinstance(field, Field) and field.kind == "select":
                found.append((f"{table}.{field.key}", field))
    return found


def test_the_scan_finds_the_cell_selects():
    """A convention test over an empty list passes for the wrong reason."""
    found = dict(_select_fields())
    assert found, "no select cells found; the scan is looking in the wrong place"
    # The one the defect actually shipped on: required, not optional_select.
    assert "PLACEMENT_FIELDS.status" in found
    assert found["PLACEMENT_FIELDS.status"].required is True
    assert found["PLACEMENT_FIELDS.status"].optional_select is False


@pytest.mark.parametrize("name,field", _select_fields(), ids=lambda v: getattr(v, "key", v))
def test_an_empty_select_cell_opens_with_nothing_chosen(name: str, field: Field):
    """An unset value must open unset. Without the blank option the browser
    selects option one and the cell shows a real answer the record does not
    hold."""
    options = _options(render_cell(None, field, "", f"/cell/{field.key}"))

    assert options, f"{name} rendered no options at all"
    assert 'value=""' in options[0], (
        f"{name} opens with a value the record does not hold: {options[0]}"
    )
    assert not any("selected" in option for option in options), (
        f"{name} pre-selects something for an empty cell"
    )


def test_a_required_select_still_refuses_an_empty_submit():
    """The blank option is not a licence to save nothing: `required` still
    rides on the select, which is the behaviour the TUI has always had."""
    field = Field("status", "status", "select", (("bound", "bound"),), required=True)
    editor = render_cell(None, field, "", "/cell/status")

    assert "required" in editor.split("</select>")[0]


def test_a_stored_value_is_still_the_selected_one():
    """The blank goes FIRST; it does not displace the record's own value."""
    field = Field("status", "status", "select",
                  (("bound", "bound"), ("lost", "lost")), required=True)
    options = _options(render_cell(None, field, "lost", "/cell/status"))

    assert 'value=""' in options[0]
    assert [o for o in options if "selected" in o] == ['<option value="lost" selected>']


def test_optional_selects_did_not_lose_their_blank():
    """The old condition was `optional_select or not required`. Making it
    unconditional must not have taken the blank away from the fields that
    already had it."""
    field = replace(
        Field("role", "role", "select", (("broker", "broker"),)),
        optional_select=True,
    )
    assert 'value=""' in _options(render_cell(None, field, "", "/cell/role"))[0]


def test_the_blank_option_is_not_behind_a_condition():
    """Anti-drift on the macro itself: the whole defect was one `{% if %}`."""
    source = MACRO.read_text()
    # comments explain the rule and must not be read as the rule
    source = re.sub(r"\{#.*?#\}", "", source, flags=re.S)

    select = source.split("<select")[1].split("</select>")[0]
    blank = '<option value=""></option>'
    assert blank in select, "the cell editor stopped offering 'nothing chosen'"
    before = select.split(blank)[0]
    assert "{%" not in before.rsplit(">", 1)[-1] + blank, (
        "the blank option is conditional again"
    )
    assert "optional_select" not in select, (
        "the blank option is gated on optional_select again"
    )


def test_the_placement_status_cell_offers_it_on_the_page(app_and_org):
    """End to end through the route the Program tab actually calls — a macro
    fixed in isolation is not a surface fixed."""
    client, org = app_and_org
    from bookkit.repo import placements

    placement = placements.for_org(client.app.state.conn, org.id)[0]
    editor = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/cell/status/edit"
    ).text

    assert editor.count("<select") == 1, "the status cell stopped being a picker"
    assert _options(editor)[0] == '<option value="">'
