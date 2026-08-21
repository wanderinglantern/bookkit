"""FastAPI application factory.

The web layer holds no field lists, no validators, no normalisation and no
SQL: it reads through repo/, renders a FormSpec from bookkit.forms, and writes
through the same apply_* the TUI calls, inside one batch."""

from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
import threading
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import origin
from . import theme_css as theme_css_mod

HERE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))


def _asset_version() -> str:
    """A short digest of the served CSS and JS, appended to their URLs.

    Without it the browser keeps the stylesheet it already has. An upgrade
    then lands with the code changed and the page unchanged, which reads as
    "the fix did not ship" — it cost an hour of chasing a layout fix that was
    already correct on the server and stale in the browser (2026-08-19).
    Digest, not a release number: the stylesheet changes many times between
    versions, and a version that stands still while the file moves is exactly
    the case that misleads."""
    digest = hashlib.sha256()
    for name in sorted((HERE / "static").glob("*.css")) + sorted((HERE / "static").glob("*.js")):
        digest.update(name.read_bytes())
    # the palette is generated, not a file on disk, and a colour change alone
    # must still bust the cache
    digest.update(theme_css_mod.css_variables().encode())
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()
TEMPLATES.env.globals["asset_version"] = ASSET_VERSION


def _register_font_types() -> None:
    """Teach mimetypes about .woff2. NOT redundant — this was checked properly
    the second time.

    Python's own table does not carry it: on 3.13.5
    `mimetypes._types_map_default.get(".woff2")` is None, and `grep woff` over
    the stdlib module finds nothing. It resolves on THIS Mac only because
    `mimetypes.init()` reads /etc/apache2/mime.types, which macOS ships. On
    python:3.13-slim, on Alpine, and on most CI images there is no such file,
    StaticFiles falls back to application/octet-stream for every vendored
    font, and tests/test_web_shell.py::test_the_vendored_fonts_are_served goes
    red on a machine that is not this one.

    A named function rather than a bare module-level call so the invariant can
    be tested after wiping mimetypes back to stdlib defaults, without
    reloading this module. Deleting it is pinned by
    tests/test_web_shell.py::test_woff2_is_typed_without_the_system_mime_files
    — a `mimetypes.init(files=())` "proof" that it is dead is a NO-OP, because
    CPython does `files = knownfiles + list(files)`; the wipe is
    `mimetypes.knownfiles = []`.
    """
    mimetypes.add_type("font/woff2", ".woff2")


_register_font_types()


class ThreadConnections:
    """ONE sqlite3.Connection PER THREAD. Never share one across threads.

    The READ routes in web/routes/ are sync `def`, so FastAPI runs each one in
    an anyio worker thread — concurrent requests are concurrent threads. This
    app used to park a single connection on app.state.conn and hand it to all
    of them, and that is not a style preference: measured on the real app, 2
    concurrent requests returned 2.6% wrong answers and 6 returned ~21%,
    including 404 "no such account" for accounts that exist, saves that
    vanished behind a 404, and — the one that outlives the request — event_log
    rows recording `old_value = NULL` for fields that had a value, which a
    later revert pastes back over live data. sqlite3.Connection keeps an LRU
    cache of prepared statements keyed by SQL text and steps them with the
    GIL released; two threads running the SAME query text on one connection
    step and reset one statement. Diagnosis, measurements and the three rejected
    alternatives:
    .superpowers/sdd/2026-08-17-web-account-page/flaky-batch-test-investigation.md

    THE WRITE ROUTES ARE `async def` and this registry does not help them.
    There are eight (relationship.contact_create / contact_cell_save;
    work.task_create / task_cell_save / request_create / request_update /
    item_create / item_cell_save), all async only because they `await
    request.form()`. An async route runs ON the event loop, so it is handed
    whichever connection the loop's thread owns — under uvicorn that thread is
    the one that called create_app (serve.py), so every write route gets
    app.state.conn, the single shared connection. They are safe for a
    DIFFERENT reason than the reads: asyncio does not preempt, and no `await`
    occurs between any BEGIN and its COMMIT, so two write coroutines never
    interleave statements on it. That reason is a rule, not an accident —
    NEVER `await` inside a db.transaction / open_batch block, enforced by
    tests/test_conventions.py::test_no_await_inside_a_transaction. It cannot
    be caught by the web tests: TestClient runs the loop on a portal thread of
    its own, so under test the async routes get a connection nobody else has.

    Per-thread rather than a checked-out pool because it costs the two call
    sites nothing: `_conn(request)` in routes/account.py and routes/book.py
    already funnel every route, and neither has a place to hand a connection
    back at the end of a request without a middleware and a ContextVar. The
    thread-death leak a plain dict would create (anyio retires idle workers)
    is closed by keying on the thread WEAKLY: when a worker exits, its entry
    drops and CPython finalises the connection.

    db._tx_lock is newly load-bearing here, not newly redundant: writer
    contention moved from one connection's "transaction within a transaction"
    to the SQLite FILE lock across several. Ordinary saves shrug that off, but
    a writer holding a transaction past busy_timeout would turn a concurrent
    save into "database is locked" without the lock. Measured both ways under
    _tx_lock's own docstring; asserted by tests/test_web_concurrency.py."""

    def __init__(self, db_path: Path, primary: sqlite3.Connection) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conns: weakref.WeakKeyDictionary[
            threading.Thread, sqlite3.Connection
        ] = weakref.WeakKeyDictionary()
        self._conns[threading.current_thread()] = primary

    def get(self) -> sqlite3.Connection:
        """This thread's connection, opening one the first time it asks.

        migrate=False: migrations ran once, eagerly, on the primary in
        create_app. check_same_thread=False so close_all() below can reach a
        worker's connection from the shutdown thread — the strict default
        would make an orderly WAL checkpoint impossible."""
        from .. import db

        thread = threading.current_thread()
        with self._lock:
            conn = self._conns.get(thread)
            if conn is None:
                conn = db.connect(self._db_path, migrate=False, check_same_thread=False)
                self._conns[thread] = conn
            return conn

    def close_all(self) -> None:
        with self._lock:
            for conn in list(self._conns.values()):
                conn.close()
            self._conns.clear()


def create_app(db_path: Path | str | None = None) -> FastAPI:
    from .. import db

    # Resolved once: every thread must open the SAME file even if $BOOKKIT_DB
    # changes under a running server.
    resolved = Path(db_path) if db_path is not None else db.default_db_path()
    if str(resolved) == ":memory:":
        # Refused rather than half-worked: a per-thread ":memory:" gives every
        # request its OWN empty, unmigrated database, which fails as absurd
        # 404s and missing tables far from here. Point the web app at a file.
        raise ValueError(
            "the web app cannot run on an in-memory database — every request "
            "thread would get a separate empty one; pass a file path"
        )
    # check_same_thread=False: the lifespan below runs on the ASGI event
    # loop's thread, not this one — see db.connect's docstring. Migrations run
    # here, once, on this connection; every other one opens with migrate=False.
    conn: sqlite3.Connection = db.connect(resolved, check_same_thread=False)
    connections = ThreadConnections(resolved, conn)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """The primary connection is opened eagerly so callers (and tests) can
        reach app.state.conn before startup, and the whole SET is closed here
        so a WAL database gets its final checkpoint instead of being abandoned
        to the GC."""
        try:
            yield
        finally:
            connections.close_all()

    app = FastAPI(title="bookkit", docs_url=None, redoc_url=None, lifespan=lifespan)
    # Before any route, and before the static mount: serve.py binds loopback,
    # but the browser is ALREADY on the loopback, so the binding is a network
    # control standing where an origin control belongs. web/origin.py says what
    # the two checks are and what was rejected instead of them.
    app.add_middleware(origin.OriginGuard)
    # app.state.conn is the connection belonging to whichever thread called
    # create_app — the test process's own, never a request's. Routes take
    # theirs from app.state.connections; see ThreadConnections.
    app.state.conn = conn
    app.state.connections = connections

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        # `resolved`, not `db_path`: on the default path the argument is
        # None and "default" tells an operator nothing about which file the
        # server actually opened.
        return {"ok": True, "db": str(resolved)}

    # Registered before the StaticFiles mount below: a mount on the same
    # prefix ("/static") would otherwise shadow this route, since FastAPI/
    # Starlette resolves routes in registration order.
    @app.get("/static/theme.css")
    def theme_css() -> Response:
        # app.css and the scripts carry ?v=<digest>; this one cannot, because
        # it is reached by an @import inside app.css with a URL no template
        # rewrites. It is a kilobyte of generated custom properties, so it
        # revalidates on every load instead — a stale palette is the same
        # class of bug (the code changed, the page did not) and this file is
        # cheap enough that correctness wins.
        return Response(
            content=theme_css_mod.css_variables(),
            media_type="text/css",
            headers={"Cache-Control": "no-cache"},
        )

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    from .routes import (
        account,
        book,
        calendar,
        capture,
        changes,
        items,
        markets,
        orgs,
        pipeline,
        program,
        projects,
        relationship,
        search,
        team,
        today,
        towers,
        work,
    )

    # book.router owns GET / and GET /book — the app's front door (Task 18).
    # Neither path overlaps /accounts/..., so registration order relative
    # to the routers below doesn't matter the way relationship's does.
    app.include_router(book.router)
    app.include_router(items.router)
    app.include_router(capture.router)
    app.include_router(search.router)
    app.include_router(team.router)
    app.include_router(markets.router)
    app.include_router(today.router)
    app.include_router(calendar.router)
    app.include_router(towers.router)

    # relationship's GET /accounts/{ref}/relationship must be registered
    # before account's generic GET /accounts/{ref}/{tab}: both patterns
    # match the same two-segment path, and Starlette resolves routes in
    # registration order across routers, not by specificity — the router
    # added first wins a request either could serve.
    # changes.router owns POST /accounts/{ref}/changes/{batch_ref}/revert —
    # a three-segment path under /accounts that nothing else matches, so its
    # position here is free.
    app.include_router(orgs.router)
    app.include_router(changes.router)
    app.include_router(relationship.router)
    app.include_router(work.router)
    app.include_router(pipeline.router)
    app.include_router(projects.router)
    # program.router owns GET /accounts/{ref}/program — same two-segment shape
    # as the generic {tab} route below, so it must be registered first.
    app.include_router(program.router)
    app.include_router(account.router)
    return app
