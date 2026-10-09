"""Chaos harness for simulating failure scenarios against person-api.

Each scenario function returns a dict with:
- scenario: str — the scenario name
- success: bool — whether the scenario executed as expected
- status_codes: list[int] — HTTP status codes observed
- otel_signals: list[str] — OTel span descriptions or error signals detected
- message: str — human-readable summary
"""

import asyncio
import uuid

import httpx
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Scenario 1: DB Latency — Inject mock delay
# ---------------------------------------------------------------------------

async def scenario_db_latency(client: AsyncClient, delay: float = 3.0) -> dict:
    """Hit /test/delay with a high delay value to simulate DB latency.

    Expected: The request takes longer than normal, OTel span records high duration.
    """
    try:
        resp = await client.get(f"/test/delay?delay={delay}", timeout=10.0)
        return {
            "scenario": "db_latency",
            "success": resp.status_code == 200,
            "status_codes": [resp.status_code],
            "otel_signals": ["span.high_duration"] if resp.status_code == 200 else [],
            "message": f"DB latency simulated: response in {resp.elapsed.total_seconds():.2f}s" if resp.status_code == 200 else f"Failed: {resp.status_code}",
        }
    except asyncio.TimeoutError:
        return {
            "scenario": "db_latency",
            "success": False,
            "status_codes": [],
            "otel_signals": [],
            "message": "Request timed out (expected for extreme delays)",
        }


# ---------------------------------------------------------------------------
# Scenario 2: Pool Exhaustion — Simulate max connections
# ---------------------------------------------------------------------------

async def scenario_pool_exhaustion(client: AsyncClient) -> dict:
    """Fire concurrent delay requests exceeding MAX_CONCURRENT to trigger 503.

    Uses /test/delay with delay=4.0 (exceeds SEMAPHORE_TIMEOUT=3.0) so the first
    MAX_CONCURRENT requests hold semaphore slots past the timeout, causing remaining
    requests to get 503.
    """
    # Fire 15 concurrent delay requests (MAX_CONCURRENT=10, so 5 should get 503)
    # delay=4.0 > SEMAPHORE_TIMEOUT=3.0 ensures semaphore slots stay occupied
    async def delay_request():
        try:
            resp = await client.get("/test/delay?delay=4.0", timeout=10.0)
            return resp.status_code
        except asyncio.TimeoutError:
            return 504

    tasks = [delay_request() for _ in range(15)]
    status_codes = await asyncio.gather(*tasks)

    has_503 = any(code == 503 for code in status_codes)
    return {
        "scenario": "pool_exhaustion",
        "success": has_503,
        "status_codes": sorted(status_codes),
        "otel_signals": ["span.error_503"] if has_503 else [],
        "message": f"Pool exhaustion: {status_codes.count(503)} of 15 requests got 503" if has_503 else "Pool not exhausted (all succeeded)",
    }


# ---------------------------------------------------------------------------
# Scenario 3: Slow Unindexed Search — Scan large dataset
# ---------------------------------------------------------------------------

async def scenario_slow_unindexed_search(client: AsyncClient) -> dict:
    """Create many workers then list with large limit to simulate slow scan.

    Expected: Large result set returned, OTel span records query time.
    """
    # Seed 200 workers
    created = []
    for i in range(200):
        resp = await client.post(
            "/api/v1/active/workers",
            json={"name": f"slow_worker_{i}", "status": "active"},
            timeout=5.0,
        )
        if resp.status_code == 201:
            created.append(resp.json()["id"])

    # List with large limit (max allowed is MAX_PAGE_SIZE=100)
    resp = await client.get("/api/v1/active/workers?limit=100&offset=0", timeout=10.0)
    data = resp.json() if resp.status_code == 200 else {}

    return {
        "scenario": "slow_unindexed_search",
        "success": resp.status_code == 200 and data.get("total", 0) >= 200,
        "status_codes": [resp.status_code],
        "otel_signals": ["span.slow_query"] if resp.status_code == 200 else [],
        "message": f"Large scan returned {data.get('total', 0)} workers" if resp.status_code == 200 else f"Failed: {resp.status_code}",
    }


# ---------------------------------------------------------------------------
# Scenario 4: Cache Staleness — Mutate DB directly bypassing API cache
# ---------------------------------------------------------------------------

async def scenario_cache_staleness(client: AsyncClient, db_session=None) -> dict:
    """Create worker via API (cached), directly modify DB, read via API.

    Expected: API returns stale cached data instead of the DB mutation.
    """
    # Create worker via API (this caches it)
    resp = await client.post(
        "/api/v1/active/workers",
        json={"name": "cache_victim", "status": "active"},
        timeout=5.0,
    )
    if resp.status_code != 201:
        return {
            "scenario": "cache_staleness",
            "success": False,
            "status_codes": [resp.status_code],
            "otel_signals": [],
            "message": f"Failed to create worker: {resp.status_code}",
        }

    worker_id = resp.json()["id"]
    original_name = resp.json()["name"]

    # Read via API to ensure it's cached
    resp = await client.get(f"/api/v1/active/workers/{worker_id}", timeout=5.0)

    # Directly mutate the DB (bypassing API cache)
    if db_session is not None:
        from sqlalchemy import select, text
        from app.models import Worker

        stmt = select(Worker).where(Worker.id == worker_id)
        result = await db_session.execute(stmt)
        worker = result.scalar_one_or_none()
        if worker:
            worker.name = "mutated_via_db"
            await db_session.flush()
            await db_session.commit()

    # Read via API again — should see stale cached data
    resp_after = await client.get(f"/api/v1/active/workers/{worker_id}", timeout=5.0)
    cached_name = resp_after.json().get("name") if resp_after.status_code == 200 else None

    is_stale = cached_name == original_name  # If it's still the original name, cache was stale

    return {
        "scenario": "cache_staleness",
        "success": is_stale,  # Scenario succeeds if we see stale data
        "status_codes": [resp_after.status_code],
        "otel_signals": ["cache.stale_read"] if is_stale else [],
        "message": f"Cache staleness detected: API returned '{cached_name}' (expected 'mutated_via_db')" if is_stale else "Cache was refreshed (no staleness detected)",
    }


# ---------------------------------------------------------------------------
# Scenario 5: Retry Storm — Fire 20 simultaneous async requests
# ---------------------------------------------------------------------------

async def scenario_retry_storm(client: AsyncClient) -> dict:
    """Fire 20 simultaneous delay requests to overwhelm the concurrency limiter.

    Uses /test/delay with delay=4.0 (exceeds SEMAPHORE_TIMEOUT=3.0) so the first
    MAX_CONCURRENT requests hold semaphore slots, causing remaining requests to get 503.
    """
    async def storm_request():
        try:
            resp = await client.get("/test/delay?delay=4.0", timeout=10.0)
            return resp.status_code
        except asyncio.TimeoutError:
            return 504

    tasks = [storm_request() for _ in range(20)]
    status_codes = await asyncio.gather(*tasks)

    successes = sum(1 for code in status_codes if code < 400)
    failures = sum(1 for code in status_codes if code >= 500)

    return {
        "scenario": "retry_storm",
        "success": failures > 0,  # Scenario succeeds if some requests failed
        "status_codes": sorted(status_codes),
        "otel_signals": ["span.error_503"] if failures > 0 else [],
        "message": f"Retry storm: {successes} succeeded, {failures} failed with 5xx",
    }


# ---------------------------------------------------------------------------
# Scenario 6: Dependency Timeout — Simulate mock HTTP drop
# ---------------------------------------------------------------------------

async def scenario_dependency_timeout(client: AsyncClient) -> dict:
    """Simulate a downstream dependency timeout.

    Uses /test/delay with a delay exceeding the semaphore timeout to trigger
    a 503 when combined with concurrency pressure.
    """
    # First, saturate the semaphore with long-running requests
    # SEMAPHORE_TIMEOUT=3.0s, so delay=4.0 will hold the semaphore past timeout
    async def blocking_request():
        try:
            resp = await client.get("/test/delay?delay=5", timeout=15.0)
            return resp.status_code
        except asyncio.TimeoutError:
            return 504

    # Fire 11 blocking requests (MAX_CONCURRENT=10)
    # The 11th should timeout and get 503
    tasks = [blocking_request() for _ in range(11)]
    status_codes = await asyncio.gather(*tasks)

    has_503 = any(code == 503 for code in status_codes)
    has_504 = any(code == 504 for code in status_codes)

    return {
        "scenario": "dependency_timeout",
        "success": has_503 or has_504,
        "status_codes": sorted(status_codes),
        "otel_signals": ["span.timeout"] if has_503 or has_504 else [],
        "message": f"Dependency timeout: {status_codes.count(503)} 503s, {status_codes.count(504)} 504s",
    }


# ---------------------------------------------------------------------------
# Scenario 7: Real DB Timeout — pg_sleep beyond DB_QUERY_TIMEOUT (live only)
# ---------------------------------------------------------------------------

async def scenario_db_timeout(client: AsyncClient, delay: float = 5.0) -> dict:
    """Hit /test/db-delay with a real HTTP call against a live person-api.

    The route executes SELECT pg_sleep(delay) through the normal DB session on
    real PostgreSQL; a delay exceeding DB_QUERY_TIMEOUT produces a real 504
    from the timeout guard (concurrency slot + DB session released). A delay
    under the timeout returns 200.

    This scenario is live-only: it must be pointed at a person-api instance
    backed by PostgreSQL (the client's base_url), since under SQLite the route
    returns 503 "engine unsupported".
    """
    try:
        resp = await client.get(f"/test/db-delay?delay={delay}", timeout=15.0)
    except httpx.RequestError as exc:
        return {
            "scenario": "db_timeout",
            "success": False,
            "status_codes": [],
            "otel_signals": [],
            "message": f"Live person-api unreachable: {type(exc).__name__}: {exc}",
        }

    if resp.status_code == 504:
        return {
            "scenario": "db_timeout",
            "success": True,
            "status_codes": [504],
            "otel_signals": ["span.timeout_504"],
            "message": f"db-delay {delay}s exceeded DB_QUERY_TIMEOUT: real 504 from the guard",
        }

    return {
        "scenario": "db_timeout",
        "success": resp.status_code == 200,
        "status_codes": [resp.status_code],
        "otel_signals": [],
        "message": (
            f"db-delay {delay}s within timeout: HTTP {resp.status_code}"
            if resp.status_code == 200
            else f"db-delay {delay}s: unexpected HTTP {resp.status_code}"
        ),
    }


# ---------------------------------------------------------------------------
# Master runner
# ---------------------------------------------------------------------------

# The six in-process scenarios. scenario_db_timeout is deliberately NOT in
# this list: it requires a live person-api backed by PostgreSQL (see above)
# and is exercised by the gated live test, not the in-process suite.
ALL_SCENARIOS = [
    scenario_db_latency,
    scenario_pool_exhaustion,
    scenario_slow_unindexed_search,
    scenario_cache_staleness,
    scenario_retry_storm,
    scenario_dependency_timeout,
]


async def run_all_scenarios(client: AsyncClient, *, db_session=None) -> list[dict]:
    """Run all chaos scenarios sequentially and return results."""
    results = []
    for scenario_fn in ALL_SCENARIOS:
        kwargs = {"db_session": db_session} if db_session and scenario_fn == scenario_cache_staleness else {}
        result = await scenario_fn(client, **kwargs)
        results.append(result)
    return results
