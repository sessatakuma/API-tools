"""
An API interface that provide two functionalities
(1) Accent Marker   (/api/MarkAccent/)
(2) Furigana Marker (/api/MarkFurigana/)
(3) Usage Query    (/api/UsageQuery/)
(4) Dictionary Query (/api/DictQuery/)
(5) Sentence Query  (/api/SentenceQuery/)
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from api import accent_marker, dict_query, furigana_marker, sentence_query, usage_query
from config.settings import ALLOW_ORIGINS, ALLOWED_HOSTS


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

# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure Trusted Host middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=ALLOWED_HOSTS,
)

# TODO: adjust rate limit as needed for each api endpoint
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore
app.add_middleware(SlowAPIMiddleware)

# Include routers from different modules
app.include_router(accent_marker.router, prefix="/api")
app.include_router(furigana_marker.router, prefix="/api")
app.include_router(usage_query.router, prefix="/api")
app.include_router(dict_query.router, prefix="/api")
app.include_router(sentence_query.router, prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format="{asctime} [{levelname:^8s}] {message} ({name}.{module}:{lineno})",
    datefmt="%Y-%m-%d %H:%M:%S",
    style="{",
)
