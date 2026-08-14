from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date as _date
from pathlib import Path

import pytest

from bookkit import db

# The day the snapshot suite pretends it is. Snapshots render absolute dates
# ("2026-09-03") and countdowns ("◆ 345d over") side by side, so a suite that
# ran against the real clock would diff itself every morning and be switched
# off inside a week — which is exactly how the old write-only snapshots earned
# their place in the review as artifacts rather than tests (F14).
FROZEN_TODAY = _date(2026, 8, 14)
FROZEN_NOW = "2026-08-14T09:00:00+00:00"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


class _FrozenDate(_date):
    @classmethod
    def today(cls) -> _date:
        return _date(FROZEN_TODAY.year, FROZEN_TODAY.month, FROZEN_TODAY.day)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> _date:
    """Pin `date.today()` and `db.utc_now()` across every bookkit module.

    Fourteen modules do `from datetime import date` and call `date.today()` at
    render time, so patching one place is not enough. Import the whole package
    first, then rebind the name wherever it is the stdlib class — monkeypatch
    puts it all back afterwards.
    """
    import importlib
    import pkgutil

    import bookkit

    for info in pkgutil.walk_packages(bookkit.__path__, prefix="bookkit."):
        try:
            importlib.import_module(info.name)
        except Exception:  # optional/py-version-gated modules must not break setup
            continue

    import sys

    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if not name.startswith("bookkit"):
            continue
        if getattr(module, "date", None) is _date:
            monkeypatch.setattr(module, "date", _FrozenDate, raising=False)
    monkeypatch.setattr(db, "utc_now", lambda: FROZEN_NOW)
    return FROZEN_TODAY


@pytest.fixture
def snapshot_db(tmp_path: Path, frozen_clock: _date) -> Path:
    """A seeded book as of FROZEN_TODAY, with the towerkit programs projected
    so the placements tab has a real tower to render."""
    from bookkit import seed, sync

    path = tmp_path / "snapshot.db"
    programs = tmp_path / "programs"
    connection = db.connect(path)
    seed.seed(connection, today=frozen_clock, programs_dir=programs)
    sync.project_all(connection, [programs])
    connection.close()
    return path
