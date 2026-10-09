"""Tests for OpenWindow Incident Ingestion Endpoint & 10-Min Idempotency Guard."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone
from openwindow.incidents.models import Incident

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    """Return Django test client configured for the incident API."""
    from django.test import Client

    return Client()


def json_post(client, path, payload):
    """Post JSON payload using data= + content_type to avoid Django test client json= quirks."""
    return client.post(path, data=json.dumps(payload), content_type="application/json")


VALID_PAYLOAD = {
    "root_cause": "database connection pool exhausted",
    "confidence": 0.92,
    "target_resource": "person-api-db-pool",
    "citations": [{"trace_id": "trace-abc", "span_id": "span-1", "snippet": "Pool exhausted"}],
    "dry_run_payload": {"action": "reset_pool"},
}


class TestIngestIncidentPositive:
    """+v: Valid RCA ingestion creates a new incident."""

    def test_ingest_creates_new_incident(self, client):
        """+v: A valid RCA payload creates an incident with status NEW."""
        response = json_post(client, "/api/v1/incidents", VALID_PAYLOAD)
        assert response.status_code == 201

        body = response.json()
        assert body["status"] == "NEW"
        assert body["root_cause"] == "database connection pool exhausted"
        assert body["target_resource"] == "person-api-db-pool"
        assert body["confidence"] == 0.92
        assert body["id"] is not None
        assert body["citations"] == VALID_PAYLOAD["citations"]
        assert body["dry_run_payload"] == VALID_PAYLOAD["dry_run_payload"]

        # Verify persisted
        incident = Incident.objects.get(pk=body["id"])
        assert incident.status == Incident.Status.NEW

    def test_ingest_minimal_valid_payload(self, client):
        """+v: A minimal payload (required fields only) succeeds."""
        minimal = {
            "root_cause": "cache miss",
            "confidence": 0.75,
            "target_resource": "redis-primary",
        }
        response = json_post(client, "/api/v1/incidents", minimal)
        assert response.status_code == 201
        assert response.json()["status"] == "NEW"

    def test_ingest_returns_older_duplicate(self, client):
        """+v: A duplicate within 10 min returns DUPLICATE_IGNORED with existing id."""
        # First request creates the incident
        r1 = json_post(client, "/api/v1/incidents", VALID_PAYLOAD)
        assert r1.status_code == 201
        assert r1.json()["status"] == "NEW"
        first_id = r1.json()["id"]

        # Second request with same key should be flagged as duplicate
        r2 = json_post(client, "/api/v1/incidents", VALID_PAYLOAD)
        assert r2.status_code == 201
        body = r2.json()
        assert body["status"] == "DUPLICATE_IGNORED"
        assert body["id"] == first_id
        assert "Duplicate incident already tracked" in body["message"]

        # Only one incident persisted
        assert Incident.objects.count() == 1


class TestIngestIncidentIdempotencyWindow:
    """+v: Idempotency guard respects the 10-minute window."""

    def test_older_incident_not_duplicate(self, client):
        """+v: Same key but created >10 min ago is NOT a duplicate; creates a new incident."""
        # Create an incident then backdate its created_at (auto_now_add ignores explicit values)
        old = Incident.objects.create(
            root_cause=VALID_PAYLOAD["root_cause"],
            confidence=VALID_PAYLOAD["confidence"],
            target_resource=VALID_PAYLOAD["target_resource"],
            status=Incident.Status.NEW,
        )
        old_time = timezone.now() - timedelta(minutes=11)
        Incident.objects.filter(pk=old.pk).update(created_at=old_time)
        old.refresh_from_db()

        # Ingest again — should create a NEW incident, not flag as duplicate
        response = json_post(client, "/api/v1/incidents", VALID_PAYLOAD)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "NEW"
        assert body["id"] != old.id

        # Now two incidents exist
        assert Incident.objects.count() == 2

    def test_different_target_resource_not_duplicate(self, client):
        """+v: Same root_cause but different target_resource creates a new incident."""
        first = {
            "root_cause": "cache miss",
            "confidence": 0.8,
            "target_resource": "redis-primary",
        }
        second = {
            "root_cause": "cache miss",
            "confidence": 0.8,
            "target_resource": "redis-replica",
        }
        r1 = json_post(client, "/api/v1/incidents", first)
        assert r1.status_code == 201
        assert r1.json()["status"] == "NEW"

        r2 = json_post(client, "/api/v1/incidents", second)
        assert r2.status_code == 201
        assert r2.json()["status"] == "NEW"

        assert Incident.objects.count() == 2


class TestIngestIncidentValidationErrors:
    """-v: Malformed requests return HTTP 400."""

    def test_missing_root_cause_returns_400(self, client):
        """-v: Missing root_cause returns HTTP 400."""
        payload = {
            "confidence": 0.5,
            "target_resource": "resource",
        }
        response = json_post(client, "/api/v1/incidents", payload)
        assert response.status_code == 400
        assert "root_cause" in response.json()["detail"]

    def test_empty_root_cause_returns_400(self, client):
        """-v: Empty string root_cause returns HTTP 400."""
        payload = {
            "root_cause": "",
            "confidence": 0.5,
            "target_resource": "resource",
        }
        response = json_post(client, "/api/v1/incidents", payload)
        assert response.status_code == 400

    def test_missing_confidence_returns_400(self, client):
        """-v: Missing confidence returns HTTP 400."""
        payload = {
            "root_cause": "some error",
            "target_resource": "resource",
        }
        response = json_post(client, "/api/v1/incidents", payload)
        assert response.status_code == 400
        assert "confidence" in response.json()["detail"]

    def test_confidence_above_one_returns_400(self, client):
        """-v: Confidence > 1.0 returns HTTP 400."""
        payload = {
            "root_cause": "some error",
            "confidence": 1.5,
            "target_resource": "resource",
        }
        response = json_post(client, "/api/v1/incidents", payload)
        assert response.status_code == 400
        assert "confidence" in response.json()["detail"]

    def test_confidence_below_zero_returns_400(self, client):
        """-v: Confidence < 0.0 returns HTTP 400."""
        payload = {
            "root_cause": "some error",
            "confidence": -0.1,
            "target_resource": "resource",
        }
        response = json_post(client, "/api/v1/incidents", payload)
        assert response.status_code == 400
        assert "confidence" in response.json()["detail"]

    def test_missing_target_resource_returns_400(self, client):
        """-v: Missing target_resource returns HTTP 400."""
        payload = {
            "root_cause": "some error",
            "confidence": 0.5,
        }
        response = json_post(client, "/api/v1/incidents", payload)
        assert response.status_code == 400
        assert "target_resource" in response.json()["detail"]

    def test_empty_body_returns_400(self, client):
        """-v: Empty JSON body returns HTTP 400."""
        response = json_post(client, "/api/v1/incidents", {})
        assert response.status_code == 400
