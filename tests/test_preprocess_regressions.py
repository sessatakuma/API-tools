from __future__ import annotations

from api.accent.models import WordAccentResult
from api.accent.preprocess import (
    restore_number_commas,
    restore_urls,
    restore_x_between_digits,
    strip_number_commas,
    strip_urls,
    strip_x_between_digits,
)


def _word(surface: str) -> WordAccentResult:
    return WordAccentResult(surface=surface, furigana=surface, accent=[], subword=[])


def test_url_restore_does_not_consume_literal_placeholder() -> None:
    cleaned, rewrites = strip_urls("URLPLACEHOLDERとhttps://x.comです")
    assert cleaned == "URLPLACEHOLDERと「URLPLACEHOLDER」です"
    restored = restore_urls(
        [
            _word("URLPLACEHOLDER"),
            _word("と"),
            _word("「"),
            _word("URLPLACEHOLDER"),
            _word("」"),
            _word("です"),
        ],
        rewrites,
    )
    assert [word.surface for word in restored] == [
        "URLPLACEHOLDER",
        "と",
        "https://x.com",
        "です",
    ]


def test_number_comma_restore_targets_formatted_occurrence() -> None:
    cleaned, rewrites = strip_number_commas("1234円と1,234円です")
    assert cleaned == "1234円と1234円です"
    restored = restore_number_commas(
        [_word("1234"), _word("円と"), _word("1234"), _word("円です")],
        rewrites,
    )
    assert [word.surface for word in restored] == [
        "1234",
        "円と",
        "1,234",
        "円です",
    ]


def test_multiplication_restore_leaves_literal_fraction_unchanged() -> None:
    cleaned, rewrites = strip_x_between_digits("1/2と19×19です")
    assert cleaned == "1/2と19と19です"
    restored = restore_x_between_digits(
        [
            _word("1"),
            _word("/"),
            _word("2と"),
            _word("19"),
            _word("と"),
            _word("19です"),
        ],
        rewrites,
    )
    assert [word.surface for word in restored] == [
        "1",
        "/",
        "2と",
        "19",
        "×",
        "19です",
    ]
    assert restored[4].furigana == ""
    assert restored[4].accent == []


def test_url_restore_uses_whitespace_free_surface_coordinates() -> None:
    _cleaned, rewrites = strip_urls("foo https://x.com猫")
    restored = restore_urls(
        [
            _word("foo"),
            _word("「"),
            _word("URLPLACEHOLDER"),
            _word("」"),
            _word("猫"),
        ],
        rewrites,
    )
    assert [word.surface for word in restored] == ["foo", "https://x.com", "猫"]
    assert restored[1].furigana == ""


def test_number_restore_uses_whitespace_free_surface_coordinates() -> None:
    _cleaned, rewrites = strip_number_commas("foo 1,234猫")
    restored = restore_number_commas(
        [_word("foo"), _word("1234"), _word("猫")],
        rewrites,
    )
    assert [word.surface for word in restored] == ["foo", "1,234", "猫"]


def test_multiplication_restore_uses_whitespace_free_surface_coordinates() -> None:
    _cleaned, rewrites = strip_x_between_digits("foo 19×19猫")
    restored = restore_x_between_digits(
        [_word("foo"), _word("19"), _word("と"), _word("19"), _word("猫")],
        rewrites,
    )
    assert [word.surface for word in restored] == ["foo", "19", "×", "19", "猫"]


def test_multiplication_restore_preserves_surrounding_whitespace() -> None:
    cleaned, rewrites = strip_x_between_digits("1 × 2")
    assert cleaned == "1と2"

    restored = restore_x_between_digits(
        [_word("1"), _word("と"), _word("2")],
        rewrites,
    )

    assert "".join(word.surface for word in restored) == "1 × 2"


def test_layered_rewrites_share_one_logical_coordinate_system() -> None:
    text, urls = strip_urls("foo 1 × 2 1,234猫https://x.com")
    text, numbers = strip_number_commas(text)
    _text, multiplications = strip_x_between_digits(text)
    result = [
        _word("foo"),
        _word("1"),
        _word("と"),
        _word("2"),
        _word("1234"),
        _word("猫"),
        _word("「"),
        _word("URLPLACEHOLDER"),
        _word("」"),
    ]
    result = restore_x_between_digits(result, multiplications)
    result = restore_number_commas(result, numbers)
    result = restore_urls(result, urls)
    assert [word.surface for word in result] == [
        "foo",
        "1",
        " × ",
        "2",
        "1,234",
        "猫",
        "https://x.com",
    ]
