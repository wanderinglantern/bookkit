"""Money in integer minor units (cents). Never floats, never round().

Parsing and compact formatting reuse towerkit's parser (which speaks whole
dollars); the ×100 conversion to cents happens here and only here. towerkit
JSON files also carry whole dollars — sync.py uses these same helpers.
"""

from __future__ import annotations

import re

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


# a plain decimal amount: "1,234.56", "$1234.5". Shorthand ("2m", "250k")
# and thousands-separated whole amounts go to towerkit's parser below.
_CENTS_RE = re.compile(r"^\$?\s*(-?\d{1,3}(?:,\d{3})*|-?\d+)\.(\d{1,2})$")


def parse_money_cents(text: str) -> int:
    """'2m', '250k', '$1,500,000', '1,234.56' → integer cents.

    bookkit stores CENTS, so its own parser has to accept them: format_cents
    renders a stored 123456 as "$1,234.56", and a form that pre-fills a value
    its parser then refuses makes the whole record unsaveable — not the
    status, not the dates — until the money is manually rounded, destroying
    the cents in the process.

    The whole-dollar rule belongs to towerkit files, not to entry, and it
    stays enforced where it applies: cents_to_dollars still refuses sub-dollar
    amounts on write-through."""
    match = _CENTS_RE.match(text.strip())
    if match:
        whole_text, frac_text = match.group(1), match.group(2).ljust(2, "0")
        whole = int(whole_text.replace(",", ""))
        cents = abs(whole) * 100 + int(frac_text)
        return -cents if whole_text.lstrip("$ ").startswith("-") else cents
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
    """Share entry: '25%', '25', '12.5' → basis points. Percent semantics —
    delegated to towerkit, which owns the tower-grammar parsers (DRY: one
    authoritative percent→bps rule across both tools)."""
    from towerkit.money import parse_share

    return parse_share(text)


def format_share_pct(bps: int) -> str:
    """Basis points → the display form, '3500' → '35%'. Delegated for the same
    reason parse_share_bps is: towerkit owns the percent↔bps grammar, and two
    formatters is how the same share reads differently on two surfaces."""
    from towerkit.money import format_share

    return format_share(bps)


def commission_cents(premium_cents: int, commission_bps: int) -> int:
    """Commission on a premium, floor-divided so money stays integer."""
    return premium_cents * commission_bps // BPS_SCALE


def weighted_cents(amount_cents: int, probability_pct: int) -> int:
    """Probability-weighted pipeline value, floor-divided."""
    return amount_cents * probability_pct // 100
