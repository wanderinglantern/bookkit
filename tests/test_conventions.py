"""Architecture conventions that grep can enforce."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "bookkit"


def test_no_openpyxl_outside_imports_package():
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        if rel.parts[0] == "imports":
            continue  # readers/templates own workbook I/O
        assert "openpyxl" not in path.read_text(), f"openpyxl leaked into {rel}"


def test_no_raw_sql_in_tui_imports_or_services():
    # services joined the list 2026-08-13: the batch-revert engine was the
    # first service to grow its own .execute(), via a dead-or-alive read the
    # repo didn't offer — repo.base.raw_row now does, so the rule holds.
    for pkg in ("tui", "imports", "services"):
        for path in (SRC / pkg).rglob("*.py"):
            assert ".execute(" not in path.read_text(), \
                f"raw SQL in {path.relative_to(SRC)} — queries live in repo/"


def test_no_raw_sql_in_mcpserver():
    text = (SRC / "mcpserver.py").read_text()
    assert ".execute(" not in text, "mcpserver must consume repo/services only"


def test_no_raw_sql_in_web():
    for path in (SRC / "web").rglob("*.py"):
        assert ".execute(" not in path.read_text(), \
            f"raw SQL in {path.relative_to(SRC)} — queries live in repo/"


def test_web_and_tui_never_import_each_other():
    """Shared code lives in bookkit.forms or it is not shared. A helper copied
    across the boundary is how the surfaces drift."""
    for path in (SRC / "web").rglob("*.py"):
        assert "bookkit.tui" not in path.read_text() and "from ..tui" not in path.read_text(), \
            f"{path.relative_to(SRC)} imports the TUI"
    for path in (SRC / "tui").rglob("*.py"):
        assert "bookkit.web" not in path.read_text() and "from ..web" not in path.read_text(), \
            f"{path.relative_to(SRC)} imports the web layer"


def test_mcpserver_never_imports_the_tui():
    """The MCP server is headless. It imported split_items from tui/widgets for
    a long time because nothing asserted otherwise."""
    text = (SRC / "mcpserver.py").read_text()
    for bad in ("from .tui", "from bookkit.tui", "import bookkit.tui"):
        assert bad not in text, f"mcpserver imports the TUI ({bad})"
