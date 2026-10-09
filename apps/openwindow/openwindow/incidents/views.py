"""Django views for OpenWindow Incident Ingestion & Dashboard."""

import json
from datetime import timedelta

import httpx
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from openwindow.incidents.models import Incident


@require_GET
def health(request):
    """Readiness probe used by the live test harness/certification."""
    return JsonResponse({"status": "healthy", "service": "openwindow"})


@csrf_exempt
@require_http_methods(["POST"])
def ingest_incident(request):
    """Ingest an RCA result into the Control Desk.

    - Validates required fields: root_cause, confidence, target_resource.
    - Enforces 10-minute idempotency guard on (root_cause, target_resource).
    - Returns created incident (NEW) or duplicate status (DUPLICATE_IGNORED).
    """
    # Parse JSON body
    try:
        payload = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON body"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Request body must be a JSON object"}, status=400)

    # --- Input validation ---
    root_cause = payload.get("root_cause")
    confidence = payload.get("confidence")
    target_resource = payload.get("target_resource")

    if not root_cause or not isinstance(root_cause, str):
        return JsonResponse({"detail": "root_cause is required and must be a string"}, status=400)
    if confidence is None or not isinstance(confidence, (int, float)):
        return JsonResponse({"detail": "confidence is required and must be a number (0.0–1.0)"}, status=400)
    if not (0.0 <= confidence <= 1.0):
        return JsonResponse({"detail": "confidence must be between 0.0 and 1.0"}, status=400)
    if not target_resource or not isinstance(target_resource, str):
        return JsonResponse({"detail": "target_resource is required and must be a string"}, status=400)

    # --- 10-minute idempotency guard ---
    cutoff = timezone.now() - timedelta(minutes=10)
    duplicate = (
        Incident.objects.filter(
            root_cause=root_cause,
            target_resource=target_resource,
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
        .first()
    )

    if duplicate:
        return JsonResponse(
            {
                "id": duplicate.id,
                "status": "DUPLICATE_IGNORED",
                "root_cause": duplicate.root_cause,
                "target_resource": duplicate.target_resource,
                "confidence": duplicate.confidence,
                "created_at": duplicate.created_at.isoformat(),
                "duplicate": True,
                "message": f"Duplicate incident already tracked (id={duplicate.id}, created {duplicate.created_at.isoformat()})",
            },
            status=201,
        )

    # --- Create new incident ---
    incident = Incident.objects.create(
        root_cause=root_cause,
        confidence=confidence,
        citations=payload.get("citations", []),
        dry_run_payload=payload.get("dry_run_payload", {}),
        status=Incident.Status.NEW,
        target_resource=target_resource,
        raw_log=payload.get("raw_log"),
        roi=payload.get("roi"),
    )

    return JsonResponse(
        {
            "id": incident.id,
            "status": incident.status,
            "root_cause": incident.root_cause,
            "target_resource": incident.target_resource,
            "confidence": incident.confidence,
            "citations": incident.citations,
            "dry_run_payload": incident.dry_run_payload,
            "raw_log": incident.raw_log,
            "roi": incident.roi,
            "created_at": incident.created_at.isoformat(),
            "duplicate": False,
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
def update_incident_roi(request, incident_id):
    """Store the measured ROI for an ingested incident (baseline -> measured)."""
    try:
        incident = Incident.objects.get(pk=incident_id)
    except Incident.DoesNotExist:
        return JsonResponse({"detail": "Incident not found"}, status=404)

    try:
        payload = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON body"}, status=400)

    if not isinstance(payload, dict) or not isinstance(payload.get("roi"), dict):
        return JsonResponse({"detail": "roi object is required"}, status=400)

    incident.roi = payload["roi"]
    incident.save(update_fields=["roi"])

    return JsonResponse({"id": incident.id, "roi": incident.roi}, status=200)


# ---------------------------------------------------------------------------
# Dashboard views
# ---------------------------------------------------------------------------

def _counts(incidents) -> dict:
    return {
        "total": incidents.count(),
        "new": incidents.filter(status=Incident.Status.NEW).count(),
        "approved": incidents.filter(status=Incident.Status.APPROVED).count(),
        "executed": incidents.filter(status=Incident.Status.EXECUTED).count(),
        "duplicate": incidents.filter(status=Incident.Status.DUPLICATE_IGNORED).count(),
    }


def _incidents_payload(incidents) -> list[dict]:
    rows = []
    for inc in incidents:
        roi = inc.roi if isinstance(inc.roi, dict) else None
        rows.append(
            {
                "id": inc.id,
                "root_cause": inc.root_cause,
                "confidence": inc.confidence,
                "status": inc.status,
                "target_resource": inc.target_resource,
                "citations": inc.citations,
                "dry_run_payload": inc.dry_run_payload,
                "raw_log": inc.raw_log,
                "roi": roi,
                "created_at": inc.created_at.isoformat(),
            }
        )
    return rows


def _total_time_saved(incidents) -> float:
    """Cumulative time saved; roi = null contributes 0 (never 500)."""
    total = 0.0
    for inc in incidents:
        roi = inc.roi
        if isinstance(roi, dict):
            saved = roi.get("time_saved_minutes")
            if isinstance(saved, (int, float)):
                total += float(saved)
    return total


@require_GET
def dashboard(request):
    """Render the incident dashboard with summary counts and incident list."""
    incidents = Incident.objects.all()

    counts = _counts(incidents)

    return render(request, "incidents/dashboard.html", {
        "incidents": incidents,
        "counts": counts,
        "total_time_saved": _total_time_saved(incidents),
    })


@require_GET
def dashboard_data(request):
    """Additive JSON data endpoint polled by the dashboard.

    Shape: {counts, totals: {time_saved_minutes}, incidents: [...]}.
    Incidents with roi = null contribute 0 to the cumulative total and are
    returned with roi: null — this endpoint must never 500.
    """
    incidents = Incident.objects.all()

    return JsonResponse(
        {
            "counts": _counts(incidents),
            "totals": {"time_saved_minutes": round(_total_time_saved(incidents), 3)},
            "incidents": _incidents_payload(incidents),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def approve_incident(request, incident_id):
    """Approve an incident for execution.

    - If the incident is NEW, update status to APPROVED and call Sentinel callback.
    - If the Sentinel callback succeeds, update to EXECUTED.
    - If the incident is invalid/already processed, return 400.
    - If the incident doesn't exist, return 404.
    """
    try:
        incident = Incident.objects.get(pk=incident_id)
    except Incident.DoesNotExist:
        return JsonResponse({"detail": "Incident not found"}, status=404)

    if incident.status != Incident.Status.NEW:
        return JsonResponse(
            {
                "detail": f"Cannot approve incident with status '{incident.status}'. Only NEW incidents can be approved.",
                "id": incident.id,
                "status": incident.status,
            },
            status=400,
        )

    # Transition to APPROVED
    incident.status = Incident.Status.APPROVED
    incident.save(update_fields=["status"])

    # Attempt Sentinel execution callback (best-effort, non-blocking)
    callback_url = getattr(settings, "SENTINEL_CALLBACK_URL", None)
    if callback_url:
        try:
            resp = httpx.post(
                callback_url,
                json={
                    "incident_id": incident.id,
                    "dry_run_payload": incident.dry_run_payload,
                },
                timeout=5.0,
            )
            if resp.status_code == 200:
                incident.status = Incident.Status.EXECUTED
                incident.save(update_fields=["status"])
        except httpx.RequestError:
            # Sentinel unreachable — keep status as APPROVED for manual retry
            pass

    return JsonResponse(
        {
            "id": incident.id,
            "status": incident.status,
            "root_cause": incident.root_cause,
            "message": (
                f"Incident approved and executed" if incident.status == Incident.Status.EXECUTED
                else f"Incident approved (Sentinel callback unavailable, awaiting retry)"
            ),
        },
        status=200,
    )
