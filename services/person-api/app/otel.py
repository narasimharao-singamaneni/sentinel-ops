import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

_test_exporter = InMemorySpanExporter()
_provider = None


def configure_tracer() -> TracerProvider:
    """Configure the tracer provider (idempotent — safe to call multiple times).

    In test mode (OTEL_TEST_MODE=1 or TESTING=true), uses InMemorySpanExporter
    with SimpleSpanProcessor so spans are immediately available for test assertions.
    In production, uses OTLP exporter with BatchSpanProcessor.
    """
    global _provider

    if _provider is not None:
        return _provider

    is_test = os.getenv("OTEL_TEST_MODE") == "1" or os.getenv("TESTING") == "true"

    _provider = TracerProvider()

    if is_test:
        # SimpleSpanProcessor exports synchronously — spans are available immediately
        _provider.add_span_processor(SimpleSpanProcessor(_test_exporter))
    else:
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
        try:
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            _provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:
            # Gracefully fall back to in-memory if OTLP is unreachable at startup
            _provider.add_span_processor(BatchSpanProcessor(_test_exporter))

    trace.set_tracer_provider(_provider)
    return _provider


def get_test_exporter() -> InMemorySpanExporter:
    """Return the in-memory exporter for test assertions."""
    return _test_exporter
