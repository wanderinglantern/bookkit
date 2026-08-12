"""Import pipeline pure core: fieldspec, readers, tablemap, staging, matcher,
book mapper, committer, and the bookctl template/import verbs."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from bookkit.imports.fieldspec import BOOK_FIELDS, FieldSpec, write_template
from bookkit.imports.readers import read_table
from bookkit.imports.tablemap import apply_mapping, map_headers


def test_book_fields_have_unique_keys_and_aliases() -> None:
    keys = [spec.key for spec in BOOK_FIELDS]
    assert len(keys) == len(set(keys))
    all_hints = [hint for spec in BOOK_FIELDS for hint in (spec.key, *spec.aliases)]
    assert len(all_hints) == len(set(all_hints))
    assert [spec.key for spec in BOOK_FIELDS if spec.required] == ["account"]


def test_template_round_trips_with_zero_unmapped(tmp_path: Path) -> None:
    out = write_template(BOOK_FIELDS, tmp_path / "book.xlsx")
    table = read_table(out)
    mapping = map_headers(table.headers, BOOK_FIELDS)
    assert mapping.unmapped == []
    rows = apply_mapping(table, mapping)
    assert rows[0]["account"] == "Atomic Industries"


def test_read_table_csv_and_xlsx_agree(tmp_path: Path) -> None:
    csv_path = tmp_path / "t.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Account", "Premium"])
        writer.writerow(["Atomic", "250,000"])
        writer.writerow(["", ""])  # blank row skipped
    table = read_table(csv_path)
    assert table.headers == ["Account", "Premium"]
    assert table.rows == [{"Account": "Atomic", "Premium": "250,000"}]
    assert table.source == "t.csv"
    assert len(table.sha256) == 64

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Account", "Premium"])
    wb.active.append(["Atomic", "250,000"])
    xlsx_path = tmp_path / "t.xlsx"
    wb.save(xlsx_path)
    assert read_table(xlsx_path).rows == table.rows


def test_read_table_rejects_unknown_suffix(tmp_path: Path) -> None:
    path = tmp_path / "t.pdf"
    path.write_text("nope")
    with pytest.raises(ValueError, match="pdf"):
        read_table(path)


def test_map_headers_exact_alias_fuzzy_and_garbage() -> None:
    specs = (
        FieldSpec("account", True, "A", ("insured", "client")),
        FieldSpec("inception", False, "2026-10-01", ("eff date", "effective date")),
        FieldSpec("premium", False, "1", ()),
    )
    mapping = map_headers(
        ["Insured", "Efective Date", "Annual Premium Zebra Wombat", "Premium"], specs
    )
    assert mapping.assigned["Insured"] == "account"
    assert mapping.assigned["Efective Date"] == "inception"  # fuzzy over typo
    assert mapping.assigned["Premium"] == "premium"
    assert "Annual Premium Zebra Wombat" in mapping.unmapped


def test_map_headers_first_claim_wins() -> None:
    specs = (FieldSpec("account", True, "A", ("insured",)),)
    mapping = map_headers(["Account", "Insured"], specs)
    assert mapping.assigned == {"Account": "account"}
    assert mapping.unmapped == ["Insured"]


# --- staging --------------------------------------------------------------------

from bookkit.imports.staging import (  # noqa: E402
    Issue,
    Severity,
    StagedImport,
    StagedRecord,
)


def _staged(records: list[StagedRecord], unmapped: list[str] | None = None) -> StagedImport:
    return StagedImport("book.xlsx", "ab" * 32, records, unmapped or [])


def test_staged_import_gates_on_errors() -> None:
    clean = StagedRecord("account", "Atomic", {"name": "Atomic"}, source_row=1)
    broken = StagedRecord(
        "placement", "Atomic/Property", {}, source_row=2,
        issues=[Issue(Severity.ERROR, "premium", "cannot parse 'banana'")],
    )
    assert _staged([clean]).ok
    staged = _staged([clean, broken])
    assert not staged.ok
    assert [issue.field for issue in staged.errors] == ["premium"]


def test_staged_report_shows_counts_issues_and_unmapped() -> None:
    records = [
        StagedRecord("account", "Atomic", {}, source_row=1),
        StagedRecord("account", "Borealis", {}, source_row=2, action="update", target_id="x"),
        StagedRecord(
            "contact", "Atomic/Rosa", {}, source_row=1,
            issues=[Issue(Severity.WARNING, "phone", "no digits")],
        ),
    ]
    report = _staged(records, unmapped=["Wibble Col"]).report()
    assert "account: 1 create, 1 update" in report
    assert "contact: 1 create" in report
    assert "⚠ phone: no digits" in report
    assert "Wibble Col" in report
    assert "book.xlsx" in report


# --- matcher --------------------------------------------------------------------

from bookkit.imports.matcher import (  # noqa: E402
    match_contact,
    match_org,
    match_placement,
)
from bookkit.repo import contacts as contacts_repo  # noqa: E402
from bookkit.repo import orgs as orgs_repo  # noqa: E402
from bookkit.repo import placements as placements_repo  # noqa: E402


def test_match_org_exact_fuzzy_conflict_none(conn) -> None:
    a = orgs_repo.create(conn, kind="client", name="Atomic Industries, Inc.")
    orgs_repo.create(conn, kind="client", name="Atomic Industrial Holdings")
    orgs_repo.create(conn, kind="market", name="Atomic Industries, Inc.")  # ignored: market
    exact, candidates = match_org(conn, "Atomic Industries, Inc.")
    assert exact == a.id and candidates == []
    none_id, candidates = match_org(conn, "Atomic Ind")  # too close to two clients
    assert none_id is None
    no_id, no_candidates = match_org(conn, "Zephyr Logistics")
    assert no_id is None and no_candidates == []


def test_match_contact_by_email(conn) -> None:
    org = orgs_repo.create(conn, kind="client", name="Atomic")
    rosa = contacts_repo.create(
        conn, org.id, first_name="Rosa", last_name="Silva", email="rosa@atomic.example.com"
    )
    assert match_contact(conn, org.id, "ROSA@atomic.example.com") == rosa.id
    assert match_contact(conn, org.id, "nobody@atomic.example.com") is None


# --- book mapper ------------------------------------------------------------------

from bookkit.imports.mappers.book import stage_book  # noqa: E402
from bookkit.imports.readers import RawTable  # noqa: E402
from bookkit.imports.tablemap import Mapping  # noqa: E402

_BOOK_HEADERS = [
    "account", "status", "contact_name", "contact_email",
    "program", "inception", "expiry", "premium", "commission",
]


def _book_table(rows: list[dict[str, object]]) -> tuple[RawTable, Mapping]:
    table = RawTable("book.csv", "ab" * 32, _BOOK_HEADERS, rows)
    return table, map_headers(_BOOK_HEADERS, BOOK_FIELDS)


def test_stage_book_stages_account_contact_placement(conn) -> None:
    table, mapping = _book_table([
        {
            "account": "Atomic Industries", "status": "active",
            "contact_name": "Rosa Silva", "contact_email": "Rosa@Atomic.example.com",
            "program": "Property", "inception": "10/1/2026", "expiry": "10/1/2027",
            "premium": "250,000", "commission": "15%",
        },
        {"account": "Atomic Industries", "contact_name": "Ken Ito",
         "contact_email": "ken@atomic.example.com"},
        {"account": "Borealis Foods", "status": "wibble"},
        {"account": "", "program": "Casualty"},
    ])
    staged = stage_book(conn, table, mapping)
    kinds = [(r.kind, r.action) for r in staged.records]
    assert kinds.count(("account", "create")) == 2
    assert kinds.count(("contact", "create")) == 2
    assert kinds.count(("placement", "create")) == 1

    placement = next(r for r in staged.records if r.kind == "placement")
    assert placement.fields["period_from"] == "2026-10-01"
    assert placement.fields["total_premium"] == 25_000_000  # cents
    assert placement.fields["commission_bps"] == 1500
    assert placement.fields["org_key"] == "Atomic Industries"

    contact = next(r for r in staged.records if "Rosa" in r.key)
    assert contact.fields["email"] == "Rosa@atomic.example.com"  # local part keeps case
    assert contact.fields["first_name"] == "Rosa"

    bad_status = next(r for r in staged.records if r.key == "Borealis Foods")
    assert any(i.field == "status" for i in bad_status.issues)
    assert any(i.field == "account" for r in staged.records for i in r.issues)
    assert not staged.ok


def test_stage_book_matches_existing_as_update(conn) -> None:
    org = orgs_repo.create(conn, kind="client", name="Atomic Industries")
    contacts_repo.create(
        conn, org.id, first_name="Rosa", last_name="Silva",
        email="rosa@atomic.example.com",
    )
    placements_repo.create(conn, org.id, "Property", "2026-10-01", "2027-10-01")
    table, mapping = _book_table([
        {
            "account": "Atomic Industries", "contact_name": "Rosa Silva",
            "contact_email": "rosa@atomic.example.com", "program": "Property",
            "inception": "2026-10-01", "expiry": "2027-10-01",
        },
    ])
    staged = stage_book(conn, table, mapping)
    assert staged.ok
    assert all(r.action == "update" and r.target_id for r in staged.records)


def test_stage_book_expiry_before_inception_is_error(conn) -> None:
    table, mapping = _book_table([
        {"account": "Atomic", "program": "Property",
         "inception": "2027-10-01", "expiry": "2026-10-01"},
    ])
    staged = stage_book(conn, table, mapping)
    assert any(i.field == "expiry" for r in staged.records for i in r.issues)
    assert not staged.ok


def test_match_placement_is_period_aware(conn) -> None:
    org = orgs_repo.create(conn, kind="client", name="Atomic")
    p26 = placements_repo.create(conn, org.id, "Property", "2026-10-01", "2027-10-01")
    p27 = placements_repo.create(conn, org.id, "Property", "2027-10-01", "2028-10-01")
    placements_repo.create(conn, org.id, "Casualty", "2026-10-01", "2027-10-01")
    assert match_placement(conn, org.id, "property", "2026-10-01", "2027-10-01") == p26.id
    assert match_placement(conn, org.id, "Property", "2027-12-01", "2028-12-01") == p27.id
    assert match_placement(conn, org.id, "Property", "2030-01-01", "2031-01-01") is None
