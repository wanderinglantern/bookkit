"""FastAPI application factory.

The web layer holds no field lists, no validators, no normalisation and no
SQL: it reads through repo/, renders a FormSpec from bookkit.forms, and writes
through the same apply_* the TUI calls, inside one batch."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))


def create_app(db_path: Path | str | None = None) -> FastAPI:
    from .. import db

    app = FastAPI(title="bookkit", docs_url=None, redoc_url=None)
    conn: sqlite3.Connection = db.connect(db_path)
    app.state.conn = conn
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "db": str(db_path) if db_path else "default"}

    from .routes import account

    app.include_router(account.router)
    return app
