"""CLI: run the canonical RCA pipeline over pasted log lines.

Pipeline: normalize (raw log) -> Correlator context -> Retriever (runbook
knowledge) -> Orchestrator (LLM + whitelist) -> RCAResponse JSON.
"""

import asyncio
import json

from app.core.llm import QwenClient
from app.engine.correlator import IncidentContext
from app.engine.orchestrator import Orchestrator
from app.normalizer import normalize_raw_log

raw_log = [
    "2026-05-19 ERROR worker_service.py: OutOfMemoryError - Container killed",
    # "2026-05-19 ERROR db_service.py: TimeoutError - Request to database failed after 30s",
    # "2026-05-19 ERROR auth_service.py: Connection lost to DB",
    # "2026-05-19 WARNING cache_service.py: High latency detected",
    # "ERROR worker_service.py: Memory leak detected",
    # "2026-05-19 INFO scheduler.py: Job completed successfully",
]


def main():
    orchestrator = Orchestrator(client=QwenClient())
    for log in raw_log:
        normalized = normalize_raw_log(log)
        incident = IncidentContext(trace_id=normalized.trace_id, events=normalized.events)
        rca = asyncio.run(orchestrator.analyze(incident))
        print(json.dumps(rca.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
