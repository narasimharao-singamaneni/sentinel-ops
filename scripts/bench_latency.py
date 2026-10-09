"""Live latency benchmark for person-api over the 200K dataset (Task 6.2 / D13).

Runs real HTTP against a live person-api (default base http://127.0.0.1:8002,
which must be running against the 200K-worker PostgreSQL dataset):

  query types:
    PK lookup (active worker)
    PK lookup (inactive worker)
    indexed list (status filter, limit=100)
  volume: 200 timed requests per type at concurrency 1 and 10
  warmup: 20 requests per type/level — excluded from the SLO gate,
          reported separately as informational

Outputs reports/latency-<ts>.json and .md containing: timestamp,
host/environment (when available), PostgreSQL version (when available),
dataset counts, warmup count, concurrency levels, sample counts, SLO
thresholds used, and p50/p95/p99 per type/level (warm), plus warmup
percentiles.

SLO gate (warm p99 at BOTH concurrency levels):
    lookups < --lookup-p99-ms  (default 25, env SLO_LOOKUP_P99_MS)
    list    < --list-p99-ms    (default 150, env SLO_LIST_P99_MS)
Gate fail -> "SLO FAIL" printed and non-zero exit.
"""

import argparse
import asyncio
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone

import asyncpg
import httpx

DATABASE_DSN = os.getenv("PERSON_API_DSN", "postgresql://localhost:5432/person_api")
DEFAULT_BASE_URL = os.getenv("PERSON_API_URL", "http://127.0.0.1:8002")
DEFAULT_REQUESTS = 200
DEFAULT_WARMUP = 20
CONCURRENCY_LEVELS = (1, 10)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

QUERY_TYPES = ("pk_lookup_active", "pk_lookup_inactive", "indexed_list")


def percentile_nearest_rank(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil((p / 100.0) * len(ordered)))
    return ordered[rank - 1]


def _percentiles(values: list[float]) -> dict:
    return {
        "p50_ms": round(percentile_nearest_rank(values, 50) or 0.0, 3),
        "p95_ms": round(percentile_nearest_rank(values, 95) or 0.0, 3),
        "p99_ms": round(percentile_nearest_rank(values, 99) or 0.0, 3),
        "min_ms": round(min(values), 3) if values else None,
        "max_ms": round(max(values), 3) if values else None,
        "mean_ms": round(sum(values) / len(values), 3) if values else None,
        "count": len(values),
    }


async def _db_facts(dsn: str) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        version = await conn.fetchval("SELECT version()")
        counts = dict(
            (r["status"], r["count"])
            for r in await conn.fetch("SELECT status, COUNT(*) AS count FROM workers GROUP BY status")
        )
        active_id = await conn.fetchval("SELECT id FROM workers WHERE status = 'active' LIMIT 1")
        inactive_id = await conn.fetchval("SELECT id FROM workers WHERE status = 'inactive' LIMIT 1")
    finally:
        await conn.close()
    if not active_id or not inactive_id:
        raise SystemExit("dataset incomplete: need at least one active and one inactive worker")
    return {
        "postgresql_version": version,
        "counts": counts,
        "total": sum(counts.values()),
        "sample_active_id": active_id,
        "sample_inactive_id": inactive_id,
    }


async def _phase(
    client: httpx.AsyncClient,
    urls: dict[str, str],
    qtype: str,
    concurrency: int,
    n_requests: int,
) -> list[float]:
    """Run n_requests at the given concurrency level; return per-request ms."""
    url = urls[qtype]
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors: list[str] = []

    async def one() -> None:
        async with semaphore:
            started = time.perf_counter()
            resp = await client.get(url)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if resp.status_code != 200:
                errors.append(f"{url} -> HTTP {resp.status_code}: {resp.text[:200]}")
                return
            latencies.append(elapsed_ms)

    await asyncio.gather(*(one() for _ in range(n_requests)))
    if errors:
        raise RuntimeError(f"non-200 response(s) during benchmark: {errors[:3]}")
    return latencies


async def _benchmark(base_url: str, db_facts: dict, requests_per_type: int, warmup: int) -> dict:
    urls = {
        "pk_lookup_active": f"{base_url}/api/v1/active/workers/{db_facts['sample_active_id']}",
        "pk_lookup_inactive": f"{base_url}/api/v1/inactive/workers/{db_facts['sample_inactive_id']}",
        "indexed_list": f"{base_url}/api/v1/active/workers?limit=100",
    }

    results: dict = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.get(f"{base_url}/health")
        for level in CONCURRENCY_LEVELS:
            for qtype in QUERY_TYPES:
                warmup_ms = await _phase(client, urls, qtype, level, warmup)
                timed_ms = await _phase(client, urls, qtype, level, requests_per_type)
                results[f"{qtype}_c{level}"] = {
                    "warmup": _percentiles(warmup_ms),
                    "warm": _percentiles(timed_ms),
                }
    return results


def _slo_checks(results: dict, lookup_p99: float, list_p99: float) -> list[dict]:
    checks = []
    thresholds = {
        "pk_lookup_active": lookup_p99,
        "pk_lookup_inactive": lookup_p99,
        "indexed_list": list_p99,
    }
    for level in CONCURRENCY_LEVELS:
        for qtype in QUERY_TYPES:
            cell = results[f"{qtype}_c{level}"]["warm"]
            threshold = thresholds[qtype]
            checks.append(
                {
                    "type": qtype,
                    "concurrency": level,
                    "metric": "warm_p99_ms",
                    "value": cell["p99_ms"],
                    "threshold_ms": threshold,
                    "passed": cell["p99_ms"] < threshold,
                }
            )
    return checks


def _render_markdown(report: dict) -> str:
    lines = [
        "# Latency Benchmark — person-api @ 200K",
        "",
        f"- Timestamp: {report['timestamp']}",
        f"- Host: {report['host']}",
        f"- Environment: {report['environment']}",
        f"- PostgreSQL: {report['postgresql_version']}",
        f"- Dataset: {report['dataset']['active']} active / {report['dataset']['inactive']} inactive / {report['dataset']['total']} total",
        f"- Per type/level: {report['requests_per_type']} timed requests, {report['warmup']} warmup (excluded from gate)",
        f"- SLO thresholds: lookup warm p99 < {report['thresholds']['lookup_p99_ms']} ms, indexed list warm p99 < {report['thresholds']['list_p99_ms']} ms (both concurrency levels)",
        "",
        "## Warm percentiles (SLO-gated)",
        "",
        "| Type | Concurrency | p50 (ms) | p95 (ms) | p99 (ms) | Samples |",
        "|---|---|---|---|---|---|",
    ]
    for key, cell in (
        (k, v) for k, v in results_order(report["results"])
    ):
        warm = cell["warm"]
        qtype, level = key.rsplit("_c", 1)
        lines.append(
            f"| {qtype} | c={level} | {warm['p50_ms']} | {warm['p95_ms']} | {warm['p99_ms']} | {warm['count']} |"
        )

    lines += [
        "",
        "## Warmup / cold percentiles (informational, excluded from gate)",
        "",
        "| Type | Concurrency | p50 (ms) | p95 (ms) | p99 (ms) | Samples |",
        "|---|---|---|---|---|---|",
    ]
    for key, cell in results_order(report["results"]):
        warmup = cell["warmup"]
        qtype, level = key.rsplit("_c", 1)
        lines.append(
            f"| {qtype} | c={level} | {warmup['p50_ms']} | {warmup['p95_ms']} | {warmup['p99_ms']} | {warmup['count']} |"
        )

    lines += ["", "## SLO gate", ""]
    for check in report["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"- {mark} {check['type']} c={check['concurrency']}: warm p99 {check['value']} ms "
            f"{'<' if check['passed'] else '>='} {check['threshold_ms']} ms"
        )
    lines += ["", f"## Verdict", "", f"**{report['verdict']}**", ""]
    return "\n".join(lines)


def results_order(results: dict) -> list[tuple[str, dict]]:
    ordered = []
    for level in CONCURRENCY_LEVELS:
        for qtype in QUERY_TYPES:
            key = f"{qtype}_c{level}"
            ordered.append((key, results[key]))
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description="person-api 200K latency benchmark with SLO gate")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="live person-api base URL")
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS, help="timed requests per type/level")
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help="warmup requests per type/level")
    parser.add_argument(
        "--lookup-p99-ms",
        type=float,
        default=float(os.getenv("SLO_LOOKUP_P99_MS", "25")),
        help="SLO threshold (ms) for PK lookup warm p99 (default 25)",
    )
    parser.add_argument(
        "--list-p99-ms",
        type=float,
        default=float(os.getenv("SLO_LIST_P99_MS", "150")),
        help="SLO threshold (ms) for indexed list warm p99 (default 150)",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    try:
        now = datetime.now(timezone.utc)
        db_facts = asyncio.run(_db_facts(DATABASE_DSN))
        results = asyncio.run(
            _benchmark(base_url, db_facts, args.requests, args.warmup)
        )
    except SystemExit:
        raise
    except (OSError, httpx.HTTPError, RuntimeError) as exc:
        print(f"BENCHMARK ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    checks = _slo_checks(results, args.lookup_p99_ms, args.list_p99_ms)
    overall = all(c["passed"] for c in checks)
    verdict = "SLO PASS" if overall else "SLO FAIL"

    report = {
        "timestamp": now.isoformat(),
        "host": platform.node(),
        "environment": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "base_url": base_url,
        "postgresql_version": db_facts["postgresql_version"],
        "dataset": {
            "active": db_facts["counts"].get("active", 0),
            "inactive": db_facts["counts"].get("inactive", 0),
            "total": db_facts["total"],
        },
        "requests_per_type": args.requests,
        "warmup": args.warmup,
        "concurrency_levels": list(CONCURRENCY_LEVELS),
        "thresholds": {"lookup_p99_ms": args.lookup_p99_ms, "list_p99_ms": args.list_p99_ms},
        "results": results,
        "checks": checks,
        "verdict": verdict,
    }

    os.makedirs(REPORTS_DIR, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    json_path = os.path.join(REPORTS_DIR, f"latency-{stamp}.json")
    md_path = os.path.join(REPORTS_DIR, f"latency-{stamp}.md")
    with open(json_path, "w") as fh:
        json.dump(report, fh, indent=2)
    with open(md_path, "w") as fh:
        fh.write(_render_markdown(report))

    for check in checks:
        print(
            f"{'PASS' if check['passed'] else 'FAIL'} {check['type']} c={check['concurrency']}: "
            f"warm p99 {check['value']} ms vs threshold {check['threshold_ms']} ms"
        )
    print(f"verdict: {verdict}")
    print(f"reports: {json_path}")
    print(f"reports: {md_path}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
