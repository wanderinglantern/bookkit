"""The client-facing export: a three-tab workbook — Open Items · Projects ·
Schedule of Insurance — composed PURELY, with rendering left to towerkit
(write() in this module glues to towerkit.render.table_xlsx /
render.soi_xlsx; bookkit has no xlsx dependency). Sheet 1 (Open Items):
org-level tasks split by category (SOV-style, alphabetical) plus a
trailing General for uncategorized tasks and loose submissions, one
section per placement (its tasks + outstanding submissions), one per
project (unmet needs) — always present, even when empty. Sheet 2
(Projects): every need on every live project, omitted (not blank) when
the org has none. Sheet 3 (Schedule of Insurance): towerkit's SOI
machinery per linked placement with a book-data fallback, present
whenever any placement exists. Determinism: `today` is a parameter, never
the wall clock."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from towerkit.model import load_program
from towerkit.soi import SoiRow, SoiSection, build_soi

from ..models import Placement, Project, Task
from ..money import MoneyParseError, cents_to_dollars, format_cents
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


# --- sheet 3: Schedule of Insurance — towerkit's SOI machinery, per client ------

_UNLINKED_CARRIER = "See policy documents"


def _premium_dollars(cents: int | None) -> int | None:
    """Placement premium cents → the SOI's whole-dollar premium column.
    Delegates to the guarded money boundary first; on its sub-dollar refusal
    floors to dollars — display only, the same deliberate floor
    format_cents_compact documents. Nothing is written back anywhere."""
    if cents is None:
        return None
    try:
        return cents_to_dollars(cents)
    except MoneyParseError:
        return cents // 100


def _book_data_section(org_name: str, placement: Placement) -> SoiSection:
    """Minimal SOI section for a placement with no (readable) towerkit file —
    program name, period, status, premium from book data, so the policy list
    is complete, never silently partial."""
    row = SoiRow(
        insured=org_name,
        coverage=placement.program_name,
        carrier=_UNLINKED_CARRIER,
        policy_number="",
        effective=date.fromisoformat(placement.period_from),
        expiration=date.fromisoformat(placement.period_to),
        limits="",
        retention="",
        premium=_premium_dollars(placement.total_premium),
    )
    return SoiSection(
        label=f"{placement.program_name} ({_status_label(str(placement.status))})",
        rows=(row,),
    )


def compose_soi(conn: sqlite3.Connection, org_id: str) -> list[SoiSection]:
    """build_soi sections for every LINKED placement, each under a
    program-name label (prefixing flattens the per-program nesting); minimal
    book-data sections for UNLINKED, unreadable, or layerless ones. Non-empty
    exactly when the org has any placement — the sheet-inclusion rule."""
    org = orgs.get(conn, org_id)
    out: list[SoiSection] = []
    for placement in placements.for_org(conn, org_id):
        sections: list[SoiSection] = []
        if placement.program_path:
            try:
                program = load_program(Path(placement.program_path))
            except Exception:  # moved/unreadable file — fall back to book data
                program = None
            if program is not None:
                sections = [
                    SoiSection(
                        label=placement.program_name
                        if section.label is None
                        else f"{placement.program_name} — {section.label}",
                        rows=section.rows,
                    )
                    for section in build_soi(program)
                ]
        if not sections:
            sections = [_book_data_section(org.name, placement)]
        out.extend(sections)
    return out


_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Item", 30.0), ("Description", 40.0), ("Detail", 44.0), ("Type", 12.0),
    ("Due / Needed by", 16.0), ("Status", 14.0),
)

_PROJECT_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Line", 28.0), ("Notes", 50.0), ("Needed by", 16.0),
    ("Status", 14.0), ("Limit", 16.0),
)


def write(conn: sqlite3.Connection, org_id: str, out_path: Path, today: date) -> Path:
    """The three-tab client deliverable — Open Items · Projects · Schedule of
    Insurance — rendered via towerkit so every sheet carries SOI formatting
    exactly (the money.parse_share pattern: formatting authority in one
    place). Projects appears only when live projects exist; the SOI sheet
    whenever any placement exists; finalize runs ONCE."""
    from towerkit.render.soi_xlsx import render_soi_sheet
    from towerkit.render.table_xlsx import (
        TableColumn,
        TableSection,
        finalize_workbook,
        new_workbook,
        render_table_sheet,
        sanitize_sheet_title,
    )
    from towerkit.theme import load_theme

    org = orgs.get(conn, org_id)
    theme = load_theme(None)
    wb = new_workbook()

    # Sheet 1 — Open Items: content identical to the single-sheet era.
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
    ws = wb.active
    assert ws is not None
    ws.title = sanitize_sheet_title(f"Open Items — {org.name}"[:31])
    render_table_sheet(
        ws, columns, sections, theme=theme,
        # Detail is the only multi-line column; two-line floor like the SOI
        row_height=lambda values: 18.0 * max(2, str(values[2]).count("\n") + 1),
    )

    # Sheet 2 — Projects: omitted (not blank) when no live projects.
    project_sections = compose_projects(conn, org_id)
    if project_sections:
        ws_projects = wb.create_sheet(sanitize_sheet_title("Projects"))
        project_columns = [TableColumn(h, w) for h, w in _PROJECT_COLUMNS[:-1]]
        project_columns.append(TableColumn("Limit", 16.0, align="right"))
        render_table_sheet(
            ws_projects, project_columns,
            [TableSection(s.label, s.rows) for s in project_sections],
            theme=theme,
            # Notes is the only multi-line column; same two-line floor
            row_height=lambda values: 18.0 * max(2, str(values[1]).count("\n") + 1),
        )

    # Sheet 3 — Schedule of Insurance: whenever any placement exists
    # (compose_soi is non-empty exactly then). The client's own program:
    # show_premiums=True.
    soi_sections = compose_soi(conn, org_id)
    if soi_sections:
        ws_soi = wb.create_sheet(sanitize_sheet_title("Schedule of Insurance"))
        render_soi_sheet(ws_soi, soi_sections, theme=theme, show_premiums=True)

    return finalize_workbook(wb, out_path)
