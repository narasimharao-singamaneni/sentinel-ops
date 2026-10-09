"""Per-incident ROI: exact, runbook-metadata-driven, auditable model.

manual_estimate_minutes = clamp(
  runbook_base_minutes
  + 15 * max(0, distinct_error_signatures - 1)
  + 20 * max(0, distinct_services - 1)
  + 60 * (1 - confidence),
  5, 480
)

tool_time_minutes = (openwindow created_at - error event timestamp) / 60 s
time_saved_minutes = manual_estimate_minutes - tool_time_minutes

If ingestion fails or no usable created_at is available, both measured
fields are None. There is NO single hardcoded universal manual baseline:
bases come from runbook `manual_triage_base_minutes` frontmatter, falling
back to FALLBACK_BASE_MINUTES (recorded in the breakdown) when missing.
"""

from __future__ import annotations

from datetime import datetime, timezone

FALLBACK_BASE_MINUTES = 180
MIN_ESTIMATE_MINUTES = 5.0
MAX_ESTIMATE_MINUTES = 480.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _parse_iso(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def compute_manual_estimate(
    runbook_class: str,
    base_minutes: int | float | None,
    distinct_error_signatures: int,
    distinct_services: int,
    confidence: float | None,
) -> tuple[float, list[dict]]:
    """Return (estimate_minutes, breakdown_items) for the exact formula."""
    breakdown: list[dict] = []

    if base_minutes is None:
        base = float(FALLBACK_BASE_MINUTES)
        breakdown.append(
            {
                "item": "runbook_base",
                "label": f"fallback reference base (runbook '{runbook_class}' has no manual_triage_base_minutes)",
                "minutes": base,
            }
        )
    else:
        base = float(base_minutes)
        breakdown.append(
            {
                "item": "runbook_base",
                "label": f"reference manual triage base for runbook '{runbook_class}'",
                "minutes": base,
            }
        )

    signatures = max(0, int(distinct_error_signatures))
    extra_signatures = max(0, signatures - 1)
    breakdown.append(
        {
            "item": "extra_error_signatures",
            "label": "distinct error signatures above first",
            "count": signatures,
            "minutes": 15 * extra_signatures,
        }
    )

    services = max(0, int(distinct_services))
    extra_services = max(0, services - 1)
    breakdown.append(
        {
            "item": "cross_service_correlation",
            "label": "distinct services above first",
            "count": services,
            "minutes": 20 * extra_services,
        }
    )

    conf = _clamp(float(confidence if confidence is not None else 0.0), 0.0, 1.0)
    breakdown.append(
        {
            "item": "verification_factor",
            "label": "manual verification effort (1 - confidence) * 60 minutes",
            "confidence": round(conf, 4),
            "minutes": round(60.0 * (1.0 - conf), 3),
        }
    )

    estimate = _clamp(
        base + 15 * extra_signatures + 20 * extra_services + 60.0 * (1.0 - conf),
        MIN_ESTIMATE_MINUTES,
        MAX_ESTIMATE_MINUTES,
    )
    return round(estimate, 3), breakdown


def compute_roi(
    runbook_class: str,
    base_minutes: int | float | None,
    *,
    distinct_error_signatures: int = 1,
    distinct_services: int = 1,
    confidence: float | None = None,
    error_timestamp: str | None = None,
    created_at: str | None = None,
    synthetic_trace: bool = False,
) -> dict:
    """Build the full roi object (min shape: runbook_class, manual_estimate_minutes,
    tool_time_minutes, time_saved_minutes, breakdown)."""
    estimate, breakdown = compute_manual_estimate(
        runbook_class,
        base_minutes,
        distinct_error_signatures,
        distinct_services,
        confidence,
    )

    if synthetic_trace:
        breakdown.append(
            {
                "item": "synthetic_trace_id",
                "label": "event had no trace_id; synthetic trace id generated",
                "minutes": 0,
            }
        )

    tool_time: float | None = None
    event_dt = _parse_iso(error_timestamp)
    created_dt = _parse_iso(created_at)
    if event_dt is not None and created_dt is not None:
        tool_time = round((created_dt - event_dt).total_seconds() / 60.0, 4)

    time_saved = round(estimate - tool_time, 3) if tool_time is not None else None

    return {
        "runbook_class": runbook_class,
        "manual_estimate_minutes": estimate,
        "tool_time_minutes": tool_time,
        "time_saved_minutes": time_saved,
        "breakdown": breakdown,
    }
