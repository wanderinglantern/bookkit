"""The CLI findings that can damage real data or silently misconfigure things.

`seed --demo` is the sharpest: `$BOOKKIT_DB` defaults to the real book, the
command is in the README quick start, and running it twice against one
database took it from 35 orgs to 70 — with no provenance on the seeded rows,
so they cannot be cleanly removed afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookkit import connector, db
from bookkit.cli import main
from bookkit.repo import orgs


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "cli.db"
    monkeypatch.setenv("BOOKKIT_DB", str(path))
    return path


def _org_count(path: Path) -> int:
    conn = db.connect(path)
    try:
        return len(orgs.list_orgs(conn))
    finally:
        conn.close()


# --- seed --demo does not silently double a real book ------------------------


def test_seed_refuses_a_book_that_already_has_accounts(cli_db: Path, capsys) -> None:
    main(["init"])
    assert main(["seed", "--demo"]) == 0
    before = _org_count(cli_db)
    assert before > 0
    capsys.readouterr()

    assert main(["seed", "--demo"]) != 0
    out = capsys.readouterr()
    assert _org_count(cli_db) == before, "the second seed wrote anyway"
    assert "--force" in (out.out + out.err)


def test_seed_force_backs_up_before_it_writes(cli_db: Path, capsys) -> None:
    """--force is for seeding a book that already holds YOUR data."""
    main(["init"])
    conn = db.connect(cli_db)
    orgs.create(conn, kind="client", name="A Real Client", status="active")
    conn.close()
    before = _org_count(cli_db)
    capsys.readouterr()

    assert main(["seed", "--demo", "--force"]) == 0
    assert _org_count(cli_db) > before
    backups = list((cli_db.parent / "backups").glob("*.bak"))
    assert backups, "forced seed wrote without taking a backup first"
    # the backup must hold the PRE-seed book, or it is not a rollback
    assert _org_count(backups[0]) == before


def test_forcing_a_second_demo_seed_fails_without_half_seeding(
    cli_db: Path, capsys
) -> None:
    """Seeding the demo twice collides on the fixed team names, and the guard
    refuses. Seed is one transaction, so the book is left exactly as it was
    rather than half-populated."""
    main(["init"])
    main(["seed", "--demo"])
    before = _org_count(cli_db)
    capsys.readouterr()

    assert main(["seed", "--demo", "--force"]) != 0
    assert _org_count(cli_db) == before, "a refused seed left rows behind"
    assert "already holds that name" in capsys.readouterr().err


def test_seed_names_the_database_it_is_about_to_fill(cli_db: Path, capsys) -> None:
    main(["init"])
    assert main(["seed", "--demo"]) == 0
    out = capsys.readouterr().out
    assert str(cli_db) in out, "the user is never told which book was seeded"


def test_seed_still_works_on_an_empty_book(cli_db: Path) -> None:
    main(["init"])
    assert main(["seed", "--demo"]) == 0
    assert _org_count(cli_db) > 0


# --- paths that mean the same thing from any directory ----------------------


def test_roots_are_stored_absolute(cli_db: Path, tmp_path, monkeypatch, capsys) -> None:
    """towerctl parses `roots --json` to build its own config, so a relative
    path there means nothing in towerctl's process."""
    main(["init"])
    programs = tmp_path / "programs"
    programs.mkdir()
    monkeypatch.chdir(tmp_path)

    assert main(["roots", "./programs"]) == 0
    capsys.readouterr()

    monkeypatch.chdir(tmp_path.parent)      # any other cwd
    assert main(["roots", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["roots"] == [str(programs)]


def test_connector_arguments_carry_an_absolute_db(tmp_path, monkeypatch) -> None:
    """Command is absolute because a GUI app inherits no shell environment.
    The --db inside Arguments has to be, for exactly the same reason."""
    monkeypatch.chdir(tmp_path)
    got = connector.fields("./book.db")
    assert "--db" in got.arguments
    given = got.arguments[got.arguments.index("--db") + 1]
    assert Path(given).is_absolute(), f"relative --db in connector args: {given}"


def test_roots_json_keeps_stdout_parseable_on_the_error_path(
    cli_db: Path, capsys
) -> None:
    main(["init"])
    capsys.readouterr()
    assert main(["roots", "/no/such/dir", "--json"]) != 0
    out = capsys.readouterr()
    assert out.out.strip() == "" or json.loads(out.out)
    assert "not a directory" in out.err


# --- a wrong --db is an error, not a traceback ------------------------------


def test_pointing_db_at_a_non_database_says_so(tmp_path, monkeypatch, capsys) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("this is not a database")
    monkeypatch.setenv("BOOKKIT_DB", str(notes))

    rc = main(["today"])          # used to raise sqlite3.DatabaseError

    assert rc != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(notes) in err


def test_a_read_command_does_not_create_a_book_at_a_typo(
    tmp_path, monkeypatch, capsys
) -> None:
    """`bookctl --db /typo/path today` used to create the file and print a
    cheerful all-zeros brief, so a wrong path was indistinguishable from a
    quiet book."""
    missing = tmp_path / "typo" / "book.db"
    monkeypatch.setenv("BOOKKIT_DB", str(missing))

    rc = main(["today"])

    assert rc != 0
    assert not missing.exists(), "a read command created the database"
    assert "init" in capsys.readouterr().err


def test_the_error_names_the_db_you_actually_passed(
    tmp_path, monkeypatch, capsys
) -> None:
    """`main()` is called with argv=None from the console entry point, so a
    hint derived from `argv` silently described the DEFAULT database instead
    of the --db that failed."""
    monkeypatch.setenv("BOOKKIT_DB", str(tmp_path / "default.db"))
    notes = tmp_path / "notes.txt"
    notes.write_text("not a database")
    monkeypatch.setattr("sys.argv", ["bookctl", "--db", str(notes), "today"])

    assert main() != 0

    err = capsys.readouterr().err
    assert str(notes) in err
    assert "default.db" not in err
