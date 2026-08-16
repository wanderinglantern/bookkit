"""Undo the last thing the USER did in this app (u).

One writer action is one undo unit, so `u` reverts a whole batch through the
same machinery `R` uses on the changes table — there is no second, weaker
undo path any more.

Three rules, each of which was a bug before:

- SCOPED to source='tui'. `u` is this app's undo. It must never reach an
  assistant batch (that is `R`'s job) or an unbatched machine write — pressing
  `u` on a freshly opened app used to revert a sync projection's synced_at,
  a field the user never set and cannot see.
- WHOLE ACTIONS. A stage move writes stage, closed_at, outcome and
  probability_pct together; putting back only the last of them left a dead
  deal sitting at 75% probability.
- REFUSALS ARE REPORTED. If the record changed since, the revert is refused
  and said so, rather than reported as success ("surface, don't guess").

Imports stay deliberately unbatched (their snapshot is the rollback), so `u`
after an import correctly finds nothing rather than raising.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from dataclasses import field as dc_field

from ..repo import batches as batches_repo
from . import batches as batches_svc

SOURCE = "tui"


@dataclass(frozen=True)
class UndoResult:
    ref: str
    summary: str
    applied: bool
    description: str
    refused: list[batches_svc.Conflict] = dc_field(default_factory=list)


def undo_last(conn: sqlite3.Connection, now: str | None = None) -> UndoResult | None:
    """Revert the most recent un-reverted TUI batch. None when there is
    nothing this app wrote left to undo."""
    from ..db import utc_now

    batch = batches_repo.last_undoable(conn, source=SOURCE)
    if batch is None:
        return None

    result = batches_svc.revert(conn, batch.ref, now=now or utc_now())
    if not result.applied:
        return UndoResult(
            ref=batch.ref,
            summary=batch.summary,
            applied=False,
            description=_refusal(batch.summary, result.refused),
            refused=result.refused,
        )
    return UndoResult(
        ref=batch.ref,
        summary=batch.summary,
        applied=True,
        description=f"undid {batch.summary}",
    )


def _refusal(summary: str, conflicts: list[batches_svc.Conflict]) -> str:
    """Name the fields that block it, not the internals. The user's next move
    is to look at those fields, so they have to be in the sentence."""
    fields = sorted({c.change.field for c in conflicts})
    named = ", ".join(fields[:3]) + ("…" if len(fields) > 3 else "")
    return (
        f"cannot undo {summary} — {named} changed since. "
        "Nothing was reverted."
    )
