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
from bookkit.forms.spec import Field, initial_text
from bookkit.money import MoneyParseError, format_cents, parse_money_cents
from bookkit.repo import team as team_repo

TODAY = date(2026, 8, 14)


# --- money round-trips ------------------------------------------------------


@pytest.mark.parametrize("cents", [123456, 100, 999, 1, 250050])
def test_what_the_form_shows_is_what_the_form_accepts(cents: int) -> None:
    """The round-trip that was broken: initial_text renders the stored value,
    the parser has to take it back. Otherwise opening a record and pressing
    save is an error the user cannot clear without destroying data."""
    shown = initial_text(Field("premium", "premium", "money"), cents)
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


# --- the share kind (D6) ------------------------------------------------------


def test_a_share_parses_through_towerkits_one_rule():
    """CLAUDE.md: one percent→bps rule, owned by towerkit money.parse_share;
    bookkit delegates. A second conversion here is how 33.33% becomes 3333 bps
    in one place and 333300 in another."""
    from bookkit.forms.spec import Field, parse_value

    field = Field("share_pct", "share", "share")

    assert parse_value(field, "33.33%") == parse_value(field, "33.33") == 3333
    assert parse_value(field, "100%") == 10_000


def test_a_share_over_one_hundred_percent_is_refused():
    """towerkit's rule, verified rather than assumed: parse_share bounds bps
    to 0 < bps <= 10_000. A layer cannot be more than fully signed, and the
    over-sign the validator catches later should not have got this far."""
    from bookkit.forms.spec import Field, parse_value

    with pytest.raises(ValueError):
        parse_value(Field("share_pct", "share", "share"), "140%")


def test_a_share_with_sub_basis_point_precision_is_refused_not_rounded():
    """Rounding a share silently changes who owns what."""
    from bookkit.forms.spec import Field, parse_value

    with pytest.raises(ValueError):
        parse_value(Field("share_pct", "share", "share"), "33.333")


def test_a_share_pre_fills_as_the_percent_it_was_typed_as():
    """The editor pre-fills from initial_text, and what a form pre-fills has
    to survive its own parser: bps in the box would be read back as a percent
    and multiply the share by a hundred."""
    from bookkit.forms.spec import Field, initial_text, parse_value

    field = Field("share_pct", "share", "share")

    assert parse_value(field, initial_text(field, 3333)) == 3333


# --- vocabularies are read from the enum, never restated ----------------------


def test_every_form_select_vocabulary_reads_its_enum() -> None:
    """forms/entities.py hand-typed five vocabularies that models.py already
    defines as StrEnums, in the one place a user PICKS from them (DRY,
    CLAUDE.md 2026-08-20). Restating a vocabulary is not merely noise: a copy
    that drifts either offers a value the model refuses, or hides one it
    accepts, and neither mypy nor any test would have seen it — the same
    failure that put a fourth copy of towerkit's retention types inside a
    Jinja template.

    Asserted here, and by inspecting the module SOURCE, because reading the
    tuples back proves only that they agree today."""
    import inspect

    from bookkit.forms import entities as ef
    from bookkit.models import (
        AppetiteLevel,
        MarketType,
        OrgKind,
        OrgStatus,
        PlacementStatus,
        SubmissionStatus,
    )

    assert ef._STATUS == tuple((s.value, s.value) for s in OrgStatus)
    assert ef._KINDS == tuple((k.value, k.value) for k in OrgKind)
    assert ef._MARKET_TYPES == tuple((m.value, m.value) for m in MarketType)
    assert ef._PLACEMENT_STATUS == tuple((s.value, s.value) for s in PlacementStatus)
    assert ef._APPETITE == tuple((a.value, a.value) for a in AppetiteLevel)
    # every outcome EXCEPT 'out', which is the state the submission is
    # already in and is not a legal thing to RECORD
    assert ef._RESPONSE == tuple(
        (s.value, s.value) for s in SubmissionStatus if s is not SubmissionStatus.OUT
    )

    # ...and the module must not spell any of those values out AT ALL: the
    # defaults were the other half of the copy ("prospect" as an initial, a
    # "market" kind passed to a query). A default naming a value the enum has
    # since renamed is worse than a stale option — Select refuses a value
    # missing from its own options, so the form would not open.
    # Field LABELS are excluded: "market" is both a value of OrgKind and the
    # word printed beside the market picker, and a scan that cannot tell them
    # apart would either fail on correct code or be switched off.
    source = "\n".join(
        line for line in inspect.getsource(ef).splitlines() if "Field(" not in line
    )
    spelled = [
        member.value
        for member in (*OrgStatus, *OrgKind, *MarketType, *PlacementStatus, *AppetiteLevel)
        if f'"{member.value}"' in source
    ]
    assert spelled == [], f"vocabulary values spelled out again: {spelled}"
