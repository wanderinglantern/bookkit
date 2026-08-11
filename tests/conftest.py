from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from bookkit import db


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"
