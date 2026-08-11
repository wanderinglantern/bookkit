"""Merge duplicate records into one.

Placements: the target survives; the source's submissions, tasks, and
documents move to it, the file link carries over when only the source has
one, and the source is soft-deleted (recoverable with undo). Two file-backed
placements never merge — that would be two sources of truth, which is the
situation §5 exists to prevent.

Markets: the duplicate's contacts, appetite, submissions, interactions,
documents, and aliases move to the survivor, and the duplicate's NAME becomes
an alias — so tower spellings that created the duplicate keep resolving."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..models import Org, Placement
from ..repo import (
    aliases,
    base,
    contacts,
    documents,
    interactions,
    orgs,
    placements,
    projection,
    submissions,
    tasks,
)


class MergeError(ValueError):
    pass


@dataclass(frozen=True)
class MergeResult:
    target: Placement
    moved_submissions: int
    moved_tasks: int
    moved_documents: int
    carried_link: bool


def merge_placements(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> MergeResult:
    if source_id == target_id:
        raise MergeError("a placement cannot merge into itself")
    source = placements.get(conn, source_id)
    target = placements.get(conn, target_id)
    if source.org_id != target.org_id:
        raise MergeError(f"{source.ref} and {target.ref} belong to different accounts")
    if source.program_path and target.program_path:
        raise MergeError(
            f"both {source.ref} and {target.ref} are file-backed — two sources of "
            "truth cannot merge; unlink one first"
        )

    moved_subs = submissions.reassign_placement(conn, source.id, target.id)
    moved_tasks = tasks.reassign_placement(conn, source.id, target.id)
    moved_docs = documents.reassign_placement(conn, source.id, target.id)

    carried_link = False
    if source.program_path and not target.program_path:
        projection.reassign(conn, source.id, target.id)
        placements.update(
            conn,
            target.id,
            note=f"link carried from {source.ref} on merge",
            program_path=source.program_path,
            source_sha256=source.source_sha256,
            synced_at=source.synced_at,
        )
        placements.update(
            conn, source.id, note="link moved on merge",
            program_path=None, source_sha256=None, synced_at=None,
        )
        # program_link is path→org and both placements share the org: unchanged.
        carried_link = True

    base.log_event(
        conn, "placement", target.id, "merged_from", None, source.ref,
        note=f"{moved_subs} submissions, {moved_tasks} tasks, {moved_docs} documents",
    )
    base.soft_delete(conn, "placement", source.id, note=f"merged into {target.ref}")
    return MergeResult(
        placements.get(conn, target.id), moved_subs, moved_tasks, moved_docs, carried_link
    )


@dataclass(frozen=True)
class MarketMergeResult:
    target: Org
    moved_contacts: int
    moved_appetite: int
    moved_submissions: int
    alias_added: str


def merge_markets(conn: sqlite3.Connection, source_id: str, target_id: str) -> MarketMergeResult:
    """Fold a duplicate market org (e.g. 'Axa XL' created from a tower
    spelling) into the real one ('AXA XL'). The duplicate's name becomes an
    alias of the survivor so every tower keeps resolving."""
    if source_id == target_id:
        raise MergeError("a market cannot merge into itself")
    source = orgs.get(conn, source_id)
    target = orgs.get(conn, target_id)
    if source.kind != "market" or target.kind != "market":
        raise MergeError("market merge is for market orgs only")

    moved_contacts = contacts.reassign_org(conn, source.id, target.id)
    moved_appetite = orgs.reassign_appetite(conn, source.id, target.id)
    moved_subs = submissions.reassign_market(conn, source.id, target.id)
    interactions.reassign_org(conn, source.id, target.id)
    documents.reassign_org(conn, source.id, target.id)
    aliases.reassign_market(conn, source.id, target.id)
    aliases.set_alias(conn, source.name, target.id)

    profile = orgs.get_market_profile(conn, source.id)
    if profile and orgs.get_market_profile(conn, target.id) is None:
        fields = {
            k: v
            for k, v in profile.model_dump().items()
            if k != "org_id" and v is not None
        }
        if fields:
            orgs.set_market_profile(conn, target.id, **fields)

    base.log_event(
        conn, "org", target.id, "merged_from", None, source.name,
        note=f"{moved_contacts} contacts, {moved_appetite} appetite, {moved_subs} submissions",
    )
    base.soft_delete(conn, "org", source.id, note=f"merged into {target.name}")
    return MarketMergeResult(
        orgs.get(conn, target.id), moved_contacts, moved_appetite, moved_subs, source.name
    )
