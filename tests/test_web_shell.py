"""The server starts, serves its shell, and stays on loopback."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app) as test_client:
        yield test_client


def test_healthz_reports_the_database(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_static_htmx_is_served(client):
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text[:2000].lower()


def test_cli_registers_the_web_command():
    from bookkit.cli import build_parser

    args = build_parser().parse_args(["web", "--port", "8931"])
    assert args.command == "web"
    assert args.port == 8931


def test_serve_binds_loopback_only():
    """The database is 0600 and holds client contacts and premium figures.
    0.0.0.0 would publish the whole book to the LAN."""
    import inspect

    from bookkit.web import serve

    source = inspect.getsource(serve)
    assert "127.0.0.1" in source
    assert "0.0.0.0" not in source
