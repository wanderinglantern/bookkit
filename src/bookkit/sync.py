"""towerkit integration: write-through, not sync (§5).

The JSON file is the only source of truth for program structure; bookkit is a
second editor of that file, going through towerkit's model, validator, and
canonical serialiser. The proj_* tables are a derived cache rebuilt from files
at will.

Linking policy — maximise unified records, never wrongly merge:
- link at birth: `renew` clones next period's file and placement pre-linked
- standing confirmation: a new path auto-links when its insured string is
  byte-identical to one the user already confirmed (provenance recorded)
- rename detection: identical content hash re-points a dangling link silently
- adoption: within a linked org, a new file adopts the single uncontested
  file-less placement whose period overlaps; any ambiguity (multiple
  candidates, or two files claiming one placement in the same run) queues
  for the user instead
- everything fuzzier goes to the review queue; wrong merges are worse than
  duplicates

Conflict rule — the whole conflict story: source_sha256 is recorded at
projection time; before any write the file is re-hashed, and a mismatch
refuses the write. Never merge, never overwrite silently.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

# towerkit.edit is the ONE definition of a structural mutation and of the id
# rule that names what it creates. bookkit used to keep private copies of both
# (a `_slug` that saw only layer ids, and no healing at all); towerkit's own
# tests/test_conventions.py bans reaching past this module, but scans only
# src/towerkit/tui, so the rule was invisible here. See
# tests/test_conventions.py::test_sync_delegates_program_structure_to_towerkit.
from towerkit import mcpsurface
from towerkit.edit import add_layer as edit_add_layer
from towerkit.edit import add_line as edit_add_line
from towerkit.edit import add_named_limit as edit_add_named_limit
from towerkit.edit import add_retention as edit_add_retention
from towerkit.edit import add_sublimit as edit_add_sublimit
from towerkit.edit import edit_retention as edit_edit_retention
from towerkit.edit import edit_sublimit as edit_edit_sublimit
from towerkit.edit import heal_follows, slugify, unique_id
from towerkit.edit import move_line as edit_move_line
from towerkit.edit import remove_layer as edit_remove_layer
from towerkit.edit import remove_line as edit_remove_line
from towerkit.edit import remove_named_limit as edit_remove_named_limit
from towerkit.edit import remove_retention as edit_remove_retention
from towerkit.edit import remove_sublimit as edit_remove_sublimit
from towerkit.edit import rename_line as edit_rename_line
from towerkit.edit import set_container as edit_set_container
from towerkit.edit import set_field as edit_set_field
from towerkit.model import SCHEMA_ID, Program, dump_program, load_program
from towerkit.model import Layer as TkModelLayer

# towerkit shares/premiums: dollars + bps; participant premium is floor-divided
from towerkit.money import premium_share
from towerkit.validate import Diagnostics, validate_file, validate_program

from . import programpath
from .db import utc_now
from .models import Opportunity, Org, Placement
from .money import MoneyParseError, cents_to_dollars, dollars_to_cents
from .repo import aliases, links, opportunities, orgs, placements, projection


def file_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configured_roots(conn: sqlite3.Connection) -> list[Path]:
    """Where program files live — programpath.roots is the definition."""
    return programpath.roots(conn)


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


class AmbiguousPlacement(Exception):
    """More than one file-less placement could adopt this file — user picks."""

    def __init__(self, candidates: list[Placement]) -> None:
        super().__init__(f"{len(candidates)} candidate placements")
        self.candidates = candidates


@dataclass(frozen=True)
class LinkSuggestion:
    path: Path
    insured: str
    candidates: list[tuple[Org, float]]  # (org, match score 0-100), best first


@dataclass(frozen=True)
class PlacementSuggestion:
    path: Path
    org_id: str
    insured: str
    candidates: list[Placement]  # file-less overlapping placements


@dataclass(frozen=True)
class OpportunityCandidate:
    """An unplaced (TBD) line on a proposed program — potential new revenue."""

    path: Path
    org_id: str
    placement_id: str
    line_id: str
    line_name: str
    layer_names: tuple[str, ...]
    premium: int | None  # cents: indicated premium across the pending layers
    target_effective: str  # DATE — program period start


@dataclass(frozen=True)
class CarrierSuggestion:
    """A carrier string on projected towers matching no market org and no
    alias — every cross-book join misses it until it's aliased or created."""

    carrier: str
    candidates: list[tuple[Org, float]]  # (market org, match score), best first


@dataclass
class SyncReport:
    projected: list[tuple[Path, str]] = field(default_factory=list)  # (path, placement ref)
    adopted: list[tuple[Path, str]] = field(default_factory=list)  # subset of projected
    relinked: list[tuple[Path, str]] = field(default_factory=list)  # (path, how)
    needs_link: list[LinkSuggestion] = field(default_factory=list)
    needs_placement: list[PlacementSuggestion] = field(default_factory=list)
    opportunity_candidates: list[OpportunityCandidate] = field(default_factory=list)
    unresolved_carriers: list[CarrierSuggestion] = field(default_factory=list)
    failed: list[tuple[Path, Diagnostics]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def render(self) -> str:
        lines = [f"projected {len(self.projected)} file(s)"]
        adopted_paths = {p for p, _ in self.adopted}
        for path, ref in self.projected:
            mark = "⇤ adopted" if path in adopted_paths else "✓"
            lines.append(f"  {mark} {path.name} → {ref}")
        for path, how in self.relinked:
            lines.append(f"  ↺ {path.name}: re-linked ({how})")
        for suggestion in self.needs_link:
            best = ", ".join(f"{o.name} ({score:.0f})" for o, score in suggestion.candidates[:3])
            lines.append(
                f"  ? {suggestion.path.name}: insured {suggestion.insured!r} not linked"
                + (f" — candidates: {best}" if best else " — no candidates")
            )
        for ps in self.needs_placement:
            refs = ", ".join(p.ref for p in ps.candidates)
            lines.append(
                f"  ? {ps.path.name}: {len(ps.candidates)} matching placements ({refs})"
                " — resolve in the TUI (y on Today)"
            )
        for oc in self.opportunity_candidates:
            lines.append(
                f"  $ {oc.path.name}: line {oc.line_name!r} unplaced on proposed program"
                " — potential opportunity (offer in the TUI)"
            )
        for cs in self.unresolved_carriers:
            best = ", ".join(f"{o.name} ({score:.0f})" for o, score in cs.candidates[:3])
            lines.append(
                f"  ≈ carrier {cs.carrier!r} matches no market"
                + (f" — alias to: {best}?" if best else " — create it?")
                + " (resolve in the TUI)"
            )
        for path, diags in self.failed:
            lines.append(f"  ✗ {path.name}:")
            lines.extend(f"      {d}" for d in diags.errors)
        return "\n".join(lines)


# --- projection ---------------------------------------------------------------


def project(
    conn: sqlite3.Connection,
    path: Path,
    placement_id: str | None = None,
    create_new: bool = False,
) -> Diagnostics:
    """Parse + validate one file through towerkit, then upsert proj_* rows.
    Validation errors mean nothing is projected. May raise AmbiguousPlacement
    unless an explicit target (placement_id) or create_new is given."""
    diags, _how, _pid = _project_impl(conn, path, placement_id, create_new)
    return diags


def _project_impl(
    conn: sqlite3.Connection,
    path: Path,
    placement_id: str | None = None,
    create_new: bool = False,
) -> tuple[Diagnostics, str, str | None]:
    program, diags = validate_file(path)
    if program is None or not diags.ok:
        return diags, "failed", None

    org_id = links.org_for_path(conn, str(path))
    if org_id is None:
        diags.error("unlinked", f"{path}: no confirmed account link — confirm in review queue")
        return diags, "failed", None

    if placement_id is not None:
        placement, how = placements.get(conn, placement_id), "adopted"
    else:
        placement, how = _placement_for(conn, path, org_id, program, create_new)
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
        # RELATIVE TO A ROOT where one contains it (programpath.store): the
        # absolute spelling is what turned a moved towerkit checkout into five
        # programs the web called empty (2026-08-20).
        program_path=programpath.store(conn, path),
        source_sha256=sha,
        synced_at=synced,
    )
    return diags, how, placement.id


def _placement_for(
    conn: sqlite3.Connection,
    path: Path,
    org_id: str,
    program: Program,
    create_new: bool = False,
) -> tuple[Placement, str]:
    """The placement a file projects into: by path; by exact period (never
    stealing another file's placement); by adopting the single file-less
    overlapping placement; else created. Ambiguity raises."""
    existing = placements.by_program_path(conn, str(path))
    if existing is not None:
        return existing, "path"
    start, end = program.period.start.isoformat(), program.period.end.isoformat()
    if not create_new:
        for candidate in placements.for_org(conn, org_id):
            if (
                candidate.period_from == start
                and candidate.period_to == end
                and candidate.program_path in (None, str(path))
            ):
                return candidate, "exact"
        clean, dangling = _adoption_candidates(conn, org_id, start, end)
        if dangling or len(clean) > 1:
            raise AmbiguousPlacement(clean + dangling)
        if len(clean) == 1:
            return clean[0], "adopted"
    status = "bound" if program.placement.value == "bound" else "prospective"
    return placements.create(conn, org_id, program.program, start, end, status=status), "created"


def _adoption_candidates(
    conn: sqlite3.Connection, org_id: str, start: str, end: str
) -> tuple[list[Placement], list[Placement]]:
    """(clean, dangling): file-less overlapping placements are clean adoption
    candidates; placements whose linked file is GONE from disk are dangling —
    plausibly this file renamed-and-edited, but content changed, so they are
    only ever offered in the queue, never auto-adopted."""
    clean = placements.unlinked_overlapping(conn, org_id, start, end)
    dangling = [
        p
        for p in placements.for_org(conn, org_id)
        if p.program_path is not None
        and not programpath.resolve(conn, p.program_path).ok
        and p.period_from < end
        and p.period_to > start
    ]
    return clean, dangling


# --- the sweep ----------------------------------------------------------------


def project_all(conn: sqlite3.Connection, roots: list[Path]) -> SyncReport:
    report = SyncReport()
    client_orgs = orgs.list_orgs(conn, kind="client")

    resolved: list[tuple[Path, str]] = []  # (path, org_id)
    for path in scan(roots):
        org_id = links.org_for_path(conn, str(path))
        if org_id is None:
            org_id = _detect_rename(conn, path)
            if org_id is not None:
                report.relinked.append((path, "rename — identical content"))
        if org_id is None:
            insured = _insured(path)
            org_id = links.org_for_insured(conn, insured)
            if org_id is not None:
                links.confirm(
                    conn, programpath.store(conn, path), org_id, insured,
                    source="insured_match",
                )
                report.relinked.append((path, f"insured previously confirmed: {insured!r}"))
        if org_id is None:
            report.needs_link.append(_suggest(conn, path, client_orgs))
        else:
            resolved.append((path, org_id))

    # Placement plan with the contested-candidate guard: two files in one run
    # must never race for the same manual placement (bound + proposed programs
    # for one insured is a normal, real case).
    plans: list[tuple[Path, str | None]] = []  # (path, explicit target or None)
    pending: list[tuple[Path, str, list[Placement], list[Placement]]] = []
    claims: dict[str, int] = {}
    for path, org_id in resolved:
        if placements.by_program_path(conn, str(path)) is not None:
            plans.append((path, None))
            continue
        try:
            program = load_program(path)
        except Exception:
            plans.append((path, None))  # projection will report the failure
            continue
        clean, dangling = _adoption_candidates(
            conn, org_id, program.period.start.isoformat(), program.period.end.isoformat()
        )
        pending.append((path, org_id, clean, dangling))
        for candidate in clean:
            claims[candidate.id] = claims.get(candidate.id, 0) + 1
    for path, org_id, clean, dangling in pending:
        if not dangling and len(clean) == 1 and claims[clean[0].id] == 1:
            plans.append((path, clean[0].id))
        elif not clean and not dangling:
            plans.append((path, None))
        else:
            report.needs_placement.append(
                PlacementSuggestion(path, org_id, _insured(path), clean + dangling)
            )

    for path, target in plans:
        diags, how, placement_id = _project_impl(conn, path, placement_id=target)
        if diags.ok and placement_id is not None:
            placement = placements.get(conn, placement_id)
            report.projected.append((path, placement.ref))
            if how == "adopted":
                report.adopted.append((path, placement.ref))
            report.opportunity_candidates.extend(opportunities_for_path(conn, path))
        else:
            report.failed.append((path, diags))

    report.unresolved_carriers = carrier_suggestions(conn)
    return report


def carrier_suggestions(conn: sqlite3.Connection) -> list[CarrierSuggestion]:
    """Unresolved tower carriers with fuzzy market candidates for the review
    queue. Suggestions only — an alias is a user decision."""
    market_orgs = orgs.list_orgs(conn, kind="market")
    names = {org.name: org for org in market_orgs}
    out: list[CarrierSuggestion] = []
    for carrier in aliases.unresolved_carriers(conn):
        matches = process.extract(carrier, list(names), scorer=fuzz.WRatio, limit=3)
        candidates = [(names[n], float(score)) for n, score, _ in matches if score >= 60]
        out.append(CarrierSuggestion(carrier, candidates))
    return out


def known_carriers(conn: sqlite3.Connection) -> set[str]:
    """Every carrier spelling that resolves to a market org — the market names
    themselves plus every recorded alias.

    A carrier that is not in here is a string in a towerkit file and nothing
    more: it misses exposure, hit rate and the market dossier. The surfaces use
    this to SAY so at the point the carrier is written, rather than leaving it
    to be discovered on a tab that never mentions it (Grant, 2026-08-20)."""
    names = {org.name for org in orgs.list_orgs(conn, kind="market")}
    return names | set(aliases.alias_map(conn))


def alias_carrier(conn: sqlite3.Connection, carrier: str, market_org_id: str) -> None:
    aliases.set_alias(conn, carrier, market_org_id)


def create_market_for_carrier(conn: sqlite3.Connection, carrier: str) -> Org:
    """The carrier really is a market we don't have yet: create it under the
    exact tower spelling, so it matches by name with no alias needed."""
    return orgs.create(conn, kind="market", name=carrier, status="active")


def _insured(path: Path) -> str:
    try:
        return load_program(path).insured
    except Exception:
        return path.stem


def _detect_rename(conn: sqlite3.Connection, path: Path) -> str | None:
    """Identical content at a new path while the old path is gone: the same
    file moved. Re-point silently; anything less than byte-identical queues."""
    sha = file_sha256(path)
    matches = [
        p
        for p in placements.all_linked(conn)
        if p.source_sha256 == sha
        and p.program_path is not None
        and not programpath.resolve(conn, p.program_path).ok
    ]
    if len(matches) != 1:
        return None
    moved = matches[0]
    old_path = moved.program_path
    links.confirm(
        conn, programpath.store(conn, path), moved.org_id, _insured(path),
        source="rename",
    )
    if old_path is not None:
        links.forget(conn, old_path)
    placements.update(
        conn, moved.id, program_path=programpath.store(conn, path),
        note=f"file renamed from {old_path}",
    )
    return moved.org_id


def _suggest(conn: sqlite3.Connection, path: Path, client_orgs: list[Org]) -> LinkSuggestion:
    """Fuzzy candidates for the review queue. Suggestions only — a wrong guess
    attached to the wrong account is worse than asking (§5.2)."""
    insured = _insured(path)
    names = {org.name: org for org in client_orgs}
    matches = process.extract(insured, list(names), scorer=fuzz.WRatio, limit=3)
    candidates = [(names[name], float(score)) for name, score, _ in matches if score >= 55]
    return LinkSuggestion(path, insured, candidates)


# --- review-queue resolutions -------------------------------------------------


def confirm_link(conn: sqlite3.Connection, path: Path, org_id: str) -> Diagnostics:
    """User confirmed a file ↔ account link: record it and project. May raise
    AmbiguousPlacement — the caller then asks which placement to adopt."""
    links.confirm(conn, programpath.store(conn, path), org_id, _insured(path))
    return project(conn, path)


def confirm_placement(
    conn: sqlite3.Connection, path: Path, placement_id: str | None
) -> Diagnostics:
    """User resolved a placement ambiguity: adopt the chosen placement (also
    re-pointing a dangling link and forgetting its dead path), or None to
    create a fresh one."""
    if placement_id is not None:
        chosen = placements.get(conn, placement_id)
        if chosen.program_path and chosen.program_path != str(path):
            links.forget(conn, chosen.program_path)
        return project(conn, path, placement_id=placement_id)
    return project(conn, path, create_new=True)


# --- opportunities from proposed programs -------------------------------------


def opportunities_for_path(conn: sqlite3.Connection, path: Path) -> list[OpportunityCandidate]:
    """TBD lines on a proposed program: lines carried only by pending layers
    (no participants — towerkit's 'To be placed'). Offered, never auto-created;
    lines already tracked by an opportunity with the same line id and effective
    date are not re-offered."""
    try:
        program = load_program(path)
    except Exception:
        return []
    if program.placement.value != "proposed":
        return []
    placement = placements.by_program_path(conn, str(path))
    if placement is None:
        return []
    effective = program.period.start.isoformat()
    existing = {
        (o.lines, o.target_effective)
        for o in opportunities.for_org(conn, placement.org_id)
    }
    out: list[OpportunityCandidate] = []
    for line in program.lines:
        line_layers = [ly for ly in program.layers if line.id in ly.applies_to]
        pending = [ly for ly in line_layers if not ly.participants]
        if not line_layers or not pending:
            continue
        if _line_already_tracked(
            line, effective, existing, {ln.id for ln in program.lines}
        ):
            continue
        premiums = [ly.premium for ly in pending if ly.premium is not None]
        out.append(
            OpportunityCandidate(
                path=path,
                org_id=placement.org_id,
                placement_id=placement.id,
                line_id=line.id,
                line_name=line.name,
                layer_names=tuple(ly.name for ly in pending),
                premium=dollars_to_cents(sum(premiums)) if premiums else None,
                target_effective=effective,
            )
        )
    return out


# fuzz.ratio floor for "same line, id re-slugged". One-typo fixes score >=88
# ("cybr"/"cyber" 88.9, "genral-liability"/"general-liability" 97). Ratio
# ALONE is not a safe discriminator — it scales with length, so a genuinely
# distinct sibling like "general-liability-2" scores 94 against
# "general-liability" — which is why the bridge below also requires the
# stored id to have VANISHED from the program and refuses unique_id's
# numeric-suffix siblings outright. Boundaries pinned in test_linking_flow.py.
_RENAME_CUTOFF = 85

_NUMERIC_SUFFIX = re.compile(r"-\d+$")


def _line_already_tracked(
    line: Any,
    effective: str,
    existing: set[tuple[str | None, str | None]],
    current_ids: set[str],
) -> bool:
    """Does an opportunity already cover this line for this effective date?

    Exact id match first. Then the rename bridge: towerkit line ids follow
    their names now (towerkit 67ac42f), so a typo fix re-slugs the id and an
    exact-only dedupe would create a DUPLICATE opportunity on the next sync.

    The bridge fires only when ALL of:
    - the stored key matches NO current line id — a rename means the old id
      vanished; if it still names a live line, the opportunity is that
      line's, and this one is genuinely new;
    - neither id is the other plus a "-N" suffix — that suffix is exactly
      what session.unique_id mints for a same-slug SIBLING, never a rename;
    - fuzz.ratio clears the cutoff.

    The line's display name is also accepted for TUI-authored opportunities,
    which store names rather than slugs."""
    from rapidfuzz import fuzz

    if (line.id, effective) in existing:
        return True
    for stored, when in existing:
        if when != effective or not stored:
            continue
        if stored == line.name:
            return True
        if stored in current_ids:
            continue  # still a live line's key — not a rename of THIS one
        if _NUMERIC_SUFFIX.sub("", stored) == _NUMERIC_SUFFIX.sub("", line.id) and (
            _NUMERIC_SUFFIX.search(stored) or _NUMERIC_SUFFIX.search(line.id)
        ):
            continue  # unique_id's collision suffix marks a sibling, not a rename
        if fuzz.ratio(stored, line.id) >= _RENAME_CUTOFF:
            return True
    return False


def create_opportunity(conn: sqlite3.Connection, candidate: OpportunityCandidate) -> Opportunity:
    """Turn an offered TBD line into a tracked opportunity."""
    return opportunities.create(
        conn,
        candidate.org_id,
        f"{candidate.line_name} placement",
        lines=candidate.line_id,
        target_premium=candidate.premium,
        target_effective=candidate.target_effective,
        source="towerkit proposed program",
    )


# --- renew: link at birth -----------------------------------------------------


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _bump_years(text: str) -> str:
    return _YEAR_RE.sub(lambda m: str(int(m.group(0)) + 1), text)


def _plus_year_iso(iso: str) -> str:
    """A DATE one year on, clamping Feb 29 like towerkit's clone_as_renewal."""
    from datetime import date as _date

    d = _date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:  # Feb 29
        return d.replace(year=d.year + 1, day=28).isoformat()


def renewal_period(placement: Placement) -> tuple[str, str]:
    """The period renew() will stamp — for confirms that say what will happen
    BEFORE it does, without reaching into this module's date helpers."""
    return _plus_year_iso(placement.period_from), _plus_year_iso(placement.period_to)


def renew(
    conn: sqlite3.Connection, placement_id: str
) -> tuple[Placement | None, Path | None, Diagnostics]:
    """Roll a placement into its next period. File-backed placements also get
    next year's towerkit file via clone_as_renewal, linked at birth — no
    matching ever needed. Returns (new placement, new file path, diagnostics);
    placement is None when the renew was refused."""
    diags = Diagnostics()
    placement = placements.get(conn, placement_id)

    new_path: Path | None = None
    program: Program | None = None
    if placement.program_path:
        try:
            path = program_file(conn, placement)
        except ProgramFileMissing as missing:
            diags.error("io", str(missing))
            return None, None, diags
        program, file_diags = validate_file(path)
        if program is None or not file_diags.ok:
            diags.items.extend(file_diags.items)
            diags.error("invalid-source", f"{path.name}: fix validation errors before renewing")
            return None, None, diags
        stem = _bump_years(path.stem)
        if stem == path.stem:
            new_start = program.clone_as_renewal().period.start
            stem = f"{path.stem}-{new_start.year}"
        new_path = path.with_name(f"{stem}{path.suffix}")
        if new_path.exists():
            diags.error(
                "exists", f"{new_path.name} already exists — refusing to overwrite"
            )
            return None, None, diags

    from_date = _plus_year_iso(placement.period_from)
    to_date = _plus_year_iso(placement.period_to)
    new_placement = placements.create(
        conn,
        placement.org_id,
        _bump_years(placement.program_name),
        from_date,
        to_date,
        status="prospective",
        total_premium=placement.total_premium,  # expiring indication
        total_limit=placement.total_limit,
        commission_bps=placement.commission_bps,
        currency=placement.currency,
    )

    if new_path is not None and program is not None:
        clone = program.clone_as_renewal()
        dump_program(clone, new_path)
        links.confirm(
            conn, programpath.store(conn, new_path), placement.org_id, clone.insured,
            source="renewal",
        )
        diags = project(conn, new_path, placement_id=new_placement.id)
        new_placement = placements.get(conn, new_placement.id)
    return new_placement, new_path, diags


# --- scaffold: the inverse of adoption (DRY the other direction) --------------


def scaffold_program(
    conn: sqlite3.Connection, placement_id: str, dest: Path
) -> tuple[Path | None, Diagnostics]:
    """Create a towerkit file FROM a bookkit placement — insured, program
    name, period, and indicated premium/limit flow over so nothing is typed
    twice. The scaffold carries one TBD line with one pending ('To be placed')
    layer, which is valid towerkit (warnings only), renders dashed, and is
    ready to be built out in towerkit's editor."""
    from datetime import date as _date

    from towerkit.model import Layer, Line
    from towerkit.model import Period as TkPeriod
    from towerkit.model import Placement as TkPlacement

    diags = Diagnostics()
    placement = placements.get(conn, placement_id)
    if placement.program_path:
        diags.error("linked", f"{placement.ref} already has a program file")
        return None, diags
    if dest.exists():
        diags.error("exists", f"{dest} already exists — refusing to overwrite")
        return None, diags
    org = orgs.get(conn, placement.org_id)

    def _dollars(cents: int | None) -> int | None:
        if cents is None:
            return None
        try:
            return cents_to_dollars(cents)
        except MoneyParseError:
            return cents // 100

    limit = _dollars(placement.total_limit) or 1_000_000
    program = Program(
        insured=org.name,
        program=placement.program_name,
        placement=(
            TkPlacement.BOUND if placement.status == "bound" else TkPlacement.PROPOSED
        ),
        period=TkPeriod(
            start=_date.fromisoformat(placement.period_from),
            end=_date.fromisoformat(placement.period_to),
        ),
        currency=placement.currency,
        lines=[Line(id="tbd", name="Coverage TBD", abbr="TBD")],
        layers=[
            Layer(
                id="tbd-primary",
                name="To be placed",
                applies_to=["tbd"],
                attach=0,
                limit=limit,
                premium=_dollars(placement.total_premium),
                participants=[],
            )
        ],
    )
    check = validate_program(program)
    if not check.ok:
        return None, check
    dest.parent.mkdir(parents=True, exist_ok=True)
    dump_program(program, dest)
    links.confirm(conn, programpath.store(conn, dest), org.id, org.name, source="scaffold")
    diags = project(conn, dest, placement_id=placement.id)
    return dest, diags


# --- transactional program edits (all via write_through) ----------------------
#
# bookkit edits the facts that arise from book events — premiums firming up,
# markets binding onto layers, dates moving. Tower DESIGN (lines, retentions,
# structure) stays in towerkit's editor; `o` jumps there.


def _dollars_or_diag(cents: int, diags: Diagnostics, label: str) -> int | None:
    try:
        return cents_to_dollars(cents)
    except MoneyParseError:
        diags.error("money", f"{label}: {cents} cents is not whole dollars")
        return None


def update_program(
    conn: sqlite3.Connection,
    placement_id: str,
    program_name: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> Diagnostics:
    """Edit the program-level facts the file owns: name and effective dates."""
    from datetime import date as _date

    def mutate(program: Program) -> None:
        if program_name is not None:
            program.program = program_name
        start = _date.fromisoformat(period_from) if period_from else program.period.start
        end = _date.fromisoformat(period_to) if period_to else program.period.end
        if (period_from or period_to) and end <= start:
            raise ValueError(f"period ends {end} on or before it starts {start}")
        if period_from or period_to:
            from towerkit.model import Period as TkPeriod

            program.period = TkPeriod(start=start, end=end)

    return _mutate(conn, placement_id, mutate)


def update_layer(
    conn: sqlite3.Connection,
    placement_id: str,
    layer_id: str,
    name: str | None = None,
    policy_number: str | None = None,
    attach_cents: int | None = None,
    limit_cents: int | None = None,
    premium_cents: int | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> Diagnostics:
    from datetime import date as _date

    from towerkit.model import Period as TkPeriod

    def mutate(program: Program) -> None:
        layer = _find_layer(program, layer_id)
        if name is not None:
            layer.name = name
        if policy_number is not None:
            layer.policy_number = policy_number
        if attach_cents is not None:
            layer.attach = _require_dollars(attach_cents, "attach")
        if limit_cents is not None:
            layer.limit = _require_dollars(limit_cents, "limit")
        if premium_cents is not None:
            layer.premium = _require_dollars(premium_cents, "premium")
        if period_from or period_to:
            base = layer.period or program.period
            start = _date.fromisoformat(period_from) if period_from else base.start
            end = _date.fromisoformat(period_to) if period_to else base.end
            if end <= start:
                raise ValueError(f"policy period ends {end} on or before it starts {start}")
            layer.period = TkPeriod(start=start, end=end)

    return _mutate(conn, placement_id, mutate)


def add_layer(
    conn: sqlite3.Connection,
    placement_id: str,
    name: str,
    line_ids: list[str],
    attach_cents: int,
    limit_cents: int,
    premium_cents: int | None = None,
) -> Diagnostics:
    """Append a pending ('To be placed') layer — participants join as markets
    bind.

    THE APPEND AND THE ID BOTH BELONG TO towerkit.edit. The private `_slug`
    this used to call considered only LAYER ids taken, so a new layer named
    after an existing line ("Cyber" beside line `cy`... or line `cyber`) got
    that line's id — and no validator catches an id shared across the two
    collections, because nothing looks. `edit.unique_id` takes the union of
    layer and line ids, which is the rule towerkit's own two surfaces obey.
    """

    def mutate(program: Program) -> None:
        attach = _require_dollars(attach_cents, "attach")
        limit = _require_dollars(limit_cents, "limit")
        premium = (
            _require_dollars(premium_cents, "premium") if premium_cents is not None else None
        )
        layer = edit_add_layer(program, line_ids)
        # edit.add_layer names and seats the layer the way towerkit's editor
        # would (auto ordinal, suggested attach); bookkit is placing a layer
        # the broker has already named and priced, so the facts are overwritten
        # here — through the object edit.add_layer returned, never a second
        # append. `exclude=layer.id` keeps unique_id from colliding the new
        # layer with the placeholder id it is replacing.
        layer.id = unique_id(program, slugify(name), exclude=layer.id)
        layer.name = name
        layer.attach = attach
        layer.limit = limit
        layer.premium = premium

    return _mutate(conn, placement_id, mutate)


def _find_line(program: Program, line_id: str) -> None:
    """A ValueError towerkit's KeyError-raising _line would bypass _mutate
    with — same reason remove_layer pre-checks via _find_layer."""
    if not any(line.id == line_id for line in program.lines):
        raise ValueError(f"line {line_id!r} is not in this program (re-sync?)")


def add_line(conn: sqlite3.Connection, placement_id: str, name: str) -> Diagnostics:
    """A new line of cover, ARRIVING WITH a pending 'To be placed' layer —
    towerkit's validator makes an empty line an ERROR (line-empty), so a bare
    line could never be written through. Same shape scaffold_program uses;
    build the real layers here or in towerkit. (D1, phase 3, 2026-08-19.)"""

    def mutate(program: Program) -> None:
        if any(line.name == name for line in program.lines):
            raise ValueError(f"this program already has a line named {name!r}")
        line = edit_add_line(program, name)
        layer = edit_add_layer(program, [line.id])
        layer.name = "To be placed"
        layer.attach = 0

    return _mutate(conn, placement_id, mutate)


def rename_line(
    conn: sqlite3.Connection, placement_id: str, line_id: str, name: str
) -> Diagnostics:
    """towerkit's rename: the id follows the name and CASCADES through every
    appliesTo, so nothing is stranded on the old slug. This is the write that
    turns a scaffold's 'Coverage TBD' into a real line without leaving the
    browser — the dead-end the parity review called F4."""

    def mutate(program: Program) -> None:
        _find_line(program, line_id)
        edit_rename_line(program, line_id, name)

    return _mutate(conn, placement_id, mutate)


def remove_line(
    conn: sqlite3.Connection, placement_id: str, line_id: str
) -> Diagnostics:
    """towerkit's cascade: the id leaves every appliesTo and any layer,
    retention or sublimit left with an empty appliesTo goes with it. The
    validator still gates the result, so a removal that leaves the program
    invalid refuses with nothing written."""

    def mutate(program: Program) -> None:
        _find_line(program, line_id)
        if len(program.lines) == 1:
            raise ValueError(
                "this is the program's only line — a program with no lines "
                "cannot be drawn; rename it instead"
            )
        edit_remove_line(program, line_id)

    return _mutate(conn, placement_id, mutate)


def _check_index(items: list[Any], index: int, what: str) -> None:
    """towerkit's _at raises IndexError, which _mutate does not catch — the
    pre-check turns a bad index into the ordinary refusal shape."""
    if not 0 <= index < len(items):
        raise ValueError(f"no {what} at index {index} (there are {len(items)})")


def add_retention(
    conn: sqlite3.Connection,
    placement_id: str,
    applies_to: list[str],
    type: str,
    amount_cents: int,
    aggregate_cents: int | None = None,
    notes: str | None = None,
) -> Diagnostics:
    """A retention under the tower (deductible / SIR / captive). Amounts are
    cents on the wire, whole dollars in the file, like every money field.

    A NEW row has nothing to preserve, so `notes` needs no `set_notes` flag
    here: None is an absent note, which is what a new retention has."""

    def mutate(program: Program) -> None:
        retention = edit_add_retention(
            program,
            applies_to,
            type,
            _require_dollars(amount_cents, "amount"),
            aggregate=(
                _require_dollars(aggregate_cents, "aggregate")
                if aggregate_cents is not None
                else None
            ),
        )
        if notes:
            at = program.retentions.index(retention)
            # The new row's position IS its address — see edit_retention.
            edit_set_field(program, "retention", "notes", notes, at, at)

    return _mutate(conn, placement_id, mutate)


def edit_retention(
    conn: sqlite3.Connection,
    placement_id: str,
    index: int,
    *,
    applies_to: list[str] | None = None,
    type: str | None = None,
    amount_cents: int | None = None,
    notes: str | None = None,
    set_notes: bool = False,
) -> Diagnostics:
    """None means leave alone — vehicle rides through untouched, the same
    preservation rule towerkit's own edit_retention keeps. Retentions have no
    ids; the index is towerkit's own addressing, and every web write re-renders
    the panel so an index is never stale.

    `set_notes` is separate from `notes` because None already means "leave
    alone" here, and a note has to be CLEARABLE — one flag rather than a
    sentinel value, so the call site says which of the two it means in words.
    The note itself is written through `edit.set_field`, the choke point, not
    through towerkit's `notes=` keyword: that keyword cannot express a clear
    either, and it is a hand-written copy of the model that `mcpsurface` exists
    to stop trusting.
    """

    def mutate(program: Program) -> None:
        _check_index(list(program.retentions), index, "retention")
        edit_edit_retention(
            program,
            index,
            applies_to=applies_to,
            type=type,
            amount=(
                _require_dollars(amount_cents, "amount")
                if amount_cents is not None
                else None
            ),
        )
        if set_notes:
            # A RETENTION'S TARGET IS ITS INDEX. `edit._entity` takes the
            # list position as `target` for a retention or a sublimit (one
            # argument cannot say both, and a participant's target is its
            # LAYER) — passing None here raised "retention needs a target",
            # which `_mutate` folded into an ordinary refusal, so the whole
            # save came back as a re-rendered form with the figure unchanged
            # and nothing said about why. `mcpsurface.edit_address` states the
            # rule; these four call sites are the only ones that do not read
            # it from there, because they sit inside another verb's mutation.
            edit_set_field(program, "retention", "notes", notes, index, index)

    return _mutate(conn, placement_id, mutate)


def remove_retention(
    conn: sqlite3.Connection, placement_id: str, index: int
) -> Diagnostics:
    def mutate(program: Program) -> None:
        _check_index(list(program.retentions), index, "retention")
        edit_remove_retention(program, index)

    return _mutate(conn, placement_id, mutate)


def add_sublimit(
    conn: sqlite3.Connection,
    placement_id: str,
    name: str,
    amount_cents: int,
    applies_to: list[str],
    notes: str | None = None,
) -> Diagnostics:
    """A new row has nothing to preserve — see `add_retention` on why `notes`
    needs no `set_notes` flag on this side."""

    def mutate(program: Program) -> None:
        sublimit = edit_add_sublimit(
            program, name, _require_dollars(amount_cents, "amount"), applies_to
        )
        if notes:
            at = program.sublimits.index(sublimit)
            edit_set_field(program, "sublimit", "notes", notes, at, at)

    return _mutate(conn, placement_id, mutate)


def edit_sublimit(
    conn: sqlite3.Connection,
    placement_id: str,
    index: int,
    *,
    name: str | None = None,
    amount_cents: int | None = None,
    applies_to: list[str] | None = None,
    notes: str | None = None,
    set_notes: bool = False,
) -> Diagnostics:
    """None means leave alone.

    `set_notes` is separate from `notes` because None already means "leave
    alone" here, and a note has to be CLEARABLE — one flag rather than a
    sentinel value, so the call site says which of the two it means in words.
    The note itself is written through `edit.set_field`, the choke point, not
    through towerkit's `notes=` keyword: that keyword cannot express a clear
    either, and it is a hand-written copy of the model that `mcpsurface` exists
    to stop trusting.
    """

    def mutate(program: Program) -> None:
        _check_index(list(program.sublimits), index, "sublimit")
        edit_edit_sublimit(
            program,
            index,
            name=name,
            amount=(
                _require_dollars(amount_cents, "amount")
                if amount_cents is not None
                else None
            ),
            applies_to=applies_to,
        )
        if set_notes:
            # See edit_retention: a sublimit is addressed by position too.
            edit_set_field(program, "sublimit", "notes", notes, index, index)

    return _mutate(conn, placement_id, mutate)


def remove_sublimit(
    conn: sqlite3.Connection, placement_id: str, index: int
) -> Diagnostics:
    def mutate(program: Program) -> None:
        _check_index(list(program.sublimits), index, "sublimit")
        edit_remove_sublimit(program, index)

    return _mutate(conn, placement_id, mutate)


def move_line(
    conn: sqlite3.Connection, placement_id: str, line_id: str, delta: int
) -> Diagnostics:
    """Column order in the drawing. A move off either end is towerkit's
    documented NO-OP, not an error — the write still round-trips (canonical
    dump, re-project), which is harmless and keeps one code path."""

    def mutate(program: Program) -> None:
        _find_line(program, line_id)
        edit_move_line(program, line_id, delta)

    return _mutate(conn, placement_id, mutate)


def set_statutory(
    conn: sqlite3.Connection,
    placement_id: str,
    layer_id: str,
    statutory: bool,
    limit_cents: int | None = None,
) -> Diagnostics:
    """Mark a layer statutory (WC Part A: benefits, NO dollar limit — the
    model forces limit == 0 and the renderer draws it off-scale), or back to
    a dollar-limited layer, which REQUIRES the limit to restore. Field-level
    writes are sync's established practice (update_layer does the same);
    towerkit's validator still gates the result.

    Grant, 2026-08-19: 'fully built but not accessible' — statutory handling
    existed everywhere except as something a browser could change."""

    def mutate(program: Program) -> None:
        layer = _find_layer(program, layer_id)
        if statutory:
            # THE FULL RESET, matching towerkit's own edit.set_statutory:
            # statutory cover attaches at nothing and follows nothing —
            # leaving either set makes the validator refuse
            # (statutory-attach / statutory-follows) with a message that
            # never names the toggle to clear (fresh-eyes review, phase 3).
            layer.statutory = True
            layer.limit = 0
            layer.attach = 0
            layer.follows_underlying = False
        else:
            if limit_cents is None:
                raise ValueError(
                    f"{layer.name}: leaving statutory needs the dollar limit "
                    "that replaces it"
                )
            layer.statutory = False
            layer.limit = _require_dollars(limit_cents, "limit")

    return _mutate(conn, placement_id, mutate)


def link_policy(
    conn: sqlite3.Connection, placement_id: str, layer_id: str, other_id: str
) -> Diagnostics:
    """Put two layers on ONE issued policy — WC Part A and Part B being the
    case it exists for, since the model cannot make them one layer.

    towerkit's `edit.link_policy` owns the rule (a group joins rather than
    being assigned, and two populated policies refuse to merge silently); this
    is the wrapper that runs it inside bookkit's write-through, so the write is
    validated, snapshotted and revertible like every other program edit.
    """

    def mutate(program: Program) -> None:
        from towerkit import edit

        edit.link_policy(program, layer_id, other_id)

    return _mutate(conn, placement_id, mutate)


def unlink_policy(
    conn: sqlite3.Connection, placement_id: str, layer_id: str
) -> Diagnostics:
    """Take ONE layer off its policy. The rest of the group keeps its token —
    a policy does not stop being one because a part left it."""

    def mutate(program: Program) -> None:
        from towerkit import edit

        edit.unlink_policy(program, layer_id)

    return _mutate(conn, placement_id, mutate)


def policy_partners(
    conn: sqlite3.Connection, placement_id: str, layer_id: str
) -> list[tuple[str, str]]:
    """(id, name) of the OTHER layers on this layer's policy, file order.

    Read-only, and the thing a surface actually needs: the token is machine
    minted and no screen should ever print it. Empty when the layer is not
    linked.
    """
    from towerkit import edit

    program = linked_program(conn, placement_id).program
    if program is None:
        return []
    layer = next((ly for ly in program.layers if ly.id == layer_id), None)
    if layer is None or not layer.policy_group:
        return []
    return [
        (other.id, other.name)
        for other in edit.policy_group_members(program, layer.policy_group)
        if other.id != layer_id
    ]


def set_follows_underlying(
    conn: sqlite3.Connection, placement_id: str, layer_id: str, follows: bool
) -> Diagnostics:
    """A follows-underlying layer's attachment is DERIVED — write_through's
    heal_follows recomputes it from the layers below on every write, so
    turning this on hands the attachment to the tower and turning it off
    freezes whatever the heal last computed."""
    from towerkit.edit import set_follows_underlying as edit_set_follows

    def mutate(program: Program) -> None:
        _find_layer(program, layer_id)
        edit_set_follows(program, layer_id, follows)

    return _mutate(conn, placement_id, mutate)


def remove_layer(
    conn: sqlite3.Connection, placement_id: str, layer_id: str
) -> Diagnostics:
    """Remove one layer, ITS SEATS WITH IT (D2, 2026-08-19 — before this no
    bookkit surface could take off a mis-added layer at all).

    towerkit's validator still gates the result: removing a middle layer
    strands the one above it over a gap, and that refusal — in towerkit's
    words, with nothing written — is exactly the answer a surface should
    show. `_find_layer` runs first so an unknown id refuses with the re-sync
    hint instead of towerkit's KeyError, which _mutate would not catch."""

    def mutate(program: Program) -> None:
        _find_layer(program, layer_id)
        edit_remove_layer(program, layer_id)

    return _mutate(conn, placement_id, mutate)


def add_participant(
    conn: sqlite3.Connection,
    placement_id: str,
    layer_id: str,
    carrier: str,
    share_bps: int,
) -> Diagnostics:
    """A market bound onto a layer at a share. Over-signing is refused by
    towerkit's validator and nothing is written."""
    from towerkit.model import Participant as TkParticipant

    def mutate(program: Program) -> None:
        layer = _find_layer(program, layer_id)
        for existing in layer.participants:
            if existing.carrier == carrier:
                raise ValueError(f"{carrier} is already on {layer.name}")
        layer.participants.append(TkParticipant(carrier=carrier, share_bps=share_bps))

    return _mutate(conn, placement_id, mutate)


def update_participant(
    conn: sqlite3.Connection,
    placement_id: str,
    layer_id: str,
    carrier: str,
    share_bps: int | None = None,
    new_carrier: str | None = None,
) -> Diagnostics:
    """Correct a market's seat in place — its share, its name, or both.

    IN PLACE, never remove-then-add. The two writes are separate mutations
    with a validator run between them: the intermediate state is a layer short
    of its share, and a refusal on the second half leaves it that way. The same
    reasoning team assignments are corrected in place rather than re-scoped
    (CLAUDE.md).

    Renaming onto a carrier already seated here is refused: two rows for one
    market on one layer double-count its share, and towerkit's validator has
    no reason to object because the arithmetic still totals correctly.
    """

    def mutate(program: Program) -> None:
        layer = _find_layer(program, layer_id)
        seat = next((p for p in layer.participants if p.carrier == carrier), None)
        if seat is None:
            seated = ", ".join(p.carrier for p in layer.participants) or "nobody"
            raise ValueError(
                f"{carrier} is not on {layer.name} — {seated} is"
            )
        if new_carrier is not None and new_carrier != carrier:
            if any(p.carrier == new_carrier for p in layer.participants):
                raise ValueError(f"{new_carrier} is already on {layer.name}")
            seat.carrier = new_carrier
        if share_bps is not None:
            seat.share_bps = share_bps

    return _mutate(conn, placement_id, mutate)


def remove_participant(
    conn: sqlite3.Connection, placement_id: str, layer_id: str, carrier: str
) -> Diagnostics:
    """Take a market off a layer. THE LAYER SURVIVES.

    Removing the last participant leaves the layer unplaced — towerkit's own
    'To be placed', which is a state and not an absence. Deleting the layer
    because its last market fell away would destroy the tower's shape, and the
    shape is the thing the tower exists to record.
    """

    def mutate(program: Program) -> None:
        layer = _find_layer(program, layer_id)
        seat = next((p for p in layer.participants if p.carrier == carrier), None)
        if seat is None:
            seated = ", ".join(p.carrier for p in layer.participants) or "nobody"
            raise ValueError(f"{carrier} is not on {layer.name} — {seated} is")
        layer.participants.remove(seat)

    return _mutate(conn, placement_id, mutate)


def set_applies_to(
    conn: sqlite3.Connection, placement_id: str, layer_id: str, line_ids: list[str]
) -> Diagnostics:
    """Re-scope a layer onto a different set of lines, through towerkit.edit.

    The KeyError conversion is load-bearing: `edit.set_applies_to` raises
    KeyError for an unknown line, and `_mutate` folds only ValueError and
    WriteConflict into diagnostics — so without this the refusal escaped as an
    unhandled exception and reached the browser as a 500 instead of a message
    naming the line.
    """
    from towerkit.edit import set_applies_to as edit_set_applies_to

    def mutate(program: Program) -> None:
        try:
            edit_set_applies_to(program, layer_id, list(line_ids))
        except KeyError as exc:
            raise ValueError(str(exc).strip("\"'")) from exc

    return _mutate(conn, placement_id, mutate)


# --- the derived field seam ----------------------------------------------------
#
# Everything above this line is a HAND-WRITTEN wrapper: one bookkit function per
# towerkit verb, each naming its own arguments. That shape is right for the
# structural verbs (adding a line means adding a pending layer too; removing one
# cascades) — the wrapper is where bookkit's own rule lives.
#
# It is the WRONG shape for a plain scalar. towerkit publishes every writable
# field as data (`mcpsurface.SURFACE`, derived from the models at import time)
# and funnels every scalar write through one choke point (`edit.set_field`,
# where the conditional guards and the normalisers live). Seventeen fields
# arrived in D6 with no way to reach them from a browser, and seventeen more
# hand-written wrappers would have been seventeen places to edit the day
# towerkit grows an eighteenth — which is exactly the drift the field ledger in
# web/parity.py exists to catch, and which it caught only AFTER five fields had
# already shipped unreachable. So the scalar path is derived: one function, over
# whatever towerkit says is writable today.


def _addressed(
    program: Program, kind: str, target: str | None, index: int | None
) -> Any:
    """The row a (kind, target, index) address names.

    `mcpsurface.TARGET` says which halves of the address each kind needs;
    towerkit's `edit._entity` then resolves it, raising KeyError for an unknown
    id and IndexError for a bad position — neither of which `_mutate` catches,
    so both would escape as a 500. This resolves the same address out of
    bookkit's OWN finders, which raise the ordinary ValueError refusal instead.

    It is assembled from `_find_layer` / `_find_line` / `_check_index` rather
    than written fresh, so it is not a second definition of "which row" — and
    `tests/test_towerfields.py` pins it against `edit._entity` for every kind
    towerkit publishes, so a change to towerkit's addressing turns this red
    rather than letting the two drift.
    """
    needs = mcpsurface.TARGET.get(kind)
    if needs is None:
        raise ValueError(f"{kind!r} is not a towerkit record kind")
    if "target" in needs and not target:
        raise ValueError(f"a {kind} is addressed by id; none was given")
    if "index" in needs and index is None:
        raise ValueError(f"a {kind} is addressed by position; none was given")

    if kind == "program":
        return program
    if kind == "line":
        _find_line(program, str(target))
        return next(line for line in program.lines if line.id == str(target))
    if kind == "layer":
        return _find_layer(program, str(target))
    if kind == "retention":
        _check_index(list(program.retentions), int(index or 0), "retention")
        return program.retentions[int(index or 0)]
    if kind == "sublimit":
        _check_index(list(program.sublimits), int(index or 0), "sublimit")
        return program.sublimits[int(index or 0)]
    layer = _find_layer(program, str(target))
    if kind == "participant":
        _check_index(list(layer.participants), int(index or 0), "market")
        return layer.participants[int(index or 0)]
    if kind == "named_limit":
        _check_index(list(layer.named_limits), int(index or 0), "named limit")
        return layer.named_limits[int(index or 0)]
    raise ValueError(f"{kind!r} is not a towerkit record kind")


def tower_field_value(
    program: Program, kind: str, name: str, target: str | None = None,
    index: int | None = None,
) -> Any:
    """What a field currently holds, addressed the same way a write is.

    Reads go through `Entry.path` rather than the wire name because pydantic
    accepts the PYTHON name and towerkit's wire name is the alias —
    `getattr(layer, 'policyNumber')` raises. An absent container reads as None
    (there is no render block, so `showTotals` has no value), which is what the
    display cell renders as an em-dash and what the write path then
    materialises from defaults.
    """
    entry = mcpsurface.resolve(kind, name)
    value: Any = _addressed(program, kind, target, index)
    for step in entry.path:
        if value is None:
            return None
        value = getattr(value, step)
    return value


def set_tower_field(
    conn: sqlite3.Connection,
    placement_id: str,
    kind: str,
    name: str,
    value: Any,
    target: str | None = None,
    index: int | None = None,
) -> Diagnostics:
    """Set ONE towerkit field, over the whole derived surface.

    `value` has already been through `towerfields.to_wire`, so it is the type
    the model holds (whole dollars for money, a real bool, a list for states) —
    the money boundary and the typed-text lexicon are that module's job, and
    doing either here would put a second copy of the cents rule inside the
    write path.

    A MISSING PARENT is materialised from its defaults, on the same terms the
    MCP server uses: only if the model can build one with no invented data
    (`RenderSettings()` can; `Period` needs both dates and so is refused with
    the sentence naming them), and only through `edit.set_container`, because
    `program.render` is on towerkit's denylist precisely so no surface writes
    one with a bare setattr. The note saying WHICH defaults were written rides
    back as a warning: the caller is now answerable for values they never sent.
    """
    entry = mcpsurface.resolve(kind, name)
    created: list[str] = []

    def mutate(program: Program) -> None:
        entity = _addressed(program, kind, target, index)
        if entry.container is not None and getattr(entity, entry.path[0]) is None:
            if not mcpsurface.container_defaultable(entry):
                raise ValueError(
                    mcpsurface.missing_container_message(
                        entry, mcpsurface.subject(kind, target, index)
                    )
                )
            container, note = mcpsurface.create_container(entry)
            edit_set_container(
                program, kind, entry.field, container,
                mcpsurface.edit_address(entry, target, index), index,
            )
            created.append(note)
        mcpsurface.apply(program, entry, target, index, value)

    diags = _mutate(conn, placement_id, mutate)
    for note in created:
        if diags.ok:
            diags.warn("container-created", note)
    return diags


def add_named_limit(
    conn: sqlite3.Connection, placement_id: str, layer_id: str,
    name: str, amount_cents: int,
) -> Diagnostics:
    """One coordinate limit on a layer — the several figures a policy states
    where `limit` states one. Appended, never sorted: order is the file's order
    and the file's order is display order (towerkit.edit.add_named_limit)."""

    def mutate(program: Program) -> None:
        _find_layer(program, layer_id)
        if not name.strip():
            raise ValueError("a named limit needs a name")
        edit_add_named_limit(
            program, layer_id, name.strip(), _require_dollars(amount_cents, "amount")
        )

    return _mutate(conn, placement_id, mutate)


def remove_named_limit(
    conn: sqlite3.Connection, placement_id: str, layer_id: str, index: int
) -> Diagnostics:
    def mutate(program: Program) -> None:
        layer = _find_layer(program, layer_id)
        _check_index(list(layer.named_limits), index, "named limit")
        edit_remove_named_limit(program, layer_id, index)

    return _mutate(conn, placement_id, mutate)


def named_limits_of(
    conn: sqlite3.Connection, placement_id: str, layer_id: str
) -> list[dict[str, Any]]:
    """A layer's coordinate limits as the panel reads them: cents, because
    every money value bookkit HANDS a surface is cents (CLAUDE.md) and the
    file's whole dollars are towerkit's side of the boundary."""
    linked = linked_program(conn, placement_id)
    if linked.program is None:
        return []
    for layer in linked.program.layers:
        if layer.id == layer_id:
            return [
                {
                    "index": i,
                    "name": nl.name,
                    "amount_cents": dollars_to_cents(nl.amount),
                }
                for i, nl in enumerate(layer.named_limits)
            ]
    return []

@dataclass(frozen=True)
class LinkedProgram:
    """A placement's towerkit file, loaded — or the reason it is not.

    ONE SEAM FOR EVERY READ. Five functions below used to open the file
    themselves behind `except Exception: return []`, so "this file will not
    load" and "this program has nothing in it" arrived at the surfaces as the
    same value, and the surfaces printed the second. On 2026-08-20 that turned
    a moved towerkit checkout into five programs the web swore were empty,
    while the TUI's tower preview — the one reader that prints its exception
    (tui/widgets/tower_preview.py) — rendered them correctly and made the bug
    look like a ghost. Every read now goes through here, and `error` is a
    sentence the surfaces are expected to PRINT.

    `moved_from` is set when programpath recovered the file somewhere other
    than the stored row says. The read succeeds; the surfaces say so, because
    a file answering from a path the database does not record is how one
    program quietly ends up serving two placements.
    """

    path: Path | None
    program: Program | None
    error: str | None = None
    moved_from: str | None = None

    @property
    def linked(self) -> bool:
        """The placement CLAIMS a file — true even when it cannot be read.
        The surfaces need this to tell 'no program yet' (offer to scaffold
        one) from 'a program that will not open' (say why)."""
        return self.path is not None or self.error is not None


def linked_program(conn: sqlite3.Connection, placement_id: str) -> LinkedProgram:
    """Open a placement's program file, saying what happened either way."""
    placement = placements.get(conn, placement_id)
    return linked_program_for(conn, placement.program_path)


def linked_program_for(conn: sqlite3.Connection, stored: str | None) -> LinkedProgram:
    if not stored:
        return LinkedProgram(None, None)
    where = programpath.resolve(conn, stored)
    if where.path is None:
        return LinkedProgram(None, None, error=where.error)
    try:
        program = load_program(where.path)
    except Exception as exc:
        # The message towerkit gives is the useful half — a pydantic
        # ValidationError names the field and the value. Prefix it with the
        # file, because a reader looking at a panel has no other way to know
        # WHICH file refused.
        return LinkedProgram(
            where.path,
            None,
            error=f"{where.path.name} will not load: {exc}",
            moved_from=where.moved_from,
        )
    return LinkedProgram(where.path, program, moved_from=where.moved_from)


class ProgramFileMissing(Exception):
    """The placement claims a file and nothing on disk answers to it.

    Raised rather than returned wherever a WRITE is about to happen: a writer
    that treats "no file" as "empty program" would create the file fresh and
    silently drop every layer in it. The message is programpath's — which
    path was tried, and what to run — because a bare "file not found" tells a
    broker nothing about a path they never typed.
    """


def program_file(conn: sqlite3.Connection, placement: Placement) -> Path:
    """Where this placement's towerkit file is right now.

    THE STORED VALUE IS NOT THE PATH. It is relative to a program root, or
    absolute and possibly stale, and programpath owns turning it into a real
    location (including recovering a tree that moved wholesale). Reaching for
    `Path(placement.program_path)` directly is what broke on 2026-08-20 and is
    banned by tests/test_conventions.py.
    """
    if not placement.program_path:
        raise ProgramFileMissing(f"{placement.ref}: no program file linked")
    where = programpath.resolve(conn, placement.program_path)
    if where.path is None:
        raise ProgramFileMissing(where.error or f"{placement.program_path}: not found")
    return where.path


def program_file_or_none(conn: sqlite3.Connection, placement: Placement) -> Path | None:
    """`program_file` for readers that must render regardless."""
    try:
        return program_file(conn, placement)
    except ProgramFileMissing:
        return None


def program_load_error(conn: sqlite3.Connection, placement_id: str) -> str | None:
    """The one-line reason this placement's program will not open, or None."""
    return linked_program(conn, placement_id).error


def layer_details(conn: sqlite3.Connection, placement_id: str) -> list[dict[str, Any]]:
    """The linked program's layers with everything the edit form needs, plus
    the carrier panel on each; money in cents (bookkit-native).

    THE PANEL IS NOT AN EXTRA READ. It comes off the `program` already loaded
    here, so this stays ONE file open per call — which matters, because the
    account page was deliberately reduced to a single layer_details call per
    render (web/routes/account.py) and a per-layer load would undo that
    silently.

    Participants carry `share_pct`, not bps, on purpose: `signed_pct` sits in
    the same dict, and two share units in one payload is the cents-vs-dollars
    mistake in miniature — a reader comparing a participant to the layer total
    would be off by a hundred. A share of an unknown premium is None, never
    zero, the same rule the projection applies.

    An unplaced layer is an EMPTY list, never a missing key: "nobody is on it"
    and "we did not look" are different facts and towerkit prints the first as
    'To be placed'."""
    return layer_details_of(linked_program(conn, placement_id).program)


def layer_details_of(program: Program | None) -> list[dict[str, Any]]:
    """`layer_details` without the I/O, so a caller that already holds the
    parsed program (the web memoises one LinkedProgram per placement per
    render) does not open and re-parse the same file to get the rows."""
    if program is None:
        return []
    out: list[dict[str, Any]] = []
    for layer in program.layers:
        out.append(
            {
                "id": layer.id,
                "name": layer.name,
                "policy_number": layer.policy_number,
                "attach_cents": dollars_to_cents(layer.attach),
                "limit_cents": dollars_to_cents(layer.limit),
                "premium_cents": (
                    dollars_to_cents(layer.premium) if layer.premium is not None else None
                ),
                "period_from": layer.period.start.isoformat() if layer.period else None,
                "period_to": layer.period.end.isoformat() if layer.period else None,
                "signed_pct": layer.signed_bps / 100,
                "applies_to": list(layer.applies_to),
                # WC Part A carries statutory benefits and NO dollar limit, so
                # towerkit forces limit == 0 (model.py:98) and draws it off-scale.
                # Without this flag a reader of these dicts cannot tell "unlimited
                # cover" from "a layer whose limit happens to be zero" — the two
                # are opposite facts that arithmetic alone renders identical.
                "statutory": layer.statutory,
                # A buffer is drawn and counted differently from cover, so the surface
                # needs it on the row rather than re-opening the file to ask.
                "buffer": layer.buffer,
                "follows_underlying": layer.follows_underlying,
                "participants": [
                    {
                        "carrier": part.carrier,
                        "share_pct": part.share_bps / 100,
                        "premium_cents": (
                            dollars_to_cents(premium_share(layer.premium, part.share_bps))
                            if layer.premium is not None
                            else None
                        ),
                    }
                    for part in layer.participants
                ],
            }
        )
    return out


def program_terms(
    conn: sqlite3.Connection, placement_id: str
) -> dict[str, list[dict[str, Any]]]:
    """The tower's terms — retentions and sublimits — with everything their
    editors need; money in cents (bookkit-native), applies_to as line ids.
    Index order is towerkit's own addressing for these (they carry no ids),
    and every web write re-renders the panel so an index is never stale."""
    return program_terms_of(linked_program(conn, placement_id).program)


def program_terms_of(program: Program | None) -> dict[str, list[dict[str, Any]]]:
    """`program_terms` without the I/O — see `layer_details_of`."""
    empty: dict[str, list[dict[str, Any]]] = {"retentions": [], "sublimits": []}
    if program is None:
        return empty
    return {
        "retentions": [
            {
                "index": i,
                "type": str(r.type),
                "amount_cents": dollars_to_cents(r.amount),
                "applies_to": list(r.applies_to),
                # D6: read so the edit form can PREFILL it. A note the form
                # cannot see is a note the form silently clears on the next
                # save, which is worse than not offering the field at all.
                "notes": r.notes,
            }
            for i, r in enumerate(program.retentions)
        ],
        "sublimits": [
            {
                "index": i,
                "name": s.name,
                "amount_cents": dollars_to_cents(s.amount),
                "applies_to": list(s.applies_to),
                "notes": s.notes,
            }
            for i, s in enumerate(program.sublimits)
        ],
    }


def line_labels(program_path: str | None, conn: sqlite3.Connection | None = None) -> str:
    """Compact lines-of-cover label ("GL, AL, EL") straight from the file —
    what cover the placement actually is, for the attention tables. Empty
    string when unlinked or unreadable (never raises: home must render).

    `conn` is optional ONLY because this is called from renderers that hold a
    path and nothing else; pass it wherever you have it, or a program_path
    stored relative to a root cannot be resolved and every attention row
    silently loses its lines of cover. The attention tables are the one place
    where an empty label is genuinely survivable — a row that says "PLC-0006"
    with no lines is still a row, and the panel that owns the file says why."""
    program = _program_at(program_path, conn)
    if program is None:
        return ""
    return ", ".join(line.label for line in program.lines)


def _program_at(
    program_path: str | None, conn: sqlite3.Connection | None
) -> Program | None:
    """Load by stored path for the two callers that have no placement id.

    With a connection the stored value goes through programpath (relative to a
    root, and recovered when the tree moved); without one it can only be tried
    as written, which is what it was before roots existed."""
    if not program_path:
        return None
    if conn is not None:
        return linked_program_for(conn, program_path).program
    try:
        return load_program(Path(program_path))
    except Exception:
        return None


def line_ends(
    program_path: str | None, conn: sqlite3.Connection | None = None
) -> list[tuple[str, date]]:
    """(label, end date) per line of cover, soonest first — the date each
    LINE actually needs renewing, which is not always the program period end:
    policies are issued per layer, and a line's cover runs out when the first
    layer that applies to it expires. Lines with no layers (TBD, unplaced)
    are omitted — there is no policy to expire. Empty when unlinked or
    unreadable (never raises: home must render)."""
    program = _program_at(program_path, conn)
    if program is None:
        return []
    out: list[tuple[str, date]] = []
    for line in program.lines:
        covering = [layer for layer in program.layers if line.id in layer.applies_to]
        if not covering:
            continue
        end = min(
            layer.period.end if layer.period else program.period.end
            for layer in covering
        )
        out.append((line.label, end))
    return sorted(out, key=lambda pair: pair[1])


def program_lines(conn: sqlite3.Connection, placement_id: str) -> list[tuple[str, str]]:
    """(id, name) of the linked program's lines, for the add-layer picker."""
    return program_lines_of(linked_program(conn, placement_id).program)


def program_lines_of(program: Program | None) -> list[tuple[str, str]]:
    """`program_lines` without the I/O — see `layer_details_of`."""
    if program is None:
        return []
    return [(line.id, line.name) for line in program.lines]


def _mutate(
    conn: sqlite3.Connection, placement_id: str, mutation: Callable[[Program], None]
) -> Diagnostics:
    """write_through with ValueError/WriteConflict surfaced as diagnostics,
    so forms get one uniform result shape."""
    try:
        return write_through(conn, placement_id, mutation)
    except ValueError as exc:
        diags = Diagnostics()
        diags.error("edit", str(exc))
        return diags
    except WriteConflict as exc:
        diags = Diagnostics()
        diags.error("conflict", str(exc))
        return diags


def _find_layer(program: Program, layer_id: str) -> TkModelLayer:
    for layer in program.layers:
        if layer.id == layer_id:
            return layer
    raise ValueError(f"layer {layer_id!r} is not in this program (re-sync?)")


def _require_dollars(cents: int, label: str) -> int:
    try:
        return cents_to_dollars(cents)
    except MoneyParseError as exc:
        raise ValueError(f"{label}: {exc}") from exc


# --- write-through ------------------------------------------------------------


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
    try:
        path = program_file(conn, placement)
    except ProgramFileMissing as missing:
        # Which of the two it is matters to the reader: "no file linked" is an
        # invitation to scaffold one, "the file moved" is an invitation to run
        # relink. programpath's message already distinguishes them.
        diags.error("no-file" if not placement.program_path else "io", str(missing))
        return diags
    if not placement.source_sha256:
        # A NULL sha USED TO SHORT-CIRCUIT THE WHOLE GUARD, which inverted it:
        # a mismatched sha means "bookkit saw this file once and it has moved
        # on", a missing one means "bookkit has never verified this file at
        # all". The second is strictly less knowledge than the first, so it is
        # the case to refuse hardest, not the case to wave through. Seeded
        # books linked a path without projecting and every one of them wrote
        # through with no conflict and no diagnostic (2026-08-18). Re-projecting
        # is one non-destructive command; the alternative is an unverified
        # overwrite of a user's irreplaceable program file.
        raise WriteConflict(
            f"{path} was linked without a projection, so bookkit has never "
            f"verified its contents — run `bookctl sync` before editing it here"
        )
    if file_sha256(path) != placement.source_sha256:
        raise WriteConflict(
            f"{path} changed on disk since last projection — re-sync and retry"
        )

    program = load_program(path)
    mutation(program)
    # HEAL BEFORE VALIDATE, and before the dump that follows it. A
    # follows-underlying attachment is DERIVED state (towerkit.edit.heal_follows,
    # the highest underlying top), and towerkit's other two write paths —
    # mcpserver._write and tui EditSession.mutate — both heal here, between the
    # mutation and the check. bookkit did not, so raising a primary layer's
    # limit on any program carrying an excess layer was refused by
    # `layer-follows-attach`/`layer-follows-overlap`, naming an attachment the
    # broker never set, for a mutation towerkit itself accepts.
    #
    # The position is the whole fix. Heal AFTER validate and the same edit is
    # still refused; heal after the dump and the file on disk keeps the stale
    # attachment, so the projection and towerkit's own editor disagree about
    # where the excess sits. Healing here means one thing is validated, dumped
    # and re-projected: the healed program.
    heal_follows(program)
    check = validate_program(program)
    if not check.ok:
        return check

    dump_program(program, path)
    return project(conn, path)
