"""Messy headers → canonical field keys. Exact key/alias match first, then
RapidFuzz; anything below the bar lands in `unmapped` — surfaced to the user,
never silently dropped (spec rule)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from .fieldspec import FieldSpec
from .readers import RawTable

_FUZZ_THRESHOLD = 85
_NORM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Mapping:
    assigned: dict[str, str]  # original header → canonical key
    unmapped: list[str]  # original headers with no home


def _norm(text: str) -> str:
    return _NORM_RE.sub(" ", text.lower()).strip()


def map_headers(headers: list[str], specs: tuple[FieldSpec, ...]) -> Mapping:
    hints: dict[str, str] = {}  # normalized hint → canonical key
    for spec in specs:
        for hint in (spec.key, *spec.aliases):
            hints[_norm(hint)] = spec.key
    assigned: dict[str, str] = {}
    claimed: set[str] = set()
    unmapped: list[str] = []
    for header in headers:
        key = _match(_norm(header), hints)
        if key is None or key in claimed:  # first claim wins, left to right
            unmapped.append(header)
            continue
        assigned[header] = key
        claimed.add(key)
    return Mapping(assigned, unmapped)


def _match(header: str, hints: dict[str, str]) -> str | None:
    if not header:
        return None
    exact = hints.get(header)
    if exact is not None:
        return exact
    best_key, best_score = None, float(_FUZZ_THRESHOLD)
    for hint, key in hints.items():
        # token_sort (not token_set): every token counts, so a header that
        # merely CONTAINS a canonical word can't hijack the field
        score = fuzz.token_sort_ratio(header, hint)
        if score > best_score:
            best_key, best_score = key, score
        elif score == best_score and best_key is not None and key != best_key:
            best_key = None  # tie between different keys → refuse to guess
    return best_key


def apply_mapping(table: RawTable, mapping: Mapping) -> list[dict[str, object]]:
    """Canonical-keyed rows, order preserved; unmapped columns fall away here
    (they were already surfaced in Mapping.unmapped)."""
    out: list[dict[str, object]] = []
    for raw in table.rows:
        row: dict[str, object] = {}
        for header, value in raw.items():
            key = mapping.assigned.get(str(header))
            if key is not None:
                row[key] = value
        out.append(row)
    return out
