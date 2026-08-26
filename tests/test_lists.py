"""The editable vocabularies — phase 1: the registry, the seed, and the gates
that keep the registry honest before anything reads it.

WHAT THIS FILE IS FOR. `bookkit.lists` gathers, for every vocabulary a broker
can legitimately want their own words for, what each value READS as, what tint
it takes and where it sorts. Three of those facts used to live in three
different modules and only ONE vocabulary had all three. Gathering them is the
DRY win; these are the assertions that stop the gathering from drifting away
from what the app actually renders, and — the one that matters most — that stop
a controlled vocabulary from being added to this book with no home at all.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from bookkit import db, lists
from bookkit.models import MARKET_RESPONSE_STATUS_LABELS


def _fresh(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "lists.db")


def _named(failures: list[str], rule: str) -> None:
    if failures:
        raise AssertionError(
            f"{rule}\n\n" + "\n".join(f"  * {line}" for line in failures)
        )


# --- the registry says what models.py says ---------------------------------


def test_every_list_offers_exactly_the_words_models_declares() -> None:
    """THE REGISTRY IS DERIVED, NOT A SECOND COPY. A value added to a tuple in
    models.py and forgotten here would be storable and unpickable — present in
    the CHECK, absent from every dropdown — which is the copy that quietly
    differs, in the direction that looks like a broken form."""
    failures = []
    for list_id, declared in lists.DERIVED_FROM.items():
        spec = lists.BY_ID[list_id]
        offered = {b.value for b in spec.values}
        for missing in sorted(set(declared) - offered):
            failures.append(
                f"{list_id}: models.py declares {missing!r} and the registry "
                f"does not offer it"
            )
        for extra in sorted(offered - set(declared)):
            failures.append(
                f"{list_id}: the registry offers {extra!r}, which models.py "
                f"does not declare — nothing can store it"
            )
    _named(failures, "the registry and models.py disagree about a vocabulary:")


def test_every_tone_is_one_the_stylesheet_can_render() -> None:
    """A TINT THIS FILE INVENTS RENDERS AS NO TINT AT ALL — silently, on a
    client-facing pill. Checked against app.css rather than against the tuple
    beside it, because the tuple is this module's claim and the stylesheet is
    the fact."""
    css = (Path(db.__file__).parent / "web" / "static" / "app.css").read_text()
    real = set(re.findall(r"\.(is-[a-z]+)\b", css))
    failures = [
        f"{spec.id}/{built.value}: tone {built.tone!r} is not a class app.css "
        f"defines"
        for spec in lists.SPECS
        for built in spec.values
        if built.tone and built.tone not in real
    ]
    failures += [
        f"lists.TONES names {tone!r}, which app.css does not define"
        for tone in lists.TONES
        if tone and tone not in real
    ]
    failures += [
        f"{spec.id}/{built.value}: tone {built.tone!r} is not in lists.TONES"
        for spec in lists.SPECS
        for built in spec.values
        if built.tone not in lists.TONES
    ]
    _named(failures, "a value carries a tone that cannot render:")


# --- the registry says what the app renders TODAY --------------------------
#
# Phase 1 changes nothing: `marketing_grid._STATUS_TONE` and
# `marketing_report._STATUS_ORDER` are still what the panel and the workbook
# read. These two hold the registry equal to them, so the day phase 4 moves the
# reads across nothing on screen moves with them.


def test_the_registry_carries_the_tints_the_grid_renders_today() -> None:
    from bookkit.web.marketing_grid import _STATUS_TONE

    spec = lists.BY_ID["market_response.status"]
    _named(
        [
            f"{b.value}: registry says {b.tone!r}, the grid renders "
            f"{_STATUS_TONE.get(b.value, '')!r}"
            for b in spec.values
            if b.tone != _STATUS_TONE.get(b.value, "")
        ],
        "the registry would change a tint the moment phase 4 reads it:",
    )


def test_the_registry_carries_the_order_the_report_prints_today() -> None:
    from bookkit.services.marketing_report import _STATUS_ORDER

    spec = lists.BY_ID["market_response.status"]
    registry = [b.value for b in spec.values]
    report = sorted(_STATUS_ORDER, key=lambda v: _STATUS_ORDER[v])
    assert registry == report, (
        "the registry would reorder a client's workbook the moment phase 4 "
        f"reads it\n  registry: {registry}\n  report:   {report}"
    )


def test_the_registry_carries_the_labels_models_declares() -> None:
    spec = lists.BY_ID["market_response.status"]
    _named(
        [
            f"{b.value}: registry says {b.label!r}, models.py says "
            f"{MARKET_RESPONSE_STATUS_LABELS[b.value]!r}"
            for b in spec.values
            if b.label != MARKET_RESPONSE_STATUS_LABELS[b.value]
        ],
        "the registry and models.py disagree about what a person reads:",
    )


# --- the coverage claim, which is the load-bearing gate --------------------


# The vocabularies that stay in CODE, each with the reason. A word added to one
# of these would be a new RULE with nothing sensible for `behaves_as` to
# inherit, which is precisely where this design stops (Grant, 2026-08-26).
STRUCTURAL: dict[tuple[str, str], str] = {
    ("org", "kind"): (
        "client / market / other decides which pickers a row reaches, which "
        "routes accept it and which half of the book it is in. A fourth kind "
        "would be a word with no behaviour behind it."
    ),
    ("interaction", "sentiment"): "pos / neu / neg is three-valued by nature.",
    ("opportunity", "outcome"): (
        "won / lost / no_decision IS the hit rate — services/hit_rate.py reads "
        "these three by name."
    ),
}


def _check_pinned_columns() -> dict[tuple[str, str], tuple[str, ...]]:
    """Every (table, column) an enumerated `CHECK` pins, and the words it
    allows, off the migrations.

    READ FROM THE SQL rather than listed here, so a CHECK added tomorrow is
    walked on the commit that adds it. Two things the parse has to get right:
    later migrations rebuild a table under a temporary name
    (`market_response_new`), so the rename is followed and the pair is reported
    under the name the schema ends up with; and a column pinned more than once
    across migrations keeps the LAST definition, because that is the one the
    database is actually left holding.
    """
    found: dict[tuple[str, str], tuple[str, ...]] = {}
    root = Path(db.__file__).resolve().parents[2] / "migrations"
    for path in sorted(root.glob("*.sql")):
        text = path.read_text()
        renames = dict(
            re.findall(r"ALTER TABLE (\w+) RENAME TO (\w+)", text)
        )
        for tm in re.finditer(r"CREATE TABLE (\w+)\s*\((.*?)\n\);", text, re.S):
            table, body = tm.group(1), tm.group(2)
            table = renames.get(table, table)
            for cm in re.finditer(
                r"(\w+)\s+TEXT[^,]*?CHECK\s*\(\s*\1\s+IN\s*\(([^)]*)\)", body, re.S
            ):
                values = tuple(
                    v.strip().strip("'")
                    for v in cm.group(2).split(",")
                    if v.strip()
                )
                found[(table, cm.group(1))] = values
    return found


def test_every_check_pinned_vocabulary_has_a_home() -> None:
    """THE GATE THAT MAKES THE COVERAGE CLAIM TRUE.

    Every column this book pins with an enumerated `CHECK` is either a list a
    broker can edit (in `lists.SPECS`) or a vocabulary declared STRUCTURAL
    above, with the reason. Neither is a default: a new CHECK added with no
    entry either way is a twelfth table rebuild waiting to happen, and this is
    where it is noticed rather than the day somebody needs a new word.

    WHERE IT CANNOT LOOK: a vocabulary with no CHECK behind it at all — the
    tuples in models.py that were never pinned (TEAM_ROLES, CONTACT_ROLES) are
    caught by `test_every_list_offers_exactly_the_words_models_declares`
    instead, and a NEW unpinned tuple would be seen by neither.
    """
    pinned = set(_check_pinned_columns())
    editable = {(spec.table, spec.column) for spec in lists.SPECS}
    _named(
        [
            f"{table}.{column} is pinned by a CHECK and is neither an editable "
            f"list in lists.SPECS nor declared in STRUCTURAL with a reason"
            for table, column in sorted(pinned)
            if (table, column) not in editable and (table, column) not in STRUCTURAL
        ],
        "a controlled vocabulary has no home:",
    )
    _named(
        [
            f"{table}.{column} is declared STRUCTURAL and is also an editable "
            f"list — it cannot be both"
            for table, column in sorted(STRUCTURAL)
            if (table, column) in editable
        ],
        "a vocabulary is declared twice:",
    )


def test_a_check_pinned_list_offers_exactly_what_the_column_can_store() -> None:
    """THE OTHER HALF OF THE DERIVATION, and the hole the first one leaves.

    Nine of the seventeen lists read their words from a tuple in models.py and
    cannot drift from it. The other eight have no tuple at all — their
    vocabulary lives ONLY in the `CHECK` on the column — so the registry could
    offer a word the database will refuse, and nothing would say so until a
    broker picked it from a dropdown and the write came back with SQLite's own
    sentence. Found by mutation on the day this landed: adding `deferred` to
    `task.status` passed every other gate in this file.

    Checked against the `CHECK` for now, which is the authority TODAY. Phase 3
    replaces those with referential triggers reading `list_value`, and on that
    commit this gate inverts — the list becomes the authority and the trigger
    is derived from it. It is worth keeping in that direction, and the
    docstring is where that is said rather than left to be rediscovered.
    """
    pinned = _check_pinned_columns()
    failures = []
    for spec in lists.SPECS:
        allowed = pinned.get((spec.table, spec.column))
        if allowed is None:
            continue        # not pinned; the models.py half covers it
        offered = {b.value for b in spec.values}
        for missing in sorted(set(allowed) - offered):
            failures.append(
                f"{spec.id}: the column can store {missing!r} and the registry "
                f"does not offer it — a stored word nothing can pick"
            )
        for extra in sorted(offered - set(allowed)):
            failures.append(
                f"{spec.id}: the registry offers {extra!r} and the CHECK on "
                f"{spec.table}.{spec.column} refuses it — picking it fails the "
                f"write"
            )
    _named(failures, "a list offers words its own column disagrees with:")


def test_every_list_names_a_column_that_exists(tmp_path: Path) -> None:
    """`<table>.<column>` is the id, and phase 3's triggers are built from it —
    so a list naming a column that is not there would produce a trigger on
    nothing, which SQLite reports at migration time and nowhere else."""
    conn = _fresh(tmp_path)
    failures = []
    for spec in lists.SPECS:
        cols = {
            r["name"]
            for r in conn.execute(f"PRAGMA table_info({spec.table})").fetchall()
        }
        if not cols:
            failures.append(f"{spec.id}: no table called {spec.table!r}")
        elif spec.column not in cols:
            failures.append(f"{spec.id}: {spec.table} has no column {spec.column!r}")
    _named(failures, "a list names a column the schema does not have:")


# --- the seed --------------------------------------------------------------


def test_the_seed_lands_and_is_idempotent(tmp_path: Path) -> None:
    conn = _fresh(tmp_path)

    assert conn.execute("SELECT COUNT(*) FROM list_definition").fetchone()[0] == len(
        lists.SPECS
    )
    assert conn.execute("SELECT COUNT(*) FROM list_value").fetchone()[0] == sum(
        len(s.values) for s in lists.SPECS
    )
    # A SECOND OPEN WRITES NOTHING. The web layer opens a connection per thread
    # and the CLI opens one per command; a seed that wrote every time would put
    # a write on the read path of every surface in the app.
    assert db.seed_builtin_lists(conn) == 0


def test_a_builtin_behaves_as_itself(tmp_path: Path) -> None:
    """The self-reference is what lets ONE foreign key hold the whole shape
    with no second table, and it is what every rule will resolve to."""
    conn = _fresh(tmp_path)
    rows = conn.execute(
        "SELECT value, behaves_as FROM list_value WHERE is_builtin = 1"
    ).fetchall()
    assert rows
    _named(
        [f"{r['value']} behaves as {r['behaves_as']}" for r in rows
         if r["value"] != r["behaves_as"]],
        "a built-in must behave as itself:",
    )


def test_a_value_can_only_behave_as_one_of_its_own_lists_values(tmp_path: Path) -> None:
    """The foreign key is `(list_id, behaves_as)`, so a value cannot inherit
    behaviour from a DIFFERENT vocabulary — 'behaves as bound' has to mean the
    bound of this list."""
    conn = _fresh(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO list_value (id, list_id, value, label, tone, rank,"
            " behaves_as, is_builtin, created_at, updated_at)"
            " VALUES ('X2', 'task.status', 'referred', 'Referred', '', 9,"
            " 'quoted', 0, '2026-08-26', '2026-08-26')"
        )


def test_a_value_in_use_as_a_behaviour_cannot_be_deleted(tmp_path: Path) -> None:
    """The bonus the self-referencing key gives: nothing can pull the built-in
    out from under a value that inherits from it."""
    conn = _fresh(tmp_path)
    conn.execute(
        "INSERT INTO list_value (id, list_id, value, label, tone, rank,"
        " behaves_as, is_builtin, created_at, updated_at)"
        " VALUES ('X1', 'market_response.status', 'referred_up',"
        " 'Referred to underwriting', 'is-warn', 9, 'pending', 0,"
        " '2026-08-26', '2026-08-26')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "DELETE FROM list_value WHERE list_id = 'market_response.status'"
            " AND value = 'pending'"
        )


def test_a_word_somebody_edited_survives_the_next_release(tmp_path: Path) -> None:
    """`sync_builtins` runs on every open, so a registry that wrote its own
    label back every time would quietly revert a broker's own words — and they
    would only notice by looking. What has been edited is read off the event
    log, where every deliberate write in this book already lands."""
    from bookkit.repo import base

    conn = _fresh(tmp_path)
    conn.execute(
        "UPDATE list_value SET label = 'No reply' WHERE list_id ="
        " 'market_response.status' AND value = 'non_response'"
    )
    row_id = conn.execute(
        "SELECT id FROM list_value WHERE list_id = 'market_response.status'"
        " AND value = 'non_response'"
    ).fetchone()[0]
    base.log_event(
        conn, "list_value", row_id, "label", "Non-response", "No reply", "renamed",
    )

    assert db.seed_builtin_lists(conn) == 0
    assert conn.execute(
        "SELECT label FROM list_value WHERE list_id = 'market_response.status'"
        " AND value = 'non_response'"
    ).fetchone()[0] == "No reply"


def test_a_word_the_code_drops_is_retired_not_deleted(tmp_path: Path) -> None:
    """Rows on disk may still hold it — that is the whole reason a vocabulary
    change is a migration — and deleting the value would leave those rows
    naming nothing at all."""
    conn = _fresh(tmp_path)
    conn.execute(
        "INSERT INTO list_value (id, list_id, value, label, tone, rank,"
        " behaves_as, is_builtin, created_at, updated_at)"
        " VALUES ('X3', 'task.status', 'deferred', 'Deferred', '', 9,"
        " 'deferred', 1, '2026-08-26', '2026-08-26')"
    )

    assert db.seed_builtin_lists(conn) == 1

    row = conn.execute(
        "SELECT retired_at FROM list_value WHERE list_id = 'task.status'"
        " AND value = 'deferred'"
    ).fetchone()
    assert row is not None, "the value was deleted"
    assert row["retired_at"], "the value should have been retired"


def test_nothing_reads_the_lists_to_make_a_decision_yet() -> None:
    """PHASE 1 IS INERT, and this is what says so. `sync_builtins` writes the
    tables and `db.seed_builtin_lists` calls it; nothing else in src/ touches
    them. When phase 4 lands, this test is the one to delete — deliberately,
    naming what now reads it — rather than something that quietly stops being
    true."""
    root = Path(db.__file__).parent
    readers = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "list_value" in p.read_text() or "list_definition" in p.read_text()
    )
    # `repo/base.py` names the table in ENTITY_TABLES so an edit to a list
    # value is event-logged and revertible like every other write, and
    # `mcpparity.py` names the four cells the assistant does not reach yet and
    # says which phase adds them. Both are REGISTRATION — a row in a ledger —
    # not a read that decides anything. `repo/base.py` has to be here in phase
    # 1 because `sync_builtins` asks the event log whether a person has renamed
    # a built-in; `mcpparity.py` has to be here because the parity gate counts
    # every entity and an undeclared one is an unexplained gap.
    assert readers == ["db.py", "lists.py", "mcpparity.py", "repo/base.py"], (
        "something new reads the vocabulary tables — phase 1 is meant to be "
        f"inert, and these read them: {readers}"
    )
