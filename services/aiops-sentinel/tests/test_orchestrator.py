"""Tests for the Grounded Prompt Orchestrator & Remediation Whitelist."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.llm import QwenClient
from app.engine.correlator import IncidentContext, LogEvent
from app.engine.orchestrator import Orchestrator, ALLOWED_ACTIONS, _build_prompt
from app.engine.retriever import RunbookRetriever
from app.schemas.rca import RCAResponse


@pytest.fixture
def qwen_client():
    return QwenClient(base_url="http://localhost:9999/v1", model="qwen2.5-7b", timeout=0.5)


@pytest.fixture
def retriever(tmp_path):
    """Create a retriever with test runbooks."""
    runbook_dir = tmp_path / "runbooks"
    runbook_dir.mkdir()
    (runbook_dir / "db_latency.md").write_text("# Database Latency\n\nRemediation: reset_pool, flush_cache")
    (runbook_dir / "default_fallback.md").write_text("# Default Fallback\n\nManual review required.")
    return RunbookRetriever(runbook_dir=runbook_dir)


@pytest.fixture
def incident():
    return IncidentContext(
        trace_id="trace-abc-123",
        events=[
            LogEvent(trace_id="trace-abc-123", message="Connection pool exhausted", level="ERROR", timestamp=1000.0),
            LogEvent(trace_id="trace-abc-123", message="Query timeout after 5s", level="ERROR", timestamp=1001.0),
        ],
    )


def _mock_llm(client, content: str):
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    return patch.object(client._client.chat.completions, "create", new_callable=AsyncMock, return_value=mock_completion)


def test_build_prompt_includes_trace_and_runbook():
    """Verify prompt construction includes runbook context and incident evidence."""
    incident = IncidentContext(
        trace_id="trace-xyz",
        events=[
            LogEvent(trace_id="trace-xyz", message="Pool error", level="ERROR", timestamp=1000.0),
        ],
    )
    prompt = _build_prompt("runbook content here", incident)
    assert "runbook content here" in prompt
    assert "trace-xyz" in prompt
    assert "Pool error" in prompt
    assert "confidence must be between 0.0 and 1.0" in prompt


def test_allowed_actions_defined():
    """Verify the whitelist contains expected allowed actions."""
    assert "flush_cache" in ALLOWED_ACTIONS
    assert "reset_pool" in ALLOWED_ACTIONS
    assert "drop_table" not in ALLOWED_ACTIONS


@pytest.mark.asyncio
async def test_orchestrator_happy_path(qwen_client, retriever, incident):
    """Full pipeline: retrieve runbook -> LLM -> valid response."""
    with _mock_llm(
        qwen_client,
        (
            '{"root_cause": "database connection pool exhausted", '
            '"confidence": 0.92, '
            f'"citations": [{{"trace_id": "{incident.trace_id}", "span_id": "span-1", "source": "incident_event", "snippet": "Connection pool exhausted"}}], '
            '"dry_run_payload": {"action": "reset_pool", "target_resource": "person-api-db-pool", "params": {}, "fallback": false}}'
        ),
    ):
        orch = Orchestrator(client=qwen_client, retriever=retriever)
        result = await orch.analyze(incident)

    assert isinstance(result, RCAResponse)
    assert result.root_cause == "database connection pool exhausted"
    assert 0.0 <= result.confidence <= 1.0
    assert len(result.citations) >= 1
    assert result.citations[0].trace_id == incident.trace_id
    assert result.citations[0].source == "incident_event"
    assert result.dry_run_payload.action == "reset_pool"
    assert result.dry_run_payload.fallback is False


@pytest.mark.asyncio
async def test_orchestrator_blocks_unauthorized_action(qwen_client, retriever, incident):
    """LLM proposes `drop_table` — replaced with manual_review + fallback, never returned."""
    with _mock_llm(
        qwen_client,
        (
            '{"root_cause": "rogue query", '
            '"confidence": 0.85, '
            '"citations": [{"trace_id": "trace-abc-123", "span_id": "span-2", "source": "incident_event", "snippet": "DROP TABLE event"}], '
            '"dry_run_payload": {"action": "drop_table", "target_resource": "person-api", "params": {}, "fallback": false}}'
        ),
    ):
        orch = Orchestrator(client=qwen_client, retriever=retriever)
        result = await orch.analyze(incident)

    assert isinstance(result, RCAResponse)
    assert result.root_cause == "rogue query"
    assert result.dry_run_payload.action == "manual_review"
    assert result.dry_run_payload.fallback is True
    dumped = str(result.dry_run_payload)
    assert "drop_table" not in dumped


@pytest.mark.asyncio
async def test_orchestrator_allows_whitelisted_action(qwen_client, retriever, incident):
    """Allowed actions like `flush_cache` pass through unchanged."""
    with _mock_llm(
        qwen_client,
        (
            '{"root_cause": "stale cache entries", '
            '"confidence": 0.88, '
            '"citations": [{"trace_id": "trace-abc-123", "span_id": "span-3", "source": "incident_event", "snippet": "cache miss"}], '
            '"dry_run_payload": {"action": "flush_cache", "target_resource": "person-api-cache", "params": {}, "fallback": false}}'
        ),
    ):
        orch = Orchestrator(client=qwen_client, retriever=retriever)
        result = await orch.analyze(incident)

    assert isinstance(result, RCAResponse)
    assert result.dry_run_payload.action == "flush_cache"
    assert result.dry_run_payload.fallback is False


@pytest.mark.asyncio
async def test_orchestrator_llm_unavailable_fallback(qwen_client, retriever, incident):
    """When LLM is unreachable, returns manual_review_required fallback."""
    from httpx import ConnectError

    with patch.object(
        qwen_client._client.chat.completions, "create", new_callable=AsyncMock,
        side_effect=ConnectError("Connection refused"),
    ):
        orch = Orchestrator(client=qwen_client, retriever=retriever)
        result = await orch.analyze(incident)

    assert isinstance(result, RCAResponse)
    assert result.root_cause == "manual_review_required"
    assert result.confidence == 0.0
    assert result.citations[0].trace_id == incident.trace_id
    assert result.citations[0].span_id is None
    assert result.citations[0].source == "llm_unavailable"
    assert "LLM endpoint unavailable" in result.citations[0].snippet
    assert result.dry_run_payload.fallback is True
    assert result.dry_run_payload.action == "manual_review"


@pytest.mark.asyncio
async def test_orchestrator_malformed_llm_json_falls_back(qwen_client, retriever, incident):
    """Malformed LLM JSON (no parseable RCA) is treated as LLM unavailable — fallback, not 5xx."""
    with _mock_llm(qwen_client, "Sorry, I can't analyze that. Here are some thoughts: ..."):
        orch = Orchestrator(client=qwen_client, retriever=retriever)
        result = await orch.analyze(incident)

    assert result.root_cause == "manual_review_required"
    assert result.confidence == 0.0
    assert result.dry_run_payload.fallback is True
    assert result.citations[0].source == "llm_unavailable"
    assert result.citations[0].trace_id == incident.trace_id


@pytest.mark.asyncio
async def test_orchestrator_schema_invalid_llm_output_falls_back(qwen_client, retriever, incident):
    """Schema-invalid LLM JSON (missing citations) also falls back."""
    with _mock_llm(
        qwen_client,
        '{"root_cause": "no citations provided", "confidence": 0.7}',
    ):
        orch = Orchestrator(client=qwen_client, retriever=retriever)
        result = await orch.analyze(incident)

    assert result.root_cause == "manual_review_required"
    assert result.dry_run_payload.fallback is True


@pytest.mark.asyncio
async def test_orchestrator_empty_payload_keeps_defaults(qwen_client, retriever, incident):
    """An empty dry_run_payload validates with safe defaults (manual_review, no fallback flag)."""
    with _mock_llm(
        qwen_client,
        (
            '{"root_cause": "normal operation", '
            '"confidence": 0.95, '
            '"citations": [{"trace_id": "trace-abc-123", "span_id": "span-5", "source": "incident_event", "snippet": "all clear"}], '
            '"dry_run_payload": {}}'
        ),
    ):
        orch = Orchestrator(client=qwen_client, retriever=retriever)
        result = await orch.analyze(incident)

    assert result.dry_run_payload.action == "manual_review"
    assert result.dry_run_payload.fallback is False


@pytest.mark.asyncio
async def test_analyze_with_meta_returns_runbook_identity(qwen_client, retriever, incident):
    """analyze_with_meta exposes the matched runbook class + metadata for the ROI model."""
    with _mock_llm(
        qwen_client,
        (
            '{"root_cause": "db latency", "confidence": 0.9, '
            '"citations": [{"trace_id": "trace-abc-123", "span_id": "s", "source": "incident_event", "snippet": "x"}], '
            '"dry_run_payload": {"action": "reset_pool", "target_resource": "t", "params": {}, "fallback": false}}'
        ),
    ):
        orch = Orchestrator(client=qwen_client, retriever=retriever)
        rca, runbook_class, meta = await orch.analyze_with_meta(incident)

    assert isinstance(rca, RCAResponse)
    assert runbook_class == "db_latency.md"
    assert isinstance(meta, dict)
