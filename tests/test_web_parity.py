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
    # Each reason names the ACTUAL call site(s), checked by grepping every
    # `ClassName(` construction in tui/, not assumed from the class's own
    # docstring — two of these were wrong in fix round 2 ("already ledgered"
    # for widgets that were never reachable from any AccountScreen action at
    # all) and that is worse than an admission of no coverage, because it
    # stops the next reader from checking (fix round 3, 2026-08-17).
    excluded_widgets: dict[type, str] = {
        # pushed from many actions across many screens — accurate only for
        # the account screen, where every opener (add_here, edit_here,
        # assign_team, scaffold_tower, add_layer, edit_layer, new_submission,
        # ...) is already a ledger key. onboarding, team, markets, today,
        # pipeline, book and navigator push it independently of this ledger.
        FormModal: (
            "modal opened by many account actions, already ledgered on this "
            "screen — also pushed independently by onboarding/team/markets/"
            "today/pipeline/book/navigator, outside this ledger's scope"
        ),
        # NOT account-scoped: the only call site is TodayScreen, not
        # AccountScreen — verified by grep, no `LinkReview(` in account.py.
        LinkReview: (
            "not account-scoped — pushed only from "
            "TodayScreen.action_sync_programs, which is not an AccountScreen "
            "action and has no ledger key"
        ),
        # pushed from account.py (import_here) AND independently by markets
        # and team (their own signature-paste flows) — verified by grep.
        PasteImportModal: (
            "modal entered from import_here, already ledgered on this screen "
            "— markets and team also push it independently (their own "
            "signature pastes), outside this ledger's scope"
        ),
        # the only call site in the whole tree is account.py:1751, inside
        # action_import_here — verified by grep, so this one is fully
        # accurate as stated.
        ImportChooser: "only ever pushed from import_here — already ledgered",
        # the only call site is App.action_quick_capture (an app-level `n`
        # binding), never from AccountScreen — verified by grep.
        QuickCapture: (
            "not account-scoped — pushed only by the App-level `n` binding "
            "(action_quick_capture), not by any account action"
        ),
        # NOT account-scoped: entered from QuickCapture, itself pushed by the
        # app-level binding above, not by any account action — verified by
        # grep, no `ConfirmTask(` in account.py.
        ConfirmTask: (
            "not account-scoped — entered from QuickCapture, itself pushed "
            "by the App-level `n` binding rather than by any account action"
        ),
        # pushed from app.py, today.py, navigator.py — never from account.py
        # — verified by grep.
        SettingsModal: "modal entered from a settings binding, not account-scoped",
        # pushed from account.py (edit_layer's layer picker, assign_team's
        # market-to-layer picker) AND independently by markets and team —
        # verified by grep.
        Picker: (
            "modal entered from already-ledgered account actions (edit_layer, "
            "assign_team) — markets and team also push it independently, "
            "outside this ledger's scope"
        ),
        # the only call site is InlineTable itself (inline_edit.py:99), the
        # cell editor that opens under `i` — verified by grep.
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


def test_undo_is_implemented_and_names_its_route():
    """`u`/`R` have a web equivalent now — POST
    /accounts/{ref}/changes/{batch_ref}/revert, driven by the rail's per-change
    Revert and the top bar's Undo pill (Task 15b). A PENDING entry left behind
    after the route exists is a lie about coverage in the direction that is
    hardest to notice: the ledger only ever fails loudly for MISSING keys."""
    assert "undo" not in PENDING
    assert "/changes/" in IMPLEMENTED["undo"] and "revert" in IMPLEMENTED["undo"]


# --- the verb ledger is discovered from sync.py itself -------------------------


def test_every_sync_program_mutator_is_in_the_verb_ledger():
    """Both directions: a new sync writer that calls _mutate (or joins the
    named non-mutate writers) turns this red until SYNC_VERBS covers or
    defers it; a ledger entry for a deleted verb goes red too."""
    import re
    from pathlib import Path

    from bookkit.web.parity import SYNC_VERBS

    source = Path("src/bookkit/sync.py").read_text()
    chunks = re.split(r"^def ", source, flags=re.M)[1:]
    mutators = {
        chunk.split("(", 1)[0]
        for chunk in chunks
        if "_mutate(" in chunk and not chunk.startswith("_")
    }
    mutators |= {"scaffold_program", "renew", "create_program"}  # write files without _mutate

    assert mutators == set(SYNC_VERBS), (
        f"uncovered: {mutators - set(SYNC_VERBS)}; stale: {set(SYNC_VERBS) - mutators}"
    )


def test_every_verb_entry_speaks_for_all_three_surfaces():
    from bookkit.web.parity import SYNC_VERBS

    for verb, surfaces in SYNC_VERBS.items():
        assert set(surfaces) == {"web", "tui", "mcp"}, verb
        for surface, note in surfaces.items():
            assert note.strip(), f"{verb}/{surface} says nothing"


def test_every_towerkit_edit_op_is_accounted_for():
    """Grant, 2026-08-19: 'fully built but not accessible' — a capability can
    exist end-to-end in towerkit and never surface in the browser, and no
    test noticed because parity was enumerated over bookkit's surfaces only.
    This introspects towerkit.edit AT RUNTIME: an op towerkit grows (or
    loses) turns this red until TOWERKIT_EDIT_OPS names it — covered,
    deferred with a reason, or branch-only pending a decision."""
    import inspect

    from towerkit import edit

    from bookkit.web.parity import TOWERKIT_EDIT_OPS

    public = {
        name
        for name, obj in vars(edit).items()
        if inspect.isfunction(obj)
        and obj.__module__ == "towerkit.edit"
        and not name.startswith("_")
    }

    assert public == set(TOWERKIT_EDIT_OPS), (
        f"unaccounted towerkit ops: {public - set(TOWERKIT_EDIT_OPS)}; "
        f"stale ledger entries: {set(TOWERKIT_EDIT_OPS) - public}"
    )


def test_every_tui_screen_is_in_the_screen_ledger():
    """The audit's structural fix: a WHOLE screen could be web-absent and no
    ledger turned red — the account-action, sync-verb and towerkit-op guards
    all look below screen level. Discovered from the screens package, so a
    new TUI screen joins the parity question the day it exists."""
    from pathlib import Path

    from bookkit.web.parity import SCREENS

    modules = {
        p.stem
        for p in Path("src/bookkit/tui/screens").glob("*.py")
        if p.stem != "__init__"
    }
    missing = modules - set(SCREENS)
    assert not missing, f"TUI screens with no parity entry: {missing}"
    app_level = {"quick_capture_app_key", "sync_and_settings_app_keys"}
    stale = set(SCREENS) - modules - app_level
    assert not stale, f"screen ledger entries with no screen: {stale}"


def test_every_towerkit_model_field_is_accounted_for():
    """The other half of "built but not accessible".

    TOWERKIT_EDIT_OPS enumerates towerkit's VERBS and says nothing about its
    NOUNS. towerkit grew `named_limits`, `states` and three detail strings on
    Layer — coverage facts a broker states on a quote and towerkit prints on
    the SOI — and every parity test stayed green, because adding a FIELD adds
    no op. They were reachable only from towerkit's own editor behind the
    TUI's `o`, which a browser does not have (Grant, 2026-08-20).

    Introspected at runtime, like the op ledger: a field towerkit grows turns
    this red until TOWERKIT_MODEL_FIELDS names it.
    """
    import inspect

    from pydantic import BaseModel
    from towerkit import model

    from bookkit.web.parity import TOWERKIT_MODEL_FIELDS

    public = {
        f"{name}.{field}"
        for name, obj in vars(model).items()
        if inspect.isclass(obj)
        and issubclass(obj, BaseModel)
        and obj.__module__ == "towerkit.model"
        and not name.startswith("_")
        for field in obj.model_fields
    }

    assert public == set(TOWERKIT_MODEL_FIELDS), (
        f"towerkit fields with no parity entry: "
        f"{sorted(public - set(TOWERKIT_MODEL_FIELDS))}; "
        f"stale ledger entries: {sorted(set(TOWERKIT_MODEL_FIELDS) - public)}"
    )


# --- D6: the placement table is load-bearing, not descriptive -----------------


def test_every_placed_field_is_one_towerkit_actually_publishes():
    """`routes/program.py:_PLACED` says where each derived field is PUT. Every
    key is resolved against `mcpsurface.SURFACE`, so a field towerkit renames
    or denies turns this red rather than 404ing at a user who clicks the cell.
    """
    from towerkit import mcpsurface

    from bookkit.web.routes.program import _PLACED

    unknown = []
    for key in _PLACED:
        kind, _, name = key.partition(".")
        if name not in mcpsurface.SURFACE.get(kind, {}):
            unknown.append(key)
    assert not unknown, f"_PLACED names fields towerkit does not publish: {unknown}"


def test_a_field_with_a_cell_is_not_still_described_as_planned():
    """THE DRIFT THAT HAS SHIPPED THREE TIMES: the thing gets built and the
    ledger keeps saying it is pending, so the ledger stops being evidence and
    the next audit re-finds work that is already done (three entries corrected
    on 2026-08-19 alone). A field with a cell on the page cannot still be
    marked planned here.
    """
    import re

    from bookkit.web.parity import TOWERKIT_MODEL_FIELDS
    from bookkit.web.routes.program import _PLACED

    # `_PLACED` is keyed by towerkit's WIRE name (layer.limitsDetail); the
    # ledger is keyed by the model's python name (Layer.limits_detail). One
    # rule converts between them rather than a second hand-written table.
    def ledger_key(placed: str) -> str:
        kind, _, name = placed.partition(".")
        head = "".join(part.title() for part in kind.split("_"))
        snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name.split(".")[0]).lower()
        return f"{head}.{snake}"

    still_planned = [
        key for key in _PLACED
        if "NOT BUILT" in TOWERKIT_MODEL_FIELDS.get(ledger_key(key), "")
    ]
    assert not still_planned, (
        f"built and reachable, still marked planned in the ledger: {still_planned}"
    )
