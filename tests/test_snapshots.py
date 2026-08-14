"""Visual regression for every screen, at the two terminal sizes that matter.

Before this file, `tests/test_tui.py` WROTE snapshots and nothing ever read
them — they were build artifacts in a gitignored directory, which is why a
whole batch of layout defects (a countdown column truncated to "-", a premium
column pushed off screen, a modal's Save button clipped out of its own box)
reached a review instead of a test run. See docs/tui-review.md F14.

Two sizes on purpose: 140x45 is the daily driver, 80x24 is the degradation
case that all of those defects lived in. A change that only breaks the small
one is exactly the change that used to ship unnoticed.

Re-baseline deliberately, never reflexively:

    uv run pytest tests/test_snapshots.py --snapshot-update

and read the HTML diff report the plugin writes on failure before you do.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bookkit.tui.app import BookkitApp

WIDE = (140, 45)
SMALL = (80, 24)
BOTH = [pytest.param(WIDE, id="140x45"), pytest.param(SMALL, id="80x24")]


def _app(snapshot_db: Path) -> BookkitApp:
    return BookkitApp(snapshot_db)


# --- the screens you land on ------------------------------------------------


@pytest.mark.parametrize("size", BOTH)
def test_navigator(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(_app(snapshot_db), terminal_size=size)


@pytest.mark.parametrize("size", BOTH)
def test_navigator_overdue_renewals(snap_compare, snapshot_db: Path, size) -> None:
    """The attention table, dived into — the working surface, not the tree."""
    assert snap_compare(
        _app(snapshot_db), terminal_size=size, press=["down", "down", "enter"]
    )


@pytest.mark.parametrize("size", BOTH)
def test_navigator_tasks_due(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(
        _app(snapshot_db),
        terminal_size=size,
        press=["down", "down", "down", "down", "enter"],
    )


@pytest.mark.parametrize("size", BOTH)
def test_today(snap_compare, snapshot_db: Path, size) -> None:
    """Four panes into one terminal — the screen F4 is about."""
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["t"])


@pytest.mark.parametrize("size", BOTH)
def test_book(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["b"])


@pytest.mark.parametrize("size", BOTH)
def test_calendar(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["c"])


@pytest.mark.parametrize("size", BOTH)
def test_pipeline(snap_compare, snapshot_db: Path, size) -> None:
    """Five kanban columns; the highlighted card is F28's fix, pinned here."""
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["p"])


@pytest.mark.parametrize("size", BOTH)
def test_markets(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["m"])


@pytest.mark.parametrize("size", BOTH)
def test_market_detail(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["m", "enter"])


@pytest.mark.parametrize("size", BOTH)
def test_team(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["w"])


# --- the account screen, tab by tab ------------------------------------------


@pytest.mark.parametrize("size", BOTH)
def test_account_overview(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["b", "enter"])


@pytest.mark.parametrize("size", BOTH)
def test_account_placements(snap_compare, snapshot_db: Path, size) -> None:
    """Table beside the tower preview — the split F5 is about."""
    assert snap_compare(
        _app(snapshot_db), terminal_size=size, press=["b", "enter", "4"]
    )


@pytest.mark.parametrize("size", BOTH)
def test_account_interactions(snap_compare, snapshot_db: Path, size) -> None:
    """The relationship log over the selected note — F33's master/detail."""
    assert snap_compare(
        _app(snapshot_db), terminal_size=size, press=["b", "enter", "3"]
    )


@pytest.mark.parametrize("size", BOTH)
def test_account_documents_empty(snap_compare, snapshot_db: Path, size) -> None:
    """An empty tab. Pinning the empty state so F12 cannot silently regress."""
    assert snap_compare(
        _app(snapshot_db), terminal_size=size, press=["b", "enter", "7"]
    )


@pytest.mark.parametrize("size", BOTH)
def test_account_requests_empty(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(
        _app(snapshot_db), terminal_size=size, press=["b", "enter", "9"]
    )


# --- modals ------------------------------------------------------------------


@pytest.mark.parametrize("size", BOTH)
def test_help(snap_compare, snapshot_db: Path, size) -> None:
    """160 rows of help in a 19-row viewport at 80 columns — F13."""
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["question_mark"])


@pytest.mark.parametrize("size", BOTH)
def test_new_task_form(snap_compare, snapshot_db: Path, size) -> None:
    """The Save button falls outside the modal box below 28 rows — F8."""
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["ctrl+t"])


@pytest.mark.parametrize("size", BOTH)
def test_quick_capture(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(_app(snapshot_db), terminal_size=size, press=["n"])


@pytest.mark.parametrize("size", BOTH)
def test_search(snap_compare, snapshot_db: Path, size) -> None:
    assert snap_compare(
        _app(snapshot_db), terminal_size=size, press=["slash", "a", "t", "o", "m"]
    )
