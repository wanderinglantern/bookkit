"""Taking a program out of the book — the placement, and its file moved aside.

Grant created two programs for one client by mistake (2026-08-21). They were
not duplicates in the merge sense: two DIFFERENT towerkit files, one of which
should never have existed. Merge is the wrong tool for that — it exists to
fold two records of the same thing together, and it refuses two file-backed
placements outright, deliberately, because that would be two sources of truth.
What was missing was a way to say "this one was a mistake".

THE FILE IS MOVED, NEVER UNLINKED (Grant, 2026-08-21). towerkit JSON is the
sole authority for program structure — the proj_* tables are a rebuildable
cache and the file is not — so deleting one is the only genuinely
unrecoverable thing this app could do. It goes to `<program dir>/.removed/`
under a timestamped name, and the confirm prints the destination before
anything happens. Putting it back is `mv`.

ORDER IS LOAD-BEARING: the database write commits FIRST and the file moves
after. Both orders can fail halfway; only one of them fails safe. File-first
would leave a live placement pointing at a file that is not there — the
"missing program file" state, reached by an app that did it to itself.
Database-first leaves at worst a removed record and a file still in place,
which is recoverable by hand and by re-running.

NOTHING LIVE MAY POINT AT IT. A soft-deleted placement whose submissions,
tasks or requests still reference it leaves readers holding a dead foreign key
— the same hazard services/merge.py exists to move records away from. So this
REFUSES while any of them are alive and names each one, rather than cascading:
a program with real work filed against it is not the mistake this is for, and
the honest answer is Merge (to keep the work) or moving the records first.
`member_deactivate`'s `cascade=True` is the shape to add if that proves too
strict; it is deliberately not here yet.

UNDO IS HALF A STORY, AND THE CONFIRM SAYS SO. The placement comes back —
`deleted_at` is one field in one batch. The file does not follow it out of
`.removed/`, because file contents are not event_log rows (the same wall
services/program_files.py was written around). Saying "undo puts it back"
without that qualification would be false safety about the one part that is
not automatic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..models import Placement
from ..repo import placements, projection

REMOVED_DIRNAME = ".removed"


class ProgramRemoveRefused(ValueError):
    """The removal cannot proceed, with the reasons already in broker words."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass(frozen=True)
class Removal:
    placement_ref: str
    program_name: str
    file_from: str | None
    file_to: str | None


def blockers(conn: sqlite3.Connection, placement_id: str) -> list[str]:
    """What still points at this program, in the words the confirm shows.

    The counting is `repo.placements.dependants` — every query lives in repo/,
    and the set of tables that name a placement is a fact about the schema
    rather than a rule about removal.
    """
    return [
        f"{count} {label}"
        for label, count in placements.dependants(conn, placement_id)
    ]


def consequences(conn: sqlite3.Connection, placement: Placement) -> list[str]:
    """What removing this program will do, for the confirm step to show.

    A confirm that only asks "are you sure?" is not a confirm — the things that
    matter here are invisible from the section header: how much of the tower
    goes with it, where the file lands, and which half of this undo actually
    undoes.
    """
    notes: list[str] = []
    layers = projection.layers_for_placement(conn, placement.id)
    if layers:
        notes.append(
            f"{len(layers)} layer{'s' if len(layers) != 1 else ''} of cover stop "
            f"being counted anywhere in the book"
        )
    if placement.program_path:
        notes.append(
            f"the program file is MOVED, not deleted — it goes to "
            f"{REMOVED_DIRNAME}/ beside it and can be moved back by hand"
        )
    else:
        notes.append("this program has no file linked, so nothing moves on disk")
    notes.append(
        "undo brings the RECORD back; the file stays where this put it, "
        "because file contents are not part of an undo batch"
    )
    return notes


def retired_path(program_path: Path, *, now: str) -> Path:
    """Where the file goes: `.removed/<stamp>-<name>` beside it.

    Beside it, and not in `backups/`, for the same reason `.mcp-snapshots`
    lives there — a program file belongs to its program root, and a rollback
    a person has to go looking for in a different tree is one they will not
    find. The stamp is passed IN: no wall clock in here.
    """
    stamp = now.replace(":", "").replace("-", "").replace(".", "")[:15]
    return program_path.parent / REMOVED_DIRNAME / f"{stamp}-{program_path.name}"


def remove(
    conn: sqlite3.Connection,
    placement: Placement,
    *,
    open_batch: object,
    now: str,
) -> Removal:
    """Remove one program: refuse, then write, then move the file.

    `open_batch` is passed in rather than imported, the same way
    services/program_files.write takes it — this module depends on neither
    surface, and each caller supplies its own source stamp.
    """
    from .. import sync as sync_mod

    held = blockers(conn, placement.id)
    if held:
        raise ProgramRemoveRefused([
            f"{placement.ref} still has " + ", ".join(held),
            "move them to the surviving program (or Merge, which does it for "
            "you) and remove this one after",
        ])

    source_file: Path | None = None
    if placement.program_path:
        source_file = sync_mod.program_file(conn, placement)
        if not source_file.exists():
            # Not an error worth refusing over — the record is still the thing
            # being removed, and a file that is already gone is one less step.
            # Said plainly in the result rather than silently ignored.
            source_file = None
        else:
            _refuse_if_shared(conn, placement, source_file)

    destination = (
        retired_path(source_file, now=now) if source_file is not None else None
    )

    # THE DATABASE FIRST. See the module docstring: this order fails safe.
    # `remove_program`, NOT `program_remove`: services/batches.revert reserves
    # the `program_` PREFIX for towerkit-file writes that carry a snapshot
    # pre-image, and refuses to batch-revert one because reverting the proj_*
    # cache under an untouched file would be a lie. This is not one of those —
    # it writes a column and moves a file, and its record half is revertible
    # like any other. A prefixed name would have routed `u` into the file-side
    # path and refused an undo that works perfectly well (caught by the test
    # that reverts it, 2026-08-21).
    with open_batch(  # type: ignore[operator]
        conn,
        tool="remove_program",
        org_id=placement.org_id,
        summary=f"removed {placement.program_name}",
    ):
        projection.replace_for_placement(conn, placement.id, now, [], [], [])
        placements.delete(conn, placement.id)

    if source_file is not None and destination is not None:
        destination.parent.mkdir(exist_ok=True)
        source_file.rename(destination)

    return Removal(
        placement_ref=placement.ref,
        program_name=placement.program_name,
        file_from=str(source_file) if source_file else None,
        file_to=str(destination) if destination else None,
    )


def _refuse_if_shared(
    conn: sqlite3.Connection, placement: Placement, source_file: Path
) -> None:
    """Another live placement reading the same file is a hard stop.

    Moving it would break that one instead, silently — it would keep its
    `program_path`, keep reading as linked, and fail at the next write with an
    errno. Two placements sharing a file is legal today (a renewal carried the
    link over) and this is the one operation for which it is fatal.
    """
    from .. import sync as sync_mod

    for other in placements.for_org(conn, placement.org_id):
        if other.id == placement.id or not other.program_path:
            continue
        if sync_mod.program_file(conn, other) == source_file:
            raise ProgramRemoveRefused([
                f"{other.ref} ({other.program_name}) reads the same file, "
                f"{source_file.name}",
                "unlink or remove that one first — moving the file would "
                "break it with nothing said",
            ])


__all__ = [
    "REMOVED_DIRNAME",
    "ProgramRemoveRefused",
    "Removal",
    "blockers",
    "consequences",
    "remove",
    "retired_path",
]
