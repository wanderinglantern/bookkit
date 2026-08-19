"""The client-facing export: a four-tab workbook — Open Items · Information
Requests · Projects · Schedule of Insurance — composed PURELY, with
rendering left to towerkit (write() in this module glues to
towerkit.render.table_xlsx / render.soi_xlsx; bookkit has no xlsx
dependency). Sheet 1 (Open Items): org-level tasks split by category
(SOV-style — except the Internal category, which is withheld from the
client entirely: see compose's include_internal) plus a trailing General
for uncategorized tasks and loose submissions, one section per placement
(its tasks + outstanding submissions), one per project (unmet needs) —
always present, even when empty. OVERDUE LEADS, at both levels: sections
carrying a past-due item sort first, most overdue first, and within every
section past-due rows lead. Everything with nothing overdue keeps the
composition order (categories alphabetical, then General, then placements,
then projects) — the sort is stable, so that ordering is the tiebreak, not
a second rule. Sheet 2 (Information Requests):
what the client still owes us, composed by services/export_rfi.py —
omitted (not blank) when nothing is outstanding. Sheet 3 (Projects): every
need on every live project, omitted (not blank) when the org has none.
Sheet 4 (Schedule of Insurance): towerkit's SOI machinery per linked
placement with a book-data fallback, covering only placements whose cover
is still in force on the export date — omitted (not blank) when none is.
Sheet 1 opens on SCOPE_NOTE, the standing statement of what the report
covers and what it withholds — invariant text, identical on every export.
Determinism: `today` is a parameter, never the wall clock."""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from towerkit.model import load_program
from towerkit.soi import SoiRow, SoiSection, SoiStatus, build_soi

from ..models import (
    INTERNAL_CATEGORY,
    AssigneeKind,
    Placement,
    PlacementStatus,
    Project,
    Task,
    is_internal_category,
)
from ..money import MoneyParseError, cents_to_dollars, format_cents
from ..repo import contacts, orgs, placements, submissions
from ..repo import projects as projects_repo
from ..repo import tasks as tasks_repo

CLIENT_OWNER = "You"
OUR_OWNER = "Us"
"""C4, Grant 2026-08-18 — the two words in the client's Owner column.

The CFO asked for one column saying which open items are theirs and which
are ours, and rated it above every formatting fix on the list, because
"Return signed TRIA form" and "Chase Zurich on the loss run" render
identically today. The AE then reframed it: the internal fact worth holding
is a NAMED assignee, and You / Us is its projection onto the client's copy.
So this is the projection, and nothing stores it."""


@dataclass(frozen=True)
class ExportRow:
    item: str
    description: str
    detail: str
    kind: str      # "Task" | "Need" | "Submission"
    due: str       # ISO or ""
    status: str
    ref: str = ""  # task/need/submission id — MCP's per-client open_items
    # needs this to satisfy task_complete's "exact ref you read" contract;
    # the xlsx writer's explicit column tuple in write() does NOT include
    # it, so refs never reach the client-facing workbook (see write()).
    internal: bool = False  # same mechanism as `ref`: carried for callers that
    # ask for the internal rows (MCP), absent from write()'s column tuple, so
    # the flag itself can never print. ExportRow has no `category` field, so
    # without this an Internal task shown to an MCP caller under a PLACEMENT
    # section — which is labelled by program name — would carry no sign of it.
    owner: str = OUR_OWNER  # C4 — "You" or "Us", and DERIVED, never stored.
    # Defaulted to ours: every row shape that cannot carry an assignee at all
    # (a submission out at market, an unmet project need) is ours by nature,
    # and so is a task nobody has claimed. The wrong-direction failure here is
    # asymmetric — see owner_of.


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


def owner_of(conn: sqlite3.Connection, task: Task, org_id: str) -> str:
    """WHICH SIDE OF THE TABLE this task sits on, derived from the assignee's
    KIND and identity — never from their name.

    A contact at the account being exported is the client's own person, so
    the row is theirs. Everyone else is ours: our team by definition, an
    underwriter or wholesaler because chasing them is our job and not the
    client's, and a freeform third party because a name we could not resolve
    is a name we cannot make a claim about.

    NO NAME IS COMPARED ANYWHERE IN HERE. Matching "Sam" against "Sam Garcia"
    would flip a column a client reads, silently, in the one direction that
    cannot be recovered — the failure models.is_internal_category spends
    fourteen lines refusing on the neighbouring column. The comparison is
    between two org ids.

    Unassigned is `Us`: unassigned work is ours until someone says otherwise
    (Grant, 2026-08-18). So is an assignment whose contact row has since been
    removed — `contacts.get` raises and the answer falls to the safe side
    rather than to a stale claim."""
    if task.assignee_kind is not AssigneeKind.CONTACT or not task.assignee_id:
        return OUR_OWNER
    try:
        contact = contacts.get(conn, task.assignee_id)
    except KeyError:
        return OUR_OWNER
    return CLIENT_OWNER if contact.org_id == org_id else OUR_OWNER


def _task_row(conn: sqlite3.Connection, task: Task, today: date, org_id: str) -> ExportRow:
    overdue = task.due_on is not None and task.due_on < today.isoformat()
    return ExportRow(
        item=task.title,
        description=task.description or "",
        detail=flatten_markdown(task.detail or ""),
        kind="Task", due=task.due_on or "",
        status="Overdue" if overdue else "Open",
        ref=task.id,
        internal=is_internal_category(task.category),
        owner=owner_of(conn, task, org_id),
    )


def _overdue_on(row: ExportRow, today: date) -> str | None:
    """The due date of a row that is PAST due, else None. Read off `due`, not
    off `status`: only task rows carry the "Overdue" status word, while a
    project need past its needed-by date is every bit as late and reaches
    sheet 1 through a different branch."""
    return row.due if row.due and row.due < today.isoformat() else None


def _overdue_first(
    sections: list[ExportSection], today: date
) -> list[ExportSection]:
    """Grant's C7 answer — overdue leads — expressed as a REORDERING, never a
    regrouping. Alphabetical is the one ordering that serves nobody: the
    reviewer's only 12-days-overdue item sat below a certificate not due for
    three days.

    Overdue rows are NOT lifted into a leading section of their own. Sheet 1
    carries two kinds of section — by category and by placement — and a task
    that moved into an "Overdue" section would either lose the only context
    saying which program it belongs to, or appear twice. Sections sort by
    their most-overdue member instead, so nothing crosses a section boundary
    and duplication is impossible by construction; within a section, past-due
    rows lead in the same order, because a section that opens on a row due
    next month while holding one two weeks late has the same defect at
    smaller scale.

    Both sort keys put non-overdue after overdue with an EQUAL tail, so
    Python's stable sort leaves composition order intact underneath."""
    ranked = [
        ExportSection(
            section.label,
            tuple(sorted(
                section.rows,
                key=lambda r: ((0, due) if (due := _overdue_on(r, today)) else (1, "")),
            )),
        )
        for section in sections
    ]
    return sorted(
        ranked,
        key=lambda s: (
            (0, overdue[0]) if (overdue := sorted(
                d for r in s.rows if (d := _overdue_on(r, today))
            )) else (1, "")
        ),
    )


def compose(
    conn: sqlite3.Connection, org_id: str, today: date, *, include_internal: bool = False
) -> list[ExportSection]:
    """Sheet 1, as data, OVERDUE FIRST (see _overdue_first). Tasks filed
    under the Internal category are withheld
    unless `include_internal` — the default is the client-safe one, so a
    future caller composing something client-facing inherits it without
    knowing this feature exists. A caller that wanted the internal rows and
    forgot to ask gets a visibly missing task; the other way round is a leak
    nobody sees. Exactly one caller asks: MCP's per-client open_items."""
    org = orgs.get(conn, org_id)
    sections: list[ExportSection] = []

    org_tasks = tasks_repo.open_tasks_for_client(conn, org.id)
    if not include_internal:
        # ON THE TASK LIST, before either split. Sheet 1 buckets these tasks
        # TWICE — by category, and by placement with category ignored — so a
        # filter inside the category branch would let an Internal task that
        # carries a placement_id ship to the client anyway.
        org_tasks = [t for t in org_tasks if not is_internal_category(t.category)]
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
            tuple(_task_row(conn, t, today, org.id) for t in by_category[category]),
        ))

    general_rows = [_task_row(conn, t, today, org.id) for t in uncategorized] + [
        ExportRow(
            item=f"Submission to {row['market_name']}",
            description=row["about"] or "", detail="", kind="Submission", due="",
            status="Out at market", ref=row["id"],
        )
        for row in loose_subs
    ]
    if general_rows:
        sections.append(ExportSection(f"General — {org.name}", tuple(general_rows)))

    from .. import sync  # line labels for section headers, matching attention tables

    for placement in placements.for_org(conn, org.id):
        rows = [_task_row(conn, t, today, org.id) for t in by_placement.get(placement.id, [])]
        rows += [
            ExportRow(
                item=f"Submission to {row['market_name']}",
                description=row["about"] or "", detail="", kind="Submission", due="",
                status="Out at market", ref=row["id"],
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
                        ref=n.id,
                    )
                    for n in needs
                ),
            ))
    return _overdue_first(sections, today)


def withheld_internal(conn: sqlite3.Connection, org_id: str) -> list[Task]:
    """This client's open tasks filed under the Internal category — exactly
    what compose() withholds on the default path. Deliberately narrow: it is
    NOT "everything compose() leaves out" (a task pointing at a soft-deleted
    placement is dropped too, for unrelated reasons), because the number it
    backs is user-facing and must mean one thing."""
    return [
        t for t in tasks_repo.open_tasks_for_client(conn, org_id)
        if is_internal_category(t.category)
    ]


def near_miss_internal(conn: sqlite3.Connection, org_id: str) -> list[Task]:
    """This client's open tasks whose category LOOKS internal but is not:
    case-folded and trimmed it contains "internal" without equalling it —
    "Internal Review", "internal note", "Client internal audit".

    These were EXPORTED. The match rule is exact equality on purpose
    (models.is_internal_category says why), so this is the one shape the rule
    fails on, and the failure is otherwise invisible: nothing about the row or
    the file says a category that reads internal was not treated as one."""
    return [
        t for t in tasks_repo.open_tasks_for_client(conn, org_id)
        if t.category
        and INTERNAL_CATEGORY.lower() in t.category.strip().lower()
        and not is_internal_category(t.category)
    ]


def withheld_note(conn: sqlite3.Connection, org_id: str) -> str:
    """The suffix both export surfaces append to "wrote <path>" — what the
    client did NOT get, and what looked like it would be withheld and was not.

    An absence is not a signal to someone who has never seen the presence. The
    silence-means-it-worked version of this line taught nobody anything on
    their first internal task: "Internal Review" renders byte-identically to
    "Renewal" everywhere, so the near miss had no positive signal on any
    surface. Naming it here says the rule fired and says what the rule is,
    without weakening the rule (a prefix match would silently drop "Internal
    audit support", a real client-facing task). Empty only when there is
    genuinely nothing to say."""
    parts: list[str] = []
    held = len(withheld_internal(conn, org_id))
    if held:
        parts.append(f"{held} internal task{'' if held == 1 else 's'} withheld")
    missed = near_miss_internal(conn, org_id)
    if missed:
        labels = ", ".join(
            f'"{c}"' for c in _dedupe_categories(t.category or "" for t in missed)
        )
        parts.append(
            f"{len(missed)} task{'' if len(missed) == 1 else 's'} categorised {labels} "
            f"{'WAS' if len(missed) == 1 else 'WERE'} exported "
            f'(only the exact category "{INTERNAL_CATEGORY}" is withheld)'
        )
    return f" — {'; '.join(parts)}" if parts else ""


def _dedupe_categories(values: Iterable[str]) -> list[str]:
    """Distinct spellings, first one wins, alphabetical — the same rule
    repo/vocab._dedupe applies, so two tasks both filed "Internal Review" name
    the category once."""
    seen: dict[str, str] = {}
    for value in values:
        cleaned = value.strip()
        if cleaned:
            seen.setdefault(cleaned.lower(), cleaned)
    return sorted(seen.values(), key=str.lower)


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

# WHAT THE BOOK KNOWS, IN THE WORDS THE CLIENT READS. towerkit's SoiStatus is
# a display vocabulary of seven; bookkit's PlacementStatus reaches five of
# them. Two are not identity mappings and both are deliberate:
#
#   lapsed → Expired      "Lapsed" is our word for a dead policy year; the
#                         client's word for cover that has run out is expired.
#   prospective → To be placed
#                         NOT `Proposed`. `Proposed` means a designed program
#                         with carriers pencilled against it; a prospective
#                         placement has nobody on the risk at all, which is
#                         exactly what "to be placed" says.
#
# Only BOUND is cover in force — `SoiRow.is_bound` decides which subtotal a
# premium lands under, and every other value here lands it under `Unbound`.
_SOI_STATUS: dict[PlacementStatus, SoiStatus] = {
    PlacementStatus.PROSPECTIVE: SoiStatus.TO_BE_PLACED,
    PlacementStatus.SUBMITTED: SoiStatus.SUBMITTED,
    PlacementStatus.QUOTED: SoiStatus.QUOTED,
    PlacementStatus.BOUND: SoiStatus.BOUND,
    PlacementStatus.LAPSED: SoiStatus.EXPIRED,
}

# THE MAP MUST BE TOTAL, AND MUST SAY SO AT IMPORT. A missing key is not a
# blank cell on one row: an unstated status is not bound, so the premium of a
# placement whose status nobody mapped silently joins the `Unbound cover`
# subtotal on a client's schedule — a wrong number, printed confidently, with
# nothing on the sheet to hint at it. `_SOI_STATUS[...]` is therefore a plain
# subscript (KeyError, never `.get()` returning None), and this check turns a
# new PlacementStatus member into an import-time failure on every surface at
# once rather than a quiet miscount discovered by a client.
if set(_SOI_STATUS) != set(PlacementStatus):
    missing = sorted(set(PlacementStatus) - set(_SOI_STATUS))
    raise RuntimeError(
        f"_SOI_STATUS does not map every PlacementStatus: {missing}. "
        "Add the client-facing towerkit.soi.SoiStatus for it — an unmapped "
        "status would print blank and be counted as unbound cover."
    )


def _expired(placement: Placement, today: date) -> bool:
    """Is this policy year OVER as of `today`? Decided on the placement's own
    `period_to` — the very date the SOI prints in its Expiration column, so
    the sheet cannot contradict its own rule.

    NOT `RenewalItem.renewal_on`. CLAUDE.md's rule ("the renewal date is
    renewal_on, never period_to") governs a different question: renewal_on is
    the EARLIEST line end, because attention must open the moment the first
    layer runs out. Exclusion asks the opposite question — has ALL cover
    ended — and the earliest line end answers it wrong in the dangerous
    direction: a program whose IM layer lapsed in March while GL runs to
    October would vanish from the client's Schedule of Insurance with their
    General Liability policy still in force. A wrong INCLUSION here is loud
    (a dated row the reader can see is historic); a wrong exclusion is
    silent, and takes live cover off the schedule.

    Expiring TODAY is still in force: cover runs to the end of its last day,
    so the comparison is strictly `<`, never `<=`."""
    return placement.period_to < today.isoformat()


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
    is complete, never silently partial.

    THE STATUS IS A ROW FIELD, NOT A LABEL SUFFIX. This section used to say
    it as `Legacy Property (Bound)` in the heading while a LINKED placement's
    heading said nothing at all — so the more we knew about a programme, the
    less the client could tell whether it was real. Now both surfaces answer
    in the same Status column, and the answer is load-bearing rather than
    decorative: `SoiRow.is_bound` is what puts this premium under `Bound
    cover` instead of `Unbound cover`."""
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
        status=_SOI_STATUS[placement.status],
    )
    return SoiSection(label=placement.program_name, rows=(row,))


def _placement_status_applied(
    rows: tuple[SoiRow, ...], mapped: SoiStatus
) -> tuple[SoiRow, ...]:
    """The file's per-layer status, overridden only where the BOOK knows
    better.

    `build_soi` already stamps every row from the program file, and that is
    the FINER answer: it can say `To be placed` for the one layer nobody is on
    inside an otherwise bound programme, which the placement's single status
    can never express. Flattening it would print bound cover over an unplaced
    layer and sweep its premium into the `Bound cover` subtotal — the exact
    class of overstatement this column exists to remove.

    So a BOUND placement leaves the file alone. Anything else replaces every
    row, because those are the cases the file cannot see: a fully-populated
    design the broker has not yet bound reads as BOUND to towerkit, while the
    book knows it is only `Quoted`. Overriding always moves a row towards
    unbound, never towards bound, so the sheet can only ever understate cover.

    `SoiRow` is frozen — `dataclasses.replace`, not assignment."""
    if mapped == SoiStatus.BOUND:
        return rows
    return tuple(replace(row, status=mapped) for row in rows)


def compose_soi(conn: sqlite3.Connection, org_id: str, today: date) -> list[SoiSection]:
    """build_soi sections for every LINKED placement still in force, each
    under a program-name label (prefixing flattens the per-program nesting);
    minimal book-data sections for UNLINKED, unreadable, or layerless ones.

    EXPIRED POLICY YEARS ARE EXCLUDED (see `_expired`). A Schedule of
    Insurance is a statement of cover in force; a prior year rendered
    identically beside current cover reads as current cover, and the Status
    column does not save the reader from that — `Bound` is true in the past
    tense, and last year's programme was bound. Non-empty exactly when the org has a placement
    that has not expired — the sheet-inclusion rule. An account whose cover
    has ALL expired therefore gets no SOI sheet rather than a sheet with
    headers and no policies: the same omitted-not-blank rule sheets 2 and 3
    already follow, and the honest one, because there is no schedule to
    print. It is never an empty sheet — write()'s `if soi_sections` guard is
    what makes that true, and a test pins it."""
    org = orgs.get(conn, org_id)
    out: list[SoiSection] = []
    for placement in placements.for_org(conn, org_id):
        if _expired(placement, today):
            continue
        sections: list[SoiSection] = []
        if placement.program_path:
            try:
                program = load_program(Path(placement.program_path))
            except Exception:  # moved/unreadable file — fall back to book data
                program = None
            if program is not None:
                mapped = _SOI_STATUS[placement.status]
                sections = [
                    SoiSection(
                        label=placement.program_name
                        if section.label is None
                        else f"{placement.program_name} — {section.label}",
                        rows=_placement_status_applied(section.rows, mapped),
                    )
                    for section in build_soi(program)
                ]
        if not sections:
            sections = [_book_data_section(org.name, placement)]
        out.extend(sections)
    return out


_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Item", 30.0), ("Description", 50.0), ("Detail", 75.0), ("Type", 12.0),
    ("Due / Needed by", 16.0), ("Status", 14.0), ("Owner", 10.0),
)
# Owner is LAST and narrow on purpose: it is a two-letter answer, and putting
# it first would push the item it describes off the first screen of a sheet
# whose whole job is the list. There is no viewport to overflow here — a
# spreadsheet scrolls — so unlike the TUI panes nothing had to be dropped to
# make room for it.

_PROJECT_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Line", 28.0), ("Notes", 50.0), ("Needed by", 16.0),
    ("Status", 14.0), ("Limit", 16.0),
)

_RFI_COLUMNS: tuple[tuple[str, float], ...] = (
    ("Item", 34.0), ("Detail", 75.0), ("Type", 12.0), ("Needed by", 16.0),
)

_RFI_HEADER_LABEL = "Items we need from you"

SCOPE_NOTE = (
    "This report lists items owned by you or by us on your account. "
    "Internal administrative items are not included."
)
"""C8, Grant 2026-08-18 — the withholding rule, stated once, in the workbook.

PER-EXPORT INVARIANT TEXT. It carries nothing about this account: no count,
no names, no date, no branch on whether anything was actually withheld. A
sentence that changed shape when something was held back would BE the count
it replaces — the reviewer's objection to printing one was that it "converts
a non-event into a standing question on every export". Same words every time
means the omission is a known boundary rather than a discovery.

It is NOT `withheld_note()`. That line is ours: it names a NEAR MISS — a task
categorised e.g. "Internal Review" that WAS exported — to the operator on the
CLI and the TUI toast at the moment the file is written. It never enters the
workbook and the client never sees it.

It rides sheet 1 as a leading label-only section, the same mechanism
_RFI_HEADER_LABEL uses. Sheet 1 is the ONLY unconditionally present sheet, so
one line there is exactly once per export; repeating it on sheets 2-4 would
make it conditional and redundant, and none of those sheets carries tasks
anyway. It also survives C5 (towerkit's per-sheet header block) landing
later: this is sheet BODY, not chrome, so a header block rendered above it
pushes it down without duplicating it — a generic header block carries the
account, the report name and the date, which is precisely what this sentence
deliberately does not."""


def _wrapped_line_count(text: str, width: float) -> int:
    """How many wrapped lines `text` needs at `width` characters — an
    ESTIMATE, not true Excel autofit (the xlsx library behind this has no
    autofit API; Excel wraps by rendered glyph width, not character
    count). Each newline-separated
    segment wraps on its own, minimum one line, so embedded line breaks
    (flatten_markdown's bullets, multi-paragraph descriptions) count for
    real instead of being flattened into one long division."""
    if not text:
        return 1
    return sum(max(1, math.ceil(len(segment) / width)) for segment in text.split("\n"))


def _prose_row_height(*columns: tuple[str, float]) -> float:
    """18px-per-line row height from the widest wrap among the given
    (text, width) prose columns, floored at 2 lines. Mirrors towerkit's
    soi_xlsx._row_height precedent (same per-column division, same 2-line
    floor); extended here to respect embedded newlines, which the SOI's
    single-paragraph columns never needed."""
    lines = max((_wrapped_line_count(text, width) for text, width in columns), default=1)
    return 18.0 * max(lines, 2)


def write(conn: sqlite3.Connection, org_id: str, out_path: Path, today: date) -> Path:
    """The client deliverable — Open Items · Information Requests · Projects
    · Schedule of Insurance — rendered via towerkit so every sheet carries
    SOI formatting exactly (the money.parse_share pattern: formatting
    authority in one place). Sheet 1 opens on SCOPE_NOTE, the same words on
    every export. Information Requests appears only when the
    client has outstanding items; Projects only when live projects exist;
    the SOI sheet only when some placement is still in force; finalize runs
    ONCE."""
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

    from .export_rfi import compose_information_requests

    org = orgs.get(conn, org_id)
    theme = load_theme(None)
    wb = new_workbook()

    # Sheet 1 — Open Items, under the standing scope line (SCOPE_NOTE): the
    # rule is stated whether or not this account had anything withheld, and
    # ahead of the body, so it is read before the contents it describes.
    columns = [TableColumn(h, w) for h, w in _COLUMNS]
    body = [
        TableSection(
            s.label,
            tuple((r.item, r.description, r.detail, r.kind, r.due, r.status, r.owner)
                  for r in s.rows),
        )
        for s in compose(conn, org_id, today)
    ] or [TableSection(None, ((f"No open items as of {today.isoformat()}",
                               "", "", "", "", "", ""),))]
    sections = [TableSection(SCOPE_NOTE, ())] + body
    ws = wb.active
    assert ws is not None
    ws.title = sanitize_sheet_title(f"Open Items — {org.name}"[:31])
    render_table_sheet(
        ws, columns, sections, theme=theme,
        # Description and Detail both wrap; the taller of the two wins.
        row_height=lambda values: _prose_row_height(
            (str(values[1]), _COLUMNS[1][1]), (str(values[2]), _COLUMNS[2][1]),
        ),
    )

    # Sheet 2 — Information Requests: omitted (not blank) when the client
    # has nothing outstanding. The leading merged row is a label-only
    # section — the same mechanism every other section header uses, just
    # with no rows under it.
    rfi_sections = compose_information_requests(conn, org_id, today)
    if rfi_sections:
        ws_rfi = wb.create_sheet(sanitize_sheet_title("Information Requests"))
        rfi_columns = [TableColumn(h, w) for h, w in _RFI_COLUMNS]
        render_table_sheet(
            ws_rfi, rfi_columns,
            [TableSection(_RFI_HEADER_LABEL, ())] +
            [TableSection(s.label, s.rows) for s in rfi_sections],
            theme=theme,
            # Detail is the only multi-line column here; same estimate,
            # same family as sheet 1's row-height rule.
            row_height=lambda values: _prose_row_height(
                (str(values[1]), _RFI_COLUMNS[1][1]),
            ),
        )

    # Sheet 3 — Projects: omitted (not blank) when no live projects.
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

    # Sheet 4 — Schedule of Insurance: whenever some placement is still in
    # force (compose_soi is non-empty exactly then). The client's own
    # program: show_premiums=True.
    soi_sections = compose_soi(conn, org_id, today)
    if soi_sections:
        ws_soi = wb.create_sheet(sanitize_sheet_title("Schedule of Insurance"))
        render_soi_sheet(ws_soi, soi_sections, theme=theme, show_premiums=True)

    return finalize_workbook(wb, out_path)
