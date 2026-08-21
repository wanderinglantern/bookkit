"""uvicorn bootstrap. Loopback only — the database holds client contacts and
premium figures at mode 0600. Never `0.0.0.0` — that would publish the whole
book to the LAN.

It also runs `doctor` first, and REFUSES TO SERVE on a blocked finding. A
launcher that starts happily into a broken install turns a one-command fix into
a traceback twenty minutes later; see doctor.py for the two that argued for
it."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"


def serve(db_path: Path | str | None, port: int, open_browser: bool = True) -> int:
    import uvicorn

    from .. import doctor
    from . import portguard
    from .app import create_app

    # THE DOCTOR RUNS BEFORE THE PORT IS TAKEN. A blocked finding means serving
    # anyway produces a 500 in a route later — which is how a version skew
    # spent an afternoon presenting as "the toggle arrow is non functional"
    # (Grant, 2026-08-21). Better to refuse at the door, naming the command.
    found = doctor.findings()
    if found:
        print(doctor.report(found))
    if doctor.blocked(found):
        print("\nnot serving: fix the above first.")
        return 1

    try:
        portguard.reclaim(HOST, port)
    except portguard.PortHeld as refusal:
        portguard.say(str(refusal))
        return 1

    if open_browser:
        threading.Timer(0.7, webbrowser.open, args=(f"http://{HOST}:{port}/",)).start()
    uvicorn.run(create_app(db_path), host=HOST, port=port, log_level="warning")
    return 0
