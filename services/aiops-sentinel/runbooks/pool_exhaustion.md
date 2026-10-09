---
manual_triage_base_minutes: 45
---

# Concurrency / Pool Exhaustion (503 Rejections)

## Symptoms
- `503 Service Unavailable` — "Too many concurrent requests. Please try again shortly."
- Concurrency limiter rejecting requests while all slots are busy
- Burst traffic (retry storm, concurrent flood) against a saturated concurrency limit

## Root Cause Candidates
- Request burst exceeding `MAX_CONCURRENT` while handlers hold slots
- Downstream dependency making handlers slower so slots stay occupied longer
- Client-side retry amplification on top of a busy service

## Remediation
1. Check the concurrency gauge (in-use vs max) and wait timeout
2. Confirm whether the burst is organic traffic or client retry amplification
3. If slots are held by healthy-but-slow requests, throttle/retry upstream or `flush_cache` cold reads
4. If pool is saturated behind the limiter, `reset_pool` to reclaim idle connections

## Runbook Actions
- `reset_pool` — Reclaim idle connections behind the limiter
- `flush_cache` — Reduce cache work on hot keys during the burst
