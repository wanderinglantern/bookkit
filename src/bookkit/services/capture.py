"""Quick capture support, shared by every surface that logs an interaction:
resolve "who was there" against the account's roster (refusing rather than
guessing), and spot follow-up phrases in a note to OFFER a task — never
silently create one (§6.2).

Attendee resolution lived in the TUI's QuickCapture widget until 2026-08-20,
when the web grew its own capture form: two copies of a fuzzy threshold are
two thresholds the moment either is tuned, so the loop moved here and both
surfaces call it (the same reason batches.open_batch lives in services/)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz, utils

from ..dates import parse_human_date
from ..repo import contacts

# rapidfuzz WRatio, out of 100, over default_process — which lowercases and
# strips punctuation. WITHOUT it "rosa" scores 73 against "Rosa Delgado" and
# 90 with a capital R, so a name typed in a hurry would be refused for its
# case. "delgado", "Rosa D" and the full name all score >= 85; a name off this
# account scores 45. High enough that a typo is refused and named rather than
# resolved into the wrong person's file.
ATTENDEE_MATCH = 80
# ...and how far clear of the runner-up the winner has to be. An EXACT tie is
# the rare case (two people called Chen); the common one on a real account is
# two similar-but-different names, where the winner leads by a couple of
# points and the loser is a different human being. Measured, WRatio over
# default_process:
#     "J Smith"            Jon Smith 87.5 / Jonathan Smith 85.5   gap  2.0
#     "Michel Brennan"     Michael 96.6   / Michelle 93.3         gap  3.2
#     "Rosa Delgado-Vance" Rosa Delgado 90.0 / Robert D-V 84.2    gap  5.8
# — every one of those picked the wrong person silently. Against that, when
# the full name IS typed the gap is never small: the tightest pair of
# genuinely distinct names measured is Michael vs Michelle Brennan at 9.7,
# and typing either in full leads by that much. 8 sits in the empty band
# between the two populations, on the cautious side of it: everything that
# guessed wrong is refused, and a name typed out in full still resolves.
ATTENDEE_MARGIN = 8


def resolve_attendees(
    conn: sqlite3.Connection, org_id: str, typed: str
) -> tuple[list[str], str | None]:
    """Comma-separated names → contact ids, or a sentence saying why not.

    Refuses rather than guesses, twice over: a name that matches nobody is a
    typo or a person who is not a contact yet, and a name two people answer to
    would otherwise put the wrong one in the room, in writing, on the client's
    file. Same rule repo/team.py enforces on member names.

    The roster is `contacts.for_org`'s: a REMOVED contact is excluded by
    `base.alive()` (removal takes them off attendee lists while the
    interaction_contact rows survive for an undelete), and the
    `active_only=True` default further drops the person who has LEFT — still
    on the account's file because their history is, and not somebody who can
    have been in the room this morning. Naming them would be a
    plausible-looking lie on the client's record, which is the one thing this
    field exists to stop."""
    roster = contacts.for_org(conn, org_id)
    ids: list[str] = []
    for raw in typed.split(","):
        name = raw.strip()
        if not name:
            continue
        scored = sorted(
            (
                (fuzz.WRatio(name, c.name, processor=utils.default_process), c)
                for c in roster
            ),
            key=lambda pair: -pair[0],
        )
        best = [(score, c) for score, c in scored if score >= ATTENDEE_MATCH]
        if not best:
            return [], (
                f"no contact on this account matches {name!r} — "
                "add them on the account first, or clear the field"
            )
        if len(best) > 1 and best[0][0] - best[1][0] < ATTENDEE_MARGIN:
            tied = " and ".join(c.name for _, c in best[:2])
            return [], f"{name!r} matches {tied} — type more of the name"
        if best[0][1].id not in ids:
            ids.append(best[0][1].id)
    return ids, None

# "follow up Tuesday", "call him next week", "check back in 2 weeks", …
_CUE_RE = re.compile(
    r"\b(follow\s*up|call( \w+)?|check\s*back|chase|circle\s*back|remind( me)?)\b"
    r"(?P<rest>[^.;\n]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskSuggestion:
    phrase: str  # the text that triggered the offer
    due_on: date


def suggest_task(text: str, today: date | None = None) -> TaskSuggestion | None:
    """Return a suggested follow-up task when the note contains a cue phrase
    with a parseable date after it."""
    today = today or date.today()
    match = _CUE_RE.search(text)
    if match is None:
        return None
    rest = match.group("rest").strip()
    if not rest:
        return None
    # try progressively shorter tails: "next week", "Tuesday", "in 2 weeks"
    words = rest.split()
    for size in range(min(4, len(words)), 0, -1):
        candidate = " ".join(words[:size])
        parsed = parse_human_date(candidate, today)
        if parsed is None and candidate.lower().startswith("in "):
            parsed = parse_human_date(candidate[3:], today)
        if parsed and parsed >= today:
            return TaskSuggestion(match.group(0).strip(), parsed)
    return None
