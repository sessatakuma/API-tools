"""Tests for the in-process OpenJTalk accent engine (`openjtalk.py`).

These exercise the real pyopenjtalk frontend (a hard project dependency), so
they double as an integration check that the g2p mora stream and the
full-context accent stream stay the same length. The `・`-separated cases pin
the katakana-block-punctuation fix: those marks live in U+30A0–U+30FF but are
not spoken morae, so they must be filtered out of the kana stream to match
`extract_fullcontext`, which drops them.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from api.accent.openjtalk import (
    _accent_markings,
    _is_kana_mora,
    _kana_morae,
    _tag,
    get_openjtalk_result,
)

# Golden per-mora output of the OpenJTalk g2p + full-context frontend.
# NOTE: these are the *phonetic* g2p morae, which spell long vowels with the
# chōonpu `ー` (今日→きょー, 東京→とーきょー, 英語→えーご, 多い→おーい). The
# orthographic readings from the PR description (きょう / とうきょう / えいご /
# おおい) are produced downstream by `align.py`, which re-attaches the UniDic
# tokeniser reading to these accent marks — not by `get_openjtalk_result`,
# which is what these tests pin. Each value is (morae, accent markings); the
# two lists MUST be the same length.
GOLDEN: dict[str, tuple[list[str], list[int]]] = {
    "今日": (["きょ", "ー"], [2, 0]),  # atamadaka
    "東京": (["と", "ー", "きょ", "ー"], [0, 1, 1, 2]),  # odaka
    "英語": (["え", "ー", "ご"], [0, 1, 2]),  # odaka
    "多い": (["お", "ー", "い"], [2, 0, 0]),  # atamadaka
    "切手": (["き", "っ", "て"], [0, 1, 2]),  # odaka
    "ケーキ": (["け", "ー", "き"], [2, 0, 0]),  # loanword, ー kept
    "コーヒー": (["こ", "ー", "ひ", "ー"], [0, 1, 2, 0]),  # loanword, ー kept
}

# Inputs whose g2p kana stream and full-context accent stream must agree in
# length. Includes the two regressed families: adjacent single-mora phrases
# and `・`-separated compounds/names.
PARITY_CORPUS = [
    "今日",
    "東京",
    "英語",
    "多い",
    "切手",
    "ケーキ",
    "コーヒー",
    "私は本を読む",
    "母は日本語を話す",
    "はい、そうです",
    "え、あ、う",  # adjacent single-mora phrases (Bug 1)
    "ア・イ",  # middle-dot separator (Bug 2)
    "東京・大阪",  # middle-dot between multi-mora words (Bug 2)
    "テレビ・ラジオ",
    "バラク・オバマ",  # foreign name with ・ (Bug 2)
]


class TestIsKanaMora:
    def test_normal_kana_accepted(self) -> None:
        # `_is_kana_mora` runs on g2p's katakana output (before kata2hira),
        # so it only ever sees katakana.
        assert all(_is_kana_mora(m) for m in ("ア", "キ", "ャ", "ン", "ッ"))

    def test_chouonpu_accepted(self) -> None:
        # `ー` (U+30FC) is a spoken mora and must be kept.
        assert _is_kana_mora("ー")

    def test_katakana_block_punctuation_rejected(self) -> None:
        # `゠・ヽヾヿ` live in the katakana block but are not spoken morae.
        assert not any(_is_kana_mora(m) for m in ("・", "゠", "ヽ", "ヾ", "ヿ"))

    def test_ascii_punctuation_rejected(self) -> None:
        assert not any(_is_kana_mora(m) for m in ("。", "、", "?", "", "A"))


class TestKanaMorae:
    def test_middle_dot_dropped(self) -> None:
        assert "・" not in _kana_morae("東京・大阪")

    def test_reading_returned_in_hiragana(self) -> None:
        assert _kana_morae("今日") == ["きょ", "ー"]


@pytest.mark.parametrize("text", PARITY_CORPUS)
def test_mora_and_marking_lengths_match(text: str) -> None:
    """The kana stream and the accent stream must be the same length; any
    drift means per-mora accents shift downstream."""
    morae = _kana_morae(text)
    markings = _accent_markings(text)
    assert len(morae) == len(markings), (
        f"{text!r}: {len(morae)} morae {morae} vs {len(markings)} marks {markings}"
    )


class TestGetOpenjtalkResult:
    def _run(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        return asyncio.run(get_openjtalk_result(text, client=None))  # type: ignore[arg-type]

    def test_shape_and_marking_domain(self) -> None:
        paragraph, results = self._run("東京・大阪")
        # One entry per kana mora; the ・ must not appear.
        assert [r["text"] for r in results] == _kana_morae("東京・大阪")
        assert paragraph == "".join(_kana_morae("東京・大阪"))
        assert all(r["accent"] in (0, 1, 2) for r in results)

    def test_result_length_equals_mora_count(self) -> None:
        # Even on the collapse-prone input, every mora gets an entry.
        _, results = self._run("え、あ、う")
        assert len(results) == len(_kana_morae("え、あ、う"))

    def test_atamadaka_contour(self) -> None:
        # 今日 = きょー, atamadaka: FALL on mora 1, then LOW.
        _, results = self._run("今日")
        assert [r["accent"] for r in results] == [2, 0]

    def test_empty_input(self) -> None:
        paragraph, results = self._run("")
        assert paragraph == ""
        assert results == []

    @pytest.mark.parametrize("text", list(GOLDEN))
    def test_golden_cases(self, text: str) -> None:
        # Per-mora text sequence AND accent markings must match the golden,
        # and stay aligned (equal length) so no accent shifts off its mora.
        morae, markings = GOLDEN[text]
        paragraph, results = self._run(text)
        assert [r["text"] for r in results] == morae
        assert [r["accent"] for r in results] == markings
        assert paragraph == "".join(morae)

    @pytest.mark.parametrize("text", ["ケーキ", "コーヒー", "東京", "今日"])
    def test_long_vowel_chouonpu_kept(self, text: str) -> None:
        # The long-vowel mark `ー` is a spoken mora and must survive as its
        # own entry — dropping it would shorten the mora stream and shift the
        # accent marks.
        _, results = self._run(text)
        assert "ー" in [r["text"] for r in results]


class TestPunctuationDoesNotShiftAccents:
    """A mid-sentence `、` must not shift downstream per-mora accents by one.

    `g2p(kana=True)` echoes the `、` but `extract_fullcontext` drops it; if the
    kana stream isn't filtered to match, every accent mark after the comma
    slides one mora to the left. Assert the spoken morae + their accents are
    identical with and without the comma.
    """

    def test_comma_does_not_shift(self) -> None:
        _, without = asyncio.run(get_openjtalk_result("本を読む", client=None))  # type: ignore[arg-type]
        _, with_comma = asyncio.run(get_openjtalk_result("本を、読む", client=None))  # type: ignore[arg-type]
        pairs_without = [(r["text"], r["accent"]) for r in without]
        pairs_with = [(r["text"], r["accent"]) for r in with_comma]
        # The comma carries no spoken mora, so the two mora/accent streams
        # must be byte-identical.
        assert pairs_with == pairs_without
        # And every mora keeps its accent aligned (no off-by-one).
        assert len(pairs_with) == len(_kana_morae("本を、読む"))


class TestAsyncPathMatchesDirectCall:
    """The `to_thread` / lock refactor must not change output vs a direct
    synchronous call, and concurrent callers must not corrupt each other's
    shared OpenJTalk C state."""

    @pytest.mark.parametrize("text", list(GOLDEN) + ["え、あ、う", "東京・大阪"])
    def test_async_matches_sync(self, text: str) -> None:
        # Direct synchronous frontend call (no event loop, no to_thread).
        morae, markings = _tag(text)
        expected = [
            {"text": morae[i], "accent": markings[i]} for i in range(len(morae))
        ]
        # Async path (asyncio.to_thread + lock).
        paragraph, results = asyncio.run(get_openjtalk_result(text, client=None))  # type: ignore[arg-type]
        assert results == expected
        assert paragraph == "".join(morae)

    def test_concurrent_calls_do_not_corrupt(self) -> None:
        # Fire many overlapping calls across the event loop's thread pool; the
        # lock must keep each result identical to its serial baseline.
        corpus = list(GOLDEN) + ["え、あ、う", "私は本を読む", "東京・大阪"]
        texts = corpus * 6  # 60+ overlapping calls

        baseline = {
            t: asyncio.run(get_openjtalk_result(t, client=None))  # type: ignore[arg-type]
            for t in corpus
        }

        async def _gather() -> list[tuple[str, list[dict[str, Any]]]]:
            return await asyncio.gather(
                *(get_openjtalk_result(t, client=None) for t in texts)  # type: ignore[arg-type]
            )

        results = asyncio.run(_gather())
        for text, result in zip(texts, results):
            assert result == baseline[text], f"corruption on {text!r}"
