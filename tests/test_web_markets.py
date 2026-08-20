"""The Markets surface (gap 6) — list, detail, and every write the TUI's
MarketsScreen / MarketDetailScreen make: create/edit a market, appetite
add/edit/remove, underwriters, aliases, merge, nest.

House invariants driven here:
- refusals are HTTP 200 with the sentence in the page and NOTHING written;
- confirm GETs write nothing;
- every write is one batch, under the TUI's own tool name where the TUI
  names one (merge_markets, appetite_delete) and under the FormModal-derived
  title slug everywhere else;
- an alias is not decoration: after adding one, the consumers that search by
  alias (services.exposure.for_market, aliases.resolve) actually find the
  market under the new spelling.
"""

from __future__ import annotations

import html
from datetime import date as _date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.repo import aliases as aliases_repo
from bookkit.repo import batches as batches_repo
from bookkit.repo import contacts as contacts_repo
from bookkit.repo import orgs as orgs_repo
from bookkit.repo import submissions as submissions_repo
from bookkit.services import exposure as exposure_svc
from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    """base_url is loopback because web/origin.py refuses TestClient's
    default Host of "testserver" — the forged name the guard exists for."""
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _conn(client: TestClient):
    return client.app.state.conn


def _market(client: TestClient, name: str):
    org = orgs_repo.find_by_name(_conn(client), name)
    assert org is not None and org.kind == "market", f"seed lost market {name!r}"
    return org


def _last_batch(client: TestClient):
    batch = batches_repo.most_recent(_conn(client))
    assert batch is not None
    return batch


# --- the list ----------------------------------------------------------------


def test_markets_list_names_every_market_and_links_each_row(client: TestClient):
    conn = _conn(client)
    page = client.get("/markets")
    assert page.status_code == 200
    families = orgs_repo.market_families(conn)
    assert families, "seed produced no markets"
    for top, kids in families:
        assert top.name in page.text
        assert f"/markets/{top.ref}" in page.text
        for kid in kids:
            assert f"/markets/{kid.ref}" in page.text


def test_markets_list_shows_the_hit_rate_denominator_column(client: TestClient):
    """`n` — what the rates are out of — is a real column, not decoration:
    a bare 100% over one decided submission is not a hit rate anyone can
    act on (the TUI list's own rule)."""
    page = client.get("/markets")
    assert ">n</th>" in page.text
    assert ">Quote</th>" in page.text and ">Bind</th>" in page.text


def test_a_client_ref_is_not_a_market(client: TestClient):
    conn = _conn(client)
    a_client = orgs_repo.list_orgs(conn, kind="client")[0]
    assert client.get(f"/markets/{a_client.ref}").status_code == 404


# --- the detail --------------------------------------------------------------


def _market_with_rows(client: TestClient):
    """A seeded market that has both appetite rows and contacts."""
    conn = _conn(client)
    for org in orgs_repo.list_orgs(conn, kind="market"):
        if orgs_repo.appetite_for_market(conn, org.id) and contacts_repo.for_org(
            conn, org.id
        ):
            return org
    pytest.fail("seed produced no market with appetite and contacts")


def test_market_detail_shows_appetite_underwriters_and_submissions(client: TestClient):
    conn = _conn(client)
    market = _market_with_rows(client)
    page = client.get(f"/markets/{market.ref}")
    assert page.status_code == 200
    for appetite in orgs_repo.appetite_for_market(conn, market.id):
        # through html.escape: Jinja autoescapes, so a line like "d&o"
        # renders as "d&amp;o" — comparing raw would fail on exactly the
        # values escaping exists to protect
        assert html.escape(appetite.line.replace("_", " ")) in page.text
    for who in contacts_repo.for_org(conn, market.id):
        assert who.name in page.text
    subs = submissions_repo.for_market(conn, market.id)
    if subs:
        assert subs[0].sent_on in page.text


# --- create ------------------------------------------------------------------


def test_new_market_form_renders_and_create_writes_one_batch(client: TestClient):
    conn = _conn(client)
    form = client.get("/markets/new")
    assert form.status_code == 200
    assert 'action="/markets/new"' in form.text

    done = client.post(
        "/markets/new",
        data={
            "name": "CNA", "kind": "market", "status": "active",
            "market_type": "carrier", "am_best_rating": "A",
        },
    )
    assert done.status_code == 200
    created = orgs_repo.find_by_name(conn, "CNA")
    assert created is not None and created.kind == "market"
    profile = orgs_repo.get_market_profile(conn, created.id)
    assert profile is not None and profile.am_best_rating == "A"
    # the success response is the refreshed panel, so the new row is on screen
    assert "CNA" in done.text and 'id="markets-panel"' in done.text
    batch = _last_batch(client)
    assert batch.source == "web" and batch.tool == "new_account"


def test_market_create_refused_in_place_writes_nothing(client: TestClient):
    conn = _conn(client)
    before = len(orgs_repo.list_orgs(conn, kind="market"))
    refused = client.post("/markets/new", data={"name": "", "kind": "market"})
    assert refused.status_code == 200
    assert "name is required" in refused.text
    assert len(orgs_repo.list_orgs(conn, kind="market")) == before


# --- edit --------------------------------------------------------------------


def test_edit_market_updates_the_profile_and_refreshes_the_page(client: TestClient):
    conn = _conn(client)
    market = _market(client, "Liberty")
    done = client.post(
        f"/markets/{market.ref}/edit",
        data={
            "name": market.name, "kind": "market", "status": "active",
            "market_type": "carrier", "am_best_rating": "B++",
        },
        follow_redirects=False,
    )
    # page-level write: a plain post gets a 303 back to the page it changed
    assert done.status_code == 303
    assert done.headers["location"] == f"/markets/{market.ref}"
    profile = orgs_repo.get_market_profile(conn, market.id)
    assert profile is not None and profile.am_best_rating == "B++"
    assert _last_batch(client).tool == "edit_account"


# --- appetite ----------------------------------------------------------------


def test_appetite_add_edit_and_remove_round_trip(client: TestClient):
    conn = _conn(client)
    market = _market(client, "Liberty")

    added = client.post(
        f"/markets/{market.ref}/appetite/new",
        data={"line": "aviation", "appetite": "target", "min_premium": "250k"},
    )
    assert added.status_code == 200
    rows = [
        a for a in orgs_repo.appetite_for_market(conn, market.id) if a.line == "aviation"
    ]
    assert len(rows) == 1
    assert rows[0].appetite == "target"
    assert rows[0].min_premium == 250_000_00  # money parses to CENTS
    assert _last_batch(client).tool == "add_appetite"

    edited = client.post(
        f"/markets/{market.ref}/appetite/{rows[0].id}/edit",
        data={"line": "aviation", "appetite": "no"},
    )
    assert edited.status_code == 200
    assert orgs_repo.get_appetite(conn, rows[0].id).appetite == "no"

    # the confirm GET names the line and writes NOTHING
    confirm = client.get(f"/markets/{market.ref}/appetite/{rows[0].id}/remove")
    assert confirm.status_code == 200
    assert "aviation" in confirm.text
    assert orgs_repo.get_appetite(conn, rows[0].id) is not None  # still there

    removed = client.post(f"/markets/{market.ref}/appetite/{rows[0].id}/remove")
    assert removed.status_code == 200
    assert not [
        a for a in orgs_repo.appetite_for_market(conn, market.id) if a.line == "aviation"
    ]
    # the TUI's own tool name for this write
    assert _last_batch(client).tool == "appetite_delete"


def test_appetite_refusal_is_a_sentence_in_the_page(client: TestClient):
    conn = _conn(client)
    market = _market(client, "Liberty")
    before = len(orgs_repo.appetite_for_market(conn, market.id))
    refused = client.post(
        f"/markets/{market.ref}/appetite/new",
        data={"line": "cyber", "appetite": "LOVE_IT"},
    )
    assert refused.status_code == 200
    assert "must be one of" in refused.text
    assert len(orgs_repo.appetite_for_market(conn, market.id)) == before


def test_an_appetite_row_of_another_market_is_404(client: TestClient):
    """{ref} plus an appetite id is TWO claims and both get checked — the
    routes/account.py ownership rule, on this module's id-carrying rows."""
    conn = _conn(client)
    liberty = _market(client, "Liberty")
    other = next(
        o for o in orgs_repo.list_orgs(conn, kind="market")
        if o.id != liberty.id and orgs_repo.appetite_for_market(conn, o.id)
    )
    foreign = orgs_repo.appetite_for_market(conn, other.id)[0]
    assert (
        client.get(f"/markets/{liberty.ref}/appetite/{foreign.id}/edit").status_code
        == 404
    )


# --- underwriters ------------------------------------------------------------


def test_add_underwriter_defaults_the_role_like_the_tui(client: TestClient):
    conn = _conn(client)
    market = _market(client, "Liberty")
    done = client.post(
        f"/markets/{market.ref}/underwriters/new",
        data={"first_name": "Dana", "last_name": "Reeve", "role": ""},
    )
    assert done.status_code == 200
    who = next(
        c for c in contacts_repo.for_org(conn, market.id)
        if c.first_name == "Dana" and c.last_name == "Reeve"
    )
    assert who.role == "underwriter"
    assert "Dana Reeve" in done.text and 'id="uw-panel"' in done.text

    edited = client.post(
        f"/markets/{market.ref}/underwriters/{who.id}/edit",
        data={"first_name": "Dana", "last_name": "Reeve", "title": "VP, Casualty"},
    )
    assert edited.status_code == 200
    assert contacts_repo.get(conn, who.id).title == "VP, Casualty"


def test_a_contact_of_another_org_is_404_here(client: TestClient):
    conn = _conn(client)
    market = _market(client, "Liberty")
    a_client = next(
        o for o in orgs_repo.list_orgs(conn, kind="client")
        if contacts_repo.for_org(conn, o.id)
    )
    foreign = contacts_repo.for_org(conn, a_client.id)[0]
    assert (
        client.get(
            f"/markets/{market.ref}/underwriters/{foreign.id}/edit"
        ).status_code
        == 404
    )


# --- aliases -----------------------------------------------------------------


def test_alias_add_resolves_through_the_consumers(client: TestClient):
    """The point of an alias is that things FIND the market under it. Two
    real consumers are asserted through: aliases.resolve (what projection
    and quick lookups use) and services.exposure.for_market (which searches
    the org name AND every alias across projected towers). The seed writes a
    tower with carrier 'CNA' and no CNA market, so the spelling is
    unresolved until this alias lands."""
    conn = _conn(client)
    market = _market(client, "Liberty")
    assert "CNA" in aliases_repo.unresolved_carriers(conn), (
        "seed no longer leaves 'CNA' unresolved; pick another spelling"
    )
    # a wide-open window (any period_to from 2000 on), so the consumer
    # assertion cannot rot with the seeded placements' renewal clocks
    window = {"days": 36_500, "today": _date(2000, 1, 1)}
    before = exposure_svc.for_market(conn, market.id, **window)
    assert not [r for r in before if r.carrier == "CNA"]

    done = client.post(
        f"/markets/{market.ref}/aliases/new",
        data={"alias": "CNA"},
        follow_redirects=False,
    )
    assert done.status_code == 303

    assert aliases_repo.resolve(conn, "CNA") == market.id
    after = exposure_svc.for_market(conn, market.id, **window)
    assert [r for r in after if r.carrier == "CNA"], (
        "exposure.for_market no longer finds the market under its new alias"
    )
    page = client.get(f"/markets/{market.ref}")
    assert "also written as: CNA" in page.text


def test_alias_refused_empty_in_place(client: TestClient):
    market = _market(client, "Liberty")
    refused = client.post(f"/markets/{market.ref}/aliases/new", data={"alias": "  "})
    assert refused.status_code == 200
    assert "tower spelling is required" in refused.text
    assert not aliases_repo.for_market(_conn(client), market.id)


# --- merge -------------------------------------------------------------------


def test_merge_confirm_names_the_alias_rule_and_writes_nothing(client: TestClient):
    conn = _conn(client)
    source = _market(client, "AIG")
    target = _market(client, "Chubb")
    confirm = client.get(
        f"/markets/{source.ref}/merge/confirm", params={"target": target.id}
    )
    assert confirm.status_code == 200
    assert "becomes an alias" in confirm.text
    assert target.name in confirm.text
    # the GET wrote NOTHING: the duplicate is still alive
    assert orgs_repo.find(conn, source.ref) is not None
    assert source.name not in aliases_repo.for_market(conn, target.id)


def test_merge_moves_everything_and_preserves_the_name_as_an_alias(client: TestClient):
    conn = _conn(client)
    source = _market(client, "AIG")
    target = _market(client, "Chubb")
    source_contacts = {c.id for c in contacts_repo.for_org(conn, source.id)}
    source_subs = {s.id for s in submissions_repo.for_market(conn, source.id)}

    done = client.post(
        f"/markets/{source.ref}/merge/confirm",
        params={"target": target.id},
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert done.headers["location"] == f"/markets/{target.ref}"

    # the duplicate is soft-deleted and off the list…
    assert orgs_repo.find(conn, source.ref) is None
    assert f"/markets/{source.ref}" not in client.get("/markets").text
    # …its name is an alias of the survivor, so towers keep resolving…
    assert source.name in aliases_repo.for_market(conn, target.id)
    assert aliases_repo.resolve(conn, source.name) == target.id
    # …and its children moved
    assert source_contacts <= {c.id for c in contacts_repo.for_org(conn, target.id)}
    assert source_subs <= {s.id for s in submissions_repo.for_market(conn, target.id)}
    batch = _last_batch(client)
    assert batch.source == "web" and batch.tool == "merge_markets"


def test_merge_refuses_a_forged_target_in_place(client: TestClient):
    conn = _conn(client)
    source = _market(client, "Travelers")
    refused = client.post(
        f"/markets/{source.ref}/merge/confirm", params={"target": source.id}
    )
    assert refused.status_code == 200
    assert "pick which market" in refused.text
    assert orgs_repo.find(conn, source.ref) is not None  # nothing written


# --- nest --------------------------------------------------------------------


def test_nest_under_an_existing_master_and_unnest(client: TestClient):
    conn = _conn(client)
    sompo = _market(client, "Sompo")
    chubb = _market(client, "Chubb")

    done = client.post(
        f"/markets/{sompo.ref}/nest", data={"parent": chubb.id},
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert orgs_repo.get(conn, sompo.id).parent_org_id == chubb.id
    # the list draws the outline: the child is nested beneath its master
    assert "└" in client.get("/markets").text

    undone = client.post(
        f"/markets/{sompo.ref}/nest", data={"parent": ""}, follow_redirects=False
    )
    assert undone.status_code == 303
    assert orgs_repo.get(conn, sompo.id).parent_org_id is None


def test_nest_can_create_the_master_on_the_spot(client: TestClient):
    """The AXA XL case: the master company does not exist yet, so the nest
    form's free-text name creates it and nests in the same batch."""
    conn = _conn(client)
    axa = _market(client, "AXA XL")
    done = client.post(
        f"/markets/{axa.ref}/nest",
        data={"parent": "", "new_master": "AXA Group"},
        follow_redirects=False,
    )
    assert done.status_code == 303
    master = orgs_repo.find_by_name(conn, "AXA Group")
    assert master is not None and master.kind == "market"
    assert orgs_repo.get(conn, axa.id).parent_org_id == master.id
    assert _last_batch(client).tool == "new_master_company"


def test_nest_refuses_a_cycle_in_place(client: TestClient):
    conn = _conn(client)
    sompo = _market(client, "Sompo")
    berkley = _market(client, "Berkley")
    assert client.post(
        f"/markets/{sompo.ref}/nest", data={"parent": berkley.id},
        follow_redirects=False,
    ).status_code == 303
    refused = client.post(
        f"/markets/{berkley.ref}/nest", data={"parent": sompo.id}
    )
    assert refused.status_code == 200
    assert "descendant" in refused.text
    assert orgs_repo.get(conn, berkley.id).parent_org_id is None


def test_merging_a_master_folds_its_children_into_the_survivor(client: TestClient):
    """The Sompo-under-Chubb case: merging Chubb into AIG must not leave
    Sompo's parent FK pointing at a dead org — that 500'd the child's own
    detail page, and the unnest control that could repair it lived there."""
    conn = _conn(client)
    sompo = _market(client, "Sompo")
    chubb = _market(client, "Chubb")
    aig = _market(client, "AIG")

    assert client.post(
        f"/markets/{sompo.ref}/nest", data={"parent": chubb.id},
        follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/markets/{chubb.ref}/merge/confirm", params={"target": aig.id},
        follow_redirects=False,
    ).status_code == 303

    # the child moved under the survivor, and its dossier still renders
    assert orgs_repo.get(conn, sompo.id).parent_org_id == aig.id
    assert client.get(f"/markets/{sompo.ref}").status_code == 200


def test_merging_a_master_into_its_own_child_unnests_the_child(client: TestClient):
    conn = _conn(client)
    sompo = _market(client, "Sompo")
    chubb = _market(client, "Chubb")
    assert client.post(
        f"/markets/{sompo.ref}/nest", data={"parent": chubb.id},
        follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/markets/{chubb.ref}/merge/confirm", params={"target": sompo.id},
        follow_redirects=False,
    ).status_code == 303
    # the survivor cannot nest under itself; it takes the dead master's spot
    assert orgs_repo.get(conn, sompo.id).parent_org_id is None
    assert client.get(f"/markets/{sompo.ref}").status_code == 200


def test_a_child_whose_parent_died_before_the_family_fix_still_renders(
    client: TestClient,
):
    """Pre-fix data: a parent merged away before merges re-parented children.
    The detail page floats the child free, the same as the list does."""
    conn = _conn(client)
    sompo = _market(client, "Sompo")
    chubb = _market(client, "Chubb")
    from bookkit.repo import base as base_repo

    orgs_repo.set_parent(conn, sompo.id, chubb.id)
    base_repo.soft_delete(conn, "org", chubb.id, note="test: dead parent")
    page = client.get(f"/markets/{sompo.ref}")
    assert page.status_code == 200
    assert f"nested under {chubb.name}" not in page.text  # no ghost master
