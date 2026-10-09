"""Grounded Prompt Orchestrator & Remediation Whitelist.

Combines Correlator + Retriever + Qwen LLM into a unified RCA pipeline.
Validates all remediation actions against an allow-list before returning results.
"""

from pydantic import ValidationError

from app.core.llm import QwenClient, LLMUnavailableException
from app.engine.correlator import IncidentContext
from app.engine.retriever import RunbookRetriever
from app.schemas.rca import RCAResponse, DryRunPayload, EvidenceCitation

# Allowed remediation actions — anything else is blocked
ALLOWED_ACTIONS = frozenset({"flush_cache", "reset_pool"})


def _build_prompt(runbook_context: str, incident: IncidentContext) -> str:
    """Build a grounded RCA prompt incorporating runbook knowledge and trace evidence."""
    return f"""You are an expert SRE (Site Reliability Engineer).

Analyze the incident and provide structured root cause analysis.

Runbook Context:
{runbook_context}

Incident Evidence:
- Trace ID: {incident.trace_id}
- Error count: {incident.error_count}
- Snippet: {incident.snippet}

Return ONLY valid JSON in the following format:

{{
  "root_cause": "...",
  "confidence": 0.92,
  "citations": [
    {{
      "trace_id": "{incident.trace_id}",
      "span_id": "...",
      "source": "incident_event",
      "snippet": "..."
    }}
  ],
  "dry_run_payload": {{
    "action": "reset_pool",
    "target_resource": "...",
    "params": {{}},
    "fallback": false
  }}
}}

Rules:
- Be concise and grounded in the runbook context
- Use the trace evidence to cite specific error details
- confidence must be between 0.0 and 1.0
- Include at least one citation referencing the provided trace_id
- Only use remediation actions from the runbook (e.g., flush_cache, reset_pool)
"""


class Orchestrator:
    """Unified RCA pipeline: Correlator -> Retriever -> LLM -> Whitelist validation."""

    def __init__(
        self,
        client: QwenClient | None = None,
        retriever: RunbookRetriever | None = None,
    ):
        self.client = client or QwenClient()
        self.retriever = retriever or RunbookRetriever()

    def _retrieve(self, incident: IncidentContext) -> tuple[str, str, dict]:
        return self.retriever.retrieve_with_meta(incident.snippet)

    async def analyze_with_meta(self, incident: IncidentContext) -> tuple[RCAResponse, str, dict]:
        """Run the full grounded RCA pipeline; return (rca, runbook_class, runbook_metadata)."""
        # Step 1: Retrieve relevant runbook context (content, filename, metadata)
        runbook_context, runbook_class, runbook_meta = self._retrieve(incident)

        # Step 2: Build grounded prompt
        prompt = _build_prompt(runbook_context, incident)

        try:
            # Step 3: Call LLM for structured output
            raw_dict = await self.client.generate_structured(prompt)
            rca = RCAResponse(**raw_dict)
        except (LLMUnavailableException, ValidationError, ValueError, KeyError, TypeError):
            # LLM down OR malformed / schema-invalid LLM output → fall back, never 5xx
            return self._fallback_response(incident), runbook_class, runbook_meta

        # Step 4: Validate actions against whitelist
        return self._validate_actions(rca), runbook_class, runbook_meta

    async def analyze(self, incident: IncidentContext) -> RCAResponse:
        """Run the full grounded RCA pipeline for a single incident context.

        1. Retrieves relevant runbook context based on the incident snippet.
        2. Builds a grounded prompt with runbook knowledge and trace evidence.
        3. Calls the LLM for structured RCA output.
        4. Validates dry_run_payload actions against the whitelist.
        5. Returns a fully validated RCAResponse.

        On LLM failure (or malformed LLM output), returns a manual-review fallback response.
        """
        rca, _, _ = await self.analyze_with_meta(incident)
        return rca

    def _validate_actions(self, rca: RCAResponse) -> RCAResponse:
        """Validate the dry-run action against the allowed whitelist.

        If the LLM proposes an action not in the whitelist, the action is
        replaced with `manual_review`, `fallback` is set to true, and the
        unauthorized action value is never returned.
        """
        payload = rca.dry_run_payload
        if payload.action not in ALLOWED_ACTIONS and payload.action != "manual_review":
            sanitized = DryRunPayload(
                action="manual_review",
                target_resource=payload.target_resource,
                params={},
                fallback=True,
            )
            return RCAResponse(
                root_cause=rca.root_cause,
                confidence=rca.confidence,
                citations=rca.citations,
                dry_run_payload=sanitized,
            )
        return rca

    def _fallback_response(self, incident: IncidentContext) -> RCAResponse:
        """Return a manual-review fallback when the LLM is unavailable or unparseable."""
        return RCAResponse(
            root_cause="manual_review_required",
            confidence=0.0,
            citations=[
                EvidenceCitation(
                    trace_id=incident.trace_id,
                    span_id=None,
                    source="llm_unavailable",
                    snippet="LLM endpoint unavailable; manual review required",
                )
            ],
            dry_run_payload=DryRunPayload(
                action="manual_review",
                target_resource="unknown",
                params={},
                fallback=True,
            ),
        )
