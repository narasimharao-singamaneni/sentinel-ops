"""Fire-and-forget Sentinel error reporter (D3 / D4).

Reports requests that complete with a 5xx status, or in which an unhandled
exception occurs, to ``$SENTINEL_URL/api/v1/analyze``. 4xx is never reported.

Guarantees:
- The original response is sent before the reporter awaits Sentinel. For an
  explicit 5xx response the report task is spawned only after the final
  response body message has been sent; for unhandled exceptions a yield is
  inserted before spawning so the error response can go out first.
- The report call uses a 30-second timeout.
- Failures, timeouts, and transport errors are logged and swallowed.
- No retries in v1.
- ``SENTINEL_URL`` unset -> complete no-op with zero outbound connections.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger("person-api.error-reporter")

SERVICE_NAME = "person-api"
REPORT_TIMEOUT = 30.0
MAX_DETAIL_BYTES = 2048

_pending: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def get_pending_report_tasks() -> set[asyncio.Task]:
    return set(_pending)


async def _report(sentinel_url: str, event: dict) -> None:
    url = sentinel_url.rstrip("/") + "/api/v1/analyze"
    try:
        async with httpx.AsyncClient(timeout=REPORT_TIMEOUT) as client:
            resp = await client.post(url, json=event)
        logger.info(
            json.dumps(
                {
                    "event": "sentinel_report",
                    "trace_id": event.get("trace_id"),
                    "status_code": event.get("status_code"),
                    "analyze_status": resp.status_code,
                }
            )
        )
    except Exception as exc:
        # Logged and swallowed. No retries in v1.
        logger.warning(
            json.dumps(
                {
                    "event": "sentinel_report_failed",
                    "trace_id": event.get("trace_id"),
                    "status_code": event.get("status_code"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        )


def _build_event(
    scope: Scope,
    request_id: str | None,
    status: int,
    detail_bytes: bytes,
    exception: BaseException | None,
    trace_id: str | None,
    span_id: str | None,
) -> dict:
    if exception is not None:
        detail = f"{type(exception).__name__}: {exception}"
    elif detail_bytes:
        detail = detail_bytes.decode("utf-8", errors="replace")
    else:
        detail = f"HTTP {status}"
    return {
        "service": SERVICE_NAME,
        "method": scope.get("method"),
        "path": scope.get("path"),
        "status_code": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "error_detail": detail,
    }


class SentinelErrorReporterMiddleware:
    """Raw ASGI middleware: report 5xx / unhandled-exception requests to Sentinel.

    Sits inside RequestIdMiddleware (reads scope state for the request id)
    and outside the concurrency limiter (sees rejected 503 responses).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.get("state") or {}
        request_id: str | None = state.get("request_id")

        # The FastAPI instrumentor starts the server span as the outermost
        # layer, so every request has a real OTel trace identity by the time
        # this middleware runs — including requests the concurrency limiter
        # rejects with 503 before they reach any route.
        span_ctx = trace.get_current_span().get_span_context()
        trace_id = f"{span_ctx.trace_id:032x}" if span_ctx.is_valid else None
        span_id = f"{span_ctx.span_id:016x}" if span_ctx.is_valid else None

        info: dict = {"status": None, "detail": b"", "exception": None}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                info["status"] = message["status"]
            elif (
                message["type"] == "http.response.body"
                and info["status"] is not None
                and info["status"] >= 500
            ):
                info["detail"] = (info["detail"] + (message.get("body") or b""))[:MAX_DETAIL_BYTES]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            info["exception"] = exc
            if info["status"] is None:
                info["status"] = 500  # ServerErrorMiddleware surfaces a 500
            raise
        finally:
            status = info["status"]
            if status is not None and status >= 500:
                sentinel_url = settings.SENTINEL_URL
                if sentinel_url:
                    event = _build_event(
                        scope,
                        request_id,
                        status,
                        info["detail"],
                        info["exception"],
                        trace_id,
                        span_id,
                    )
                    if info["exception"] is not None:
                        # Let the error response go out before reporting.
                        await asyncio.sleep(0)
                    _spawn(_report(sentinel_url, event))
