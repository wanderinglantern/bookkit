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
    "ENTRY_FORMS",
    "RATE_FORMS",
    "RATE_SCALE",
    "COUNT_FORMS",
    "STORE_MAX",
    "money_refusal",
    "rate_refusal",
    "count_refusal",
    "parse_money_cents",
    "parse_rate_micros",
    "parse_count",
    "format_count",
    "format_rate_micros",
    "format_cents",
    "format_cents_compact",
    "dollars_to_cents",
    "cents_to_dollars",
    "commission_cents",
    "weighted_cents",
]

_LOCALE = "en_US"
BPS_SCALE = 10_000

# THE CEILING IS THE STORE'S, AND IT IS REFUSED HERE (found 2026-08-25). A
# pasted 20-digit premium parsed cleanly, was multiplied into cents, and then
# failed inside the INSERT — so the sentence a broker read in the premium cell
# was "Python int too large to convert to SQLite INTEGER". A refusal says
# something, in this book's own words, never a library's; and a figure that
# cannot be stored has to be refused before any writer sees it, not caught
# afterwards by whichever route happens to be catching.
#
# The bound is SQLite's signed 64-bit INTEGER, which is what every one of these
# columns is, so nothing storable is refused by it — this is the storage limit
# stated out loud, not a judgment about how large an amount insurance permits.
# It applies to all three parsers because all three write integer columns: a
# 25-digit exposure count and a 25-digit rate overflow the same way a premium
# does.
STORE_MAX = 2**63 - 1

# The forms a money entry may take, named ONCE: the placeholder every surface
# shows (forms.spec.PLACEHOLDERS) and the sentence every refusal gives are the
# same three examples, so a hint and a refusal can never recommend different
# things. Cents are one of them on purpose — bookkit stores cents and
# format_cents renders them, so a message that only showed whole amounts would
# be telling people to destroy the cents they were just handed.
ENTRY_FORMS = ("1.5m", "250k", "1,234.56")

# A RATE IS NOT MONEY, and it lives here anyway because this is the module that
# owns numeric ENTRY: one parser, one formatter, one scale, one refusal. There
# were three copies of the ×1,000,000 before this — mcpserver._rate_micros,
# marketing_report._MICROS and whatever the next surface wrote — and the copy
# that quietly differs is the one that files a 1.42 rate as 1.42 micros.
#
# Stored ×1,000,000 because a rate rounded to cents lies at the fourth decimal,
# which is exactly where casualty rates differ. No currency symbol and no
# percent sign: 1.42 is 1.42 per unit of exposure, and the unit is the rating
# basis's business, not this function's.
RATE_SCALE = 1_000_000
RATE_FORMS = ("1.42", "0.0850", "12")

# A COUNT IS NOT MONEY EITHER, and it lives here for the same reason the rate
# does: this is the module that owns numeric ENTRY. A non-monetary rating
# basis measures a WHOLE COUNT — 42 power units, 1,200 employees — and 42
# power units and $0.42 are the same digits, so the two must not share a
# parser. models.RatingBasis.monetary is the one place that decides WHICH of
# the two a given exposure is; this is only the parser for the count half.
COUNT_FORMS = ("42", "350", "1,200")


# a plain decimal amount: "1,234.56", "$1234.5". Shorthand ("2m", "250k")
# and thousands-separated whole amounts go to towerkit's parser below.
# NO leading minus: negatives are refused above, by name (Grant, 2026-08-20).
_CENTS_RE = re.compile(r"^\$?\s*(\d{1,3}(?:,\d{3})*|\d+)\.(\d{1,2})$")

# towerkit's generic "it did not parse" wording, which says nothing our own
# sentence does not already say better. Its SPECIFIC objections (an unknown
# suffix, mixed grouping) do add something and are kept.
_GENERIC = "cannot parse money value"


def money_refusal(text: str, why: str = "") -> str:
    """The one sentence every surface gives when an amount will not parse.

    Same shape as forms.spec.date_refusal, which is the house model: name the
    offending value, then name three forms that WOULD be accepted, then the
    specific objection when there is one. The old message was towerkit's
    `cannot parse money value: '1.2mm'` — an objection with no remedy, which
    the data-entry rules call half a message."""
    forms = ", ".join(ENTRY_FORMS[:-1]) + f" or {ENTRY_FORMS[-1]}"
    return f"{text!r} is not an amount — enter one like {forms}" + (f"; {why}" if why else "")


def rate_refusal(text: str, why: str = "") -> str:
    """The one sentence every surface gives when a rate will not parse.

    Same shape as money_refusal and date_refusal: the offending value, then
    what WOULD be accepted. A rate refused with "could not convert string to
    float" tells a broker nothing about the fact that the percent sign is what
    the field objects to."""
    forms = ", ".join(RATE_FORMS[:-1]) + f" or {RATE_FORMS[-1]}"
    return (
        f"{text!r} is not a rate — enter a rate like {forms}, per unit of "
        f"exposure, with no currency symbol and no percent sign"
        + (f"; {why}" if why else "")
    )


def count_refusal(text: str, why: str = "") -> str:
    """The one sentence every surface gives when a count will not parse.

    Same shape as money_refusal and rate_refusal: name the value, then name
    what WOULD be accepted. The remedy matters more here than anywhere else in
    this module, because the value that reaches this refusal is almost always
    an amount typed into a field that counts things — and the fix is not
    "format it differently", it is "this basis counts, it does not measure
    money"."""
    forms = ", ".join(COUNT_FORMS[:-1]) + f" or {COUNT_FORMS[-1]}"
    return (
        f"{text!r} is not a whole count — a non-monetary rating basis counts "
        f"things, so enter a whole number like {forms}, with no decimals and "
        f"no currency symbol" + (f"; {why}" if why else "")
    )


def parse_count(text: str) -> int:
    """'350', '1,200' → the stored whole count.

    REFUSES A FRACTION rather than flooring it. `int(float("1,234.56"))` would
    file 1,234 power units against a figure somebody typed as an amount, and
    the report would then print a rate per unit computed off a number nobody
    gave — the silent half of the same mistake that made 350 power units
    render to a client as $3.50. Thousands separators are accepted because a
    count of 1,200 employees is written that way; the decimal point is the
    thing being refused, not the comma.

    Negative is refused by the same digits check: nothing is counted below
    zero, and a minus here is a typo, never an entry."""
    cleaned = str(text).strip().replace(",", "").replace("_", "")
    if not cleaned:
        raise MoneyParseError(count_refusal(text))
    if not cleaned.isdigit():
        why = "a count is whole" if "." in cleaned else ""
        raise MoneyParseError(count_refusal(text, why))
    count = int(cleaned)
    if count > STORE_MAX:
        raise MoneyParseError(count_refusal(text, "that is more than this book can record"))
    return count


def format_count(count: int) -> str:
    """The stored count back as entry text: grouped, no unit. What this prints,
    parse_count accepts back unchanged — which is what makes opening a count
    cell to read it cost nothing (the unchanged-value guard compares the two)."""
    return f"{count:,}"


def parse_rate_micros(text: str) -> int:
    """'1.42', '0.0850', '8.10' → the stored rate, ×1,000,000.

    Decimal rather than float: 8.10 * 1_000_000 is 8100000.0000000009 in
    binary floating point, and a rate that round-trips through a cell editor
    must come back the same number it went in as. A negative rate is refused
    by name rather than stored — nobody is paid to take risk."""
    from decimal import Decimal, InvalidOperation

    cleaned = str(text).strip().replace(",", "")
    if not cleaned:
        raise MoneyParseError(rate_refusal(text))
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise MoneyParseError(rate_refusal(text)) from None
    if not value.is_finite():
        raise MoneyParseError(rate_refusal(text))
    if value < 0:
        raise MoneyParseError(rate_refusal(text, "a rate is never negative"))
    micros = int((value * RATE_SCALE).to_integral_value())
    if micros > STORE_MAX:
        raise MoneyParseError(rate_refusal(text, "that is higher than this book can record"))
    return micros


def format_rate_micros(micros: int) -> str:
    """The stored rate back as entry text: two decimals, NO currency symbol and
    no thousands separator — a grouped 1,010.05 in a rate column reads as a
    premium. What this prints, parse_rate_micros accepts back unchanged."""
    return f"{micros / RATE_SCALE:.2f}"


def _objection(exc: MoneyParseError, text: str) -> str:
    """towerkit's specific complaint with the value stripped back out — the
    sentence around it already names the value once, and twice reads as a
    stutter."""
    message = str(exc).replace(f" in {text!r}", "").replace(f": {text!r}", "")
    message = message.replace(f"{text!r} ", "")
    return "" if message.startswith(_GENERIC) else message


def parse_money_cents(text: str) -> int:
    """'2m', '250k', '$1,500,000', '1,234.56' → integer cents.

    bookkit stores CENTS, so its own parser has to accept them: format_cents
    renders a stored 123456 as "$1,234.56", and a form that pre-fills a value
    its parser then refuses makes the whole record unsaveable — not the
    status, not the dates — until the money is manually rounded, destroying
    the cents in the process.

    The whole-dollar rule belongs to towerkit files, not to entry, and it
    stays enforced where it applies: cents_to_dollars still refuses sub-dollar
    amounts on write-through.

    NEGATIVES ARE REFUSED (Grant, 2026-08-20). They were refused
    INCONSISTENTLY, which is worse than either answer: "-1000" and "-1m" fell
    through to towerkit and were refused, while "-1,000.00" took the cents
    branch and stored -100000 — a premium that subtracts from the book on one
    entry form and not on another. The check is here, ahead of both branches,
    so every surface (forms, MCP arguments, the importers) inherits it."""
    text = text.strip()
    if text.lstrip("$").strip().startswith("-"):
        raise MoneyParseError(money_refusal(text, "amounts are positive"))
    match = _CENTS_RE.match(text)
    if match:
        whole, frac_text = match.group(1).replace(",", ""), match.group(2).ljust(2, "0")
        return _storable(int(whole) * 100 + int(frac_text), text)
    try:
        cents = parse_money(text) * 100
    except MoneyParseError as exc:
        raise MoneyParseError(money_refusal(text, _objection(exc, text))) from exc
    return _storable(cents, text)


def _storable(cents: int, text: str) -> int:
    """Refused HERE, ahead of both entry forms, for the same reason negatives
    are: a bound enforced on one branch and not the other is a figure this book
    accepts as "1,234.56" and refuses as "1234.56"."""
    if cents > STORE_MAX:
        raise MoneyParseError(
            money_refusal(text, "that is more money than this book can record")
        )
    return cents


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
