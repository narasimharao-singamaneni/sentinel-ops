"""E2E real DB-timeout loop (live, no mocks).

A real GET /test/db-delay?delay=5 executes SELECT pg_sleep(5) through the
person-api session on real PostgreSQL; the DB_QUERY_TIMEOUT guard returns a
real 504, the error reporter sends it to Sentinel, the real LLM produces a
non-fallback RCA, and openwindow ingests an incident with a valid, non-empty
ROI breakdown. After the 504 drains, live pool telemetry is back to baseline.
A delay under the timeout (control) produces a 200 and no incident.
"""

import asyncio
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

TARGET_RESOURCE = "person-api:/test/db-delay"


async def _poll_for_db_timeout_incident(ow: httpx.AsyncClient, trace_values: set[str], before_ids: set[str], budget: float = POLL_BUDGET) -> dict:
    deadline = time.monotonic() + budget
    last: dict | None = None
    while time.monotonic() < deadline:
        data = (await ow.get("/dashboard/data/")).json()
        last = data
        for inc in incidents_matching(data["incidents"], TARGET_RESOURCE, "status=504"):
            if str(inc["id"]) in before_ids:
                continue
            trace = raw_trace(inc["raw_log"])
            if trace and trace in trace_values and citation_trace_matches(inc, trace):
                if inc.get("root_cause") != FALLBACK_ROOT_CAUSE:
                    return inc
        await asyncio.sleep(POLL_INTERVAL)
    raise AssertionError(
        f"no real-LLM 504 incident within {budget}s; last dashboard data: "
        f"{str(last)[:2000] if last else '<none>'}"
    )


async def test_db_timeout_504_produces_rca_incident_with_roi(live_stack: dict):
    person_api = live_stack["person_api"]
    openwindow = live_stack["openwindow"]

    async with httpx.AsyncClient(base_url=person_api, timeout=30.0) as api, httpx.AsyncClient(
        base_url=openwindow, timeout=10.0
    ) as ow:
        pool = (await api.get("/api/v1/telemetry/pool")).json()
        assert pool["concurrency_in_use"] == 0, f"expected drained limiter before injection: {pool}"
        before = (await ow.get("/dashboard/data/")).json()
        before_ids = {str(i["id"]) for i in before["incidents"]}

        resp = await api.get("/test/db-delay?delay=5", timeout=15.0)
        assert resp.status_code == 504, f"expected a real 504 from the DB timeout guard, got {resp.status_code}: {resp.text}"
        assert "timeout" in resp.text and "pg_sleep" in resp.text, f"504 body must carry the timeout signature: {resp.text}"

        request_id = resp.headers["x-request-id"]
        traces = traces_for_request_ids(live_stack["logs"], [request_id])
        trace_values = set(traces.values())

        for _ in range(20):
            pool = (await api.get("/api/v1/telemetry/pool")).json()
            if pool["concurrency_in_use"] == 0:
                break
            await asyncio.sleep(0.5)
        assert pool["concurrency_in_use"] == 0, f"concurrency leak after the 504 drain: {pool}"

        incident = await _poll_for_db_timeout_incident(ow, trace_values, before_ids)

    raw = incident["raw_log"]
    assert "status=504" in raw and "timeout" in raw, "incident raw evidence must carry the 504 timeout signature"
    trace = raw_trace(raw)
    assert trace in trace_values, "incident must reference the injected 504's real trace id"
    assert incident["root_cause"] != FALLBACK_ROOT_CAUSE
    assert incident["root_cause"].strip()
    assert_whitelisted_action(incident)

    roi = incident["roi"]
    assert isinstance(roi, dict) and roi, f"incident must carry a valid roi object: {roi!r}"
    assert roi.get("runbook_class"), "roi must name its runbook class"
    breakdown = roi.get("breakdown")
    assert isinstance(breakdown, list) and breakdown, "roi.breakdown must be non-empty"
    for item in breakdown:
        assert set(item) >= {"item", "label", "minutes"}, f"breakdown item malformed: {item!r}"
    assert isinstance(roi.get("manual_estimate_minutes"), (int, float))
    assert isinstance(roi.get("tool_time_minutes"), (int, float)), "measured tool time must be present for a successful ingest"
    saved = roi.get("time_saved_minutes")
    assert isinstance(saved, (int, float)) and saved > 0, f"time_saved_minutes must be > 0, got {saved!r}"


async def test_db_delay_under_timeout_control_yields_no_incident(live_stack: dict):
    person_api = live_stack["person_api"]
    openwindow = live_stack["openwindow"]

    async with httpx.AsyncClient(base_url=person_api, timeout=30.0) as api, httpx.AsyncClient(
        base_url=openwindow, timeout=10.0
    ) as ow:
        resp = await api.get("/test/db-delay?delay=0.3", timeout=10.0)
        assert resp.status_code == 200, f"delay under DB_QUERY_TIMEOUT must return 200, got {resp.status_code}"
        request_id = resp.headers["x-request-id"]

        for _ in range(4):
            data = (await ow.get("/dashboard/data/")).json()
            for inc in data["incidents"]:
                assert request_id not in (inc.get("raw_log") or ""), (
                    "a 200 control request must never be reported or become an incident"
                )
            await asyncio.sleep(2.0)

        pool = (await api.get("/api/v1/telemetry/pool")).json()
        assert pool["concurrency_in_use"] == 0, f"limiter must be drained after the control request: {pool}"
