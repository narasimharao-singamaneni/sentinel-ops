"""Tests for the OpenWindow Dashboard & Human Approval Callback."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, override_settings

from openwindow.incidents.models import Incident


@pytest.fixture()
def client():
    """Django test client."""
    return Client()


@pytest.fixture()
def sentinel_callback_ok():
    """Mock Sentinel callback that returns 200."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        yield mock_post


@pytest.fixture()
def sentinel_callback_unreachable():
    """Mock Sentinel callback that raises httpx.RequestError."""
    import httpx
    with patch("httpx.post", side_effect=httpx.RequestError("Connection refused")) as mock_post:
        yield mock_post


# ---------------------------------------------------------------------------
# Dashboard — positive tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDashboard:
    """Tests for GET /dashboard/."""

    def test_empty_dashboard_renders_200(self, client):
        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        assert b"OpenWindow" in resp.content
        assert b"Sentinel Ops" in resp.content

    def test_empty_dashboard_shows_zero_counts(self, client):
        resp = client.get("/dashboard/")
        assert resp.context["counts"]["total"] == 0
        assert resp.context["counts"]["new"] == 0
        assert resp.context["counts"]["approved"] == 0
        assert resp.context["counts"]["executed"] == 0
        assert resp.context["counts"]["duplicate"] == 0

    def test_dashboard_listed_incidents(self, client):
        Incident.objects.create(
            root_cause="DB pool exhaustion",
            confidence=0.92,
            target_resource="person-api-db-pool",
            status=Incident.Status.NEW,
        )
        Incident.objects.create(
            root_cause="Cache miss rate > 80%",
            confidence=0.75,
            target_resource="redis-primary",
            status=Incident.Status.EXECUTED,
        )

        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        assert resp.context["counts"]["total"] == 2
        assert resp.context["counts"]["new"] == 1
        assert resp.context["counts"]["executed"] == 1

        assert len(resp.context["incidents"]) == 2
        assert b"DB pool exhaustion" in resp.content
        assert b"Cache miss rate" in resp.content


# ---------------------------------------------------------------------------
# Dashboard — negative tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDashboardNegative:
    """Negative tests for dashboard."""

    def test_dashboard_post_returns_405(self, client):
        """Dashboard only accepts GET."""
        resp = client.post("/dashboard/")
        assert resp.status_code == 405

    def test_dashboard_root_alias_works(self, client):
        """GET / should also render dashboard."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"OpenWindow" in resp.content


# ---------------------------------------------------------------------------
# Approve — positive tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestApproveIncident:
    """Tests for POST /incidents/{id}/approve/."""

    def test_approve_new_incident_success(self, client, sentinel_callback_ok):
        """Approving a NEW incident transitions to EXECUTED when callback succeeds."""
        incident = Incident.objects.create(
            root_cause="High CPU on worker-3",
            confidence=0.88,
            target_resource="worker-3",
            status=Incident.Status.NEW,
            dry_run_payload={"action": "restart"},
        )

        resp = client.post(f"/incidents/{incident.id}/approve/")
        assert resp.status_code == 200

        data = json.loads(resp.content)
        assert data["status"] == "EXECUTED"
        assert "approved and executed" in data["message"]

        # Verify httpx.post was called
        assert sentinel_callback_ok.called
        call_json = sentinel_callback_ok.call_args[1]["json"]
        assert call_json["incident_id"] == incident.id
        assert call_json["dry_run_payload"] == {"action": "restart"}

        incident.refresh_from_db()
        assert incident.status == Incident.Status.EXECUTED

    def test_approve_unreachable_sentinel(self, client, sentinel_callback_unreachable):
        """When Sentinel is unreachable, incident stays APPROVED."""
        incident = Incident.objects.create(
            root_cause="Disk full on /data",
            confidence=0.95,
            target_resource="storage-01",
            status=Incident.Status.NEW,
        )

        resp = client.post(f"/incidents/{incident.id}/approve/")
        assert resp.status_code == 200

        data = json.loads(resp.content)
        assert data["status"] == "APPROVED"
        assert "awaiting retry" in data["message"]

        incident.refresh_from_db()
        assert incident.status == Incident.Status.APPROVED


# ---------------------------------------------------------------------------
# Approve — negative tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestApproveIncidentNegative:
    """Negative tests for approve endpoint."""

    def test_approve_nonexistent_incident_404(self, client):
        """Approving a non-existent ID returns 404."""
        resp = client.post("/incidents/99999/approve/")
        assert resp.status_code == 404
        data = json.loads(resp.content)
        assert "not found" in data["detail"]

    def test_approve_already_executed_400(self, client, sentinel_callback_ok):
        """Cannot approve an already executed incident."""
        incident = Incident.objects.create(
            root_cause="Already done",
            confidence=0.99,
            target_resource="something",
            status=Incident.Status.EXECUTED,
        )

        resp = client.post(f"/incidents/{incident.id}/approve/")
        assert resp.status_code == 400
        data = json.loads(resp.content)
        assert "EXECUTED" in data["detail"]

    def test_approve_duplicate_ignored_400(self, client, sentinel_callback_ok):
        """Cannot approve a DUPLICATE_IGNORED incident."""
        incident = Incident.objects.create(
            root_cause="Already seen",
            confidence=0.6,
            target_resource="resource",
            status=Incident.Status.DUPLICATE_IGNORED,
        )

        resp = client.post(f"/incidents/{incident.id}/approve/")
        assert resp.status_code == 400

    def test_approve_get_returns_405(self, client):
        """Approve endpoint only accepts POST."""
        resp = client.get("/incidents/1/approve/")
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Approve — edge cases
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestApproveEdgeCases:
    """Edge cases for approve endpoint."""

    def test_approve_with_no_callback_url(self, client, sentinel_callback_unreachable):
        """When SENTINEL_CALLBACK_URL is not set, still approves."""
        incident = Incident.objects.create(
            root_cause="No callback configured",
            confidence=0.7,
            target_resource="test-resource",
            status=Incident.Status.NEW,
        )

        with override_settings(SENTINEL_CALLBACK_URL=None):
            resp = client.post(f"/incidents/{incident.id}/approve/")

        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["status"] == "APPROVED"

        incident.refresh_from_db()
        assert incident.status == Incident.Status.APPROVED

    def test_approve_invalid_id_type(self, client):
        """Invalid incident_id type returns 404."""
        resp = client.post("/incidents/abc/approve/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dashboard UI polish (Task 5.3)
# ---------------------------------------------------------------------------

MEASURED_ROI = {
    "runbook_class": "db_latency",
    "manual_estimate_minutes": 90.0,
    "tool_time_minutes": 0.01,
    "time_saved_minutes": 89.99,
    "breakdown": [
        {"item": "runbook_base", "label": "reference manual triage base for runbook 'db_latency'", "minutes": 90.0},
        {"item": "extra_error_signatures", "label": "distinct error signatures above first", "count": 2, "minutes": 15.0},
        {"item": "cross_service_correlation", "label": "distinct services above first", "count": 1, "minutes": 0.0},
        {"item": "verification_factor", "label": "manual verification effort", "confidence": 0.9, "minutes": 6.0},
    ],
}


def _seed_rca_incident(roi=None):
    return Incident.objects.create(
        root_cause="DB query timeout on pg_sleep",
        confidence=0.9,
        target_resource="person-api:/test/db-delay",
        status=Incident.Status.NEW,
        citations=[
            {"trace_id": "a" * 32, "span_id": "b" * 16, "source": "runbook:db_latency.md", "snippet": "504 pg_sleep exceeded"}
        ],
        dry_run_payload={
            "action": "reset_pool",
            "target_resource": "person-api:/test/db-delay",
            "params": {},
            "fallback": False,
        },
        raw_log="504 GET /test/db-delay?delay=5 database query timeout after 1.0s",
        roi=roi,
    )


@pytest.mark.django_db
class TestDashboardPolish:
    """Task 5.3 render tests: light-theme dashboard, ROI cards, approve guard."""

    def test_dashboard_200_for_empty_and_seeded(self, client):
        assert client.get("/dashboard/").status_code == 200
        _seed_rca_incident(roi=MEASURED_ROI)
        assert client.get("/dashboard/").status_code == 200

    def test_seeded_page_shows_root_cause_badge_and_roi_breakdown(self, client):
        incident = _seed_rca_incident(roi=MEASURED_ROI)

        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        content = resp.content.decode()

        assert incident.root_cause in content
        assert "person-api:/test/db-delay" in content
        # Status badge
        assert "status-NEW" in content
        # Confidence percentage
        assert "90.0%" in content
        # Expandable citations + dry-run payload
        assert "evidence &amp; dry-run" in content
        assert "db_latency.md" in content
        assert "reset_pool" in content
        # Time-saved card: label + runbook class + base + adjustments +
        # measured tool time + saved
        assert "knowledge-base baseline vs measured tool time" in content
        assert "db_latency" in content
        assert "90.00 min" in content  # runbook base
        assert "15.00 min" in content  # signature adjustment
        assert "6.00 min" in content  # verification factor
        assert "0.0100 min" in content  # measured tool time
        assert "89.99 min" in content  # time saved
        # A measured ROI incident renders no "unavailable" card
        # (the single occurrence of the phrase is the JS literal).
        assert content.count("time saved: unavailable") == 1

    def test_seeded_page_shows_total_time_saved_chip(self, client):
        _seed_rca_incident(roi=MEASURED_ROI)
        _seed_rca_incident(roi=None)

        content = client.get("/dashboard/").content.decode()
        assert "89.99" in content  # chip sums the non-null roi only

    def test_null_roi_renders_unavailable_without_error(self, client):
        _seed_rca_incident(roi=None)

        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        content = resp.content.decode()
        # The null-ROI incident renders an unavailable card in addition to
        # the JS literal: one extra occurrence of each marker per rendered
        # null-ROI incident.
        assert content.count("time saved: unavailable") == 2
        assert content.count("roi-card roi-unavailable") == 2

    def test_page_uses_no_external_assets(self, client):
        _seed_rca_incident(roi=MEASURED_ROI)
        content = client.get("/dashboard/").content.decode()
        assert "http://" not in content
        assert "https://" not in content

    def test_approve_is_guarded_by_confirm_and_no_post_without_interaction(self, client):
        incident = _seed_rca_incident(roi=MEASURED_ROI)

        content = client.get("/dashboard/").content.decode()
        # The rendered page must only POST after the user confirms.
        assert "window.confirm(" in content
        assert f'data-id="{incident.id}"' in content

        # Without any user interaction (no JS run, no confirm) nothing is
        # approved — the data endpoint still reports the incident as NEW.
        data = client.get("/dashboard/data/").json()
        row = next(i for i in data["incidents"] if i["id"] == incident.id)
        assert row["status"] == "NEW"
