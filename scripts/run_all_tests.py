#!/usr/bin/env python3
"""
Master integration test script for sentinel-ops monorepo.

Executes pytest across all 5 test suites:
  1. services/person-api/ — FastAPI endpoint & telemetry tests
  2. services/aiops-sentinel/ — AI Ops engine (correlator, LLM, orchestrator, RCA)
  3. apps/openwindow/ — Django dashboard, ingestion, models
  4. chaos/ — Python chaos harness (6 failure scenarios)
  5. integration/ — No-mock live E2E (spawns real services; skips cleanly
     when the real LLM or PostgreSQL is absent)

Usage:
    uv run python scripts/run_all_tests.py

Pre-flight sync:
    Automatically runs `uv sync --all-packages` to ensure all member
    dependencies are installed in the shared .venv before executing tests.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUITES = [
    {
        "name": "person-api",
        "cwd": "services/person-api",
        "args": ["pytest", "tests/", "-v", "--tb=short"],
        "timeout": 120,
        "description": "FastAPI endpoint & telemetry tests",
    },
    {
        "name": "aiops-sentinel",
        "cwd": "services/aiops-sentinel",
        "args": ["pytest", "tests/", "-v", "--tb=short"],
        "timeout": 120,
        "description": "AI Ops engine (correlator, LLM, orchestrator, RCA)",
    },
    {
        "name": "openwindow",
        "cwd": "apps/openwindow",
        "args": ["pytest", "tests/", "-v", "--tb=short"],
        "timeout": 120,
        "description": "Django dashboard, ingestion, models",
    },
    {
        "name": "chaos",
        "cwd": "chaos",
        "args": ["pytest", "test_chaos_harness.py", "-v", "--tb=short"],
        "timeout": 600,
        "description": "Chaos harness — 6 failure scenarios",
    },
    {
        "name": "integration",
        "cwd": "integration",
        "args": ["pytest", "tests/", "-v", "--tb=short"],
        "timeout": 900,
        "description": "No-mock live E2E — requires real LLM + Postgres (skips cleanly when absent)",
    },
]


def _parse_pytest_summary(line: str) -> tuple:
    """Extract (passed, skipped, failed) counts from pytest's final summary line."""

    def field(word: str) -> int:
        match = re.search(rf"(\d+)\s+{word}\b", line)
        return int(match.group(1)) if match else 0

    return field("passed"), field("skipped"), field("failed")


def ensure_sync():
    """Run uv sync --all-packages to install all member deps into .venv."""
    print("\nPre-flight: syncing workspace dependencies...")
    result = subprocess.run(
        ["uv", "sync", "--all-packages"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode == 0:
        print("  Dependencies synced successfully.")
    else:
        print(f"  WARNING: sync returned {result.returncode}")
        print(f"  stderr: {result.stderr[:500]}")


def run_suite(suite: dict) -> dict:
    """Run a single test suite using uv run from its workspace directory.

    Streams output in real-time for visibility.
    """
    name = suite["name"]
    cwd = suite["cwd"]
    args = suite["args"]
    timeout = suite.get("timeout", 300)

    print(f"\n{'=' * 70}")
    print(f"  {name}: {suite['description']}")
    print(f"  Working directory: {cwd}")
    print(f"  Timeout: {timeout}s")
    print(f"{'=' * 70}")

    start = time.time()
    cmd = ["uv", "run"] + args
    cwd_path = PROJECT_ROOT / cwd

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        summary_line = ""
        for line in proc.stdout:
            print(line.rstrip())
            if any(kw in line for kw in ("passed", "skipped", "failed", "error")):
                summary_line = line.strip()

        proc.wait(timeout=timeout)
        elapsed = time.time() - start

        if not summary_line:
            summary_line = f"Exit code: {proc.returncode}"

        passed, skipped, _failed = _parse_pytest_summary(summary_line)
        if proc.returncode != 0:
            status = "FAIL"
        elif passed == 0 and skipped > 0:
            status = "SKIP"
        else:
            status = "PASS"
        print(f"Duration: {elapsed:.1f}s | Status: {status} (passed {passed}, skipped {skipped})")

        return {
            "name": name,
            "status": status,
            "returncode": proc.returncode,
            "elapsed": elapsed,
            "summary": summary_line,
        }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"\nTimeout after {elapsed:.1f}s")
        return {
            "name": name,
            "status": "TIMEOUT",
            "returncode": -1,
            "elapsed": elapsed,
            "summary": f"Suite timed out after {timeout}s",
        }


def main():
    print("Sentinel-Ops Monorepo Test Runner")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Python: {sys.version}")
    print(f"Suites: {len(SUITES)}")

    # Pre-flight: ensure all member deps are in .venv
    ensure_sync()

    results = []
    total_start = time.time()

    for suite in SUITES:
        try:
            res = run_suite(suite)
            results.append(res)
        except Exception as e:
            results.append({
                "name": suite["name"],
                "status": "ERROR",
                "returncode": -1,
                "elapsed": 0.0,
                "summary": str(e),
            })

    total_elapsed = time.time() - total_start

    # Final summary
    passed = sum(1 for r in results if r["status"] == "PASS")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    failed = sum(1 for r in results if r["status"] not in ("PASS", "SKIP"))

    print(f"\n{'=' * 70}")
    print(f"  FINAL SUMMARY")
    print(f"{'=' * 70}")
    for r in results:
        icon = {"PASS": "\u2705", "SKIP": "\u27a1"}.get(r["status"], "\u274c")
        print(f"  {icon} {r['name']}: {r['summary']} ({r['elapsed']:.1f}s")

    total_line = f"{passed}/{len(SUITES)} suites passed"
    if skipped:
        total_line += f", {skipped} skipped"
    if failed:
        total_line += f", {failed} failed"
    print(f"\nTotal: {total_line} in {total_elapsed:.1f}s")

    if failed:
        print(f"\n{failed} suite(s) failed. See output above for details.")
        sys.exit(1)
    elif skipped:
        print("\nCompleted — some suites skipped (external dependencies absent; see preflight messages).")
        sys.exit(0)
    else:
        print("\nAll suites passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
