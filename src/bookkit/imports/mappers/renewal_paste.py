"""Renewal updates: pasted new-term schedule diffed against the CURRENT
linked program, by layer name. Matched layers with changed money become
update records; everything else is reported, never guessed at — new layers
are built in towerkit, participant moves happen in the binding flows."""

from __future__ import annotations

import sqlite3

from towerkit.ingest import parse_tower
from towerkit.model import Layer, load_program
from towerkit.money import format_money

from ...money import dollars_to_cents
from ...repo import placements
from ...sync import program_file
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
    current = load_program(program_file(conn, placement))
    draft = parse_tower(text, insured=current.insured, program=current.program)
    records: list[StagedRecord] = []
    claimed: set[str] = set()  # existing layer ids already matched
    matched = 0
    for rownum, pasted in enumerate(draft.layers, start=1):
        existing = _match_layer(current.layers, pasted, claimed)
        if existing is None:
            record = StagedRecord(
                "layer", f"{key_base}/{pasted.name}", {}, source_row=rownum,
                action="skip",
            )
            record.warn("layer", "not in the expiring program — build it in towerkit")
            records.append(record)
            continue
        claimed.add(existing.id)
        matched += 1
        records.append(_diff_layer(key_base, existing, pasted, rownum))
    for layer in current.layers:
        if layer.id not in claimed:
            record = StagedRecord(
                "layer", f"{key_base}/{layer.name}", {}, source_row=0, action="skip"
            )
            record.warn("layer", "not in the paste — renews unchanged")
            records.append(record)
    if draft.layers and matched == 0:
        broken = StagedRecord("renewal", key_base, {}, source_row=0, action="skip")
        broken.error(
            "layers",
            "nothing in the paste matches the expiring program — committing would "
            "renew with the OLD terms and silently drop everything pasted",
        )
        records.append(broken)
    for diag in draft.diagnostics.errors:
        broken = StagedRecord("renewal", key_base, {}, source_row=0, action="skip")
        broken.error(diag.code, diag.message)
        records.append(broken)
    return StagedImport("paste", "", records, [])


def _match_layer(
    layers: list[Layer], pasted: Layer, claimed: set[str]
) -> Layer | None:
    """Pasted layers carry auto-generated band names, real files carry human
    names ('Primary GL', '1st Excess') — so match by name first, then by the
    layer's tower identity: its attachment point. Ambiguity means no match,
    never a guess."""
    wanted = pasted.name.strip().lower()
    for layer in layers:
        if layer.id not in claimed and layer.name.strip().lower() == wanted:
            return layer
    by_attach = [
        layer
        for layer in layers
        if layer.id not in claimed and layer.attach == pasted.attach
    ]
    return by_attach[0] if len(by_attach) == 1 else None


def _diff_layer(
    key_base: str, existing: Layer, pasted: Layer, rownum: int
) -> StagedRecord:
    """Always a record, even when nothing changed.

    A layer whose premium the parser missed produced NO record at all, so it
    renewed carrying the EXPIRING premium with nothing on screen — a prefill
    of the one figure that comes off the renewal schedule. Every matched layer
    now says what it will do, and every figure the paste did not supply is
    named together with the expiring amount it will carry forward."""
    changes: dict[str, object] = {}
    diff_parts: list[str] = []
    carried: list[str] = []
    for attr, cents_key in (
        ("premium", "premium_cents"),
        ("limit", "limit_cents"),
        ("attach", "attach_cents"),
    ):
        new = getattr(pasted, attr)
        old = getattr(existing, attr)
        if new is None:
            if old:  # a real expiring figure the paste did not restate
                carried.append(f"{attr} ({format_money(old)})")
            continue
        if new == old:
            continue
        changes[cents_key] = dollars_to_cents(new)
        old_text = format_money(old) if old is not None else "—"
        diff_parts.append(f"{attr}: {old_text} → {format_money(new)}")
    record = StagedRecord(
        "layer", f"{key_base}/{existing.name}",
        {"layer_name": existing.name}, source_row=rownum,
        action="update" if changes else "skip",
    )
    if changes:
        record.fields.update(changes)
        record.fields["diff"] = "; ".join(diff_parts)
    else:
        record.warn("layer", "nothing changed in the paste — renews unchanged")
    if carried:
        record.warn(
            "layer",
            "not in the paste: " + ", ".join(carried) + " — the layer renews "
            "carrying the expiring figure(s); paste them or edit the layer "
            "after renewing",
        )
    return record
