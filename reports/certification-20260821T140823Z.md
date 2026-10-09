# Certification Report

- Timestamp: 2026-08-21T14:25:02.018064+00:00
- Host: wifi-automations-MacBook-Pro.local
- Environment: Darwin 25.4.0 (arm64)
- Python: 3.12.13
- Preflight targets: PostgreSQL `localhost:5432`, LLM `http://127.0.0.1:8000/v1`

## 1. PREFLIGHT — PASS

- PostgreSQL target: `localhost:5432`
- LLM target: `http://127.0.0.1:8000/v1`

- PASS PostgreSQL reachable at localhost:5432 — PostgreSQL 16.14 (Homebrew) on aarch64-apple-darwin25.6.0
- PASS LLM reachable at http://127.0.0.1:8000/v1


## 2. TEST SUITES — PASS

All five suites via `scripts/run_all_tests.py` (streamed).

| Suite | Collected | Passed | Skipped | Status | Duration (s) |
|---|---|---|---|---|---|
| person-api | 85 | 85 | 0 | PASS | 837.4 |
| aiops-sentinel | 94 | 94 | 0 | PASS | 8.1 |
| openwindow | 52 | 52 | 0 | PASS | 0.5 |
| chaos | 15 | 13 | 2 | PASS | 36.6 |
| integration | 15 | 15 | 0 | PASS | 98.6 |

- PASS master runner exit code 0 — exit code 0
- PASS all five suites reported
- PASS no suite FAIL/TIMEOUT/ERROR
- PASS integration suite ran live (status PASS) — integration status: PASS

Total master-runner duration: 981s


## 3. DATASET LOAD — PASS

`scripts/load_workers.py` — wipe + 50,000 active / 150,000 inactive / 200,000 total.

`active:   50000`
`inactive: 150000`
`total:    200000`
`elapsed:  2.33s`

- PASS loader exit code 0 — exit code 0
- PASS counts 50,000 / 150,000 / 200,000 — active=50000 inactive=150000 total=200000

Load elapsed: 2.33s


## 4. SLO — PASS

Thresholds: lookup warm p99 < 25.0 ms, indexed list warm p99 < 150.0 ms.

- PASS port 8002 free
- PASS pk_lookup_active c=1: warm p99 2.334 ms vs 25.0 ms
- PASS pk_lookup_inactive c=1: warm p99 1.153 ms vs 25.0 ms
- PASS indexed_list c=1: warm p99 16.117 ms vs 150.0 ms
- PASS pk_lookup_active c=10: warm p99 24.017 ms vs 25.0 ms
- PASS pk_lookup_inactive c=10: warm p99 23.312 ms vs 25.0 ms
- PASS indexed_list c=10: warm p99 35.885 ms vs 150.0 ms
- benchmark SLO PASS

- PASS benchmark exit code 0 (SLO PASS) — exit code 0


## 5. LIVE LOOP — PASS

Dedicated D12-shape harness: three real services + unique temp openwindow DB, one live DB-timeout injection.

- PASS harness preflight (Postgres + LLM)
- PASS temp database + migrations — openwindow_test_0a0fe21d4442
- PASS all three services healthy
- PASS observed real 504 from /test/db-delay?delay=5 — body carries timeout + pg_sleep signature
- PASS concurrency drained after 504 (in_use == 0)
- PASS incident ingested with matching real trace id
- PASS root cause is non-fallback


## 6. EVIDENCE VS RCA — PASS

Observed fault evidence vs generated RCA for the injected 504.

| Field | Observed | Generated (RCA) |
|---|---|---|
| HTTP status | 504 | — |
| Route | `/test/db-delay?delay=5` | — |
| pg_sleep delay | 5.0s | — |
| Request id | `e04c31a4-2593-4aa5-8ac2-1e1327596cef` | — |
| Trace id | `180dfc00e8e3e59ca0b68361214bfa05` | citation trace id `180dfc00e8e3e59ca0b68361214bfa05` |
| Timeout detail | `{"detail":"database query timeout after 1.0s (pg_sleep 5.0s exceeded DB_QUERY_TIMEOUT)"}` | — |
| Root cause | — | `A slow pg_sleep-class query holding sessions past the query timeout` |
| Confidence | — | 0.95 |
| Recommended action | — | `reset_pool` (target `database_connection_pool`, fallback=False) |
| ROI summary | — | runbook `db_latency.md` — manual 93.0 min vs measured tool time 0.1237 min → saved 92.876 min (4 breakdown items) |

- PASS citation trace id matches the observed real trace
- PASS recommended action is whitelisted — action='reset_pool'
- PASS confidence in [0, 1] — 0.95
- PASS root cause present and non-fallback
- PASS ROI object valid, breakdown non-empty, time saved > 0


## 7. TIME SAVED — PASS

Per-incident time saved on the live temp openwindow DB (knowledge-base baseline vs measured tool time).

| Incident | Status | Runbook class | Manual estimate (min) | Measured tool time (min) | Time saved (min) | Duplicate |
|---|---|---|---|---|---|---|
| 1 | NEW | db_latency.md | 93.0 | 0.1237 | 92.876 | ? |

Incidents listed: 1
Cumulative time saved: 92.876 min

- PASS table rendered without error (null ROI shown as unavailable) — 1 incident(s)


## 8. OVERALL VERDICT — PASS

**PASS** — every gate section green

| Section | Status |
|---|---|
| PREFLIGHT | PASS |
| TEST SUITES | PASS |
| DATASET LOAD | PASS |
| SLO | PASS |
| LIVE LOOP | PASS |
| EVIDENCE VS RCA | PASS |
| TIME SAVED | PASS |
