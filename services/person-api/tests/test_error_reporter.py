"""Tests for the fire-and-forget Sentinel error reporter (D3/D4).

The receiver is a real local app: plain uvicorn on a loopback socket — real
HTTP, no mocks.
"""

import asyncio
import logging
import socket
import time
import uuid
from datetime import datetime

import pytest
import uvicorn
from fastapi import FastAPI, Request
from httpx import AsyncClient

from app import error_reporter
from app.config import settings
from app.main import app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _assert_iso8601(ts: str) -> None:
    datetime.fromisoformat(ts.replace("Z", "+00:00"))


async def _wait_until(cond, *, budget: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(interval)
    return cond()


def _assert_event_shape(event: dict) -> None:
    for field in (
        "service",
        "method",
        "path",
        "status_code",
        "timestamp",
        "request_id",
        "trace_id",
        "span_id",
        "error_detail",
    ):
        assert field in event, f"missing field {field} in {event}"
    assert event["service"] == "person-api"
    _assert_iso8601(event["timestamp"])
    assert event["request_id"], "request_id must be present"
    trace_id = event["trace_id"]
    assert isinstance(trace_id, str) and len(trace_id) == 32
    assert all(c in "0123456789abcdef" for c in trace_id)
    assert event["span_id"] is None or (len(event["span_id"]) == 16)
    assert event["error_detail"]


class _Receiver:
    """Real uvicorn app bound to a loopback socket that records analyze events."""

    def __init__(self, *, stall: float | None = None) -> None:
        self.stall = stall
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.received: list[dict] = []
        self.received_at: list[float] = []
        self.connections: int = 0

        self.app = FastAPI()

        @self.app.middleware("http")
        async def _count(request: Request, call_next):
            self.connections += 1
            return await call_next(request)

        @self.app.post("/api/v1/analyze")
        async def analyze(request: Request):
            if self.stall is not None:
                await asyncio.sleep(self.stall)
            body = await request.json()
            self.received.append(body)
            self.received_at.append(time.monotonic())
            return {"ok": True}

        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            timeout_graceful_shutdown=2,
        )
        self.server = uvicorn.Server(config)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self.server.serve())
        for _ in range(200):
            if self.server.started:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError("receiver did not start")

    async def stop(self) -> None:
        self.server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass


@pytest.fixture
async def receiver():
    r = _Receiver()
    await r.start()
    yield r
    await r.stop()


@pytest.fixture
async def stall_receiver():
    r = _Receiver(stall=120.0)
    await r.start()
    yield r
    await r.stop()


@pytest.fixture
def reporter_log_capture() -> list[str]:
    lines: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("person-api.error-reporter")
    logger.addHandler(handler)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)


# Route that raises — used to exercise the unhandled-exception reporting path.
@app.get("/test/boom", include_in_schema=False)
async def _boom():
    raise RuntimeError("boom for reporter test")


async def _fire_503_storm(client: AsyncClient) -> list[tuple[int, str, float]]:
    """Return (status, x_request_id, response_complete_time) for 12 burst requests."""

    async def fire() -> tuple[int, str, float]:
        resp = await client.get("/test/delay?delay=0.3", timeout=10)
        return resp.status_code, resp.headers.get("x-request-id") or "", time.monotonic()

    return list(await asyncio.gather(*[fire() for _ in range(12)]))


class TestReporterPositive:
    async def test_503_reported_with_full_event_after_response_sent(
        self,
        client: AsyncClient,
        receiver: _Receiver,
        monkeypatch,
        concurrency_semaphore,
        in_memory_exporter,
    ):
        monkeypatch.setattr(settings, "SENTINEL_URL", receiver.url)

        results = await _fire_503_storm(client)
        rejected = [(rid, t) for status, rid, t in results if status == 503]
        assert rejected, "expected at least one 503 rejection"

        ok = await _wait_until(lambda: len(receiver.received) >= len(rejected), budget=10)
        assert ok, f"receiver got {len(receiver.received)} events, expected >= {len(rejected)}"

        trace_ids_seen = {f"{s.context.trace_id:032x}" for s in in_memory_exporter.get_finished_spans()}
        events_by_rid = {event["request_id"]: idx for idx, event in enumerate(receiver.received)}

        # Every rejected 503 was reported, with the response sent first.
        for rid, client_done in rejected:
            idx = events_by_rid.get(rid)
            assert idx is not None, f"no reported event for rejected request id {rid}"
            event = receiver.received[idx]
            assert event["status_code"] == 503
            _assert_event_shape(event)
            assert event["method"] == "GET"
            assert event["path"] == "/test/delay"
            assert "Too many concurrent" in event["error_detail"]
            # Derived target_resource per D3: person-api:<path>
            assert f"person-api:{event['path']}" == "person-api:/test/delay"
            # The 503 response was sent before the reporter awaited the receiver.
            assert client_done < receiver.received_at[idx], (
                f"response for {rid} completed at {client_done} after the receiver saw the "
                f"report at {receiver.received_at[idx]}"
            )
        # Every reported event is a real trace (finished OTel span) of this storm.
        for idx, event in enumerate(receiver.received):
            assert event["status_code"] == 503, f"unexpected reported status {event['status_code']}"
            assert event["trace_id"] in trace_ids_seen, (
                f"event trace id {event['trace_id']} not among finished OTel spans"
            )

    async def test_4xx_never_reported(self, client: AsyncClient, receiver: _Receiver, monkeypatch):
        monkeypatch.setattr(settings, "SENTINEL_URL", receiver.url)

        resp = await client.get(f"/api/v1/active/workers/{uuid.uuid4()}")
        assert resp.status_code == 404

        await asyncio.sleep(0.5)
        assert receiver.received == [], f"404 must not be reported, got {receiver.received}"
        assert receiver.connections == 0

    async def test_unhandled_exception_reported_as_500(
        self, client: AsyncClient, receiver: _Receiver, monkeypatch
    ):
        monkeypatch.setattr(settings, "SENTINEL_URL", receiver.url)

        with pytest.raises(RuntimeError):
            await client.get("/test/boom", timeout=10)

        ok = await _wait_until(lambda: len(receiver.received) == 1)
        assert ok, "unhandled exception should be reported"
        event = receiver.received[0]
        assert event["status_code"] == 500
        assert event["path"] == "/test/boom"
        assert "RuntimeError" in event["error_detail"]
        _assert_event_shape(event)


class TestReporterNegative:
    async def test_receiver_down_does_not_affect_response(
        self,
        client: AsyncClient,
        monkeypatch,
        concurrency_semaphore,
        reporter_log_capture: list[str],
    ):
        port = _free_port()  # nothing listening
        monkeypatch.setattr(settings, "SENTINEL_URL", f"http://127.0.0.1:{port}")

        results = await _fire_503_storm(client)
        assert any(status == 503 for status, _rid, _t in results), "expected a 503 in the storm"

        await asyncio.sleep(0.5)
        lines = reporter_log_capture
        assert any("sentinel_report_failed" in line for line in lines), (
            f"expected a swallowed failure log line, got {lines}"
        )

    async def test_30s_timeout_logged_swallowed_no_retry(
        self,
        client: AsyncClient,
        stall_receiver: _Receiver,
        monkeypatch,
        concurrency_semaphore,
        reporter_log_capture: list[str],
    ):
        monkeypatch.setattr(settings, "SENTINEL_URL", stall_receiver.url)

        results = await _fire_503_storm(client)
        assert any(status == 503 for status, _rid, _t in results), "expected a 503 in the storm"

        # The report tasks must finish via the 30s timeout (no hang).
        ok = await _wait_until(
            lambda: not error_reporter.get_pending_report_tasks(),
            budget=45,
            interval=0.5,
        )
        assert ok, "report task did not finish within budget (hang?)"

        # Exactly one connection attempt per reported 503 — no retries.
        reported = sum(1 for status, _rid, _t in results if status == 503)
        assert stall_receiver.connections == reported, (
            f"expected {reported} connection attempt(s), saw {stall_receiver.connections}"
        )
        await asyncio.sleep(1.0)
        assert stall_receiver.connections == reported, "retry observed after the timeout"

        lines = reporter_log_capture
        assert any("sentinel_report_failed" in line for line in lines), (
            f"expected timeout to be logged and swallowed, got {lines}"
        )

        # Later requests are unaffected.
        resp = await client.get("/test/delay?delay=0.1", timeout=10)
        assert resp.status_code == 200

    async def test_unset_sentinel_url_is_noop_with_zero_connections(
        self, client: AsyncClient, receiver: _Receiver, monkeypatch, concurrency_semaphore
    ):
        monkeypatch.setattr(settings, "SENTINEL_URL", None)

        results = await _fire_503_storm(client)
        assert any(status == 503 for status, _rid, _t in results), "expected a 503 in the storm"

        await asyncio.sleep(0.5)
        assert receiver.connections == 0, "unset SENTINEL_URL must produce zero outbound connections"
        assert receiver.received == []
