"""Every account action is implemented on the web, or explicitly deferred.

Nothing may be silently missing: the gap has to be a number you can read.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from bookkit.web.parity import IMPLEMENTED, PENDING

# Cursor movement and scrolling are not actions to reach parity ON: the
# browser does them natively, with no route to build and nothing to defer.
_NAVIGATION = frozenset({"cursor_down", "cursor_up", "scroll_top", "scroll_bottom"})


def _widget_sources():
    """The interactive widgets AccountScreen composes that bind their own
    actions. AccountScreen.BINDINGS does not carry these — walking only the
    screen left `i` (in-cell editing, the web's primary edit affordance) and
    `Y` (copy row) invisible to the ledger (fix round 2, 2026-08-17)."""
    from bookkit.tui.widgets.inline_edit import InlineTable
    from bookkit.tui.widgets.tables import ListTable

    return (ListTable, InlineTable)


def _account_actions() -> set[str]:
    """Every action AccountScreen binds, PLUS every action its own tables
    bind (see _widget_sources), with arguments stripped
    ("show_tab('tab-overview')" -> "show_tab"), screen-level navigation
    excluded ('app.pop_screen' is the browser's back button), and cursor
    movement / scrolling excluded (see _NAVIGATION)."""
    from bookkit.tui.screens.account import AccountScreen

    actions: set[str] = set()
    for source in (AccountScreen, *_widget_sources()):
        for binding in source.BINDINGS:
            action = getattr(binding, "action", None) or ""
            name = action.split("(")[0].strip()
            if not name or name.startswith("app.") or name in _NAVIGATION:
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


def _widget_classes_with_own_bindings() -> set[type]:
    """Every class under bookkit.tui.widgets that declares BINDINGS itself,
    rather than only inheriting one. Checked against __dict__, not dir(), so
    a subclass that adds no bindings of its own doesn't count twice; checked
    against __module__ so a name merely imported into another widget module
    isn't counted twice either."""
    import bookkit.tui.widgets as widgets_pkg

    found: set[type] = set()
    for module_info in pkgutil.iter_modules(widgets_pkg.__path__, widgets_pkg.__name__ + "."):
        module = importlib.import_module(module_info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue  # imported here, defined elsewhere — skip the alias
            if "BINDINGS" in vars(obj):
                found.add(obj)
    return found


def test_every_binding_bearing_widget_is_enumerated_or_excluded():
    """A new interactive widget with its own BINDINGS would be invisible to the
    ledger the same way ListTable and InlineTable were. Modals are excluded on
    purpose: they are their own flows, entered from an action that is itself
    ledgered."""
    from bookkit.tui.widgets.forms import FormModal
    from bookkit.tui.widgets.inline_edit import CellEditor
    from bookkit.tui.widgets.link_review import LinkReview
    from bookkit.tui.widgets.paste_import import ImportChooser, PasteImportModal
    from bookkit.tui.widgets.picker import Picker
    from bookkit.tui.widgets.quick_capture import ConfirmTask, QuickCapture
    from bookkit.tui.widgets.settings import SettingsModal

    widget_sources = set(_widget_sources())
    # every one of these is a ModalScreen (or, for CellEditor, a transient
    # editor) entered from an action that is already a ledger key — its own
    # internal escape/save/cancel bindings are not a second parity surface.
    excluded_widgets: dict[type, str] = {
        FormModal: "modal entered from add_here/edit_here/etc — already ledgered",
        LinkReview: "modal entered from an import flow — already ledgered",
        PasteImportModal: "modal entered from import_here — already ledgered",
        ImportChooser: "modal entered from import_here — already ledgered",
        QuickCapture: "modal entered from a quick-capture binding elsewhere",
        ConfirmTask: "confirmation modal entered from an already-ledgered action",
        SettingsModal: "modal entered from a settings binding, not account-scoped",
        Picker: "generic option-list modal entered from an already-ledgered action",
        CellEditor: (
            "transient one-line editor that floats over a cell opened by "
            "inline_edit — already ledgered, not a second surface"
        ),
    }
    accounted = widget_sources | set(excluded_widgets)
    found = _widget_classes_with_own_bindings()
    missing = found - accounted
    assert not missing, (
        "widgets with their own BINDINGS, not enumerated in _widget_sources or "
        f"excluded with a reason: {sorted(c.__qualname__ for c in missing)}"
    )
