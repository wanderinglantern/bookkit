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
