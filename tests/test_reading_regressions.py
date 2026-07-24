from __future__ import annotations

import re

import pytest

from api.accent import reading_overrides
from api.accent.models import WordAccentResult, WordResult
from api.accent.reading_overrides import (
    FuriganaOverride,
    ReplacementToken,
    apply_accent_overrides,
    apply_furigana_overrides,
)


def _word(surface: str, furigana: str) -> WordResult:
    return WordResult(surface=surface, furigana=furigana, pos="名詞")


def _accent_word(surface: str, furigana: str) -> WordAccentResult:
    return WordAccentResult(surface=surface, furigana=furigana, accent=[], subword=[])


@pytest.mark.parametrize(
    "tokens",
    [
        [_word("1", "いち"), _word("日", "にち"), _word("休む", "やすむ")],
        [_word("一", "いち"), _word("日", "にち"), _word("中", "じゅう")],
    ],
)
def test_one_day_is_not_calendar_reading_without_date_context(
    tokens: list[WordResult],
) -> None:
    result = apply_furigana_overrides(tokens)
    assert all(word.furigana != "ついたち" for word in result)


def test_first_day_uses_calendar_reading_after_month() -> None:
    tokens = [
        _word("3", "さん"),
        _word("月", "がつ"),
        _word("1", "いち"),
        _word("日", "にち"),
    ]
    result = apply_furigana_overrides(tokens)
    assert [(word.surface, word.furigana) for word in result][-1] == (
        "1日",
        "ついたち",
    )


@pytest.mark.parametrize(
    ("digit", "reading"),
    [("1", "ひとり"), ("2", "ふたり")],
)
def test_person_counter_uses_irregular_reading(digit: str, reading: str) -> None:
    result = apply_furigana_overrides([_word(digit, digit), _word("人", "にん")])
    assert [(word.surface, word.furigana) for word in result] == [
        (digit + "人", reading)
    ]


def test_date_accent_entries_are_split_by_mora() -> None:
    result = apply_accent_overrides(
        [_accent_word("11", "じゅういち"), _accent_word("日", "にち")]
    )
    assert len(result) == 1
    assert result[0].furigana == "じゅういちにち"
    assert [accent.furigana for accent in result[0].accent] == [
        "じゅ",
        "う",
        "い",
        "ち",
        "に",
        "ち",
    ]


def test_person_counter_does_not_rewrite_lexical_compounds() -> None:
    tokens = [
        _word("第", "だい"),
        _word("一", "いち"),
        _word("人", "にん"),
        _word("者", "しゃ"),
        _word("二", "に"),
        _word("人", "にん"),
        _word("称", "しょう"),
    ]
    result = apply_furigana_overrides(tokens)
    assert [word.furigana for word in result] == [
        "だい",
        "いち",
        "にん",
        "しゃ",
        "に",
        "にん",
        "しょう",
    ]


def test_consecutive_person_counters_are_each_rewritten() -> None:
    result = apply_furigana_overrides(
        [_word("1", "1"), _word("人", "にん"), _word("2", "2"), _word("人", "にん")]
    )
    assert [(word.surface, word.furigana) for word in result] == [
        ("1人", "ひとり"),
        ("2人", "ふたり"),
    ]


def test_person_counter_allows_following_digit_compound() -> None:
    result = apply_furigana_overrides(
        [_word("2", "2"), _word("人", "にん"), _word("3", "3"), _word("脚", "きゃく")]
    )
    assert result[0].furigana == "ふたり"


def test_user_patch_priority_wins_over_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = FuriganaOverride(
        pattern=re.compile("1人"),
        replacements=(ReplacementToken(furigana="ひとり"),),
    )
    user = FuriganaOverride(
        pattern=re.compile("1人"),
        replacements=(ReplacementToken(furigana="ひとかた"),),
        priority=1,
    )
    monkeypatch.setattr(reading_overrides, "OVERRIDES", [builtin, user])
    result = apply_furigana_overrides([_word("1", "1"), _word("人", "にん")])
    assert result[0].furigana == "ひとかた"


def test_user_patch_priority_wins_when_nested_in_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = FuriganaOverride(
        pattern=re.compile("1人前"),
        replacements=(ReplacementToken(furigana="いちにんまえ"),),
    )
    user = FuriganaOverride(
        pattern=re.compile("人前"),
        replacements=(ReplacementToken(furigana="ひとまえ"),),
        priority=1,
    )
    monkeypatch.setattr(reading_overrides, "OVERRIDES", [builtin, user])
    result = apply_furigana_overrides(
        [_word("1", "1"), _word("人", "にん"), _word("前", "まえ")]
    )
    assert [(word.surface, word.furigana) for word in result] == [
        ("1", "1"),
        ("人前", "ひとまえ"),
    ]


def test_weekday_override_accepts_fullwidth_parentheses() -> None:
    result = apply_furigana_overrides(
        [_word("（", "（"), _word("土", "つち"), _word("）", "）")]
    )
    assert [(word.surface, word.furigana) for word in result] == [
        ("（", "（"),
        ("土", "ど"),
        ("）", "）"),
    ]
