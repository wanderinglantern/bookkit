"""The PRIMARY edit path gets the same vocabulary as the secondary one.

CLAUDE.md and forms/inline.py both say the inline cell is the primary way a
value is changed on both surfaces; the add/edit modal is the secondary one.
Where the modal constrained a field and the cell did not, the path people
actually use was the weaker one:

* `contact.role` — a select over CONTACT_ROLES in `contact_form`, free text in
  the cell, and nothing at all in the DB.
* `rfi_item.category` — completed from `vocab.rfi_categories` in
  `rfi_item_form`, no suggestions in the cell.

Every test here asserts the vocabulary reaches THE RENDERED EDITOR, not just
the builder that could supply one. A green suite proves nothing broke, not
that the new path is taken (CLAUDE.md) — and the failure mode being guarded
against is exactly a vocabulary that exists in forms/inline.py and is never
read by the route or the screen that renders the cell.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import db, seed
from bookkit.forms import inline
from bookkit.models import CONTACT_ROLES, PlacementStatus
from bookkit.repo import contacts as contacts_repo
from bookkit.repo import orgs, vocab
from bookkit.repo import rfi as rfi_repo
from bookkit.web.app import create_app

# a role the declared vocabulary does NOT contain — everything below that
# tests "stranding nothing already typed" uses this one.
LEGACY_ROLE = "safety_director"


# --- repo/vocab ---------------------------------------------------------------


def test_contact_roles_offers_the_declared_vocabulary_on_an_empty_book(
    conn: sqlite3.Connection,
) -> None:
    """Without the declared half a fresh book offers nothing and `role` is a
    text box again — nobody discovers "broker_of_record" exists."""
    assert vocab.contact_roles(conn) == sorted(CONTACT_ROLES, key=str.lower)


def test_contact_roles_keeps_a_role_the_book_already_uses(
    conn: sqlite3.Connection,
) -> None:
    """Without the book half the picker would REFUSE a role already stored.
    checked_option is authoritative on the way in, so a bare select over
    CONTACT_ROLES makes such a contact unsaveable (Grant, 2026-08-20)."""
    org = orgs.create(conn, name="Atomic Industries", kind="client")
    contacts_repo.create(conn, org.id, first_name="Dana", last_name="Reed",
                         role=LEGACY_ROLE)
    roles = vocab.contact_roles(conn)
    assert LEGACY_ROLE in roles
    assert set(CONTACT_ROLES) <= set(roles)


def test_contact_roles_does_not_double_an_existing_spelling(
    conn: sqlite3.Connection,
) -> None:
    """_dedupe folds case and the BOOK's spelling comes first, so a stored
    role appears in the options exactly as stored — which is what a <select>
    needs to pre-select it."""
    org = orgs.create(conn, name="Atomic Industries", kind="client")
    contacts_repo.create(conn, org.id, first_name="Dana", last_name="Reed",
                         role="CFO")
    roles = vocab.contact_roles(conn)
    assert "CFO" in roles
    assert "cfo" not in roles


def test_contact_titles_complete_from_the_book(conn: sqlite3.Connection) -> None:
    org = orgs.create(conn, name="Atomic Industries", kind="client")
    contacts_repo.create(conn, org.id, first_name="Dana", last_name="Reed",
                         title="VP, Risk Management")
    assert vocab.contact_titles(conn) == ["VP, Risk Management"]


# --- forms/inline -------------------------------------------------------------


def test_contact_role_cell_is_a_picker_widened_by_the_book(
    conn: sqlite3.Connection,
) -> None:
    org = orgs.create(conn, name="Atomic Industries", kind="client")
    contacts_repo.create(conn, org.id, first_name="Dana", last_name="Reed",
                         role=LEGACY_ROLE)
    role = {f.key: f for f in inline.contact_fields(conn)}["role"]
    assert role.kind == "select"
    assert (LEGACY_ROLE, LEGACY_ROLE) in role.options
    assert ("cfo", "cfo") in role.options
    # the TUI's cell editor is a one-line Input for every kind, so the same
    # list has to arrive as suggestions or the terminal is the weaker surface
    assert LEGACY_ROLE in role.suggestions


def test_a_stored_role_survives_its_own_parser(conn: sqlite3.Connection) -> None:
    """The failure this whole design exists to prevent: a picker that refuses
    a value already on the record."""
    from bookkit.forms.spec import parse_value

    org = orgs.create(conn, name="Atomic Industries", kind="client")
    contacts_repo.create(conn, org.id, first_name="Dana", last_name="Reed",
                         role=LEGACY_ROLE)
    role = {f.key: f for f in inline.contact_fields(conn)}["role"]
    assert parse_value(role, LEGACY_ROLE) == LEGACY_ROLE
    with pytest.raises(ValueError):
        parse_value(role, "not-a-role")


def test_contact_title_gets_suggestions_not_options(
    conn: sqlite3.Connection,
) -> None:
    """A title is prose off a signature block: the valid set is not knowable,
    so a select would refuse the next real one."""
    org = orgs.create(conn, name="Atomic Industries", kind="client")
    contacts_repo.create(conn, org.id, first_name="Dana", last_name="Reed",
                         title="VP, Risk Management")
    title = {f.key: f for f in inline.contact_fields(conn)}["title"]
    assert title.kind == "text"
    assert title.options == ()
    assert title.suggestions == ("VP, Risk Management",)


def test_rfi_item_category_cell_completes_from_the_book(
    conn: sqlite3.Connection,
) -> None:
    org = orgs.create(conn, name="Atomic Industries", kind="client")
    request = rfi_repo.create_request(conn, org.id, "Loss run refresh", "2026-08-10")
    rfi_repo.add_item(conn, request.id, "loss runs", category="Financials")
    by_key = {f.key: f for f in inline.rfi_item_fields(conn)}
    assert by_key["category"].suggestions == ("Financials",)
    # only the category — a stray vocabulary elsewhere means the enrichment is
    # being applied by position rather than by key
    assert all(f.suggestions == () for k, f in by_key.items() if k != "category")
    # and the static tuple stays vocabulary-free: it is the column shape, and
    # a module-level constant cannot see a group typed a minute ago
    assert all(f.suggestions == () for f in inline.RFI_ITEM_FIELDS)


# --- the third copy of the placement statuses ---------------------------------


def test_the_tui_placement_edit_form_reads_the_enum(conn: sqlite3.Connection) -> None:
    """entity_actions.py spelled the five placement statuses out as a string
    tuple — a THIRD copy beside models.PlacementStatus and entities.py's own
    literal. It now builds from forms.inline.PLACEMENT_FIELDS, which builds
    from the enum."""
    import inspect

    from bookkit.tui.widgets import entity_actions

    source = inspect.getsource(entity_actions)
    assert "prospective" not in source, "the literal is back"

    status = {f.key: f for f in inline.PLACEMENT_FIELDS}["status"]
    assert status.options == tuple((s.value, s.value) for s in PlacementStatus)


# --- the web, end to end ------------------------------------------------------


@pytest.fixture
def web(tmp_path: Path):
    path = tmp_path / "vocab.db"
    connection = db.connect(path)
    seed.seed(connection, today=date.today(), programs_dir=tmp_path / "programs")
    connection.close()

    app = create_app(path)
    conn = app.state.conn
    org = orgs.list_orgs(conn, kind="client")[0]
    contact = contacts_repo.create(
        conn, org.id, first_name="Dana", last_name="Reed", role=LEGACY_ROLE
    )
    request = rfi_repo.create_request(conn, org.id, "Loss run refresh", "2026-08-10")
    item = rfi_repo.add_item(conn, request.id, "loss runs", category="Financials")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org, contact, request, item


def test_the_web_role_cell_renders_a_picker_carrying_the_book(web) -> None:
    """THE SEAM, end to end: the rendered editor is a <select>, its options
    are the widened vocabulary, and the contact's own legacy role is one of
    them and pre-selected. Rendering it from the module-level CONTACT_FIELDS
    would produce the eleven declared roles and no selection at all."""
    client, org, contact, _request, _item = web
    response = client.get(f"/accounts/{org.ref}/contacts/{contact.id}/cell/role/edit")
    assert response.status_code == 200
    assert "<select" in response.text
    assert f'<option value="{LEGACY_ROLE}" selected>' in response.text
    assert '<option value="cfo"' in response.text
    # EVERY SELECT RENDERS A BLANK OPTION (data-entry-integrity §2): role is
    # optional and a browser must not pre-pick row one on an unset contact
    assert '<option value=""></option>' in response.text


def test_the_web_role_cell_saves_a_role_only_the_book_knows(web) -> None:
    """checked_option is authoritative on the POST too, so the save path has
    to rebuild the field from the same query the GET did. Parsing against the
    declared eleven refuses this and the cell comes back with an error — a
    contact unable to re-save its own stored value."""
    client, org, contact, _request, _item = web
    response = client.post(
        f"/accounts/{org.ref}/contacts/{contact.id}/cell/role",
        data={"role": LEGACY_ROLE},
    )
    assert response.status_code == 200
    assert "cell-error" not in response.text
    assert contacts_repo.get(client.app.state.conn, contact.id).role == LEGACY_ROLE


def test_the_web_role_cell_refuses_a_role_nobody_offered(web) -> None:
    """The picker constrains new entry: the markup constrains a mouse and
    nothing else, so the route checks it server-side and writes nothing."""
    client, org, contact, _request, _item = web
    response = client.post(
        f"/accounts/{org.ref}/contacts/{contact.id}/cell/role",
        data={"role": "chief vibes officer"},
    )
    assert response.status_code == 200
    assert "cell-error" in response.text
    assert contacts_repo.get(client.app.state.conn, contact.id).role == LEGACY_ROLE


def test_the_web_title_cell_offers_the_books_titles(web) -> None:
    client, org, contact, _request, _item = web
    conn = client.app.state.conn
    contacts_repo.update(conn, contact.id, title="VP, Risk Management")
    response = client.get(f"/accounts/{org.ref}/contacts/{contact.id}/cell/title/edit")
    assert response.status_code == 200
    assert 'list="cl-title"' in response.text
    assert '<option value="VP, Risk Management">' in response.text


def test_the_web_rfi_category_cell_offers_the_books_groups(web) -> None:
    """The mirror of the task category cell, which has had this since it was
    written."""
    client, org, _contact, request, item = web
    response = client.get(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/cell/category/edit"
    )
    assert response.status_code == 200
    assert 'list="cl-category"' in response.text
    assert '<option value="Financials">' in response.text


def test_a_non_vocabulary_rfi_cell_carries_no_datalist(web) -> None:
    """Only `category` has a vocabulary — a datalist on `prompt` would mean
    the enrichment is applied by position, not by key."""
    client, org, _contact, request, item = web
    response = client.get(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/cell/prompt/edit"
    )
    assert response.status_code == 200
    assert "datalist" not in response.text


def test_the_rfi_editable_set_guard_still_runs_before_the_query(web) -> None:
    """`status` is deliberately not inline-editable (apply_rfi_item owns the
    status/received_on pair). The vocabulary lookup must not become a way to
    reach a field the cell contract does not expose."""
    client, org, _contact, request, item = web
    assert client.get(
        f"/accounts/{org.ref}/requests/{request.id}/items/{item.id}/cell/status/edit"
    ).status_code == 404


# --- the TUI, end to end ------------------------------------------------------


@pytest.fixture
def tui_db(tmp_path: Path) -> Path:
    path = tmp_path / "tui-vocab.db"
    conn = db.connect(path)
    seed.seed(conn, today=date.today(), programs_dir=tmp_path / "programs")
    conn.close()
    return path


async def test_the_tui_contact_cell_carries_the_widened_picker(tui_db: Path) -> None:
    """The screen's own `inline_fields` — what InlineTable reads when `i`
    opens an editor — carries the book-widened role. A module-level column map
    built once at import could only ever offer the declared eleven, and the
    legacy role would be unsaveable from the terminal."""
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.inline_edit import CellEditor, InlineTable

    app = BookkitApp(tui_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    contact = contacts_repo.create(
        app.conn, org.id, first_name="Dana", last_name="Reed", role=LEGACY_ROLE
    )
    async with app.run_test(size=(160, 48)) as pilot:
        nav = app.screen
        nav._current = ("group", ("contacts", org.id))
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", InlineTable)
        role = table.inline_fields[2]
        assert role.key == "role"
        assert (LEGACY_ROLE, LEGACY_ROLE) in role.options
        assert LEGACY_ROLE in role.suggestions

        # and the editor that opens over the cell completes from it
        table.focus()
        table.move_cursor(row=table.get_row_index(f"contact:{contact.id}"))
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        editor = nav.query_one(CellEditor)
        assert editor.value == LEGACY_ROLE
        assert editor.suggester is not None
        # committing the value it was pre-filled with must not be refused
        await pilot.press("enter")
        await pilot.pause()
        assert contacts_repo.get(app.conn, contact.id).role == LEGACY_ROLE


async def test_the_tui_rfi_group_cell_completes_from_the_book(tui_db: Path) -> None:
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.inline_edit import InlineTable

    app = BookkitApp(tui_db)
    org = orgs.list_orgs(app.conn, kind="client")[0]
    request = rfi_repo.create_request(
        app.conn, org.id, "Loss run refresh", "2026-08-10"
    )
    rfi_repo.add_item(app.conn, request.id, "loss runs", category="Financials")
    async with app.run_test(size=(160, 48)) as pilot:
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("9")
        await pilot.pause()
        items = app.screen.query_one("#rfi-items", InlineTable)
        group = items.inline_fields[3]
        assert group.key == "category"
        assert "Financials" in group.suggestions
