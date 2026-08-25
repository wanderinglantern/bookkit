"""Lines of coverage: the book's ONE vocabulary for what a program covers.

Every guard on identity lives here and not in a caller, for the reason
`repo/team.py` already carries in its own comment: name uniqueness enforced in
one surface is uniqueness the other surfaces write straight past. bookkit has
three surfaces and one of them is an agent, so "the form checks it" is not a
guard — it is a guard against mice.

WHY A NEAR-MATCH GUARD AND NOT JUST A UNIQUENESS INDEX. The index refuses
`General Liability` twice. It happily admits `General Liabilty`, `Gen
Liability` and `General Liability (Primary)`, which is exactly how four
free-text columns became five spellings of the same line in the first place.
An easy-to-add vocabulary WITHOUT a near-match warning becomes the same mess
in six months, only now with foreign keys pointing at it (Grant, 2026-08-25).
The warning is advisory — it never refuses, because genuinely similar lines
exist (`Excess Liability` and `Employers Liability` share four letters and are
not the same thing) and a refusal a user cannot override is a feature that
makes a correct entry impossible.
"""

from __future__ import annotations

import re
import sqlite3

from rapidfuzz import fuzz, process

from ..models import LineOfCoverage
from . import base

_ENTITY = "line_of_coverage"

NEAR_MATCH_CUTOFF = 82
"""Deliberately high. A false warning costs one glance; a missed one costs a
duplicate line that then has to be merged, taking its foreign keys with it."""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "line"


def _free_slug(conn: sqlite3.Connection, name: str) -> str:
    """A slug nothing already holds — including a SOFT-DELETED row, because
    the id is a primary key and soft-delete leaves it occupying the table.
    (The same shape of bug the ref counter had: a minted id must not be one
    the table already holds.)"""
    base_slug = _slugify(name)
    slug, n = base_slug, 1
    while conn.execute(
        "SELECT 1 FROM line_of_coverage WHERE id = ?", (slug,)
    ).fetchone():
        n += 1
        slug = f"{base_slug}-{n}"
    return slug


def all_lines(conn: sqlite3.Connection) -> list[LineOfCoverage]:
    rows = conn.execute(
        f"SELECT * FROM line_of_coverage WHERE {base.alive()}"
        " ORDER BY sort_order, name COLLATE NOCASE"
    ).fetchall()
    return [LineOfCoverage.from_row(r) for r in rows]


def get(conn: sqlite3.Connection, line_id: str) -> LineOfCoverage | None:
    row = base.get(conn, _ENTITY, line_id)
    return LineOfCoverage.from_row(row) if row else None


def by_name(conn: sqlite3.Connection, name: str) -> LineOfCoverage | None:
    """Exact match, case- and whitespace-insensitive. The lookup a backfill or
    an import uses before deciding it has found something new."""
    row = conn.execute(
        f"SELECT * FROM line_of_coverage WHERE LOWER(name) = LOWER(?) AND {base.alive()}",
        (name.strip(),),
    ).fetchone()
    if row is not None:
        return LineOfCoverage.from_row(row)
    row = conn.execute(
        f"SELECT * FROM line_of_coverage WHERE LOWER(abbr) = LOWER(?) AND {base.alive()}",
        (name.strip(),),
    ).fetchone()
    return LineOfCoverage.from_row(row) if row else None


def near_matches(
    conn: sqlite3.Connection, name: str, limit: int = 5, cutoff: int = NEAR_MATCH_CUTOFF
) -> list[tuple[LineOfCoverage, int]]:
    """Existing lines that look like `name`, best first, with their scores.

    Matches on name AND abbr: someone typing "GL" when `General Liability`
    already exists is the same mistake as typing "Gen Liability", and only the
    abbr catches the first one."""
    candidates = all_lines(conn)
    if not candidates:
        return []
    choices: dict[int, str] = {}
    owner: dict[int, LineOfCoverage] = {}
    for line in candidates:
        for text in filter(None, (line.name, line.abbr)):
            key = len(choices)
            choices[key] = text
            owner[key] = line
    hits = process.extract(
        name.strip(), choices, scorer=fuzz.WRatio, limit=limit * 2, score_cutoff=cutoff
    )
    best: dict[str, tuple[LineOfCoverage, int]] = {}
    for _text, score, key in hits:
        line = owner[key]
        current = best.get(line.id)
        if current is None or score > current[1]:
            best[line.id] = (line, int(score))
    return sorted(best.values(), key=lambda pair: -pair[1])[:limit]


class DuplicateLine(Exception):
    """A line by that name already exists. Carries it, so the caller can offer
    to USE it rather than only saying no — a refusal says something."""

    def __init__(self, existing: LineOfCoverage) -> None:
        super().__init__(f"a line of coverage named {existing.name!r} already exists")
        self.existing = existing


def create(
    conn: sqlite3.Connection,
    name: str,
    *,
    abbr: str | None = None,
    acord_code: str | None = None,
    sort_order: int | None = None,
) -> str:
    """Add a line. Refuses an exact duplicate; near matches are the caller's
    warning to show, not this function's to refuse."""
    name = name.strip()
    if not name:
        raise ValueError("a line of coverage needs a name")
    existing = by_name(conn, name)
    if existing is not None:
        raise DuplicateLine(existing)
    if sort_order is None:
        # Alive rows only: a RETIRED line's position should not push every
        # future line further down the list forever. sort_order is not unique,
        # so reusing a dead line's number is harmless.
        row = conn.execute(
            f"SELECT MAX(sort_order) FROM line_of_coverage WHERE {base.alive()}"
        ).fetchone()
        sort_order = int(row[0] or 0) + 10
    return base.insert(
        conn,
        _ENTITY,
        {
            "id": _free_slug(conn, name),
            "name": name,
            "abbr": (abbr or "").strip() or None,
            "acord_code": (acord_code or "").strip().upper() or None,
            "sort_order": sort_order,
        },
    )


def rename(conn: sqlite3.Connection, line_id: str, name: str) -> None:
    """Rename behind the same duplicate guard as create. The id never moves —
    that is the whole point of the slug — so every reference survives."""
    name = name.strip()
    if not name:
        raise ValueError("a line of coverage needs a name")
    existing = by_name(conn, name)
    if existing is not None and existing.id != line_id:
        raise DuplicateLine(existing)
    base.update(conn, _ENTITY, line_id, {"name": name})


# --- merging ---------------------------------------------------------------
#
# The four places a line is referenced. Two are entity tables whose rewrites
# go through base.update and are therefore event-logged and revertible; two
# are pure link tables with no id and no soft-delete, whose moves are recorded
# as provenance but cannot be un-pressed. `usage` exists so the confirm can
# say exactly what will move BEFORE it moves — surface, don't guess.

_ENTITY_REFS = (("appetite", "appetite"), ("project_need", "project_need"))
_LINK_REFS = (
    ("opportunity_line", "opportunity_id"),
    ("team_assignment_line", "team_assignment_id"),
)


def usage(conn: sqlite3.Connection, line_id: str) -> dict[str, int]:
    """How many rows in each table point at this line. The merge preview and
    the retire guard both read it."""
    counts: dict[str, int] = {}
    for table, _entity in _ENTITY_REFS:
        counts[table] = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE line_id = ? AND {base.alive()}",
                (line_id,),
            ).fetchone()[0]
        )
    for table, _owner in _LINK_REFS:
        counts[table] = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE line_id = ?", (line_id,)
            ).fetchone()[0]
        )
    return counts


def merge(conn: sqlite3.Connection, source_id: str, target_id: str) -> dict[str, int]:
    """Fold `source_id` into `target_id`: move every reference, then retire the
    source. Returns what moved.

    NOT UNDOABLE IN ONE PRESS, and the confirm must say so. The entity-table
    rewrites revert cleanly; the link rows do not, because a link table has no
    identity to revert to and re-splitting a merged set is guesswork. A
    merge is therefore a decision, presented with its consequences, rather
    than something to try and take back."""
    if source_id == target_id:
        raise ValueError("a line cannot be merged into itself")
    if get(conn, target_id) is None:
        raise KeyError(f"no line of coverage {target_id!r} to merge into")
    source = get(conn, source_id)
    if source is None:
        raise KeyError(f"no line of coverage {source_id!r} to merge")

    moved = usage(conn, source_id)
    for table, entity in _ENTITY_REFS:
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE line_id = ? AND {base.alive()}", (source_id,)
        ).fetchall()
        for row in rows:
            base.update(conn, entity, row["id"], {"line_id": target_id}, note="line merge")
    for table, owner in _LINK_REFS:
        # INSERT OR IGNORE first: a row already carrying BOTH lines would
        # violate the composite primary key on a bare UPDATE, and that row is
        # not an error — it is one opportunity that named both spellings.
        conn.execute(
            f"INSERT OR IGNORE INTO {table} ({owner}, line_id)"
            f" SELECT {owner}, ? FROM {table} WHERE line_id = ?",
            (target_id, source_id),
        )
        conn.execute(f"DELETE FROM {table} WHERE line_id = ?", (source_id,))
    base.log_event(
        conn, _ENTITY, target_id, "merged_from", None, source_id, note="line merge"
    )
    base.log_event(
        conn, _ENTITY, source_id, "line_link", source_id, target_id, note="line merge"
    )
    base.soft_delete(conn, _ENTITY, source_id, note="line merge")
    return moved
