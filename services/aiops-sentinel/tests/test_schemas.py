import pytest
from pydantic import ValidationError

from app.schemas.rca import RCAResponse, EvidenceCitation


class TestRCAResponseValid:
    """Positive test: valid structured JSON deserializes into RCAResponse."""

    def test_valid_rca_response(self):
        data = {
            "root_cause": "Database connection pool exhausted due to too many concurrent queries",
            "confidence": 0.92,
            "citations": [
                {
                    "trace_id": "abc-123-trace",
                    "span_id": "def-456-span",
                    "snippet": "ConnectionPoolError: max connections reached",
                }
            ],
            "dry_run_payload": {"mode": "analyze"},
        }
        result = RCAResponse(**data)
        assert result.root_cause == "Database connection pool exhausted due to too many concurrent queries"
        assert result.confidence == 0.92
        assert len(result.citations) == 1
        assert result.citations[0].trace_id == "abc-123-trace"
        assert result.citations[0].source == "event"
        assert result.citations[0].snippet == "ConnectionPoolError: max connections reached"
        # legacy free-form payload accepted; known fields fill in
        assert result.dry_run_payload.action == "manual_review"
        assert result.dry_run_payload.fallback is False

    def test_valid_rca_minimal(self):
        """Minimal valid payload (dry_run_payload defaults to empty dict)."""
        data = {
            "root_cause": "Network timeout",
            "confidence": 0.5,
            "citations": [
                {"trace_id": "t1", "span_id": "s1", "snippet": "TimeoutError"}
            ],
        }
        result = RCAResponse(**data)
        assert result.confidence == 0.5
        assert result.dry_run_payload.action == "manual_review"
        assert result.dry_run_payload.fallback is False

    def test_citation_with_nullable_ids_and_source(self):
        """Citations use {trace_id|null, span_id|null, source, snippet}."""
        data = {
            "root_cause": "LLM down",
            "confidence": 0.0,
            "citations": [
                {
                    "trace_id": "trace-original-or-synthetic",
                    "span_id": None,
                    "source": "llm_unavailable",
                    "snippet": "LLM endpoint unavailable; manual review required",
                }
            ],
            "dry_run_payload": {
                "action": "manual_review",
                "target_resource": "person-api:unknown",
                "params": {},
                "fallback": True,
            },
        }
        result = RCAResponse(**data)
        assert result.citations[0].trace_id == "trace-original-or-synthetic"
        assert result.citations[0].span_id is None
        assert result.citations[0].source == "llm_unavailable"
        assert result.dry_run_payload.action == "manual_review"
        assert result.dry_run_payload.fallback is True

    def test_dry_run_payload_full_shape_round_trips(self):
        data = {
            "root_cause": "pool saturated",
            "confidence": 0.9,
            "citations": [{"trace_id": "t1", "span_id": "s1", "source": "incident_event", "snippet": "503 flood"}],
            "dry_run_payload": {
                "action": "reset_pool",
                "target_resource": "person-api-db-pool",
                "params": {"max_retries": 1},
                "fallback": False,
            },
        }
        result = RCAResponse(**data)
        assert result.dry_run_payload.action == "reset_pool"
        assert result.dry_run_payload.target_resource == "person-api-db-pool"
        assert result.dry_run_payload.params == {"max_retries": 1}
        assert result.dry_run_payload.fallback is False


class TestRCAResponseInvalid:
    """Negative test: missing citations or invalid confidence fails with ValidationError."""

    def test_missing_citations_fails(self):
        data = {
            "root_cause": "Something broke",
            "confidence": 0.8,
        }
        with pytest.raises(ValidationError) as exc_info:
            RCAResponse(**data)
        assert "citations" in str(exc_info.value)

    def test_confidence_above_one_fails(self):
        data = {
            "root_cause": "Something broke",
            "confidence": 1.5,
            "citations": [
                {"trace_id": "t1", "span_id": "s1", "snippet": "error"}
            ],
        }
        with pytest.raises(ValidationError) as exc_info:
            RCAResponse(**data)
        assert "confidence" in str(exc_info.value).lower()

    def test_confidence_below_zero_fails(self):
        data = {
            "root_cause": "Something broke",
            "confidence": -0.1,
            "citations": [
                {"trace_id": "t1", "span_id": "s1", "snippet": "error"}
            ],
        }
        with pytest.raises(ValidationError) as exc_info:
            RCAResponse(**data)
        assert "confidence" in str(exc_info.value).lower()

    def test_empty_citations_list_fails(self):
        data = {
            "root_cause": "Something broke",
            "confidence": 0.9,
            "citations": [],
        }
        with pytest.raises(ValidationError) as exc_info:
            RCAResponse(**data)
        assert "citations" in str(exc_info.value)
