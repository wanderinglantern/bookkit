"""Client-facing open-items list, composed PURELY — rendering is towerkit's
job (write() in this module glues to towerkit.render.table_xlsx; bookkit
has no xlsx dependency). Sections: General (org-level tasks), one per
placement (its tasks + outstanding submissions), one per project (unmet
needs). Determinism: `today` is a parameter, never the wall clock."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..models import Task
from ..money import format_cents
from ..repo import orgs, placements, submissions
from ..repo import projects as projects_repo
from ..repo import tasks as tasks_repo


@dataclass(frozen=True)
class ExportRow:
    item: str
    details: str
    kind: str      # "Task" | "Need" | "Submission"
    due: str       # ISO or ""
    status: str
    days_open: int


@dataclass(frozen=True)
class ExportSection:
    label: str | None
    rows: tuple[ExportRow, ...]


_MD_STRIP = (
    (re.compile(r"```.*?```", re.S), ""),          # fenced code blocks
    (re.compile(r"^#{1,6}\s*", re.M), ""),          # headings
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),  # links → text
    (re.compile(r"[*_]{1,3}(\S(?:.*?\S)?)[*_]{1,3}"), r"\1"),  # emphasis
    (re.compile(r"`([^`]*)`"), r"\1"),              # inline code
    (re.compile(r"^\s*[*+]\s+", re.M), "- "),       # bullets normalize to "- "
)


def flatten_markdown(text: str) -> str:
    """Markdown notes → clean plain text for a spreadsheet cell. Bullets
    survive as '- ' lines; everything decorative is stripped."""
    out = text
    for pattern, repl in _MD_STRIP:
        out = pattern.sub(repl, out)
    return "\n".join(line.rstrip() for line in out.splitlines() if line.strip())


def _days_since(created_at: str, today: date) -> int:
    return (today - date.fromisoformat(created_at[:10])).days


def _task_row(task: Task, today: date) -> ExportRow:
    details = "\n".join(
        part for part in (task.description or "", flatten_markdown(task.detail or ""))
        if part
    )
    overdue = task.due_on is not None and task.due_on < today.isoformat()
    return ExportRow(
        item=task.title, details=details, kind="Task", due=task.due_on or "",
        status="Overdue" if overdue else "Open",
        days_open=_days_since(task.created_at, today),
    )


def compose(conn: sqlite3.Connection, org_id: str, today: date) -> list[ExportSection]:
    org = orgs.get(conn, org_id)
    sections: list[ExportSection] = []

    org_tasks = tasks_repo.open_tasks(conn, org_id=org.id)
    general = tuple(_task_row(t, today) for t in org_tasks if not t.placement_id)
    by_placement: dict[str, list[Task]] = {}
    for t in org_tasks:
        if t.placement_id:
            by_placement.setdefault(t.placement_id, []).append(t)

    subs = submissions.outstanding_for_org(conn, org.id)
    subs_by_placement: dict[str, list[sqlite3.Row]] = {}
    loose_subs: list[sqlite3.Row] = []
    for row in subs:
        if row["about_placement_id"]:
            subs_by_placement.setdefault(row["about_placement_id"], []).append(row)
        else:
            loose_subs.append(row)

    general_rows = list(general) + [
        ExportRow(
            item=f"Submission to {row['market_name']}",
            details=row["about"] or "", kind="Submission", due="",
            status="Out at market", days_open=_days_since(row["sent_on"], today),
        )
        for row in loose_subs
    ]
    if general_rows:
        sections.append(ExportSection(f"General — {org.name}", tuple(general_rows)))

    from .. import sync  # line labels for section headers, matching attention tables

    for placement in placements.for_org(conn, org.id):
        rows = [_task_row(t, today) for t in by_placement.get(placement.id, [])]
        rows += [
            ExportRow(
                item=f"Submission to {row['market_name']}",
                details=row["about"] or "", kind="Submission", due="",
                status="Out at market", days_open=_days_since(row["sent_on"], today),
            )
            for row in subs_by_placement.get(placement.id, [])
        ]
        if rows:
            lines = sync.line_labels(placement.program_path)
            label = placement.program_name + (f" ({lines})" if lines else "")
            sections.append(ExportSection(label, tuple(rows)))

    for project in projects_repo.projects_for_org(conn, org.id):
        needs = [
            n for n in projects_repo.needs_for_project(conn, project.id)
            if n.status in projects_repo.ATTENTION_STATUSES
        ]
        if needs:
            sections.append(ExportSection(
                f"Project — {project.name}",
                tuple(
                    ExportRow(
                        item=f"{n.line} cover",
                        details="\n".join(part for part in (
                            n.notes or "",
                            f"Limit {format_cents(n.limit_cents)}" if n.limit_cents else "",
                        ) if part),
                        kind="Need", due=n.needed_by, status=n.status,
                        days_open=_days_since(n.created_at, today),
                    )
                    for n in needs
                ),
            ))
    return sections


_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Item", 30.0), ("Details", 58.0), ("Type", 12.0),
    ("Due / Needed by", 16.0), ("Status", 14.0), ("Days open", 10.0),
)


def write(conn: sqlite3.Connection, org_id: str, out_path: Path, today: date) -> Path:
    """Render via towerkit so the workbook carries SOI formatting exactly —
    formatting authority stays in one place (the money.parse_share pattern)."""
    from towerkit.render.table_xlsx import TableColumn, TableSection, write_table
    from towerkit.theme import load_theme

    org = orgs.get(conn, org_id)
    columns = [TableColumn(h, w) for h, w in _COLUMNS[:-1]]
    columns.append(TableColumn("Days open", 10.0, align="right"))

    sections = [
        TableSection(
            s.label,
            tuple((r.item, r.details, r.kind, r.due, r.status, r.days_open)
                  for r in s.rows),
        )
        for s in compose(conn, org_id, today)
    ] or [TableSection(None, ((f"No open items as of {today.isoformat()}",
                               "", "", "", "", ""),))]

    return write_table(
        columns, sections,
        title=f"Open Items — {org.name}"[:31],  # Excel sheet-title cap
        theme=load_theme(None), out_path=out_path,
        # Details is the only multi-line column; two-line floor like the SOI
        row_height=lambda values: 18.0 * max(2, str(values[1]).count("\n") + 1),
    )
