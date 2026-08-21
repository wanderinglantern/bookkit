"""What a number entry will and will not take, and what it SAYS when it won't.

Three rules live here, all of them about the one parser both surfaces share:

* negatives are refused, consistently (they were refused on two of three
  spellings, which is how -100000 got stored);
* a field's numeric range is enforced before SQLite gets a chance to answer
  with `CHECK constraint failed`;
* every refusal is shaped like `date_refusal` — the offending value, then
  what would be accepted.
"""

from __future__ import annotations

import pytest

from bookkit.forms import entities, inline
from bookkit.forms.spec import (
    BOUNDS,
    PLACEHOLDERS,
    Field,
    FormSpec,
    date_refusal,
    parse_value,
    parse_values,
)
from bookkit.money import ENTRY_FORMS, MoneyParseError, money_refusal, parse_money_cents

PROBABILITY = Field("probability_pct", "probability %", "int")
COMMISSION = Field("commission_bps", "commission (bps)", "int")
PREMIUM = Field("total_premium", "premium", "money")


# --- negatives ----------------------------------------------------------------


@pytest.mark.parametrize("text", ["-1,000.00", "-1000", "-1m", "-$5", "$-5", "-0.01"])
def test_every_spelling_of_a_negative_is_refused(text: str) -> None:
    """The bug was the INCONSISTENCY. `-1,000.00` matched the cents branch and
    came back as -100000 while `-1000` and `-1m` fell through to towerkit and
    were refused: the same amount, two answers, chosen by how it was typed."""
    with pytest.raises(MoneyParseError) as err:
        parse_money_cents(text)
    assert "amounts are positive" in str(err.value)
    assert repr(text.strip()) in str(err.value), "the refusal must name the value"


def test_a_negative_is_refused_through_the_form_parser_too() -> None:
    """Not just the parser in isolation: the money field is the door people
    actually type through, and it must not be reachable with a minus."""
    with pytest.raises(ValueError, match="amounts are positive"):
        parse_value(PREMIUM, "-1,000.00")


def test_positive_money_still_parses_including_cents() -> None:
    """The cents rule is load-bearing (CLAUDE.md): format_cents renders a
    stored 123456 as $1,234.56 and the editor pre-fills exactly that, so a
    parser that refused it would make the record unsaveable. Refusing
    negatives must not cost us the cents branch."""
    assert parse_money_cents("1,234.56") == 123_456
    assert parse_money_cents("$1234.5") == 123_450
    assert parse_money_cents("2m") == 200_000_000
    assert parse_money_cents("$1,500,000") == 150_000_000
    assert parse_money_cents("0.01") == 1


# --- refusal wording ----------------------------------------------------------


def test_money_refusal_names_the_value_and_three_forms_that_work() -> None:
    """date_refusal is the house model: the value, then the remedy. The money
    message used to be towerkit's `cannot parse money value: '1.2mm'` — an
    objection with no fix in it."""
    with pytest.raises(MoneyParseError) as err:
        parse_money_cents("1.2mm")
    message = str(err.value)
    assert "'1.2mm'" in message
    for form in ENTRY_FORMS:
        assert form in message, f"the refusal does not offer {form}"
    # and towerkit's own diagnosis survives, once
    assert "unknown magnitude suffix 'mm'" in message
    assert message.count("'1.2mm'") == 1, "the value is named once, not stuttered"


def test_money_refusal_drops_towerkits_contentless_objection() -> None:
    """'cannot parse money value' says nothing our sentence has not said."""
    with pytest.raises(MoneyParseError) as err:
        parse_money_cents("wibble")
    assert "cannot parse money value" not in str(err.value)
    assert str(err.value) == money_refusal("wibble")


def test_the_money_hint_and_the_money_refusal_offer_the_same_forms() -> None:
    """DRY, on the thing a user reads. A placeholder that recommends one set of
    forms and a refusal that recommends another teaches people to trust
    neither."""
    assert PLACEHOLDERS["money"] == " · ".join(ENTRY_FORMS)
    refusal = money_refusal("nope")
    assert all(form in refusal for form in ENTRY_FORMS)


def test_int_refusal_names_the_range_when_the_field_has_one() -> None:
    """`'x' is not a whole number` is the other half-message the data-entry
    rules call out by name."""
    with pytest.raises(ValueError) as err:
        parse_value(PROBABILITY, "half")
    assert str(err.value) == "'half' is not a whole number — enter a whole number from 0 to 100"
    with pytest.raises(ValueError) as unbounded:
        parse_value(Field("headcount", "headcount", "int"), "half")
    assert "enter digits only, like 0, 25 or 100" in str(unbounded.value)


def test_select_refusal_names_the_vocabulary_or_the_labels() -> None:
    status = Field("status", "status", "select", (("open", "open"), ("closed", "closed")))
    with pytest.raises(ValueError) as err:
        parse_value(status, "NOT_A_STATUS")
    assert str(err.value) == "'NOT_A_STATUS' must be one of closed, open"

    # a picker's values are opaque ids, so the refusal offers the LABELS
    picker = Field("placement_id", "program", "select", (("PLC-0001 — GL tower", "abc"),))
    with pytest.raises(ValueError) as picked:
        parse_value(picker, "someone-elses-id")
    assert "pick one of PLC-0001 — GL tower" in str(picked.value)
    assert "abc" not in str(picked.value), "an opaque id is not ours to print back"


def test_a_long_picker_is_counted_not_recited() -> None:
    many = Field(
        "market_id", "market", "select", tuple((f"Market {n}", f"id{n}") for n in range(20))
    )
    with pytest.raises(ValueError) as err:
        parse_value(many, "id999")
    assert "(and 12 more)" in str(err.value)
    assert "Market 19" not in str(err.value)


def test_refusals_share_date_refusals_shape() -> None:
    """One wording rule, not four: value first, then an em-dash, then the fix."""
    sentences = [
        date_refusal("5"),
        money_refusal("5x"),
        "'x' is not a whole number — enter a whole number from 0 to 100",
    ]
    for sentence in sentences:
        head, _, fix = sentence.partition(" — ")
        assert head.startswith("'"), sentence
        assert fix and "enter" in fix, sentence


# --- bounds -------------------------------------------------------------------


@pytest.mark.parametrize("text", ["101", "-1", "1000"])
def test_probability_out_of_range_is_refused_with_its_range(text: str) -> None:
    """It used to reach SQLite, which answered `CHECK constraint failed:
    probability_pct BETWEEN 0 AND 100` — the schema talking to a broker."""
    with pytest.raises(ValueError) as err:
        parse_value(PROBABILITY, text)
    assert str(err.value) == f"{text!r} is out of range — enter a whole number from 0 to 100"


def test_probability_accepts_both_ends() -> None:
    """0 and 100 are real values — `lost` sets 0 and `won` sets 100."""
    assert parse_value(PROBABILITY, "0") == 0
    assert parse_value(PROBABILITY, "100") == 100


@pytest.mark.parametrize("text", ["10001", "-1", "100000"])
def test_commission_out_of_range_is_refused_with_its_range(text: str) -> None:
    """A commission is a share, and the share range is towerkit's 0-10000 bps.
    Typed as bps into an `int` field, nothing checked it: 100000 bps is a
    1000% commission and it stored."""
    with pytest.raises(ValueError) as err:
        parse_value(COMMISSION, text)
    assert str(err.value) == f"{text!r} is out of range — enter a whole number from 0 to 10000"


def test_commission_accepts_the_whole_share_range() -> None:
    assert parse_value(COMMISSION, "0") == 0
    assert parse_value(COMMISSION, "1250") == 1250
    assert parse_value(COMMISSION, "10000") == 10000


def test_money_bounds_are_quoted_in_the_units_typed_not_in_cents() -> None:
    """Bounds are stored units (cents); the sentence must say dollars, or a cap
    of $10,000 reads as 'up to 1000000' to the person who has to obey it."""
    capped = Field("fee", "fee", "money", min_value=0, max_value=1_000_000)
    assert parse_value(capped, "9,999.99") == 999_999
    with pytest.raises(ValueError) as err:
        parse_value(capped, "10,000.01")
    assert str(err.value) == "'10,000.01' is out of range — enter an amount from 0 to 10,000"


def test_share_bounds_are_quoted_as_percent() -> None:
    half = Field("share", "share", "share", min_value=0, max_value=5_000)
    assert parse_value(half, "50") == 5_000
    with pytest.raises(ValueError) as err:
        parse_value(half, "60")
    assert str(err.value) == "'60' is out of range — enter a share from 0 to 50"


def test_bounds_refusal_carries_the_field_key_through_parse_values() -> None:
    """So the web can put the message on the right input and the TUI can put
    focus there."""
    spec = FormSpec("edit opportunity", [PROBABILITY])
    with pytest.raises(Exception) as err:
        parse_values(spec, {"probability_pct": "150"})
    assert getattr(err.value, "field_key", None) == "probability_pct"
    assert "from 0 to 100" in str(err.value)


# --- the seam is actually used ------------------------------------------------


def _declared_fields() -> list[Field]:
    """Every Field the two form modules declare, module constants and builders
    alike. A green suite proves nothing broke, not that the new path is taken
    (CLAUDE.md) — the bounds are only real if the fields users type into carry
    them."""
    found: list[Field] = []
    for module in (entities, inline):
        for value in vars(module).values():
            if isinstance(value, Field):
                found.append(value)
            elif isinstance(value, tuple) and value and all(isinstance(v, Field) for v in value):
                found.extend(value)
    for build in (entities.placement_form, entities.opportunity_form):
        found.extend(build().fields)
    return found


def test_every_declared_field_with_a_bounded_key_carries_its_bounds() -> None:
    """commission_bps is declared at three sites and probability_pct at two.
    The bounds are registered once, by column, so a site cannot be missed —
    this asserts that they actually arrive."""
    declared = _declared_fields()
    bounded = [f for f in declared if f.key in BOUNDS]
    assert len(bounded) >= 3, f"the scan found almost nothing: {len(declared)} fields"
    for field in bounded:
        assert (field.min_value, field.max_value) == BOUNDS[field.key], field


def test_an_unregistered_field_is_left_unbounded() -> None:
    """The registry is opt-in by column: nothing else grows a silent cap."""
    plain = Field("headcount", "headcount", "int")
    assert (plain.min_value, plain.max_value) == (None, None)
    assert parse_value(plain, "999999") == 999_999


def test_an_explicit_bound_beats_the_registry() -> None:
    override = Field("probability_pct", "probability %", "int", min_value=0, max_value=50)
    assert override.max_value == 50
