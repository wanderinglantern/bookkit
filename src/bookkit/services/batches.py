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
from ..repo import events as events_repo

# Provenance, not a mutation — derived from the one skip-list in repo/events
# ('created' is not skipped here: the planner handles it as its own kind).
SKIP_FIELDS = frozenset(events_repo.NON_MUTATION_FIELDS) - {"created"}


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


def account_names(
    conn: sqlite3.Connection, batches: list[EventBatch]
) -> dict[str, str]:
    """org_id → account name for every batch that names one, in ONE query —
    the label rule shared by the MCP list and the TUI section (which were
    forked on day one, and drifting). A missing key means the org was merged
    or deleted away; the caller chooses how '(deleted account)' renders."""
    from ..repo import orgs

    return orgs.names_for(conn, {b.org_id for b in batches if b.org_id})


def _cell(row: sqlite3.Row, field: str) -> str | None:
    value = row[field]
    return None if value is None else str(value)


def plan_revert(conn: sqlite3.Connection, batch: EventBatch) -> RevertPlan:
    """Collapse the batch to its net effect, then check each net change against
    what the record holds now — read through repo.base.raw_row (dead or alive),
    because a revert compares against reality, not the alive() view."""
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

    # one dead-or-alive read per entity, shared by every check below
    entities = (
        {(t, i) for (t, i, _f) in net} | set(created) | set(deleted)
    )
    rows = {
        entity: base.raw_row(conn, entity[0], entity[1]) for entity in entities
    }

    updates: list[Change] = []
    conflicts: list[Conflict] = []

    for (entity_type, entity_id, field), (old, new) in net.items():
        entity = (entity_type, entity_id)
        # A row this batch created is going away wholesale; its field checks
        # are the created-entity check below.
        if entity in created:
            continue
        change = Change(entity_type, entity_id, field, old, new)
        row = rows[entity]
        if row is None:
            conflicts.append(Conflict(change, "(row gone)"))
            continue
        if row["deleted_at"] is not None and entity not in deleted:
            # the user deleted this record AFTER the batch; restoring field
            # values on it would resurrect nothing and base.update would
            # refuse anyway — surface it instead
            conflicts.append(Conflict(change, "(deleted since)"))
            continue
        current = _cell(row, field)
        if current != new:
            conflicts.append(Conflict(change, current))
        else:
            updates.append(change)

    for entity, change in deleted.items():
        row = rows[entity]
        if row is None or row["deleted_at"] is None:
            # someone undeleted it since (or it vanished outright)
            conflicts.append(Conflict(change, None))

    for entity, change in created.items():
        # The contract is 'changed since → refused', and a row this batch
        # created is no exception: user edits after the create would vanish
        # under the soft-delete with no conflict shown. base.insert logs no
        # field values, so the check is the event log, not a value compare.
        edits = batches_repo.external_change_count(
            conn, entity[0], entity[1], batch.id
        )
        if edits:
            conflicts.append(
                Conflict(change, f"({edits} change(s) since it was created)")
            )

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

    # force applies ONLY the clean changes: anything conflicted is filtered
    # out here and reported strictly as refused, never both
    blocked = {
        (c.change.entity_type, c.change.entity_id, c.change.field)
        for c in plan.conflicts
    }

    def clean(change: Change) -> bool:
        return (change.entity_type, change.entity_id, change.field) not in blocked

    deletes = [c for c in plan.deletes if clean(c)]
    updates = [c for c in plan.updates if clean(c)]
    creates = [c for c in plan.creates if clean(c)]

    with db.transaction(conn):                     # deliberately unbatched
        # undeletes FIRST: a batch that edited then soft-deleted one row needs
        # the row alive again before base.update (alive-filtered) restores it
        for change in deletes:
            base.undelete(conn, change.entity_type, change.entity_id)
        for change in updates:
            base.update(
                conn, change.entity_type, change.entity_id,
                {change.field: change.old_value}, note="revert",
            )
        for change in creates:
            if base.get(conn, change.entity_type, change.entity_id) is not None:
                base.soft_delete(
                    conn, change.entity_type, change.entity_id, note="revert"
                )
        batches_repo.mark_reverted(conn, batch.id, now)

    return RevertResult(
        batch=batch,
        reverted=[*updates, *creates, *deletes],
        refused=plan.conflicts,
        applied=True,
    )
