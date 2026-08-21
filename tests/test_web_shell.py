"""The server starts, serves its shell, and stays on loopback."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
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
    with TestClient(app, base_url="http://127.0.0.1"):
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


def test_every_css_variable_the_stylesheet_uses_is_defined():
    """A `var(--name)` nobody defines does not fall back — it kills the whole
    declaration it sits in.

    `border-left: 1px solid var(--rule)` with `--rule` undefined computes the
    SHORTHAND to unset, so `border-left-style` becomes `none` and the rule
    simply is not drawn. Nothing warns: no console error, no visual artifact,
    and the markup still reads exactly as intended. That is what shipped —
    `--rule` is the TUI palette's name for borders (tui/theme.py), the web
    palette calls them `--hairline` / `--hairline-2` / `--border`, and the
    account page's program-scope bracket was invisible on the page while its
    commit message called it load-bearing.

    Every colour already has to come from the palette (the test above); this
    says every variable REFERENCE has to resolve, against theme.css plus
    app.css's own `:root` (which defines the non-colour tokens — fonts,
    sizes)."""
    import re
    from pathlib import Path

    import bookkit
    from bookkit.web import theme_css

    raw = (Path(bookkit.__file__).parent / "web" / "static" / "app.css").read_text()
    # comments out first: this file explains its own bugs, so `var(--rule)`
    # appears in prose right above the line that used to contain it
    css = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
    defined = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", theme_css.css_variables()))
    defined |= set(re.findall(r"^\s*(--[A-Za-z0-9_-]+)\s*:", css, re.MULTILINE))
    used = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", css))
    assert used, "no custom properties found in app.css — the scan is broken"
    undefined = sorted(used - defined)
    assert not undefined, (
        f"app.css references undefined custom properties {undefined} — an "
        "invalid var() voids its whole declaration silently"
    )


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

    from bookkit.web import portguard
    from bookkit.web import serve as serve_mod

    seen: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    # serve() reclaims the port before binding it, and 8931 may well be Grant's
    # own running server: a test must never be the thing that stops it.
    monkeypatch.setattr(portguard, "reclaim", lambda host, port, **kw: None)

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


def _app_css() -> str:
    return (_web_dir() / "static" / "app.css").read_text()


def _declared_families() -> set[str]:
    """The families app.css actually SHIPS, read off its @font-face blocks.

    Derived, never hand-listed: a literal ("Noto Sans", "Noto Serif",
    "JetBrains Mono") written here would reintroduce one level up exactly the
    hole the test below exists to close — the list would agree with itself
    while the stylesheet asked for nothing that ships."""
    families = set()
    for block in re.findall(r"@font-face\s*\{(.*?)\}", _app_css(), re.DOTALL):
        match = re.search(r"font-family:\s*([^;]+);", block)
        assert match, f"@font-face with no font-family:\n{block}"
        families.add(match.group(1).strip().strip("\"'"))
    assert families, "no @font-face families in app.css"
    return families


def _stack_leads() -> dict[str, str]:
    """The FIRST family in each of --sans / --serif / --mono."""
    leads = {}
    css = _app_css()
    for name in ("sans", "serif", "mono"):
        match = re.search(rf"--{name}:\s*([^;]+);", css)
        assert match, f"--{name} is not defined in app.css"
        leads[name] = match.group(1).split(",")[0].strip().strip("\"'")
    return leads


def test_every_vendored_family_leads_the_stack_that_uses_it():
    """THE test. Vendoring, serving and declaring all seven files is worth
    nothing if no stack asks for them, and every other test here stays green
    through that: rename a @font-face family to "Noto Sanz", or delete
    "JetBrains Mono" from --mono, and the files still ship, still resolve and
    still serve — the page just quietly renders in system-ui again. This is
    CLAUDE.md's own rule ("a green suite proves nothing broke, not that the
    new path is taken") applied to the one seam this branch adds.

    Both directions in one equality: every declared family must lead a stack
    (nothing is vendored that the page never asks for) and every stack must be
    led by a declared family (no stack silently falls back to the system)."""
    leads = _stack_leads()
    assert len(set(leads.values())) == 3, f"two stacks lead with the same family: {leads}"
    assert set(leads.values()) == _declared_families(), (
        f"the stacks ask for {sorted(set(leads.values()))} but app.css declares "
        f"{sorted(_declared_families())} — a vendored family the page never "
        f"requests, or a stack that falls straight through to the system font"
    )


def test_the_theme_import_precedes_every_font_face():
    """@import must come first — CSS ignores one that follows any rule other
    than @charset/@layer. Move a @font-face above it and the import of
    theme.css is discarded silently, which drops EVERY --colour token on the
    page: total blast radius, no error anywhere, and nothing else here notices
    because the fonts themselves still load."""
    css = _app_css()
    first_import = css.find("@import")
    first_face = css.find("@font-face")
    assert first_import != -1, "app.css no longer imports theme.css"
    assert first_face != -1, "app.css declares no @font-face"
    assert first_import < first_face, (
        "a @font-face precedes the @import of theme.css — the import is "
        "invalid there and every colour token on the page is lost"
    )


def test_every_mono_rule_switches_ligatures_off():
    """JetBrains Mono ships `calt`, which rewrites -> != == -- :: ... on sight.
    Nothing in the chrome hits one (the empty-value placeholder is — U+2014,
    not --), but free text reaches a td.mono — a filename like
    renewal--2026.xlsx. Paired at every site rather than set once on :root,
    because font-variant-ligatures INHERITS and `none` would also kill the
    common ligatures Noto Sans and Noto Serif do ship."""
    css = _app_css()
    consumers = css.count("font-family: var(--mono);")
    assert consumers, "no --mono consumers found — did the property get renamed?"
    guarded = len(
        re.findall(r"font-family: var\(--mono\);\n\s*font-variant-ligatures: none;", css)
    )
    assert guarded == consumers, (
        f"{consumers - guarded} of {consumers} --mono rules do not switch "
        f"ligatures off; put font-variant-ligatures: none on the next line"
    )


def test_the_open_font_licences_ship_beside_the_fonts_they_cover():
    """An OFL obligation, not a nicety: the notice must travel with the font,
    including inside the wheel. Both licence files could be deleted today
    without a single test noticing.

    Checked under the INSTALLED package directory, which is what hatch ships
    (`packages = ["src/bookkit"]` copies the tree wholesale), so a file that
    is here is a file in the wheel. Matched by derivation — each font's
    project prefix must be covered by some OFL-<project>.txt — so deleting
    either licence fails, rather than a hand-written pair of filenames that
    would only agree with itself."""
    fonts_dir = _web_dir() / "static" / "fonts"
    licences = sorted(fonts_dir.glob("OFL*.txt"))
    assert licences, "no OFL notice ships with the vendored fonts"
    for licence in licences:
        assert "SIL OPEN FONT LICENSE" in licence.read_text().upper(), (
            f"{licence.name} is not an OFL notice"
        )
    projects = [licence.stem.removeprefix("OFL-") for licence in licences]
    for font in sorted(fonts_dir.glob("*.woff2")):
        family = font.name.split("-")[0]  # NotoSans, NotoSerif, JetBrainsMono
        assert any(family.startswith(project) for project in projects), (
            f"{font.name} ships with no OFL notice covering it (have {projects})"
        )


def test_woff2_is_typed_without_the_system_mime_files():
    """The .woff2 -> font/woff2 mapping is NOT in Python's own table, and this
    is the test that stops it being deleted again for that wrong reason.

    Wiping `mimetypes.knownfiles` is the only honest way to check it: the
    obvious `mimetypes.init(files=())` is a NO-OP, because CPython's init does
    `files = knownfiles + list(files)` and re-reads the system files anyway.
    With them gone — the state of python:3.13-slim, Alpine, and most CI
    images — the stdlib types .woff2 as nothing at all, StaticFiles serves
    every vendored font as application/octet-stream, and
    test_the_vendored_fonts_are_served above fails on any machine that is not
    a Mac."""
    import mimetypes

    from bookkit.web import app as app_module

    original = mimetypes.knownfiles
    try:
        mimetypes.knownfiles = []
        mimetypes.init()  # stdlib defaults only
        assert mimetypes.guess_type("x.woff2")[0] is None, (
            "this Python now types .woff2 by itself, so web/app.py's "
            "_register_font_types may finally be redundant — but check the "
            "OLDEST Python in requires-python before deleting it, and delete "
            "this test in the same commit"
        )
        app_module._register_font_types()
        assert mimetypes.guess_type("x.woff2")[0] == "font/woff2"
    finally:
        mimetypes.knownfiles = original
        mimetypes.init()
        app_module._register_font_types()  # init() dropped it; put it back


# --- the origin control, beside the network one -------------------------------
#
# serve.py binds loopback and the test above pins it. That is a NETWORK
# control, and the browser is already on the loopback, so it was standing where
# an origin control belongs: a reviewer drove a cross-origin POST that created
# a contact and then reverted a real edit — and services.batches.revert writes
# its restoration WITHOUT a batch_id, so that one cannot be undone. A forged
# `Host: evil.example` served /book in full. bookkit.web.origin says what the
# two checks are and what was rejected instead of them (no CSRF token flow).

_EVIL = {"Origin": "https://evil.example", "Referer": "https://evil.example/x"}


@pytest.fixture
def writable(snapshot_db: Path):
    """A client fixture that also hands back an account to write to."""
    from bookkit.repo import orgs as orgs_repo

    app = create_app(snapshot_db)
    org = orgs_repo.list_orgs(app.state.conn, kind="client")[0]
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client, org


def test_a_cross_origin_write_is_refused_and_writes_nothing(writable):
    """The reviewer's reproduction: a POST from another site's page."""
    from bookkit.repo import contacts as contacts_repo

    client, org = writable
    conn = client.app.state.conn
    before = len(contacts_repo.for_org(conn, org.id))

    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "Mallory", "last_name": "Forged"},
        headers=_EVIL,
    )

    assert response.status_code == 403
    assert len(contacts_repo.for_org(conn, org.id)) == before


def test_a_cross_origin_revert_is_refused(writable):
    """The one that matters most: a revert restores a field with no batch_id
    of its own, so nothing on either surface can take it back."""
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import contacts as contacts_repo
    from bookkit.services import batches as batches_svc

    client, org = writable
    conn = client.app.state.conn
    victim = contacts_repo.for_org(conn, org.id)[0]
    with batches_svc.open_batch(
        conn, source="tui", tool="edit_contact",
        summary=f"set title on {victim.name}", org_id=org.id,
    ):
        contacts_repo.update(conn, victim.id, title="Treasurer")
    batch = batches_repo.recent(conn, since="", limit=1)[0]

    response = client.post(
        f"/accounts/{org.ref}/changes/{batch.ref}/revert", headers=_EVIL
    )

    assert response.status_code == 403
    assert contacts_repo.get(conn, victim.id).title == "Treasurer"


def test_a_forged_host_is_refused_even_on_a_read(writable):
    """The Host check is what makes the loopback binding mean what it says: a
    DNS-rebinding page resolves its own name to 127.0.0.1 and is then
    same-origin, so no request header is out of place — only the NAME is."""
    client, org = writable

    response = client.get("/book", headers={"Host": "evil.example"})

    assert response.status_code == 400
    assert org.name not in response.text


def test_a_same_origin_write_still_goes_through(writable):
    """The guard has to let the app work: this is what the real page sends."""
    from bookkit.repo import contacts as contacts_repo

    client, org = writable
    conn = client.app.state.conn
    before = len(contacts_repo.for_org(conn, org.id))

    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "Rosa", "last_name": "Legitimate"},
        headers={"Origin": "http://127.0.0.1", "Referer": "http://127.0.0.1/book"},
    )

    assert response.status_code == 200
    assert len(contacts_repo.for_org(conn, org.id)) == before + 1


def test_another_local_port_is_a_different_origin(writable):
    """Same host, different port, is not the same origin — and another app on
    this machine is exactly who else can reach a loopback port."""
    from bookkit.repo import contacts as contacts_repo

    client, org = writable
    conn = client.app.state.conn
    before = len(contacts_repo.for_org(conn, org.id))

    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "Mallory", "last_name": "Neighbour"},
        headers={"Origin": "http://127.0.0.1:9999"},
    )

    assert response.status_code == 403
    assert len(contacts_repo.for_org(conn, org.id)) == before


def test_a_referer_stands_in_when_origin_is_absent(writable):
    from bookkit.repo import contacts as contacts_repo

    client, org = writable
    conn = client.app.state.conn
    before = len(contacts_repo.for_org(conn, org.id))

    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "Mallory", "last_name": "Referred"},
        headers={"Referer": "https://evil.example/x"},
    )

    assert response.status_code == 403
    assert len(contacts_repo.for_org(conn, org.id)) == before


def test_a_request_with_neither_header_is_allowed(writable):
    """Deliberate, and the reason every other test in this file still works
    unchanged: browsers send Origin on every non-GET, so a request carrying
    neither header is a script at this machine's own keyboard — the party the
    loopback binding already trusts. Refusing it breaks local scripting to
    protect against nothing a browser can do."""
    from bookkit.repo import contacts as contacts_repo

    client, org = writable
    conn = client.app.state.conn
    before = len(contacts_repo.for_org(conn, org.id))

    response = client.post(
        f"/accounts/{org.ref}/contacts/new",
        data={"first_name": "Rosa", "last_name": "Scripted"},
    )

    assert response.status_code == 200
    assert len(contacts_repo.for_org(conn, org.id)) == before + 1


def test_a_cross_origin_read_is_left_to_the_host_check(writable):
    """GETs are not gated on Origin: they write nothing, and a cross-origin
    page cannot READ the response without CORS headers this app never sends.
    Gating them would break a plain link into the app from anywhere."""
    client, org = writable

    response = client.get(f"/accounts/{org.ref}/work", headers=_EVIL)

    assert response.status_code == 200


def test_the_binding_and_the_allowlist_cannot_drift(writable):
    """The network control and the origin control name the same host, by
    construction — whatever serve.py binds is a name this server answers to."""
    from bookkit.web import origin, serve

    assert serve.HOST in origin.LOOPBACK_HOSTS


@pytest.mark.parametrize(
    "authority,loopback",
    [
        ("127.0.0.1", True),
        ("127.0.0.1:8931", True),
        ("localhost:8931", True),
        ("[::1]:8931", True),
        ("::1", True),
        ("evil.example", False),
        ("evil.example:8931", False),
        ("127.0.0.1.evil.example", False),
        ("", False),
    ],
)
def test_which_names_this_server_answers_to(authority: str, loopback: bool):
    """`[::1]:8931` is why the port strip is not a plain rsplit: that turns a
    bare `::1` into `:`."""
    from bookkit.web.origin import is_loopback

    assert is_loopback(authority) is loopback


# --- the browser must notice an upgrade --------------------------------------


def test_the_stylesheet_and_scripts_carry_a_version(client):
    """Without a changing URL the browser keeps the CSS it already has, and an
    upgrade lands with the code changed and the page unchanged. That is
    indistinguishable from a fix that did not ship — it cost an hour of
    chasing a layout change that was correct on the server the whole time."""
    from bookkit.web.app import ASSET_VERSION

    page = client.get("/book").text

    for asset in ("app.css", "htmx.min.js", "inline-cell.js"):
        assert f"/static/{asset}?v={ASSET_VERSION}" in page, asset


def test_the_version_moves_when_the_stylesheet_does(tmp_path, monkeypatch):
    """A digest, not a release number: the stylesheet changes many times
    between versions, and a version that stands still while the file moves is
    exactly the case that misleads."""
    from bookkit.web import app as app_mod

    before = app_mod._asset_version()

    real = app_mod.HERE / "static" / "app.css"
    edited = tmp_path / "static"
    edited.mkdir()
    for existing in (app_mod.HERE / "static").glob("*.css"):
        (edited / existing.name).write_bytes(existing.read_bytes())
    for existing in (app_mod.HERE / "static").glob("*.js"):
        (edited / existing.name).write_bytes(existing.read_bytes())
    (edited / real.name).write_text(real.read_text() + "\n/* a change */\n")
    monkeypatch.setattr(app_mod, "HERE", tmp_path)

    assert app_mod._asset_version() != before


def test_the_generated_palette_is_revalidated_every_load(client):
    """theme.css is reached by an @import inside app.css, with a URL no
    template rewrites — so it cannot carry the digest and must revalidate."""
    response = client.get("/static/theme.css")

    assert response.status_code == 200
    assert "no-cache" in response.headers.get("cache-control", "")


def test_form_host_hygiene_script_is_wired(snapshot_db):
    """One open editor per section (F2): the behaviour lives in
    static/form-host.js. The suite has no JS runtime, so the honest
    server-side assertions are that the page loads it and the server serves
    it — a missing script tag is exactly how the behaviour would silently
    vanish."""
    from fastapi.testclient import TestClient

    from bookkit.web.app import create_app

    with TestClient(create_app(snapshot_db), base_url="http://127.0.0.1") as client:
        page = client.get("/book")
        asset = client.get("/static/form-host.js")

    assert "/static/form-host.js" in page.text
    assert asset.status_code == 200
    assert "htmx:beforeSwap" in asset.text


def test_the_cell_editor_description_is_not_dangling(client):
    """`aria-describedby` pointing at an id that is not on the page announces
    NOTHING — a silent accessibility failure that looks correct in the markup.
    The attribute and its target ship together or not at all.

    Blur commits and Escape discards (CLAUDE.md, 2026-08-20), so Escape is the
    only way out of a cell without writing, and the editor has to say so.
    """
    page = client.get("/").text
    assert 'id="cell-keys"' in page, "the shell dropped the cell-editor description"
    assert "Escape discards" in page

    from bookkit.forms.spec import Field
    from bookkit.web.forms_render import render_cell

    editor = render_cell(None, Field("limit_cents", "limit", "money"), "$5M", "/cell/x")
    assert 'aria-describedby="cell-keys"' in editor


def test_no_hover_revealed_control_is_hidden_from_the_keyboard():
    """`visibility: hidden` removes an element from the FOCUS ORDER.

    The chip controls (remove, reorder) are held back until their chip is
    hovered or focused, which is right — twelve visible "remove" links in one
    strip is noise. Doing it with `visibility: hidden` makes them unreachable
    by keyboard entirely: `.focus()` does nothing, so `:focus-within` never
    fires and the reveal can never happen. Verified in Chrome the day it was
    written (2026-08-20) — `focusable: false` before, `true` after. It is the
    dead-affordance class tests/test_dead_keys.py exists to stop on the
    terminal side, arriving through a purely visual rule.

    `opacity: 0` keeps the element focusable and keeps the chip's width fixed.
    """
    import re
    from pathlib import Path

    css = (
        Path(__file__).resolve().parents[1]
        / "src" / "bookkit" / "web" / "static" / "app.css"
    ).read_text()
    # comments explain the rule and must not trip it
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    offenders = [
        block.strip().splitlines()[0].strip()
        for block in re.split(r"}\s*", css)
        if "visibility: hidden" in block
    ]
    assert not offenders, (
        "a control hidden with `visibility: hidden` cannot be focused, so any "
        f"focus-based reveal for it is dead: {offenders}"
    )


# --- blur-commit must not be able to wedge itself off ------------------------
#
# SOURCE ASSERTIONS, and they are the weakest kind — there is no JS harness in
# this project, so nothing here EXECUTES inline-cell.js. They exist because the
# bugs they pin were invisible to every other test and cost Grant real work:
# each behaviour was verified in a real browser (Playwright, 2026-08-21) and
# these guard the exact lines that verification turned on.


def _inline_cell_js() -> str:
    from bookkit.web import app as app_mod

    return (app_mod.HERE / "static" / "inline-cell.js").read_text()


def test_the_commit_mark_lives_on_the_form_not_in_a_global():
    """TWO bugs, one week, both from a page-global flag — pinned here.

    First the flag reset only when the completed request's element was the
    `.cell-editor` that submitted. That is never true after a save whose
    response swaps away the form's ancestors (every Program-tab save): htmx
    re-fires `htmx:afterRequest` on the nearest attached ancestor, a <section>
    is not a .cell-editor, so the flag stuck ON and blur-commit was dead until
    a reload. Grant: "sometimes i need to hit enter, other times not".

    Then the reset was widened to unconditional — and fresh-eyes review showed
    ANY unrelated request completing (another cell's Escape-revert) cleared the
    flag while a commit was still in flight, reopening the double-submit race
    the flag exists to prevent.

    The fix is no global at all: the mark lives on the form element itself
    (`__bkCommitting`), so one form's traffic cannot touch another's, and a
    detached form's mark dies with the node. The afterRequest reset touches
    only `detail.elt` when it IS the still-attached form — the network-error
    path, the one case where nothing swapped and blur must work again.
    """
    js = _inline_cell_js()

    assert "__bkCommitting" in js
    assert "var committing" not in js, "the page-global flag is back"

    # The focusout guard consults the FORM's own mark.
    focusout = js[js.index('addEventListener("focusout"') :]
    focusout = focusout[: focusout.index("});")]
    assert "form.__bkCommitting" in focusout

    # The afterRequest reset touches only the element that owned the request,
    # and only when it is a cell-editor — never a page-global, never a sweep.
    reset = js[js.index('addEventListener("htmx:afterRequest"') :]
    reset = reset[: reset.index("});")]
    assert "__bkCommitting = false" in reset
    assert 'classList.contains("cell-editor")' in reset


def test_the_saved_flash_only_fires_on_a_write():
    """Grant, 2026-08-21: "unclear when changes are saved as it just stays
    blue". A committed cell swaps back to a display cell identical to the one
    that was there before, so a flash marks the save.

    THE GATE IS THE POINT, not the flash: every revert — Escape's discard, the
    unchanged-value blur-close — is a GET returning the IDENTICAL markup a
    successful POST save returns, so the swapped element alone cannot say
    whether anything was written. Ungated, the flash congratulated the user on
    Escape — the exact opposite of "Escape discards" (fresh-eyes review,
    2026-08-21, driving the discard path the original browser check missed).
    """
    from bookkit.web import app as app_mod

    js = _inline_cell_js()
    css = (app_mod.HERE / "static" / "app.css").read_text()

    assert "cell-saved" in js
    assert ".cell.cell-saved" in css
    assert "prefers-reduced-motion" in css

    swap = js[js.index('addEventListener("htmx:afterSwap"') :]
    assert 'requestConfig.verb === "post"' in swap, "the flash lost its verb gate"
    # Raised from BOTH swap shapes — a plain cell save swaps the cell, a
    # program write swaps the whole section and only `refocus` knows which
    # cell to mark — and gated in both.
    assert js.count("flashSaved(") >= 3, "one of the two save shapes is unmarked"
    assert "if (wrote) flashSaved(cell);" in js, "the refocus flash lost its gate"


def test_escape_cannot_lose_its_race_to_a_timer():
    """PRE-EXISTING DATA-INTEGRITY BUG, found 2026-08-21 while verifying the
    saved-flash fix with real keys: Escape COMMITTED the value it discarded.

    The old guard was a global `cancelling` flag reset by `setTimeout(0)` —
    and the revert it guards is a network GET, so the swap-fired focusout
    arrives long after timeout zero. Flag false, value changed, the focusout
    handler submitted the discarded text; it landed in the database with an
    event-log row. Every earlier check had driven blur-commit, never
    Escape-then-watch-the-DB.

    The fix is a mark on the CELL node (`__bkCancelled`), which needs no
    timer: the revert's own swap replaces the cell and the new node arrives
    unmarked. So the property to pin is the absence of the timer as much as
    the presence of the mark.
    """
    js = _inline_cell_js()

    escape = js[js.index('if (evt.key !== "Escape") return;\n    pendingHop') :]
    escape = escape[: escape.index("});")]
    assert "__bkCancelled = true" in escape
    assert "setTimeout" not in escape, "the zero-timer race is back"

    focusout = js[js.index('addEventListener("focusout"') :]
    focusout = focusout[: focusout.index("});")]
    assert "cell.__bkCancelled" in focusout
