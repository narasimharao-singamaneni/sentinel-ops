"""URL patterns for the incidents app."""

from django.urls import path

from openwindow.incidents.views import (
    approve_incident,
    dashboard,
    dashboard_data,
    health,
    ingest_incident,
    update_incident_roi,
)

urlpatterns = [
    # Readiness
    path("health/", health, name="health"),

    # Dashboard
    path("dashboard/", dashboard, name="dashboard"),
    path("dashboard/data/", dashboard_data, name="dashboard_data"),
    path("", dashboard, name="dashboard_root"),

    # API: Ingest incident
    path("api/v1/incidents", ingest_incident, name="ingest_incident"),

    # API: Update measured ROI for an ingested incident
    path("api/v1/incidents/<int:incident_id>/roi", update_incident_roi, name="update_incident_roi"),

    # API: Approve incident
    path("incidents/<int:incident_id>/approve/", approve_incident, name="approve_incident"),
]
