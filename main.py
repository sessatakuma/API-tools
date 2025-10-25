"""
An API interface that provide two functionalities
(1) Accent Marker   (/api/MarkAccent/)
(2) Furigana Marker (/api/MarkFurigana/)
(3) Usage Query    (/api/UsageQuery/)
(4) Dictionary Query (/api/DictQuery/)
(5) Sentence Query  (/api/SentenceQuery/)
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import accent_marker, dict_query, furigana_marker, sentence_query, usage_query


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers from different modules
app.include_router(accent_marker.router, prefix="/api")
app.include_router(furigana_marker.router, prefix="/api")
app.include_router(usage_query.router, prefix="/api")
app.include_router(dict_query.router, prefix="/api")
app.include_router(sentence_query.router, prefix="/api")
