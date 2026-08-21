"""Building a tower in the browser: the stack, not the whole program.

Spec: docs/superpowers/specs/2026-08-21-web-tower-builder-design.md
"""

from __future__ import annotations

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
        o for o in orgs.list_orgs(conn, kind="client")
        if [p for p in placements.for_org(conn, o.id) if p.program_path]
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _linked(conn, org):
    from bookkit.repo import placements

    return next(p for p in placements.for_org(conn, org.id) if p.program_path)


def test_layer_details_reports_whether_a_slab_is_a_buffer(app_and_org) -> None:
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)

    rows = sync.layer_details(conn, placement.id)

    assert rows, "fixture drifted — no layers"
    assert all("buffer" in row for row in rows)
    assert not any(row["buffer"] for row in rows)
