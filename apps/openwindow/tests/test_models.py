"""Tests for OpenWindow Incident Django model."""

import pytest
from django.core.exceptions import ValidationError
from openwindow.incidents.models import Incident


pytestmark = pytest.mark.django_db


class TestIncidentCreation:
    """Positive (+v) tests for model creation and valid data."""

    def test_create_incident_minimal_fields(self):
        """+v: Creating an incident with required fields succeeds."""
        incident = Incident.objects.create(
            root_cause="database connection pool exhausted",
            confidence=0.92,
            citations=[{"trace_id": "trace-abc", "span_id": "span-1", "snippet": "Pool exhausted"}],
            dry_run_payload={"action": "reset_pool"},
            target_resource="person-api-db-pool",
        )
        assert incident.id is not None
        assert incident.status == Incident.Status.NEW
        assert incident.confidence == 0.92

    def test_create_incident_with_json_fields(self):
        """+v: JSONField citations and dry_run_payload store and retrieve correctly."""
        citations = [
            {"trace_id": "trace-1", "span_id": "s-1", "snippet": "conn error"},
            {"trace_id": "trace-2", "span_id": "s-2", "snippet": "timeout"},
        ]
        payload = {"action": "flush_cache", "scope": "redis"}
        incident = Incident.objects.create(
            root_cause="stale cache entries",
            confidence=0.85,
            citations=citations,
            dry_run_payload=payload,
            target_resource="redis-primary",
        )
        db_incident = Incident.objects.get(pk=incident.pk)
        assert db_incident.citations == citations
        assert db_incident.dry_run_payload == payload

    def test_create_incident_default_status_is_new(self):
        """+v: Default status is NEW when not explicitly set."""
        incident = Incident.objects.create(
            root_cause="test error",
            confidence=0.5,
            target_resource="test-resource",
        )
        assert incident.status == Incident.Status.NEW

    def test_create_incident_explicit_status(self):
        """+v: Explicit status values are accepted."""
        for status in Incident.Status.values:
            incident = Incident.objects.create(
                root_cause=f"test for {status}",
                confidence=0.5,
                status=status,
                target_resource="test-resource",
            )
            assert incident.status == status

    def test_created_at_auto_set(self):
        """+v: created_at is automatically populated on save."""
        incident = Incident.objects.create(
            root_cause="test",
            confidence=0.5,
            target_resource="resource",
        )
        assert incident.created_at is not None

    def test_ordering_by_created_at_desc(self):
        """+v: Default ordering is by -created_at (newest first)."""
        import time
        Incident.objects.create(root_cause="first", confidence=0.5, target_resource="r1")
        time.sleep(0.05)
        second = Incident.objects.create(root_cause="second", confidence=0.5, target_resource="r2")
        time.sleep(0.05)
        Incident.objects.create(root_cause="third", confidence=0.5, target_resource="r3")

        results = list(Incident.objects.all())
        assert results[0].root_cause == "third"
        assert results[1].pk == second.pk
        assert results[2].root_cause == "first"


class TestIncidentValidationErrors:
    """Negative (-v) tests for validation failures."""

    def test_missing_root_cause_raises_validation_error(self):
        """-v: Creating an incident without root_cause raises ValidationError."""
        with pytest.raises(ValidationError):
            Incident(
                confidence=0.5,
                target_resource="resource",
            ).full_clean()

    def test_missing_target_resource_raises_validation_error(self):
        """-v: Creating an incident without target_resource raises ValidationError."""
        with pytest.raises(ValidationError):
            Incident(
                root_cause="some error",
                confidence=0.5,
            ).full_clean()

    def test_invalid_status_raises_validation_error(self):
        """-v: Setting an invalid status choice raises ValidationError."""
        with pytest.raises(ValidationError):
            Incident(
                root_cause="some error",
                confidence=0.5,
                status="INVALID_STATUS",
                target_resource="resource",
            ).full_clean()

    def test_confidence_above_one_raises_validation_error(self):
        """-v: Confidence > 1.0 raises ValidationError via MaxValueValidator."""
        with pytest.raises(ValidationError):
            Incident(
                root_cause="some error",
                confidence=1.5,
                target_resource="resource",
            ).full_clean()
