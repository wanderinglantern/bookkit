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
REFUSES BY DEFAULT while any of them are alive and names each one, because a
program with real work filed against it is usually not the mistake this is for
and the honest answer is Merge, which keeps the work.

`cascade=True` is the other answer, on `member_deactivate`'s shape (Grant,
2026-08-21): the dependants go WITH the program, in ONE batch, so a single
revert puts every one of them back. Two things make that safe enough to offer.
Each row is removed through its own kind's VERB, never a blanket UPDATE — an
information request takes its items with it, because an item is reachable only
through its request and one left behind is unreachable rather than preserved.
And one refusal survives cascade entirely: a request somebody has ANSWERED.
Deleting the question deletes the client's answer with it, and no amount of
"yes I am sure" about a *program* is consent to that — services/rfi.py owns
that rule and this defers to it rather than restating it.

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
    # how many rows went WITH the program, when cascade was asked for
    cascaded: int = 0


# Which verb takes each kind of dependant off the book. NOT a blanket UPDATE
# over the six tables: a request has to go through the service that takes its
# ITEMS with it, and routing every kind through its own door is what stops this
# module from becoming a seventh place that knows how to delete a submission.
# A table added to repo.placements._DEPENDANTS and missing here refuses loudly
# rather than being skipped — asserted by tests/test_program_remove.py.
def _remove_dependant(
    conn: sqlite3.Connection, table: str, row_id: str, *, source: str
) -> None:
    from ..repo import documents, projects, submissions, tasks, team
    from . import rfi as rfi_svc

    if table == "submission":
        submissions.delete(conn, row_id)
    elif table == "task":
        tasks.delete(conn, row_id)
    elif table == "rfi_request":
        # THE SERVICE, not the repo: an item is reachable only through its
        # request, so a request removed without its items leaves them
        # unreachable rather than preserved. It opens its own batch, which
        # JOINS this one (db.transaction nests by joining), so the whole
        # cascade stays one undo unit.
        rfi_svc.remove_request(conn, row_id, source=source)
    elif table == "document":
        documents.delete(conn, row_id)
    elif table == "team_assignment":
        team.unassign(conn, row_id)
    elif table == "project_need":
        projects.delete_need(conn, row_id)
    else:  # pragma: no cover - the guard is the point; the test proves it
        raise ProgramRemoveRefused([
            f"{table} rows point at this program and nothing here knows how "
            f"to remove one — add its verb to _remove_dependant",
        ])


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


def cascade_refusals(conn: sqlite3.Connection, placement_id: str) -> list[str]:
    """What a cascade STILL cannot take, in the words the confirm shows.

    Separate from `blockers` because they answer different questions:
    `blockers` is "what would be stranded", which cascade resolves, and this is
    "what would be destroyed that is not ours to destroy", which it does not.

    Today that is exactly one thing — an ANSWERED information request. The rule
    and its wording live in services/rfi.remove_request; this asks the same
    question ahead of time so the confirm can decline before the click rather
    than half-way through a batch.
    """
    from . import rfi as rfi_svc

    refusals: list[str] = []
    for table, row_id in placements.dependant_rows(conn, placement_id):
        if table != "rfi_request":
            continue
        try:
            rfi_svc.check_removable(conn, row_id)
        except ValueError as exc:
            refusals.append(str(exc))
    return refusals


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
    source: str = "web",
    cascade: bool = False,
) -> Removal:
    """Remove one program: refuse, then write, then move the file.

    `open_batch` is passed in rather than imported, the same way
    services/program_files.write takes it — this module depends on neither
    surface. `source` is only for the dependant verbs that stamp one of their
    own (services/rfi), and it defaults rather than being required because the
    batch row already records the surface.

    `cascade=True` takes the dependants with it, in the SAME batch, so one
    revert puts every one of them back. It does not override
    `cascade_refusals`: an answered information request is not this decision's
    to make.
    """
    from .. import sync as sync_mod

    held = blockers(conn, placement.id)
    if held and not cascade:
        raise ProgramRemoveRefused([
            f"{placement.ref} still has " + ", ".join(held),
            "move them to the surviving program (or Merge, which does it for "
            "you) and remove this one after — or remove them WITH it, as one "
            "revertible batch",
        ])
    carried = placements.dependant_rows(conn, placement.id) if cascade else []
    if carried:
        refused = cascade_refusals(conn, placement.id)
        if refused:
            raise ProgramRemoveRefused([
                f"{placement.ref} cannot take everything with it:",
                *refused,
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
    summary = f"removed {placement.program_name}"
    if carried:
        summary += f" and {len(carried)} record{'s' if len(carried) != 1 else ''}"
    with open_batch(  # type: ignore[operator]
        conn,
        tool="remove_program",
        org_id=placement.org_id,
        summary=summary,
    ):
        # DEPENDANTS FIRST, then the placement. The order is not cosmetic: each
        # dependant verb reads the row it is removing, and one of them
        # (rfi_svc.remove_request) reads the request's own org — none of which
        # is helped by the placement already being a tombstone.
        for table, row_id in carried:
            _remove_dependant(conn, table, row_id, source=source)
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
        cascaded=len(carried),
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
    "cascade_refusals",
    "consequences",
    "remove",
    "retired_path",
]
