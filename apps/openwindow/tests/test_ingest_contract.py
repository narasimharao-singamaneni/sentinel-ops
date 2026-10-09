"""Tests for the extended ingest contract (end-to-end-rca-pipeline, Task 5.1).

Covers: GET /health/, additive raw_log/roi persistence, the additive
`duplicate` response flag (false for new, true for suppressed), unchanged
validation, and the additive ROI update endpoint. The pre-existing
tests/test_ingest.py stays unmodified and must keep passing.
"""

import json

import pytest
from django.test import Client

from openwindow.incidents.models import Incident

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


def json_post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


PAYLOAD = {
    "root_cause": "database query timeout on pg_sleep",
    "confidence": 0.9,
    "target_resource": "person-api:/test/db-delay",
    "citations": [
        {"trace_id": "a" * 32, "span_id": "b" * 16, "source": "runbook:db_latency.md", "snippet": "pg_sleep exceeded DB_QUERY_TIMEOUT"}
    ],
    "dry_run_payload": {"action": "reset_pool", "target_resource": "person-api:/test/db-delay", "params": {}, "fallback": False},
    "raw_log": "504 GET /test/db-delay?delay=5 database query timeout after 1.0s",
    "roi": {
        "runbook_class": "db_latency",
        "manual_estimate_minutes": 90.0,
        "tool_time_minutes": None,
        "time_saved_minutes": None,
        "breakdown": [
            {"item": "runbook_base", "label": "reference manual triage base for runbook 'db_latency'", "minutes": 90.0}
        ],
    },
}


class TestHealthEndpoint:
    def test_health_returns_200_when_ready(self, client):
        resp = client.get("/health/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"

    def test_health_rejects_post(self, client):
        assert client.post("/health/").status_code == 405


class TestIngestNewFields:
    def test_new_incident_persists_raw_log_and_roi(self, client):
        resp = json_post(client, "/api/v1/incidents", PAYLOAD)
        assert resp.status_code == 201
        body = resp.json()

        assert body["id"] is not None
        assert body["created_at"]
        assert body["duplicate"] is False

        incident = Incident.objects.get(pk=body["id"])
        assert incident.raw_log == PAYLOAD["raw_log"]
        assert incident.roi == PAYLOAD["roi"]

    def test_ingest_without_new_fields_behaves_as_before(self, client):
        minimal = {"root_cause": "cache miss", "confidence": 0.7, "target_resource": "redis-primary"}
        resp = json_post(client, "/api/v1/incidents", minimal)
        assert resp.status_code == 201
        body = resp.json()
        assert body["duplicate"] is False

        incident = Incident.objects.get(pk=body["id"])
        assert incident.raw_log is None
        assert incident.roi is None

    def test_missing_required_fields_still_400(self, client):
        resp = json_post(client, "/api/v1/incidents", {"confidence": 0.5, "target_resource": "r"})
        assert resp.status_code == 400
        assert "root_cause" in resp.json()["detail"]


class TestIngestDuplicateFlag:
    def test_new_then_duplicate_flags(self, client):
        r1 = json_post(client, "/api/v1/incidents", PAYLOAD)
        assert r1.status_code == 201
        first = r1.json()
        assert first["duplicate"] is False

        r2 = json_post(client, "/api/v1/incidents", PAYLOAD)
        assert r2.status_code == 201
        second = r2.json()

        assert second["duplicate"] is True
        assert second["id"] == first["id"]
        assert second["created_at"] == first["created_at"]
        assert Incident.objects.count() == 1


class TestRoiUpdate:
    def test_update_roi_persists_measured_roi(self, client):
        incident = Incident.objects.create(
            root_cause="DB query timeout",
            confidence=0.9,
            target_resource="person-api:/test/db-delay",
            roi=None,
        )
        measured = {
            "runbook_class": "db_latency",
            "manual_estimate_minutes": 90.0,
            "tool_time_minutes": 0.02,
            "time_saved_minutes": 89.98,
            "breakdown": [{"item": "runbook_base", "label": "reference base", "minutes": 90.0}],
        }
        resp = json_post(client, f"/api/v1/incidents/{incident.id}/roi", {"roi": measured})
        assert resp.status_code == 200
        assert resp.json()["roi"] == measured

        incident.refresh_from_db()
        assert incident.roi == measured

    def test_update_unknown_incident_404(self, client):
        resp = json_post(client, "/api/v1/incidents/999999/roi", {"roi": {}})
        assert resp.status_code == 404

    def test_update_without_roi_object_400(self, client):
        incident = Incident.objects.create(
            root_cause="x", confidence=0.5, target_resource="r"
        )
        assert json_post(client, f"/api/v1/incidents/{incident.id}/roi", {"nope": 1}).status_code == 400
        assert json_post(client, f"/api/v1/incidents/{incident.id}/roi", {"roi": "not-a-dict"}).status_code == 400
