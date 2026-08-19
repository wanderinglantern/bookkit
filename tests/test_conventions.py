"""Architecture conventions that grep can enforce."""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "bookkit"

# THREE ways to run SQL through sqlite3, not one. The rule was spelled as the
# literal ".execute(" — which is a substring of NEITHER ".executemany(" nor
# ".executescript(" — so a bulk write moved into services/, tui/, imports/,
# web/ or mcpserver.py via executemany left every convention test green
# (2026-08-18). db.py already uses executescript, so the idiom is live here.
_RAW_SQL = re.compile(r"\.execute(?:many|script)?\s*\(")


def _raw_sql_in(path: Path) -> list[str]:
    rel = path.relative_to(SRC)
    return [
        f"{rel}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if _RAW_SQL.search(line)
    ]


def test_the_raw_sql_predicate_catches_all_three_spellings():
    """The rule above is only as good as its pattern, and the pattern is the
    thing that was wrong. Pin it directly."""
    for spelling in ("conn.execute(", "conn.executemany(", "conn.executescript("):
        assert _RAW_SQL.search(spelling), f"predicate misses {spelling}"
    assert not _RAW_SQL.search("self.execute_plan()")
    # and it must actually fire somewhere real, or the tests below are vacuous
    assert _raw_sql_in(SRC / "repo" / "base.py"), "predicate matches nothing in repo/"


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
            assert _raw_sql_in(path) == [], \
                f"raw SQL in {path.relative_to(SRC)} — queries live in repo/"


def test_no_raw_sql_in_mcpserver():
    assert _raw_sql_in(SRC / "mcpserver.py") == [], \
        "mcpserver must consume repo/services only"


def test_no_raw_sql_in_web():
    for path in (SRC / "web").rglob("*.py"):
        assert _raw_sql_in(path) == [], \
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


# --- no suspension point inside a write transaction -------------------------

_TX_OPENERS = {"transaction", "open_batch", "_open_batch"}


def _opens_a_transaction(item: ast.withitem) -> bool:
    call = item.context_expr
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in _TX_OPENERS


def test_no_await_inside_a_transaction():
    """NEVER await inside db.transaction / open_batch. This is what makes the
    web layer's async write routes safe.

    The eight `async def` routes in web/routes/{relationship,work}.py run on
    the event loop, not in a worker thread, so under uvicorn they all share
    ONE connection — the loop thread's. They do not corrupt each other only
    because asyncio never preempts: with no suspension point between BEGIN and
    COMMIT, two write coroutines can never interleave statements on it. The
    first `await` inside a transaction breaks that argument silently and
    reproduces, on the write path, exactly the damage
    web.app.ThreadConnections was written to stop on the read path.

    Nothing else asserts it: sharing is invisible under TestClient, which runs
    the loop on a portal thread of its own.
    """
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            if not any(_opens_a_transaction(i) for i in node.items):
                continue
            for inner in ast.walk(node):
                if inner is node:
                    continue
                if isinstance(inner, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
                    offenders.append(f"{path.relative_to(SRC)}:{inner.lineno}")
    assert offenders == [], (
        "a coroutine can suspend inside a write transaction, which lets two "
        "async routes interleave statements on the shared event-loop "
        "connection — read the value BEFORE the transaction opens and pass it "
        f"in: {offenders}"
    )
