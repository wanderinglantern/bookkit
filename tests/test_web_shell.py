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


def test_healthz_reports_the_database(client, snapshot_db: Path):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["db"] == str(snapshot_db)


def test_the_connection_closes_on_shutdown(snapshot_db: Path):
    """A WAL database abandoned to the GC never gets a final checkpoint."""
    import sqlite3

    app = create_app(snapshot_db)
    with TestClient(app):
        app.state.conn.execute("SELECT 1")  # open during the app's life
    with pytest.raises(sqlite3.ProgrammingError):
        app.state.conn.execute("SELECT 1")  # closed after it


def test_static_htmx_is_served(client):
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text[:2000].lower()


def test_cli_registers_the_web_command():
    from bookkit.cli import build_parser

    args = build_parser().parse_args(["web", "--port", "8931"])
    assert args.command == "web"
    assert args.port == 8931


def test_serve_binds_loopback_only(db_path: Path, monkeypatch):
    """The database is 0600 and holds client contacts and premium figures, so
    the server must never bind 0.0.0.0 and publish the book to the LAN.

    This asserts the host uvicorn is actually handed, not the module's source
    text — a substring scan cannot tell a bind from a comment warning against
    one, and made the comment worse to stay green.

    Uses the tmp_path-backed db_path fixture rather than db_path=None: None
    resolves through db.default_db_path() to the real book, and a test has
    no business creating or migrating that file."""
    import uvicorn

    from bookkit.web import serve as serve_mod

    seen: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))

    serve_mod.serve(db_path, 8931, open_browser=False)

    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8931
