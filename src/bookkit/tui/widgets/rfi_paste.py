"""Turn a pasted litany into items. Underwriter questions arrive as a
numbered or bulleted block in an email; typing them one form at a time is
the failure mode that kills the feature, so one line becomes one item."""

from __future__ import annotations

import re

# leading "1." / "1)" / "-" / "*" / "•" plus the space after it. The trailing
# \s+ is required: "2026 payroll figures" is a question, not item 2026.
_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")


def split_items(text: str) -> list[str]:
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        cleaned = _MARKER.sub("", line).strip()
        if cleaned:
            out.append(cleaned)
    return out
