---
manual_triage_base_minutes: 90
---

# Database Latency / Timeout

## Symptoms
- `TimeoutError` / query timeouts after 5s+
- `ConnectionPoolError: max connections reached`
- A query waiting on the database breaching `DB_QUERY_TIMEOUT` (HTTP 504)
- Slow indexed list scans at scale

## Root Cause Candidates
- Missing index on frequently queried column
- Connection pool size too low for peak traffic
- Long-running unindexed search queries blocking pool slots
- A slow `pg_sleep`-class query holding sessions past the query timeout

## Remediation
1. Check connection pool telemetry for checked-out/overflow connections
2. Run `EXPLAIN` on slow queries to identify missing indexes
3. If pool exhaustion confirmed, trigger `reset_pool` action to reclaim idle connections
4. Increase `max_pool_size` if infrastructure permits

## Runbook Actions
- `reset_pool` — Flush idle connections and rebuild the pool
- `flush_cache` — Clear stale query cache entries
