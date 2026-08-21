"""WC Part A and Part B are ONE POLICY, and the browser can now say so.

Workers' compensation Part A (statutory benefits, no dollar limit) and Part B
(employers liability, a real limit) come on one policy from one carrier, and
towerkit CANNOT make them one layer: `statutory` requires limit 0. The
schematic draws them apart, correctly, and until 2026-08-21 nothing in the file
said they belonged together.

What bookkit owns here is the CONTROL, and its shape is the argument: the
stored value is a machine-minted token no screen should ever print, so the
picker offers the program's other layers by NAME and nothing else. The write
itself is towerkit's `link_policy` through the ordinary program-file seam, so
it validates, snapshots and reverts like any other program edit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import sync
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o
        for o in orgs.list_orgs(conn, kind="client")
        if [p for p in placements.for_org(conn, o.id) if p.program_path]
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _two_layers(conn, org):
    from bookkit.repo import placements

    placement = next(p for p in placements.for_org(conn, org.id) if p.program_path)
    layers = sync.layer_details(conn, placement.id)
    assert len(layers) > 1, "fixture drifted — need two layers to link"
    return placement, str(layers[0]["id"]), str(layers[1]["id"])


def _url(org, placement, layer_id):
    return (
        f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/policy"
    )


def _details(client, org, placement, layer_id) -> str:
    return client.get(
        f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/details"
    ).text


def _group_of(conn, placement, layer_id):
    program = sync.linked_program(conn, placement.id).program
    return next(ly for ly in program.layers if ly.id == layer_id).policy_group


class TestTheControl:
    def test_it_is_a_picker_of_the_other_layers_and_not_a_text_box(
        self, app_and_org
    ) -> None:
        """The token is machine-minted; the storable answers are exactly the
        other layers. Constrained input over an open field, and the picker
        offers only what can be stored."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, theirs = _two_layers(conn, org)

        row = _details(client, org, placement, mine)
        block = row[row.index('name="policy_group"') :]
        block = block[: block.index("</select>")]

        assert "<input" not in block
        assert f'value="{theirs}"' in block
        assert f'value="{mine}"' not in block, "a layer offered itself"

    def test_it_renders_a_blank_option_that_means_not_linked(
        self, app_and_org
    ) -> None:
        """A real answer, not an absence: without it, unlinking is unreachable
        from the only control that offers it."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, _ = _two_layers(conn, org)

        row = _details(client, org, placement, mine)
        block = row[row.index('name="policy_group"') :]

        assert re.search(r'<option value="">\s*not linked', block)

    def test_it_sits_in_the_policy_group_of_the_details_row(
        self, app_and_org
    ) -> None:
        """Grouping is information: it is a fact about the POLICY, so it sits
        with the number, the dates and auditable — not among the coverage."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, _ = _two_layers(conn, org)

        row = _details(client, org, placement, mine)
        assert (
            row.index('data-field="policy_number"')
            < row.index('name="policy_group"')
            < row.index('data-field="layer.limitsDetail"')
        )


class TestLinking:
    def test_choosing_a_layer_puts_both_on_one_policy(self, app_and_org) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, theirs = _two_layers(conn, org)

        saved = client.post(_url(org, placement, mine), data={"policy_group": theirs})

        assert saved.status_code == 200
        group = _group_of(conn, placement, mine)
        assert group and _group_of(conn, placement, theirs) == group

    def test_the_link_survives_a_reload_and_shows_as_selected(
        self, app_and_org
    ) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, theirs = _two_layers(conn, org)
        client.post(_url(org, placement, mine), data={"policy_group": theirs})

        row = _details(client, org, placement, mine)
        block = row[row.index('name="policy_group"') :]
        block = block[: block.index("</select>")]

        assert re.search(rf'value="{theirs}"[^>]*selected', block)

    def test_the_token_is_never_printed_on_the_page(self, app_and_org) -> None:
        """It is an id towerkit mints. A screen that shows it invites somebody
        to type one."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, theirs = _two_layers(conn, org)
        client.post(_url(org, placement, mine), data={"policy_group": theirs})
        group = _group_of(conn, placement, mine)

        assert group
        assert group not in _details(client, org, placement, mine)
        assert group not in client.get(f"/accounts/{org.ref}/program").text

    def test_blank_unlinks(self, app_and_org) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, theirs = _two_layers(conn, org)
        client.post(_url(org, placement, mine), data={"policy_group": theirs})
        assert _group_of(conn, placement, mine)

        client.post(_url(org, placement, mine), data={"policy_group": ""})

        assert _group_of(conn, placement, mine) is None
        assert _group_of(conn, placement, theirs), "the other side was unlinked too"

    def test_the_write_is_one_revertible_batch(self, app_and_org) -> None:
        from bookkit.repo import batches as batches_repo

        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, theirs = _two_layers(conn, org)

        client.post(_url(org, placement, mine), data={"policy_group": theirs})

        batch = batches_repo.recent(conn, "0000", limit=1)[0]
        assert batch.source == "web"
        assert batch.tool.startswith("program_")


class TestTheGuards:
    def test_a_layer_id_that_is_not_offered_is_refused(self, app_and_org) -> None:
        """The picker's own options are the authority — markup constrains a
        mouse and nothing else — and membership IS the scope check: a layer of
        another placement is simply not in this list."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, _ = _two_layers(conn, org)

        refused = client.post(
            _url(org, placement, mine), data={"policy_group": "not-a-layer"}
        )

        assert refused.status_code == 200
        assert "not one of the choices offered" in refused.text
        assert _group_of(conn, placement, mine) is None

    def test_a_layer_cannot_be_put_on_its_own_policy(self, app_and_org) -> None:
        """It is not in its own picker, so this can only arrive by hand — and
        towerkit refuses it in its own words."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, _ = _two_layers(conn, org)

        refused = client.post(_url(org, placement, mine), data={"policy_group": mine})

        assert refused.status_code == 200
        assert _group_of(conn, placement, mine) is None

    def test_another_accounts_program_is_a_404(self, app_and_org) -> None:
        from bookkit.repo import orgs

        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, theirs = _two_layers(conn, org)
        other = next(
            o for o in orgs.list_orgs(conn, kind="client") if o.id != org.id
        )

        got = client.post(
            f"/accounts/{other.ref}/program/{placement.id}/layers/{mine}/policy",
            data={"policy_group": theirs},
        )

        assert got.status_code == 404

    def test_a_refusal_comes_back_as_the_row_not_a_status_code(
        self, app_and_org
    ) -> None:
        """An error response produces no swap and no message at all under
        htmx — the control would simply stop working."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, _ = _two_layers(conn, org)

        refused = client.post(
            _url(org, placement, mine), data={"policy_group": "not-a-layer"}
        )

        assert refused.text.lstrip().startswith("<tr")


class TestTheRuleItCarries:
    def test_two_parts_stating_different_policy_numbers_is_refused(
        self, app_and_org
    ) -> None:
        """A link field with no rule attached is just a note. Parts of one
        policy share its paper, and towerkit's validator says so — which
        reaches the browser as an ordinary refusal in the row."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, mine, theirs = _two_layers(conn, org)
        base = f"/accounts/{org.ref}/program/{placement.id}/layers"
        client.post(f"{base}/{mine}/cell/policy_number", data={"policy_number": "WC-1"})
        client.post(f"{base}/{theirs}/cell/policy_number", data={"policy_number": "WC-2"})

        refused = client.post(_url(org, placement, mine), data={"policy_group": theirs})

        assert "policy number" in refused.text
        assert _group_of(conn, placement, mine) is None, "the bad link was stored"
