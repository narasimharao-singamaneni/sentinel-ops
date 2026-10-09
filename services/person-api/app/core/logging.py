"""Structured JSON logger with OpenTelemetry trace context injection.

Extracts `trace_id` and `span_id` from the current OTel context and
appends them to every JSON log record. Outside of a request context,
both fields default to `null`.
"""

import json
import logging
from typing import Any

from opentelemetry import trace


class OtelJsonFormatter(logging.Formatter):
    """JSON formatter that injects OTel trace_id and span_id.

    Output format:
        {"level": "INFO", "message": "...", "trace_id": "...", "span_id": "...", ...}
    """

    def format(self, record: logging.LogRecord) -> str:
        span = trace.get_current_span()
        ctx = span.get_span_context()

        extra: dict[str, Any] = {
            "trace_id": f"{ctx.trace_id:032x}" if ctx.is_valid and ctx.trace_id else None,
            "span_id": f"{ctx.span_id:016x}" if ctx.is_valid and ctx.span_id else None,
        }

        # Build the JSON log entry
        log_entry = {
            "level": record.levelname,
            "message": record.getMessage(),
            "trace_id": extra["trace_id"],
            "span_id": extra["span_id"],
        }

        # Include extra fields from the record if available
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        return json.dumps(log_entry)


def setup_logger(name: str = "person-api") -> logging.Logger:
    """Configure and return a logger with JSON + OTel formatting."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(OtelJsonFormatter())
    if not logger.handlers:
        logger.addHandler(handler)

    return logger
