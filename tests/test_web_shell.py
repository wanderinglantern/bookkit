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


def test_theme_css_is_served_not_shadowed_by_the_static_mount(client):
    """/static/theme.css is a route registered before app.mount("/static", ...);
    a StaticFiles mount on the same prefix would otherwise shadow it and this
    would 404 (or serve a stale file) instead of the generated stylesheet."""
    response = client.get("/static/theme.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "--ink" in response.text
    assert ":root" in response.text


def test_theme_css_comes_from_the_one_palette(client):
    """Colour is signal. A second palette in a stylesheet is how two surfaces
    come to disagree about what red means."""
    from bookkit import palette

    response = client.get("/static/theme.css")
    assert response.status_code == 200
    for name in palette.WEB_TOKENS:
        assert getattr(palette, name) in response.text, f"{name} missing from theme.css"


def test_the_stylesheet_has_no_literal_colours():
    """Every colour comes from theme.css, generated from the palette module."""
    import re
    from pathlib import Path

    import bookkit

    css = (Path(bookkit.__file__).parent / "web" / "static" / "app.css").read_text()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "literal hex in app.css"
    assert not re.search(r"\b(rgb|hsl)a?\(", css), "literal rgb/hsl in app.css"


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
