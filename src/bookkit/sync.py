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
from towerkit.model import SCHEMA_ID, Program, dump_program, load_program
from towerkit.model import Layer as TkModelLayer

# towerkit shares/premiums: dollars + bps; participant premium is floor-divided
from towerkit.money import premium_share
from towerkit.validate import Diagnostics, validate_file, validate_program

from .db import utc_now
from .models import Opportunity, Org, Placement
from .money import MoneyParseError, cents_to_dollars, dollars_to_cents
from .repo import aliases, links, opportunities, orgs, placements, projection, settings


def file_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configured_roots(conn: sqlite3.Connection) -> list[Path]:
    """Where program files live: the saved setting, falling back to the
    BOOKKIT_PROGRAM_ROOTS env var (colon-separated) for scripting."""
    import os

    saved = settings.get_program_roots(conn)
    if saved:
        return [Path(r).expanduser() for r in saved]
    raw = os.environ.get("BOOKKIT_PROGRAM_ROOTS", "")
    return [Path(r).expanduser() for r in raw.split(":") if r]


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
        program_path=str(path),
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
        and not Path(p.program_path).exists()
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
                links.confirm(conn, str(path), org_id, insured, source="insured_match")
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
        and not Path(p.program_path).exists()
    ]
    if len(matches) != 1:
        return None
    moved = matches[0]
    old_path = moved.program_path
    links.confirm(conn, str(path), moved.org_id, _insured(path), source="rename")
    if old_path is not None:
        links.forget(conn, old_path)
    placements.update(
        conn, moved.id, program_path=str(path), note=f"file renamed from {old_path}"
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
    links.confirm(conn, str(path), org_id, _insured(path))
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
        if (line.id, effective) in existing:
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
        path = Path(placement.program_path)
        if not path.exists():
            diags.error("io", f"{path}: linked file is missing — fix the link first")
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
        links.confirm(conn, str(new_path), placement.org_id, clone.insured, source="renewal")
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
    links.confirm(conn, str(dest), org.id, org.name, source="scaffold")
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
    bind."""
    from towerkit.model import Layer as TkLayer

    def mutate(program: Program) -> None:
        layer_id = _slug(name, {ly.id for ly in program.layers})
        program.layers.append(
            TkLayer(
                id=layer_id,
                name=name,
                applies_to=line_ids,
                attach=_require_dollars(attach_cents, "attach"),
                limit=_require_dollars(limit_cents, "limit"),
                premium=(
                    _require_dollars(premium_cents, "premium")
                    if premium_cents is not None
                    else None
                ),
                participants=[],
            )
        )

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


def layer_details(conn: sqlite3.Connection, placement_id: str) -> list[dict[str, Any]]:
    """The linked program's layers with everything the edit form needs,
    money in cents (bookkit-native)."""
    placement = placements.get(conn, placement_id)
    if not placement.program_path:
        return []
    try:
        program = load_program(Path(placement.program_path))
    except Exception:
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
            }
        )
    return out


def line_labels(program_path: str | None) -> str:
    """Compact lines-of-cover label ("GL, AL, EL") straight from the file —
    what cover the placement actually is, for the attention tables. Empty
    string when unlinked or unreadable (never raises: home must render)."""
    if not program_path:
        return ""
    try:
        program = load_program(Path(program_path))
    except Exception:
        return ""
    return ", ".join(line.label for line in program.lines)


def line_ends(program_path: str | None) -> list[tuple[str, date]]:
    """(label, end date) per line of cover, soonest first — the date each
    LINE actually needs renewing, which is not always the program period end:
    policies are issued per layer, and a line's cover runs out when the first
    layer that applies to it expires. Lines with no layers (TBD, unplaced)
    are omitted — there is no policy to expire. Empty when unlinked or
    unreadable (never raises: home must render)."""
    if not program_path:
        return []
    try:
        program = load_program(Path(program_path))
    except Exception:
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
    placement = placements.get(conn, placement_id)
    if not placement.program_path:
        return []
    try:
        program = load_program(Path(placement.program_path))
    except Exception:
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


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "layer"
    slug = base
    counter = 2
    while slug in taken:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


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
