"""Django Ninja API for OpenWindow Incident Ingestion."""

import json
from datetime import timedelta

from django.utils import timezone
from ninja import Router

from openwindow.incidents.models import Incident

router = Router()


@router.post("/api/v1/incidents", response={201: dict, 400: dict})
def ingest_incident(request):
    """Ingest an RCA result into the Control Desk.

    - Validates required fields: root_cause, confidence, target_resource.
    - Enforces 10-minute idempotency guard on (root_cause, target_resource).
    - Returns created incident (NEW) or duplicate status (DUPLICATE_IGNORED).
    """
    # Parse JSON body manually to avoid Ninja 422 on raw dict annotation
    try:
        payload = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return 400, {"detail": "Invalid JSON body"}

    if not isinstance(payload, dict):
        return 400, {"detail": "Request body must be a JSON object"}

    # --- Input validation ---
    root_cause = payload.get("root_cause")
    confidence = payload.get("confidence")
    target_resource = payload.get("target_resource")

    if not root_cause or not isinstance(root_cause, str):
        return 400, {"detail": "root_cause is required and must be a string"}
    if confidence is None or not isinstance(confidence, (int, float)):
        return 400, {"detail": "confidence is required and must be a number (0.0–1.0)"}
    if not (0.0 <= confidence <= 1.0):
        return 400, {"detail": "confidence must be between 0.0 and 1.0"}
    if not target_resource or not isinstance(target_resource, str):
        return 400, {"detail": "target_resource is required and must be a string"}

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
        return 201, {
            "id": duplicate.id,
            "status": "DUPLICATE_IGNORED",
            "root_cause": duplicate.root_cause,
            "target_resource": duplicate.target_resource,
            "confidence": duplicate.confidence,
            "created_at": duplicate.created_at.isoformat(),
            "message": f"Duplicate incident already tracked (id={duplicate.id}, created {duplicate.created_at.isoformat()})",
        }

    # --- Create new incident ---
    incident = Incident.objects.create(
        root_cause=root_cause,
        confidence=confidence,
        citations=payload.get("citations", []),
        dry_run_payload=payload.get("dry_run_payload", {}),
        status=Incident.Status.NEW,
        target_resource=target_resource,
    )

    return 201, {
        "id": incident.id,
        "status": incident.status,
        "root_cause": incident.root_cause,
        "target_resource": incident.target_resource,
        "confidence": incident.confidence,
        "citations": incident.citations,
        "dry_run_payload": incident.dry_run_payload,
        "created_at": incident.created_at.isoformat(),
    }
