"""The lines-of-coverage vocabulary: identity, the near-match guard, merging.

The guards here are the point of the table. A uniqueness index alone admits
`General Liabilty` beside `General Liability`, which is precisely how four
free-text columns became five spellings of one line and left the marketing
report with no key to group by.
"""

from __future__ import annotations

import sqlite3

import pytest

from bookkit.repo import base, lines, opportunities, orgs


def _market(conn: sqlite3.Connection, name: str = "Swiss Re"):
    return orgs.create(conn, kind="market", name=name, status="active")


def _opportunity(conn: sqlite3.Connection, title: str = "2027 casualty") -> str:
    """A REAL opportunity: `foreign_keys=ON` is set in db.connect, so a made-up
    id fails the constraint rather than quietly linking to nothing."""
    client = orgs.create(conn, kind="client", name=f"Client for {title}", status="active")
    return opportunities.create(conn, org_id=client.id, title=title).id


def test_the_standard_set_is_seeded_and_ordered(conn) -> None:
    got = lines.all_lines(conn)
    assert len(got) == 17
    assert [line.id for line in got][:3] == [
        "general-liability",
        "auto",
        "workers-compensation",
    ]
    # ACORD codes are filled ONLY where confirmed; a guessed code interchanges
    # wrongly and silently, which is worse than an absent one.
    by_id = {line.id: line for line in got}
    assert by_id["general-liability"].acord_code == "CGL"
    assert by_id["workers-compensation"].acord_code == "WORK"
    assert by_id["umbrella"].acord_code is None


def test_lookup_is_case_insensitive_and_finds_the_abbreviation(conn) -> None:
    assert lines.by_name(conn, "general liability").id == "general-liability"
    assert lines.by_name(conn, "  GENERAL LIABILITY ").id == "general-liability"
    # Typing the abbreviation is the same act as typing the name.
    assert lines.by_name(conn, "gl").id == "general-liability"
    assert lines.by_name(conn, "Marine Cargo") is None


def test_an_exact_duplicate_is_refused_and_carries_the_existing_line(conn) -> None:
    with pytest.raises(lines.DuplicateLine) as caught:
        lines.create(conn, "general liability")
    # A refusal says something: the caller can offer to USE the existing line
    # rather than only reporting that it cannot make a new one.
    assert caught.value.existing.id == "general-liability"


def test_a_new_line_gets_a_slug_and_sorts_last(conn) -> None:
    line_id = lines.create(conn, "Marine Cargo", abbr="cargo", acord_code="ocarg")
    assert line_id == "marine-cargo"
    made = lines.get(conn, line_id)
    assert made.abbr == "cargo"
    assert made.acord_code == "OCARG"  # stored upper: it is a code, not prose
    others = [line.sort_order for line in lines.all_lines(conn) if line.id != line_id]
    assert made.sort_order > max(others)


def test_a_slug_never_collides_with_a_retired_line(conn) -> None:
    """Soft-delete leaves the id occupying the primary key. Minting the same
    slug again would raise on INSERT — the same shape of bug the ref counter
    had, where a minted ref was one the table already held."""
    first = lines.create(conn, "Marine Cargo")
    base.soft_delete(conn, "line_of_coverage", first)
    second = lines.create(conn, "Marine Cargo")
    assert second == "marine-cargo-2"
    assert first != second


def test_near_matches_warn_on_a_misspelling(conn) -> None:
    hits = lines.near_matches(conn, "General Liabilty")
    assert hits, "a one-letter slip must be caught before it becomes a second line"
    assert hits[0][0].id == "general-liability"


def test_near_matches_do_not_collapse_genuinely_different_lines(conn) -> None:
    """`Excess Liability` and `Employers Liability` share most of their letters
    and are not the same thing. A guard that flags them is a guard people
    learn to click past, which is a guard that no longer works."""
    hits = {line.id for line, _score in lines.near_matches(conn, "Employers Liability")}
    assert "employers-liability" in hits
    assert "excess-liability" not in hits


def test_near_matches_never_refuse(conn) -> None:
    """Advisory, not a veto: similar-but-distinct lines genuinely exist, and a
    refusal a user cannot override makes a CORRECT entry impossible."""
    assert lines.near_matches(conn, "Excess Liabilty")
    made = lines.create(conn, "Excess Liabilty")  # deliberately, over the warning
    assert lines.get(conn, made) is not None


def test_rename_keeps_the_id_and_honours_the_duplicate_guard(conn) -> None:
    lines.rename(conn, "auto", "Business Auto")
    assert lines.get(conn, "auto").name == "Business Auto"  # the id never moves
    with pytest.raises(lines.DuplicateLine):
        lines.rename(conn, "auto", "General Liability")


def test_merge_moves_every_reference_and_retires_the_source(conn) -> None:
    market = _market(conn)
    duplicate = lines.create(conn, "Gen Liability")
    appetite = orgs.add_appetite(
        conn, market_org_id=market.id, line="Gen Liability", appetite="target"
    )
    base.update(conn, "appetite", appetite.id, {"line_id": duplicate})
    opp = _opportunity(conn)
    conn.execute(
        "INSERT INTO opportunity_line (opportunity_id, line_id) VALUES (?, ?)",
        (opp, duplicate),
    )

    moved = lines.merge(conn, duplicate, "general-liability")

    assert moved == {
        "appetite": 1,
        "project_need": 0,
        "opportunity_line": 1,
        "team_assignment_line": 0,
    }
    assert conn.execute(
        "SELECT line_id FROM appetite WHERE id = ?", (appetite.id,)
    ).fetchone()[0] == "general-liability"
    assert conn.execute(
        "SELECT line_id FROM opportunity_line WHERE opportunity_id = ?", (opp,)
    ).fetchone()[0] == "general-liability"
    assert lines.get(conn, duplicate) is None  # retired, not deleted
    assert lines.by_name(conn, "Gen Liability") is None


def test_merge_survives_a_row_that_already_names_both_lines(conn) -> None:
    """One opportunity that named both spellings is not an error — it is the
    reason the merge is happening. A bare UPDATE would violate the composite
    primary key and take the whole merge down."""
    duplicate = lines.create(conn, "Gen Liability")
    opp = _opportunity(conn, "names both spellings")
    for line_id in (duplicate, "general-liability"):
        conn.execute(
            "INSERT INTO opportunity_line (opportunity_id, line_id) VALUES (?, ?)",
            (opp, line_id),
        )
    lines.merge(conn, duplicate, "general-liability")
    rows = conn.execute(
        "SELECT line_id FROM opportunity_line WHERE opportunity_id = ?", (opp,)
    ).fetchall()
    assert [r[0] for r in rows] == ["general-liability"]


def test_merge_refuses_a_line_into_itself_and_an_unknown_target(conn) -> None:
    with pytest.raises(ValueError):
        lines.merge(conn, "general-liability", "general-liability")
    with pytest.raises(KeyError):
        lines.merge(conn, "general-liability", "no-such-line")


def test_merge_events_pass_the_undo_landmine_guard(conn) -> None:
    """`base.log_event` refuses a field that is neither a column nor declared
    in NON_MUTATION_FIELDS, because undo writes that field back to that column
    and an undeclared name only fires days later, as IndexError, under someone
    pressing `u`. The merge writes two such names; both are declared."""
    duplicate = lines.create(conn, "Gen Liability")
    lines.merge(conn, duplicate, "general-liability")
    fields = {
        r[0]
        for r in conn.execute(
            "SELECT field FROM event_log WHERE entity_type = 'line_of_coverage'"
        )
    }
    assert {"merged_from", "line_link"} <= fields
