from __future__ import annotations

from pathlib import Path

import pytest

from bookkit import db
from bookkit.cli import main


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "cli.db"
    monkeypatch.setenv("BOOKKIT_DB", str(path))
    return path


def test_init_creates_db(cli_db: Path, capsys) -> None:
    assert main(["init"]) == 0
    assert cli_db.exists()
    assert "schema v1" in capsys.readouterr().out


def test_migrate_reports_up_to_date(cli_db: Path, capsys) -> None:
    main(["init"])
    assert main(["migrate"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_seed_today_renewals_search(cli_db: Path, capsys) -> None:
    assert main(["seed", "--demo"]) == 0
    out = capsys.readouterr().out
    assert "35 orgs" in out

    assert main(["today"]) == 0
    out = capsys.readouterr().out
    for heading in ("TASKS DUE", "RENEWALS NEXT 90 DAYS", "STALE ACCOUNTS", "SUBMISSIONS PAST SLA"):
        assert heading in out

    assert main(["renewals", "--days", "120"]) == 0
    assert "[" in capsys.readouterr().out

    assert main(["search", "atomic"]) == 0
    out = capsys.readouterr().out
    assert "ORGS" in out and "Atomic Industries" in out

    assert main(["search", "zzz-no-such-thing"]) == 1


def test_backup(cli_db: Path, tmp_path: Path, capsys) -> None:
    main(["init"])
    dest = tmp_path / "out" / "backup.db"
    assert main(["backup", "--dest", str(dest)]) == 0
    assert dest.exists()
    check = db.connect(dest, migrate=False)
    assert db.integrity_check(check)
    check.close()
