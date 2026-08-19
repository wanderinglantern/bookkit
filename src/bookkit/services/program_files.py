"""Snapshot-based revert for MCP program-file writes.

File contents are not event_log rows, so batch undo cannot restore a
program file — pretending otherwise would be false safety. Instead, every
MCP write captures the file's pre-image keyed by its batch ref, and
program_revert_file restores it ONLY while the file still holds exactly
what that write produced (post-write sha match). Anything newer — the TUI,
towerkit's editor, a later MCP write — makes the pre-image stale and the
revert refuses, per the house 'surface, don't guess' rule.

Snapshots are additive files in `<program dir>/.mcp-snapshots/`; nothing
existing is rewritten. The last SNAPSHOT_KEEP per directory are retained."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from towerkit.atomicio import atomic_write_bytes

from ..sync import file_sha256

SNAPSHOT_KEEP = 20
_DIRNAME = ".mcp-snapshots"
# The directory name is historical: it was the MCP server's alone until the
# web became the second writer (2026-08-19). Renaming it would strand every
# snapshot already on Grant's disk, which is the opposite of what a revert
# store is for, so the name stays and this comment explains it.


class ProgramWriteRefused(ValueError):
    """A program write towerkit's validator refused — NOTHING was written.

    Carries the Diagnostics rather than a flattened string, because the web
    has to tell apart two refusals the MCP server does not:

    - an ordinary validation refusal (an over-signed layer, sub-dollar money,
      a layer id that no longer exists) — re-render the field with the message
      and the typed value, exactly like every other web form refusal;
    - a CONFLICT (`code == "conflict"`) — the file moved under this write, and
      the answer is a three-way choice, not an error message.

    Being a ValueError is what makes the first case free: it propagates out of
    open_batch, the transaction rolls back, and the existing refusal path
    renders it with no new code.
    """

    def __init__(self, diags: Any) -> None:
        self.diags = diags
        super().__init__("; ".join(d.message for d in diags.errors))


def _one_error(code: str, message: str) -> Any:
    """A one-error Diagnostics, so a refusal raised here carries the same shape
    towerkit's own refusals do and every caller reads it the same way."""
    from towerkit.validate import Diagnostics

    diags = Diagnostics()
    diags.error(code, message)
    return diags


def raise_on_errors(diags: Any) -> list[str]:
    """write_through's Diagnostics → a caller's contract: errors refuse
    (nothing was written), warnings ride along in the return.

    Warnings must NOT refuse: an unplaced layer is a warning, and a tower is
    routinely half-built. Refusing on one would make the normal working state
    of a placement unsaveable.
    """
    if not diags.ok:
        raise ProgramWriteRefused(diags)
    return [d.message for d in diags.warnings]


def write(
    conn: Any,
    placement: Any,
    *,
    tool: str,
    summary: str,
    mutate: Any,
    open_batch: Any,
) -> tuple[Any, list[str]]:
    """One batched program-file write: capture the pre-image, run the sync.*
    writer, snapshot on success.

    THE ONE SEAM, TWO CALLERS — the MCP server and the web. A route that calls
    sync.* directly writes outside a batch and leaves no pre-image, which is
    exactly what makes a program write unrevertible; that is why this is a
    service and not a helper inside mcpserver.py.

    `open_batch` is passed in rather than imported so this module does not
    depend on either surface: each caller supplies its own source stamp
    ('mcp' or 'web'), and the tool names stay identical across the two so the
    changes list reads uniformly whichever surface made the edit.

    The pre-image is read BEFORE the batch opens and captured only AFTER the
    write succeeds, so a refused write leaves no snapshot debris. Both halves
    of that order are load-bearing.

    sync._mutate already folds WriteConflict into the diagnostics with
    code='conflict', so a conflict arrives here as an ordinary refusal and the
    caller decides how much of a fuss to make about it.
    """
    if not placement.program_path:
        # Checked HERE, before the read. Path(str(None)) is the literal path
        # "None", so the first thing a broker saw when adding a layer to an
        # unlinked placement was "[Errno 2] No such file or directory: 'None'"
        # — an errno for a state the app knows about perfectly well (found by
        # review, 2026-08-19). sync.write_through has its own guard, but this
        # read beats it.
        raise ProgramWriteRefused(
            _one_error(
                "no-file",
                f"{placement.ref} has no program file linked — scaffold one first",
            )
        )
    path = Path(str(placement.program_path))
    pre_image = path.read_bytes()
    with open_batch(
        conn, tool=tool, org_id=placement.org_id, summary=summary,
    ) as batch:
        diags = mutate()
        warnings = raise_on_errors(diags)  # raising rolls the batch back
        capture(path, batch.ref, pre_image)
    return batch, warnings


def _snapdir(program_path: Path) -> Path:
    return program_path.parent / _DIRNAME


def capture(program_path: Path, batch_ref: str, pre_image: bytes) -> None:
    """Record the pre-image and the post-write sha for one batched write.
    Called AFTER a successful write — `pre_image` was read before it — so a
    refused write leaves no snapshot debris."""
    snapdir = _snapdir(program_path)
    snapdir.mkdir(exist_ok=True)
    (snapdir / f"{batch_ref}.json").write_bytes(pre_image)
    (snapdir / f"{batch_ref}.meta.json").write_text(json.dumps({
        "path": str(program_path),
        "post_sha256": file_sha256(program_path),
    }))
    _prune(snapdir)


def restore(program_path: Path, batch_ref: str) -> None:
    """Put the pre-image back, only if the file still holds exactly what the
    batch wrote. Raises ValueError otherwise; the caller re-projects."""
    snapdir = _snapdir(program_path)
    image = snapdir / f"{batch_ref}.json"
    meta_file = snapdir / f"{batch_ref}.meta.json"
    if not image.exists() or not meta_file.exists():
        raise ValueError(
            f"no snapshot for {batch_ref} — it may have been pruned "
            f"(the last {SNAPSHOT_KEEP} writes are kept)"
        )
    meta = json.loads(meta_file.read_text())
    if str(program_path) != meta["path"]:
        raise ValueError(f"{batch_ref} was a write to {meta['path']}, not this file")
    if file_sha256(program_path) != meta["post_sha256"]:
        raise ValueError(
            f"the file has changed since {batch_ref} wrote it — a newer edit "
            f"(TUI, towerkit, or a later batch) would be lost; revert newer "
            f"changes first or fix it in towerkit"
        )
    # ATOMIC, not shutil.copyfile. copyfile truncates the destination before
    # the first byte lands, so a crash, a full disk or a dropped mount between
    # those two moments destroyed the very file this function exists to
    # protect — and left the pre-image in .mcp-snapshots with nothing to say
    # which of the two was the real one. towerkit routes every program write
    # through atomicio (same-directory temp, fsync, os.replace) for exactly
    # this reason, and its JSON is the declared source of truth for program
    # structure; the undo path had no business being the one write that was
    # not durable (2026-08-18).
    atomic_write_bytes(program_path, image.read_bytes())


def _prune(snapdir: Path) -> None:
    """Oldest first by mtime; keep SNAPSHOT_KEEP pre-image/meta pairs."""
    images = sorted(
        (p for p in snapdir.glob("MCP-*.json") if not p.name.endswith(".meta.json")),
        key=lambda p: p.stat().st_mtime,
    )
    for stale in images[:-SNAPSHOT_KEEP]:
        stale.unlink(missing_ok=True)
        (snapdir / f"{stale.stem}.meta.json").unlink(missing_ok=True)
