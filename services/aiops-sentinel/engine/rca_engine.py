from pydantic import ValidationError

from app.core.llm import QwenClient, LLMUnavailableException
from app.schemas.rca import RCAResponse, EvidenceCitation


def _build_prompt(enriched_log: dict) -> str:
    """Build the RCA prompt for Qwen with strict JSON output instructions."""
    return f"""You are an expert SRE (Site Reliability Engineer).

Analyze the log and provide structured root cause analysis.

Input:
{enriched_log}

Return ONLY valid JSON in the following format:

{{
  "root_cause": "...",
  "confidence": 0.92,
  "citations": [
    {{
      "trace_id": "...",
      "span_id": "...",
      "snippet": "..."
    }}
  ],
  "dry_run_payload": {{}}
}}

Rules:
- Be concise
- Use context information if available
- Do NOT include explanations outside JSON
- confidence must be between 0.0 and 1.0
- Include at least one citation
"""


async def analyze_event(enriched_log: dict, client: QwenClient | None = None) -> RCAResponse:
    """Analyze an enriched log event and return a structured RCAResponse.

    If the LLM is unavailable, returns a fallback response requiring manual review.
    """
    if client is None:
        client = QwenClient()

    prompt = _build_prompt(enriched_log)

    try:
        raw_dict = await client.generate_structured(prompt)
        return RCAResponse(**raw_dict)
    except (LLMUnavailableException, ValidationError, ValueError, KeyError, TypeError):
        return RCAResponse(
            root_cause="manual_review_required",
            confidence=0.0,
            citations=[
                EvidenceCitation(
                    trace_id="N/A",
                    span_id=None,
                    source="llm_unavailable",
                    snippet="LLM endpoint unavailable; manual review required",
                )
            ],
            dry_run_payload={"action": "manual_review", "target_resource": "unknown", "params": {}, "fallback": True},
        )
