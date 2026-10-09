from dataclasses import dataclass, field
from time import monotonic

from pydantic import BaseModel, Field


class LogEvent(BaseModel):
    """A normalized incoming log/event."""

    trace_id: str = Field(..., description="OpenTelemetry trace ID")
    message: str = Field(..., description="Log message content")
    timestamp: float = Field(..., description="Unix epoch timestamp (seconds)")
    level: str = Field(default="INFO", description="Log level (INFO, ERROR, WARN)")


@dataclass
class IncidentContext:
    """A correlated group of events sharing the same trace_id within the window."""

    trace_id: str
    events: list[LogEvent] = field(default_factory=list)

    @property
    def earliest_time(self) -> float:
        return min(ev.timestamp for ev in self.events)

    @property
    def error_count(self) -> int:
        return sum(1 for ev in self.events if ev.level == "ERROR")

    @property
    def snippet(self) -> str:
        error_msgs = [ev.message for ev in self.events if ev.level == "ERROR"]
        return "; ".join(error_msgs) if error_msgs else self.events[-1].message


class Correlator:
    """In-memory rolling 10-second window correlator.

    Groups events by `trace_id` within a 10-second sliding window.
    Events older than 10 seconds are evicted on each `ingest()` or `collect()` call.
    """

    def __init__(self, window_seconds: float = 10.0):
        self._window = window_seconds
        # trace_id -> list of LogEvent
        self._buffer: dict[str, list[LogEvent]] = {}

    def ingest(self, event: LogEvent) -> None:
        """Ingest a single event. Evicts events outside the rolling window."""
        now = monotonic()
        cutoff = now - self._window
        self._evict(cutoff)

        if event.trace_id not in self._buffer:
            self._buffer[event.trace_id] = []
        self._buffer[event.trace_id].append(event)

    def collect(self) -> list[IncidentContext]:
        """Return all current incident contexts for error-bearing traces.

        Ignores traces with no ERROR-level events.
        """
        now = monotonic()
        cutoff = now - self._window
        self._evict(cutoff)

        result = []
        for trace_id, events in self._buffer.items():
            if any(ev.level == "ERROR" for ev in events):
                result.append(
                    IncidentContext(
                        trace_id=trace_id,
                        events=sorted(events, key=lambda e: e.timestamp),
                    )
                )
        return result

    def _evict(self, cutoff: float) -> None:
        """Remove events and empty trace entries older than cutoff."""
        for trace_id in list(self._buffer.keys()):
            self._buffer[trace_id] = [
                ev for ev in self._buffer[trace_id] if ev.timestamp >= cutoff
            ]
            if not self._buffer[trace_id]:
                del self._buffer[trace_id]

    def clear(self) -> None:
        """Clear all buffered events (useful for tests)."""
        self._buffer.clear()
