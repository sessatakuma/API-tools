"""FastAPI routers for MarkAccent and MarkFurigana.

Each endpoint is a thin wrapper around its data layer:
  - /MarkFurigana/        → `furigana.fetch_furigana`
  - /MarkAccent/          → `pipeline.process_accent_chunk`
  - /MarkAccent/stream/   → `pipeline.stream_accent_chunks` (NDJSON)

Two separate routers (rather than one shared one) keep the OpenAPI
tagging clean and let main.py register each with its own
dependencies if needed later.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.accent.furigana import fetch_furigana
from api.accent.models import AccentResponse, FuriganaResponse, Request
from api.accent.pipeline import process_accent_chunk, stream_accent_chunks
from api.dependencies import get_http_client

logger = logging.getLogger("api")

tags_metadata = [
    {
        "name": "MarkAccent",
        "description": "Mark accent of given text",
    },
    {
        "name": "MarkFurigana",
        "description": "Mark furigana of given text",
    },
]

accent_router = APIRouter()
furigana_router = APIRouter()


@furigana_router.post(
    "/MarkFurigana/", tags=["MarkFurigana"], response_model=FuriganaResponse
)
async def mark_furigana(
    request: Request,
    client: httpx.AsyncClient = Depends(get_http_client),
) -> FuriganaResponse:
    """Receive POST request, return a FuriganaResponse object."""
    return await fetch_furigana(request.text, client)


@accent_router.post("/MarkAccent/", tags=["MarkAccent"], response_model=AccentResponse)
async def mark_accent(
    request: Request,
    client: httpx.AsyncClient = Depends(get_http_client),
) -> AccentResponse:
    """Receive POST request, return an AccentResponse object."""
    logger.info(f"[API] Received Request Text: {request.text}")
    return await process_accent_chunk(request.text, client)


@accent_router.post("/MarkAccent/stream/", tags=["MarkAccent"])
async def mark_accent_stream(
    request: Request,
    client: httpx.AsyncClient = Depends(get_http_client),
) -> StreamingResponse:
    """Split the input on `\\n` (line) and then on full-width sentence
    terminators (sub-chunk), process each piece in parallel under a bounded
    semaphore, and stream one NDJSON line per piece in input order.
    """
    logger.info(f"[API] Received streaming request: {request.text!r}")
    return StreamingResponse(
        stream_accent_chunks(request, client),
        media_type="application/x-ndjson",
    )
