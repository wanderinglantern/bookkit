"""Batch revert rules. A batch is one writer action; reverting it puts the
book back the way it was — or refuses and says exactly what stops it.

The revert never guesses: if anything in the batch was changed afterwards by
someone else, the whole revert is refused and the conflicts are reported.
That is the house 'surface, don't guess' rule; a half-reverted record is
neither the before nor the after of any single action.

A REVERT RESTORES WHAT WAS TYPED AND RE-DERIVES WHAT WAS COMPUTED. Replaying
a derived column backwards restores the value the cache held at that moment,
which is not what it would say about the rows now standing — `_rederive_caches`
carries the case that shipped and why the fix is a recompute after the replay
rather than a column that stops being logged."""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from ..models import EventBatch, EventLogEntry
from ..repo import aliases as aliases_repo
from ..repo import base
from ..repo import batches as batches_repo
from ..repo import events as events_repo
from ..repo import marketing as marketing_repo

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

    active = db.current_batch()
    if active is not None:
        # ALREADY INSIDE A BATCH — join it and create NO row.
        #
        # db.transaction has always joined rather than nested (SQLite has no
        # nested BEGIN) and has always ignored an inner `batch=`, so the events
        # written here were already landing on the OUTER batch. What this
        # function still did was INSERT a batch row for the inner action
        # anyway, and that row then had zero events: a line in the changes list
        # describing an action, offering a Revert, and reverting nothing.
        #
        # Found when the program cascade called services/rfi.remove_request
        # inside its own batch (2026-08-21) — the right call, because a request
        # has to take its items with it — and the changes list grew a phantom
        # "removed information request" beside the real removal. Every service
        # that opens a batch and is reachable from inside another one had the
        # same shape; this closes the class rather than teaching one caller to
        # avoid it.
        with db.transaction(conn):
            yield batches_repo.get(conn, active.batch_id)
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
    clause: str | None = None
    """The sentence this conflict is refused WITH, where the generic
    '<entity> <field> changed since' the surfaces build would be wrong.

    A dependent conflict (below) is not a field that moved — nothing on the
    row changed at all — so a surface rendering it as "submission created
    changed since" describes neither what happened nor what to do about it.
    The clause is written once, here, and both surfaces read it rather than
    each growing a copy that drifts (the `program_file_refusal` pattern)."""


@dataclass(frozen=True)
class RevertPlan:
    batch: EventBatch
    creates: list[Change]
    deletes: list[Change]
    updates: list[Change]
    conflicts: list[Conflict]
    aliases: list[Change] = dataclasses.field(default_factory=list)
    """carrier_alias moves — their own lane; see `_plan_alias_moves`."""

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


def dependent_clause(
    entity_type: str, holders: list[tuple[str, str, str]]
) -> str:
    """The sentence a dependent conflict is refused with, in one home because
    two surfaces render it.

    It names the way OUT, which is the whole reason this refuses rather than
    cascading: the children were recorded after this batch, so undoing them
    first leaves nothing hanging off the parent and this batch reverts clean.
    That is ordinary last-in-first-out undo, not a dead end.

    Entity types are printed with their underscores opened out rather than
    through a label table — a second vocabulary is a second thing to drift."""
    counts: dict[str, int] = {}
    for child_entity, _child_id, _column in holders:
        counts[child_entity] = counts.get(child_entity, 0) + 1
    named = ", ".join(
        f"{count} {child.replace('_', ' ')}(s)" for child, count in counts.items()
    )
    return (
        f"{entity_type.replace('_', ' ')} still has {named} recorded against it "
        f"since — undo those first"
    )


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

    revertible = set(created)
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
            revertible.discard(entity)

    # ... and 'acquired children since → refused', which is the same fact read
    # through the SCHEMA instead of the event log, because a child logs against
    # the child and an earlier batch's plan cannot see it (repo/base.py's
    # `child_links` carries the why).
    #
    # A LINK THIS BATCH IS LETTING GO OF DOES NOT HOLD. `need_to_opportunity`
    # creates an opportunity and points an EXISTING need at it; reverting sets
    # `need.opportunity_id` back to NULL in the same act, so the need is not a
    # holder — it is being released. Reading the raw row alone called that a
    # conflict and made the flow unrevertible. Only a CLEAN update releases:
    # one that conflicted is not going to be written back, so the link stays
    # and it does hold.
    released = {
        (change.entity_type, change.entity_id, change.field): change.old_value
        for change in updates
    }

    def holds(parent_id: str, held: tuple[str, str, str]) -> bool:
        child_type, child_id, column = held
        key = (child_type, child_id, column)
        return released.get(key, parent_id) == parent_id

    links = base.child_links(conn)
    holders = {
        entity: [
            held
            for held in base.live_dependents(conn, entity[0], entity[1], links)
            if holds(entity[1], held)
        ]
        for entity in created
    }

    # Settled to a FIXPOINT because the answer feeds itself: a child this batch
    # created but can no longer delete (edited since, above) is a child that
    # survives, so its parent cannot go either. `revertible` shrinks every
    # round, so this terminates.
    while True:
        stuck = {
            entity: [
                held for held in holders[entity]
                if (held[0], held[1]) not in revertible
            ]
            for entity in revertible
        }
        stuck = {entity: held for entity, held in stuck.items() if held}
        if not stuck:
            break
        for entity, held in stuck.items():
            conflicts.append(
                Conflict(
                    created[entity],
                    f"({len(held)} row(s) still point at it)",
                    clause=dependent_clause(entity[0], held),
                )
            )
        revertible -= set(stuck)

    alias_moves, alias_conflicts = _plan_alias_moves(conn, events)
    conflicts.extend(alias_conflicts)

    return RevertPlan(
        batch=batch,
        creates=list(created.values()),
        deletes=list(deleted.values()),
        updates=updates,
        aliases=alias_moves,
        conflicts=conflicts,
    )


def _plan_alias_moves(
    conn: sqlite3.Connection, events: list[EventLogEntry]
) -> tuple[list[Change], list[Conflict]]:
    """carrier_alias rows in their OWN lane, because they are not entities.

    The table is keyed by the alias string — no id, no updated_at, no
    deleted_at — so `base.raw_row` cannot read one and `base.update` cannot
    write one, which is why 'carrier_alias' is in NON_MUTATION_FIELDS and the
    generic planner above skips it. Skipping it in the PLANNER is right;
    skipping it in the REVERT was the bug: a market merge moved every alias to
    the survivor and reverting the merge brought the duplicate back with none
    of them (2026-08-18).

    The event says where the alias ended up (entity_id) and where it came from
    (old_value, None if it was created outright). Same 'surface, don't guess'
    rule as every other lane: if the alias points somewhere other than where
    this batch left it, someone moved it since and the revert is refused."""
    from ..repo import aliases

    net: dict[str, Change] = {}                    # alias -> the batch's net move
    for event in events:
        if event.field != "carrier_alias" or event.new_value is None:
            continue
        alias = event.new_value
        first = net.get(alias)
        net[alias] = Change(
            "org", event.entity_id, "carrier_alias",
            first.old_value if first else event.old_value,   # earliest origin
            alias,
        )
    if not net:
        return [], []

    current = aliases.alias_map(conn)
    moves, conflicts = [], []
    for alias, change in net.items():
        now_owned_by = current.get(alias)
        if now_owned_by != change.entity_id:
            conflicts.append(Conflict(change, now_owned_by))
        else:
            moves.append(change)
    return moves, conflicts


class AlreadyReverted(Exception):
    """This batch has been reverted once already."""


def program_file_refusal(ref: str) -> str:
    """The one sentence a program_* batch is refused with.

    Extracted from `revert` below (2026-08-18) because the web surface has to
    render that refusal as a toast on a LATER request than the one that hit
    it — the POST answers with an HX-Redirect carrying only a short outcome
    token, never the message text, so the page that renders the toast has to
    derive the sentence again. Re-typing it in the web layer would be two
    copies of the same refusal free to drift; this is the `date_refusal`
    pattern (one home per refusal) applied to the one refusal both surfaces
    show."""
    return (
        f"{ref} wrote a towerkit program FILE — use program_revert_file, "
        f"which restores the file itself and re-projects"
    )


def _rederive_caches(conn: sqlite3.Connection, reverted: list[Change]) -> None:
    """A REVERT PUTS THE AUTHORITY BACK; EVERY CACHE OVER IT IS RECOMPUTED.

    Everything above this line replays a batch's events backwards, field by
    field, and that is right for a fact somebody TYPED: the old value is what
    the book said before, and `plan_revert` refuses when anything moved since.
    It is wrong for a DERIVED column. A cache is not a fact about the past, it
    is a statement about the present, and replaying it backwards restores what
    it happened to hold at that moment — which is a different thing from what
    it would say about the rows now standing.

    `submission`'s six marketing columns are the one such cache in the event
    log today. They are event-logged on purpose and must stay that way: their
    old values are the ONLY record of figures typed before responses existed
    (`repo.marketing.roll_up_submission`'s "open edge"), so a revert has to be
    able to put those back. So the answer is not to stop logging them, the way
    proj_* is not logged — it is to re-derive AFTER the replay, on top of it.
    Where responses survive, the derivation wins and the restored value is
    overwritten in the same act; where none do, the roll-up writes nothing at
    all and the restored figures stand, which is exactly the case the logging
    exists for.

    Only rows this revert actually MOVED are re-derived — never every
    submission the batch mentions. A batch that typed on a submission and
    touched no response has nothing derived to fix, and recomputing it would
    let this function overwrite the very value the replay just restored.
    """
    marketing_repo.roll_up_for_responses(
        conn,
        [c.entity_id for c in reverted if c.entity_type == "market_response"],
        note="revert",
    )


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
    cannot itself be batch-reverted and `u` skips it the way it skips undo.

    Replaying the events is not the whole job: a column DERIVED from rows this
    batch moved has to be recomputed from the rows that survive, never
    restored. `_rederive_caches` below carries why."""
    from .. import db

    batch = batches_repo.get_by_ref(conn, ref)     # KeyError on unknown
    if batch.tool.startswith("program_"):
        # File contents are not event_log rows — reverting the proj_* cache
        # under an untouched file would be a lie. The file-side path exists.
        raise ValueError(program_file_refusal(ref))
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
    alias_moves = [c for c in plan.aliases if clean(c)]

    reverted = [*updates, *creates, *deletes, *alias_moves]
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
        for change in alias_moves:
            # new_value is the alias, old_value the org it came from — None
            # when the batch created it, in which case putting it back means
            # removing it
            if change.old_value is None:
                aliases_repo.remove(conn, str(change.new_value))
            else:
                aliases_repo.set_alias(conn, str(change.new_value), change.old_value)
        # LAST, and inside the same transaction: the caches derived from the
        # rows this revert just moved are recomputed on top of the replay.
        _rederive_caches(conn, reverted)
        batches_repo.mark_reverted(conn, batch.id, now)

    return RevertResult(
        batch=batch,
        reverted=reverted,
        refused=plan.conflicts,
        applied=True,
    )
