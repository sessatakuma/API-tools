"""Unit tests for the DP aligner (`align.py`).

`align_accent` is pure Python — it needs neither fugashi nor pyopenjtalk, so
these run anywhere. We construct `WordResult` tokens and the per-mora accent
stream (`ojad_results`, a `list[{"text": <kana>, "accent": 0|1|2}]`) by hand
and drive the async aligner with `asyncio.run`.

Each test pins a behaviour the aligner's own comments flag as historically
fragile: the numeric anchor that used to over-consume, the fallback path that
advanced OJAD by exactly +1 and cascaded, and the punct / rendaku / english
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
    tokens: list[WordResult], ojad: list[dict[str, Any]]
) -> list[WordAccentResult]:
    return asyncio.run(align_accent(tokens, ojad))


def _pairs(word: WordAccentResult) -> list[tuple[str, int]]:
    return [(a.furigana, a.accent_marking_type) for a in word.accent]


def test_basic_kana_token_spans_its_morae() -> None:
    """A single kana token aligns to its whole mora span with per-mora marks."""
    ojad = [_mora("と", 0), _mora("う", 1), _mora("きょ", 1), _mora("う", 2)]
    (word,) = _run([_kana("とうきょう", "東京")], ojad)
    assert word.surface == "東京"
    assert word.furigana == "とうきょう"
    # token_morae == voiced_span, so ruby uses the tokeniser morae with the
    # OpenJTalk accent mark zipped on per mora.
    assert _pairs(word) == [("と", 0), ("う", 1), ("きょ", 1), ("う", 2)]


def test_rendaku_fold_matches_voiced_reading() -> None:
    """Tokeniser `ふんかん` aligns against voiced OJAD `ぷんかん` (fold, no cascade)."""
    ojad = [_mora("ぷ", 0), _mora("ん", 0), _mora("か", 1), _mora("ん", 2)]
    (word,) = _run([_kana("ふんかん", "分間")], ojad)
    # The voicing fold makes ぷ↔ふ alias, so the DP treats this as an
    # equal-length match and keeps the tokeniser's voiceless ruby (ふ, not ぷ)
    # while carrying the OpenJTalk accent marks — it does NOT cascade-fail.
    assert word.furigana == "ふんかん"
    assert _pairs(word) == [("ふ", 0), ("ん", 0), ("か", 1), ("ん", 2)]


def test_numeric_split_keeps_empty_on_punct() -> None:
    """`19×19` (→ `19/19`) splits 4+4; the empty phrase-break entry lands on `/`."""
    # Models what preprocessing produces after `strip_x_between_digits`:
    # two numeric tokens straddling a `/`, with an empty-text OJAD entry as
    # the phrase-break sentinel between the two spelled-out numbers.
    ojad = [
        _mora("じゅ", 0), _mora("う", 0), _mora("きゅ", 0), _mora("う", 0),
        _mora("", 0),  # phrase-break sentinel inserted by the \d/\d rewrite
        _mora("じゅ", 0), _mora("う", 0), _mora("きゅ", 0), _mora("う", 0),
    ]  # fmt: skip
    first, slash, second = _run([_numeric("19"), _punct("/"), _numeric("19")], ojad)
    # Each numeric free-consumes its own 4 morae — NOT sliced 1+7 — and the
    # empty entry's 0.01 tiebreaker keeps it out of the numeric spans.
    assert first.furigana == "じゅうきゅう"
    assert second.furigana == "じゅうきゅう"
    assert len(first.accent) == 4
    assert len(second.accent) == 4
    # The empty sentinel is absorbed by the punct token, which renders no ruby.
    assert slash.furigana == ""
    assert slash.accent == []


def test_punct_entry_not_leaked_onto_neighbour() -> None:
    """An OJAD `。` entry stays on the punct token, off the adjacent kana tokens."""
    ojad = [
        _mora("ね", 0), _mora("こ", 2),
        _mora("。", 0),
        _mora("い", 0), _mora("ぬ", 2),
    ]  # fmt: skip
    tokens = [_kana("ねこ", "猫"), _punct("。"), _kana("いぬ", "犬")]
    neko, maru, inu = _run(tokens, ojad)
    # `。` carries no spoken mora; the OJAD-punct guard keeps it from bleeding
    # onto 猫's or 犬's accent list.
    assert _pairs(neko) == [("ね", 0), ("こ", 2)]
    assert _pairs(inu) == [("い", 0), ("ぬ", 2)]
    assert maru.furigana == ""
    assert maru.accent == []


def test_english_elision_does_not_steal_kana_mora() -> None:
    """When OJAD elides an english token (k=0 free), the kana token keeps all morae."""
    # OJAD returns morae only for ふりがな — Whisper is elided entirely.
    ojad = [_mora("ふ", 0), _mora("り", 0), _mora("が", 1), _mora("な", 0)]
    whisper, furigana = _run([_english("Whisper"), _kana("ふりがな", "ふりがな")], ojad)
    # k=0 is free for english compounds, so the DP does NOT charge the fallback
    # penalty by stealing ふ from the neighbour — ふりがな keeps its 4 morae.
    assert _pairs(furigana) == [("ふ", 0), ("り", 0), ("が", 1), ("な", 0)]
    # The english token falls back to a single type-0 entry over its surface.
    assert whisper.furigana == "Whisper"
    assert _pairs(whisper) == [("Whisper", 0)]
