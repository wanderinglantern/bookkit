"""Repair placement↔file links after a towerkit tree moves.

`bookctl relink` is the writer that `programpath.resolve` deliberately is not.
Resolution happens on every read and must never rewrite a row: a read path that
quietly migrates data turns every render into a migration, and a wrong guess
would then be permanent. So reads recover in memory and say so; this repairs on
purpose, reports what it would do first, and refuses whatever it cannot decide.

Three kinds of broken link, and they need different evidence:

  MOVED    The file is under a program root at the same tail. Repaired.
           This is what a re-homed checkout looks like, and it is the case
           that cost Grant five programs on 2026-08-20.

  RENAMED  Nothing matches the path, but a file under the roots has the
           placement's recorded `source_sha256` — byte-identical content is
           the only evidence strong enough to re-point a link on its own, and
           it is the same rule `sync._detect_rename` already applies.

  LOST     Neither. Reported by name and left alone. A placement pointing at
           a file that no longer exists is a fact worth seeing; inventing a
           link to whatever else is lying around is how a client's tower ends
           up attached to the wrong account.

AMBIGUOUS is a fourth outcome and always a refusal: two roots holding the same
tail, or two candidate files with the same content hash. `bookctl relink` names
them and stops.

The repair also rewrites paths that resolve fine but are stored ABSOLUTE, into
the root-relative form — that is the change that stops the next move from
breaking anything, and it is why `--dry-run` reports rows that are not broken.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import programpath
from ..db import transaction
from ..models import Placement
from ..repo import links, placements


@dataclass(frozen=True)
class Finding:
    """One placement's link, and what relink would do about it."""

    placement: Placement
    verdict: str  # "ok" | "restate" | "moved" | "renamed" | "lost" | "ambiguous"
    stored: str
    resolved: Path | None = None
    detail: str = ""

    @property
    def repairable(self) -> bool:
        return self.verdict in {"restate", "moved", "renamed"}

    def render(self) -> str:
        head = f"{self.placement.ref}  {self.placement.program_name}"
        if self.verdict == "ok":
            return f"  ✓ {head}\n      {self.stored}"
        mark = {"restate": "→", "moved": "↺", "renamed": "↺", "lost": "✗", "ambiguous": "?"}
        return (
            f"  {mark[self.verdict]} {head}\n"
            f"      stored: {self.stored}\n"
            f"      {self.detail}"
        )


def inspect(conn: sqlite3.Connection) -> list[Finding]:
    """Every linked placement, and the state of its link. Writes nothing."""
    roots = programpath.roots(conn)
    findings: list[Finding] = []
    for placement in sorted(placements.all_linked(conn), key=lambda p: p.ref):
        stored = str(placement.program_path)
        where = programpath.resolve(conn, stored)
        if where.path is not None and where.moved_from is None:
            canonical = programpath.store(conn, where.path)
            if canonical == stored:
                findings.append(Finding(placement, "ok", stored, where.path))
            else:
                findings.append(
                    Finding(
                        placement, "restate", stored, where.path,
                        detail=f"store as: {canonical}  (relative to its program root)",
                    )
                )
            continue
        if where.path is not None:
            findings.append(
                Finding(
                    placement, "moved", stored, where.path,
                    detail=f"found at: {where.path}",
                )
            )
            continue
        if where.error and "more than one program root" in where.error:
            findings.append(Finding(placement, "ambiguous", stored, detail=where.error))
            continue
        found, why = _by_content(conn, placement, roots)
        if found is not None:
            findings.append(
                Finding(placement, "renamed", stored, found, detail=f"same content at: {found}")
            )
        elif why:
            findings.append(Finding(placement, "ambiguous", stored, detail=why))
        else:
            findings.append(
                Finding(
                    placement, "lost", stored,
                    detail=where.error or "no file, and no matching content under the roots",
                )
            )
    return findings


def _by_content(
    conn: sqlite3.Connection, placement: Placement, roots: list[Path]
) -> tuple[Path | None, str]:
    """A file under the roots whose bytes hash to this placement's recorded
    `source_sha256`. Exactly one, or nothing — two files with identical content
    are two programs as far as anything here can tell, and picking either is a
    guess about which client's tower this is."""
    from .. import sync

    if not placement.source_sha256:
        return None, ""
    matches = [
        candidate
        for candidate in sync.scan(roots)
        if sync.file_sha256(candidate) == placement.source_sha256
    ]
    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        where = ", ".join(str(m) for m in matches)
        return None, f"{len(matches)} files have this placement's content ({where})"
    return None, ""


def repair(conn: sqlite3.Connection, findings: list[Finding]) -> list[Finding]:
    """Write the repairable findings back. ONE transaction.

    All or nothing for the same reason `seed()` is one transaction: a
    half-relinked book is worse than either outcome, because the half that
    moved and the half that did not now disagree about where the roots are.
    """
    repairable = [f for f in findings if f.repairable]
    if not repairable:
        return []
    # Unbatched on purpose. `bookctl relink` is a repair of the BOOKKEEPING
    # about files, not an edit to a client's data, and rolling it back with `u`
    # would restore paths that are known to point nowhere. The dry run is the
    # review step; the DB snapshot the CLI takes first is the rollback.
    with transaction(conn):
        for finding in repairable:
            assert finding.resolved is not None
            canonical = programpath.store(conn, finding.resolved)
            # READ THE LINK BEFORE FORGETTING IT. `links.forget` deletes every
            # spelling of a path, so asking which org owned this file after the
            # delete answers None and the re-confirm would fall back to the
            # placement's own org — right by luck here, wrong the moment a
            # link row and a placement disagree, which is exactly the state
            # relink exists to find.
            org_id = links.org_for_path(conn, finding.stored) or links.org_for_path(
                conn, str(finding.resolved)
            )
            if canonical != finding.stored:
                links.forget(conn, finding.stored)
            links.confirm(
                conn,
                canonical,
                org_id or finding.placement.org_id,
                # The INSURED, not the program name: org_for_insured matches on
                # this string, and seeding it with a program name would teach
                # the standing-confirmation rule the wrong vocabulary.
                _insured(finding.resolved),
                source="relink",
            )
            placements.update(
                conn,
                finding.placement.id,
                program_path=canonical,
                note=f"relinked from {finding.stored}",
            )
    return repairable


def _insured(path: Path) -> str:
    """The insured string towerkit records in the file, or its stem."""
    from towerkit.model import load_program

    try:
        return str(load_program(path).insured)
    except Exception:
        return path.stem


def render(findings: list[Finding], *, repaired: bool) -> str:
    """The report. Counts first, because the answer to "is my book alright"
    is a number, and the per-row detail is for the ones that are not."""
    buckets: dict[str, list[Finding]] = {}
    for finding in findings:
        buckets.setdefault(finding.verdict, []).append(finding)

    lines: list[str] = []
    if not findings:
        return "no placements are linked to a program file"
    verb = "repaired" if repaired else "would repair"
    counts = ", ".join(f"{len(v)} {k}" for k, v in sorted(buckets.items()))
    lines.append(f"{len(findings)} linked placement(s): {counts}")

    for verdict in ("ambiguous", "lost", "moved", "renamed", "restate", "ok"):
        rows = buckets.get(verdict)
        if not rows:
            continue
        heading = {
            "ambiguous": "REFUSED — bookkit will not guess which file these mean",
            "lost": "NOT FOUND — the file is gone; re-export it or unlink the placement",
            "moved": f"{verb}: the file moved",
            "renamed": f"{verb}: the file was renamed (identical content)",
            "restate": f"{verb}: stored as an absolute path; restating it relative to its root",
            "ok": "already correct",
        }[verdict]
        lines.append(f"\n{heading}")
        lines.extend(row.render() for row in rows)

    if not repaired and any(f.repairable for f in findings):
        lines.append("\nnothing was written — re-run with --write to apply")
    return "\n".join(lines)
