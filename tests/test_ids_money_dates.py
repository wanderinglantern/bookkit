from __future__ import annotations

import sqlite3
import time
from datetime import date

import pytest

from bookkit.dates import days_until, parse_human_date
from bookkit.ids import new_ulid, next_ref
from bookkit.money import (
    MoneyParseError,
    cents_to_dollars,
    commission_cents,
    dollars_to_cents,
    format_cents,
    format_cents_compact,
    parse_money_cents,
    weighted_cents,
)

# Crockford base32, spelled out here rather than imported from bookkit.ids so
# the decode below is an independent reading of the ULID, not the generator
# agreeing with itself.
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid_ms(ulid: str) -> int:
    """The 48-bit millisecond stamp, decoded out of the first 10 chars."""
    value = 0
    for ch in ulid[:10]:
        value = value * 32 + _CROCKFORD32.index(ch)
    return value


def test_ulid_shape_and_ordering() -> None:
    a, b = new_ulid(), new_ulid()
    assert len(a) == len(b) == 26
    assert a != b
    assert a[:8] <= b[:8]  # time-prefixed, lexically sortable
    # `<=` alone is satisfied by EQUALITY: two ULIDs minted inside the same
    # millisecond share a prefix, so zeroing the timestamp outright kept this
    # green (2026-08-18) — while event_log ordering and every ref depend on it.
    now_ms = time.time() * 1000
    assert abs(_ulid_ms(a) - now_ms) < 60_000, "no real time in the prefix"
    time.sleep(0.005)
    later = new_ulid()
    assert _ulid_ms(later) > _ulid_ms(a), "the clock did not advance across 5ms"
    assert a[:10] < later[:10], "the prefix is not lexically ordered by time"


def test_refs_count_per_type(conn: sqlite3.Connection) -> None:
    assert next_ref(conn, "ACC") == "ACC-0001"
    assert next_ref(conn, "ACC") == "ACC-0002"
    assert next_ref(conn, "OPP") == "OPP-0001"
    assert next_ref(conn, "ACC") == "ACC-0003"


def test_money_parse_to_cents() -> None:
    assert parse_money_cents("2m") == 200_000_000
    assert parse_money_cents("250k") == 25_000_000
    assert parse_money_cents("$1,500,000") == 150_000_000
    with pytest.raises(MoneyParseError):
        parse_money_cents("2mm")


def test_money_format_round_trip() -> None:
    assert format_cents(200_000_000) == "$2,000,000"
    assert format_cents(123_456) == "$1,234.56"
    assert format_cents_compact(2_500_000_000) == "$25M"


def test_money_integer_only() -> None:
    assert dollars_to_cents(1_000_000) == 100_000_000
    assert cents_to_dollars(100_000_000) == 1_000_000
    with pytest.raises(MoneyParseError):
        cents_to_dollars(100_000_050)
    # floor division, never round(). 100_000_001 does NOT defend that: it is
    # 15_000_000.15, which floors and rounds the same way, so swapping in
    # round() left this green (2026-08-18). These two land past the halfway
    # mark, where floor and round part company.
    assert commission_cents(100_000_001, 1500) == 15_000_000
    assert commission_cents(100_000_005, 1500) == 15_000_000  # .75 -> floors down
    assert commission_cents(1_999, 5_000) == 999  # 999.5 -> floors, never 1_000
    assert weighted_cents(99, 50) == 49  # 49.5 -> floors, never 50


def test_relative_dates() -> None:
    today = date(2026, 8, 11)  # a Tuesday
    assert parse_human_date("today", today) == today
    assert parse_human_date("tomorrow", today) == date(2026, 8, 12)
    assert parse_human_date("+2w", today) == date(2026, 8, 25)
    assert parse_human_date("+3d", today) == date(2026, 8, 14)
    assert parse_human_date("+1m", today) == date(2026, 9, 11)
    assert parse_human_date("+1y", today) == date(2027, 8, 11)
    assert parse_human_date("2026-10-15", today) == date(2026, 10, 15)
    assert parse_human_date("", today) is None
    assert parse_human_date("gibberish never a date", today) is None


def test_human_date_forms() -> None:
    today = date(2026, 8, 11)
    assert parse_human_date("15 oct", today) == date(2026, 10, 15)
    fri = parse_human_date("fri", today)
    assert fri is not None and fri.weekday() == 4 and fri > today


def test_month_end_clamp() -> None:
    assert parse_human_date("+1m", date(2026, 1, 31)) == date(2026, 2, 28)


def test_days_until() -> None:
    assert days_until("2026-08-21", date(2026, 8, 11)) == 10
    assert days_until("2026-08-01", date(2026, 8, 11)) == -10
