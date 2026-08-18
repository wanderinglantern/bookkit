"""Parses pasted RFI item text — a numbered or bulleted litany of underwriter
questions arrives as one paste in an email; typing them one form at a time is
the failure mode that kills the feature, so one line becomes one item. Pure
`re`, no framework dependency, shared by every surface (MCP tool, TUI widget)
that turns a paste into a batch of items."""

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
