from unittest.mock import AsyncMock, patch

import pytest
from httpx import ConnectError

from app.core.llm import QwenClient, LLMUnavailableException
from app.schemas.rca import RCAResponse
from engine.rca_engine import analyze_event


class TestAnalyzeEventHappyPath:
    """Positive test: happy-path mock returns structured RCAResponse with citations."""

    @pytest.mark.asyncio
    async def test_analyze_event_returns_rca_response(self):
        enriched_log = {
            "message": "ConnectionPoolError: max connections reached",
            "metadata": {"source": "person-api", "trace_id": "abc-123"},
        }
        client = QwenClient(base_url="http://localhost:9999/v1", model="qwen", timeout=0.5)

        mock_structured = AsyncMock(
            return_value={
                "root_cause": "Database connection pool exhausted",
                "confidence": 0.95,
                "citations": [
                    {
                        "trace_id": "abc-123",
                        "span_id": "span-456",
                        "snippet": "ConnectionPoolError: max connections reached",
                    }
                ],
                "dry_run_payload": {"mode": "analyze"},
            }
        )
        with patch.object(client, "generate_structured", mock_structured):
            result = await analyze_event(enriched_log, client)

        assert isinstance(result, RCAResponse)
        assert result.root_cause == "Database connection pool exhausted"
        assert result.confidence == 0.95
        assert len(result.citations) == 1
        assert result.citations[0].trace_id == "abc-123"
        assert result.dry_run_payload.action == "manual_review"
        assert result.dry_run_payload.fallback is False


class TestAnalyzeEventFallback:
    """Negative test: on LLMUnavailableException, falls back to manual_review_required."""

    @pytest.mark.asyncio
    async def test_analyze_event_fallback_on_llm_unavailable(self):
        enriched_log = {
            "message": "Some error",
            "metadata": {"source": "unknown"},
        }
        client = QwenClient(base_url="http://localhost:9999/v1", model="qwen", timeout=0.5)

        error_mock = AsyncMock(side_effect=LLMUnavailableException("Connection refused"))
        with patch.object(client, "generate_structured", error_mock):
            result = await analyze_event(enriched_log, client)

        assert isinstance(result, RCAResponse)
        assert result.root_cause == "manual_review_required"
        assert result.confidence == 0.0
        assert len(result.citations) == 1
        assert "manual review" in result.citations[0].snippet.lower()
        assert result.dry_run_payload.fallback is True
        assert result.dry_run_payload.action == "manual_review"
