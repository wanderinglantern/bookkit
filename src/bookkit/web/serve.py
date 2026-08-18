"""uvicorn bootstrap. Loopback only — the database holds client contacts and
premium figures at mode 0600. Never `0.0.0.0` — that would publish the whole
book to the LAN."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"


def serve(db_path: Path | str | None, port: int, open_browser: bool = True) -> int:
    import uvicorn

    from .app import create_app

    if open_browser:
        threading.Timer(0.7, webbrowser.open, args=(f"http://{HOST}:{port}/",)).start()
    uvicorn.run(create_app(db_path), host=HOST, port=port, log_level="warning")
    return 0
