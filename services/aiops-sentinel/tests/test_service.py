"""Tests for the Sentinel HTTP service.

The service code is real and runs in-process via ASGI transport (unit scope).
The LLM is stubbed only at the client boundary (duck-typed fake). The
openwindow ingest target is a REAL loopback HTTP server (no mocks).
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from httpx import ASGITransport, AsyncClient

from app.engine.orchestrator import Orchestrator


class FakeLLM:
    """LLM boundary stub. Echoes the incident trace id from the grounded prompt
    (mirrors a well-behaved grounded LLM: 'cite the provided trace_id')."""

    def __init__(self, structured_response):
        self.structured_response = structured_response

    async def generate_structured(self, prompt: str) -> dict:
        import copy
        import re

        response = copy.deepcopy(self.structured_response)
        match = re.search(r"Trace ID: (\S+)", prompt)
        if match and response.get("citations"):
            response["citations"][0]["trace_id"] = match.group(1)
        return response


class _IngestState:
    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.response = {
            "id": 7,
            "status": "NEW",
            "created_at": "2026-08-14T10:05:00Z",
            "duplicate": False,
        }


_state = _IngestState()


class _IngestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _state.requests.append((self.path, body))
        if self.path.endswith("/roi"):
            payload = b'{"ok": true}'
            status = 200
        else:
            payload = json.dumps(_state.response).encode()
            status = 201
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class _IngestServer(ThreadingHTTPServer):
    daemon_threads = True  # never let a per-request handler block interpreter shutdown


@pytest.fixture
def ingest_server():
    _state.__init__()
    server = _IngestServer(("127.0.0.1", 0), _IngestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _valid_rca() -> dict:
    return {
        "root_cause": "database query timeout: pg_sleep exceeded DB_QUERY_TIMEOUT",
        "confidence": 0.9,
        "citations": [
            {
                "trace_id": "trace-evt-1",
                "span_id": "span-evt-1",
                "source": "incident_event",
                "snippet": "504 database query timeout after 1.0s",
            }
        ],
        "dry_run_payload": {
            "action": "reset_pool",
            "target_resource": "person-api-db-pool",
            "params": {},
            "fallback": False,
        },
    }


def _error_event(**overrides) -> dict:
    event = {
        "service": "person-api",
        "method": "GET",
        "path": "/test/db-delay",
        "status_code": 504,
        "timestamp": "2026-08-14T10:00:00Z",
        "request_id": "req-42",
        "trace_id": "trace-evt-1",
        "span_id": "span-evt-1",
        "error_detail": "database query timeout after 1.0s (pg_sleep 3.0s exceeded DB_QUERY_TIMEOUT)",
    }
    event.update(overrides)
    return event


@pytest.fixture
def svc_client(ingest_server, monkeypatch):
    monkeypatch.setenv("OPENWINDOW_INGEST_URL", f"{ingest_server}/api/v1/incidents")
    orchestrator = Orchestrator(client=FakeLLM(_valid_rca()))
    from app.service import create_app, _executions

    _executions.clear()
    app = create_app(orchestrator=orchestrator)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://sentinel-test")


class TestHealth:
    async def test_health_returns_ready(self, svc_client):
        async with svc_client as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


class TestAnalyze:
    async def test_analyze_happy_path_returns_full_shape(self, svc_client):
        async with svc_client as client:
            resp = await client.post("/api/v1/analyze", json=_error_event())
        assert resp.status_code == 200
        data = resp.json()
        assert data["incident_id"] == "7"
        assert data["ingested"] is True
        assert data["duplicate"] is False
        assert data["rca"]["root_cause"].startswith("database query timeout")
        assert data["rca"]["confidence"] == 0.9
        assert len(data["rca"]["citations"]) == 1
        assert data["rca"]["dry_run_payload"]["action"] == "reset_pool"
        assert "roi" in data

    async def test_analyze_roi_measures_real_tool_time_from_created_at(self, svc_client):
        # ingest response created_at = 10:05:00Z, event timestamp = 10:00:00Z -> 5.0 min
        async with svc_client as client:
            resp = await client.post("/api/v1/analyze", json=_error_event())
        data = resp.json()
        assert data["roi"]["tool_time_minutes"] == 5.0
        assert data["roi"]["time_saved_minutes"] == data["roi"]["manual_estimate_minutes"] - 5.0
        assert data["roi"]["breakdown"]
        for item in data["roi"]["breakdown"]:
            assert "item" in item and "label" in item and "minutes" in item

    async def test_analyze_pushes_incident_with_payload_fields(self, svc_client):
        async with svc_client as client:
            await client.post("/api/v1/analyze", json=_error_event())
        assert len(_state.requests) >= 1
        path, body = _state.requests[0]
        assert path == "/api/v1/incidents"
        assert body["root_cause"].startswith("database query timeout")
        assert body["target_resource"] == "person-api:/test/db-delay"
        assert body["raw_log"]
        assert body["citations"][0]["trace_id"] == "trace-evt-1"
        assert body["roi"]["runbook_class"]

    async def test_analyze_raw_log_string_is_accepted(self, svc_client):
        async with svc_client as client:
            resp = await client.post(
                "/api/v1/analyze",
                content=b"2026-08-14 ERROR db: TimeoutError query exceeded 30s",
                headers={"Content-Type": "text/plain"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rca"]["root_cause"]
        assert data["ingested"] is True

    async def test_analyze_duplicate_state_propagates(self, svc_client):
        _state.response = {
            "id": 7,
            "status": "DUPLICATE_IGNORED",
            "created_at": "2026-08-14T10:05:00Z",
            "duplicate": True,
        }
        async with svc_client as client:
            resp = await client.post("/api/v1/analyze", json=_error_event())
        data = resp.json()
        assert data["ingested"] is True
        assert data["duplicate"] is True
        assert data["incident_id"] == "7"

    async def test_analyze_ingest_down_degrades_gracefully(self, svc_client, monkeypatch):
        monkeypatch.setenv("OPENWINDOW_INGEST_URL", f"http://127.0.0.1:{_free_port()}/api/v1/incidents")
        async with svc_client as client:
            resp = await client.post("/api/v1/analyze", json=_error_event())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] is False
        assert data["incident_id"] is None
        assert data["duplicate"] is False
        assert data["rca"]["root_cause"]  # RCA still returned
        assert data["roi"]["tool_time_minutes"] is None
        assert data["roi"]["time_saved_minutes"] is None
        assert data["roi"]["manual_estimate_minutes"] is not None

    async def test_analyze_missing_trace_id_uses_synthetic_recorded_in_roi(self, svc_client):
        event = _error_event(trace_id=None)
        async with svc_client as client:
            resp = await client.post("/api/v1/analyze", json=event)
        assert resp.status_code == 200
        data = resp.json()
        items = {b["item"] for b in data["roi"]["breakdown"]}
        assert "synthetic_trace_id" in items
        cit = data["rca"]["citations"][0]
        assert cit["trace_id"].startswith("synthetic-")

    async def test_analyze_invalid_json_returns_4xx_not_5xx(self, svc_client):
        async with svc_client as client:
            resp = await client.post(
                "/api/v1/analyze",
                content=b'{"broken": ',
                headers={"Content-Type": "application/json"},
            )
        assert 400 <= resp.status_code < 500

    async def test_analyze_empty_body_returns_4xx(self, svc_client):
        async with svc_client as client:
            resp = await client.post("/api/v1/analyze", content=b"   ")
        assert 400 <= resp.status_code < 500


class TestExecute:
    async def test_execute_records_and_acknowledges(self, svc_client):
        async with svc_client as client:
            resp = await client.post(
                "/api/v1/execute",
                json={"incident_id": "7", "dry_run_payload": {"action": "reset_pool"}},
            )
        assert resp.status_code == 200
        assert resp.json() == {"status": "executed", "incident_id": "7"}

    async def test_executions_listing_is_deterministic(self, svc_client):
        async with svc_client as client:
            await client.post("/api/v1/execute", json={"incident_id": "7"})
            await client.post("/api/v1/execute", json={"incident_id": "8"})
            resp = await client.get("/api/v1/executions")
        assert resp.status_code == 200
        executions = resp.json()["executions"]
        assert len(executions) == 2
        assert executions[0]["incident_id"] == "7"
        assert executions[0]["status"] == "executed"
        assert executions[0]["dry_run"] is True
        assert executions[1]["incident_id"] == "8"

    async def test_execute_missing_incident_id_returns_4xx(self, svc_client):
        async with svc_client as client:
            resp = await client.post("/api/v1/execute", json={"dry_run_payload": {}})
        assert 400 <= resp.status_code < 500

    async def test_execute_invalid_json_returns_4xx(self, svc_client):
        async with svc_client as client:
            resp = await client.post(
                "/api/v1/execute",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
        assert 400 <= resp.status_code < 500
