"""Program-from-schedule: pasted tower text (or canonical rows) → a towerkit
DraftProgram plus a staged review of what it means for the book.

The seam rule in action: towerkit.ingest decides what the tower MEANS;
this module supplies the book context (insured, program, period) and checks
each carrier against the market aliases. Unknown carriers are warnings —
after projection they surface in the existing carrier-suggestions flow."""

from __future__ import annotations

import sqlite3
from datetime import date

from towerkit.ingest import DraftProgram, parse_tower, program_from_rows
from towerkit.model import Period as TkPeriod

from ...repo import aliases
from ..staging import StagedImport, StagedRecord


def stage_program(
    conn: sqlite3.Connection,
    source: str | list[dict[str, object]],
    org_name: str,
    program_name: str,
    period_from: str,
    period_to: str,
) -> tuple[StagedImport, DraftProgram]:
    if isinstance(source, str):
        draft = parse_tower(source, insured=org_name, program=program_name)
    else:
        draft = program_from_rows(source, insured=org_name, program=program_name)
    borrowed_period = draft.period is None
    if borrowed_period:
        draft.period = TkPeriod(
            start=date.fromisoformat(period_from), end=date.fromisoformat(period_to)
        )
    program_record = StagedRecord(
        "program",
        f"{org_name}/{program_name}",
        {
            "layers": len(draft.layers),
            "period": f"{period_from} → {period_to}",
        },
        source_row=1,
    )
    if borrowed_period:
        # a period is a date off the schedule; taking the placement's silently
        # is exactly the prefill nobody checks
        program_record.warn(
            "period",
            f"the paste names no period — using the placement's "
            f"{period_from} → {period_to}. If the schedule says otherwise, "
            "paste the dates or fix the period in the program file after import",
        )
    for diag in draft.diagnostics.errors:
        program_record.error(diag.code, diag.message)
    for diag in draft.diagnostics.warnings:
        program_record.warn(diag.code, diag.message)
    records = [program_record]
    seen: set[str] = set()
    for layer in draft.layers:
        for participant in layer.participants:
            if participant.carrier in seen:
                continue
            seen.add(participant.carrier)
            carrier = StagedRecord(
                "carrier", participant.carrier, {}, source_row=1, action="skip"
            )
            market_id = aliases.resolve(conn, participant.carrier)
            if market_id is not None:
                carrier.action, carrier.target_id = "update", market_id
            else:
                carrier.warn(
                    "carrier",
                    f"{participant.carrier!r} is unknown — it will surface in "
                    "carrier suggestions after projection",
                )
            records.append(carrier)
    return StagedImport("paste", "", records, []), draft
