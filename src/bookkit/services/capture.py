"""Quick capture support: spot follow-up phrases in a note and OFFER a task —
never silently create one (§6.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..dates import parse_human_date

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
