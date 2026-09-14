"""
An API interface that marks Japanese pitch accent and furigana on input text
(/api/MarkAccent/  +  /api/MarkAccent/stream/).
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

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


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=MAX_REQUEST_BODY_BYTES,
)

# The accent pipeline is the only router: it runs fully in-process, so the
# application holds no shared HTTP client (see #54 — the former DictQuery /
# SentenceQuery / UsageQuery scrapes and `api.dependencies` went with it).
app.include_router(accent_router, prefix="/api")
logging.basicConfig(
    level=logging.INFO,
    format="{asctime} [{levelname:^8s}] {message} ({name}.{module}:{lineno})",
    datefmt="%Y-%m-%d %H:%M:%S",
    style="{",
)
