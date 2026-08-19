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

from towerkit.atomicio import atomic_write_bytes

from ..sync import file_sha256

SNAPSHOT_KEEP = 20
_DIRNAME = ".mcp-snapshots"


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
