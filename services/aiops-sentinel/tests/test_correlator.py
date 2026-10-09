from time import monotonic
from unittest.mock import patch

import pytest

from app.engine.correlator import Correlator, IncidentContext, LogEvent


class TestCorrelatorGrouping:
    """Positive test: multiple log events with same trace_id collapse into single IncidentContext."""

    def test_events_same_trace_id_grouped(self):
        correlator = Correlator(window_seconds=10.0)
        base_time = monotonic()

        correlator.ingest(
            LogEvent(trace_id="abc-123", message="Connection failed", timestamp=base_time, level="ERROR")
        )
        correlator.ingest(
            LogEvent(trace_id="abc-123", message="Retry exhausted", timestamp=base_time + 1.0, level="ERROR")
        )
        correlator.ingest(
            LogEvent(trace_id="abc-123", message="Initial request", timestamp=base_time + 0.5, level="INFO")
        )

        incidents = correlator.collect()
        assert len(incidents) == 1
        assert incidents[0].trace_id == "abc-123"
        assert len(incidents[0].events) == 3
        assert incidents[0].error_count == 2


class TestCorrelatorWindowAndFiltering:
    """Negative test: logs outside 10s window or without error status are ignored."""

    def test_old_events_evicted(self):
        correlator = Correlator(window_seconds=10.0)
        base_time = monotonic()

        correlator.ingest(
            LogEvent(
                trace_id="old-1",
                message="Stale error",
                timestamp=base_time - 15.0,  # 15s ago — outside window
                level="ERROR",
            )
        )

        incidents = correlator.collect()
        assert len(incidents) == 0

    def test_non_error_traces_ignored(self):
        correlator = Correlator(window_seconds=10.0)
        base_time = monotonic()

        correlator.ingest(
            LogEvent(trace_id="info-only", message="All is well", timestamp=base_time, level="INFO")
        )

        incidents = correlator.collect()
        assert len(incidents) == 0

    def test_mixed_levels_includes_trace_with_any_error(self):
        """A trace with both INFO and ERROR is included (because it has at least one ERROR)."""
        correlator = Correlator(window_seconds=10.0)
        base_time = monotonic()

        correlator.ingest(
            LogEvent(trace_id="mixed", message="Request started", timestamp=base_time, level="INFO")
        )
        correlator.ingest(
            LogEvent(trace_id="mixed", message="Timeout after 5s", timestamp=base_time + 1.0, level="ERROR")
        )

        incidents = correlator.collect()
        assert len(incidents) == 1
        assert incidents[0].trace_id == "mixed"

    def test_different_trace_ids_separate_incidents(self):
        correlator = Correlator(window_seconds=10.0)
        base_time = monotonic()

        correlator.ingest(
            LogEvent(trace_id="trace-a", message="Error A", timestamp=base_time, level="ERROR")
        )
        correlator.ingest(
            LogEvent(trace_id="trace-b", message="Error B", timestamp=base_time + 0.5, level="ERROR")
        )

        incidents = correlator.collect()
        assert len(incidents) == 2
        trace_ids = {inc.trace_id for inc in incidents}
        assert trace_ids == {"trace-a", "trace-b"}

    def test_clear_resets_buffer(self):
        correlator = Correlator(window_seconds=10.0)
        base_time = monotonic()

        correlator.ingest(
            LogEvent(trace_id="abc", message="Error", timestamp=base_time, level="ERROR")
        )
        assert len(correlator.collect()) == 1

        correlator.clear()
        assert len(correlator.collect()) == 0


class TestCorrelatorSnippet:
    def test_snippet_composed_of_error_messages(self):
        correlator = Correlator(window_seconds=10.0)
        base_time = monotonic()

        correlator.ingest(
            LogEvent(trace_id="snip-1", message="Pool empty", timestamp=base_time, level="ERROR")
        )
        correlator.ingest(
            LogEvent(trace_id="snip-1", message="Timeout", timestamp=base_time + 1.0, level="ERROR")
        )

        incidents = correlator.collect()
        assert "Pool empty" in incidents[0].snippet
        assert "Timeout" in incidents[0].snippet

    def test_snippet_falls_back_to_last_message_when_no_errors(self):
        """Even though collect() filters out non-error traces, snippet property still works on raw IncidentContext."""
        event = LogEvent(trace_id="x", message="Only info", timestamp=monotonic(), level="INFO")
        ctx = IncidentContext(trace_id="x", events=[event])
        assert ctx.snippet == "Only info"
