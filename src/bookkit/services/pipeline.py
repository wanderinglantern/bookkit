"""Opportunity stage transitions and pipeline metrics.

Every stage move goes through move_stage so the event_log records it — that's
what makes stage-duration and conversion metrics computable with no extra
bookkeeping.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..db import utc_now
from ..models import EventLogEntry, Opportunity
from ..money import weighted_cents
from ..repo import base, events, opportunities

STAGES = ("identified", "qualified", "submitted", "quoted", "presented", "won", "lost")
OPEN_STAGES = STAGES[:5]
CLOSED = ("won", "lost")


class StageError(ValueError):
    pass


def allowed_next(stage: str) -> tuple[str, ...]:
    """Forward one step, or close (won/lost) from any open stage. Deals die
    anywhere; they only *advance* one gate at a time."""
    if stage in CLOSED:
        return ()
    idx = OPEN_STAGES.index(stage)
    forward = (OPEN_STAGES[idx + 1],) if idx + 1 < len(OPEN_STAGES) else ()
    return (*forward, "won", "lost")


def move_stage(
    conn: sqlite3.Connection,
    opp_id: str,
    to_stage: str,
    note: str | None = None,
    loss_reason: str | None = None,
) -> Opportunity:
    opp = opportunities.get(conn, opp_id)
    if to_stage not in allowed_next(opp.stage):
        raise StageError(f"cannot move {opp.ref} from {opp.stage!r} to {to_stage!r}")
    changes: dict[str, object] = {"stage": to_stage}
    if to_stage in CLOSED:
        changes["closed_at"] = utc_now()
        changes["outcome"] = "won" if to_stage == "won" else "lost"
        if loss_reason:
            changes["loss_reason"] = loss_reason
        if to_stage == "won":
            changes["probability_pct"] = 100
        else:
            changes["probability_pct"] = 0
    base.update(conn, "opportunity", opp_id, changes, note)
    return opportunities.get(conn, opp_id)


@dataclass(frozen=True)
class StageMetrics:
    stage: str
    count: int
    total_cents: int
    weighted_cents: int
    avg_days_in_stage: float | None  # from event_log; None with no data


def metrics(conn: sqlite3.Connection) -> list[StageMetrics]:
    out: list[StageMetrics] = []
    for stage in STAGES:
        opps = opportunities.by_stage(conn, stage)
        total = sum(o.target_premium or 0 for o in opps)
        weighted = sum(
            weighted_cents(o.target_premium or 0, o.probability_pct) for o in opps
        )
        out.append(
            StageMetrics(stage, len(opps), total, weighted, _avg_days_in_stage(conn, stage))
        )
    return out


def _avg_days_in_stage(conn: sqlite3.Connection, stage: str) -> float | None:
    """Average days between entering `stage` and leaving it, from event_log."""
    rows = events.stage_durations(conn, stage)
    if not rows:
        return None
    total = 0.0
    for row in rows:
        delta = datetime.fromisoformat(row["left_at"]) - datetime.fromisoformat(row["entered_at"])
        total += delta.total_seconds() / 86_400
    return total / len(rows)


def conversion(conn: sqlite3.Connection) -> dict[str, float]:
    """Won ÷ closed, plus per-gate advance rates from event_log."""
    won = len(opportunities.by_stage(conn, "won"))
    lost = len(opportunities.by_stage(conn, "lost"))
    out = {"win_rate": won / (won + lost) if won + lost else 0.0}
    for stage in OPEN_STAGES[:-1]:
        entered = events.stage_entry_count(conn, stage)
        advanced = events.stage_entry_count(conn, OPEN_STAGES[OPEN_STAGES.index(stage) + 1])
        out[f"{stage}_advance"] = advanced / entered if entered else 0.0
    return out


def stage_history(conn: sqlite3.Connection, opp_id: str) -> list[EventLogEntry]:
    return events.field_history(conn, "opportunity", opp_id, "stage")
