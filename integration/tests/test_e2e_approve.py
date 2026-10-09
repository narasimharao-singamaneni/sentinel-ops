"""E2E approve-to-execution loop: a live NEW incident is approved through the
openwindow dashboard API; Sentinel records the execution deterministically
(GET /api/v1/executions — no log parsing) and the incident reaches EXECUTED.
Re-approving is rejected (400, unchanged contract) and never double-executes.
"""

import uuid
from datetime import datetime, timezone

import httpx


async def _create_incident(sent: httpx.AsyncClient) -> tuple[str, str]:
    path = f"/e2e-approve-{uuid.uuid4().hex[:8]}"
    event = {
        "service": "person-api",
        "method": "GET",
        "path": path,
        "status_code": 504,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": uuid.uuid4().hex,
        "trace_id": uuid.uuid4().hex,
        "span_id": uuid.uuid4().hex[:16],
        "error_detail": '{"detail": "database query timeout after 1.0s (pg_sleep 5.0s exceeded DB_QUERY_TIMEOUT)"}',
    }
    resp = await sent.post("/api/v1/analyze", json=event)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ingested"] is True and data["incident_id"], f"incident creation failed: {data}"
    return str(data["incident_id"]), path


def _executions_for(executions: list[dict], incident_id: str) -> list[dict]:
    return [e for e in executions if e["incident_id"] == incident_id]


async def test_approve_new_incident_executes_end_to_end(live_stack: dict):
    sentinel = live_stack["sentinel"]
    openwindow = live_stack["openwindow"]

    async with httpx.AsyncClient(base_url=sentinel, timeout=90.0) as sent, httpx.AsyncClient(
        base_url=openwindow, timeout=10.0
    ) as ow:
        incident_id, _path = await _create_incident(sent)

        data = (await ow.get("/dashboard/data/")).json()
        incident = next(i for i in data["incidents"] if str(i["id"]) == incident_id)
        assert incident["status"] == "NEW"

        approve = await ow.post(f"/incidents/{incident_id}/approve/")
        assert approve.status_code == 200, approve.text
        assert approve.json()["status"] == "EXECUTED", approve.text

        executions = (await sent.get("/api/v1/executions")).json()["executions"]
        recorded = _executions_for(executions, incident_id)
        assert len(recorded) == 1, f"exactly one execution record expected, got {recorded}"
        assert recorded[0]["status"] == "executed"
        assert recorded[0]["dry_run"] is True
        assert recorded[0]["timestamp"]

        data = (await ow.get("/dashboard/data/")).json()
        incident = next(i for i in data["incidents"] if str(i["id"]) == incident_id)
        assert incident["status"] == "EXECUTED"

        again = await ow.post(f"/incidents/{incident_id}/approve/")
        assert again.status_code == 400, f"re-approval must be rejected unchanged: {again.text}"

        executions = (await sent.get("/api/v1/executions")).json()["executions"]
        recorded = _executions_for(executions, incident_id)
        assert len(recorded) == 1, "re-approval must not double-execute"
