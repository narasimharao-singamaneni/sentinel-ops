"""Tests for the additive GET /dashboard/data/ endpoint (Task 5.2)."""

import pytest
from django.test import Client

from openwindow.incidents.models import Incident

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


def make_roi(class_name: str, saved: float | None) -> dict:
    return {
        "runbook_class": class_name,
        "manual_estimate_minutes": 90.0,
        "tool_time_minutes": 0.01 if saved is not None else None,
        "time_saved_minutes": saved,
        "breakdown": [
            {"item": "runbook_base", "label": "reference base", "minutes": 90.0}
        ],
    }


class TestDashboardData:
    def test_returns_counts_totals_and_incident_rows(self, client):
        Incident.objects.create(
            root_cause="DB query timeout",
            confidence=0.9,
            target_resource="person-api:/test/db-delay",
            status=Incident.Status.NEW,
            citations=[{"trace_id": "a" * 32, "span_id": "b" * 16, "source": "runbook:db_latency.md", "snippet": "504"}],
            dry_run_payload={"action": "reset_pool"},
            raw_log="504 ...",
            roi=make_roi("db_latency", 100.5),
        )
        Incident.objects.create(
            root_cause="Pool exhaustion",
            confidence=0.85,
            target_resource="person-api:/api/v1/active/workers",
            status=Incident.Status.EXECUTED,
            roi=make_roi("pool_exhaustion", 44.25),
        )
        Incident.objects.create(
            root_cause="Unmeasured incident",
            confidence=0.5,
            target_resource="person-api:unknown",
            status=Incident.Status.NEW,
            roi=None,
        )

        resp = client.get("/dashboard/data/")
        assert resp.status_code == 200
        body = resp.json()

        assert body["counts"] == {
            "total": 3,
            "new": 2,
            "approved": 0,
            "executed": 1,
            "duplicate": 0,
        }
        # Cumulative total sums only the non-null roi values.
        assert body["totals"]["time_saved_minutes"] == pytest.approx(144.75)

        rows = {row["id"]: row for row in body["incidents"]}
        assert len(rows) == 3
        for row in rows.values():
            for field in (
                "root_cause",
                "confidence",
                "status",
                "target_resource",
                "citations",
                "dry_run_payload",
                "raw_log",
                "roi",
                "created_at",
            ):
                assert field in row, f"missing {field} in {row}"
        assert any(row["roi"] is None for row in rows.values())

    def test_only_null_roi_incidents_return_zero_total_not_500(self, client):
        for _ in range(2):
            Incident.objects.create(
                root_cause="no roi",
                confidence=0.4,
                target_resource="person-api:unknown",
                status=Incident.Status.NEW,
                roi=None,
            )

        resp = client.get("/dashboard/data/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["counts"]["total"] == 2
        assert body["totals"]["time_saved_minutes"] == 0
        assert len(body["incidents"]) == 2
        assert all(row["roi"] is None for row in body["incidents"])
