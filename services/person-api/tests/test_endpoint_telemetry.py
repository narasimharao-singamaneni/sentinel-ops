"""Verify that OpenTelemetry spans are generated for every worker endpoint.

Covers both active and inactive worker CRUD operations, plus the /health endpoint.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.models import Worker


class TestTelemetryAllActiveEndpoints:
    """+v: All active worker CRUD endpoints emit trace spans."""

    async def test_create_active_worker_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.post(
            "/api/v1/active/workers",
            json={"name": "Create Worker", "status": "active"},
        )

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 201

    async def test_get_active_worker_span(
        self, client: AsyncClient, active_worker: Worker, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.get(f"/api/v1/active/workers/{active_worker.id}")

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 200

    async def test_update_active_worker_span(
        self, client: AsyncClient, active_worker: Worker, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.put(
            f"/api/v1/active/workers/{active_worker.id}",
            json={"name": "Updated Name"},
        )

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 200

    async def test_delete_active_worker_span(
        self, client: AsyncClient, active_worker: Worker, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.delete(f"/api/v1/active/workers/{active_worker.id}")

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        # DELETE returns 200 with JSON body
        assert req_spans[0].attributes.get("http.status_code") == 200

    async def test_list_active_workers_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.get("/api/v1/active/workers")

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 200


class TestTelemetryAllInactiveEndpoints:
    """+v: All inactive worker CRUD endpoints emit trace spans."""

    async def test_create_inactive_worker_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.post(
            "/api/v1/inactive/workers",
            json={"name": "Inactive Worker", "status": "inactive"},
        )

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 201

    async def test_get_inactive_worker_span(
        self, client: AsyncClient, inactive_worker: Worker, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.get(f"/api/v1/inactive/workers/{inactive_worker.id}")

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 200

    async def test_update_inactive_worker_span(
        self, client: AsyncClient, inactive_worker: Worker, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.put(
            f"/api/v1/inactive/workers/{inactive_worker.id}",
            json={"name": "Updated Inactive"},
        )

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 200

    async def test_delete_inactive_worker_span(
        self, client: AsyncClient, inactive_worker: Worker, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.delete(f"/api/v1/inactive/workers/{inactive_worker.id}")

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        # DELETE returns 200 with JSON body
        assert req_spans[0].attributes.get("http.status_code") == 200

    async def test_list_inactive_workers_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        await client.get("/api/v1/inactive/workers")

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 200


class TestTelemetryHealthEndpoint:
    """+v: /health returns 200 and has a trace span."""

    async def test_health_has_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        resp = await client.get("/health")
        assert resp.status_code == 200

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        assert req_spans[0].attributes.get("http.status_code") == 200


class TestTelemetryErrorEndpoints:
    """-v: Error cases return proper HTTP codes with trace context."""

    async def test_active_not_found_error_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        fake_id = str(uuid4())
        await client.get(f"/api/v1/active/workers/{fake_id}")

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        span = req_spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes.get("http.status_code") == 404

    async def test_inactive_not_found_error_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        fake_id = str(uuid4())
        await client.get(f"/api/v1/inactive/workers/{fake_id}")

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        span = req_spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes.get("http.status_code") == 404

    async def test_active_bad_status_error_span(
        self, client: AsyncClient, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        resp = await client.post(
            "/api/v1/active/workers",
            json={"name": "Bad Status Worker", "status": "unknown_status"},
        )
        assert resp.status_code in (400, 422)

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        span = req_spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes.get("http.status_code") in (400, 422)

    async def test_inactive_update_bad_status_error_span(
        self, client: AsyncClient, inactive_worker: Worker, mock_semaphore, in_memory_exporter: InMemorySpanExporter,
    ):
        resp = await client.put(
            f"/api/v1/inactive/workers/{inactive_worker.id}",
            json={"status": "bad_status"},
        )
        # Pydantic validates status pattern before handler, returns 422
        assert resp.status_code == 422

        spans = in_memory_exporter.get_finished_spans()
        req_spans = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(req_spans) >= 1
        span = req_spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes.get("http.status_code") == 422
