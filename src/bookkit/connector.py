"""What to type into the Cowork tool's Add MCP Connector panel, and whether
it will work.

The panel is hand-entry only — the vendor stores env values encrypted and
owns its own config.toml, so nothing here writes a file. These helpers
return data; cli.py prints it.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import humanize

from . import db


@dataclass(frozen=True)
class ConnectorFields:
    """One field per input in the Add MCP Connector dialog."""

    name: str
    command: str
    arguments: list[str]
    env: dict[str, str]
    mode: str


@dataclass(frozen=True)
class CheckResult:
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class CheckReport:
    checks: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _schema(path: Path) -> CheckResult:
    """Read-only on purpose: this is the connection the server's read tools
    use, so a permission problem surfaces here rather than mid-conversation."""
    conn = db.connect_readonly(path)
    try:
        version = db.schema_version(conn)
        pending = db.pending_migrations(conn)
    finally:
        conn.close()
    if pending:
        missing = ", ".join(str(v) for v, _ in pending)
        return CheckResult("schema", False, f"v{version}; run `bookctl migrate` for {missing}")
    return CheckResult("schema", True, f"v{version}, up to date")


def _describe(path: Path) -> str:
    """Size and age, so the real book is distinguishable from a stale copy."""
    stat = path.stat()
    size = humanize.naturalsize(stat.st_size)
    modified = humanize.naturaltime(datetime.now() - datetime.fromtimestamp(stat.st_mtime))
    return f"{path} ({size}, modified {modified})"


def _command() -> CheckResult:
    path = Path(fields().command)
    if path.is_file() and os.access(path, os.X_OK):
        return CheckResult("command", True, str(path))
    return CheckResult("command", False, f"not an executable file: {path}")


def _stdout(path: Path) -> CheckResult:
    """stdout is the MCP wire; anything printed at startup corrupts it."""
    from . import mcpserver

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        mcpserver.build_server(path)
    noise = buffer.getvalue()
    if noise:
        return CheckResult("stdout", False, f"startup printed {noise[:80]!r}")
    return CheckResult("stdout", True, "silent at startup")


def check(db_override: Path | str | None = None) -> CheckReport:
    path = Path(db_override) if db_override else db.default_db_path()
    checks = [_command()]
    if not path.exists():
        checks.append(CheckResult("database", False, f"no such file: {path}"))
        return CheckReport(checks)
    checks.append(CheckResult("database", True, _describe(path)))
    schema = _schema(path)
    checks.append(schema)
    if schema.ok:
        checks.append(_stdout(path))
    else:
        # build_server migrates the database it is handed, so probing stdout
        # here would silently perform the upgrade this report just flagged.
        checks.append(CheckResult("stdout", False, "not probed — migrate first"))
    return CheckReport(checks)


def fields(db_override: Path | str | None = None) -> ConnectorFields:
    # .resolve() for the same reason `command` is absolute: the connector is
    # launched by a GUI app that inherits no shell environment and no useful
    # cwd. A relative --db here fails SILENTLY, because build_server creates
    # the file on connect — the connector starts clean and reports an empty
    # book.
    resolved = (
        Path(db_override).expanduser().resolve()
        if db_override
        else db.default_db_path()
    )
    inherited = db.default_db_path(env={})
    arguments = ["mcp"] if resolved == inherited else ["--db", str(resolved), "mcp"]
    return ConnectorFields(
        name="bookkit",
        command=str(Path(sys.executable).parent / "bookctl"),
        arguments=arguments,
        env={},
        mode="both",
    )
