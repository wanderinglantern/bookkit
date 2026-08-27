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
        f"/markets/{source.ref}/merge/confirm",
        # `survivor=other` is the direction this page used to assume: the
        # market you are looking at dies. It is asked now (`_merge_pair`).
        params={"target": target.id, "survivor": "other"},
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
        params={"target": target.id, "survivor": "other"},
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
        f"/markets/{source.ref}/merge/confirm",
        params={"target": source.id, "survivor": "other"},
    )
    assert refused.status_code == 200
    assert "pick the other market" in refused.text
    assert orgs_repo.find(conn, source.ref) is not None  # nothing written


def test_merge_refuses_a_direction_nobody_chose(client: TestClient):
    """WHICH WAY ROUND IS ASKED, NEVER ASSUMED. This page's market used to die
    by construction, so the older record survived whichever one you happened to
    open — and the button that gets here reads "Merge duplicate…", promising
    the opposite (Grant, 2026-08-27: "there is a prompt to merge AIG into AIG
    Environmental but technically I need it the other way around").

    Rank it by what the WRITE does: this soft-deletes an org, moves every
    contact, submission and alias off it, and turns its NAME into an alias of
    the other. There is no default to fall back on."""
    conn = _conn(client)
    source = _market(client, "AIG")
    target = _market(client, "Chubb")

    refused = client.post(
        f"/markets/{source.ref}/merge/confirm", params={"target": target.id}
    )

    assert refused.status_code == 200
    assert "which of the two survives" in refused.text
    assert orgs_repo.find(conn, source.ref) is not None
    assert orgs_repo.find(conn, target.ref) is not None


def test_merge_can_keep_the_market_whose_page_you_are_on(client: TestClient):
    """Grant's case exactly: the older record (AIG Environmental) was set up
    first, the real one (AIG) came later, and the merge has to run the other
    way. Reached from EITHER page now, and the confirm names both by name."""
    conn = _conn(client)
    keeper = _market(client, "AIG")
    doomed = _market(client, "Chubb")

    confirm = client.get(
        f"/markets/{keeper.ref}/merge/confirm",
        params={"target": doomed.id, "survivor": "this"},
    )
    assert confirm.status_code == 200
    assert f"Merge <strong>{doomed.name}</strong> into <strong>{keeper.name}</strong>" in (
        confirm.text
    )
    # THE COUNTS ARE THE LOSER'S — they say what MOVES, and reading them off
    # this page's own market would describe the other merge entirely. Asserted
    # on a pair whose counts DIFFER, or the check passes either way round.
    contacts_repo.create(
        conn, org_id=doomed.id, first_name="Second", last_name="Underwriter"
    )
    confirm = client.get(
        f"/markets/{keeper.ref}/merge/confirm",
        params={"target": doomed.id, "survivor": "this"},
    )
    mine = len(contacts_repo.for_org(conn, keeper.id))
    theirs = len(contacts_repo.for_org(conn, doomed.id))
    assert mine != theirs, "the fixture cannot tell the two directions apart"
    assert f"{theirs} contact(s)" in confirm.text
    assert f"{mine} contact(s)" not in confirm.text

    done = client.post(
        f"/markets/{keeper.ref}/merge/confirm",
        params={"target": doomed.id, "survivor": "this"},
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert done.headers["location"] == f"/markets/{keeper.ref}"
    assert orgs_repo.find(conn, doomed.ref) is None
    assert orgs_repo.find(conn, keeper.ref) is not None
    assert doomed.name in aliases_repo.for_market(conn, keeper.id)


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
        f"/markets/{chubb.ref}/merge/confirm",
        params={"target": aig.id, "survivor": "other"},
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
        f"/markets/{chubb.ref}/merge/confirm",
        params={"target": sompo.id, "survivor": "other"},
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


# --- carriers on the towers that the book does not know -----------------------
#
# Grant, 2026-08-20: "New market added to program saved, but it does not carry
# forward to the Markets tab." A carrier is a STRING in a towerkit file; it
# joins the book only when a market org carries that name or an alias points at
# one. The web could write the string and had no way to do the second half, so
# a carrier bound in the browser missed exposure, hit rate and every market
# page — silently, on the tab where you would go looking for it.


def _bind_a_new_carrier(client, conn, name="Brand New Re"):
    """Put a carrier nobody has heard of onto a real layer, the way the
    Program tab does."""
    from bookkit import sync
    from bookkit.repo import orgs, placements

    org = next(
        o for o in orgs.list_orgs(conn, kind="client") if placements.for_org(conn, o.id)
    )
    placement = next(p for p in placements.for_org(conn, org.id) if p.program_path)
    # A layer with ROOM on it: towerkit refuses a seat that would take the
    # signed share past 100%, and every seeded layer is fully placed. So add
    # one on top of the tower — which is also the shape of Grant's report,
    # "new market added to program".
    existing = sync.layer_details(conn, placement.id)
    # Per LINE, not per tower: a new layer covering one line has to sit on top
    # of that line's own stack, or towerkit refuses it as a gap.
    line = existing[0]["applies_to"][0]
    top = max(
        ly["attach_cents"] + ly["limit_cents"]
        for ly in existing
        if line in ly["applies_to"]
    )
    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "Test Excess",
            "line": line,
            "attach_cents": f"{top // 100:,}",
            "limit_cents": "5,000,000",
            "premium_cents": "",
        },
    )
    assert added.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["name"] == "Test Excess"
    )
    posted = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers/{layer['id']}/markets",
        data={"carrier": name, "share_pct": "1"},
    )
    assert posted.status_code == 200
    seated = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert any(seat["carrier"] == name for seat in seated["participants"]), (
        f"the bind was refused: {posted.text[:400]}"
    )
    return org, placement, layer


def test_a_carrier_bound_on_a_program_shows_up_on_the_markets_tab(client):
    conn = client.app.state.conn
    _bind_a_new_carrier(client, conn)

    page = client.get("/markets").text

    assert "Brand New Re" in page, "the bound carrier never reached the Markets tab"
    assert "On your towers, not in the book" in page


def test_adding_an_unknown_carrier_as_a_market_makes_it_a_real_market(client):
    from bookkit.repo import orgs

    conn = client.app.state.conn
    _bind_a_new_carrier(client, conn)

    added = client.post("/markets/unlinked/create", data={"carrier": "Brand New Re"})

    assert added.status_code == 200
    assert any(o.name == "Brand New Re" for o in orgs.list_orgs(conn, kind="market"))
    # and it has LEFT the unlinked list rather than appearing in both. Scoped
    # to this carrier: the seeded book has other unresolved spellings, and
    # asserting the whole panel is gone would pass only by accident.
    unlinked = added.text[added.text.index("unlinked-panel"):]
    assert "Brand New Re" not in unlinked[: unlinked.index("markets-panel")]


def test_an_unknown_carrier_can_be_linked_to_the_market_it_already_is(client):
    from bookkit.repo import aliases, orgs

    conn = client.app.state.conn
    _bind_a_new_carrier(client, conn, "Chubb Limited")
    existing = orgs.list_orgs(conn, kind="market")[0]

    linked = client.post(
        "/markets/unlinked/link",
        data={"carrier": "Chubb Limited", "org_id": existing.id},
    )

    assert linked.status_code == 200
    assert aliases.resolve(conn, "Chubb Limited") == existing.id
    # no second market org was invented for a spelling of one we already have
    assert not any(o.name == "Chubb Limited" for o in orgs.list_orgs(conn, kind="market"))


def test_linking_to_something_that_is_not_a_market_is_refused(client):
    from bookkit.repo import aliases, orgs

    conn = client.app.state.conn
    _bind_a_new_carrier(client, conn)
    a_client = orgs.list_orgs(conn, kind="client")[0]

    refused = client.post(
        "/markets/unlinked/link",
        data={"carrier": "Brand New Re", "org_id": a_client.id},
    )

    assert refused.status_code == 200
    assert "is not a market" in refused.text
    assert aliases.resolve(conn, "Brand New Re") is None


def test_the_layer_chip_says_a_carrier_is_not_in_the_book(client):
    """Said where the carrier is. A fact only visible on another page is a
    fact nobody sees."""
    conn = client.app.state.conn
    org, placement, layer = _bind_a_new_carrier(client, conn)
    # SELECT the seeded layer rather than trusting the default. The rail groups
    # by line of coverage since 2026-08-24, so the page opens on the top of the
    # FIRST line — and this helper seats its carrier on whichever line the
    # fixture's first layer covers. What is under test is the chip, not which
    # layer a fresh page happens to show.
    where = f"/accounts/{org.ref}/program?layer={layer['id']}"

    page = client.get(where).text

    # The badge reads "NEW"; the sentence lives in the accessible name, where
    # it is available without being repeated at the reader once per seat.
    assert 'aria-label="Brand New Re is not a market in the book' in page
    assert "market-unlinked" in page

    client.post("/markets/unlinked/create", data={"carrier": "Brand New Re"})
    after = client.get(where).text

    assert "Brand New Re" in after
    assert 'aria-label="Brand New Re is not a market in the book' not in after, (
        "the marker outlived the thing it marked"
    )
