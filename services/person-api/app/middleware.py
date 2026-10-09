import asyncio
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.core.logging import setup_logger

logger = setup_logger()
_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)

SKIP_PATHS = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico",
    "/api/v1/telemetry/pool",
    "/api/v1/telemetry/cache",
)


class ConcurrencyLimiterMiddleware:
    """Raw ASGI middleware — avoids BaseHTTPMiddleware cancel scope issues.

    Starlette 0.30+'s BaseHTTPMiddleware.call_next() creates a CollapsingCancelScope
    that can crash with 'exit cancel scope in different task' when concurrent requests
    timeout. This raw implementation avoids that entirely.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        if request.url.path in SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        try:
            await asyncio.wait_for(_semaphore.acquire(), timeout=settings.SEMAPHORE_TIMEOUT)
        except asyncio.TimeoutError:
            response = JSONResponse(
                status_code=503,
                content={"detail": "Too many concurrent requests. Please try again shortly."},
            )
            await response(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        finally:
            _semaphore.release()


def reset_semaphore() -> None:
    """Reset the semaphore to its initial state. For test isolation."""
    global _semaphore
    _semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)