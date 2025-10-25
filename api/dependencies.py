import httpx
from fastapi import Request


async def get_http_client(request: Request) -> httpx.AsyncClient:
    """
    Dependency to provide an HTTP client for making asynchronous requests.

    This function retrieves the HTTP client instance stored in the FastAPI
    application's state. It can be used as a dependency in route handlers
    to perform HTTP requests.

    Args:
        request (Request): The FastAPI request object.

    Returns:
        httpx.AsyncClient: The HTTP client instance.
    """
    return request.app.state.http_client
