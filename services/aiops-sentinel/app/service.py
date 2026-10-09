"""AI Ops Sentinel HTTP service.

Endpoints:
  GET  /health               — readiness (used by the live harness)
  POST /api/v1/analyze       — synchronous RCA: error event or raw log -> RCA + ROI
  POST /api/v1/execute       — dry-run execution acknowledgment (append-only record)
  GET  /api/v1/executions    — list recorded executions

Analyze input contract: a structured JSON error event (service, method, path,
status_code, timestamp, request_id, trace_id, span_id, error_detail) or a raw
log string. Invalid JSON / empty input -> 4xx, never 5xx. Missing trace_id ->
synthetic trace id, origin recorded in the ROI breakdown.

Analyze response shape (always 200 on processable input):
{
  "incident_id": "string | null",
  "ingested": true,
  "duplicate": false,
  "rca": {"root_cause": "...", "confidence": 0.0, "citations": [...], "dry_run_payload": {...}},
  "roi": {...}
}
"""

import json
import logging
import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.llm import QwenClient
from app.engine.correlator import IncidentContext
from app.engine.orchestrator import Orchestrator
from app.normalizer import NormalizeError, normalize_error_event, normalize_raw_log
from app.roi import compute_roi

logger = logging.getLogger("aiops-sentinel.service")
exec_log = logging.getLogger("aiops-sentinel.executions")

DEFAULT_INGEST_URL = "http://127.0.0.1:8003/api/v1/incidents"
INGEST_TIMEOUT = 10.0
ROI_UPDATE_TIMEOUT = 5.0

_STRUCTURAL_KEYS = ("service", "method", "path", "status_code", "error_detail")

_executions: list[dict] = []


def _ingest_url() -> str:
    return os.getenv("OPENWINDOW_INGEST_URL", DEFAULT_INGEST_URL)


def create_app(orchestrator: Orchestrator | None = None) -> FastAPI:
    app = FastAPI(title="AI Ops Sentinel", version="1.0.0")
    orch = orchestrator or Orchestrator(client=QwenClient())
    app.state.orchestrator = orch

    @app.get("/health")
    async def health():
        return {"status": "healthy", "service": "aiops-sentinel"}

    @app.post("/api/v1/analyze")
    async def analyze(request: Request):
        body_raw = await request.body()
        text = body_raw.decode("utf-8", errors="replace").strip()
        if not text:
            return JSONResponse(status_code=400, content={"detail": "empty analyze input"})

        structured = None
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return JSONResponse(status_code=400, content={"detail": "invalid JSON body"})
            if isinstance(parsed, dict) and any(k in parsed for k in _STRUCTURAL_KEYS):
                structured = parsed

        try:
            normalized = (
                normalize_error_event(structured)
                if structured is not None
                else normalize_raw_log(text)
            )
        except NormalizeError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        incident_ctx = IncidentContext(trace_id=normalized.trace_id, events=normalized.events)
        rca, runbook_class, runbook_meta = await orch.analyze_with_meta(incident_ctx)
        base = runbook_meta.get("manual_triage_base_minutes")

        def _roi(created_at: str | None) -> dict:
            return compute_roi(
                runbook_class,
                base,
                distinct_error_signatures=normalized.distinct_error_signatures,
                distinct_services=normalized.distinct_services,
                confidence=rca.confidence,
                error_timestamp=normalized.timestamp_iso,
                created_at=created_at,
                synthetic_trace=normalized.trace_synthetic,
            )

        roi = _roi(None)
        incident_payload = {
            "root_cause": rca.root_cause,
            "confidence": rca.confidence,
            "target_resource": normalized.target_resource,
            "citations": [c.model_dump() for c in rca.citations],
            "dry_run_payload": rca.dry_run_payload.model_dump(),
            "raw_log": normalized.raw_log,
            "roi": roi,
        }

        incident_id = None
        ingested = False
        duplicate = False
        created_at = None
        try:
            async with httpx.AsyncClient(timeout=INGEST_TIMEOUT) as client:
                resp = await client.post(_ingest_url(), json=incident_payload)
                if resp.status_code == 201:
                    data = resp.json()
                    ingested = True
                    if data.get("id") is not None:
                        incident_id = str(data["id"])
                    duplicate = bool(data.get("duplicate", False))
                    created_at = data.get("created_at")
                else:
                    logger.warning(
                        "openwindow ingest returned %s: %s", resp.status_code, resp.text[:300]
                    )
        except httpx.RequestError as exc:
            logger.warning("openwindow ingest unreachable: %s", exc)

        if ingested and created_at:
            roi = _roi(created_at)
            if incident_id:
                # Best-effort: store the measured roi alongside the created incident.
                try:
                    async with httpx.AsyncClient(timeout=ROI_UPDATE_TIMEOUT) as client:
                        await client.post(
                            f"{_ingest_url()}/{incident_id}/roi", json={"roi": roi}
                        )
                except httpx.RequestError as exc:
                    logger.warning("roi update failed (non-fatal): %s", exc)

        return JSONResponse(
            status_code=200,
            content={
                "incident_id": incident_id,
                "ingested": ingested,
                "duplicate": duplicate,
                "rca": {
                    "root_cause": rca.root_cause,
                    "confidence": rca.confidence,
                    "citations": [c.model_dump() for c in rca.citations],
                    "dry_run_payload": rca.dry_run_payload.model_dump(),
                },
                "roi": roi,
            },
        )

    @app.post("/api/v1/execute")
    async def execute(request: Request):
        body_raw = await request.body()
        try:
            data = json.loads(body_raw or b"{}")
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"detail": "invalid JSON body"})

        if not isinstance(data, dict) or not str(data.get("incident_id") or "").strip():
            return JSONResponse(status_code=400, content={"detail": "incident_id is required"})

        incident_id = str(data["incident_id"])
        record = {
            "incident_id": incident_id,
            "status": "executed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dry_run": True,
        }
        _executions.append(record)
        exec_log.info(
            json.dumps(
                {"event": "execution", **record, "dry_run_payload": data.get("dry_run_payload") or {}}
            )
        )
        return {"status": "executed", "incident_id": incident_id}

    @app.get("/api/v1/executions")
    async def executions():
        return {"executions": list(_executions)}

    return app


app = create_app()
