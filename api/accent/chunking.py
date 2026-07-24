from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, AsyncGenerator, TypeVar

from api.accent.models import AccentResponse
from api.accent.preprocess import MAX_CHUNK_CHARS, cap_chunk_length, split_sentences
from api.accent.tokenizer import tag_local

MAX_CHUNKS_PER_REQUEST = 64
_process_chunk_loop: asyncio.AbstractEventLoop | None = None
_process_chunk_limiter: asyncio.Semaphore | None = None
T = TypeVar("T")


def _get_process_chunk_limiter() -> asyncio.Semaphore:
    global _process_chunk_limiter, _process_chunk_loop
    loop = asyncio.get_running_loop()
    if _process_chunk_loop is not loop or _process_chunk_limiter is None:
        _process_chunk_loop = loop
        _process_chunk_limiter = asyncio.Semaphore(4)
    return _process_chunk_limiter


def _consume_task_exception(task: asyncio.Task[T]) -> None:
    if not task.cancelled():
        task.exception()


async def _run_with_permit(factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    admitted = asyncio.Event()

    async def run_admitted() -> T:
        async with _get_process_chunk_limiter():
            admitted.set()
            return await factory()

    task = asyncio.create_task(run_admitted())
    task.add_done_callback(_consume_task_exception)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not admitted.is_set():
            task.cancel()
        raise


def _token_boundaries(text: str) -> set[int]:
    boundaries: set[int] = set()
    cursor = 0
    for token in tag_local(text):
        start = text.find(token.surface, cursor)
        if start < 0:
            continue
        end = start + len(token.surface)
        boundaries.update((start, end))
        cursor = end
    return boundaries


async def build_chunks(text: str) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    for line_idx, line in enumerate(text.split("\n")):
        if not line.strip():
            continue
        sub_idx = 0
        for sentence in split_sentences(line):
            boundaries = None
            if len(sentence) > MAX_CHUNK_CHARS:
                boundaries = await _run_with_permit(
                    lambda: asyncio.to_thread(_token_boundaries, sentence)
                )
            for piece in cap_chunk_length(sentence, boundaries):
                chunks.append((line_idx, sub_idx, piece))
                sub_idx += 1
                if len(chunks) > MAX_CHUNKS_PER_REQUEST:
                    return chunks
    return chunks


async def schedule_chunks(
    chunks: list[tuple[int, int, str]],
    render_english_furigana: bool,
    render_katakana_furigana: bool,
    script: str = "hiragana",
) -> AsyncGenerator[tuple[tuple[int, int, str], asyncio.Task[AccentResponse]], None]:
    async def run_chunk(line: str) -> AccentResponse:
        from api.accent.pipeline import process_accent_chunk

        return await _run_with_permit(
            lambda: process_accent_chunk(
                line,
                render_english_furigana=render_english_furigana,
                render_katakana_furigana=render_katakana_furigana,
                script=script,
            )
        )

    chunk_iterator = iter(chunks)
    pending: list[tuple[tuple[int, int, str], asyncio.Task[AccentResponse]]] = []
    for chunk in chunk_iterator:
        pending.append((chunk, asyncio.create_task(run_chunk(chunk[2]))))
        if len(pending) == 4:
            break

    try:
        while pending:
            yield pending[0]
            pending.pop(0)
            next_chunk = next(chunk_iterator, None)
            if next_chunk is not None:
                pending.append(
                    (next_chunk, asyncio.create_task(run_chunk(next_chunk[2])))
                )
    finally:
        await cancel_pending([task for _chunk, task in pending])


async def cancel_pending(tasks: list[asyncio.Task[AccentResponse]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
