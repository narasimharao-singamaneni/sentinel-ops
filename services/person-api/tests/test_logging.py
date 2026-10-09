"""Tests for structured JSON request logging (8-field line) + general JSON logs."""

import asyncio
import json
import logging

import pytest
from httpx import AsyncClient

from app.core.logging import OtelJsonFormatter


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def request_log_capture() -> "_CaptureHandler":
    handler = _CaptureHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("person-api.requests")
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


def _entries(handler: "_CaptureHandler") -> list[dict]:
    return [json.loads(line) for line in handler.lines if line.strip()]


class TestStructuredRequestLogging:
    """Every request produces exactly one JSON line with the 8 required fields."""

    async def test_request_log_line_has_all_fields(
        self, client: AsyncClient, request_log_capture: "_CaptureHandler"
    ):
        resp = await client.get("/health")
        assert resp.status_code == 200

        entries = _entries(request_log_capture)
        matched = [e for e in entries if e.get("path") == "/health"]
        assert len(matched) == 1, f"expected exactly one /health log line, got {entries}"
        entry = matched[0]

        for field in (
            "timestamp",
            "request_id",
            "trace_id",
            "span_id",
            "method",
            "path",
            "status",
            "duration_ms",
        ):
            assert field in entry, f"missing field {field} in {entry}"

        assert entry["request_id"] == resp.headers["x-request-id"]
        assert entry["method"] == "GET"
        assert entry["status"] == 200
        assert isinstance(entry["duration_ms"], (int, float))

    async def test_request_log_trace_id_matches_otel_span(
        self, client: AsyncClient, request_log_capture: "_CaptureHandler"
    ):
        from app.otel import get_test_exporter

        exporter = get_test_exporter()
        exporter.clear()

        resp = await client.get("/api/v1/active/workers?limit=1")
        assert resp.status_code == 200

        entry = [e for e in _entries(request_log_capture) if e.get("path") == "/api/v1/active/workers"][0]
        assert entry["trace_id"] is not None
        assert len(entry["trace_id"]) == 32
        assert all(c in "0123456789abcdef" for c in entry["trace_id"])
        assert entry["span_id"] is not None
        assert len(entry["span_id"]) == 16

        span_trace_ids = {f"{s.context.trace_id:032x}" for s in exporter.get_finished_spans()}
        assert span_trace_ids, "expected finished OTel spans for the request"
        assert entry["trace_id"] in span_trace_ids

    async def test_rejected_503_request_is_logged_with_status(
        self,
        client: AsyncClient,
        request_log_capture: "_CaptureHandler",
        concurrency_semaphore,
    ):
        async def fire():
            return await client.get("/test/delay?delay=0.3", timeout=10)

        responses = await asyncio.gather(*[fire() for _ in range(12)])
        assert any(r.status_code == 503 for r in responses)

        entries = [e for e in _entries(request_log_capture) if e.get("path") == "/test/delay"]
        assert any(e["status"] == 503 for e in entries), f"no 503 log line in {entries}"
        assert any(e["status"] == 200 for e in entries)

    async def test_startup_log_outside_request_is_json_with_null_trace(
        self, request_log_capture: "_CaptureHandler"
    ):
        output_handler = _CaptureHandler()
        output_handler.setFormatter(OtelJsonFormatter())
        logger = logging.getLogger("person-api")
        logger.addHandler(output_handler)
        try:
            logger.info("service started")
        finally:
            logger.removeHandler(output_handler)

        assert output_handler.lines, "no general log captured"
        entry = json.loads(output_handler.lines[-1])
        assert entry["trace_id"] is None
        assert entry["span_id"] is None
        assert entry["message"] == "service started"
