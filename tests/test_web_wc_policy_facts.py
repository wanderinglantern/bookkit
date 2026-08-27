"""Two workers-compensation facts, from the browser: where cover is FILED, and
whether the policy is AUDITED.

Both are towerkit fields, both arrive through the derived seam (routes/program
`_PLACED` -> towerfields -> sync.set_tower_field), and neither needed a
hand-written route. What is asserted here is the half bookkit owns — that a
broker can actually reach them, and that towerkit's own parsing survives the
trip through a form post.

The states half is the one with a bug behind it: Grant pasted a schedule
straight off a policy — bare two-letter codes, no commas — and the whole run
was stored as ONE state. towerkit's `parse_states` now reads it; this file
proves the web cell does not undo that on the way through.
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


def _statutory_layer(conn, org):
    """A layer that may legally carry states — towerkit refuses them on a
    dollar-limited one, so the fixture's first layer will not do."""
    from bookkit.repo import placements

    placement = next(p for p in placements.for_org(conn, org.id) if p.program_path)
    layer = sync.layer_details(conn, placement.id)[0]
    # `set_statutory` and not a field write: marking a layer statutory FORCES
    # the whole invariant (limit 0, attach 0, follows cleared), and it is the
    # call the web's own confirm route makes.
    assert sync.set_statutory(conn, placement.id, layer["id"], True).ok
    return placement, layer["id"]


def _field_url(org, placement, name, layer_id):
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/field/layer/{layer_id}:_/{name}"
    )


def _details(client, org, placement, layer_id) -> str:
    """The layer's worksheet — the redesign's home for the policy facts."""
    return client.get(
        f"/accounts/{org.ref}/program/{placement.id}/worksheet?layer={layer_id}"
    ).text


def _reload(conn, placement, layer_id):
    program = sync.linked_program(conn, placement.id).program
    return next(ly for ly in program.layers if ly.id == layer_id)


# --- states, pasted -----------------------------------------------------------


class TestAPastedStateList:
    @pytest.mark.parametrize(
        "pasted",
        [
            "IL, WI, IN",
            "IL WI IN",
            "IL\nWI\nIN",
            "Illinois Wisconsin Indiana",
            "illinois, wi, IN",
        ],
    )
    def test_the_web_cell_reads_it_the_way_towerkit_does(
        self, app_and_org, pasted: str
    ) -> None:
        """THE BUG, from the browser. The cell hands the raw text to towerkit's
        own parser rather than splitting it itself, so every shape a policy
        prints lands as the same three states."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)

        saved = client.post(
            _field_url(org, placement, "states", layer_id),
            data={"layer.states": pasted},
        )

        assert saved.status_code == 200
        assert _reload(conn, placement, layer_id).states == ["IL", "WI", "IN"]

    def test_a_multi_word_name_is_not_split_into_words(self, app_and_org) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)

        client.post(
            _field_url(org, placement, "states", layer_id),
            data={"layer.states": "New York New Jersey"},
        )

        assert _reload(conn, placement, layer_id).states == ["NY", "NJ"]

    def test_a_monopolistic_state_is_still_refused_in_the_browser(
        self, app_and_org
    ) -> None:
        """Normalising at entry must not quiet the one check this field exists
        for: ND, OH, WA and WY run state funds a private policy cannot write.
        The refusal has to reach the CELL, in towerkit's own words."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)

        refused = client.post(
            _field_url(org, placement, "states", layer_id),
            data={"layer.states": "IL, ohio"},
        )

        assert "monopolistic" in refused.text.lower()
        assert _reload(conn, placement, layer_id).states != ["IL", "OH"]

    def test_an_unrecognised_jurisdiction_is_not_silently_corrected(
        self, app_and_org
    ) -> None:
        """It is stored as typed, so towerkit's warning names it. A parser that
        repaired "Onterio" would be inventing where cover is filed."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)

        client.post(
            _field_url(org, placement, "states", layer_id),
            data={"layer.states": "IL, Onterio"},
        )

        assert _reload(conn, placement, layer_id).states == ["IL", "Onterio"]


# --- auditable ----------------------------------------------------------------


class TestTheAuditableCell:
    def test_it_renders_in_the_policy_group_of_the_details_row(
        self, app_and_org
    ) -> None:
        """Grouping is information (the data-entry rules): auditable is a fact
        about the POLICY, so it sits with the number and the dates rather than
        among the coverage facts."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)
        # RECORDED FIRST, so the grouping is visible to measure. Since
        # 2026-08-27 the worksheet shows the facts somebody has recorded and
        # collapses the rest behind one disclosure, so document order only
        # reflects the GROUPS when the facts being compared are on the same
        # side of that split. Recording them puts all four on the face, which
        # is the state this test is about.
        base = f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}"
        client.post(f"{base}/cell/policy_number", data={"policy_number": "WC-1"})
        client.post(f"{base}/cell/period_to", data={"period_to": "2027-09-01"})
        client.post(
            f"{base}/field/layer/limitsDetail?at={layer_id}",
            data={"layer.limitsDetail": "statutory benefits"},
        )
        row = _details(client, org, placement, layer_id)

        assert 'data-field="layer.auditable"' in row
        # Scoped to the worksheet pane: the program band above it renders the
        # PLACEMENT's own period cells under the same field keys, so an
        # unscoped index() would measure the wrong control.
        pane = row[row.index('class="worksheet"') :]
        policy = pane.index('data-field="policy_number"')
        expiry = pane.index('data-field="period_to"')
        auditable = pane.index('data-field="layer.auditable"')
        coverage = pane.index('data-field="layer.limitsDetail"')
        assert policy < expiry < auditable < coverage

    def test_the_editor_is_a_picker_with_a_blank_option(self, app_and_org) -> None:
        """A bool has a knowable set of answers, so it is a select and not a
        text box — and it renders a blank option like every other select here,
        or the browser answers a question nobody was asked."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)

        editor = client.get(
            _field_url(org, placement, "auditable", layer_id) + "/edit"
        ).text

        assert "<select" in editor
        assert re.search(r'<option value=""', editor), "no blank option"
        assert "yes" in editor.lower() and "no" in editor.lower()

    @pytest.mark.parametrize("typed,expected", [("true", True), ("false", False)])
    def test_it_saves_into_the_towerkit_file(
        self, app_and_org, typed: str, expected: bool
    ) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)

        saved = client.post(
            _field_url(org, placement, "auditable", layer_id),
            data={"layer.auditable": typed},
        )

        assert saved.status_code == 200
        assert saved.text.lstrip().startswith("<span"), "a detail cell came back as a td"
        assert _reload(conn, placement, layer_id).auditable is expected

    def test_a_statutory_wc_layer_may_be_auditable(self, app_and_org) -> None:
        """WC is the most audited line there is, and its Part A is statutory.
        A rule tying the two together would be an opinion, not a fact."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)

        client.post(
            _field_url(org, placement, "auditable", layer_id),
            data={"layer.auditable": "true"},
        )

        layer = _reload(conn, placement, layer_id)
        assert layer.statutory and layer.auditable

    def test_a_value_the_field_cannot_take_is_refused_in_its_own_words(
        self, app_and_org
    ) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)

        refused = client.post(
            _field_url(org, placement, "auditable", layer_id),
            data={"layer.auditable": "maybe"},
        )

        # A select refuses server-side through forms.spec.checked_option —
        # markup constrains a mouse and nothing else — and the refusal NAMES
        # the answers that would be accepted rather than only objecting.
        assert "not one of the choices offered" in refused.text
        assert "yes, no" in refused.text
        assert _reload(conn, placement, layer_id).auditable is False


class TestTheOnePlacementTable:
    def test_the_details_row_offers_every_layer_field_placed_for_it(
        self, app_and_org
    ) -> None:
        """`tower_cells` used to be a hand-written tuple of five names beside
        `_PLACED`'s list — a second placement table, which is exactly how
        `layer.auditable` came to be declared and rendered nowhere. It is
        derived now; this is the assertion that keeps it derived."""
        from bookkit.web.routes.program import _PLACED

        client, org = app_and_org
        conn = client.app.state.conn
        placement, layer_id = _statutory_layer(conn, org)
        row = _details(client, org, placement, layer_id)

        missing = [
            key for key in _PLACED
            if key.startswith("layer.") and f'data-field="{key}"' not in row
        ]
        assert not missing, f"placed on the layer and rendered nowhere: {missing}"
