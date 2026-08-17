"""Batch revert rules. A batch is one writer action; reverting it puts the
book back the way it was — or refuses and says exactly what stops it.

The revert never guesses: if anything in the batch was changed afterwards by
someone else, the whole revert is refused and the conflicts are reported.
That is the house 'surface, don't guess' rule; a half-reverted record is
neither the before nor the after of any single action."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from ..models import EventBatch
from ..repo import base
from ..repo import batches as batches_repo
from ..repo import events as events_repo

# Provenance, not a mutation — derived from the one skip-list in repo/events
# ('created' is not skipped here: the planner handles it as its own kind).
SKIP_FIELDS = frozenset(events_repo.NON_MUTATION_FIELDS) - {"created"}

JOIN_WINDOW_SECONDS = 60
"""How long a burst of edits to the SAME entity, from the SAME (source, tool,
org_id), keeps joining the same undo unit before a fresh edit starts a new
one. Grant's call, 2026-08-17: correcting four cells on a contact should
revert as one thing, not four."""


def _within_join_window(candidate: EventBatch, now: str) -> bool:
    """Strictly less-than, not <=: JOIN_WINDOW_SECONDS = 0 must disable
    joining outright, including two calls issued in the same wall-clock
    second (created_at has only second precision).

    A negative elapsed (the clock moved backwards — an NTP correction, a VM
    resume) is treated as OUT of window, not in it: `< JOIN_WINDOW_SECONDS`
    alone would read -5s as well inside a 60s window and let a stale batch
    join years later the moment the clock next slipped."""
    created = datetime.fromisoformat(candidate.created_at)
    current = datetime.fromisoformat(now)
    elapsed = (current - created).total_seconds()
    return 0 <= elapsed < JOIN_WINDOW_SECONDS


def _targets_only(conn: sqlite3.Connection, batch_id: str, entity_id: str) -> bool:
    """True only when this batch has at least one event, and every event in
    it names this entity.

    EQUALITY, not subset — this is load-bearing, not stylistic. The join
    read in `_joinable` happens on a plain SELECT, outside `db.transaction`
    and before `_tx_lock` is taken (deliberately, to avoid the nested-
    transaction hazard `db.transaction` warns about). That leaves a
    time-of-check/time-of-use gap: two threads editing DIFFERENT entities can
    both read the same EMPTY candidate batch and both see it as joinable,
    because an empty batch's touched-entity set is `{}`, and `{} <= {x}` is
    true for every x. Both threads then write into that one batch under
    different entities — exactly the outcome the design forbids: 'editing
    contact A then contact B inside the window are two different actions,
    and merging them would let one revert touch a record the user never
    edited.' An empty batch that once was joinable by everybody is joinable
    by nobody now, which is also correct on its own terms: an empty batch
    has no edit run to continue. Do not relax this back to a subset test."""
    touched = {event.entity_id for event in batches_repo.events_for(conn, batch_id)}
    return touched == {entity_id}


def _joinable(
    conn: sqlite3.Connection,
    candidate: EventBatch | None,
    *,
    source: str,
    tool: str,
    org_id: str | None,
    entity_id: str,
    now: str,
) -> bool:
    if candidate is None:
        return False
    if candidate.source != source or candidate.tool != tool or candidate.org_id != org_id:
        return False
    if candidate.reverted_at is not None:
        return False
    if not _within_join_window(candidate, now):
        return False
    return _targets_only(conn, candidate.id, entity_id)


@contextmanager
def open_batch(
    conn: sqlite3.Connection,
    *,
    source: str,
    tool: str,
    summary: str,
    org_id: str | None = None,
    entity_id: str | None = None,
) -> Iterator[EventBatch]:
    """One writer action, one undo unit: opens the transaction AND the batch,
    so every event written inside is grouped, revertible together, and counted
    against the blast cap.

    `source` says which surface wrote it ('mcp' or 'tui') — the only reason
    both callers are not identical. This lives in services/ rather than in
    either caller because the MCP server had it first and the TUI grew a
    second, subtly different copy; one home is what keeps `R` able to revert
    both.

    `entity_id`, when given, lets a burst of edits to the SAME record join
    the most recent batch instead of opening a new one — inline cell editing
    otherwise turns four corrected fields into four undo units. It joins only
    when the most recent batch, period, matches on source/tool/org_id, is not
    reverted, was created within JOIN_WINDOW_SECONDS, and every event already
    in it targets this same entity_id. Any mismatch — a different tool, a
    different record, a reverted or stale batch — opens a fresh batch instead.
    Omitting entity_id (the default) is today's behaviour, unchanged: every
    existing caller gets a new batch every time.

    The join decision reads prior events with plain SELECTs, before this call
    opens its own transaction — never inside one — so it neither contends for
    db._tx_lock (which only the outermost transaction() call takes) nor risks
    silently joining an already-open outer transaction the way a nested
    `db.transaction()` call would."""
    from .. import db

    if entity_id is not None:
        candidate = batches_repo.most_recent(conn)
        now = db.utc_now()
        if _joinable(
            conn, candidate, source=source, tool=tool, org_id=org_id,
            entity_id=entity_id, now=now,
        ):
            assert candidate is not None  # narrows for mypy; _joinable checked it
            with db.transaction(conn, batch=db.BatchState(batch_id=candidate.id)):
                yield candidate
            return

    batch_id = batches_repo.new_batch_id()
    with db.transaction(conn, batch=db.BatchState(batch_id=batch_id)):
        yield batches_repo.create(
            conn, batch_id=batch_id, source=source, tool=tool,
            summary=summary, org_id=org_id,
        )


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
    if batch.tool.startswith("program_"):
        # File contents are not event_log rows — reverting the proj_* cache
        # under an untouched file would be a lie. The file-side path exists.
        raise ValueError(
            f"{ref} wrote a towerkit program FILE — use program_revert_file, "
            f"which restores the file itself and re-projects"
        )
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

    reverted = [*updates, *creates, *deletes]
    if not reverted:
        # force on a fully conflicted batch: nothing to apply. Marking it
        # reverted here would report success for a no-op AND burn the batch,
        # so the user could never put it back even after undoing their own
        # change. applied means "the book moved", not "the call ran".
        return RevertResult(batch, reverted=[], refused=plan.conflicts, applied=False)

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
        reverted=reverted,
        refused=plan.conflicts,
        applied=True,
    )
