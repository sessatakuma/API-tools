from __future__ import annotations

import asyncio  # noqa: F401  # noqa: ANYIO_OK
import threading
from typing import Any

import pytest

import api.accent.align as align_module
from api.accent.align import align_accent
from api.accent.models import WordAccentResult, WordResult


def _kana(furigana: str, surface: str) -> WordResult:
    return WordResult(furigana=furigana, surface=surface, base=surface, pos="名詞")


def _mora(text: str) -> dict[str, Any]:
    return {"text": text, "accent": 0}


def test_kana_alignment_does_not_search_impossible_long_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = align_module._match_cost

    def counted_match_cost(
        token: WordResult,
        span_texts: list[str],
        is_numeric: bool,
        is_punct: bool,
        is_readable_compound: bool = False,
        is_english_compound: bool = False,
    ) -> float:
        nonlocal calls
        calls += 1
        return original(
            token,
            span_texts,
            is_numeric,
            is_punct,
            is_readable_compound,
            is_english_compound,
        )

    monkeypatch.setattr(align_module, "_match_cost", counted_match_cost)
    tokens = [_kana("とうきょう", "東京") for _ in range(30)]
    accents = [_mora(mora) for _ in range(30) for mora in ("と", "う", "きょ", "う")]

    asyncio.run(align_accent(tokens, accents))

    assert calls < 20_000


def test_cpu_bound_alignment_is_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendezvous = threading.Barrier(2)
    guard = threading.Lock()
    active = 0
    peak_active = 0

    def fake_align(
        _tokens: list[WordResult],
        _accents: list[dict[str, Any]],
        _expected: list[int | None] | None = None,
    ) -> list[WordAccentResult]:
        nonlocal active, peak_active
        with guard:
            active += 1
            peak_active = max(peak_active, active)
        try:
            rendezvous.wait(timeout=0.1)
        except threading.BrokenBarrierError:
            assert rendezvous.broken
        with guard:
            active -= 1
        return []

    monkeypatch.setattr(align_module, "_align_accent", fake_align)

    async def run_two() -> None:
        await asyncio.gather(
            align_accent([], []),
            align_accent([], []),
        )

    asyncio.run(run_two())

    assert peak_active == 1
