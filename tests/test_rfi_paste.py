from __future__ import annotations

from bookkit.tui.widgets.rfi_paste import split_items


def test_strips_numbering_and_bullets() -> None:
    assert split_items(
        "1. How many vehicles?\n"
        "2) Loss runs 2021-2025\n"
        "- Safety manual\n"
        "• EMR letter\n"
        "* Payroll by class"
    ) == [
        "How many vehicles?",
        "Loss runs 2021-2025",
        "Safety manual",
        "EMR letter",
        "Payroll by class",
    ]


def test_skips_blank_lines_and_trims() -> None:
    assert split_items("  a  \n\n\n   \n b ") == ["a", "b"]


def test_handles_crlf() -> None:
    assert split_items("a\r\nb\r\n") == ["a", "b"]


def test_single_line_and_empty() -> None:
    assert split_items("just one") == ["just one"]
    assert split_items("") == []
    assert split_items("   \n  ") == []


def test_leaves_inner_punctuation_alone() -> None:
    """Only LEADING markers go; a hyphen mid-sentence must survive."""
    assert split_items("1. Loss runs - all years") == ["Loss runs - all years"]


def test_does_not_strip_a_bare_number_that_is_the_question() -> None:
    assert split_items("2026 payroll figures") == ["2026 payroll figures"]
