"""Backup first, one transaction, provenance in event_log.

The zero-errors gate lives here as a hard refusal — the caller (TUI screen or
CLI) shows issues long before this point; reaching it with errors is a bug.
Bulk import is exactly the bulk-write case the backup rule exists for: the
SQLite file is snapshotted before a single row changes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .. import db
from ..repo import base, contacts, interactions, orgs, placements
from .staging import StagedImport, StagedRecord

_KIND_TABLES = {"account": "org", "contact": "contact", "placement": "placement"}


@dataclass
class CommitResult:
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    backup: Path = Path()


def commit_book(
    conn: sqlite3.Connection, staged: StagedImport, db_path: Path
) -> CommitResult:
    if not staged.ok:
        raise ValueError(
            f"staged import has {len(staged.errors)} error(s); commit refused"
        )
    result = CommitResult(backup=_snapshot(conn, db_path))
    note = f"import {staged.source} sha256={staged.sha256}"
    org_ids: dict[str, str] = {}  # org_key → id, for records under new accounts
    try:
        for record in staged.records:
            if record.action == "skip":
                continue
            if record.kind == "account":
                org_ids[record.key] = _apply_account(conn, record, note, result)
        for record in staged.records:
            if record.action == "skip" or record.kind == "account":
                continue
            org_id = _org_for(record, org_ids)
            if record.kind == "contact":
                _apply_contact(conn, record, org_id, note, result)
            elif record.kind == "placement":
                _apply_placement(conn, record, org_id, note, result)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def commit_contact_paste(
    conn: sqlite3.Connection, staged: StagedImport, org_id: str, db_path: Path
) -> CommitResult:
    """Contact (create or update) + the pasted text as a note interaction."""
    if not staged.ok:
        raise ValueError(
            f"staged import has {len(staged.errors)} error(s); commit refused"
        )
    result = CommitResult(backup=_snapshot(conn, db_path))
    note = "import pasted capture"
    try:
        contact_ids: list[str] = []
        for record in staged.records:
            if record.kind == "contact":
                fields = _fields(record, "org_id")
                if record.action == "update" and record.target_id is not None:
                    contacts.update(conn, record.target_id, note=note, **fields)
                    contact_ids.append(record.target_id)
                else:
                    contact_ids.append(contacts.create(conn, org_id, **fields).id)
                _count(result, record)
        for record in staged.records:
            if record.kind == "interaction":
                interactions.log(
                    conn,
                    org_id,
                    str(record.fields["type"]),
                    str(record.fields["subject"]),
                    str(record.fields["occurred_on"]),
                    body=str(record.fields.get("body") or "") or None,
                    contact_ids=contact_ids,
                )
                _count(result, record)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def commit_program(
    conn: sqlite3.Connection,
    staged: StagedImport,
    draft: object,
    placement_id: str,
    dest: Path,
    db_path: Path,
) -> tuple[Path | None, object]:
    """Write-through order: towerkit file first (validated by to_program),
    then link + projection. Returns (path, Diagnostics)."""
    from towerkit.ingest import DraftProgram
    from towerkit.model import dump_program
    from towerkit.validate import Diagnostics, ProgramInvalidError

    from .. import sync
    from ..repo import links

    assert isinstance(draft, DraftProgram)
    diags = Diagnostics()
    if not staged.ok:
        diags.error("staged", f"{len(staged.errors)} staging error(s); commit refused")
        return None, diags
    placement = placements.get(conn, placement_id)
    if placement.program_path:
        diags.error("linked", f"{placement.ref} already has a program file")
        return None, diags
    if dest.exists():
        diags.error("exists", f"{dest} already exists — refusing to overwrite")
        return None, diags
    try:
        program = draft.to_program()
    except ProgramInvalidError as exc:
        return None, exc.diagnostics
    _snapshot(conn, db_path)  # projection writes rows; same backup rule applies
    dest.parent.mkdir(parents=True, exist_ok=True)
    dump_program(program, dest)
    links.confirm(conn, str(dest), placement.org_id, program.insured, source="import")
    project_diags = sync.project(conn, dest, placement_id=placement_id)
    base.log_event(
        conn, "placement", placement_id, "import", None, dest.name,
        f"import program from paste sha256={staged.sha256 or 'n/a'}",
    )
    conn.commit()
    return dest, project_diags


def commit_renewal(
    conn: sqlite3.Connection, staged: StagedImport, placement_id: str, db_path: Path
) -> tuple[str | None, object]:
    """sync.renew rolls the placement (clone-at-birth for linked files), then
    each staged money delta lands on the renewed file via write-through."""
    from towerkit.validate import Diagnostics

    from .. import sync

    diags = Diagnostics()
    if not staged.ok:
        diags.error("staged", f"{len(staged.errors)} staging error(s); commit refused")
        return None, diags
    _snapshot(conn, db_path)
    new_placement, _new_path, renew_diags = sync.renew(conn, placement_id)
    if new_placement is None:
        return None, renew_diags
    layer_ids = {
        str(layer["name"]).strip().lower(): str(layer["id"])
        for layer in sync.layer_details(conn, new_placement.id)
    }
    for record in staged.records:
        if record.kind != "layer" or record.action != "update":
            continue
        layer_id = layer_ids.get(str(record.fields["layer_name"]).strip().lower())
        if layer_id is None:
            renew_diags.warn(
                "layer", f"{record.fields['layer_name']!r} missing from the renewed file"
            )
            continue
        update_diags = sync.update_layer(
            conn, new_placement.id, layer_id,
            attach_cents=_maybe_int(record.fields.get("attach_cents")),
            limit_cents=_maybe_int(record.fields.get("limit_cents")),
            premium_cents=_maybe_int(record.fields.get("premium_cents")),
        )
        renew_diags.items.extend(update_diags.items)
    base.log_event(
        conn, "placement", new_placement.id, "import", None, "renewal paste",
        "import renewal terms from paste",
    )
    conn.commit()
    return new_placement.id, renew_diags


def _maybe_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _snapshot(conn: sqlite3.Connection, db_path: Path) -> Path:
    backups = db_path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = db.utc_now().replace(":", "-")
    dest, n = backups / f"{db_path.name}.{stamp}.bak", 2
    while dest.exists():  # two imports inside one second — keep both snapshots
        dest, n = backups / f"{db_path.name}.{stamp}.{n}.bak", n + 1
    return db.backup(conn, dest)


def _count(result: CommitResult, record: StagedRecord) -> None:
    bucket = result.updated if record.action == "update" else result.created
    bucket[record.kind] = bucket.get(record.kind, 0) + 1


def _provenance(
    conn: sqlite3.Connection, record: StagedRecord, entity_id: str, note: str
) -> None:
    base.log_event(
        conn, _KIND_TABLES[record.kind], entity_id, "import", None, record.key, note
    )


def _fields(record: StagedRecord, *drop: str) -> dict[str, object]:
    return {k: v for k, v in record.fields.items() if k not in ("org_key", *drop)}


def _org_for(record: StagedRecord, org_ids: dict[str, str]) -> str:
    org_key = str(record.fields.get("org_key") or "")
    org_id = org_ids.get(org_key)
    if org_id is None:
        raise ValueError(f"{record.kind} {record.key!r} has no committed account")
    return org_id


def _apply_account(
    conn: sqlite3.Connection, record: StagedRecord, note: str, result: CommitResult
) -> str:
    if record.action == "update" and record.target_id is not None:
        orgs.update(conn, record.target_id, note=note, **_fields(record))
        org_id = record.target_id
    else:
        org_id = orgs.create(conn, kind="client", **_fields(record)).id
        _provenance(conn, record, org_id, note)
    _count(result, record)
    return org_id


def _apply_contact(
    conn: sqlite3.Connection,
    record: StagedRecord,
    org_id: str,
    note: str,
    result: CommitResult,
) -> None:
    if record.action == "update" and record.target_id is not None:
        contacts.update(conn, record.target_id, note=note, **_fields(record))
    else:
        contact = contacts.create(conn, org_id, **_fields(record))
        _provenance(conn, record, contact.id, note)
    _count(result, record)


def _apply_placement(
    conn: sqlite3.Connection,
    record: StagedRecord,
    org_id: str,
    note: str,
    result: CommitResult,
) -> None:
    if record.action == "update" and record.target_id is not None:
        placements.update(
            conn, record.target_id, note=note,
            **_fields(record, "program_name", "period_from", "period_to"),
        )
    else:
        placement = placements.create(
            conn, org_id,
            str(record.fields["program_name"]),
            str(record.fields["period_from"]),
            str(record.fields["period_to"]),
            **_fields(record, "program_name", "period_from", "period_to"),
        )
        _provenance(conn, record, placement.id, note)
    _count(result, record)
