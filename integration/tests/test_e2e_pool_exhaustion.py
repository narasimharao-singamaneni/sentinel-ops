"""E2E pool-exhaustion loop (live, no mocks).

12 concurrent real worker requests rejected with real 503 by the concurrency
limiter -> person-api error reporter -> Sentinel analyze (real LLM) -> openwindow
incident. The incident's citation must carry the real OTel trace id captured
from the rejected 503's response/log output. If the LLM is down, the session
fixture preflight skips the whole suite (never a stand-in LLM).
"""

import asyncio
import json
import time

import httpx

from e2e_helpers import (
    FALLBACK_ROOT_CAUSE,
    POLL_BUDGET,
    POLL_INTERVAL,
    assert_whitelisted_action,
    citation_trace_matches,
    incidents_matching,
    raw_trace,
    traces_for_request_ids,
)

WORKERS_BASE = "/api/v1/active/workers"
HOLDERS = 10
FLOOD = 12
HOLDER_DELAY = 8.0


async def _poll_for_incident(ow: httpx.AsyncClient, target_resource: str, traces: dict[str, str], budget: float = POLL_BUDGET) -> dict:
    """Poll for an incident from our 503 batch whose citation carries its own
    real trace id and whose root cause came from the real LLM (not the
    manual-review fallback — a fallback ingested first must not end the search).
    """
    deadline = time.monotonic() + budget
    last: dict | None = None
    saw_fallback_candidate = False
    trace_values = set(traces.values())
    while time.monotonic() < deadline:
        data = (await ow.get("/dashboard/data/")).json()
        last = data
        for inc in incidents_matching(data["incidents"], target_resource, "status=503"):
            trace = raw_trace(inc["raw_log"])
            if trace not in trace_values:
                continue
            if citation_trace_matches(inc, trace) and inc.get("root_cause") != FALLBACK_ROOT_CAUSE:
                return inc
            if inc.get("root_cause") == FALLBACK_ROOT_CAUSE:
                saw_fallback_candidate = True
        await asyncio.sleep(POLL_INTERVAL)
    raise AssertionError(
        f"no real-LLM 503 incident with a captured trace id within {budget}s"
        + (" (a fallback incident WAS ingested — LLM path failed under load)" if saw_fallback_candidate else "")
        + f"; last dashboard data: {json.dumps(last)[:2000] if last else '<none>'}"
    )


async def test_pool_exhaustion_503_produces_rca_incident(live_stack: dict):
    person_api = live_stack["person_api"]
    openwindow = live_stack["openwindow"]

    async with httpx.AsyncClient(base_url=person_api, timeout=30.0) as api, httpx.AsyncClient(
        base_url=openwindow, timeout=10.0
    ) as ow:
        created = await api.post(
            WORKERS_BASE, json={"name": f"e2e_pool_exhaustion_{int(time.time())}", "status": "active"}
        )
        assert created.status_code == 201, created.text
        worker_id = created.json()["id"]
        target_path = f"{WORKERS_BASE}/{worker_id}"
        target_resource = f"person-api:{target_path}"

        before = (await ow.get("/dashboard/data/")).json()
        before_ids = {str(i["id"]) for i in before["incidents"]}

        holders = [
            asyncio.ensure_future(api.get(f"/test/delay?delay={HOLDER_DELAY}", timeout=30.0))
            for _ in range(HOLDERS)
        ]
        await asyncio.sleep(2.0)
        flood = await asyncio.gather(*[api.get(target_path, timeout=15.0) for _ in range(FLOOD)])
        holder_results = await asyncio.gather(*holders)
        assert all(r.status_code == 200 for r in holder_results)

        status_codes = [r.status_code for r in flood]
        rejected = [r for r in flood if r.status_code == 503]
        assert rejected, f"expected at least one real 503 from the limiter, got {status_codes}"
        assert any("Too many concurrent requests" in r.text for r in rejected)

        request_ids = [r.headers["x-request-id"] for r in rejected]
        assert all(request_ids), "every 503 response must echo X-Request-ID"
        traces = traces_for_request_ids(live_stack["logs"], request_ids)
        assert all(len(t) == 32 for t in traces.values())

        incident = await _poll_for_incident(ow, target_resource, traces)

    assert str(incident["id"]) not in before_ids, "incident must be new, not pre-existing"
    raw = incident["raw_log"]
    trace = raw_trace(raw)
    assert trace is not None, "incident raw_log must carry a 32-hex trace id"
    assert trace in traces.values(), "incident evidence must reference a captured real 503 trace id"
    assert incident["root_cause"] != FALLBACK_ROOT_CAUSE, "root cause must come from the real LLM, not the fallback"
    assert incident["root_cause"].strip()
    citations = incident["citations"]
    assert citations, "at least one citation is required"
    matched = [c for c in citations if c.get("trace_id") == trace]
    assert matched, f"no citation carries the captured real trace id {trace}"
    assert_whitelisted_action(incident)


async def test_le10_concurrent_control_yields_no_incident(live_stack: dict):
    person_api = live_stack["person_api"]
    openwindow = live_stack["openwindow"]

    async with httpx.AsyncClient(base_url=person_api, timeout=30.0) as api, httpx.AsyncClient(
        base_url=openwindow, timeout=10.0
    ) as ow:
        pool = (await api.get("/api/v1/telemetry/pool")).json()
        assert pool["concurrency_in_use"] == 0, f"limiter must be drained before the control run: {pool}"

        created = await api.post(
            WORKERS_BASE, json={"name": f"e2e_pool_control_{int(time.time())}", "status": "active"}
        )
        assert created.status_code == 201, created.text
        target_path = f"{WORKERS_BASE}/{created.json()['id']}"
        target_resource = f"person-api:{target_path}"

        control = await asyncio.gather(*[api.get(target_path, timeout=15.0) for _ in range(10)])
        assert all(r.status_code == 200 for r in control), [r.status_code for r in control]

        for _ in range(4):
            data = (await ow.get("/dashboard/data/")).json()
            for inc in data["incidents"]:
                assert inc["target_resource"] != target_resource, (
                    "control set (<=10 concurrent, all 200) must never produce an incident"
                )
            await asyncio.sleep(2.0)
