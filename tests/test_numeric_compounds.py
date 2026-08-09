from __future__ import annotations

import asyncio  # noqa: F401  # noqa: ANYIO_OK

import pytest

from api.accent import pipeline
from api.accent.models import WordAccentResult, WordResult
from api.accent.pipeline import process_accent_chunk
from api.accent.preprocess import (
    normalize_and_strip_x_between_digits,
    restore_x_between_digits,
)


def _word(surface: str) -> WordAccentResult:
    return WordAccentResult(surface=surface, furigana=surface, accent=[], subword=[])


def test_readable_numeric_compounds_keep_independent_morae(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipeline,
        "tag_local",
        lambda _text: [
            WordResult(surface="2%", furigana="2%"),
            WordResult(surface="・", furigana="・"),
            WordResult(surface="100℃", furigana="100℃"),
        ],
    )

    async def fake_counts(texts: list[str]) -> list[int]:
        assert texts == ["2%", "100℃"]
        return [6, 5]

    async def fake_result(_text: str) -> tuple[str, list[dict[str, int | str]]]:
        morae = ["に", "ぱ", "ー", "せ", "ん", "と", "ひゃ", "く", "ど", "し", "ー"]
        return "".join(morae), [{"text": mora, "accent": 0} for mora in morae]

    monkeypatch.setattr(pipeline, "get_openjtalk_mora_counts", fake_counts)
    monkeypatch.setattr(pipeline, "get_openjtalk_result", fake_result)
    response = asyncio.run(process_accent_chunk("2%・100℃"))

    assert response.result is not None
    compounds = {
        word.surface: word.furigana
        for word in response.result
        if word.surface in {"2%", "100℃"}
    }
    assert compounds == {
        "2%": "にぱーせんと",
        "100℃": "ひゃくどしー",
    }


def test_pipeline_preserves_spaced_multiplication_surface() -> None:
    cleaned, rewrites = normalize_and_strip_x_between_digits("1 × 2")
    assert cleaned == "1と2"

    restored = restore_x_between_digits(
        [_word("1"), _word("と"), _word("2")],
        rewrites,
    )

    assert "".join(word.surface for word in restored) == "1 × 2"


def test_multiplication_offset_survives_length_changing_normalization() -> None:
    cleaned, rewrites = normalize_and_strip_x_between_digits("ｶﾞ1×2")
    assert cleaned == "ガ1と2"

    restored = restore_x_between_digits(
        [_word("ガ1"), _word("と"), _word("2")],
        rewrites,
    )

    assert "".join(word.surface for word in restored) == "ガ1×2"
