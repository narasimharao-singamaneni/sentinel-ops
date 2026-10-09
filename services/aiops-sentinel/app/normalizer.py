"""Normalize analyze inputs (structured error event JSON or raw log text)
into log events for the Orchestrator pipeline.

Contract (design D3):
- Structured events carry: service, method, path, status_code, timestamp,
  request_id, trace_id, span_id, error_detail. Timestamps are ISO-8601.
- Missing trace_id -> synthetic trace id, origin recorded for the ROI breakdown.
- target_resource: structured person-api events -> person-api:<path>;
  raw log input without a path -> person-api:unknown.
"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.engine.correlator import LogEvent


class NormalizeError(ValueError):
    """Raised when an analyze input cannot be interpreted (maps to 4xx)."""


SYNTHETIC_TRACE_PREFIX = "synthetic-"

MAX_SNIPPET_LEN = 2000
MAX_RAW_LOG_LEN = 5000


@dataclass
class NormalizedInput:
    trace_id: str
    trace_synthetic: bool
    events: list[LogEvent] = field(default_factory=list)
    target_resource: str = "person-api:unknown"
    raw_log: str = ""
    timestamp_iso: str | None = None
    distinct_error_signatures: int = 1
    distinct_services: int = 1


def _to_epoch(timestamp: str | None) -> float:
    """ISO-8601 -> unix epoch seconds; falls back to now() when absent."""
    if not timestamp:
        return time.time()
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return time.time()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _validate_iso(timestamp: str | None) -> None:
    if not timestamp:
        return
    try:
        datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        raise NormalizeError("error event timestamp must be ISO-8601") from None


def normalize_error_event(payload: object) -> NormalizedInput:
    """Normalize a structured error event (from the person-api reporter)."""
    if not isinstance(payload, dict) or not payload:
        raise NormalizeError("invalid structured error event: expected a JSON object")

    method = str(payload.get("method") or "")
    path = str(payload.get("path") or "")
    status_code = payload.get("status_code")
    detail = str(payload.get("error_detail") or "")
    service = str(payload.get("service") or "person-api")
    timestamp = payload.get("timestamp")
    _validate_iso(timestamp)

    trace_id = payload.get("trace_id")
    trace_synthetic = False
    if not trace_id or not str(trace_id).strip():
        trace_id = SYNTHETIC_TRACE_PREFIX + uuid.uuid4().hex
        trace_synthetic = True
    else:
        trace_id = str(trace_id).strip()

    message = f"{method} {path} -> {status_code}: {detail}".strip() or f"{service} error"
    message = message[:MAX_SNIPPET_LEN]

    event = LogEvent(
        trace_id=trace_id,
        message=message,
        timestamp=_to_epoch(timestamp),
        level="ERROR",
    )

    target_resource = f"person-api:{path}" if path else "person-api:unknown"
    raw_log = (
        f"service={service} {method} {path} status={status_code} "
        f"request_id={payload.get('request_id')} trace_id={trace_id} :: {detail}"
    )[:MAX_RAW_LOG_LEN]

    return NormalizedInput(
        trace_id=trace_id,
        trace_synthetic=trace_synthetic,
        events=[event],
        target_resource=target_resource,
        raw_log=raw_log,
        timestamp_iso=str(timestamp) if timestamp else None,
    )


def normalize_raw_log(text: str) -> NormalizedInput:
    """Normalize a raw log string (single-event input)."""
    if not text or not text.strip():
        raise NormalizeError("empty input: nothing to analyze")

    trace_id = SYNTHETIC_TRACE_PREFIX + uuid.uuid4().hex
    event = LogEvent(
        trace_id=trace_id,
        message=text.strip()[:MAX_SNIPPET_LEN],
        timestamp=time.time(),
        level="ERROR",
    )
    return NormalizedInput(
        trace_id=trace_id,
        trace_synthetic=True,
        events=[event],
        target_resource="person-api:unknown",
        raw_log=text.strip()[:MAX_RAW_LOG_LEN],
        timestamp_iso=None,
    )
