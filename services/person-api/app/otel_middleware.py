"""Custom middleware that marks 4xx/5xx responses as ERROR on the OTel span.

Implemented as raw ASGI (like the rest of the middleware stack) instead of
BaseHTTPMiddleware: the per-request task group and memory-object streams of
BaseHTTPMiddleware add noticeable tail latency under concurrency, and the raw
form avoids the colliding-cancel-scope problem entirely.

The default FastAPIInstrumentor only marks unhandled exceptions as ERROR, so
this middleware explicitly marks all 4xx/5xx responses for trace visibility.
"""

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class OtelStatusMiddleware:
    """Set span status to ERROR for 4xx/5xx HTTP responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state: dict = {"status": None}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
            elif (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and state["status"] is not None
                and state["status"] >= 400
            ):
                # The server span is still open at this point (the
                # instrumentor ends it one send-wrapper further out), so
                # marking it here lands on the recorded span.
                span = trace.get_current_span()
                if span.get_span_context().is_valid:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            f"HTTP {state['status']}",
                        )
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)
