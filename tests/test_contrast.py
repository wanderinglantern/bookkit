"""Contrast sweep — colour is signal in this app, so a signal you cannot see
is a defect (CLAUDE.md: "Color is signal, not decoration").

This exists because guessing failed. When Grant reported text disappearing
mid-edit, three plausible hypotheses — the cell editor's select-on-focus, the
input cursor, and the DataTable row cursor — all measured *fine*. A
brute-force sweep over every rendered line of every widget found the real one
in a single pass: OptionList paints its highlight BEHIND the prompt and does
not override an explicit foreground the way DataTable does, so every
theme.DIM span on the selected pipeline card rendered at 1.83:1 (review F28,
and F32 for this file).

The ACCEPTED list below is not a mute button: the test also fails when an
accepted pair STOPS occurring, so fixing one forces removing its entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, OptionList, Static, TextArea, Tree

from bookkit.tui.app import BookkitApp

# Below this, text stops being readable against its own background. WCAG AA
# for large text is 3.0; 2.5 is the floor at which a reviewer can still tell
# something is there, and is deliberately permissive so a failure means
# something.
FLOOR = 2.5

# Known and accepted, each tied to an open finding. Keyed by
# (widget class, foreground, background) so a pair cannot be broadened by
# accident. Remove an entry when its finding is fixed — the test will tell you.
ACCEPTED: dict[tuple[str, str, str], str] = {
    # F31 — towerkit's ASCII renderer carries its own theme, which assumes
    # towerkit's background rather than bookkit's. Not this repo's to fix;
    # the real fix is passing a theme to load_theme().
    ("TowerPreview", "#1c1c1c", "#15171c"): "F31 towerkit retention rule",
    # F30 — "chrome should whisper" (bookkit.tcss) is the right instinct, but
    # theme.RULE on theme.PANEL is inaudible rather than quiet.
    ("Static", "#3a4150", "#232733"): "F30 status-bar / account-header separators",
    ("Tree", "#333743", "#15171c"): "F30 tree guides",
    ("InlineTable", "#3a4150", "#1b1d22"): "F30 separators in a table cell",
}

# (label, keypresses) — the same journeys the snapshot suite walks
JOURNEYS: list[tuple[str, list[str]]] = [
    ("navigator", []),
    ("navigator/overdue", ["down", "down", "enter"]),
    ("navigator/tasks", ["down", "down", "down", "down", "enter"]),
    ("today", ["t"]),
    ("book", ["b"]),
    ("account/overview", ["b", "enter"]),
    ("account/contacts", ["b", "enter", "2"]),
    ("account/interactions", ["b", "enter", "3"]),
    ("account/placements", ["b", "enter", "4"]),
    ("account/open-items", ["b", "enter", "8"]),
    ("account/requests", ["b", "enter", "9"]),
    ("calendar", ["c"]),
    ("pipeline", ["p"]),
    ("markets", ["m"]),
    ("market-detail", ["m", "enter"]),
    ("team", ["w"]),
    ("help", ["question_mark"]),
    ("search", ["slash", "a", "t", "o"]),
    ("quick-capture", ["n"]),
    ("new-task", ["ctrl+t"]),
]

_PAINTED = (DataTable, Static, OptionList, Input, TextArea, Tree)


def _contrast(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    def luminance(c: tuple[int, int, int]) -> float:
        return 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2])

    a, b = luminance(fg), luminance(bg)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def _sweep(app: BookkitApp, where: str) -> dict[tuple[str, str, str], tuple[float, str]]:
    """Every visible text segment on the current screen, below the floor."""
    found: dict[tuple[str, str, str], tuple[float, str]] = {}
    for widget in app.screen.query("*"):
        if not isinstance(widget, _PAINTED) or not widget.display:
            continue
        if not widget.size.height or not widget.size.width:
            continue
        for line in range(min(widget.size.height, 60)):
            try:
                strip = widget.render_line(line)
            except Exception:  # a widget mid-layout has nothing to say yet
                break
            for segment in strip:
                style = segment.style
                if not style or not style.color or not style.bgcolor:
                    continue
                if not segment.text.strip():
                    continue
                fg = style.color.get_truecolor()
                bg = style.bgcolor.get_truecolor()
                ratio = _contrast(
                    (fg.red, fg.green, fg.blue), (bg.red, bg.green, bg.blue)
                )
                if ratio >= FLOOR:
                    continue
                key = (type(widget).__name__, fg.hex.lower(), bg.hex.lower())
                if key not in found or ratio < found[key][0]:
                    found[key] = (ratio, f"{where}: {segment.text.strip()[:40]!r}")
    return found


@pytest.mark.parametrize("size", [(140, 45), (80, 24)])
async def test_no_text_is_invisible_against_its_background(
    snapshot_db: Path, size
) -> None:
    seen: dict[tuple[str, str, str], tuple[float, str]] = {}
    for where, presses in JOURNEYS:
        app = BookkitApp(snapshot_db)
        async with app.run_test(size=size) as pilot:
            for key in presses:
                await pilot.press(key)
            await pilot.pause()
            for pair, detail in _sweep(app, where).items():
                if pair not in seen or detail[0] < seen[pair][0]:
                    seen[pair] = detail

    unexpected = {pair: detail for pair, detail in seen.items() if pair not in ACCEPTED}
    assert not unexpected, "text below the readable floor:\n" + "\n".join(
        f"  {ratio:.2f}:1  {cls} fg={fg} bg={bg}  {detail}"
        for (cls, fg, bg), (ratio, detail) in sorted(unexpected.items())
    )


async def test_the_accepted_list_has_not_rotted(snapshot_db: Path) -> None:
    """An allowlist nobody prunes becomes a mute button. Every accepted pair
    must still actually occur; fix one and this fails until you remove it."""
    seen: set[tuple[str, str, str]] = set()
    for _where, presses in JOURNEYS:
        app = BookkitApp(snapshot_db)
        async with app.run_test(size=(140, 45)) as pilot:
            for key in presses:
                await pilot.press(key)
            await pilot.pause()
            seen.update(_sweep(app, _where))

    stale = {pair: why for pair, why in ACCEPTED.items() if pair not in seen}
    assert not stale, (
        "these are no longer low-contrast — delete them from ACCEPTED:\n"
        + "\n".join(f"  {pair} — {why}" for pair, why in stale.items())
    )
