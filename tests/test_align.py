"""Unit tests for the DP aligner (`align.py`).

`align_accent` is pure Python — it needs neither fugashi nor pyopenjtalk, so
these run anywhere. We construct `WordResult` tokens and the per-mora accent
stream (`accent_results`, a `list[{"text": <kana>, "accent": 0|1|2}]`) by hand
and drive the async aligner with `asyncio.run`.

Each test pins a behaviour the aligner's own comments flag as historically
fragile: the numeric anchor that used to over-consume, the fallback path that
advanced OpenJTalk by exactly +1 and cascaded, and the punct / rendaku / english
edge cases that leaked morae onto neighbouring tokens.

IMPORTANT: a kana/kanji token only reaches the rendaku edit-distance branch of
`_match_cost` when it has MA backing (`base`/`pos` set) — a token with both
`base is None` and `pos is None` is treated as an override-synthesized token
and gets the numeric-style free-consume path instead. `_kana` sets `pos` so
these tests exercise the real kana branch, matching what `tokenizer.tag_local`
emits for dictionary words.
"""

from __future__ import annotations

import asyncio
from typing import Any

from api.accent.align import align_accent
from api.accent.models import WordAccentResult, WordResult


def _kana(furigana: str, surface: str) -> WordResult:
    """A dictionary-backed kana/kanji token (routes through the kana branch)."""
    return WordResult(furigana=furigana, surface=surface, base=surface, pos="名詞")


def _numeric(digits: str) -> WordResult:
    """A standalone numeric token (routes through the numeric branch)."""
    return WordResult(furigana=digits, surface=digits, pos="名詞")


def _punct(mark: str) -> WordResult:
    """A pure-punctuation token (furigana == the mark)."""
    return WordResult(furigana=mark, surface=mark)


def _english(surface: str) -> WordResult:
    """A merged english-compound token (`G2P`, `Whisper`, …)."""
    return WordResult(furigana=surface, surface=surface)


def _mora(text: str, accent: int) -> dict[str, Any]:
    return {"text": text, "accent": accent}


def _run(
    tokens: list[WordResult], accent: list[dict[str, Any]]
) -> list[WordAccentResult]:
    return asyncio.run(align_accent(tokens, accent))


def _pairs(word: WordAccentResult) -> list[tuple[str, int]]:
    return [(a.furigana, a.accent_marking_type) for a in word.accent]


def test_basic_kana_token_spans_its_morae() -> None:
    """A single kana token aligns to its whole mora span with per-mora marks."""
    accent = [_mora("と", 0), _mora("う", 1), _mora("きょ", 1), _mora("う", 2)]
    (word,) = _run([_kana("とうきょう", "東京")], accent)
    assert word.surface == "東京"
    assert word.furigana == "とうきょう"
    # token_morae == voiced_span, so ruby uses the tokeniser morae with the
    # OpenJTalk accent mark zipped on per mora.
    assert _pairs(word) == [("と", 0), ("う", 1), ("きょ", 1), ("う", 2)]


def test_rendaku_fold_matches_voiced_reading() -> None:
    """`ふんかん` aligns to voiced OpenJTalk `ぷんかん` via the fold, no cascade."""
    accent = [_mora("ぷ", 0), _mora("ん", 0), _mora("か", 1), _mora("ん", 2)]
    (word,) = _run([_kana("ふんかん", "分間")], accent)
    # The voicing fold makes ぷ↔ふ alias, so the DP treats this as an
    # equal-length match and keeps the tokeniser's voiceless ruby (ふ, not ぷ)
    # while carrying the OpenJTalk accent marks — it does NOT cascade-fail.
    assert word.furigana == "ふんかん"
    assert _pairs(word) == [("ふ", 0), ("ん", 0), ("か", 1), ("ん", 2)]


def test_numeric_split_keeps_spoken_connector_separate() -> None:
    accent = [
        _mora("じゅ", 0), _mora("う", 0), _mora("きゅ", 0), _mora("う", 0),
        _mora("と", 0),
        _mora("じゅ", 0), _mora("う", 0), _mora("きゅ", 0), _mora("う", 0),
    ]  # fmt: skip
    first, connector, second = _run(
        [_numeric("19"), _kana("と", "と"), _numeric("19")], accent
    )
    assert first.furigana == "じゅうきゅう"
    assert second.furigana == "じゅうきゅう"
    assert len(first.accent) == 4
    assert len(second.accent) == 4
    assert _pairs(connector) == [("と", 0)]


def test_numeric_split_balances_real_stream_without_sentinel() -> None:
    accent = [
        _mora("じゅ", 0),
        _mora("う", 0),
        _mora("きゅ", 0),
        _mora("う", 0),
        _mora("じゅ", 0),
        _mora("う", 0),
        _mora("きゅ", 0),
        _mora("う", 0),
        _mora("で", 0),
        _mora("す", 0),
    ]
    first, slash, second, desu = _run(
        [_numeric("19"), _punct("/"), _numeric("19"), _kana("です", "です")],
        accent,
    )
    assert len(first.accent) == 4
    assert slash.accent == []
    assert len(second.accent) == 4
    assert _pairs(desu) == [("で", 0), ("す", 0)]


def test_long_number_consumes_every_mora_before_counter() -> None:
    accent = [_mora("あ", 0) for _ in range(35)] + [
        _mora("え", 0),
        _mora("ん", 0),
    ]
    number, yen = _run(
        [_numeric("123456789012"), _kana("えん", "円")],
        accent,
    )
    assert len(number.accent) == 35
    assert _pairs(yen) == [("え", 0), ("ん", 0)]


def test_english_display_uses_aligned_spoken_reading() -> None:
    accent = [
        _mora("あ", 0),
        _mora("っ", 1),
        _mora("ぷ", 1),
        _mora("る", 2),
    ]
    (apple,) = _run([_english("Apple")], accent)
    assert apple.furigana == "あっぷる"
    assert "".join(a.furigana for a in apple.accent) == apple.furigana


def test_heterogeneous_numbers_use_exact_mora_targets() -> None:
    accent = [
        _mora("い", 0),
        _mora("ち", 2),
        _mora("せ", 0),
        _mora("ん", 2),
    ]
    one, comma, thousand = asyncio.run(
        align_accent(
            [_numeric("1"), _punct("、"), _numeric("1000")],
            accent,
            [2, None, 2],
        )
    )
    assert one.furigana == "いち"
    assert comma.accent == []
    assert thousand.furigana == "せん"


def test_english_and_numeric_tokens_use_independent_mora_targets() -> None:
    accent = [
        _mora("ふ", 0),
        _mora("ー", 0),
        _mora("ひゃ", 0),
        _mora("く", 2),
        _mora("ね", 0),
        _mora("こ", 2),
    ]
    english, number, cat = asyncio.run(
        align_accent(
            [_english("foo"), _numeric("100"), _kana("ねこ", "猫")],
            accent,
            [2, 2, None],
        )
    )
    assert english.furigana == "ふー"
    assert number.furigana == "ひゃく"
    assert cat.furigana == "ねこ"


def test_punct_entry_not_leaked_onto_neighbour() -> None:
    """An OpenJTalk `。` entry stays on the punct token, off adjacent kana."""
    accent = [
        _mora("ね", 0), _mora("こ", 2),
        _mora("。", 0),
        _mora("い", 0), _mora("ぬ", 2),
    ]  # fmt: skip
    tokens = [_kana("ねこ", "猫"), _punct("。"), _kana("いぬ", "犬")]
    neko, maru, inu = _run(tokens, accent)
    # `。` carries no spoken mora; the OpenJTalk-punct guard keeps it from bleeding
    # onto 猫's or 犬's accent list.
    assert _pairs(neko) == [("ね", 0), ("こ", 2)]
    assert _pairs(inu) == [("い", 0), ("ぬ", 2)]
    assert maru.furigana == ""
    assert maru.accent == []


def test_english_elision_does_not_steal_kana_mora() -> None:
    """When OpenJTalk elides an english token (k=0 free), kana keeps all morae."""
    # OpenJTalk returns morae only for ふりがな — Whisper is elided entirely.
    accent = [_mora("ふ", 0), _mora("り", 0), _mora("が", 1), _mora("な", 0)]
    whisper, furigana = _run(
        [_english("Whisper"), _kana("ふりがな", "ふりがな")], accent
    )
    # k=0 is free for english compounds, so the DP does NOT charge the fallback
    # penalty by stealing ふ from the neighbour — ふりがな keeps its 4 morae.
    assert _pairs(furigana) == [("ふ", 0), ("り", 0), ("が", 1), ("な", 0)]
    # The english token falls back to a single type-0 entry over its surface.
    assert whisper.furigana == "Whisper"
    assert _pairs(whisper) == [("Whisper", 0)]
