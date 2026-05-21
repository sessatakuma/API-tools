"""
An API interface that provide the following functionalities
(1) Accent Marker   (/api/MarkAccent/  +  /api/MarkAccent/stream/)
(2) Usage Query     (/api/UsageQuery/)
(3) Dictionary Query (/api/DictQuery/)
(4) Sentence Query  (/api/SentenceQuery/)
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI

from api import dict_query, sentence_query, usage_query
from api.accent import accent_router


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
    yield
    # Clean up resources after the application stops
    await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan)

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
