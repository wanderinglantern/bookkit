"""Client-facing open-items list, composed PURELY — rendering is towerkit's
job (write() in this module glues to towerkit.render.table_xlsx; bookkit
has no xlsx dependency). Sections: org-level tasks split by category
(SOV-style, alphabetical) plus a trailing General for uncategorized tasks
and loose submissions, one per placement (its tasks + outstanding
submissions), one per project (unmet needs). Determinism: `today` is a
parameter, never the wall clock."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..models import Project, Task
from ..money import format_cents
from ..repo import orgs, placements, submissions
from ..repo import projects as projects_repo
from ..repo import tasks as tasks_repo


@dataclass(frozen=True)
class ExportRow:
    item: str
    description: str
    detail: str
    kind: str      # "Task" | "Need" | "Submission"
    due: str       # ISO or ""
    status: str


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


def _status_label(status: str) -> str:
    """Project-need statuses are raw vocab ("not_needed") — client-facing
    cells get prose: underscores to spaces, first letter capitalized only."""
    text = status.replace("_", " ")
    return text[:1].upper() + text[1:] if text else text


def _task_row(task: Task, today: date) -> ExportRow:
    overdue = task.due_on is not None and task.due_on < today.isoformat()
    return ExportRow(
        item=task.title,
        description=task.description or "",
        detail=flatten_markdown(task.detail or ""),
        kind="Task", due=task.due_on or "",
        status="Overdue" if overdue else "Open",
    )


def compose(conn: sqlite3.Connection, org_id: str, today: date) -> list[ExportSection]:
    org = orgs.get(conn, org_id)
    sections: list[ExportSection] = []

    org_tasks = tasks_repo.open_tasks_for_client(conn, org.id)
    by_category: dict[str, list[Task]] = {}
    # case-insensitive bucketing, first-seen spelling wins (repo/vocab.py's
    # _dedupe rule) — "renewal" and "Renewal" land in one section
    category_labels: dict[str, str] = {}
    uncategorized: list[Task] = []
    for t in org_tasks:
        if t.placement_id:
            continue
        if t.category:
            label = category_labels.setdefault(t.category.lower(), t.category)
            by_category.setdefault(label, []).append(t)
        else:
            uncategorized.append(t)
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

    # SOV-style: one section per category (alphabetical, case-insensitive);
    # uncategorized org-level tasks + loose submissions land in General last.
    for category in sorted(by_category, key=str.lower):
        sections.append(ExportSection(
            f"{category} — {org.name}",
            tuple(_task_row(t, today) for t in by_category[category]),
        ))

    general_rows = [_task_row(t, today) for t in uncategorized] + [
        ExportRow(
            item=f"Submission to {row['market_name']}",
            description=row["about"] or "", detail="", kind="Submission", due="",
            status="Out at market",
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
                description=row["about"] or "", detail="", kind="Submission", due="",
                status="Out at market",
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
                        description="\n".join(part for part in (
                            n.notes or "",
                            f"Limit {format_cents(n.limit_cents)}" if n.limit_cents else "",
                        ) if part),
                        detail="",
                        kind="Need", due=n.needed_by, status=_status_label(n.status),
                    )
                    for n in needs
                ),
            ))
    return sections


# --- sheet 2: Projects — the full projects report, not the unmet slice ---------

_LIVE_EXCLUDED = ("completed", "cancelled")  # spec's "non-completed" = live only


@dataclass(frozen=True)
class SheetSection:
    """A styled-table section as plain data — label plus ready-to-render
    string rows. Pure counterpart of towerkit's TableSection (which lives in
    render/ and must not be imported at module level)."""

    label: str | None
    rows: tuple[tuple[str, ...], ...]


def _project_label(project: Project) -> str:
    label = f"{project.name} — {_status_label(project.status)}"
    if project.start_on and project.end_on:
        label += f" ({project.start_on} → {project.end_on})"
    elif project.start_on:
        label += f" (starts {project.start_on})"
    elif project.end_on:
        label += f" (ends {project.end_on})"
    return label


def compose_projects(conn: sqlite3.Connection, org_id: str) -> list[SheetSection]:
    """One section per live project, EVERY need regardless of status — the
    client's projects data in full (sheet 1 keeps only the unmet slice).
    Empty list ⇒ the Projects sheet is omitted, not rendered blank."""
    sections: list[SheetSection] = []
    for project in projects_repo.projects_for_org(conn, org_id):
        if project.status in _LIVE_EXCLUDED:
            continue
        rows = tuple(
            (
                n.line,
                n.notes or "",
                n.needed_by,
                _status_label(n.status),
                format_cents(n.limit_cents) if n.limit_cents else "",
            )
            for n in projects_repo.needs_for_project(conn, project.id)
        )
        sections.append(SheetSection(_project_label(project), rows))
    return sections


_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Item", 30.0), ("Description", 40.0), ("Detail", 44.0), ("Type", 12.0),
    ("Due / Needed by", 16.0), ("Status", 14.0),
)


def write(conn: sqlite3.Connection, org_id: str, out_path: Path, today: date) -> Path:
    """Render via towerkit so the workbook carries SOI formatting exactly —
    formatting authority stays in one place (the money.parse_share pattern)."""
    from towerkit.render.table_xlsx import TableColumn, TableSection, write_table
    from towerkit.theme import load_theme

    org = orgs.get(conn, org_id)
    columns = [TableColumn(h, w) for h, w in _COLUMNS]

    sections = [
        TableSection(
            s.label,
            tuple((r.item, r.description, r.detail, r.kind, r.due, r.status)
                  for r in s.rows),
        )
        for s in compose(conn, org_id, today)
    ] or [TableSection(None, ((f"No open items as of {today.isoformat()}",
                               "", "", "", "", ""),))]

    return write_table(
        columns, sections,
        title=f"Open Items — {org.name}"[:31],  # Excel sheet-title cap
        theme=load_theme(None), out_path=out_path,
        # Detail is the only multi-line column; two-line floor like the SOI
        row_height=lambda values: 18.0 * max(2, str(values[2]).count("\n") + 1),
    )
