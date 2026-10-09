"""Shared helpers for the no-mock live E2E tests.

Everything here is real: real HTTP against the live stack, real log lines from
the person-api structured logger, real PostgreSQL-backed dashboard data.
"""

import json
import re
import time
from pathlib import Path

FALLBACK_ROOT_CAUSE = "manual_review_required"
ALLOWED_ACTIONS = {"flush_cache", "reset_pool", "manual_review"}
POLL_BUDGET = 60.0
POLL_INTERVAL = 2.0


def traces_for_request_ids(log_dir: Path, request_ids: list[str]) -> dict[str, str]:
    """Map request ids to real OTel trace ids via person-api structured log lines."""
    wanted = set(request_ids)
    files = sorted(log_dir.glob("person-api-*.log"))
    assert files, f"no person-api log file under {log_dir}"
    deadline = time.monotonic() + 10.0
    found: dict[str, str] = {}
    while len(found) < len(wanted) and time.monotonic() < deadline:
        found = {}
        for path in files:
            with open(path, "r", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rid = entry.get("request_id")
                    if rid in wanted and entry.get("trace_id"):
                        found[rid] = entry["trace_id"]
        if len(found) < len(wanted):
            time.sleep(0.5)
    missing = wanted - set(found)
    assert not missing, f"no structured log line with a trace_id for request ids: {sorted(missing)[:3]}"
    return found


def raw_trace(raw_log: str | None) -> str | None:
    match = re.search(r"trace_id=([0-9a-f]{32})", raw_log or "")
    return match.group(1) if match else None


def incidents_matching(incidents: list[dict], target_resource: str, raw_marker: str) -> list[dict]:
    return [
        inc
        for inc in incidents
        if inc.get("target_resource") == target_resource and raw_marker in (inc.get("raw_log") or "")
    ]


def citation_trace_matches(incident: dict, trace: str) -> bool:
    return any(c.get("trace_id") == trace for c in incident.get("citations") or [])


def assert_whitelisted_action(incident: dict) -> None:
    action = incident["dry_run_payload"]["action"]
    assert action in ALLOWED_ACTIONS, f"action {action!r} is not whitelisted"
    if action == "manual_review":
        assert incident["dry_run_payload"]["fallback"] is True
