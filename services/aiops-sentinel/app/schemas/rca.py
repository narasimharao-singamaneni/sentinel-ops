from pydantic import BaseModel, Field, model_validator


class EvidenceCitation(BaseModel):
    """A citation tying RCA conclusions to specific evidence.

    Shape: {"trace_id": "string | null", "span_id": "string | null",
            "source": "string", "snippet": "string"}.
    """

    trace_id: str | None = Field(default=None, description="Identifier of the traced evidence (or synthetic id)")
    span_id: str | None = Field(default=None, description="Optional span identifier, null when unavailable")
    source: str = Field(default="event", min_length=1, description="Where the evidence came from (e.g. 'llm_unavailable')")
    snippet: str = Field(..., min_length=1, description="Relevant log snippet or evidence")


class DryRunPayload(BaseModel):
    """Safe, dry-run remediation payload.

    Shape: {"action": "string", "target_resource": "string",
            "params": {}, "fallback": false}
    Non-whitelisted actions are replaced by the orchestrator with
    'manual_review' and fallback=true.
    """

    action: str = Field(default="manual_review", min_length=1)
    target_resource: str = Field(default="unknown", min_length=1)
    params: dict = Field(default_factory=dict)
    fallback: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_plain_dict(cls, data):
        """Accept legacy free-form dicts by keeping only known fields."""
        if isinstance(data, dict):
            known = {k: v for k, v in data.items() if k in ("action", "target_resource", "params", "fallback")}
            return known
        return data


class RCAResponse(BaseModel):
    """Strictly validated structured RCA response from the LLM."""

    root_cause: str = Field(..., min_length=1, description="Root cause explanation")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    citations: list[EvidenceCitation] = Field(..., min_length=1, description="At least one evidence citation required")
    dry_run_payload: DryRunPayload = Field(default_factory=DryRunPayload)

    @model_validator(mode="before")
    @classmethod
    def validate_confidence_range(cls, data):
        """Reject confidence values outside 0.0-1.0 before Pydantic's built-in check."""
        if isinstance(data, dict):
            conf = data.get("confidence")
            if isinstance(conf, (int, float)) and (conf < 0.0 or conf > 1.0):
                raise ValueError(f"confidence must be between 0.0 and 1.0, got {conf}")
        return data
