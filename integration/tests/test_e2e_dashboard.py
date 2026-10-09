"""E2E dashboard live render.

+v: a seeded real-LLM incident renders on the live GET /dashboard/ HTML with
its root-cause text and its roi time-saved card values.
-v: the dashboard on an empty database renders 200 with zeroed counts (never
a 500). The empty-state assertions engage whenever the live session's database
is actually empty (always true when this file is run as its documented
verification command, on a fresh per-session database).
"""

import asyncio
import html
import time
import uuid
from datetime import datetime, timezone

import httpx

from e2e_helpers import POLL_INTERVAL


async def test_dashboard_renders_without_500(live_stack: dict):
    """The dashboard must never 500. When the session database is actually
    empty (true when this file runs on its own, on a fresh per-session DB) the
    zeroed empty state must render — that is the "no 500 on empty DB" guarantee.
    """
    openwindow = live_stack["openwindow"]

    async with httpx.AsyncClient(base_url=openwindow, timeout=10.0) as ow:
        page = await ow.get("/dashboard/")
        assert page.status_code == 200, f"dashboard must never 500: {page.status_code}"
        data = (await ow.get("/dashboard/data/")).json()
        counts = data["counts"]

        if counts["total"] == 0:
            assert 'id="chip-total">0<' in page.text, "total chip must render zeroed"
            assert 'id="chip-new">0<' in page.text
            assert 'id="chip-executed">0<' in page.text
            assert "No incidents found" in page.text, "empty state must be visible"
            assert 'id="chip-saved">0.0 min' in page.text
            assert data["totals"]["time_saved_minutes"] == 0.0


async def _seed_incident(sent: httpx.AsyncClient, ow: httpx.AsyncClient) -> dict:
    path = f"/e2e-dashboard-{uuid.uuid4().hex[:8]}"
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
    created = resp.json()
    assert created["ingested"] is True and created["incident_id"], f"incident creation failed: {created}"
    incident_id = str(created["incident_id"])

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        data = (await ow.get("/dashboard/data/")).json()
        incident = next((i for i in data["incidents"] if str(i["id"]) == incident_id), None)
        if incident and incident.get("roi"):
            return incident
        await asyncio.sleep(POLL_INTERVAL)
    raise AssertionError(f"seeded incident {incident_id} did not appear with an roi object")


async def test_dashboard_live_render_contains_incident_and_roi(live_stack: dict):
    sentinel = live_stack["sentinel"]
    openwindow = live_stack["openwindow"]

    async with httpx.AsyncClient(base_url=sentinel, timeout=90.0) as sent, httpx.AsyncClient(
        base_url=openwindow, timeout=10.0
    ) as ow:
        incident = await _seed_incident(sent, ow)
        roi = incident["roi"]

        page = await ow.get("/dashboard/")

    assert page.status_code == 200
    body = page.text
    assert html.escape(incident["root_cause"]) in body, "live HTML must contain the incident's root cause"
    assert incident["target_resource"] in body
    assert str(roi["runbook_class"]) in body, "time-saved card must show the runbook class"
    assert f"{float(roi['time_saved_minutes']):.2f} min" in body, "time-saved card must show the saved minutes"
    assert f"{float(roi['tool_time_minutes']):.4f} min" in body, "time-saved card must show the measured tool time"
    for item in roi["breakdown"]:
        assert f"{float(item['minutes']):.2f} min" in body, f"breakdown value for {item['item']} must render"
    assert f'class="incident-row" data-id="{incident["id"]}"' in body, "live HTML must render the incident row"
