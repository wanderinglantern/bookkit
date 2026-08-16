"""What a broker can actually type into a form, and what it saves.

Three findings from the audit, all on the daily entry path:

- a money field pre-filled a value its own parser rejected, which made the
  whole record unsaveable — not the status, not the dates, until the money
  was manually rounded;
- a bare `5` in a date field saved a date nine months out, silently, which
  drops the task off every 120-day attention window;
- renaming a colleague in the TUI bypassed the duplicate guard the MCP
  server enforces, and two members sharing a name makes every later lookup
  ambiguous.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bookkit.dates import parse_human_date
from bookkit.money import MoneyParseError, format_cents, parse_money_cents
from bookkit.repo import team as team_repo
from bookkit.tui.widgets.forms import Field, FormModal

TODAY = date(2026, 8, 14)


# --- money round-trips ------------------------------------------------------


@pytest.mark.parametrize("cents", [123456, 100, 999, 1, 250050])
def test_what_the_form_shows_is_what_the_form_accepts(cents: int) -> None:
    """The round-trip that was broken: _initial_text renders the stored value,
    the parser has to take it back. Otherwise opening a record and pressing
    save is an error the user cannot clear without destroying data."""
    shown = FormModal._initial_text(Field("premium", "premium", "money"), cents)
    assert parse_money_cents(shown) == cents


def test_a_broker_can_type_cents() -> None:
    assert parse_money_cents("1,234.56") == 123456
    assert parse_money_cents("$1,234.56") == 123456
    assert parse_money_cents("1234.56") == 123456
    assert parse_money_cents("1234.5") == 123450


def test_the_shorthand_still_works() -> None:
    assert parse_money_cents("2m") == 200_000_000
    assert parse_money_cents("250k") == 25_000_000
    assert parse_money_cents("$1,500,000") == 150_000_000


def test_nonsense_money_is_still_refused() -> None:
    for bad in ("", "abc", "1.2.3", "$", "1,23.456"):
        with pytest.raises(MoneyParseError):
            parse_money_cents(bad)


def test_odd_cents_still_refuse_to_reach_a_towerkit_file() -> None:
    """The whole-dollar boundary is towerkit's, and it stays enforced there —
    bookkit storing cents is not a licence to write them to a program file."""
    from bookkit.money import cents_to_dollars

    with pytest.raises(MoneyParseError):
        cents_to_dollars(123456)
    assert cents_to_dollars(123400) == 1234


# --- dates ------------------------------------------------------------------


@pytest.mark.parametrize("text", ["5", "12", "5 ", "-5", "05"])
def test_a_bare_number_is_refused_rather_than_read_as_a_month(text: str) -> None:
    """`5` meaning "the 5th" is the most natural short entry there is.
    dateparser read it as a MONTH and future-biased it, so a follow-up landed
    on 2027-05-01 and fell off every attention window — silently."""
    assert parse_human_date(text, TODAY) is None


def test_the_shorthand_that_should_work_still_does() -> None:
    assert parse_human_date("today", TODAY) == TODAY
    assert parse_human_date("+2w", TODAY) == date(2026, 8, 28)
    assert parse_human_date("2026-10-15", TODAY) == date(2026, 10, 15)
    assert parse_human_date("5 sep", TODAY) == date(2026, 9, 5)
    assert parse_human_date("3/4/26", TODAY) == date(2026, 3, 4)   # MDY, 20xx


# --- the rename guard belongs to the repo, not to one surface --------------


def test_renaming_onto_a_taken_name_is_refused_at_the_repo(
    conn: sqlite3.Connection,
) -> None:
    """The guard lived only in mcpserver, so the TUI wrote straight past it —
    and two colleagues sharing a name makes every later lookup ambiguous."""
    keep = team_repo.create_member(conn, "Leo Novak")
    other = team_repo.create_member(conn, "Dana Okafor")

    with pytest.raises(ValueError, match="already holds that name"):
        team_repo.update_member(conn, other.id, name="Leo Novak")

    assert team_repo.get_member(conn, other.id).name == "Dana Okafor"
    assert team_repo.get_member(conn, keep.id).name == "Leo Novak"


def test_creating_a_duplicate_name_is_refused_too(conn: sqlite3.Connection) -> None:
    team_repo.create_member(conn, "Leo Novak")
    with pytest.raises(ValueError, match="already holds that name"):
        team_repo.create_member(conn, "leo novak")     # case-insensitive


def test_a_member_can_still_be_renamed_to_something_free(
    conn: sqlite3.Connection,
) -> None:
    member = team_repo.create_member(conn, "Dana Okafor")
    team_repo.update_member(conn, member.id, name="Dana Okafor-Smith")
    assert team_repo.get_member(conn, member.id).name == "Dana Okafor-Smith"


def test_renaming_a_member_to_its_own_name_is_not_a_duplicate(
    conn: sqlite3.Connection,
) -> None:
    member = team_repo.create_member(conn, "Dana Okafor")
    team_repo.update_member(conn, member.id, name="Dana Okafor", title="SVP")
    assert team_repo.get_member(conn, member.id).title == "SVP"


def test_format_cents_is_unchanged_for_whole_dollars() -> None:
    assert format_cents(200_000_000) == "$2,000,000"
