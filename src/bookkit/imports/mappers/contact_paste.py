"""Pasted signature/thread → staged contact + interaction.

Heuristics, not grammar: email/phone/LinkedIn are found by shape anywhere in
the text; the name is the first line that isn't contact data; the title is
the line after the name. Anything unparsed stays in the interaction body, so
nothing pasted is ever lost — worst case it's a note."""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from ... import normalize
from ..matcher import match_contact, match_contact_by_name
from ..staging import StagedImport, StagedRecord

_EMAIL_RE = re.compile(r"[^\s<>|,;]+@[^\s<>|,;]+\.[^\s<>|,;]+")
_PHONE_RE = re.compile(r"[+()\d][\d\s().+-]{6,}\d(?:\s*(?:ext\.?|x)\s*\d+)?")
_LINKEDIN_RE = re.compile(r"\S*linkedin\.com/\S+", re.IGNORECASE)
_NAMEISH_RE = re.compile(r"^[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){1,3}$")
# a digit run that is actually a date (ISO or a dd Mon-adjacent fragment) must
# never become a phone number — dates format into plausible-looking phones
_DATEISH_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")


def _plausible_phone(candidate: str) -> bool:
    if _DATEISH_RE.search(candidate):
        return False
    digits = re.sub(r"\D", "", candidate)
    return candidate.lstrip().startswith("+") or len(digits) in (10, 11)


def extract_signature(record: StagedRecord, text: str, org_name: str = "") -> None:
    """Run the shape-based extraction into `record`: email/phone/linkedin by
    pattern, first_name/last_name/title by position, warnings on the record.
    Shared by the org-contact paste and the team-colleague paste."""
    remaining_lines: list[str] = []
    for raw_line in text.splitlines():
        line = normalize.clean_text(raw_line)
        consumed = False
        for pattern, field_name, cleaner in (
            (_EMAIL_RE, "email", normalize.clean_email),
            (_LINKEDIN_RE, "linkedin", normalize.clean_linkedin),
            (_PHONE_RE, "phone", normalize.clean_phone),
        ):
            match = pattern.search(line)
            if match and field_name == "phone" and not _plausible_phone(match.group()):
                match = None
            if match and field_name not in record.fields:
                try:
                    record.fields[field_name] = cleaner(match.group())
                    consumed = True
                except ValueError as exc:
                    record.warn(field_name, str(exc))
                line = normalize.clean_text(line.replace(match.group(), " "))
        if line and not consumed:
            remaining_lines.append(line)
        elif line and consumed and _NAMEISH_RE.match(line):
            remaining_lines.append(line)  # "Rosa Silva | rosa@..." style lines
    _name_and_title(record, remaining_lines, org_name)


def stage_contact_paste(
    conn: sqlite3.Connection, text: str, org_id: str, org_name: str
) -> StagedImport:
    contact = StagedRecord("contact", f"{org_name}/?", {"org_id": org_id}, source_row=1)
    extract_signature(contact, text, org_name)
    email = contact.fields.get("email")
    existing = match_contact(conn, org_id, email) if isinstance(email, str) else None
    if existing is None and "first_name" in contact.fields:
        existing = match_contact_by_name(
            conn, org_id,
            str(contact.fields.get("first_name") or ""),
            str(contact.fields.get("last_name") or ""),
        )
    if existing is not None:
        contact.action, contact.target_id = "update", existing
    records = [contact] if _has_identity(contact) else []
    if not records:
        contact = None  # type: ignore[assignment]
    note = StagedRecord(
        "interaction",
        f"{org_name}/note",
        {
            "org_id": org_id,
            "type": "note",
            "subject": "pasted capture",
            "occurred_on": date.today().isoformat(),
            "body": text,
        },
        source_row=1,
    )
    if not records:
        note.warn("contact", "no contact details recognised — keeping the text as a note")
    records.append(note)
    return StagedImport("paste", "", records, [])


def _has_identity(contact: StagedRecord) -> bool:
    return any(k in contact.fields for k in ("email", "first_name", "phone", "linkedin"))


def _name_and_title(
    contact: StagedRecord, lines: list[str], org_name: str
) -> None:
    name_index: int | None = None
    for index, line in enumerate(lines):
        if _NAMEISH_RE.match(line) and line.lower() != org_name.lower():
            name_index = index
            break
    if name_index is None:
        if "email" in contact.fields:
            local = str(contact.fields["email"]).partition("@")[0]
            contact.fields["first_name"] = local
            contact.warn("contact_name", "no name line found; using the email's local part")
            contact.key = contact.key.replace("?", local)
        return
    first, _, last = lines[name_index].rpartition(" ")
    if not first:
        first, last = last, ""
    contact.fields["first_name"], contact.fields["last_name"] = first, last
    contact.key = contact.key.replace("?", lines[name_index])
    for line in lines[name_index + 1:]:
        if line.lower() == org_name.lower() or _EMAIL_RE.search(line):
            continue
        contact.fields["title"] = line
        break
