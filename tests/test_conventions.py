"""Architecture conventions that grep can enforce."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "bookkit"


def test_no_openpyxl_outside_imports_package():
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        if rel.parts[0] == "imports":
            continue  # readers/templates own workbook I/O
        assert "openpyxl" not in path.read_text(), f"openpyxl leaked into {rel}"


def test_no_raw_sql_in_tui_or_imports():
    for pkg in ("tui", "imports"):
        for path in (SRC / pkg).rglob("*.py"):
            assert ".execute(" not in path.read_text(), \
                f"raw SQL in {path.relative_to(SRC)} — queries live in repo/"


def test_no_raw_sql_in_mcpserver():
    text = (SRC / "mcpserver.py").read_text()
    assert ".execute(" not in text, "mcpserver must consume repo/services only"
