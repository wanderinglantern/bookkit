"""FastAPI application factory.

The web layer holds no field lists, no validators, no normalisation and no
SQL: it reads through repo/, renders a FormSpec from bookkit.forms, and writes
through the same apply_* the TUI calls, inside one batch."""

from __future__ import annotations

import sqlite3
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


def create_app(db_path: Path | str | None = None) -> FastAPI:
    from .. import db

    # check_same_thread=False: the lifespan below (and every route that
    # follows) runs on the ASGI event loop's thread, not this one — see
    # db.connect's docstring.
    conn: sqlite3.Connection = db.connect(db_path, check_same_thread=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """The connection is opened eagerly so callers (and tests) can reach
        app.state.conn before startup, and closed here so a WAL database gets
        its final checkpoint instead of being abandoned to the GC."""
        try:
            yield
        finally:
            conn.close()

    app = FastAPI(title="bookkit", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.conn = conn

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
