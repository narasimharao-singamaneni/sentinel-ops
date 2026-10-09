import pytest
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.models import Worker


class TestTelemetryActiveRequest:
    """+v: Making an API request to existing /api/v1/active/workers generates an active
    OTel span with http.status_code = 200 (or 201 for creation)."""

    async def test_request_generates_span(self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter):
        await client.post(
            "/api/v1/active/workers",
            json={"name": "Traced Worker", "status": "active"},
        )

        spans = in_memory_exporter.get_finished_spans()
        request_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(request_spans) >= 1, f"Expected at least 1 SERVER span, got {len(spans)}: {[s.name for s in spans]}"

        span = request_spans[0]
        assert span.name == "POST /api/v1/active/workers"
        # OTel convention: UNSET status means success; OK is only explicitly set for errors
        assert span.status.status_code in (StatusCode.UNSET, StatusCode.OK)
        # The instrumentor sets http.status_code attribute
        status_attr = span.attributes.get("http.status_code")
        assert status_attr == 201, f"Expected http.status_code=201, got {status_attr}"

    async def test_get_request_generates_span_with_200(
        self, client: AsyncClient, active_worker: Worker, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.get(f"/api/v1/active/workers/{active_worker.id}")

        spans = in_memory_exporter.get_finished_spans()
        request_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(request_spans) >= 1

        span = request_spans[0]
        assert span.status.status_code in (StatusCode.UNSET, StatusCode.OK)
        assert span.attributes.get("http.status_code") == 200


class TestTelemetryErrorRequest:
    """-v: Making an unhandled failing request generates an OTel span with
    status_code = ERROR and records exception attributes."""

    async def test_not_found_generates_error_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.get("/api/v1/active/workers/00000000-0000-0000-0000-000000000000")

        spans = in_memory_exporter.get_finished_spans()
        request_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(request_spans) >= 1

        span = request_spans[0]
        # FastAPIInstrumentor marks 4xx/5xx as ERROR status
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes.get("http.status_code") == 404

    async def test_bad_request_generates_error_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        """A 400/422 validation error records an ERROR span with appropriate status."""
        resp = await client.get("/api/v1/active/workers", params={"limit": -999})
        assert resp.status_code in (400, 422)

        spans = in_memory_exporter.get_finished_spans()
        request_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(request_spans) >= 1

        span = request_spans[0]
        # FastAPIInstrumentor marks 4xx/5xx as ERROR status
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes.get("http.status_code") in (400, 422)
