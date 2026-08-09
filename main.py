"""
An API interface that provide the following functionalities
(1) Accent Marker   (/api/MarkAccent/  +  /api/MarkAccent/stream/)
(2) Usage Query     (/api/UsageQuery/)
(3) Dictionary Query (/api/DictQuery/)
(4) Sentence Query  (/api/SentenceQuery/)
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI

from api import dict_query, sentence_query, usage_query
from api.accent import accent_router
from api.accent.openjtalk import warmup as warmup_openjtalk
from api.accent.tokenizer import warmup as warmup_tokenizer
from api.request_body_limit import MAX_REQUEST_BODY_BYTES, RequestBodyLimitMiddleware

logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan context manager for FastAPI application.

    This function is used to manage the lifespan of the FastAPI application.
    It can be used to set up resources before the application starts and
    clean up resources after the application stops.

    Args:
        app (FastAPI): The FastAPI application instance.
    """
    # Set up resources before the application starts
    app.state.http_client = httpx.AsyncClient(timeout=10.0)

    # Warm the accent engines (fugashi/UniDic tagger + OpenJTalk frontend) so
    # the first /MarkAccent/ request doesn't pay the one-off dictionary-load
    # latency. Both are blocking C-extension loads, so run them off the event
    # loop and in parallel; startup blocks until they finish (readiness gate).
    logger.info("Warming up accent engines (UniDic tagger + OpenJTalk)...")
    await asyncio.gather(
        asyncio.to_thread(warmup_tokenizer),
        asyncio.to_thread(warmup_openjtalk),
    )
    logger.info("Accent engines ready.")

    yield
    # Clean up resources after the application stops
    await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=MAX_REQUEST_BODY_BYTES,
)

# Include routers from different modules
app.include_router(accent_router, prefix="/api")
app.include_router(usage_query.router, prefix="/api")
app.include_router(dict_query.router, prefix="/api")
app.include_router(sentence_query.router, prefix="/api")
logging.basicConfig(
    level=logging.INFO,
    format="{asctime} [{levelname:^8s}] {message} ({name}.{module}:{lineno})",
    datefmt="%Y-%m-%d %H:%M:%S",
    style="{",
)
