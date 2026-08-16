"""Keys the app advertises, and keys that do nothing without saying so.

Two failure modes, both of which teach a user the app is flaky:

- a hint line names a key that is not bound at all (`j/k move` on the
  navigator card, `u`/`esc` documented as working "everywhere");
- a key IS bound, correctly refuses because the focus rule says so, and then
  returns in silence. The gate is right; the silence is the bug.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bookkit.tui.app import BookkitApp

# markup the hint lines use for keys: [b]x[/b]
_KEY_RE = re.compile(r"\[b\]([^\[]+)\[/b\]")

# words in a hint that name a movement or a concept, not a binding of ours
_NOT_BINDINGS = {"enter", "esc", "escape", "tab", "space", "↑/↓", "⏎"}


def _advertised(hint: str) -> set[str]:
    return {
        key
        for key in _KEY_RE.findall(hint)
        if key not in _NOT_BINDINGS and len(key) <= 2
    }


def _live_keys(app: BookkitApp) -> set[str]:
    keys: set[str] = set()
    for active in app.active_bindings.values():
        for key in active.binding.key.split(","):
            keys.add(key.strip())
            display = active.binding.key_display
            if display:
                keys.add(display)
    return keys


# --- the guard that stops this recurring ------------------------------------


async def test_every_key_a_navigator_hint_names_is_bound(snapshot_db: Path) -> None:
    """`j/k move` sat on the glance card — which renders only while the TREE
    has focus, and Tree binds neither. The first sentence of guidance the app
    ever showed was wrong."""
    from bookkit.tui.screens.navigator import ROW_HINTS

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        live = _live_keys(app)
        # the card's own hint, shown on the landing screen
        card = app.screen.query_one("#nav-card")
        for key in _advertised(str(card.render())):
            assert key in live, f"navigator card advertises {key!r}, not bound"

        # and every row hint, with the rows focused
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        live = _live_keys(app)
        for name, hint in ROW_HINTS.items():
            missing = _advertised(hint) - live
            assert not missing, f"ROW_HINTS[{name!r}] advertises dead keys: {missing}"


@pytest.mark.parametrize(
    "tab,keys",
    [
        ("tab-overview", ["1"]),
        ("tab-contacts", ["2"]),
        ("tab-interactions", ["3"]),
        ("tab-placements", ["4"]),
    ],
)
async def test_every_key_an_account_tab_hint_names_is_bound(
    snapshot_db: Path, tab: str, keys: list[str]
) -> None:
    """Two independent reviewers reported `i paste import` here as dead,
    citing a comment about the binding moving to `I`. It had not: that comment
    is about `D` and `P` (paste RFI items), and `i` is bound to import_here on
    every tab. This test is what disproved it — and is why the hints were left
    alone."""
    from bookkit.repo import orgs
    from bookkit.tui.screens.account import TAB_HINTS

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        org = orgs.list_orgs(app.conn, kind="client")[0]
        app.open_account(org.id)
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()

        missing = _advertised(TAB_HINTS[tab]) - _live_keys(app)
        assert not missing, f"TAB_HINTS[{tab!r}] advertises dead keys: {missing}"


# --- keys the help screen promises work "everywhere" ------------------------


@pytest.mark.parametrize(
    "name,keys", [("calendar", ["c"]), ("markets", ["m"]), ("team", ["w"])]
)
async def test_undo_works_on_every_screen_help_says_it_does(
    snapshot_db: Path, name: str, keys: list[str]
) -> None:
    """help.py lists `u` under "everywhere". It was bound per-screen, and
    Calendar, Markets, Team and Onboarding never got it."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        assert "u" in _live_keys(app), f"{name}: u is not bound"


async def test_escape_leaves_today_the_way_it_leaves_every_other_screen(
    snapshot_db: Path,
) -> None:
    """Today was the only working screen with no escape binding, and with the
    footer blank it had no on-screen exit at all."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert type(app.screen).__name__ == "TodayScreen"
        await pilot.press("escape")
        await pilot.pause()
        assert type(app.screen).__name__ == "NavigatorScreen"


async def test_j_and_k_move_the_navigator_tree(snapshot_db: Path) -> None:
    """The card promises them and help teaches them, so make them real rather
    than deleting the promise — vim movement is otherwise on every table but
    the one widget the landing screen focuses."""
    from textual.widgets import Tree

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        tree = app.screen.query_one("#nav-tree", Tree)
        tree.focus()
        await pilot.pause()
        # the tree opens with cursor_line == -1 (no cursor until the first
        # move), so establish one before testing that j/k move it
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        start = tree.cursor_line
        assert start >= 1

        await pilot.press("j")
        await pilot.pause()
        assert tree.cursor_line > start, "j did not move the tree cursor"

        await pilot.press("k")
        await pilot.pause()
        assert tree.cursor_line == start, "k did not move it back"


# --- a refusal has to say something -----------------------------------------


@pytest.mark.parametrize("key", ["e", "r", "l", "d", "a"])
async def test_a_navigator_row_action_says_why_it_did_nothing(
    snapshot_db: Path, key: str
) -> None:
    """Row actions REQUIRE table focus (CLAUDE.md). With the tree focused six
    of seven produced no modal, no message, no change — indistinguishable
    from a broken app. Only `x` said anything."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45), notifications=True) as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("down")          # an attention leaf, tree focused
        await pilot.pause()
        app.clear_notifications()

        await pilot.press(key)
        await pilot.pause()

        messages = [str(n.message) for n in app._notifications]
        assert messages, f"{key!r} with the tree focused did nothing, silently"
        actionable = ("tab", "select", "open", "press", "enter")
        assert any(w in m.lower() for m in messages for w in actionable), (
            f"{key!r} said {messages!r}, which does not tell the user what to do"
        )
