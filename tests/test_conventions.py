"""Architecture conventions that grep can enforce."""

import ast
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


# --- the towerkit boundary ---------------------------------------------------


def test_sync_delegates_program_structure_to_towerkit():
    """towerkit.edit is the ONE definition of a structural mutation and of the
    id rule that names what it creates. towerkit's own
    tests/test_conventions.py bans reaching past it — but it scans
    `src/towerkit/tui` only, so the rule was enforced on the surface that
    obeyed it and INVISIBLE on bookkit, which did not. This is that test,
    pointed at the surface it was missing.

    Two things had already drifted before the guard existed: a private `_slug`
    that considered only LAYER ids taken (so a layer could take a LINE's id,
    and nothing validates that), and no `heal_follows` at all (so bookkit
    refused program edits towerkit accepts).

    Participants are deliberately NOT on this list: towerkit.edit has no
    participant API, because towerkit's own surfaces never bind markets onto a
    layer — that is bookkit's half of the boundary. If towerkit ever grows
    `edit.add_participant`, add `.participants.append(` here and delegate.
    """
    banned = (
        ".layers.append(", ".layers.pop(", ".layers.remove(",
        ".lines.append(", ".lines.pop(", ".lines.remove(",
        ".retentions.append(", ".retentions.pop(", ".retentions.remove(",
        ".sublimits.append(", ".sublimits.pop(", ".sublimits.remove(",
        "program.layers =", "program.lines =",
        "program.retentions =", "program.sublimits =",
    )
    text = (SRC / "sync.py").read_text()
    offenders = [
        f"sync.py:{n}: {pattern}"
        for n, line in enumerate(text.splitlines(), start=1)
        if not line.lstrip().startswith("#")
        for pattern in banned
        if pattern in line
    ]
    assert offenders == [], (
        "structural mutation of a Program belongs in towerkit.edit, not "
        f"bookkit's write-through: {offenders}"
    )


def test_sync_does_not_reimplement_towerkits_id_rule():
    """`_slug` was a copy of `towerkit.edit.unique_id` that had already drifted
    — it excluded layer ids only, so a layer id could collide with a line id."""
    text = (SRC / "sync.py").read_text()
    assert "def _slug(" not in text, (
        "sync._slug was a drifted copy of towerkit.edit.unique_id — call "
        "towerkit's function, do not grow a second one"
    )
    assert "from towerkit.edit import" in text, (
        "sync.py must import towerkit.edit rather than reimplement it"
    )


def test_client_task_counts_go_through_one_rule():
    """`open_tasks(org_id=...)` DROPS a placement-attached task whose org_id is
    NULL (legal — see repo/tasks.open_tasks_for_client). Every client-scoped
    count must use open_tasks_for_client, or the navigator's account card, the
    navigator's tree, the account screen and the web app disagree about how
    many open tasks an account has. They did, in three places.

    `open_tasks(due_by=...)` is untouched — that is the book-wide due list, and
    it is org-agnostic by design. The ban is on the org_id KEYWORD, matched by
    AST rather than by grep so a comment explaining the rule cannot trip it.
    """
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.relative_to(SRC).parts[0] == "repo":
            continue  # repo/tasks.py defines both; that is where they live
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "open_tasks":
                continue
            if any(kw.arg == "org_id" for kw in node.keywords):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == [], (
        "a client-scoped task count must call open_tasks_for_client — "
        f"open_tasks(org_id=) drops placement-attached tasks: {offenders}"
    )
