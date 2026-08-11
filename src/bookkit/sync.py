"""towerkit integration: write-through, not sync (§5).

The JSON file is the only source of truth for program structure; bookkit is a
second editor of that file, going through towerkit's model, validator, and
canonical serialiser. The proj_* tables are a derived cache rebuilt from files
at will, and linking a file to an account is always user-confirmed (§5.2).

Conflict rule — the whole conflict story: source_sha256 is recorded at
projection time; before any write the file is re-hashed, and a mismatch
refuses the write. Never merge, never overwrite silently.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process
from towerkit.model import SCHEMA_ID, Program, dump_program, load_program

# towerkit shares/premiums: dollars + bps; participant premium is floor-divided
from towerkit.money import premium_share
from towerkit.validate import Diagnostics, validate_file, validate_program

from .db import utc_now
from .models import Org, Placement
from .money import dollars_to_cents
from .repo import links, orgs, placements, projection


def file_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scan(roots: list[Path]) -> list[Path]:
    """Find towerkit program JSON under the given roots (schema-id sniff)."""
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
            except OSError:
                continue
            if SCHEMA_ID in head:
                found.append(path)
    return found


@dataclass(frozen=True)
class LinkSuggestion:
    path: Path
    insured: str
    candidates: list[tuple[Org, float]]  # (org, match score 0-100), best first


@dataclass
class SyncReport:
    projected: list[tuple[Path, str]] = field(default_factory=list)  # (path, placement ref)
    needs_link: list[LinkSuggestion] = field(default_factory=list)
    failed: list[tuple[Path, Diagnostics]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def render(self) -> str:
        lines = [f"projected {len(self.projected)} file(s)"]
        for path, ref in self.projected:
            lines.append(f"  ✓ {path.name} → {ref}")
        for suggestion in self.needs_link:
            best = ", ".join(f"{o.name} ({score:.0f})" for o, score in suggestion.candidates[:3])
            lines.append(
                f"  ? {suggestion.path.name}: insured {suggestion.insured!r} not linked"
                + (f" — candidates: {best}" if best else " — no candidates")
            )
        for path, diags in self.failed:
            lines.append(f"  ✗ {path.name}:")
            lines.extend(f"      {d}" for d in diags.errors)
        return "\n".join(lines)


def project(conn: sqlite3.Connection, path: Path) -> Diagnostics:
    """Parse + validate one file through towerkit, then upsert proj_* rows.
    Validation errors mean nothing is projected."""
    program, diags = validate_file(path)
    if program is None or not diags.ok:
        return diags

    org_id = links.org_for_path(conn, str(path))
    if org_id is None:
        diags.error("unlinked", f"{path}: no confirmed account link — confirm in review queue")
        return diags

    placement = _placement_for(conn, path, org_id, program)
    synced = utc_now()
    sha = file_sha256(path)

    layers = []
    participants = []
    for layer in program.layers:
        layers.append(
            {
                "layer_id": layer.id,
                "name": layer.name,
                "applies_to": ",".join(layer.applies_to),
                "attach": dollars_to_cents(layer.attach),
                "lim": dollars_to_cents(layer.limit),
                "premium": dollars_to_cents(layer.premium) if layer.premium is not None else None,
            }
        )
        for part in layer.participants:
            participants.append(
                {
                    "layer_id": layer.id,
                    "carrier": part.carrier,
                    "share_bps": part.share_bps,
                    "premium": (
                        dollars_to_cents(premium_share(layer.premium, part.share_bps))
                        if layer.premium is not None
                        else None
                    ),
                }
            )
    retentions = [
        {
            "applies_to": ",".join(r.applies_to),
            "type": r.type.value,
            "amount": dollars_to_cents(r.amount),
            "aggregate": dollars_to_cents(r.aggregate) if r.aggregate is not None else None,
            "vehicle": r.vehicle,
        }
        for r in program.retentions
    ]
    projection.replace_for_placement(conn, placement.id, synced, layers, participants, retentions)
    placements.update(
        conn,
        placement.id,
        note="projected from towerkit file",
        program_name=program.program,
        period_from=program.period.start.isoformat(),
        period_to=program.period.end.isoformat(),
        total_limit=dollars_to_cents(program.total_limit()),
        total_premium=dollars_to_cents(program.total_premium()),
        currency=program.currency,
        program_path=str(path),
        source_sha256=sha,
        synced_at=synced,
    )
    return diags


def _placement_for(
    conn: sqlite3.Connection, path: Path, org_id: str, program: Program
) -> Placement:
    """The placement row a file projects into: by path, else by org+period,
    else created — one row per program per period (§3.2)."""
    existing = placements.by_program_path(conn, str(path))
    if existing is not None:
        return existing
    start, end = program.period.start.isoformat(), program.period.end.isoformat()
    for candidate in placements.for_org(conn, org_id):
        if candidate.period_from == start and candidate.period_to == end:
            return candidate
    status = "bound" if program.placement.value == "bound" else "prospective"
    return placements.create(
        conn, org_id, program.program, start, end, status=status
    )


def project_all(conn: sqlite3.Connection, roots: list[Path]) -> SyncReport:
    report = SyncReport()
    client_orgs = orgs.list_orgs(conn, kind="client")
    for path in scan(roots):
        if links.org_for_path(conn, str(path)) is None:
            report.needs_link.append(_suggest(conn, path, client_orgs))
            continue
        diags = project(conn, path)
        if diags.ok:
            placement = placements.by_program_path(conn, str(path))
            report.projected.append((path, placement.ref if placement else "?"))
        else:
            report.failed.append((path, diags))
    return report


def _suggest(conn: sqlite3.Connection, path: Path, client_orgs: list[Org]) -> LinkSuggestion:
    """Fuzzy candidates for the review queue. Suggestions only — a wrong guess
    attached to the wrong account is worse than asking (§5.2)."""
    try:
        insured = load_program(path).insured
    except Exception:
        insured = path.stem
    names = {org.name: org for org in client_orgs}
    matches = process.extract(insured, list(names), scorer=fuzz.WRatio, limit=3)
    candidates = [(names[name], float(score)) for name, score, _ in matches if score >= 55]
    return LinkSuggestion(path, insured, candidates)


def confirm_link(conn: sqlite3.Connection, path: Path, org_id: str) -> Diagnostics:
    """User confirmed a file ↔ account link: record it and project."""
    try:
        insured = load_program(path).insured
    except Exception:
        insured = path.stem
    links.confirm(conn, str(path), org_id, insured)
    return project(conn, path)


class WriteConflict(Exception):
    """The file changed on disk since projection — probably towerkit's TUI."""


def write_through(
    conn: sqlite3.Connection,
    placement_id: str,
    mutation: Callable[[Program], None],
) -> Diagnostics:
    """Edit the towerkit file through towerkit: load → mutate → validate →
    canonical write → re-project. Validation failure writes nothing; a changed
    on-disk hash refuses the write outright."""
    diags = Diagnostics()
    placement = placements.get(conn, placement_id)
    if not placement.program_path:
        diags.error("no-file", f"{placement.ref}: no program file linked")
        return diags
    path = Path(placement.program_path)
    if not path.exists():
        diags.error("io", f"{path}: file is gone")
        return diags
    if placement.source_sha256 and file_sha256(path) != placement.source_sha256:
        raise WriteConflict(
            f"{path} changed on disk since last projection — re-sync and retry"
        )

    program = load_program(path)
    mutation(program)
    check = validate_program(program)
    if not check.ok:
        return check

    dump_program(program, path)
    return project(conn, path)
