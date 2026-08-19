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


def test_every_not_a_column_entry_is_a_form_field_and_not_a_column(surface_db):
    """NOT_A_COLUMN is documentary, so it has to be checked or it rots. Was
    hardcoded to org (market_type / am_best_rating) and had to be widened
    when task.assignee joined it — the roster is per (kind, field), not a
    list of org's oddities."""
    for (kind, field), reason in mcpsurface.NOT_A_COLUMN.items():
        assert kind in mcpsurface.BUILDERS, f"{kind} is not an editable kind"
        on_the_form = {f.key for f in mcpsurface.BUILDERS[kind]().fields}
        assert field in on_the_form, (
            f"{kind}.{field} is documented as a non-column but no form "
            "declares it — delete the entry"
        )
        assert field not in _columns(surface_db, kind), (
            f"{kind}.{field} IS a column now — delete the entry"
        )
        assert len(reason) > 40, f"{kind}.{field} has no real reason"


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


def test_a_denied_field_is_refused_with_its_reason(surface_db):
    """A refusal says something. "not editable; allowed: [...]" tells a model
    nothing about whether to look for another door or stop asking; the reason
    from the denylist does."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(rw, "org", "Acme", "kind", "market", expecting="client")
    message = str(err.value)
    assert "not editable" in message
    assert "markets list" in message, "the refusal dropped the denylist reason"

    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "org", "Acme", "parent_org_id", "somewhere", expecting=None
        )
    assert "Foreign keys re-scope" in str(err.value)
    rw.close()


def test_system_columns_are_denied_by_rule(monkeypatch):
    """No form declares one today, so this only means anything against a form
    that does — which is exactly what the rule is FOR. Asserted on the empty
    set the rule was a no-op nothing would have noticed the loss of."""
    for fields in mcpsurface.editable().values():
        assert not (set(fields) & mcpsurface.SYSTEM_COLUMNS)

    real = entities.project_form

    def with_bookkeeping(*args, **kwargs):
        spec = real(*args, **kwargs)
        for column in sorted(mcpsurface.SYSTEM_COLUMNS):
            spec.fields.append(Field(column, column, "text"))
        return spec

    monkeypatch.setitem(mcpsurface.BUILDERS, "project", with_bookkeeping)
    surface = mcpsurface.editable()["project"]
    assert not (set(surface) & mcpsurface.SYSTEM_COLUMNS), (
        f"a form declaring a bookkeeping column made it writable: "
        f"{sorted(set(surface) & mcpsurface.SYSTEM_COLUMNS)}"
    )


def test_a_form_field_that_is_not_a_column_never_reaches_the_surface(monkeypatch):
    """The belt behind the denylist. A field can be on a form and stored
    somewhere else entirely — org.market_type lives on market_profile — and
    advertising one means edit_field passes its own allowlist check and then
    fails at the DB layer, which is how opportunity.notes shipped. Deriving
    against the row model catches the case nobody wrote down."""
    real = entities.project_form

    def with_a_non_column(*args, **kwargs):
        spec = real(*args, **kwargs)
        spec.fields.append(Field("square_footage", "square footage", "int"))
        return spec

    monkeypatch.setitem(mcpsurface.BUILDERS, "project", with_a_non_column)
    assert "square_footage" not in mcpsurface.editable()["project"]


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


def test_describe_names_the_denied_fields_and_where_to_go_instead():
    described = mcpsurface.describe("task")
    assert "task.status" in described["denied_fields"]
    assert "task_complete" in described["denied_fields"]["task.status"]
    assert "task.priority" not in described["denied_fields"]


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


# =============================================================================
# The three defects the derivation shipped with, and the assertions that stop
# each from coming back. Everything below was reproduced before it was fixed.
# =============================================================================


# --- CRITICAL 1: a rename could point the resolver at the wrong account -------


def test_renaming_an_account_onto_a_name_in_use_is_refused(surface_db):
    """THE REVIEWER'S REPRODUCTION. Rename Acme to "Henderson Group" while a
    real Henderson Group exists and `_resolve_client("Henderson Group")` came
    back with ACC-0001 — the renamed Acme — so every later client-scoped tool
    landed on the wrong account. Verbatim the failure CLAUDE.md records for
    two colleagues sharing a name."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    acme = orgs.create(rw, name="Acme", kind="client")
    henderson = orgs.create(rw, name="Henderson Group", kind="client")

    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(
            rw, "org", "Acme", "name", "Henderson Group", expecting="Acme"
        )
    assert "already holds that name" in str(err.value)

    # nothing was written, and the resolver still finds the RIGHT account
    assert orgs.get(rw, acme.id).name == "Acme"
    assert mcpserver._resolve_client(rw, "Henderson Group").id == henderson.id
    rw.close()


def test_the_rename_guard_is_case_insensitive(surface_db):
    """The resolver's fallbacks are not case-sensitive to a human reading a
    list, so neither is the guard."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")
    orgs.create(rw, name="Henderson Group", kind="client")
    with pytest.raises(ValueError, match="already holds that name"):
        mcpserver._edit_field(
            rw, "org", "Acme", "name", "henderson group", expecting="Acme"
        )
    rw.close()


def test_the_guard_lives_in_the_repo_so_the_tui_and_web_inherit_it(surface_db):
    """WHERE THE GUARD LIVES IS THE POINT. Both other surfaces rename through
    forms.entities.apply_org -> orgs.update, never through mcpserver, so a
    guard in _edit_field would have left the same hole open on the terminal
    and the web. This is repo/team._guard_name's story one table over."""
    from bookkit.forms import entities
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    acme = orgs.create(rw, name="Acme", kind="client")
    orgs.create(rw, name="Henderson Group", kind="client")

    with pytest.raises(ValueError, match="already holds that name"):
        entities.apply_org(
            rw, {"name": "Henderson Group", "kind": "client"}, existing=acme
        )
    with pytest.raises(ValueError, match="already holds that name"):
        orgs.update(rw, acme.id, name="Henderson Group")
    rw.close()


def test_an_account_can_still_be_renamed_to_a_free_name(surface_db):
    """The guard refuses a collision, not a rename — renaming an account is
    one of the two writes the derivation exists to allow."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    acme = orgs.create(rw, name="Acme", kind="client")
    mcpserver._edit_field(
        rw, "org", "Acme", "name", "Acme Industries", expecting="Acme"
    )
    assert orgs.get(rw, acme.id).name == "Acme Industries"
    # and renaming a row to the name it already holds is not a collision
    orgs.update(rw, acme.id, name="Acme Industries", owner="Dana")
    rw.close()


def test_creating_a_duplicate_name_is_still_allowed(surface_db):
    """DELIBERATE ASYMMETRY, asserted so it is not read as a hole. Duplicate
    orgs arrive from the spreadsheet importer, sync's carrier auto-create and
    seed; the cure there is services.merge, not an exception half-way through
    an import. The MCP create door has its own rapidfuzz guard, where a human
    is on the other end. What is guarded is taking a name AWAY from the row
    that answers to it."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="AXA XL", kind="market")
    orgs.create(rw, name="Axa XL", kind="market")  # merge_markets exists for this
    rw.close()


# --- CRITICAL 2: a form's value type must fit the model's column -------------


def test_every_editable_value_type_survives_the_trip_to_its_column():
    """THE SYSTEMIC ASSERTION. task.priority shipped as a select of strings
    over an int column: _edit_field compared the stored int 2 against the
    cleaned str '2' and refused every write with

        task.priority holds 2, not what you expected ('2')

    two values identical to a reader, so a model retries forever. The
    derivation never checked that a form's value type was compatible with the
    model's column type. It does now — and when the next mismatch appears
    this fails by NAME, before anyone tries to write the field."""
    bad = []
    for kind, fields in mcpsurface.editable().items():
        for field, vtype in fields.items():
            produced = mcpsurface.produced_type(vtype)
            stored = mcpsurface.column_type(kind, field)
            if produced is not stored:
                bad.append(f"{kind}.{field}: cleans to {produced.__name__}, "
                           f"column stores {stored.__name__} (vtype={vtype!r})")
    assert not bad, (
        "the write surface advertises a value type its own column will not "
        "take — compare-and-set will refuse every write with two values a "
        "reader cannot tell apart:\n  " + "\n  ".join(bad)
    )


def test_a_select_over_an_int_column_is_reconciled_not_just_priority(monkeypatch):
    """The fix is on the derivation, not on one field. Any select whose column
    is an int becomes IntChoices and cleans to an int."""
    real = entities.opportunity_form

    def with_an_int_select(*args, **kwargs):
        spec = real(*args, **kwargs)
        # probability_pct is a real int column that no select declares today
        spec.fields.append(
            Field("probability_pct", "probability", "select",
                  (("25", "25"), ("50", "50"), ("75", "75")))
        )
        return spec

    monkeypatch.setitem(mcpsurface.BUILDERS, "opportunity", with_an_int_select)
    vtype = mcpsurface.editable()["opportunity"]["probability_pct"]
    assert isinstance(vtype, mcpsurface.IntChoices)
    assert mcpserver._clean_typed(vtype, "probability_pct", "50") == 50


def test_task_priority_is_writable_at_all(surface_db):
    """THE REVIEWER'S REPRODUCTION. expecting=None was refused as not-blank
    and every legal expecting was refused as a mismatch, so the field was
    unreachable in both directions."""
    from bookkit.repo import orgs
    from bookkit.repo import tasks as tasks_repo

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")
    created = mcpserver._task_create(rw, "Chase the cert", client="Acme")
    out = mcpserver._edit_field(
        rw, "task", created["task_ref"], "priority", "1", expecting="2"
    )
    assert out["edited"] is True
    stored = tasks_repo.get(rw, created["task_ref"]).priority
    assert stored == 1 and isinstance(stored, int)
    rw.close()


def test_priority_still_refuses_a_value_outside_its_vocabulary(surface_db):
    """The int coercion must not become a bare int(): the select is still a
    closed vocabulary, so `9` and `banana` are both refused."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")
    created = mcpserver._task_create(rw, "Chase the cert", client="Acme")
    for junk in ("9", "banana", "2.0"):
        with pytest.raises(ValueError, match="must be one of"):
            mcpserver._edit_field(
                rw, "task", created["task_ref"], "priority", junk, expecting="2"
            )
    rw.close()


def test_priority_is_still_described_as_a_select_of_the_same_values():
    """The reconciliation is invisible to a model: it still passes a string
    from the list describe prints."""
    described = mcpsurface.describe("task")["kinds"]["task"]["fields"]
    assert described["priority"] == {"type": "select", "values": ["1", "2", "3"]}


# --- CRITICAL 3: one owner for the value rules -------------------------------


def test_describe_says_money_is_entered_in_dollars():
    """describe said "Money is cents" while edit_field's docstring said
    dollars, and describe is the one a model is told to call FIRST — so a
    model wanting a $5,000,000 limit passed 500000000 and wrote
    $500,000,000."""
    note = mcpsurface.describe()["note"]
    assert "DOLLARS" in note
    assert "is cents" not in note
    # the parser is what the sentence has to be true ABOUT
    from bookkit.money import parse_money_cents

    assert parse_money_cents("500000") == 50_000_000  # $500,000, in cents
    assert parse_money_cents("2m") == 200_000_000


def test_edit_field_and_describe_cannot_disagree_about_a_value_again(tmp_path):
    """ONE SOURCE OWNS IT. edit_field is registered with
    mcpsurface.VALUE_RULES interpolated into its description, and describe
    serves the same string as its note — so there is no second copy to rot."""
    server = mcpserver.build_server(tmp_path / "rules.db")
    tools = {tool.name: (tool.description or "") for tool in
             server._tool_manager.list_tools()}
    assert mcpsurface.VALUE_RULES in tools["edit_field"]
    assert mcpsurface.describe()["note"] == mcpsurface.VALUE_RULES
    assert "money as dollars, dates as displayed" not in tools["edit_field"], (
        "the hand-written money clause is back beside the interpolated one"
    )


# --- IMPORTANT 4: the fall-through set, and the move-together columns --------


def test_no_form_field_falls_through_the_derivation_undocumented():
    """A form field that is not a column of its entity is dropped with a bare
    `continue`. NOT_A_COLUMN documents that case; nothing asserted it was
    COMPLETE, and exactly one field fell through undocumented — task.assignee,
    so a task could be assigned in the TUI and on the web but not through MCP,
    with no denylist line, no ledger cell and no describe output saying so."""
    stray = mcpsurface.unexplained_non_columns()
    assert not stray, (
        f"form fields MCP silently cannot write: {stray} — a capability the "
        "other two surfaces have and this one does not is a decision. Put "
        "each in mcpsurface.NOT_A_COLUMN with the reason, and say in "
        "mcpparity which verb would close it."
    )


def test_the_assignee_is_documented_rather_than_silently_dropped():
    assert ("task", "assignee") in mcpsurface.NOT_A_COLUMN
    reason = mcpsurface.denial_reason("task", "assignee")
    assert "repo/assignees" in reason and "three" in reason.lower()
    assert "assignee" not in mcpsurface.editable()["task"]


def test_describe_says_why_a_field_it_does_not_list_is_missing():
    """The third place that said nothing. A model reading describe("task")
    saw no `assignee` and no reason, while both other surfaces offer it —
    so the answer has to be where it looks, not only in the source."""
    denied = mcpsurface.describe("task")["denied_fields"]
    assert "task.assignee" in denied
    assert "repo/assignees" in denied["task.assignee"]
    assert "org.am_best_rating" in mcpsurface.describe("org")["denied_fields"]
    # and every field on a form that MCP will not write is answered for
    for kind, builder in mcpsurface.BUILDERS.items():
        answered = mcpsurface.describe(kind)["denied_fields"]
        surface = mcpsurface.editable()[kind]
        for field in builder().fields:
            if field.key in surface:
                continue
            assert f"{kind}.{field.key}" in answered or field.key.endswith("_id"), (
                f"{kind}.{field.key} is on a form, is not writable, and "
                "describe gives no reason"
            )


def test_the_assignee_triple_is_denied_by_name_not_by_accident(monkeypatch):
    """models.Task: "never set them field by field, or a stale id can outlive
    a kind and the pair stops meaning anything". Only assignee_id was denied,
    and only because it happens to end in `_id`; a single
    Field("assignee_name", ...) on any form would have made the other two
    writable and produced exactly that corruption."""
    for column in ("assignee_kind", "assignee_id", "assignee_name"):
        reason = mcpsurface.denial_reason("task", column)
        assert reason is not None, f"task.{column} is not denied"
        assert "three" in reason.lower(), (
            f"task.{column} is denied for the wrong reason — it moves with "
            "the other two, and a caller told the foreign-key story will go "
            "looking for an unassign/assign pair that does not exist"
        )

    real = entities.task_form

    def with_the_name_column(*args, **kwargs):
        spec = real(*args, **kwargs)
        spec.fields.append(Field("assignee_name", "assignee name", "text"))
        spec.fields.append(Field("assignee_kind", "assignee kind", "text"))
        return spec

    monkeypatch.setitem(mcpsurface.BUILDERS, "task", with_the_name_column)
    surface = mcpsurface.editable()["task"]
    assert "assignee_name" not in surface and "assignee_kind" not in surface


# --- IMPORTANT 5: a refusal names a retry that works -------------------------


def test_a_mismatch_refusal_names_an_expecting_that_would_be_accepted(surface_db):
    """org.status is the surface's only enum-typed column and it became
    reachable with the derivation. `{current!r}` printed
    <OrgStatus.PROSPECT: 'prospect'>, and a model following that literally
    passed the repr and was refused again by the vocabulary check — a refusal
    that names a way to succeed which does not succeed."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")

    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(rw, "org", "Acme", "status", "lost", expecting="active")
    message = str(err.value)
    assert "OrgStatus" not in message, f"the refusal leaks an enum repr: {message}"
    assert "'prospect'" in message

    # and the value it named is one the tool actually accepts
    mcpserver._edit_field(rw, "org", "Acme", "status", "lost", expecting="prospect")
    assert orgs.find_by_name(rw, "Acme").status == "lost"
    rw.close()


def test_the_not_blank_refusal_and_enrich_both_name_a_usable_value(surface_db):
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")

    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(rw, "org", "Acme", "status", "lost", expecting=None)
    assert "OrgStatus" not in str(err.value)

    with pytest.raises(ValueError) as err:
        mcpserver._enrich_field(rw, "Acme", "status", "lost")
    assert "OrgStatus" not in str(err.value)
    assert "expecting='prospect'" in str(err.value)
    rw.close()


def test_a_money_refusal_names_the_amount_in_the_form_it_takes_back(surface_db):
    """Same rule, the money column: the refusal used to print raw cents
    (50000000) as the `expecting` to pass, and passing that writes a hundred
    times the amount."""
    from bookkit.repo import orgs

    rw = db.connect(surface_db)
    orgs.create(rw, name="Acme", kind="client")
    created = mcpserver._opportunity_create(rw, "Acme", "Acme cyber")
    ref = created["opportunity_ref"]
    mcpserver._edit_field(rw, "opportunity", ref, "target_premium", "5m",
                          expecting=None)
    with pytest.raises(ValueError) as err:
        mcpserver._edit_field(rw, "opportunity", ref, "target_premium", "6m",
                              expecting="1")
    assert "'$5,000,000'" in str(err.value)
    # the amount it named round-trips
    mcpserver._edit_field(rw, "opportunity", ref, "target_premium", "6m",
                          expecting="$5,000,000")
    rw.close()


# --- IMPORTANT 6: prose that describes the surface as it was -----------------


def test_no_tool_description_reprints_the_derived_field_table():
    """test_mcpserver_keeps_no_second_field_table forbids a second table in
    CODE and permitted an unlimited number in prose. enrich_field's docstring
    was one: a hand-written copy of the table the refactor deleted, missing
    five now-enrichable fields and still saying edits "happen in the TUI, not
    here", which the code's own refusal no longer says."""
    import re

    surface = mcpsurface.editable()
    known = {field for fields in surface.values() for field in fields}
    runs = re.compile(r"\b([a-z_]{3,}(?:\s*,\s*[a-z_]{3,}){2,})\b")

    offenders = []
    for name in ("edit_field", "enrich_field", "describe"):
        for run in runs.findall(_tool_prose(name)):
            items = [word.strip() for word in run.split(",")]
            named = [item for item in items if item in known]
            if len(named) >= 3:
                offenders.append(f"{name}: {named}")
    assert not offenders, (
        "a tool description enumerates the derived field set — that is the "
        "second table again, in prose, and prose is where the first one "
        f"rotted: {offenders}. Point at `describe` instead."
    )


def _tool_prose(name: str) -> str:
    """The text a model actually receives for a tool, however it is set."""
    import tempfile
    from pathlib import Path as _Path

    server = mcpserver.build_server(_Path(tempfile.mkdtemp()) / "prose.db")
    for tool in server._tool_manager.list_tools():
        if tool.name == name:
            return tool.description or ""
    raise AssertionError(f"{name} is not registered")


def test_enrich_field_no_longer_claims_edits_happen_in_the_tui():
    prose = _tool_prose("enrich_field")
    assert "in the TUI, not here" not in prose
    assert "describe" in prose, "it must name where the field list really is"


def test_activity_delete_refuses_by_naming_where_the_ref_comes_from(surface_db):
    """The fifth bare KeyError, on the path log_activity's docstring names as
    the ONLY correction route for a mis-logged interaction."""
    rw = db.connect(surface_db)
    with pytest.raises(ValueError) as err:
        mcpserver._activity_delete(rw, "NOPE-1")
    assert "recent_activity" in str(err.value)
    rw.close()
