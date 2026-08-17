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

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "db": str(db_path) if db_path else "default"}

    from .routes import account

    app.include_router(account.router)
    return app
