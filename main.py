"""
An API interface that provide two functionalities
(1) Accent Marker   (/api/MarkAccent/)
(2) Furigana Marker (/api/MarkFurigana/)
(3) Usage Query    (/api/UsageQuery/)
(4) Dictionary Query (/api/DictQuery/)
(5) Sentence Query  (/api/SentenceQuery/)
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Awaitable, Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response

from api import accent_marker, dict_query, furigana_marker, sentence_query, usage_query
from api.auth import verify_jwt_token


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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_authentication_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """
    Middleware to authenticate API requests using JWT.

    All /api/ requests require a valid JWT token in the Authorization header.
    """
    # Skip authentication for non-API routes
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    # Verify JWT token
    try:
        client_id = await verify_jwt_token(request)
        # Store client_id in request state for later use
        request.state.client_id = client_id
        return await call_next(request)
    except Exception as e:
        return JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "message": str(e.detail) if hasattr(e, "detail") else str(e),
                "status": 401,
            },
        )


# Include routers from different modules
app.include_router(accent_marker.router, prefix="/api")
app.include_router(furigana_marker.router, prefix="/api")
app.include_router(usage_query.router, prefix="/api")
app.include_router(dict_query.router, prefix="/api")
app.include_router(sentence_query.router, prefix="/api")
