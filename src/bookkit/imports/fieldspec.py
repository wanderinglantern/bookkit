"""One registry per import flow: keys, aliases, examples — the single source
of truth that drives BOTH header mapping (tablemap) and template export, so a
template filled in and re-imported always maps with zero fuzz."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FieldSpec:
    key: str
    required: bool
    example: str
    aliases: tuple[str, ...] = ()


BOOK_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "account", True, "Atomic Industries",
        ("insured", "client", "named insured", "account name", "company"),
    ),
    FieldSpec("industry", False, "Manufacturing", ("sector",)),
    FieldSpec("naics", False, "3345"),
    FieldSpec("domain", False, "atomic.example.com"),
    FieldSpec("website", False, "https://atomic.example.com", ("url",)),
    FieldSpec("owner", False, "grant", ("producer", "account exec")),
    FieldSpec("status", False, "prospect"),
    FieldSpec("contact_name", False, "Rosa Silva", ("contact", "primary contact")),
    FieldSpec("contact_email", False, "rosa@atomic.example.com", ("email",)),
    FieldSpec("contact_phone", False, "(312) 555-0142", ("phone",)),
    FieldSpec("contact_title", False, "Risk Manager", ("title",)),
    FieldSpec("program", False, "Property", ("program name", "line of business", "lob")),
    FieldSpec(
        "inception", False, "2026-10-01",
        ("eff date", "effective", "effective date", "inception date"),
    ),
    FieldSpec("expiry", False, "2027-10-01", ("exp date", "expiration", "expiry date")),
    FieldSpec("premium", False, "250,000", ("annual premium", "total premium")),
    FieldSpec("limit", False, "10M", ("total limit",)),
    FieldSpec("commission", False, "15%", ("comm", "commission %")),
    FieldSpec("placement_status", False, "bound"),
)


def write_template(specs: tuple[FieldSpec, ...], path: Path) -> Path:
    """Blank populate-and-reimport workbook: canonical headers (bold, required
    filled), one worked example row, frozen header. Same conventions as
    towerkit's program template."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Import"
    required_fill = PatternFill("solid", fgColor="F9E6A0")
    for col, spec in enumerate(specs, start=1):
        cell = ws.cell(row=1, column=col, value=spec.key)
        cell.font = Font(bold=True)
        if spec.required:
            cell.fill = required_fill
        ws.column_dimensions[cell.column_letter].width = max(12, len(spec.key) + 2)
    ws.append([spec.example for spec in specs])
    ws.freeze_panes = "A2"
    wb.save(path)
    return path
