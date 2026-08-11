"""Type fast, store clean: normalisation rules for every non-date, non-money
field kind."""

from __future__ import annotations

import pytest

from bookkit.normalize import (
    clean_domain,
    clean_email,
    clean_linkedin,
    clean_naics,
    clean_phone,
    clean_text,
    clean_url,
)


def test_clean_text_collapses_whitespace() -> None:
    assert clean_text("  Atomic   Industries,  Inc.  ") == "Atomic Industries, Inc."


def test_clean_email() -> None:
    assert clean_email(" Rosa.Silva@EXAMPLE.COM ") == "Rosa.Silva@example.com"
    assert clean_email("mailto:bob@corp.io") == "bob@corp.io"
    assert clean_email("Rosa Silva <rosa@corp.io>,") == "rosa@corp.io"
    for bad in ("not-an-email", "a@b", "two@@at.com", "spaced @b.com"):
        with pytest.raises(ValueError):
            clean_email(bad)


def test_clean_phone_us_forms() -> None:
    assert clean_phone("312.555.0142") == "(312) 555-0142"
    assert clean_phone("(312) 555 0142") == "(312) 555-0142"
    assert clean_phone("1-312-555-0142") == "+1 (312) 555-0142"
    assert clean_phone("+1 312 555 0142") == "+1 (312) 555-0142"
    assert clean_phone("312-555-0142 ext. 44") == "(312) 555-0142 x44"
    assert clean_phone("312.555.0142 x9") == "(312) 555-0142 x9"


def test_clean_phone_international_passthrough() -> None:
    assert clean_phone("+44 20 7946 0958") == "+442079460958"
    with pytest.raises(ValueError):
        clean_phone("call me")


def test_clean_url() -> None:
    assert clean_url("Example.COM") == "https://example.com"
    assert clean_url("www.example.com/About-Us") == "https://www.example.com/About-Us"
    assert clean_url("http://example.com/") == "http://example.com"


def test_clean_domain() -> None:
    assert clean_domain("https://www.Example.com/about") == "example.com"
    assert clean_domain("Example.com") == "example.com"
    with pytest.raises(ValueError):
        clean_domain("not a domain")


def test_clean_linkedin() -> None:
    expected = "https://www.linkedin.com/in/rosa-silva"
    assert clean_linkedin("https://www.linkedin.com/in/rosa-silva/") == expected
    assert clean_linkedin("linkedin.com/in/rosa-silva") == expected
    assert clean_linkedin("in/rosa-silva") == expected
    assert clean_linkedin("rosa-silva") == expected
    assert clean_linkedin("@rosa-silva") == expected
    with pytest.raises(ValueError):
        clean_linkedin("rosa silva profile")


def test_clean_naics() -> None:
    assert clean_naics("332999") == "332999"
    assert clean_naics("NAICS 3329") == "3329"
    with pytest.raises(ValueError):
        clean_naics("1234567")
