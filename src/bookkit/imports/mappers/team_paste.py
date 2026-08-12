"""Pasted colleague signature → staged team member. Same shape-based
extraction as the org-contact paste; TeamMember carries one `name` field, so
first/last fold back together. Matching by email keeps re-pastes idempotent."""

from __future__ import annotations

import sqlite3

from ...repo import team
from ..staging import StagedImport, StagedRecord
from .contact_paste import extract_signature


def stage_team_paste(conn: sqlite3.Connection, text: str) -> StagedImport:
    scratch = StagedRecord("team_member", "?", {}, source_row=1)
    extract_signature(scratch, text)
    first = str(scratch.fields.pop("first_name", "") or "")
    last = str(scratch.fields.pop("last_name", "") or "")
    name = f"{first} {last}".strip()
    record = StagedRecord(
        "team_member", name or "?",
        {k: v for k, v in scratch.fields.items() if k != "linkedin"},
        source_row=1, issues=scratch.issues,
    )
    if name:
        record.fields["name"] = name
    else:
        record.error("name", "no name found in the paste — a colleague needs one")
    email = record.fields.get("email")
    if isinstance(email, str):
        wanted = email.lower()
        for member in team.list_members(conn, active_only=False):
            if member.email is not None and member.email.lower() == wanted:
                record.action, record.target_id = "update", member.id
                break
    return StagedImport("paste", "", [record], [])
