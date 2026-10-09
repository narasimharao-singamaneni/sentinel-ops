# Certification Report

- Timestamp: 2026-08-21T16:32:34.128802+00:00
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
| person-api | 85 | 85 | 0 | PASS | 44.3 |
| aiops-sentinel | 94 | 94 | 0 | PASS | 7.8 |
| openwindow | 52 | 52 | 0 | PASS | 0.4 |
| chaos | 15 | 13 | 2 | PASS | 36.5 |
| integration | 15 | 15 | 0 | PASS | 98.7 |

- PASS master runner exit code 0 — exit code 0
- PASS all five suites reported
- PASS no suite FAIL/TIMEOUT/ERROR
- PASS integration suite ran live (status PASS) — integration status: PASS

Total master-runner duration: 188s


## 3. DATASET LOAD — PASS

`scripts/load_workers.py` — wipe + 50,000 active / 150,000 inactive / 200,000 total.

`active:   50000`
`inactive: 150000`
`total:    200000`
`elapsed:  2.39s`

- PASS loader exit code 0 — exit code 0
- PASS counts 50,000 / 150,000 / 200,000 — active=50000 inactive=150000 total=200000

Load elapsed: 2.39s


## 4. SLO — FAIL

Thresholds: lookup warm p99 < 25.0 ms, indexed list warm p99 < 150.0 ms.

- PASS port 8002 free
- PASS pk_lookup_active c=1: warm p99 0.845 ms vs 25.0 ms
- PASS pk_lookup_inactive c=1: warm p99 1.27 ms vs 25.0 ms
- PASS indexed_list c=1: warm p99 12.856 ms vs 150.0 ms
- FAIL pk_lookup_active c=10: warm p99 89.538 ms vs 25.0 ms
- PASS pk_lookup_inactive c=10: warm p99 9.106 ms vs 25.0 ms
- PASS indexed_list c=10: warm p99 35.124 ms vs 150.0 ms
- benchmark SLO FAIL

- FAIL benchmark exit code 0 (SLO PASS) — exit code 1


## 5. LIVE LOOP — NOT RUN

NOT RUN — aborted at SLO.

## 6. EVIDENCE VS RCA — NOT RUN

NOT RUN — aborted at SLO.

## 7. TIME SAVED — NOT RUN

NOT RUN — aborted at SLO.

## 8. OVERALL VERDICT — FAIL

**FAIL** — failing sections: SLO, LIVE LOOP, EVIDENCE VS RCA, TIME SAVED

| Section | Status |
|---|---|
| PREFLIGHT | PASS |
| TEST SUITES | PASS |
| DATASET LOAD | PASS |
| SLO | FAIL |
| LIVE LOOP | NOT RUN |
| EVIDENCE VS RCA | NOT RUN |
| TIME SAVED | NOT RUN |
