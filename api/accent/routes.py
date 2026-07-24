"""FastAPI router for MarkAccent.

Two endpoints share the same chunked pipeline (`pipeline.build_chunks` +
`pipeline.schedule_chunks`):

  - `POST /api/MarkAccent/` collects all per-chunk results into one
    `AccentResponse`.
  - `POST /api/MarkAccent/stream/` yields one NDJSON line per chunk in
    input order.

The MarkFurigana endpoint that lived in this package previously was
removed in the local-UniDic migration — the standalone Yahoo Furigana
service has no in-process replacement, and callers that need raw
tokenisation can use `tokenizer.tag_local`.
"""

from __future__ import annotations

import asyncio  # noqa: F401  # noqa: ANYIO_OK
import json
import logging
import threading
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from api.accent.chunking import MAX_CHUNKS_PER_REQUEST, build_chunks, schedule_chunks
from api.accent.models import AccentResponse, ErrorInfo, Request, WordAccentResult

logger = logging.getLogger("api")
MAX_ACTIVE_ACCENT_REQUESTS = 4
ACCENT_REQUEST_TIMEOUT_SECONDS = 30.0
_REQUEST_LIMITER = threading.BoundedSemaphore(MAX_ACTIVE_ACCENT_REQUESTS)

tags_metadata = [
    {
        "name": "MarkAccent",
        "description": "Mark accent of given text",
    },
]

accent_router = APIRouter()


def _acquire_request_slot() -> threading.BoundedSemaphore:
    limiter = _REQUEST_LIMITER
    if not limiter.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Accent service is busy")
    return limiter


class _AdmissionStreamingResponse(StreamingResponse):
    def __init__(
        self,
        content: AsyncIterator[bytes],
        request_limiter: threading.BoundedSemaphore,
    ) -> None:
        super().__init__(content, media_type="application/x-ndjson")
        self._request_limiter = request_limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._request_limiter.release()


@accent_router.post("/MarkAccent/", tags=["MarkAccent"], response_model=AccentResponse)
async def mark_accent(
    request: Request,
) -> AccentResponse:
    """Run the same chunked pipeline as `/MarkAccent/stream/`, but
    wait for every chunk to finish and return a single AccentResponse whose
    `result` is the concatenation of all chunk results in input order.

    Per-chunk processing is identical to the streaming endpoint, so
    the two emit byte-identical word entries for the same input. If any
    chunk fails, the failure propagates: `status` takes the worst
    chunk's HTTP code and `error` takes the first chunk's error;
    successful chunks' words still appear in `result`.
    """
    request_limiter = _acquire_request_slot()
    try:
        try:
            async with asyncio.timeout(ACCENT_REQUEST_TIMEOUT_SECONDS):
                return await _mark_accent(request)
        except TimeoutError as error:
            raise HTTPException(
                status_code=504,
                detail="Accent processing timed out",
            ) from error
    finally:
        request_limiter.release()


async def _mark_accent(request: Request) -> AccentResponse:
    logger.info("[API] Received accent request chars=%d", len(request.text))

    chunks = await build_chunks(request.text)
    if len(chunks) > MAX_CHUNKS_PER_REQUEST:
        raise HTTPException(status_code=413, detail="Too many text chunks")
    if not chunks:
        return AccentResponse(status=200, result=[], error=None)

    scheduled = schedule_chunks(
        chunks,
        render_english_furigana=request.render_english_furigana,
        render_katakana_furigana=request.render_katakana_furigana,
        script=request.script,
    )

    merged: list[WordAccentResult] = []
    worst_status = 200
    first_error: ErrorInfo | None = None
    # Closing the scheduler cancels queued coroutine tasks. Native work that
    # already entered a worker thread cannot be stopped safely, so cancellation
    # drains it while retaining its process permit before cleanup completes.
    try:
        async for (chunk_idx, sub_idx, _text), task in scheduled:
            try:
                resp = await task
            except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
                logger.exception("Chunk %d.%d failed", chunk_idx, sub_idx)
                if first_error is None:
                    first_error = ErrorInfo(
                        code=500,
                        message="Accent processing failed",
                    )
                worst_status = max(worst_status, 500)
                continue
            if resp.result:
                merged.extend(resp.result)
            if resp.status > worst_status:
                worst_status = resp.status
            if resp.error is not None and first_error is None:
                first_error = resp.error
    finally:
        await scheduled.aclose()

    return AccentResponse(
        status=worst_status,
        result=merged if merged else None,
        error=first_error,
    )


@accent_router.post("/MarkAccent/stream/", tags=["MarkAccent"])
async def mark_accent_stream(
    request: Request,
) -> StreamingResponse:
    """Stream one NDJSON line per chunk in input order.

    Uses the same `build_chunks` + `schedule_chunks` pipeline as
    `/MarkAccent/`, so per-chunk results are byte-identical. The only
        difference is delivery: each chunk is yielded in input order rather
        than collected into one AccentResponse.

    Each emitted object carries `{"chunk": line_idx, "subchunk":
    sub_idx}`: `line_idx` is the original `\\n`-split index (blank
    lines preserve their position so a client knows position 2 was
    empty); `sub_idx` distinguishes sentences inside one line. A line
    with no terminator yields one subchunk with `sub_idx=0`.
    """
    request_limiter = _acquire_request_slot()
    deadline = asyncio.get_running_loop().time() + ACCENT_REQUEST_TIMEOUT_SECONDS
    try:
        try:
            async with asyncio.timeout_at(deadline):
                logger.info(
                    "[API] Received streaming request chars=%d",
                    len(request.text),
                )
                chunks = await build_chunks(request.text)
                if len(chunks) > MAX_CHUNKS_PER_REQUEST:
                    raise HTTPException(status_code=413, detail="Too many text chunks")
        except TimeoutError as error:
            raise HTTPException(
                status_code=504,
                detail="Accent processing timed out",
            ) from error
    except BaseException:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        request_limiter.release()
        raise

    async def generate() -> AsyncIterator[bytes]:
        if not chunks:
            return
        scheduled = schedule_chunks(
            chunks,
            render_english_furigana=request.render_english_furigana,
            render_katakana_furigana=request.render_katakana_furigana,
            script=request.script,
        )
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    async for (chunk_idx, sub_idx, _text), task in scheduled:
                        try:
                            resp = await task
                            payload: dict[str, Any] = {
                                "chunk": chunk_idx,
                                "subchunk": sub_idx,
                                **resp.model_dump(),
                            }
                        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
                            logger.exception(
                                "Streaming chunk %d.%d failed",
                                chunk_idx,
                                sub_idx,
                            )
                            payload = {
                                "chunk": chunk_idx,
                                "subchunk": sub_idx,
                                "status": 500,
                                "result": None,
                                "error": {
                                    "code": 500,
                                    "message": "Accent processing failed",
                                },
                            }
                        yield (json.dumps(payload, ensure_ascii=False) + "\n").encode(
                            "utf-8"
                        )
            except TimeoutError:
                payload = {
                    "chunk": -1,
                    "subchunk": -1,
                    "status": 504,
                    "result": None,
                    "error": {
                        "code": 504,
                        "message": "Accent processing timed out",
                    },
                }
                yield (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        finally:
            await scheduled.aclose()

    return _AdmissionStreamingResponse(generate(), request_limiter)
