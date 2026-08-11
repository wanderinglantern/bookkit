"""Human date entry: today, tomorrow, fri, +2w, 15 oct, 2026-10-15.

Absolute forms ride on towerkit's Dateparser wrapper; bookkit adds the +Nd/w/m/y
relative shorthand because renewal work speaks in offsets. DATE columns store
the result as YYYY-MM-DD text and never touch a timezone.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from towerkit.dates import parse_flexible_date

_RELATIVE_RE = re.compile(r"^\+(\d+)\s*([dwmy])$")


def _add_months(d: date, months: int) -> date:
    month0 = d.month - 1 + months
    year, month = d.year + month0 // 12, month0 % 12 + 1
    # clamp: Jan 31 +1m → Feb 28/29
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def parse_human_date(text: str, today: date | None = None) -> date | None:
    """Parse a human-entered date; None when it cannot be understood."""
    text = text.strip().lower()
    if not text:
        return None
    today = today or date.today()
    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    match = _RELATIVE_RE.match(text)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        if unit == "d":
            return today + timedelta(days=n)
        if unit == "w":
            return today + timedelta(weeks=n)
        if unit == "m":
            return _add_months(today, n)
        return _add_months(today, 12 * n)
    return parse_flexible_date(text)


def days_until(target: str, today: date | None = None) -> int:
    """Days from today to a stored YYYY-MM-DD date; negative when past."""
    return (date.fromisoformat(target) - (today or date.today())).days
