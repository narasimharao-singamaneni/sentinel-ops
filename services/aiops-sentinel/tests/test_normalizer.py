"""Tests for the error-event normalizer (D3 contract)."""

import pytest

from app.normalizer import (
    NormalizeError,
    SYNTHETIC_TRACE_PREFIX,
    normalize_error_event,
    normalize_raw_log,
)


class TestNormalizeStructuredEvent:
    def test_full_event_normalizes_with_real_trace(self):
        event = {
            "service": "person-api",
            "method": "GET",
            "path": "/api/v1/active/workers",
            "status_code": 503,
            "timestamp": "2026-08-14T10:00:00Z",
            "request_id": "req-123",
            "trace_id": "trace-real-1",
            "span_id": "span-1",
            "error_detail": "Too many concurrent requests. Please try again shortly.",
        }
        normalized = normalize_error_event(event)
        assert normalized.trace_id == "trace-real-1"
        assert normalized.trace_synthetic is False
        assert normalized.target_resource == "person-api:/api/v1/active/workers"
        assert len(normalized.events) == 1
        assert normalized.events[0].level == "ERROR"
        assert normalized.events[0].trace_id == "trace-real-1"
        assert "503" in normalized.events[0].message
        assert "Too many concurrent requests" in normalized.events[0].message
        assert normalized.timestamp_iso == "2026-08-14T10:00:00Z"
        assert "req-123" in normalized.raw_log
        assert normalized.distinct_error_signatures == 1
        assert normalized.distinct_services == 1

    def test_missing_trace_id_uses_synthetic_recorded_origin(self):
        event = {
            "service": "person-api",
            "method": "POST",
            "path": "/api/v1/active/workers",
            "status_code": 500,
            "timestamp": "2026-08-14T10:00:00+00:00",
            "request_id": "req-9",
            "trace_id": None,
            "span_id": None,
            "error_detail": "boom",
        }
        normalized = normalize_error_event(event)
        assert normalized.trace_synthetic is True
        assert normalized.trace_id.startswith(SYNTHETIC_TRACE_PREFIX)
        assert normalized.events[0].trace_id == normalized.trace_id

    def test_event_without_path_still_structured(self):
        event = {
            "service": "person-api",
            "method": "GET",
            "path": "",
            "status_code": 500,
            "timestamp": "2026-08-14T10:00:00Z",
            "error_detail": "x",
        }
        normalized = normalize_error_event(event)
        assert normalized.target_resource == "person-api:unknown"


class TestNormalizeErrors:
    def test_non_dict_payload_rejected(self):
        with pytest.raises(NormalizeError):
            normalize_error_event([1, 2, 3])

    def test_empty_dict_rejected(self):
        with pytest.raises(NormalizeError):
            normalize_error_event({})

    def test_non_iso_timestamp_rejected(self):
        with pytest.raises(NormalizeError):
            normalize_error_event(
                {"service": "person-api", "method": "GET", "path": "/x", "status_code": 500,
                 "timestamp": "yesterday at 5", "error_detail": "e"}
            )


class TestNormalizeRawLog:
    def test_raw_log_becomes_single_event(self):
        normalized = normalize_raw_log("2026-08-14 ERROR db: TimeoutError query exceeded 30s")
        assert normalized.trace_synthetic is True
        assert normalized.trace_id.startswith(SYNTHETIC_TRACE_PREFIX)
        assert normalized.target_resource == "person-api:unknown"
        assert len(normalized.events) == 1
        assert "TimeoutError" in normalized.events[0].message
        assert normalized.raw_log.startswith("2026-08-14 ERROR")

    def test_empty_raw_log_rejected(self):
        with pytest.raises(NormalizeError):
            normalize_raw_log("   ")
