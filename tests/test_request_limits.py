from __future__ import annotations

import asyncio  # noqa: F401  # noqa: ANYIO_OK
import threading
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest
from starlette.types import Message, Scope

from api.accent import routes
from api.accent.models import Request
from main import app

OVERSIZED_BODY_BYTES = 1_048_577


async def _never_disconnect() -> Message:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


def _raw_request() -> StarletteRequest:
    return cast(StarletteRequest, SimpleNamespace(receive=_never_disconnect))


class ResponseStartError(RuntimeError):
    pass


async def _post_body(content: bytes | AsyncIterator[bytes]) -> int:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/MarkAccent/",
            content=content,
            headers={"content-type": "application/json"},
        )
    return response.status_code


def test_declared_oversized_body_is_rejected_before_validation() -> None:
    status = asyncio.run(_post_body(b"x" * OVERSIZED_BODY_BYTES))

    assert status == 413


def test_streamed_oversized_body_is_rejected_before_validation() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        chunk = b"x" * (OVERSIZED_BODY_BYTES // 2)
        yield chunk
        yield chunk
        yield b"x"

    status = asyncio.run(_post_body(chunks()))

    assert status == 413


def test_request_admission_rejects_overflow_without_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = 0
        ready = asyncio.Event()
        release = asyncio.Event()

        async def blocked_build_chunks(_text: str) -> list[tuple[int, int, str]]:
            nonlocal entered
            entered += 1
            if entered == 4:
                ready.set()
            await release.wait()
            return []

        monkeypatch.setattr(routes, "build_chunks", blocked_build_chunks)
        requests = [
            asyncio.create_task(routes.mark_accent(Request(text="猫"), _raw_request()))
            for _ in range(4)
        ]
        await asyncio.wait_for(ready.wait(), timeout=1)
        overflow = asyncio.create_task(
            routes.mark_accent(Request(text="犬"), _raw_request())
        )
        try:
            await asyncio.sleep(0)
            assert overflow.done()
            error = overflow.exception()
            assert isinstance(error, HTTPException)
            assert error.status_code == 503
            assert entered == 4
        finally:
            release.set()
            await asyncio.gather(*requests, overflow, return_exceptions=True)

    asyncio.run(scenario())


def test_streaming_response_start_failure_releases_request_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(routes, "_REQUEST_LIMITER", threading.BoundedSemaphore(1))
        response = await routes.mark_accent_stream(Request(text=""))
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
        }

        async def receive() -> Message:
            return {"type": "http.disconnect"}

        async def send(_message: Message) -> None:
            raise ResponseStartError

        with pytest.raises(ResponseStartError):
            await response(scope, receive, send)

        follow_up = await routes.mark_accent(Request(text=""), _raw_request())
        assert follow_up.status == 200

    asyncio.run(scenario())


def test_collected_request_timeout_releases_request_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        limiter = threading.BoundedSemaphore(1)
        monkeypatch.setattr(routes, "_REQUEST_LIMITER", limiter)
        monkeypatch.setattr(routes, "ACCENT_REQUEST_TIMEOUT_SECONDS", 0.01)

        async def never_finishes(_request: Request) -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(routes, "_mark_accent", never_finishes)

        with pytest.raises(HTTPException) as caught:
            await routes.mark_accent(Request(text="猫"), _raw_request())
        assert caught.value.status_code == 504
        assert limiter.acquire(blocking=False)
        limiter.release()

    asyncio.run(scenario())


def test_collected_request_cancellation_cancels_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        cancelled = asyncio.Event()

        async def blocked_processing(_request: Request) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def disconnect() -> Message:
            return {"type": "http.disconnect"}

        monkeypatch.setattr(routes, "_mark_accent", blocked_processing)
        raw_request = cast(StarletteRequest, SimpleNamespace(receive=disconnect))

        with pytest.raises(asyncio.CancelledError):
            await routes.mark_accent(Request(text="猫"), raw_request)
        assert cancelled.is_set()

    asyncio.run(scenario())
