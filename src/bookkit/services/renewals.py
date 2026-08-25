"""Renewal pipeline: what expires when, bucketed for the home screen.

Every program of every client is scanned — a client with Property AND
Casualty shows both — and a program that EXPIRED without being renewed does
not fall off the radar: it surfaces as overdue until a successor placement
exists (or it's marked lapsed, the deliberate let-it-go)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from ..dates import days_until
from ..models import Org, Placement
from ..repo import orgs, placements
from ..sync import _bump_years  # renew-at-birth's own year-bump naming rule

BUCKETS = ((0, 30), (31, 60), (61, 90), (91, 120))
OVERDUE = "overdue"


@dataclass(frozen=True)
class RenewalItem:
    """ONE RENEWAL EVENT — not one placement.

    A program is not a renewal. Its policies are, and they do not all expire
    together: an Inland Marine layer can run out months before the program
    period does. Until 2026-08-21 this was one row per PLACEMENT and the
    attention tables printed the program's whole cover label beside a single
    countdown, so a broker reading "2025 Casualty Program · GL, AL, IM · 70d
    over" could not tell WHICH of the three was overdue — and on an unlinked
    placement, where no lines are known at all, the tables printed the program
    NAME in the cover column as if a name were cover (Grant, 2026-08-21).

    So the unit is now the DATE something runs out, per placement:

    * lines that end on the SAME day are one event and share a row — five
      identical dates for one tower is noise, not precision;
    * lines that end on different days get a row each, with their own date and
      their own countdown;
    * a placement whose lines are unknown (no file linked, or unreadable) gets
      exactly one row with `line` empty, and the surfaces say so in words
      rather than substituting the program name.

    `renewal_on` and `days_remaining` belong to THIS row and are computed from
    each other. Print the date you counted to — the four-surface bug
    (2026-08-15) was a date twenty days in the future rendering red as "70d
    over" because one came from the item and the other from placement.period_to.
    """

    placement: Placement
    org: Org
    days_remaining: int  # negative when overdue — counted to renewal_on
    bucket: str  # 'overdue' | '0-30' | '31-60' | '61-90' | '91-120'
    lines: str = ""  # the PROGRAM's whole cover label ("GL, AL, EL"); "" unlinked
    # per-line renewal dates (label, iso end), soonest first — policies are
    # issued per layer, so a LINE can run out before its program does
    line_ends: tuple[tuple[str, str], ...] = ()
    # the date days_remaining counts to: this row's own line end when the
    # placement is file-linked, else the program period end
    renewal_on: str = ""
    # WHAT RUNS OUT ON `renewal_on` — the labels of the lines ending that day
    # ("IM", or "GL, AL, EL" when they share the date). EMPTY when the
    # placement has no file and the book therefore does not know its lines;
    # that is a fact worth printing, and it is not the program's name.
    line: str = ""

    @property
    def cover(self) -> str:
        """The label an attention row prints under COVER, or "" when unknown.

        A single accessor because five surfaces ask the same question and one
        of them used to answer it with `placement.program_name` — the bug this
        class was reshaped to remove. There is no fallback here on purpose: a
        caller with "" must say "not known", never substitute something else.
        """
        return self.line

    @property
    def key(self) -> str:
        """A stable row key for THIS renewal event.

        It carries the date, and that is the whole point: a placement can now
        put several rows on one table, and both the TUI's Today pane and the
        navigator's attention tables keyed theirs by `placement.id` alone.
        Textual raises DuplicateKey on the second one, which aborted the rest
        of the screen build — Today came up with an empty renewals pane AND an
        empty stale pane, because the exception fired before either was filled
        (2026-08-21). Derived here so a third table cannot invent a fourth
        spelling of the same key.
        """
        return f"renewal:{self.placement.id}:{self.renewal_on}"


def _renewed(placement: Placement, others: list[Placement]) -> bool:
    """A successor exists: same program (allowing the renew year-bump in the
    name), starting on or after this period's end."""
    for other in others:
        if other.id == placement.id or other.period_from < placement.period_to:
            continue
        if other.program_name in (
            placement.program_name,
            _bump_years(placement.program_name),
        ):
            return True
    return False


def _labels(
    conn: sqlite3.Connection, program_path: str | None, cache: dict[str, str]
) -> str:
    from .. import sync

    if not program_path:
        return ""
    if program_path not in cache:
        cache[program_path] = sync.line_labels(program_path, conn)
    return cache[program_path]


def _line_ends(
    conn: sqlite3.Connection,
    program_path: str | None,
    cache: dict[str, tuple[tuple[str, str], ...]],
) -> tuple[tuple[str, str], ...]:
    """`conn` is not decoration: a program_path stored relative to a program
    root cannot be resolved without it, and an unresolved path here means an
    attention row counting down to the PROGRAM period end instead of to the
    earliest line end — the exact "twenty days in the future rendered red as
    70d over" class CLAUDE.md records."""
    from .. import sync

    if not program_path:
        return ()
    if program_path not in cache:
        cache[program_path] = tuple(
            (label, end.isoformat()) for label, end in sync.line_ends(program_path, conn)
        )
    return cache[program_path]


def renewal_on(placement: Placement, ends: Sequence[date]) -> date:
    """THE ONE DEFINITION of the date a placement first needs attention: the
    earliest line end when one is known, CAPPED by the program period end.

    Public because it was derived twice. `routes/towers.py` had its own
    `min(ends)` with no cap, so on a program whose layers are written past
    their own period — a data error, and exactly what the cap exists for — the
    Towers page said 281 days where the service said 20, and its `renewing`
    filter measured off the wrong one (2026-08-24). A layer counted to past
    its program's end pushes a renewal off the attention window instead of
    surfacing it.

    Takes dates, because the two callers hold dates: the surfaces converting
    to and from ISO around one rule is how the second copy started.
    """
    period_to = date.fromisoformat(placement.period_to)
    return min(min(ends), period_to) if ends else period_to


def _renewal_on(placement: Placement, ends: tuple[tuple[str, str], ...]) -> str:
    """`renewal_on` in this module's own ISO currency — what `next_for_org`
    and the account header print. `_events` below is what splits a placement
    into its several renewals; this is the soonest of them."""
    return renewal_on(
        placement, [date.fromisoformat(end) for _, end in ends]
    ).isoformat()


def _events(
    placement: Placement, ends: tuple[tuple[str, str], ...]
) -> list[tuple[str, str]]:
    """A placement's renewals as (date, cover label), soonest first.

    GROUPED BY DATE, deliberately. One row per line would put five identical
    dates on the screen for a tower whose lines all run to the program period
    — precision nobody asked for, obscuring the one line that genuinely runs
    out early. One row per DATE says exactly as much as is true: what runs out,
    and when.

    Each date is capped by the program period end for the same reason
    `_renewal_on` caps it: a layer written past its program's own period is a
    data error, and counting to it would push a renewal off the attention
    window rather than surfacing it.

    NO ENDS AT ALL — unlinked, unreadable, or a program whose lines carry no
    layers yet — is ONE event on the program period end with an EMPTY label.
    Empty is a fact ("the book does not know this program's lines"), and the
    surfaces print it as one; the program's name is not cover and must never
    stand in for it.
    """
    if not ends:
        return [(placement.period_to, "")]
    by_date: dict[str, list[str]] = {}
    for label, end in ends:
        by_date.setdefault(min(end, placement.period_to), []).append(label)
    return [(date, ", ".join(labels)) for date, labels in sorted(by_date.items())]


def _bucket(remaining: int) -> str:
    if remaining < 0:
        return OVERDUE
    return next(
        (f"{lo}-{hi}" for lo, hi in BUCKETS if lo <= remaining <= hi),
        f"{BUCKETS[-1][0]}-{BUCKETS[-1][1]}",
    )


def upcoming(
    conn: sqlite3.Connection, today: date | None = None, days: int = 120
) -> list[RenewalItem]:
    """Placements needing renewal attention within `days` — plus overdue
    unrenewed ones — soonest first, with bucket labels.

    Attention is counted to the earliest LINE end, not the program end: the
    scan covers every live placement because an Inland Marine layer can run
    out months before its program period does, and it must surface here the
    moment ITS window opens."""
    today = today or date.today()
    horizon_iso = (today + timedelta(days=days)).isoformat()
    by_org: dict[str, list[Placement]] = {}
    label_cache: dict[str, str] = {}
    ends_cache: dict[str, tuple[tuple[str, str], ...]] = {}
    items: list[RenewalItem] = []
    for placement in placements.expiring_between(conn, "0001-01-01", "9999-12-31"):
        if placement.status == "lapsed":
            continue
        ends = _line_ends(conn, placement.program_path, ends_cache)
        events = _events(placement, ends)
        # THE RENEWED CHECK IS PER PLACEMENT, not per event, and runs once: a
        # successor placement replaces the whole program, so a renewed program
        # drops all of its events and not the earliest one only. Hoisted out of
        # the loop below because it costs a query per placement.
        renewed: bool | None = None
        for renewal_on, cover in events:
            remaining = days_until(renewal_on, today)
            if remaining >= 0 and renewal_on > horizon_iso:
                continue
            if remaining < 0:
                if renewed is None:
                    others = by_org.setdefault(
                        placement.org_id,
                        placements.for_org(conn, placement.org_id),
                    )
                    renewed = _renewed(placement, others)
                if renewed:
                    continue
            items.append(
                RenewalItem(
                    placement, orgs.get(conn, placement.org_id), remaining,
                    _bucket(remaining),
                    _labels(conn, placement.program_path, label_cache),
                    ends, renewal_on, cover,
                )
            )
    # Soonest first, then by account and program, so the several events of one
    # program sit together when they share a bucket rather than interleaving
    # with everybody else's.
    return sorted(
        items,
        key=lambda item: (item.days_remaining, item.org.name, item.placement.ref),
    )


def next_for_org(
    conn: sqlite3.Connection, org_id: str, today: date | None = None
) -> RenewalItem | None:
    """The client's most urgent renewal across ALL programs: the most overdue
    unrenewed one first, else the soonest upcoming expiry."""
    today = today or date.today()
    candidates = [p for p in placements.for_org(conn, org_id) if p.status != "lapsed"]
    ends_cache: dict[str, tuple[tuple[str, str], ...]] = {}
    live: list[tuple[int, Placement, tuple[tuple[str, str], ...], str]] = []
    for placement in candidates:
        ends = _line_ends(conn, placement.program_path, ends_cache)
        renewal_on = _renewal_on(placement, ends)
        remaining = days_until(renewal_on, today)
        if remaining < 0 and _renewed(placement, candidates):
            continue
        live.append((remaining, placement, ends, renewal_on))
    if not live:
        return None
    remaining, placement, ends, renewal_on = min(live, key=lambda entry: entry[0])
    # The cover label of THE EVENT this date belongs to, not the program's whole
    # label: the account header and the book's row print one date, and it is
    # the date this cover runs out. Reading it off `_events` rather than
    # recomposing it is what stops the header and the attention table saying
    # two different things about the same renewal.
    cover = next(
        (label for date, label in _events(placement, ends) if date == renewal_on),
        "",
    )
    return RenewalItem(
        placement, orgs.get(conn, org_id), remaining, _bucket(remaining),
        _labels(conn, placement.program_path, {}), ends, renewal_on, cover,
    )


def bucketed(
    conn: sqlite3.Connection, today: date | None = None, days: int = 120
) -> dict[str, list[RenewalItem]]:
    out: dict[str, list[RenewalItem]] = {OVERDUE: []}
    out.update({f"{lo}-{hi}": [] for lo, hi in BUCKETS})
    for item in upcoming(conn, today, days):
        out[item.bucket].append(item)
    return out
