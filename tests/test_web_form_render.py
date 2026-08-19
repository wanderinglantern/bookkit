"""One FormSpec, two surfaces. The macro must render every field of every
builder — a kind it does not know renders nothing, and the form then saves
while dropping that field with no error anywhere."""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Callable

import pytest

from bookkit.forms import entities
from bookkit.forms.spec import Field, FormSpec
from bookkit.repo import interactions, orgs, placements, rfi, submissions
from bookkit.web.forms_render import render_cell, render_cell_display, render_form


def _spec_builders():
    for name, fn in vars(entities).items():
        if name.endswith("_form") or name.endswith("_form_initial_profile"):
            if inspect.isfunction(fn):
                yield name, fn


def test_there_are_builders_to_check():
    """Guards test_the_macro_renders_every_field_of_every_builder below: if
    discovery breaks, that test's real coverage collapses to zero silently
    (it walks `builders`, and an empty list means the for-loop never runs —
    the assertion below is what stops that from passing quietly)."""
    assert len(list(_spec_builders())) >= 18


def _stub_org(conn: sqlite3.Connection, kind: str = "client"):
    return orgs.create(conn, name=f"Stub {kind} {id(conn)}", kind=kind)


def _stub_submission(conn: sqlite3.Connection):
    """response_form(existing: Submission) needs a real row — a submission
    is a market's response to a placement, so build both first."""
    client = _stub_org(conn, "client")
    market = _stub_org(conn, "market")
    placement = placements.create(
        conn, client.id, "Stub Program", "2026-01-01", "2027-01-01"
    )
    return submissions.create(conn, market.id, "2026-01-01", placement_id=placement.id)


def _stub_rfi_item(conn: sqlite3.Connection):
    """rfi_answer_form(item) pre-fills from a real row — its one field is the
    item's own response."""
    org = _stub_org(conn)
    request = rfi.create_request(conn, org.id, "stub ask", "2026-01-01")
    return rfi.add_item(conn, request.id, "stub item")


def _stub_interaction(conn: sqlite3.Connection):
    org = _stub_org(conn)
    return interactions.log(
        conn, org.id, type="note", subject="stub", occurred_on="2026-01-01"
    )


# Every one of the builders discovered by _spec_builders, mapped to how it
# is actually called. Explicit rather than generic reflection: several take
# `conn`, some don't accept it at all, and three require a real model
# instance — getting any of those wrong should fail loudly, not fall back to
# a guess. A builder not in this map fails the completeness test by name.
_BUILD_CALLS: dict[str, Callable[[Callable, sqlite3.Connection], FormSpec]] = {
    "document_form": lambda build, conn: build(),
    "contact_form": lambda build, conn: build(),
    "project_form": lambda build, conn: build(),
    "org_form": lambda build, conn: build(conn=conn),
    "task_form": lambda build, conn: build(conn=conn),
    "placement_form": lambda build, conn: build(conn=conn),
    "opportunity_form": lambda build, conn: build(conn=conn),
    "member_form": lambda build, conn: build(conn=conn),
    "assignment_form": lambda build, conn: build(conn=conn),
    "appetite_form": lambda build, conn: build(conn=conn),
    "need_form": lambda build, conn: build(conn=conn),
    "request_form": lambda build, conn: build(conn=conn),
    "rfi_item_form": lambda build, conn: build(conn=conn),
    "rfi_answer_form": lambda build, conn: build(_stub_rfi_item(conn)),
    "submission_form": lambda build, conn: build(conn),
    "response_form": lambda build, conn: build(_stub_submission(conn), conn),
    # takes no conn: its only select is the status vocabulary, a models.py
    # tuple rather than anything read from the book
    "subjectivity_form": lambda build, conn: build(),
    "interaction_form": lambda build, conn: build(_stub_interaction(conn)),
    "org_form_initial_profile": lambda build, conn: build(conn, _stub_org(conn)),
}


def test_the_macro_renders_every_field_of_every_builder(conn: sqlite3.Connection):
    """The real completeness test. A hand-typed list of kind strings can
    drift from what the builders actually declare — this walks the builders
    themselves, so a new Field(..., kind="whatever") in any of them fails
    here, by name, instead of rendering nothing and saving anyway."""
    builders = list(_spec_builders())
    assert builders, "builder discovery found nothing — see test_there_are_builders_to_check"
    failures: list[str] = []
    for name, build in builders:
        call = _BUILD_CALLS.get(name)
        if call is None:
            failures.append(f"{name}: no stub construction registered — add one to _BUILD_CALLS")
            continue
        spec = call(build, conn)
        html = render_form(None, spec, action="/probe")
        for field in spec.fields:
            if f'name="{field.key}"' not in html:
                failures.append(
                    f"{name}: field {field.key!r} (kind={field.kind!r}) rendered no input"
                )
    assert not failures, "\n".join(failures)


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
    from bookkit.forms.spec import date_refusal

    spec = FormSpec("probe", [Field("due_on", "due", "date")])
    html = render_form(None, spec, action="/probe", error=f"due: {date_refusal('5')}")
    assert "is not a date" in html


def test_the_form_macro_renders_a_cancel_affordance():
    """Every add/edit form used to open with Save and nothing else to back
    out with — no Cancel, no Escape (found while clicking through the app,
    2026-08-18). Cancel must render so it cannot silently disappear again;
    inline-cell.js's delegated click/Escape handlers depend on this exact
    button being here to close the form without writing."""
    spec = FormSpec("probe", [Field("name", "name")])
    html = render_form(None, spec, action="/probe")
    assert "data-form-cancel" in html
    assert 'type="button"' in html


def test_cancel_is_not_a_submit_button():
    """Cancel must never post — it only clears the form (see
    inline-cell.js). A stray type="submit" here would fire Save's own
    hx-post instead of closing cleanly."""
    spec = FormSpec("probe", [Field("name", "name")])
    html = render_form(None, spec, action="/probe")
    cancel_start = html.index("data-form-cancel")
    tag_start = html.rindex("<button", 0, cancel_start)
    tag_end = html.index(">", tag_start)
    assert 'type="button"' in html[tag_start:tag_end]


def test_inline_field_sets_are_shared_not_duplicated(conn: sqlite3.Connection):
    """Which fields are editable in place is not a per-surface choice. The TUI
    screens build their column maps from these tuples; the web renders the same
    ones as cells. Tasks go through inline.task_fields(conn) — same fields, in
    the same order, with the category vocabulary attached."""
    from bookkit.forms import inline
    from bookkit.tui.screens.navigator import CONTACT_INLINE, task_inline

    assert tuple(CONTACT_INLINE.values()) == inline.CONTACT_FIELDS
    assert tuple(task_inline(conn).values()) == inline.task_fields(conn)
    assert (
        tuple(f.key for f in inline.task_fields(conn))
        == tuple(f.key for f in inline.TASK_FIELDS)
    )


def test_inline_task_category_completes_from_the_vocabulary(conn: sqlite3.Connection):
    """The inline cell is the PRIMARY edit path on both surfaces — `i` on the
    Open Items tab, click-the-cell on the web. A vocabulary wired only into
    the add/edit modal puts the discoverability mitigation everywhere except
    where the risk lives, so "Internal" is offered in the cell too: it is the
    one category that changes what leaves the building, and repo.vocab always
    carries it whether or not anyone has typed it."""
    from bookkit.forms import inline
    from bookkit.repo import orgs
    from bookkit.repo import tasks as tasks_repo

    org = orgs.create(conn, name="Vocab Co", kind="client")
    tasks_repo.create(conn, "renew GL", org_id=org.id, category="Renewal")

    by_key = {f.key: f for f in inline.task_fields(conn)}
    assert by_key["category"].suggestions == ("Internal", "Renewal")
    # only the category field — nothing else here has a vocabulary
    assert all(f.suggestions == () for k, f in by_key.items() if k != "category")
    # and the static tuple stays vocabulary-free: it is the column shape, and
    # a module-level constant cannot see a category typed a minute ago
    assert all(f.suggestions == () for f in inline.TASK_FIELDS)


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
    from bookkit.forms.spec import date_refusal

    field = Field("due_on", "due", "date")
    html = render_cell(None, field, value="5", action="/probe", error=date_refusal("5"))
    assert "a bare number is ambiguous" in html
    assert 'value="5"' in html


def test_render_cell_display_is_keyboard_reachable_and_fetches_its_editor():
    """The display half is the persistent state — one per row/column, always
    in the DOM — so it must be tab-reachable and activatable without a mouse,
    not just clickable. It fetches the editor from action + "/edit" and swaps
    its own outerHTML, so no listener from this cell survives activation."""
    field = Field("role", "role")
    html = render_cell_display(None, field, value="broker", action="/probe")
    assert 'tabindex="0"' in html
    assert 'hx-get="/probe/edit"' in html
    assert 'hx-swap="outerHTML"' in html
    assert "broker" in html


def test_render_cell_editor_has_exactly_one_autofocus():
    """Only one cell is ever being edited at a time; more than one autofocus
    on a page is invalid HTML and the browser picks arbitrarily."""
    field = Field("role", "role")
    html = render_cell(None, field, value="broker", action="/probe")
    assert html.count("autofocus") == 1


def test_render_cell_editor_and_display_both_swap_outerhtml():
    """Regression for the bubbling bug: an innerHTML swap on the display cell
    left its own click/Enter listener wrapped around the freshly-injected
    editor, so clicking into the input (or pressing Enter to commit) bubbled
    back up and re-fired the same fetch, discarding the typed value. Both
    halves must replace the whole <td>, listener included."""
    field = Field("role", "role")
    editor_html = render_cell(None, field, value="broker", action="/probe")
    display_html = render_cell_display(None, field, value="broker", action="/probe")
    assert 'hx-swap="outerHTML"' in editor_html
    assert 'hx-swap="outerHTML"' in display_html
    assert 'hx-swap="innerHTML"' not in editor_html
    assert 'hx-swap="innerHTML"' not in display_html


def test_render_cell_editor_reverts_on_escape():
    """Design doc, non-negotiable: 'Enter commits; Escape reverts to the
    rendered value.' Escape re-fetches the display cell from the base
    action (not action + "/edit") and replaces the whole editor <td>."""
    field = Field("role", "role")
    html = render_cell(None, field, value="broker", action="/probe")
    assert "keyup[key=='Escape']" in html
    assert 'hx-get="/probe"' in html


def test_render_cell_editor_input_has_an_accessible_name():
    """The column header supplies visual context; the fragment itself has
    none without this — form.html's render_field gets a <label>, the cell
    editor gets aria-label instead since there is no room for a label row."""
    field = Field("role", "role")
    html = render_cell(None, field, value="", action="/probe")
    assert 'aria-label="role"' in html


def test_the_editor_cell_has_no_declarative_focusout_trigger():
    """Fix round 2, 2026-08-17: blur-cancel used to be split across a
    declarative hx-trigger="focusout" on the editor AND a JS `committing`
    flag that was set and cleared but never actually consulted — so every
    commit's own removal-triggered focusout fired a spurious revert GET
    unconditionally, racing the save it sat right next to. Blur-cancel now
    lives entirely in inline-cell.js, where `committing` is genuinely read.
    Checked on the rendered macro OUTPUT (not the template source) so a
    source comment that happens to mention "focusout" can't make this pass
    by accident — Jinja comments never reach rendered output."""
    field = Field("role", "role")
    html = render_cell(None, field, value="broker", action="/probe")
    assert "focusout" not in html


def test_the_committing_guard_is_actually_read():
    """The bug fix round 2 caught: `committing` existed in inline-cell.js,
    set on submit and cleared on htmx:afterRequest, referenced nowhere else
    — a flag nothing ever consulted is not a guard, however confidently the
    surrounding comments describe it as one. This is a static/textual
    check, not a behavioural proof — this repo has no JS test harness to
    drive a real browser — but it is real: it fails if `committing` reverts
    to being set-and-forgotten, which is exactly the mistake that shipped."""
    import re
    from pathlib import Path

    import bookkit

    js = (Path(bookkit.__file__).parent / "web" / "static" / "inline-cell.js").read_text()
    assert re.search(r"if\s*\([^)]*\bcommitting\b[^)]*\)", js), (
        "committing is set/cleared but never read inside a conditional"
    )


def test_the_date_refusal_says_how_to_fix_it():
    """The old message ('cannot read a date from 5') named the objection, not
    the remedy, and mcpserver.py held four more copies of it that could (and
    did) drift from this one. date_refusal is the one function every surface
    calls, so it's tested once here rather than per call site."""
    import pytest as _pytest

    from bookkit.forms.spec import Field as F
    from bookkit.forms.spec import date_refusal
    from bookkit.forms.spec import parse_value as pv

    with _pytest.raises(ValueError) as caught:
        pv(F("due_on", "due", "date"), "5")
    assert str(caught.value) == date_refusal("5")
    assert "2026-10-15" in str(caught.value)
    assert "ambiguous" in str(caught.value)
    assert "'5'" in str(caught.value)  # the offending value, echoed
