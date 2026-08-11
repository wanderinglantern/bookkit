"""Money in integer minor units (cents). Never floats, never round().

Parsing and compact formatting reuse towerkit's parser (which speaks whole
dollars); the ×100 conversion to cents happens here and only here. towerkit
JSON files also carry whole dollars — sync.py uses these same helpers.
"""

from __future__ import annotations

from babel.numbers import format_currency
from towerkit.money import MoneyParseError, format_money, format_money_compact, parse_money

__all__ = [
    "MoneyParseError",
    "parse_money_cents",
    "format_cents",
    "format_cents_compact",
    "dollars_to_cents",
    "cents_to_dollars",
    "commission_cents",
    "weighted_cents",
]

_LOCALE = "en_US"
BPS_SCALE = 10_000


def parse_money_cents(text: str) -> int:
    """'2m', '250k', '$1,500,000' → integer cents. Rejects ambiguity."""
    return parse_money(text) * 100


def format_cents(cents: int) -> str:
    """Full form: 200000000 → '$2,000,000'; keeps cents only when present."""
    if cents % 100 == 0:
        return format_money(cents // 100)
    return format_currency(cents / 100, "USD", format="¤#,##0.00", locale=_LOCALE)


def format_cents_compact(cents: int) -> str:
    """Compact label form: 2500000000 → '$25M'. Sub-dollar residue is display
    noise at label scale and is floored away."""
    return format_money_compact(cents // 100)


def dollars_to_cents(dollars: int) -> int:
    """towerkit files carry whole dollars; bookkit stores cents."""
    return dollars * 100


def cents_to_dollars(cents: int) -> int:
    """Cents → whole dollars for write-through to towerkit files. Refuses to
    lose sub-dollar amounts silently."""
    if cents % 100:
        raise MoneyParseError(f"{cents} cents is not a whole number of dollars")
    return cents // 100


def parse_share_bps(text: str) -> int:
    """Share entry: '25%', '25', '12.5', '33.34%' → basis points. Everything
    is read as a PERCENT (the % sign optional) — brokers speak percent, and
    one consistent rule beats guessing whether 0.25 meant a quarter share.
    Sub-basis-point precision and values outside (0, 100] are rejected."""
    from decimal import Decimal, InvalidOperation

    cleaned = text.strip().rstrip("%").strip()
    try:
        pct = Decimal(cleaned)
    except InvalidOperation as exc:
        raise MoneyParseError(f"cannot read a share from {text!r}") from exc
    scaled = pct * 100  # percent → bps
    if scaled != scaled.to_integral_value():
        raise MoneyParseError(f"{text!r} has sub-basis-point precision")
    bps = int(scaled)
    if not 0 < bps <= BPS_SCALE:
        raise MoneyParseError(f"{text!r} is not a share between 0% and 100%")
    return bps


def commission_cents(premium_cents: int, commission_bps: int) -> int:
    """Commission on a premium, floor-divided so money stays integer."""
    return premium_cents * commission_bps // BPS_SCALE


def weighted_cents(amount_cents: int, probability_pct: int) -> int:
    """Probability-weighted pipeline value, floor-divided."""
    return amount_cents * probability_pct // 100
