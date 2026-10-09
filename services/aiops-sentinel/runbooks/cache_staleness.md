---
manual_triage_base_minutes: 120
---

# Cache Staleness

## Symptoms
- API returning stale data after a direct database mutation
- Divergence between what the API served moments ago and current database state
- `cache.stale_read` signal

## Root Cause Candidates
- Write path bypassing the cache (direct DB mutation)
- Cache TTL too long for the affected resource
- Invalidation race on concurrent update

## Remediation
1. Confirm divergence by reading the resource directly and comparing with the DB row
2. Trigger `flush_cache` to drop stale entries for the affected namespace
3. Review invalidation wiring on the write path
4. Tune TTL if staleness windows are too wide

## Runbook Actions
- `flush_cache` — Drop stale entries and force fresh reads
