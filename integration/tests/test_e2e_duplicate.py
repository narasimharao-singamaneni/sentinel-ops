"""E2E duplicate suppression: pushing one identical error event through the real
Sentinel analyze twice yields exactly one openwindow incident; the second
analyze response carries ingested: true, duplicate: true and the existing id.
(The harness runs the LLM at temperature 0 so identical events produce
byte-identical root-cause text and the text-based dedupe key is deterministic.)
"""

import asyncio
import uuid
from datetime import datetime, timezone

import httpx


def _make_event(path: str) -> dict:
    return {
        "service": "person-api",
        "method": "GET",
        "path": path,
        "status_code": 503,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": uuid.uuid4().hex,
        "trace_id": uuid.uuid4().hex,
        "span_id": uuid.uuid4().hex[:16],
        "error_detail": '{"detail": "Too many concurrent requests. Please try again shortly."}',
    }


async def test_identical_event_twice_yields_one_incident(live_stack: dict):
    sentinel = live_stack["sentinel"]
    openwindow = live_stack["openwindow"]
    path = f"/e2e-dedupe-{uuid.uuid4().hex[:8]}"
    event = _make_event(path)

    async with httpx.AsyncClient(base_url=sentinel, timeout=90.0) as sent, httpx.AsyncClient(
        base_url=openwindow, timeout=10.0
    ) as ow:
        first = await sent.post("/api/v1/analyze", json=event)
        assert first.status_code == 200, first.text
        d1 = first.json()
        assert d1["ingested"] is True
        assert d1["duplicate"] is False
        assert d1["incident_id"], f"new ingest must return the incident id: {d1}"
        incident_id = str(d1["incident_id"])

        second = await sent.post("/api/v1/analyze", json=event)
        assert second.status_code == 200, second.text
        d2 = second.json()
        assert d2["ingested"] is True
        assert d2["duplicate"] is True, f"second identical event must be flagged duplicate: {d2}"
        assert str(d2["incident_id"]) == incident_id, "duplicate response must carry the existing incident id"

        await asyncio.sleep(0.5)
        data = (await ow.get("/dashboard/data/")).json()
        tracked = [i for i in data["incidents"] if i["target_resource"] == f"person-api:{path}"]
        assert len(tracked) == 1, f"exactly one incident for that evidence, found {len(tracked)}: {tracked}"
        assert str(tracked[0]["id"]) == incident_id
