"""Initial book load: canonical rows → staged accounts, contacts, placements.

Rows group by account; the first occurrence stages the account, and any row
carrying contact or placement fields stages those records linked back via
fields["org_key"]. Every parse failure is an Issue on its record — the row
keeps going so one bad cell doesn't hide the rest of the review."""

from __future__ import annotations

import sqlite3

from ... import normalize
from ...dates import parse_human_date
from ...models import OrgStatus, PlacementStatus
from ...money import MoneyParseError, parse_money_cents, parse_share_bps
from ..matcher import match_contact, match_org, match_placement
from ..readers import RawTable
from ..staging import StagedImport, StagedRecord
from ..tablemap import Mapping, apply_mapping

_ORG_CLEANERS = {
    "industry": normalize.clean_text,
    "naics": normalize.clean_naics,
    "domain": normalize.clean_domain,
    "website": normalize.clean_url,
    "owner": normalize.clean_text,
}


def stage_book(
    conn: sqlite3.Connection, table: RawTable, mapping: Mapping
) -> StagedImport:
    rows = apply_mapping(table, mapping)
    records: list[StagedRecord] = []
    accounts: dict[str, StagedRecord] = {}  # cleaned account name → record
    org_targets: dict[str, str | None] = {}  # account key → matched org id
    for rownum, row in enumerate(rows, start=1):
        account = normalize.clean_text(str(row.get("account") or ""))
        if not account:
            broken = StagedRecord(
                "account", f"(row {rownum})", {}, source_row=rownum, action="skip"
            )
            broken.error("account", "account name is required")
            records.append(broken)
            continue
        if account not in accounts:
            record = _stage_account(conn, account, row, rownum)
            accounts[account] = record
            org_targets[account] = record.target_id
            records.append(record)
        org_id = org_targets[account]
        contact = _stage_contact(conn, account, org_id, row, rownum)
        if contact is not None:
            records.append(contact)
        placement = _stage_placement(conn, account, org_id, row, rownum)
        if placement is not None:
            records.append(placement)
    return StagedImport(table.source, table.sha256, records, mapping.unmapped)


def _stage_account(
    conn: sqlite3.Connection, account: str, row: dict[str, object], rownum: int
) -> StagedRecord:
    record = StagedRecord("account", account, {"name": account}, source_row=rownum)
    for key, cleaner in _ORG_CLEANERS.items():
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        try:
            record.fields[key] = cleaner(raw)
        except ValueError as exc:
            record.error(key, str(exc))
    status = str(row.get("status") or "").strip().lower()
    if status:
        try:
            record.fields["status"] = OrgStatus(status).value
        except ValueError:
            record.error(
                "status",
                f"{status!r} is not one of {[s.value for s in OrgStatus]}",
            )
    target, candidates = match_org(conn, account)
    if target is not None:
        record.action, record.target_id = "update", target
    elif candidates:
        record.error(
            "account", f"ambiguous match — could be any of {candidates!r}; pick one"
        )
    return record


def _stage_contact(
    conn: sqlite3.Connection,
    account: str,
    org_id: str | None,
    row: dict[str, object],
    rownum: int,
) -> StagedRecord | None:
    name = normalize.clean_text(str(row.get("contact_name") or ""))
    email_raw = str(row.get("contact_email") or "").strip()
    if not name and not email_raw:
        return None
    record = StagedRecord(
        "contact", f"{account}/{name or email_raw}", {"org_key": account},
        source_row=rownum,
    )
    first, _, last = name.rpartition(" ")
    if not first:  # single-token name: treat it as the first name
        first, last = last, ""
    record.fields["first_name"], record.fields["last_name"] = first or last, last if first else ""
    if not name:
        record.warn("contact_name", "no name; using email as the identity")
        record.fields["first_name"] = email_raw.partition("@")[0]
    title = normalize.clean_text(str(row.get("contact_title") or ""))
    if title:
        record.fields["title"] = title
    if email_raw:
        try:
            record.fields["email"] = normalize.clean_email(email_raw)
        except ValueError as exc:
            record.error("contact_email", str(exc))
    phone_raw = str(row.get("contact_phone") or "").strip()
    if phone_raw:
        try:
            record.fields["phone"] = normalize.clean_phone(phone_raw)
        except ValueError as exc:
            record.error("contact_phone", str(exc))
    email = record.fields.get("email")
    if org_id is not None and isinstance(email, str):
        existing = match_contact(conn, org_id, email)
        if existing is not None:
            record.action, record.target_id = "update", existing
    return record


def _stage_placement(
    conn: sqlite3.Connection,
    account: str,
    org_id: str | None,
    row: dict[str, object],
    rownum: int,
) -> StagedRecord | None:
    program = normalize.clean_text(str(row.get("program") or ""))
    has_dates = row.get("inception") or row.get("expiry")
    if not program and not has_dates:
        return None
    record = StagedRecord(
        "placement", f"{account}/{program or '?'}",
        {"org_key": account, "program_name": program}, source_row=rownum,
    )
    if not program:
        record.error("program", "placement rows need a program name")
    for key, target in (("inception", "period_from"), ("expiry", "period_to")):
        raw = str(row.get(key) or "").strip()
        if not raw:
            record.error(key, "placement rows need inception and expiry")
            continue
        parsed = parse_human_date(raw)
        if parsed is None:
            record.error(key, f"cannot read a date from {raw!r}")
        else:
            record.fields[target] = parsed.isoformat()
    period_from = record.fields.get("period_from")
    period_to = record.fields.get("period_to")
    if (
        isinstance(period_from, str)
        and isinstance(period_to, str)
        and period_to <= period_from
    ):
        record.error("expiry", f"expiry {period_to} is not after inception {period_from}")
    for key, column, parser in (
        ("premium", "total_premium", parse_money_cents),
        ("limit", "total_limit", parse_money_cents),
        ("commission", "commission_bps", parse_share_bps),
    ):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        try:
            record.fields[column] = parser(raw)
        except MoneyParseError as exc:
            record.error(key, str(exc))
    status = str(row.get("placement_status") or "").strip().lower()
    if status:
        try:
            record.fields["status"] = PlacementStatus(status).value
        except ValueError:
            record.error(
                "placement_status",
                f"{status!r} is not one of {[s.value for s in PlacementStatus]}",
            )
    if (
        org_id is not None
        and program
        and isinstance(period_from, str)
        and isinstance(period_to, str)
    ):
        existing = match_placement(conn, org_id, program, period_from, period_to)
        if existing is not None:
            record.action, record.target_id = "update", existing
    return record
