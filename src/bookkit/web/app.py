"""FastAPI application factory.

The web layer holds no field lists, no validators, no normalisation and no
SQL: it reads through repo/, renders a FormSpec from bookkit.forms, and writes
through the same apply_* the TUI calls, inside one batch."""

from __future__ import annotations

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

from . import theme_css as theme_css_mod

HERE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))


class ThreadConnections:
    """ONE sqlite3.Connection PER THREAD. Never share one across threads.

    Every route in web/routes/ is a sync `def`, so FastAPI runs it in an anyio
    worker thread — concurrent requests are concurrent threads. This app used
    to park a single connection on app.state.conn and hand it to all of them,
    and that is not a style preference: measured on the real app, 2 concurrent
    requests returned 2.6% wrong answers and 6 returned ~21%, including 404
    "no such account" for accounts that exist, saves that vanished behind a
    404, and — the one that outlives the request — event_log rows recording
    `old_value = NULL` for fields that had a value, which a later revert
    pastes back over live data. sqlite3.Connection keeps an LRU cache of
    prepared statements keyed by SQL text and steps them with the GIL
    released; two threads running the SAME query text on one connection step
    and reset one statement. Diagnosis, measurements and the three rejected
    alternatives:
    .superpowers/sdd/2026-08-17-web-account-page/flaky-batch-test-investigation.md

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
    # app.state.conn is the connection belonging to whichever thread called
    # create_app — the test process's own, never a request's. Routes take
    # theirs from app.state.connections; see ThreadConnections.
    app.state.conn = conn
    app.state.connections = connections

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "db": str(db_path) if db_path else "default"}

    # Registered before the StaticFiles mount below: a mount on the same
    # prefix ("/static") would otherwise shadow this route, since FastAPI/
    # Starlette resolves routes in registration order.
    @app.get("/static/theme.css")
    def theme_css() -> Response:
        return Response(content=theme_css_mod.css_variables(), media_type="text/css")

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    from .routes import account, book, changes, pipeline, relationship, work

    # book.router owns GET / and GET /book — the app's front door (Task 18).
    # Neither path overlaps /accounts/..., so registration order relative
    # to the routers below doesn't matter the way relationship's does.
    app.include_router(book.router)

    # relationship's GET /accounts/{ref}/relationship must be registered
    # before account's generic GET /accounts/{ref}/{tab}: both patterns
    # match the same two-segment path, and Starlette resolves routes in
    # registration order across routers, not by specificity — the router
    # added first wins a request either could serve.
    # changes.router owns POST /accounts/{ref}/changes/{batch_ref}/revert —
    # a three-segment path under /accounts that nothing else matches, so its
    # position here is free.
    app.include_router(changes.router)
    app.include_router(relationship.router)
    app.include_router(work.router)
    app.include_router(pipeline.router)
    app.include_router(account.router)
    return app
