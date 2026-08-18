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


# --- the self-hosted webfonts ------------------------------------------------
#
# The fonts are package data, and package data is the one thing pytest, mypy
# and ruff structurally cannot check: a stylesheet that names a weight nobody
# shipped does not fail anything — the browser silently synthesises it or
# falls back, and the app quietly stops looking like the design. These two
# tests close that in both directions (every src: has a file, every file has a
# src:) and a third proves the files are actually reachable over HTTP, which
# being on disk does not prove: /static is a mount, and a route registered
# ahead of it can shadow the whole prefix.


def _font_face_srcs() -> list[str]:
    """Every url() inside a @font-face src:, taken from app.css itself.

    Parsed rather than hand-listed on purpose: a hand-written list of
    filenames only ever asserts that the list matches itself, and would stay
    green through exactly the change these tests exist to catch."""
    import re
    from pathlib import Path

    import bookkit

    css = (Path(bookkit.__file__).parent / "web" / "static" / "app.css").read_text()
    urls = [
        url
        for block in re.findall(r"@font-face\s*\{(.*?)\}", css, re.DOTALL)
        for url in re.findall(r"""src:[^;]*url\(\s*["']?([^"')]+)""", block)
    ]
    assert urls, "no @font-face src: found in app.css — the parser, or the fonts, are gone"
    return urls


def _web_dir():
    from pathlib import Path

    import bookkit

    return Path(bookkit.__file__).parent / "web"


def test_every_font_face_src_resolves_to_a_file_in_package_data():
    """A missing weight must fail the build, not fall back silently."""
    for url in _font_face_srcs():
        path = _web_dir() / url.lstrip("/")
        assert path.is_file(), f"@font-face src {url} resolves to no file ({path})"


def test_every_vendored_font_file_is_declared_by_a_font_face():
    """The other direction. Without this, deleting a whole @font-face block
    pins nothing: the remaining src: lines all still resolve, so the set of
    weights the app declares is unguarded and a family can lose its bold in
    silence. Derived from the directory, so it cannot drift into a list."""
    srcs = " ".join(_font_face_srcs())
    shipped = sorted((_web_dir() / "static" / "fonts").glob("*.woff2"))
    assert shipped, "no .woff2 in package data — the fonts are not vendored"
    for font in shipped:
        assert font.name in srcs, f"{font.name} ships but no @font-face names it"


def test_the_vendored_fonts_are_served(client):
    """Present on disk is not the same as reachable: /static is a mount, and a
    route registered before it can shadow the prefix (see the theme.css test
    above). This asserts the HTTP path the browser will actually request."""
    for url in _font_face_srcs():
        response = client.get(url)
        assert response.status_code == 200, f"{url} is not served ({response.status_code})"
        assert response.headers["content-type"] == "font/woff2", url
        assert response.content[:4] == b"wOF2", f"{url} is not a woff2 file"
