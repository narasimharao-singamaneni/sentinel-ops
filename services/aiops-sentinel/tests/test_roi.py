"""Tests for the exact, auditable ROI model (app/roi.py)."""

import pytest

from app.roi import (
    FALLBACK_BASE_MINUTES,
    MAX_ESTIMATE_MINUTES,
    MIN_ESTIMATE_MINUTES,
    compute_manual_estimate,
    compute_roi,
)


class TestManualEstimateFormula:
    def test_exact_formula_single_signature_single_service(self):
        estimate, breakdown = compute_manual_estimate(
            runbook_class="db_latency.md",
            base_minutes=90,
            distinct_error_signatures=1,
            distinct_services=1,
            confidence=0.85,
        )
        # 90 + 0 + 0 + 60*(1-0.85) = 99
        assert estimate == 99.0
        items = {b["item"]: b for b in breakdown}
        assert items["runbook_base"]["minutes"] == 90
        assert items["extra_error_signatures"]["count"] == 1
        assert items["extra_error_signatures"]["minutes"] == 0
        assert items["cross_service_correlation"]["count"] == 1
        assert items["cross_service_correlation"]["minutes"] == 0
        assert items["verification_factor"]["minutes"] == pytest.approx(9.0)

    def test_signature_adjustment_uses_count(self):
        estimate, _ = compute_manual_estimate(
            "db_latency.md", 90, distinct_error_signatures=3, distinct_services=1, confidence=1.0,
        )
        # 90 + 15*(3-1) + 0 + 0 = 120
        assert estimate == 120.0

    def test_service_adjustment(self):
        estimate, _ = compute_manual_estimate(
            "db_latency.md", 90, distinct_error_signatures=1, distinct_services=4, confidence=1.0,
        )
        # 90 + 0 + 20*(4-1) + 0 = 150
        assert estimate == 150.0

    def test_different_classes_estimate_differently(self):
        pool, _ = compute_manual_estimate("pool_exhaustion.md", 45, 1, 1, 0.8)
        fallback, _ = compute_manual_estimate("default_fallback.md", 180, 1, 1, 0.8)
        assert fallback > pool
        assert fallback - pool == 135

    def test_clamped_to_bounds(self):
        low, _ = compute_manual_estimate("x.md", 0, 1, 1, 1.0)
        assert low == MIN_ESTIMATE_MINUTES
        high, _ = compute_manual_estimate("x.md", FALLBACK_BASE_MINUTES, 20, 20, 0.0)
        assert high == MAX_ESTIMATE_MINUTES


class TestMissingMetadataFallback:
    def test_missing_base_uses_180_and_records_in_breakdown(self):
        estimate, breakdown = compute_manual_estimate(
            "mystery.md", None, 1, 1, 1.0,
        )
        assert estimate == 180.0
        base_item = next(b for b in breakdown if b["item"] == "runbook_base")
        assert base_item["minutes"] == 180
        assert "no manual_triage_base_minutes" in base_item["label"]


class TestRoiObject:
    def test_full_shape_with_measured_tool_time(self):
        roi = compute_roi(
            runbook_class="db_latency.md",
            base_minutes=90,
            confidence=0.85,
            error_timestamp="2026-08-14T10:00:00Z",
            created_at="2026-08-14T10:05:00Z",
        )
        assert set(("runbook_class", "manual_estimate_minutes", "tool_time_minutes", "time_saved_minutes", "breakdown")) <= set(roi)
        assert roi["runbook_class"] == "db_latency.md"
        assert roi["manual_estimate_minutes"] == 99.0
        assert roi["tool_time_minutes"] == pytest.approx(5.0)
        assert roi["time_saved_minutes"] == pytest.approx(94.0)
        for item in roi["breakdown"]:
            assert "item" in item and "label" in item and "minutes" in item

    def test_failed_ingestion_nulls_measured_fields(self):
        roi = compute_roi(
            runbook_class="db_latency.md",
            base_minutes=90,
            confidence=0.85,
            error_timestamp="2026-08-14T10:00:00Z",
            created_at=None,
        )
        assert roi["tool_time_minutes"] is None
        assert roi["time_saved_minutes"] is None
        assert roi["manual_estimate_minutes"] == 99.0
        assert roi["breakdown"]

    def test_null_created_at_with_no_timestamps(self):
        roi = compute_roi(runbook_class="default_fallback.md", base_minutes=None, confidence=0.5)
        assert roi["tool_time_minutes"] is None
        assert roi["time_saved_minutes"] is None

    def test_synthetic_trace_recorded_in_breakdown(self):
        roi = compute_roi(
            runbook_class="pool_exhaustion.md",
            base_minutes=45,
            confidence=0.9,
            error_timestamp="2026-08-14T10:00:00Z",
            created_at="2026-08-14T10:01:00Z",
            synthetic_trace=True,
        )
        items = {b["item"] for b in roi["breakdown"]}
        assert "synthetic_trace_id" in items
        assert roi["tool_time_minutes"] == pytest.approx(1.0)

    def test_invalid_timestamps_degrade_to_null_not_error(self):
        roi = compute_roi(
            runbook_class="db_latency.md",
            base_minutes=90,
            confidence=1.0,
            error_timestamp="not-a-date",
            created_at="2026-08-14T10:05:00Z",
        )
        assert roi["tool_time_minutes"] is None
        assert roi["time_saved_minutes"] is None
