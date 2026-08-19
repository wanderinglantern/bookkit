"""The MCP write surface is DERIVED from the form builders, minus a denylist.

Two things have to stay true or the whole change is worse than what it
replaced: a field added to a form must become editable without anyone editing
mcpserver, and a field on the denylist must stay denied when it does.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from bookkit import db, mcpserver, mcpsurface
from bookkit.forms import entities
from bookkit.forms.spec import Field
from bookkit.repo.base import ENTITY_TABLES


@pytest.fixture
def surface_db(tmp_path: Path) -> Path:
    path = tmp_path / "surface.db"
    db.connect(path).close()
    return path


def _form_builders() -> set[str]:
    """Every FormSpec builder forms/entities.py exposes, read off the module so
    the roster cannot go stale."""
    return {
        name
        for name, obj in vars(entities).items()
        if inspect.isfunction(obj)
        and obj.__module__ == entities.__name__
        and "FormSpec" in str(inspect.signature(obj).return_annotation)
    }


def _columns(path: Path, kind: str) -> set[str]:
    conn = db.connect(path)
    try:
        table = ENTITY_TABLES[kind]
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


# --- the roster: nothing lands silently ---------------------------------------


def test_every_form_builder_is_mapped_or_explicitly_unmapped():
    """A new entity form must not arrive silently editable OR silently
    unreachable. This is web/parity.py's rule, one layer up."""
    mapped = {builder.__name__ for builder in mcpsurface.BUILDERS.values()}
    accounted = mapped | set(mcpsurface.UNMAPPED_BUILDERS)
    missing = _form_builders() - accounted
    assert not missing, (
        f"FormSpec builders in neither mcpsurface.BUILDERS nor "
        f"UNMAPPED_BUILDERS: {sorted(missing)} — map it to an edit_field kind "
        "or write down why the assistant cannot edit that entity"
    )


def test_the_unmapped_roster_has_no_stale_entries():
    stale = set(mcpsurface.UNMAPPED_BUILDERS) - _form_builders()
    assert not stale, f"UNMAPPED_BUILDERS names builders that no longer exist: {sorted(stale)}"


def test_a_builder_is_not_both_mapped_and_unmapped():
    mapped = {builder.__name__ for builder in mcpsurface.BUILDERS.values()}
    assert not (mapped & set(mcpsurface.UNMAPPED_BUILDERS))


def test_every_editable_kind_has_a_resolver():
    """A kind is editable only if _edit_target can name a ref for it —
    otherwise edit_field advertises a kind and then refuses every ref with a
    message about the kind, which reads as a broken tool."""
    for kind in mcpsurface.BUILDERS:
        with pytest.raises(ValueError) as err:
            mcpserver._edit_target(db.connect(":memory:"), kind, "NOPE-1", "nobody")
        assert "cannot edit kind" not in str(err.value), (
            f"{kind} is in BUILDERS but _edit_target falls through on it"
        )


# --- the derivation only ever advertises real columns -------------------------


def test_every_editable_field_is_a_real_column(surface_db):
    """The bug this guards is on the record: edit_field advertised
    opportunity.notes, which is not a column, and failed at the DB layer on
    the first call."""
    for kind, fields in mcpsurface.editable().items():
        columns = _columns(surface_db, kind)
        unreal = set(fields) - columns
        assert not unreal, f"{kind} advertises non-columns: {sorted(unreal)}"


def test_the_market_profile_fields_are_form_fields_that_are_not_columns(surface_db):
    """NOT_A_COLUMN is documentary, so it has to be checked or it rots."""
    on_the_form = {f.key for f in entities.org_form().fields}
    columns = _columns(surface_db, "org")
    for (kind, field), reason in mcpsurface.NOT_A_COLUMN.items():
        assert kind == "org" and field in on_the_form
        assert field not in columns, f"{field} IS an org column now — delete the entry"
        assert reason.strip()


# --- the denylist itself ------------------------------------------------------


def test_every_denied_field_names_a_real_column(surface_db):
    """A typo in the denylist is a field silently NOT denied."""
    for (kind, field), reason in mcpsurface.DENIED.items():
        assert kind in mcpsurface.BUILDERS, f"{kind} is not an editable kind"
        assert field in _columns(surface_db, kind), f"{kind}.{field} is not a column"
        assert len(reason) > 40, f"{kind}.{field} has no real reason"


def test_every_denied_field_is_actually_absent_from_the_surface():
    surface = mcpsurface.editable()
    for kind, field in mcpsurface.DENIED:
        assert field not in surface[kind], f"{kind}.{field} is denied and still editable"


def test_every_also_editable_field_is_a_real_column_and_reachable(surface_db):
    """These two lines are the ONLY reason the derivation does not quietly
    remove a capability that exists today."""
    surface = mcpsurface.editable()
    assert set(mcpsurface.ALSO_EDITABLE) == {("org", "legal_name"), ("project", "notes")}
    for (kind, field), (vtype, reason) in mcpsurface.ALSO_EDITABLE.items():
        assert field in _columns(surface_db, kind)
        assert surface[kind][field] == vtype
        assert reason.strip()


def test_every_redirect_names_a_field_the_denylist_denies():
    """_EDIT_REDIRECTS says WHERE to go; mcpsurface.DENIED says WHY. If they
    disagree, a field is either redirected while editable or denied with no
    destination."""
    for kind, field in mcpserver._EDIT_REDIRECTS:
        assert (kind, field) in mcpsurface.DENIED, (
            f"{kind}.{field} is redirected but not denied"
        )


def test_foreign_keys_are_denied_by_rule():
    for kind, fields in mcpsurface.editable().items():
        keys = [f for f in fields if f.endswith("_id")]
        assert not keys, f"{kind} exposes foreign keys: {keys}"


def test_system_columns_are_denied_by_rule():
    for fields in mcpsurface.editable().values():
        assert not (set(fields) & mcpsurface.SYSTEM_COLUMNS)


# --- the point of the whole change -------------------------------------------


def test_a_new_form_field_becomes_editable_with_no_mcp_edit(monkeypatch, surface_db):
    """THE POINT. Add a Field(...) to a builder — nothing in mcpserver.py or
    mcpsurface.py changes — and the assistant can write it.

    The monkeypatch stands in for the source edit; recomputing _EDITABLE
    stands in for the restart that a real edit would need."""
    real = entities.member_form
    assert "specialty" in mcpsurface.editable()["team_member"]
    assert "active" not in mcpsurface.editable()["team_member"]

    def with_a_new_field(*args, **kwargs):
        spec = real(*args, **kwargs)
        # a real team_member column that no form declares today — the shape
        # of "someone added a field to the form"
        spec.fields.append(Field("notes", "handover notes", "textarea"))
        return spec

    monkeypatch.setitem(mcpsurface.BUILDERS, "team_member", with_a_new_field)
    surface = mcpsurface.editable()["team_member"]
    assert surface["notes"] == "textarea"
    # and nothing else moved
    assert "active" not in surface


def test_a_field_added_to_a_form_is_writable_end_to_end(monkeypatch, surface_db):
    """The same claim, proved through edit_field against a real database
    rather than against the dict."""
    from bookkit.repo import orgs

    real = entities.org_form

    def with_a_new_field(*args, **kwargs):
        spec = real(*args, **kwargs)
        # `legal_name` is a real org column that no form declares — exactly
        # the shape of "someone added a field to the form"
        spec.fields.append(Field("hq_city", "hq city", "text"))
        return spec

    monkeypatch.setitem(mcpsurface.BUILDERS, "org", with_a_new_field)
    monkeypatch.setattr(mcpserver, "_EDITABLE", mcpsurface.editable())  # a restart

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")
    out = mcpserver._edit_field(rw, "org", "Acme", "hq_city", "Chicago", expecting=None)
    assert out["edited"] is True
    assert orgs.find_by_name(rw, "Acme").hq_city == "Chicago"
    rw.close()


def test_a_denied_field_stays_denied_when_a_form_declares_it(monkeypatch, surface_db):
    """THE OTHER HALF. Someone puts `stage` on the opportunity form: the TUI
    and the web get it, and MCP does not, because the denylist considered it."""
    real = entities.opportunity_form

    def with_stage(*args, **kwargs):
        spec = real(*args, **kwargs)
        spec.fields.append(
            Field("stage", "stage", "select", (("won", "won"), ("lost", "lost")))
        )
        return spec

    monkeypatch.setitem(mcpsurface.BUILDERS, "opportunity", with_stage)
    surface = mcpsurface.editable()
    assert "stage" not in surface["opportunity"]

    monkeypatch.setattr(mcpserver, "_EDITABLE", surface)
    rw = db.connect(surface_db)
    from bookkit.repo import orgs

    org = orgs.create(rw, name="Acme", kind="client")
    created = mcpserver._opportunity_create(rw, "Acme", "Acme cyber")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "opportunity", created["opportunity_ref"], "stage", "won",
            expecting="identified",
        )
    assert "opportunity_stage" in str(err.value)
    assert org  # the account is untouched
    rw.close()


def test_a_new_foreign_key_on_a_form_stays_denied(monkeypatch):
    """The rule, not a per-field line: a form that grows an FK does not widen
    the write surface."""
    real = entities.task_form

    def with_an_fk(*args, **kwargs):
        spec = real(*args, **kwargs)
        spec.fields.append(Field("placement_id", "placement", "text"))
        return spec

    monkeypatch.setitem(mcpsurface.BUILDERS, "task", with_an_fk)
    assert "placement_id" not in mcpsurface.editable()["task"]


# --- the widening Grant agreed to, asserted so it cannot be lost --------------


def test_the_fields_the_derivation_newly_reaches():
    """The behaviour change, written down. If one of these disappears, either
    a form lost a field or someone added a denylist line — both worth a
    conversation, neither worth a silent regression."""
    surface = mcpsurface.editable()
    assert surface["org"]["name"] == "text"
    assert surface["org"]["status"] == (
        "prospect", "active", "dormant", "lost", "declined",
    )
    assert "risk_manager" in surface["contact"]["role"]
    assert surface["task"]["priority"] == ("1", "2", "3")
    assert surface["rfi_item"]["detail"] == "textarea"
    assert surface["rfi_item"]["kind"] == ("question", "document")
    assert surface["rfi_request"]["requested_on"] == "date"


def test_a_select_refuses_a_value_outside_its_vocabulary(surface_db):
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(rw, "org", "Acme", "status", "banana", expecting="prospect")
    assert "must be one of" in str(err.value)
    rw.close()


def test_an_account_can_be_renamed_and_moved_to_active(surface_db):
    """The two writes the audit found an assistant could not make, because
    _editable()['org'] was defined as a copy of the enrich map."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")
    mcpserver._edit_field(rw, "org", "Acme", "status", "active", expecting="prospect")
    mcpserver._edit_field(rw, "org", "Acme", "name", "Acme Industries", expecting="Acme")
    found = orgs.find_by_name(rw, "Acme Industries")
    assert found.status == "active"
    rw.close()


# --- enrich runs over the same set, with the stricter guard ------------------


def test_enrich_and_edit_run_over_the_same_derived_set():
    surface = mcpsurface.editable()
    assert mcpserver._ENRICHABLE_ORG == surface["org"]
    assert mcpserver._ENRICHABLE_CONTACT == surface["contact"]


def test_enrich_refuses_a_value_outside_a_derived_vocabulary(surface_db):
    """The widening put closed vocabularies (contact.role) into the enrich map
    for the first time. _clean_by_kind would have fallen through to clean_text
    and written a role outside the list."""
    from bookkit.repo import contacts, orgs

    rw = db.connect(surface_db)
    org = orgs.create(rw, name="Acme", kind="client")
    contacts.create(rw, org.id, first_name="Ann", last_name="Lee")
    with pytest.raises(ValueError) as err:
        mcpserver._enrich_field(rw, "Acme", "role", "chief vibes officer", contact="Ann Lee")
    assert "must be one of" in str(err.value)
    mcpserver._enrich_field(rw, "Acme", "role", "cfo", contact="Ann Lee")
    assert contacts.for_org(rw, org.id)[0].role == "cfo"
    rw.close()


# --- discovery ----------------------------------------------------------------


def test_describe_reports_exactly_what_edit_field_enforces():
    described = mcpsurface.describe()
    surface = mcpsurface.editable()
    assert set(described["kinds"]) == set(surface)
    for kind, fields in surface.items():
        assert set(described["kinds"][kind]["fields"]) == set(fields)
    assert "select" == described["kinds"]["org"]["fields"]["status"]["type"]
    assert "lost" in described["kinds"]["org"]["fields"]["status"]["values"]


def test_describe_names_the_entities_that_are_not_editable():
    described = mcpsurface.describe()
    assert set(described["not_editable"]) >= {
        "placement", "submission", "document", "appetite", "interaction",
    }
    for reason in described["not_editable"].values():
        assert len(reason) > 40


def test_describe_refuses_an_unknown_kind_by_naming_the_known_ones():
    with pytest.raises(ValueError) as err:
        mcpsurface.describe("placement")
    message = str(err.value)
    assert "towerkit" in message and "org" in message


def test_describe_is_registered_as_a_read_tool(tmp_path):
    server = mcpserver.build_server(tmp_path / "d.db")
    names = {t.name for t in server._tool_manager.list_tools()}
    assert "describe" in names


# --- the duplicate table cannot come back ------------------------------------


def test_mcpserver_keeps_no_second_field_table():
    """The sibling of test_mcp_has_no_second_cleaner_map. The hand-written
    table is what rotted into `dict(_ENRICHABLE_ORG)`."""
    source = (Path(mcpserver.__file__)).read_text()
    assert "def _editable()" not in source, "the hand-written field table is back"
    assert '_ENRICHABLE_ORG = {' not in source
    assert '_ENRICHABLE_CONTACT = {' not in source
