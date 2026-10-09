import time
import uuid
from datetime import datetime, timezone

import logging

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("person-api.requests")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)


class RequestIdMiddleware:
    """Raw ASGI middleware: set X-Request-ID on the scope + echo it on every response.

    Honors an inbound X-Request-ID header; otherwise generates a UUID4.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound: str | None = None
        for key, value in scope.get("headers") or []:
            if key.lower() == b"x-request-id" and value:
                inbound = value.decode("latin-1").strip() or None
                break

        request_id = inbound or str(uuid.uuid4())
        scope["request_id"] = request_id
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class StructuredRequestLoggingMiddleware:
    """Raw ASGI middleware: one structured JSON log line per request.

    Line shape (exactly these fields):
        {"timestamp", "request_id", "trace_id", "span_id", "method", "path", "status", "duration_ms"}

    trace_id/span_id are captured from the active OTel span while the
    instrumented app is running (at response-start time); null when absent.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        captured: dict = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                span = trace.get_current_span()
                ctx = span.get_span_context()
                if ctx.is_valid and ctx.trace_id:
                    captured["trace_id"] = f"{ctx.trace_id:032x}"
                    captured["span_id"] = f"{ctx.span_id:016x}"
                else:
                    captured["trace_id"] = None
                    captured["span_id"] = None
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            state = scope.get("state") or {}
            line = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": state.get("request_id") or scope.get("request_id"),
                "trace_id": captured.get("trace_id"),
                "span_id": captured.get("span_id"),
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status": captured.get("status"),
                "duration_ms": round((time.perf_counter() - start) * 1000.0, 3),
            }
            logger.info(_dumps(line))


def _dumps(line: dict) -> str:
    import json

    return json.dumps(line)
