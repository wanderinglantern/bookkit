from __future__ import annotations

import sys
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
    assert "schema v" in capsys.readouterr().out


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
    for heading in (
        "TASKS DUE", "RENEWALS NEXT 120 DAYS", "STALE ACCOUNTS", "SUBMISSIONS PAST SLA",
    ):
        assert heading in out

    assert main(["renewals", "--days", "120"]) == 0
    assert "[" in capsys.readouterr().out

    assert main(["search", "atomic"]) == 0
    out = capsys.readouterr().out
    assert "ORGS" in out and "Atomic Industries" in out

    assert main(["search", "zzz-no-such-thing"]) == 1


def test_sync_projects_and_reports(cli_db: Path, tmp_path: Path, capsys) -> None:
    programs = tmp_path / "programs"
    main(["seed", "--demo", "--programs-dir", str(programs)])
    capsys.readouterr()
    assert main(["sync", "--roots", str(programs)]) == 0
    out = capsys.readouterr().out
    assert "projected 3 file(s)" in out
    # an unlinked file is a review item, not an error: reported, exit stays 0
    source = (programs / "atomic-casualty.json").read_text()
    (programs / "orphan.json").write_text(
        source.replace("Atomic Industries, Inc.", "Someone Else")
    )
    assert main(["sync", "--roots", str(programs)]) == 0
    out = capsys.readouterr().out
    assert "not linked" in out


def _seeded_programs(cli_db: Path, tmp_path: Path) -> Path:
    """Three linked program files plus the book that knows about them."""
    from datetime import date

    from bookkit import seed

    main(["init"])
    programs = tmp_path / "programs"
    conn = db.connect(cli_db)
    seed.seed(conn, today=date(2026, 8, 11), programs_dir=programs)
    conn.close()
    return programs


def test_sync_one_path_projects_only_that_file(cli_db: Path, tmp_path: Path, capsys) -> None:
    """The towerkit MCP's post-write hook calls this per file — a full roots
    scan after every design edit would be absurd."""
    programs = _seeded_programs(cli_db, tmp_path)
    target = programs / "atomic-casualty.json"

    assert main(["sync", "--path", str(target)]) == 0
    out = capsys.readouterr().out
    assert "atomic-casualty.json" in out
    assert "✓" in out


def test_sync_one_path_reports_an_unlinked_file_without_crashing(
    cli_db: Path, tmp_path: Path, capsys
) -> None:
    import shutil

    programs = _seeded_programs(cli_db, tmp_path)
    orphan = tmp_path / "orphan.json"
    shutil.copy(programs / "atomic-casualty.json", orphan)

    assert main(["sync", "--path", str(orphan)]) == 1
    assert "link" in capsys.readouterr().out.lower()


def test_backup(cli_db: Path, tmp_path: Path, capsys) -> None:
    main(["init"])
    dest = tmp_path / "out" / "backup.db"
    assert main(["backup", "--dest", str(dest)]) == 0
    assert dest.exists()
    check = db.connect(dest, migrate=False)
    assert db.integrity_check(check)
    check.close()


def test_export_open_items_writes_workbook(tmp_path: Path, capsys, monkeypatch) -> None:
    from bookkit.repo import orgs
    from bookkit.repo import tasks as tasks_repo
    db_path = tmp_path / "b.db"
    monkeypatch.setenv("BOOKKIT_DB", str(db_path))
    conn = db.connect(db_path)
    org = orgs.create(conn, name="Acme", kind="client")
    tasks_repo.create(conn, "chase quote", org_id=org.id)
    conn.close()
    out = tmp_path / "acme.xlsx"
    rc = main(["export", "open-items", "Acme", "--out", str(out)])
    assert rc == 0 and out.exists()


def test_export_unknown_org_suggests(tmp_path: Path, capsys, monkeypatch) -> None:
    from bookkit.repo import orgs
    db_path = tmp_path / "b.db"
    monkeypatch.setenv("BOOKKIT_DB", str(db_path))
    conn = db.connect(db_path)
    orgs.create(conn, name="Acme Corp", kind="client")
    conn.close()
    rc = main(["export", "open-items", "Acme Copr"])
    assert rc == 2
    assert "Acme Corp" in capsys.readouterr().out


def test_today_lists_requests_to_chase(tmp_path, capsys) -> None:
    from datetime import date

    from bookkit import db
    from bookkit.cli import main
    from bookkit.repo import orgs, rfi

    path = tmp_path / "t.db"
    conn = db.connect(path)
    org = orgs.create(conn, name="Endeavour Energy", kind="client")
    req = rfi.create_request(
        conn, org.id, "Sompo — property questions", "2026-08-05",
        due_on=date.today().isoformat(),
    )
    rfi.add_item(conn, req.id, "how many vehicles?")
    rfi.add_item(conn, req.id, "loss runs 2021-2025", kind="document")
    conn.close()

    assert main(["--db", str(path), "today"]) == 0
    out = capsys.readouterr().out
    assert "REQUESTS TO CHASE (1)" in out
    assert "Sompo — property questions" in out
    assert "2 of 2 open" in out


def test_connector_info_prints_pasteable_panel_fields(cli_db: Path, capsys) -> None:
    """`bookctl mcp --connector-info` must print, not serve — the Cowork panel
    is hand-entry, so the command's whole job is producing the five values.
    """
    assert main(["mcp", "--connector-info"]) == 0

    out = capsys.readouterr().out
    assert "bookkit" in out
    assert str(Path(sys.executable).parent / "bookctl") in out
    # cli_db points BOOKKIT_DB off-default, so the path must be pinned
    assert f"--db, {cli_db}, mcp" in out
    assert "both" in out


def test_check_exit_code_reflects_the_verdict(cli_db: Path, capsys) -> None:
    assert main(["mcp", "--check"]) == 1
    assert "no such file" in capsys.readouterr().out

    main(["init"])
    capsys.readouterr()
    assert main(["mcp", "--check"]) == 0


def test_connector_info_honours_an_explicit_db_override(tmp_path, capsys) -> None:
    """`bookctl --db X mcp --connector-info` has to describe the connector for
    X, not for wherever this shell happens to point.
    """
    path = tmp_path / "explicit.db"

    assert main(["--db", str(path), "mcp", "--connector-info"]) == 0

    assert f"--db, {path}, mcp" in capsys.readouterr().out
