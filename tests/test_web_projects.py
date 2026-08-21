"""The Projects tab: a client's jobs, and the cover each one still needs.

The fifth account tab, and the last one the TUI had that a browser did not.
Master/detail — projects, then the selected project's needs — with selection in
the QUERY STRING so a view is a link and the back button behaves.

The seeded book carries no projects (seed.py creates none), so these build
their own rather than asserting against a fixture that happens to be empty —
a test that passes because there was nothing to look at asserts nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.repo import projects as projects_repo
from bookkit.web.app import create_app


@pytest.fixture
def client_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs

    org = orgs.list_orgs(app.state.conn, kind="client")[0]
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


@pytest.fixture
def with_project(client_org):
    """One project carrying two needs — one open, one placed — so the settled
    path is exercised as well as the attention one."""
    client, org = client_org
    conn = client.app.state.conn
    project = projects_repo.create_project(
        conn, org.id, "Riverside Expansion",
        site="Riverside", status="active",
        start_on="2026-03-01", end_on="2027-06-30",
    )
    open_need = projects_repo.add_need(
        conn, project.id, "Builder's Risk", "2026-09-15",
        limit_cents=20_000_000_00, status="identified",
    )
    projects_repo.add_need(
        conn, project.id, "General Liability", "2026-04-01", status="placed"
    )
    return client, org, project, open_need


def _cells(page: str, ref: str) -> list[str]:
    return re.findall(rf'data-cell-action="(/accounts/{ref}/projects/[^"]+)"', page)


# --- the tab itself -------------------------------------------------------------


class TestTheTab:
    def test_it_is_a_real_tab_with_a_count(self, with_project) -> None:
        client, org, _project, _need = with_project
        page = client.get(f"/accounts/{org.ref}/relationship").text

        assert f'href="/accounts/{org.ref}/projects"' in page

    def test_the_badge_counts_OPEN_needs_only(self, with_project) -> None:
        """A completed project's placed cover is not work, and a badge that
        counts it never falls to zero — the rule the `work` count follows."""
        from bookkit.web.routes.account import _counts

        client, org, _project, _need = with_project
        counts = _counts(client.app.state.conn, org, 0)

        assert counts["projects"] == 1, "the placed need was counted as work"

    def test_it_lists_the_projects_and_the_selected_ones_needs(
        self, with_project
    ) -> None:
        client, org, project, _need = with_project
        page = client.get(f"/accounts/{org.ref}/projects").text

        assert 'id="projects-panel"' in page
        assert project.ref in page
        assert "Riverside Expansion" in page
        assert "Builder&#39;s Risk" in page or "Builder's Risk" in page

    def test_selection_is_a_link_not_a_click_handler(self, with_project) -> None:
        """The view is a URL: ?project= survives a reload and the back button,
        the same reason /items' filters live in the query string."""
        client, org, project, _need = with_project
        page = client.get(f"/accounts/{org.ref}/projects").text

        assert f'href="/accounts/{org.ref}/projects?project={project.id}"' in page

    def test_the_first_project_is_selected_when_none_is_asked_for(
        self, with_project
    ) -> None:
        """A master/detail with nothing selected is a half-empty screen that
        makes the reader do the app's job."""
        client, org, _project, need = with_project
        page = client.get(f"/accounts/{org.ref}/projects").text

        assert f"/projects/needs/{need.id}/cell/" in page

    def test_an_account_with_no_projects_says_so(self, client_org) -> None:
        client, org = client_org
        page = client.get(f"/accounts/{org.ref}/projects").text

        assert "no projects yet" in page
        assert "add a project to record what it needs" in page


# --- editing in place -----------------------------------------------------------


class TestCells:
    def test_every_project_field_is_editable_where_it_is_read(
        self, with_project
    ) -> None:
        from bookkit.forms.inline import PROJECT_FIELDS

        client, org, project, _need = with_project
        page = client.get(f"/accounts/{org.ref}/projects").text

        for field in PROJECT_FIELDS:
            assert f"/projects/{project.id}/cell/{field.key}" in page, field.key

    def test_every_need_field_is_editable_where_it_is_read(
        self, with_project
    ) -> None:
        from bookkit.forms.inline import NEED_FIELDS

        client, org, _project, need = with_project
        page = client.get(f"/accounts/{org.ref}/projects").text

        for field in NEED_FIELDS:
            assert f"/projects/needs/{need.id}/cell/{field.key}" in page, field.key

    def test_a_project_cell_saves(self, with_project) -> None:
        client, org, project, _need = with_project
        conn = client.app.state.conn

        saved = client.post(
            f"/accounts/{org.ref}/projects/{project.id}/cell/site",
            data={"site": "Riverside, North Bank"},
        )

        assert saved.status_code == 200
        assert projects_repo.get_project(conn, project.id).site == (
            "Riverside, North Bank"
        )

    def test_a_need_cell_saves_money_as_cents(self, with_project) -> None:
        client, org, _project, need = with_project
        conn = client.app.state.conn

        client.post(
            f"/accounts/{org.ref}/projects/needs/{need.id}/cell/limit_cents",
            data={"limit_cents": "25m"},
        )

        assert projects_repo.get_need(conn, need.id).limit_cents == 25_000_000_00

    def test_the_countdown_comes_back_with_the_saved_date(
        self, with_project
    ) -> None:
        """Print the date you counted to. A saved date that answers without its
        badge leaves the OLD countdown beside the NEW date until a refresh —
        the four-surface bug, in miniature."""
        client, org, _project, need = with_project

        saved = client.post(
            f"/accounts/{org.ref}/projects/needs/{need.id}/cell/needed_by",
            data={"needed_by": "2020-01-01"},
        )

        assert "2020-01-01" in saved.text
        assert "over" in saved.text, "the date came back without its countdown"

    def test_a_refusal_keeps_the_typed_value_and_writes_nothing(
        self, with_project
    ) -> None:
        client, org, _project, need = with_project
        conn = client.app.state.conn
        before = projects_repo.get_need(conn, need.id).needed_by

        refused = client.post(
            f"/accounts/{org.ref}/projects/needs/{need.id}/cell/needed_by",
            data={"needed_by": "5"},
        )

        assert refused.status_code == 200
        assert 'value="5"' in refused.text, "the typed value was thrown away"
        assert projects_repo.get_need(conn, need.id).needed_by == before

    def test_a_field_with_no_cell_is_refused(self, with_project) -> None:
        """The editable set is checked SERVER-SIDE: markup constrains a mouse
        and nothing else."""
        client, org, _project, need = with_project

        got = client.post(
            f"/accounts/{org.ref}/projects/needs/{need.id}/cell/opportunity_id",
            data={"opportunity_id": "whatever"},
        )

        assert got.status_code == 404

    def test_another_accounts_project_is_a_404(self, with_project) -> None:
        from bookkit.repo import orgs

        client, org, project, _need = with_project
        other = next(
            o for o in orgs.list_orgs(client.app.state.conn, kind="client")
            if o.id != org.id
        )

        got = client.post(
            f"/accounts/{other.ref}/projects/{project.id}/cell/site",
            data={"site": "nope"},
        )

        assert got.status_code == 404


# --- adding ---------------------------------------------------------------------


class TestAdding:
    def test_the_project_form_offers_a_blank_status_option(
        self, client_org
    ) -> None:
        """Every select renders a blank option, required or not — without one
        the browser answers a question nobody was asked."""
        client, org = client_org
        form = client.get(f"/accounts/{org.ref}/projects/new").text

        assert "<select" in form
        assert re.search(r'<option value=""', form)

    def test_a_project_is_created_and_the_panel_comes_back(
        self, client_org
    ) -> None:
        client, org = client_org
        conn = client.app.state.conn

        made = client.post(
            f"/accounts/{org.ref}/projects/new",
            data={"name": "Depot Rebuild", "status": "planned"},
        )

        assert made.status_code == 200
        assert "Depot Rebuild" in made.text
        assert [p for p in projects_repo.projects_for_org(conn, org.id)]

    def test_the_need_form_never_pre_fills_a_figure(self, with_project) -> None:
        """People do not check prefills, and a limit comes off a document."""
        client, org, project, _need = with_project

        form = client.get(
            f"/accounts/{org.ref}/projects/{project.id}/needs/new"
        ).text

        for money_field in ("limit_cents", "premium_indication_cents"):
            match = re.search(rf'name="{money_field}"[^>]*value="([^"]*)"', form)
            assert not (match and match.group(1)), f"{money_field} was pre-filled"

    def test_a_need_is_added_to_the_project_it_was_opened_from(
        self, with_project
    ) -> None:
        client, org, project, _need = with_project
        conn = client.app.state.conn

        client.post(
            f"/accounts/{org.ref}/projects/{project.id}/needs/new",
            data={"line": "Pollution", "needed_by": "2026-10-01",
                  "status": "identified"},
        )

        lines = {n.line for n in projects_repo.needs_for_project(conn, project.id)}
        assert "Pollution" in lines


# --- a need becomes a pursuit -----------------------------------------------------


class TestNeedToOpportunity:
    def test_it_creates_a_linked_opportunity_carrying_the_needs_own_facts(
        self, with_project
    ) -> None:
        from bookkit.repo import opportunities as opportunities_repo

        client, org, _project, need = with_project
        conn = client.app.state.conn

        done = client.post(
            f"/accounts/{org.ref}/projects/needs/{need.id}/opportunity"
        )

        assert done.status_code == 200
        fresh = projects_repo.get_need(conn, need.id)
        assert fresh.opportunity_id
        opportunity = opportunities_repo.get(conn, fresh.opportunity_id)
        assert need.line in opportunity.title
        assert opportunity.target_effective == need.needed_by

    def test_it_is_one_revertible_batch(self, with_project) -> None:
        from bookkit.repo import batches as batches_repo
        from bookkit.services import batches as batches_svc

        client, org, _project, need = with_project
        conn = client.app.state.conn
        client.post(f"/accounts/{org.ref}/projects/needs/{need.id}/opportunity")

        batch = batches_repo.recent(conn, "0000", limit=1)[0]
        assert batch.tool == "need_to_opportunity"
        batches_svc.revert(conn, batch.ref, now="2026-08-21T00:00:00Z")

        assert projects_repo.get_need(conn, need.id).opportunity_id is None

    def test_the_second_press_refuses_rather_than_making_a_second_one(
        self, with_project
    ) -> None:
        from bookkit.repo import opportunities as opportunities_repo

        client, org, _project, need = with_project
        conn = client.app.state.conn
        url = f"/accounts/{org.ref}/projects/needs/{need.id}/opportunity"
        client.post(url)
        before = len(opportunities_repo.for_org(conn, org.id, open_only=False))

        again = client.post(url)

        assert "already has an opportunity" in again.text
        assert len(opportunities_repo.for_org(conn, org.id, open_only=False)) == before

    def test_a_linked_need_offers_no_promote_button(self, with_project) -> None:
        """D4: never draw a control that cannot work."""
        client, org, _project, need = with_project
        client.post(f"/accounts/{org.ref}/projects/needs/{need.id}/opportunity")

        page = client.get(f"/accounts/{org.ref}/projects").text
        row = page[page.index(f"needs/{need.id}/cell/line") :]
        row = row[: row.index("</tr>")]

        assert "→ opportunity" not in row
