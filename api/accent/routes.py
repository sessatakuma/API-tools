"""FastAPI router for MarkAccent.

Two endpoints share the same chunked pipeline (`pipeline.build_chunks` +
`pipeline.schedule_chunks`):

  - `POST /api/MarkAccent/` collects all per-chunk results into one
    `AccentResponse`.
  - `POST /api/MarkAccent/stream/` yields one NDJSON line per chunk as
    soon as it finishes.

The MarkFurigana endpoint that lived in this package previously was
removed in the local-UniDic migration — the standalone Yahoo Furigana
service has no in-process replacement, and callers that need raw
tokenisation can use `tokenizer.tag_local`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.accent.models import AccentResponse, ErrorInfo, Request, WordAccentResult
from api.accent.pipeline import build_chunks, schedule_chunks
from api.dependencies import get_http_client

logger = logging.getLogger("api")

tags_metadata = [
    {
        "name": "MarkAccent",
        "description": "Mark accent of given text",
    },
]

accent_router = APIRouter()


@accent_router.post("/MarkAccent/", tags=["MarkAccent"], response_model=AccentResponse)
async def mark_accent(
    request: Request,
    client: httpx.AsyncClient = Depends(get_http_client),
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
    logger.info(f"[API] Received Request Text: {request.text}")

    chunks = build_chunks(request.text)
    if not chunks:
        return AccentResponse(status=200, result=[], error=None)

    tasks = schedule_chunks(
        chunks,
        client,
        render_english_furigana=request.render_english_furigana,
        render_katakana_furigana=request.render_katakana_furigana,
    )

    merged: list[WordAccentResult] = []
    worst_status = 200
    first_error: ErrorInfo | None = None
    for (chunk_idx, sub_idx, _text), task in zip(chunks, tasks):
        try:
            resp = await task
        except Exception as exc:
            logger.exception(f"Chunk {chunk_idx}.{sub_idx} failed")
            detail = str(exc) or repr(exc) or type(exc).__name__
            if first_error is None:
                first_error = ErrorInfo(code=500, message=f"Error: {detail}")
            worst_status = max(worst_status, 500)
            continue
        if resp.result:
            merged.extend(resp.result)
        if resp.status > worst_status:
            worst_status = resp.status
        if resp.error is not None and first_error is None:
            first_error = resp.error

    return AccentResponse(
        status=worst_status,
        result=merged if merged else None,
        error=first_error,
    )


@accent_router.post("/MarkAccent/stream/", tags=["MarkAccent"])
async def mark_accent_stream(
    request: Request,
    client: httpx.AsyncClient = Depends(get_http_client),
) -> StreamingResponse:
    """Stream one NDJSON line per chunk in input order.

    Uses the same `build_chunks` + `schedule_chunks` pipeline as
    `/MarkAccent/`, so per-chunk results are byte-identical. The only
    difference is delivery: each chunk is yielded as soon as it
    finishes (in input order) rather than collected into one AccentResponse.

    Each emitted object carries `{"chunk": line_idx, "subchunk":
    sub_idx}`: `line_idx` is the original `\\n`-split index (blank
    lines preserve their position so a client knows position 2 was
    empty); `sub_idx` distinguishes sentences inside one line. A line
    with no terminator yields one subchunk with `sub_idx=0`.
    """
    logger.info(f"[API] Received streaming request: {request.text!r}")

    chunks = build_chunks(request.text)

    async def generate() -> AsyncIterator[bytes]:
        if not chunks:
            return
        tasks = schedule_chunks(
            chunks,
            client,
            render_english_furigana=request.render_english_furigana,
            render_katakana_furigana=request.render_katakana_furigana,
        )
        for (chunk_idx, sub_idx, _text), task in zip(chunks, tasks):
            try:
                resp = await task
                payload: dict[str, Any] = {
                    "chunk": chunk_idx,
                    "subchunk": sub_idx,
                    **resp.model_dump(),
                }
            except Exception as exc:
                logger.exception(f"Streaming chunk {chunk_idx}.{sub_idx} failed")
                detail = str(exc) or repr(exc) or type(exc).__name__
                payload = {
                    "chunk": chunk_idx,
                    "subchunk": sub_idx,
                    "status": 500,
                    "result": None,
                    "error": {"code": 500, "message": f"Error: {detail}"},
                }
            yield (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(generate(), media_type="application/x-ndjson")
