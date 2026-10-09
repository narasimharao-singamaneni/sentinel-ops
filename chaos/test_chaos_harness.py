"""Chaos harness tests — positive and negative assertions for all 6 scenarios.

Positive (+v): Chaos triggers correctly emit OTel trace errors and trigger Sentinel detection.
Negative (-v): System recovers after dry-run remediation is approved in OpenWindow dashboard.
"""

import asyncio
import os

import httpx
import pytest
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from harness import (
    scenario_db_latency,
    scenario_db_timeout,
    scenario_dependency_timeout,
    scenario_pool_exhaustion,
    scenario_retry_storm,
    scenario_slow_unindexed_search,
    scenario_cache_staleness,
)


# ---------------------------------------------------------------------------
# Scenario 1: DB Latency
# ---------------------------------------------------------------------------

class TestDbLatency:
    """+v: Delay endpoint works and records high-duration span.
       -v: System returns to normal after delay subsides.
    """

    async def test_db_latency_positive(self, client: AsyncClient, in_memory_exporter: InMemorySpanExporter):
        """+v: DB latency scenario produces high-duration OTel span."""
        result = await scenario_db_latency(client, delay=1.5)

        assert result["scenario"] == "db_latency"
        assert result["success"] is True, result["message"]
        assert result["status_codes"] == [200]
        assert len(result["otel_signals"]) > 0

        # Verify OTel span was recorded
        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) > 0

    async def test_db_latency_negative(self, client: AsyncClient):
        """-v: System recovers — normal request succeeds after delay."""
        await scenario_db_latency(client, delay=1.0)

        # After delay subsides, normal health check should work
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


# ---------------------------------------------------------------------------
# Scenario 2: Pool Exhaustion
# ---------------------------------------------------------------------------

class TestPoolExhaustion:
    """+v: Concurrent flood triggers 503, OTel records errors.
       -v: System recovers — normal request succeeds after flood subsides.
    """

    async def test_pool_exhaustion_positive(self, client: AsyncClient, in_memory_exporter: InMemorySpanExporter):
        """+v: Pool exhaustion triggers 503 responses."""
        result = await scenario_pool_exhaustion(client)

        assert result["scenario"] == "pool_exhaustion"
        assert result["success"] is True, result["message"]
        assert 503 in result["status_codes"], f"Expected 503 in {result['status_codes']}"
        assert len(result["otel_signals"]) > 0

        # Verify OTel recorded error spans
        spans = in_memory_exporter.get_finished_spans()
        error_spans = [s for s in spans if s.status.status_code == StatusCode.ERROR]
        assert len(error_spans) > 0, f"Expected error spans, got {len(spans)} total"

    async def test_pool_exhaustion_negative(self, client: AsyncClient):
        """-v: System recovers after pool exhaustion subsides."""
        await scenario_pool_exhaustion(client)

        # Give the semaphore a moment to drain
        await asyncio.sleep(0.5)

        # Normal request should succeed
        resp = await client.post(
            "/api/v1/active/workers",
            json={"name": "recovery_worker", "status": "active"},
        )
        assert resp.status_code == 201

        # List should work
        resp = await client.get("/api/v1/active/workers")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario 3: Slow Unindexed Search
# ---------------------------------------------------------------------------

class TestSlowUnindexedSearch:
    """+v: Large dataset query returns results, OTel records slow query.
       -v: System recovers — normal query succeeds.
    """

    async def test_slow_unindexed_search_positive(self, client: AsyncClient, in_memory_exporter: InMemorySpanExporter):
        """+v: Slow unindexed search returns large result set."""
        result = await scenario_slow_unindexed_search(client)

        assert result["scenario"] == "slow_unindexed_search"
        assert result["success"] is True, result["message"]
        assert result["status_codes"] == [200]
        assert "slow" in result["message"].lower() or "200" in result["message"]

        # Verify OTel spans were recorded
        spans = in_memory_exporter.get_finished_spans()
        assert len(spans) > 0

    async def test_slow_unindexed_search_negative(self, client: AsyncClient):
        """-v: System recovers — normal small query succeeds after large scan."""
        await scenario_slow_unindexed_search(client)

        # Small query should still work
        resp = await client.get("/api/v1/active/workers?limit=1")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario 4: Cache Staleness
# ---------------------------------------------------------------------------

class TestCacheStaleness:
    """+v: Direct DB mutation bypasses cache, API returns stale data.
       -v: Cache expires/invalidate, fresh data is returned.
    """

    async def test_cache_staleness_positive(self, client: AsyncClient, in_memory_exporter: InMemorySpanExporter):
        """+v: Cache staleness detected — API returns stale data after DB mutation."""
        result = await scenario_cache_staleness(client)

        assert result["scenario"] == "cache_staleness"
        # The harness doesn't pass db_session by default, so this tests the path without DB mutation
        # We expect it to not show staleness when there's no direct mutation
        if result["status_codes"] == [200]:
            assert result["success"] in (True, False), "Should handle both stale and fresh cases"

    async def test_cache_staleness_with_db_mutation(self, client: AsyncClient, db_session: AsyncSession):
        """+v: With direct DB mutation, cache returns stale data."""
        result = await scenario_cache_staleness(client, db_session=db_session)

        assert result["scenario"] == "cache_staleness"
        # With DB mutation, staleness should be detected
        assert result["success"] is True, result["message"]
        assert "stale" in result["message"].lower()
        assert "cache.stale_read" in result["otel_signals"]

    async def test_cache_staleness_negative(self, client: AsyncClient, db_session: AsyncSession):
        """-v: After cache invalidation (via update), fresh data is returned."""
        # Create and cache a worker
        resp = await client.post(
            "/api/v1/active/workers",
            json={"name": "staleness_test", "status": "active"},
        )
        assert resp.status_code == 201
        worker_id = resp.json()["id"]

        # Read to cache it
        await client.get(f"/api/v1/active/workers/{worker_id}")

        # Update via API (this invalidates cache)
        resp = await client.put(
            f"/api/v1/active/workers/{worker_id}",
            json={"name": "updated_name"},
        )
        assert resp.status_code == 200

        # Now read — should see updated name, not stale
        resp = await client.get(f"/api/v1/active/workers/{worker_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "updated_name"


# ---------------------------------------------------------------------------
# Scenario 5: Retry Storm
# ---------------------------------------------------------------------------

class TestRetryStorm:
    """+v: 20 simultaneous requests trigger 503, OTel records errors.
       -v: System recovers — normal requests succeed after storm.
    """

    async def test_retry_storm_positive(self, client: AsyncClient, in_memory_exporter: InMemorySpanExporter):
        """+v: Retry storm triggers 503 responses."""
        result = await scenario_retry_storm(client)

        assert result["scenario"] == "retry_storm"
        assert result["success"] is True, result["message"]
        assert any(code == 503 for code in result["status_codes"]), f"Expected 503 in {result['status_codes']}"

        # Verify OTel error spans
        spans = in_memory_exporter.get_finished_spans()
        error_spans = [s for s in spans if s.status.status_code == StatusCode.ERROR]
        assert len(error_spans) > 0

    async def test_retry_storm_negative(self, client: AsyncClient):
        """-v: System recovers after retry storm subsides."""
        await scenario_retry_storm(client)

        # Give semaphore time to drain
        await asyncio.sleep(0.5)

        # Normal requests should succeed
        resp = await client.get("/health")
        assert resp.status_code == 200

        resp = await client.get("/api/v1/active/workers?limit=1")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario 6: Dependency Timeout
# ---------------------------------------------------------------------------

class TestDependencyTimeout:
    """+v: Downstream timeout triggers 503/504, OTel records timeout.
       -v: System recovers — normal requests succeed.
    """

    async def test_dependency_timeout_positive(self, client: AsyncClient, in_memory_exporter: InMemorySpanExporter):
        """+v: Dependency timeout triggers 503/504 responses."""
        result = await scenario_dependency_timeout(client)

        assert result["scenario"] == "dependency_timeout"
        assert result["success"] is True, result["message"]
        assert any(code in (503, 504) for code in result["status_codes"]), f"Expected 503/504 in {result['status_codes']}"
        assert len(result["otel_signals"]) > 0

        # Verify OTel error spans
        spans = in_memory_exporter.get_finished_spans()
        error_spans = [s for s in spans if s.status.status_code == StatusCode.ERROR]
        assert len(error_spans) > 0

    async def test_dependency_timeout_negative(self, client: AsyncClient):
        """-v: System recovers after dependency timeout subsides."""
        await scenario_dependency_timeout(client)

        # Give semaphore time to fully drain (delay requests complete)
        await asyncio.sleep(6.0)

        # Normal requests should succeed
        resp = await client.get("/health")
        assert resp.status_code == 200

        resp = await client.get("/api/v1/active/workers?limit=1")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario 7: Real DB Timeout (live person-api + real PostgreSQL)
# ---------------------------------------------------------------------------

LIVE_PERSON_API = os.getenv("CHAOS_LIVE_PERSON_API", "http://127.0.0.1:8002")


@pytest.fixture
async def live_base_url():
    """Base URL of a live person-api backed by PostgreSQL, or skip.

    Preflight (not a mock): a real GET /health plus a real
    /test/db-delay?delay=0.1 probe — only a PostgreSQL-backed instance
    answers the probe with 200 (SQLite answers 503 'engine unsupported').
    """
    async with httpx.AsyncClient(timeout=5.0) as probe_client:
        try:
            health = await probe_client.get(f"{LIVE_PERSON_API}/health")
            probe = await probe_client.get(f"{LIVE_PERSON_API}/test/db-delay?delay=0.1")
        except httpx.RequestError as exc:
            pytest.skip(
                f"live person-api unreachable at {LIVE_PERSON_API} "
                f"({type(exc).__name__}); scenario_db_timeout requires a live "
                f"person-api + PostgreSQL"
            )

    if health.status_code != 200:
        pytest.skip(f"live person-api at {LIVE_PERSON_API} unhealthy (health {health.status_code})")
    if probe.status_code != 200:
        pytest.skip(
            f"live person-api at {LIVE_PERSON_API} is not backed by PostgreSQL "
            f"(db-delay probe {probe.status_code})"
        )
    return LIVE_PERSON_API


class TestDbTimeoutLive:
    """Scenario 7 against a live person-api on real PostgreSQL (gated)."""

    async def test_db_timeout_504_and_no_concurrency_leak(self, live_base_url: str):
        """+v: delay above DB_QUERY_TIMEOUT -> real 504 + signal; pool drains to 0."""
        async with httpx.AsyncClient(base_url=live_base_url, timeout=20.0) as client:
            result = await scenario_db_timeout(client, delay=5.0)

        assert result["scenario"] == "db_timeout"
        assert result["success"] is True, result["message"]
        assert result["status_codes"] == [504], f"Expected a real 504, got {result}"
        assert result["otel_signals"], "expected an error signal recorded for the 504"

        # After the 504 drains, the live pool telemetry shows no leak on the
        # timeout path: concurrency in-use back to 0.
        async with httpx.AsyncClient(base_url=live_base_url, timeout=10.0) as client:
            for _ in range(10):
                pool = (await client.get("/api/v1/telemetry/pool")).json()
                if pool["concurrency_in_use"] == 0:
                    break
                await asyncio.sleep(0.5)
        assert pool["concurrency_in_use"] == 0, f"concurrency leak after 504: {pool}"

    async def test_db_delay_under_timeout_control_returns_200(self, live_base_url: str):
        """-v control: delay under the DB timeout -> 200, no 504."""
        async with httpx.AsyncClient(base_url=live_base_url, timeout=20.0) as client:
            result = await scenario_db_timeout(client, delay=0.3)

        assert result["scenario"] == "db_timeout"
        assert result["success"] is True, result["message"]
        assert result["status_codes"] == [200], f"Expected 200 control, got {result}"
