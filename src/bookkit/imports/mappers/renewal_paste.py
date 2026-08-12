"""Renewal updates: pasted new-term schedule diffed against the CURRENT
linked program, by layer name. Matched layers with changed money become
update records; everything else is reported, never guessed at — new layers
are built in towerkit, participant moves happen in the binding flows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from towerkit.ingest import parse_tower
from towerkit.model import Layer, load_program
from towerkit.money import format_money

from ...money import dollars_to_cents
from ...repo import placements
from ..staging import StagedImport, StagedRecord


def stage_renewal(
    conn: sqlite3.Connection, placement_id: str, text: str
) -> StagedImport:
    placement = placements.get(conn, placement_id)
    key_base = placement.program_name
    if not placement.program_path:
        broken = StagedRecord("renewal", key_base, {}, source_row=1, action="skip")
        broken.error(
            "placement",
            f"{placement.ref} has no linked program file — renewal diffs need one",
        )
        return StagedImport("paste", "", [broken], [])
    current = load_program(Path(placement.program_path))
    draft = parse_tower(text, insured=current.insured, program=current.program)
    records: list[StagedRecord] = []
    by_name = {layer.name.strip().lower(): layer for layer in current.layers}
    seen: set[str] = set()
    for rownum, pasted in enumerate(draft.layers, start=1):
        name = pasted.name.strip().lower()
        existing = by_name.get(name)
        if existing is None:
            record = StagedRecord(
                "layer", f"{key_base}/{pasted.name}", {}, source_row=rownum,
                action="skip",
            )
            record.warn("layer", "not in the expiring program — build it in towerkit")
            records.append(record)
            continue
        seen.add(name)
        diffed = _diff_layer(key_base, existing, pasted, rownum)
        if diffed is not None:
            records.append(diffed)
    for layer in current.layers:
        if layer.name.strip().lower() not in seen:
            record = StagedRecord(
                "layer", f"{key_base}/{layer.name}", {}, source_row=0, action="skip"
            )
            record.warn("layer", "not in the paste — renews unchanged")
            records.append(record)
    for diag in draft.diagnostics.errors:
        broken = StagedRecord("renewal", key_base, {}, source_row=0, action="skip")
        broken.error(diag.code, diag.message)
        records.append(broken)
    return StagedImport("paste", "", records, [])


def _diff_layer(
    key_base: str, existing: Layer, pasted: Layer, rownum: int
) -> StagedRecord | None:
    changes: dict[str, object] = {}
    diff_parts: list[str] = []
    for attr, cents_key in (
        ("premium", "premium_cents"),
        ("limit", "limit_cents"),
        ("attach", "attach_cents"),
    ):
        new = getattr(pasted, attr)
        old = getattr(existing, attr)
        if new is None or new == old:
            continue
        changes[cents_key] = dollars_to_cents(new)
        old_text = format_money(old) if old is not None else "—"
        diff_parts.append(f"{attr}: {old_text} → {format_money(new)}")
    if not changes:
        return None
    record = StagedRecord(
        "layer", f"{key_base}/{existing.name}",
        {**changes, "layer_name": existing.name, "diff": "; ".join(diff_parts)},
        source_row=rownum, action="update",
    )
    return record
