# Certification Report

- Timestamp: 2026-08-21T14:31:13.214966+00:00
- Host: wifi-automations-MacBook-Pro.local
- Environment: Darwin 25.4.0 (arm64)
- Python: 3.12.13
- Preflight targets: PostgreSQL `localhost:5432`, LLM `http://127.0.0.1:8000/v1`

## 1. PREFLIGHT — FAIL

- PostgreSQL target: `localhost:5432`
- LLM target: `http://127.0.0.1:8000/v1`

- FAIL PostgreSQL reachable at localhost:5432 — PostgreSQL unreachable at localhost:5432 (OSError); required for the live stack
- PASS LLM reachable at http://127.0.0.1:8000/v1


## 2. TEST SUITES — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 3. DATASET LOAD — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 4. SLO — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 5. LIVE LOOP — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 6. EVIDENCE VS RCA — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 7. TIME SAVED — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 8. OVERALL VERDICT — FAIL

**FAIL** — failing sections: PREFLIGHT, TEST SUITES, DATASET LOAD, SLO, LIVE LOOP, EVIDENCE VS RCA, TIME SAVED

| Section | Status |
|---|---|
| PREFLIGHT | FAIL |
| TEST SUITES | NOT RUN |
| DATASET LOAD | NOT RUN |
| SLO | NOT RUN |
| LIVE LOOP | NOT RUN |
| EVIDENCE VS RCA | NOT RUN |
| TIME SAVED | NOT RUN |
