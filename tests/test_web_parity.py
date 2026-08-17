"""Every account action is implemented on the web, or explicitly deferred.

Nothing may be silently missing: the gap has to be a number you can read.
"""

from __future__ import annotations

from bookkit.web.parity import IMPLEMENTED, PENDING


def _account_actions() -> set[str]:
    """Every action AccountScreen binds, with arguments stripped
    ("show_tab('tab-overview')" -> "show_tab") and screen-level navigation
    excluded — 'app.pop_screen' is the browser's back button."""
    from bookkit.tui.screens.account import AccountScreen

    actions: set[str] = set()
    for binding in AccountScreen.BINDINGS:
        action = getattr(binding, "action", None) or ""
        name = action.split("(")[0].strip()
        if not name or name.startswith("app."):
            continue
        actions.add(name)
    return actions


def test_every_account_action_is_implemented_or_explicitly_pending():
    actions = _account_actions()
    accounted = set(IMPLEMENTED) | set(PENDING)
    missing = actions - accounted
    assert not missing, (
        "AccountScreen actions in neither IMPLEMENTED nor PENDING: "
        f"{sorted(missing)} — add each to bookkit/web/parity.py, with a route "
        "if it is built or a one-line reason if it is not"
    )


def test_the_ledger_has_no_stale_entries():
    """An entry for an action the TUI no longer binds is a lie about coverage."""
    actions = _account_actions()
    stale = (set(IMPLEMENTED) | set(PENDING)) - actions
    assert not stale, f"ledger names actions AccountScreen no longer binds: {sorted(stale)}"


def test_an_action_is_not_both_implemented_and_pending():
    overlap = set(IMPLEMENTED) & set(PENDING)
    assert not overlap, f"both implemented and pending: {sorted(overlap)}"
