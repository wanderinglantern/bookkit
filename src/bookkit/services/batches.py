"""Batch revert rules. A batch is one writer action; reverting it puts the
book back the way it was — or refuses and says exactly what stops it.

The revert never guesses: if anything in the batch was changed afterwards by
someone else, the whole revert is refused and the conflicts are reported.
That is the house 'surface, don't guess' rule; a half-reverted record is
neither the before nor the after of any single action."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..models import EventBatch
from ..repo import base
from ..repo import batches as batches_repo

# Provenance, not a mutation — the MCP server stamps it after every write.
SKIP_FIELDS = frozenset({"source"})


@dataclass(frozen=True)
class Change:
    entity_type: str
    entity_id: str
    field: str
    old_value: str | None
    new_value: str | None


@dataclass(frozen=True)
class Conflict:
    change: Change
    current_value: str | None


@dataclass(frozen=True)
class RevertPlan:
    batch: EventBatch
    creates: list[Change]
    deletes: list[Change]
    updates: list[Change]
    conflicts: list[Conflict]

    @property
    def clean(self) -> bool:
        return not self.conflicts


def _current_value(
    conn: sqlite3.Connection, entity_type: str, entity_id: str, field: str
) -> str | None:
    """The value the row holds NOW, dead or alive — a revert must compare
    against reality, not the alive() view."""
    table = base.ENTITY_TABLES[entity_type]
    row = conn.execute(
        f"SELECT {field} FROM {table} WHERE id = ?", (entity_id,)  # noqa: S608
    ).fetchone()
    if row is None:
        return None
    return None if row[0] is None else str(row[0])


def plan_revert(conn: sqlite3.Connection, batch: EventBatch) -> RevertPlan:
    """Collapse the batch to its net effect, then check each net change against
    what the record holds now."""
    events = batches_repo.events_for(conn, batch.id)

    created: dict[tuple[str, str], Change] = {}
    deleted: dict[tuple[str, str], Change] = {}
    # (entity_type, entity_id, field) -> [first old_value, last new_value]
    net: dict[tuple[str, str, str], list[str | None]] = {}

    for event in events:
        if event.field in SKIP_FIELDS:
            continue
        entity = (event.entity_type, event.entity_id)
        if event.field == "created":
            created[entity] = Change(
                event.entity_type, event.entity_id, "created", None, None
            )
            continue
        if event.field == "deleted_at":
            deleted[entity] = Change(
                event.entity_type, event.entity_id, "deleted_at",
                event.old_value, event.new_value,
            )
            continue
        key = (event.entity_type, event.entity_id, event.field)
        if key in net:
            net[key][1] = event.new_value          # newest new_value wins
        else:
            net[key] = [event.old_value, event.new_value]

    updates: list[Change] = []
    conflicts: list[Conflict] = []

    for (entity_type, entity_id, field), (old, new) in net.items():
        # A row this batch created is going away wholesale; conflict-checking
        # its fields would refuse reverts that are in fact clean.
        if (entity_type, entity_id) in created:
            continue
        change = Change(entity_type, entity_id, field, old, new)
        current = _current_value(conn, entity_type, entity_id, field)
        if current != new:
            conflicts.append(Conflict(change, current))
        else:
            updates.append(change)

    for (entity_type, entity_id), change in deleted.items():
        current = _current_value(conn, entity_type, entity_id, "deleted_at")
        if current is None:                        # someone undeleted it since
            conflicts.append(Conflict(change, None))

    return RevertPlan(
        batch=batch,
        creates=list(created.values()),
        deletes=list(deleted.values()),
        updates=updates,
        conflicts=conflicts,
    )


class AlreadyReverted(Exception):
    """This batch has been reverted once already."""


@dataclass(frozen=True)
class RevertResult:
    batch: EventBatch
    reverted: list[Change]
    refused: list[Conflict]
    applied: bool


def revert(
    conn: sqlite3.Connection, ref: str, now: str, force: bool = False
) -> RevertResult:
    """Put the book back the way it was before this batch.

    Refuses outright when anything in the batch was changed since, unless
    `force` — then the clean changes revert and the conflicted ones are
    reported untouched. `now` is a parameter, never the wall clock.

    The revert's own writes carry note='revert' and NO batch_id, so a revert
    cannot itself be batch-reverted and `u` skips it the way it skips undo."""
    from .. import db

    batch = batches_repo.get_by_ref(conn, ref)     # KeyError on unknown
    if batch.reverted_at is not None:
        raise AlreadyReverted(f"{ref} was reverted at {batch.reverted_at}")

    plan = plan_revert(conn, batch)
    if plan.conflicts and not force:
        return RevertResult(batch, reverted=[], refused=plan.conflicts,
                            applied=False)

    with db.transaction(conn):                     # deliberately unbatched
        for change in plan.updates:
            base.update(
                conn, change.entity_type, change.entity_id,
                {change.field: change.old_value}, note="revert",
            )
        for change in plan.creates:
            if base.get(conn, change.entity_type, change.entity_id) is not None:
                base.soft_delete(
                    conn, change.entity_type, change.entity_id, note="revert"
                )
        for change in plan.deletes:
            base.undelete(conn, change.entity_type, change.entity_id)
        batches_repo.mark_reverted(conn, batch.id, now)

    return RevertResult(
        batch=batch,
        reverted=[*plan.updates, *plan.creates, *plan.deletes],
        refused=plan.conflicts,
        applied=True,
    )
