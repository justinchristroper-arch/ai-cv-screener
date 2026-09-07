"""Text normalization: deterministic, and faithful to the source.

Every evidence span in later phases is verified against normalized text, so two
properties are load-bearing: the same input always produces the same output,
and nothing meaningful is lost on the way. These tests target both.

No database and no network — pure functions.
"""

from __future__ import annotations

import pytest

from app.core.text import NORMALIZATION_VERSION, has_meaningful_text, normalize_text

# --------------------------------------------------------------------------
# Whitespace and line endings
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\r\nb", "a\nb"),
        ("a\rb", "a\nb"),
        ("a\r\n\r\nb", "a\n\nb"),
    ],
)
def test_line_endings_are_unified(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_runs_of_spaces_and_tabs_collapse() -> None:
    assert normalize_text("Python        SQL\t\tDocker") == "Python SQL Docker"


def test_trailing_whitespace_is_stripped_per_line() -> None:
    assert normalize_text("line one   \nline two\t\n") == "line one\nline two"


def test_excess_blank_lines_collapse_to_one() -> None:
    assert normalize_text("a\n\n\n\n\n\nb") == "a\n\nb"


def test_a_single_blank_line_is_preserved() -> None:
    """Paragraph structure is meaning; arbitrary vertical padding is not."""
    assert normalize_text("SKILLS\n\nPython") == "SKILLS\n\nPython"


def test_leading_and_trailing_whitespace_is_stripped() -> None:
    assert normalize_text("\n\n  Alex Rivera  \n\n") == "Alex Rivera"


# --------------------------------------------------------------------------
# Unicode and control characters
# --------------------------------------------------------------------------


def test_non_breaking_space_becomes_an_ordinary_space() -> None:
    assert normalize_text("Alex Rivera") == "Alex Rivera"


def test_zero_width_characters_are_removed() -> None:
    assert normalize_text("Py​thon") == "Python"


def test_byte_order_mark_is_removed() -> None:
    assert normalize_text("﻿Alex") == "Alex"


def test_control_characters_are_removed() -> None:
    assert normalize_text("Alex\x00\x07Rivera") == "AlexRivera"


def test_form_feed_is_removed() -> None:
    """PDF extraction emits form feeds as page breaks; pages are handled separately."""
    assert normalize_text("page one\x0cpage two") == "page onepage two"


def test_tabs_and_newlines_survive_as_layout() -> None:
    assert normalize_text("a\tb") == "a b"
    assert "\n" in normalize_text("a\nb")


def test_accented_characters_are_preserved() -> None:
    """A CV belonging to someone with an accented name must survive intact."""
    assert normalize_text("Renée Müller — Software Engineer") == (
        "Renée Müller — Software Engineer"
    )


def test_composed_and_decomposed_forms_normalize_alike() -> None:
    """NFC means the same visible text compares equal however it was encoded."""
    composed = "Renée"
    decomposed = "Renée"

    assert normalize_text(composed) == normalize_text(decomposed)


# --------------------------------------------------------------------------
# Faithfulness — the rules that matter most
# --------------------------------------------------------------------------


def test_meaningful_content_is_preserved() -> None:
    raw = "Alex Rivera\r\nBackend Engineer\r\n\r\nSKILLS   \r\nPython,   FastAPI,   PostgreSQL\r\n"

    result = normalize_text(raw)

    for token in ("Alex Rivera", "Backend Engineer", "SKILLS", "Python", "FastAPI", "PostgreSQL"):
        assert token in result


def test_normalization_does_not_reorder_or_rewrite() -> None:
    """Nothing is summarized, reordered, or invented — only whitespace changes."""
    raw = "Second line\nFirst line"

    assert normalize_text(raw) == "Second line\nFirst line"


def test_punctuation_and_numbers_are_untouched() -> None:
    raw = "2021-2025 | +44 (0)20 7946 0000 | 95% uplift; £42,000"

    assert normalize_text(raw) == raw


def test_normalization_is_idempotent() -> None:
    """Normalizing twice must equal normalizing once, or offsets could drift."""
    raw = "Alex Rivera  \r\n\r\n\r\n  Backend\t\tEngineer\x00"

    once = normalize_text(raw)

    assert normalize_text(once) == once


def test_normalization_is_deterministic() -> None:
    raw = "Alex Rivera\r\n\r\n\r\nBackend    Engineer"

    assert len({normalize_text(raw) for _ in range(20)}) == 1


def test_empty_and_whitespace_only_input() -> None:
    assert normalize_text("") == ""
    assert normalize_text("   \n\n\t  ") == ""


# --------------------------------------------------------------------------
# has_meaningful_text
# --------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\n\n", "\t", " \n \t "])
def test_blank_text_is_not_meaningful(blank: str) -> None:
    assert has_meaningful_text(blank) is False


@pytest.mark.parametrize("content", ["a", "Alex Rivera", "  x  "])
def test_any_content_is_meaningful(content: str) -> None:
    assert has_meaningful_text(content) is True


def test_the_version_is_recorded_and_stable() -> None:
    """Stored on every parsed document; changing the rules must change this."""
    assert NORMALIZATION_VERSION == "text-normalize-v1"
