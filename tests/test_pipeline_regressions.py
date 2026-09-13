from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api.accent import pipeline, tokenizer
from api.accent.chunking import schedule_chunks
from api.accent.models import (
    MAX_TEXT_CHARS,
    AccentInfo,
    AccentResponse,
    Request,
    WordAccentResult,
    WordResult,
)
from api.accent.pipeline import process_accent_chunk
from api.accent.postprocess import apply_furigana_toggles


def _accent_word(surface: str, furigana: str) -> WordAccentResult:
    return WordAccentResult(
        surface=surface,
        furigana=furigana,
        accent=[AccentInfo(furigana="あ", accent_marking_type=0, length=1)],
        subword=[],
    )


def test_english_toggle_off_clears_generated_kana_reading() -> None:
    result = apply_furigana_toggles(
        [_accent_word("Wifi.7", "Wifi.なな")],
        render_english=False,
        render_katakana=True,
    )
    assert result[0].furigana == ""
    assert result[0].accent == []


def test_numeric_unit_keeps_japanese_reading_when_english_is_hidden() -> None:
    result = apply_furigana_toggles(
        [_accent_word("53mm", "ごじゅうさんみりめーとる")],
        render_english=False,
        render_katakana=True,
    )
    assert result[0].furigana == "ごじゅうさんみりめーとる"
    assert result[0].accent


def test_pure_english_fast_path_honours_disabled_toggle() -> None:
    response = asyncio.run(process_accent_chunk("Apple"))
    assert response.result is not None
    assert response.result[0].furigana == ""
    assert response.result[0].accent == []


def test_pure_punctuation_fast_path_does_not_emit_literal_ruby() -> None:
    response = asyncio.run(process_accent_chunk("？"))
    assert response.result is not None
    assert response.result[0].furigana == ""


def test_spoken_symbol_does_not_take_no_japanese_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipeline,
        "tag_local",
        lambda _text: [WordResult(surface="#", furigana="しゃーぷ", pos="記号")],
    )

    async def fake_openjtalk(_text: str) -> tuple[str, list[dict[str, int | str]]]:
        morae = ["しゃ", "ー", "ぷ"]
        return "しゃーぷ", [
            {"text": mora, "accent": marking} for mora, marking in zip(morae, [0, 1, 2])
        ]

    monkeypatch.setattr(pipeline, "get_openjtalk_result", fake_openjtalk)
    response = asyncio.run(process_accent_chunk("#"))
    assert response.result is not None
    assert response.result[0].furigana == "しゃーぷ"


def test_english_toggle_on_uses_spoken_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipeline,
        "tag_local",
        lambda _text: [WordResult(surface="Apple", furigana="Apple")],
    )

    async def fake_openjtalk(_text: str) -> tuple[str, list[dict[str, int | str]]]:
        morae = ["あ", "っ", "ぷ", "る"]
        return "あっぷる", [
            {"text": mora, "accent": marking}
            for mora, marking in zip(morae, [0, 1, 1, 2])
        ]

    monkeypatch.setattr(pipeline, "get_openjtalk_result", fake_openjtalk)
    response = asyncio.run(process_accent_chunk("Apple", render_english_furigana=True))
    assert response.result is not None
    assert response.result[0].furigana == "あっぷる"


def test_tokenizer_merges_numeric_readable_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def feature(kana: str) -> SimpleNamespace:
        return SimpleNamespace(
            kana=kana,
            pron=kana,
            lemma="*",
            pos1="名詞",
            pos2="普通名詞",
            cType="*",
            cForm="*",
            aType="*",
        )

    fake_tokens = [
        SimpleNamespace(surface="2", white_space="", feature=feature("ニ")),
        SimpleNamespace(surface="℃", white_space="", feature=feature("*")),
    ]
    monkeypatch.setattr(tokenizer, "_get_tagger", lambda: lambda _text: fake_tokens)
    result = tokenizer.tag_local("2℃")
    assert [(word.surface, word.furigana) for word in result] == [("2℃", "2℃")]


def test_scheduler_creates_only_bounded_initial_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        started = 0
        gate = asyncio.Event()
        ready = asyncio.Event()

        async def fake_process(_text: str, **_kwargs: bool | str) -> AccentResponse:
            nonlocal started
            started += 1
            if started == 4:
                ready.set()
            await gate.wait()
            return AccentResponse(status=200, result=[])

        monkeypatch.setattr(pipeline, "process_accent_chunk", fake_process)
        chunks = [(index, 0, str(index)) for index in range(20)]
        scheduled = schedule_chunks(chunks, False, False)
        _chunk, first_task = await anext(scheduled)
        try:
            await asyncio.wait_for(ready.wait(), timeout=1)
            assert started == 4
        finally:
            gate.set()
        await first_task
        async for _chunk, task in scheduled:
            await task

    asyncio.run(scenario())


def test_hidden_english_is_removed_from_openjtalk_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_query = ""
    monkeypatch.setattr(
        pipeline,
        "tag_local",
        lambda _text: [
            WordResult(surface="foo", furigana="foo"),
            WordResult(surface="123", furigana="123", pos="名詞"),
            WordResult(surface="猫", furigana="ねこ", base="猫", pos="名詞"),
        ],
    )

    async def fake_openjtalk(text: str) -> tuple[str, list[dict[str, int | str]]]:
        nonlocal captured_query
        captured_query = text
        morae = ["ひゃ", "く", "に", "じゅ", "う", "さ", "ん", "ね", "こ"]
        return "".join(morae), [{"text": mora, "accent": 0} for mora in morae]

    monkeypatch.setattr(pipeline, "get_openjtalk_result", fake_openjtalk)
    asyncio.run(process_accent_chunk("foo 123猫", render_english_furigana=False))
    assert not any("a" <= char <= "z" or "A" <= char <= "Z" for char in captured_query)


def test_request_rejects_text_above_character_limit() -> None:
    with pytest.raises(ValidationError):
        Request(text="あ" * (MAX_TEXT_CHARS + 1))


def test_scheduler_limit_is_shared_across_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        started = 0
        gate = asyncio.Event()
        ready = asyncio.Event()

        async def fake_process(_text: str, **_kwargs: bool | str) -> AccentResponse:
            nonlocal started
            started += 1
            if started == 4:
                ready.set()
            await gate.wait()
            return AccentResponse(status=200, result=[])

        monkeypatch.setattr(pipeline, "process_accent_chunk", fake_process)
        chunks = [(index, 0, str(index)) for index in range(4)]
        first = schedule_chunks(chunks, False, False)
        second = schedule_chunks(chunks, False, False)
        _first_chunk, first_task = await anext(first)
        _second_chunk, second_task = await anext(second)
        try:
            await asyncio.wait_for(ready.wait(), timeout=1)
            assert started == 4
        finally:
            gate.set()
        await first_task
        await second_task
        await first.aclose()
        await second.aclose()

    asyncio.run(scenario())


def test_numeric_unit_stays_in_query_when_english_is_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_query = ""
    monkeypatch.setattr(
        pipeline,
        "tag_local",
        lambda _text: [WordResult(surface="53mm", furigana="53mm")],
    )

    async def fake_counts(_texts: list[str]) -> list[int]:
        return [11]

    async def fake_openjtalk(text: str) -> tuple[str, list[dict[str, int | str]]]:
        nonlocal captured_query
        captured_query = text
        morae = ["ご", "じゅ", "ー", "さ", "ん", "み", "り", "め", "ー", "と", "る"]
        return "".join(morae), [{"text": mora, "accent": 0} for mora in morae]

    monkeypatch.setattr(pipeline, "get_openjtalk_mora_counts", fake_counts)
    monkeypatch.setattr(pipeline, "get_openjtalk_result", fake_openjtalk)
    response = asyncio.run(process_accent_chunk("53mm"))
    assert captured_query == "53mm"
    assert response.result is not None
    assert response.result[0].furigana.endswith("みりめーとる")


def test_cancelled_native_work_keeps_process_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        release = threading.Event()
        first_ready = asyncio.Event()
        second_ready = asyncio.Event()
        first_started = 0
        second_started = 0

        async def fake_process(text: str, **_kwargs: bool | str) -> AccentResponse:
            nonlocal first_started, second_started
            if text.startswith("first"):
                first_started += 1
                if first_started == 4:
                    first_ready.set()
            else:
                second_started += 1
                if second_started == 4:
                    second_ready.set()
            await asyncio.to_thread(release.wait)
            return AccentResponse(status=200, result=[])

        monkeypatch.setattr(pipeline, "process_accent_chunk", fake_process)
        first = schedule_chunks(
            [(index, 0, f"first-{index}") for index in range(4)], False, False
        )
        second = schedule_chunks(
            [(index, 0, f"second-{index}") for index in range(4)], False, False
        )
        await anext(first)
        try:
            await asyncio.wait_for(first_ready.wait(), timeout=1)
        except TimeoutError:
            release.set()
            await first.aclose()
            raise
        close_first = asyncio.create_task(first.aclose())
        await asyncio.wait_for(asyncio.shield(close_first), timeout=0.1)
        await anext(second)
        await asyncio.sleep(0)
        try:
            assert second_started == 0
        finally:
            release.set()
        await asyncio.wait_for(second_ready.wait(), timeout=1)
        await second.aclose()

    asyncio.run(scenario())
