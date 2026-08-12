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
